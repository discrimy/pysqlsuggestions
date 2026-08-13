"""
Where a caret sits inside an expression, and what that admits.

`after_operand` decides the position from one token, classified as "a dialect
keyword or not". Every construct whose interior words *are* keywords reads as a
fresh operand starting — `IS`, `ASC`, `WHEN` — and every construct whose words
are not reads as a finished one. These pin the constructs where that answer is
wrong, and each is a suggestion that does not parse where it lands.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.postgres import POSTGRES
from tests.corpus.cases import split_caret

SNAPSHOT = {
    ('public', 'events'): [
        ('id', 'bigint'),
        ('name', 'varchar'),
        ('archived', 'boolean'),
        ('started_at', 'timestamp with time zone'),
    ],
}


def catalog() -> MemoryCatalog:
    """The fixture catalog."""
    return MemoryCatalog(SNAPSHOT)


def texts(marked: str, dialect: Dialect = POSTGRES) -> list[str]:
    """Suggestion texts for ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return [s.text for s in complete(sql, caret, dialect, catalog())]


def expecting(marked: str) -> str:
    """The expression position derived for ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return derive_request(sql, caret, POSTGRES).expecting


def test_is_wants_the_words_that_finish_it() -> None:
    """`WHERE id IS ⌶` accepting a column gives `IS name`, which does not parse."""
    assert texts('SELECT * FROM events WHERE id IS ⌶') == ['NULL', 'NOT NULL', 'TRUE', 'FALSE', 'DISTINCT FROM']
    assert texts('SELECT * FROM events WHERE id IS NOT ⌶') == ['NULL', 'TRUE', 'FALSE', 'DISTINCT FROM']


def test_a_finished_null_test_takes_a_connective() -> None:
    """Once `IS NULL` is written the predicate is complete, whichever way it was typed."""
    assert 'AND' in texts('SELECT * FROM events WHERE id IS NULL ⌶')
    assert 'AND' in texts('SELECT * FROM events WHERE id IS NOT NULL ⌶')
    assert 'name' not in texts('SELECT * FROM events WHERE id IS NULL ⌶')


def test_nulls_wants_first_or_last() -> None:
    """`ORDER BY id NULLS ⌶` offered `NULLS LAST`, producing `NULLS NULLS LAST`."""
    assert texts('SELECT * FROM events ORDER BY id NULLS ⌶') == ['FIRST', 'LAST']


def test_a_sort_direction_does_not_reopen_the_item() -> None:
    """`ORDER BY id ASC ⌶` accepting a column gives `ASC name`."""
    offered = texts('SELECT * FROM events ORDER BY id ASC ⌶')
    assert 'events.name' not in offered
    assert 'LIMIT' in offered
    assert 'name' not in texts('SELECT * FROM events ORDER BY id NULLS LAST ⌶')


def test_a_parenthesised_predicate_is_finished() -> None:
    """
    `WHERE (a AND b) ⌶` takes AND, OR or the next clause. It was reading as an
    operator position, which offered `LIKE` and `BETWEEN` after a closing paren
    and withheld the two words that belong.
    """
    assert expecting("SELECT * FROM events WHERE (id > 1 AND name = 'x') ⌶") == 'connective'
    offered = texts("SELECT * FROM events WHERE (id > 1 AND name = 'x') ⌶")
    assert 'AND' in offered
    assert 'OR' in offered
    assert 'BETWEEN' not in offered


def test_an_unfinished_group_still_wants_an_operator() -> None:
    """The counterpart: inside the parens the ordinary rules apply."""
    assert expecting('SELECT * FROM events WHERE (id ⌶') == 'operator'
    assert expecting('SELECT * FROM events WHERE (id > 1 ⌶') == 'connective'


def test_a_case_branch_is_a_predicate_not_a_select_item() -> None:
    """`SELECT CASE WHEN id ⌶` offered `AS` and `FROM`, giving `CASE WHEN id AS`."""
    offered = texts('SELECT CASE WHEN id ⌶ FROM events')
    assert '=' in offered
    assert 'AS' not in offered
    assert 'FROM' not in offered


def test_case_offers_the_words_that_continue_it() -> None:
    """WHEN, THEN, ELSE and END exist nowhere in the clause model today."""
    assert 'THEN' in texts('SELECT CASE WHEN id = 1 ⌶ FROM events')
    assert 'WHEN' in texts('SELECT CASE ⌶ FROM events')
    assert 'ELSE' in texts("SELECT CASE WHEN id = 1 THEN 'a' ⌶ FROM events")
    assert 'END' in texts("SELECT CASE WHEN id = 1 THEN 'a' ⌶ FROM events")


def test_a_case_branch_takes_an_operand_after_then() -> None:
    """`THEN ⌶` and `ELSE ⌶` open a value, so columns belong there."""
    assert 'events.name' in texts('SELECT CASE WHEN id = 1 THEN ⌶ FROM events')
    assert 'events.name' in texts("SELECT CASE WHEN id = 1 THEN 'a' ELSE ⌶ FROM events")


def test_a_finished_case_is_a_completed_item() -> None:
    """`END` closes the expression, so what follows is AS or the next clause."""
    offered = texts("SELECT CASE WHEN id = 1 THEN 'a' END ⌶ FROM events")
    assert 'AS' in offered
    assert 'name' not in offered


def test_a_sort_direction_is_not_offered_twice() -> None:
    """`ORDER BY id ASC ⌶` cannot take `DESC`; the two are one choice, already made."""
    offered = texts('SELECT * FROM events ORDER BY id ASC ⌶')
    assert 'ASC' not in offered
    assert 'DESC' not in offered
    assert 'NULLS FIRST' in offered


def test_the_next_item_gets_the_choice_back() -> None:
    """A comma starts a new sort item, with its own direction to pick."""
    assert 'ASC' in texts('SELECT * FROM events ORDER BY id ASC, name ⌶')


def test_a_nulls_placement_is_not_offered_twice() -> None:
    """The same rule for the other pair in an ORDER BY item."""
    offered = texts('SELECT * FROM events ORDER BY id NULLS LAST ⌶')
    assert 'NULLS FIRST' not in offered
    assert 'NULLS LAST' not in offered
    assert 'LIMIT' in offered


def test_a_clause_name_stopped_between_its_words_takes_only_the_rest() -> None:
    """
    `GROUP BY` is one clause with a space in it, and a typist stops in that
    space constantly.

    The first word read as a clause already complete, so the caret after it was
    offered whatever that clause admits — every relation in the schema after
    `GROUP `, and a column after `ORDER `. Nothing but the second word can stand
    there, and accepting anything else wrote SQL no server parses.
    """
    assert texts('SELECT * FROM events GROUP ⌶') == ['BY']
    assert texts('SELECT * FROM events ORDER ⌶') == ['BY']
    assert texts('SELECT * FROM events LEFT ⌶') == ['JOIN']
    assert texts('INSERT ⌶') == ['INTO']
    assert texts('DELETE ⌶') == ['FROM']


def test_a_first_word_that_is_also_a_clause_still_opens_what_follows_it() -> None:
    """
    The limit of the same rule, and why it is derived rather than listed.

    `ON` begins `ON CONFLICT` and is a clause in its own right. Answering `ON `
    with `CONFLICT` alone would refuse the join predicate that almost always
    follows it, so a head that is itself a phrase is left out of the table.
    """
    assert 'CONFLICT' not in texts('SELECT * FROM events e JOIN other o ON ⌶')
    assert 'e.id' in texts('SELECT * FROM events e JOIN other o ON ⌶')


def test_a_clause_that_shapes_a_result_set_needs_a_result_set() -> None:
    """
    GROUP BY, ORDER BY, LIMIT and the set operators belong to a query.

    A finished `UPDATE ... WHERE id = 2` was offered all of them, and an UPDATE
    has no result to group or order — every one of them wrote SQL the server
    refuses. RETURNING is the mirror image and was already declared, which is
    how the omission stayed invisible: the mechanism worked, and these clauses
    simply never said which statements they belong to.
    """
    updating = texts('UPDATE events SET name = 1 WHERE id = 2 ⌶')
    assert updating == ['AND', 'OR', 'RETURNING']
    assert texts('DELETE FROM events WHERE id = 1 ⌶') == ['AND', 'OR', 'RETURNING']

    querying = texts('SELECT * FROM events WHERE id = 1 ⌶')
    assert {'GROUP BY', 'ORDER BY', 'UNION'} <= set(querying)


def test_a_query_inside_a_statement_of_another_form_keeps_its_own_clauses() -> None:
    """
    The form is read at the caret, not at the head of the text.

    `INSERT INTO t SELECT ...` is a query from the SELECT onward, and filtering
    its clauses by the statement's first word would leave the commonest way of
    writing an INSERT unable to group or order anything.
    """
    assert 'GROUP BY' in texts('INSERT INTO events SELECT * FROM events WHERE id = 1 ⌶')
    assert 'ORDER BY' in texts('UPDATE events SET name = (SELECT name FROM events WHERE id = 1 ⌶')


def test_a_word_that_begins_a_reference_is_not_offered_after_one() -> None:
    """
    `LATERAL` modifies the relation after it rather than joining to the one
    before, and the server agrees: `FROM events LATERAL (...)` is a syntax
    error while the comma and JOIN forms are not.

    It declares that it follows FROM and JOIN, which was read as "after a
    complete relation in one of those" — the one place it cannot go. `JOIN`
    itself carries its own separator and so may follow a relation, which is why
    this belongs to the clause rather than to relation clauses in general.
    """
    assert 'LATERAL' not in texts('SELECT * FROM events e ⌶')
    assert 'LATERAL' not in texts('SELECT * FROM events e JOIN other o ⌶')
    assert 'LATERAL' in texts('SELECT * FROM events e, ⌶')
    assert 'LATERAL' in texts('SELECT * FROM events e JOIN ⌶')


def test_a_row_count_is_typed_rather_than_suggested() -> None:
    """
    `LIMIT ` takes a number, and a kind there filled the position with the
    clause's own successors — OFFSET, which belongs after the count rather than
    instead of it.

    UNION keeps its kind, because `UNION ALL` genuinely is what comes next.
    That is the difference `followed_by` cannot express on its own, so it is
    settled per clause rather than by a rule about operand positions.
    """
    assert texts('SELECT * FROM events ORDER BY id LIMIT ⌶') == []
    # `LIMIT 10 ` continues into OFFSET and into the locking clause, which is
    # legal after a row count: `SELECT … LIMIT 10 FOR UPDATE` runs. The count
    # position itself stays empty, which is what this test is about.
    assert texts('SELECT * FROM events ORDER BY id LIMIT 10 ⌶') == [
        'OFFSET',
        'FOR UPDATE',
        'FOR NO KEY UPDATE',
        'FOR SHARE',
        'FOR KEY SHARE',
    ]
    assert texts('SELECT id FROM events UNION ⌶') == ['ALL', 'SELECT']


def test_two_spellings_of_the_same_limit_are_one_choice() -> None:
    """
    `LIMIT 10 FETCH FIRST 2 ROWS ONLY` names a row count twice and no server
    takes it, so writing either settles both — the same rule that stops
    `ORDER BY id ASC ` offering DESC.
    """
    assert 'FETCH' not in texts('SELECT * FROM events ORDER BY id LIMIT 10 ⌶')
    assert 'OFFSET' in texts('SELECT * FROM events ORDER BY id LIMIT 10 ⌶')


def test_a_word_that_precedes_the_item_is_offered_before_one_and_behind_a_prefix() -> None:
    """
    `DISTINCT` may only follow SELECT itself — `SELECT * DISTINCT` and
    `SELECT id, DISTINCT` are both syntax errors.

    Listing it among what follows the clause put it in the one place it cannot
    go, and in none of the places it can: after an item it was offered and
    rejected, and `SELECT dis` found nothing at all.

    Behind a prefix rather than offered outright, because `SELECT ` is the
    commonest caret in the language and a column is nearly always what belongs
    there. A rarely-wanted word above every column costs more than it returns;
    behind two typed letters it costs nothing.
    """
    assert 'DISTINCT' not in texts('SELECT * ⌶')
    assert 'DISTINCT' not in texts('SELECT count(*) AS n ⌶')
    assert 'DISTINCT' not in texts('SELECT ⌶'), 'a column is what belongs here'
    assert texts('SELECT dis⌶') == ['DISTINCT']
    assert texts('SELECT id, dis⌶') == [], 'and never after an item, however it is spelled'


def test_a_clause_follows_what_it_actually_follows() -> None:
    """
    `UPDATE t FROM y` and `INSERT INTO t RETURNING id` name no assignment and
    no rows, and neither parses.

    Both clauses listed everything that appears anywhere later in their
    statement as following them directly. What follows the relation an UPDATE
    names is SET, and what follows the table an INSERT names is the rows —
    WHERE, FROM and RETURNING all come after those.
    """
    assert texts('UPDATE events ⌶')[-1:] == ['SET']
    assert texts('UPDATE events SET name = 1 ⌶') == ['WHERE', 'FROM', 'RETURNING']
    assert texts('INSERT INTO events ⌶') == ['VALUES', 'SELECT']
    assert texts('INSERT INTO events VALUES (1) ⌶') == ['ON CONFLICT', 'RETURNING']


def test_an_operator_position_takes_operators_and_not_the_dictionary() -> None:
    """
    `UPDATE t SET total ` offered AS, BY, DO, IN, IS and ON — the reserved word
    list, reached by a fallback meant for a caret with no clause at all.

    A clause that declares operators and no predicate words has nothing to say
    here, and saying nothing is the answer: `=` arrives as an operator, which is
    a kind of its own.
    """
    assert texts('UPDATE events SET name ⌶') == ['=']


def test_an_insert_target_is_not_offered_a_bare_alias() -> None:
    """
    `UPDATE orders o` and `DELETE FROM orders o` are legal; `INSERT INTO orders o`
    is not — that one spells its alias `AS o`, and the generated names are bare.
    """
    assert texts('INSERT INTO events ⌶') == ['VALUES', 'SELECT']
    assert 'e' in texts('UPDATE events ⌶')


def test_a_star_takes_no_alias() -> None:
    """
    `SELECT * AS x` and `SELECT t.* AS x` are both syntax errors.

    A star is not a word, so nothing in the item marked it as written and AS
    was offered after it as after any other select item. It stands in the item
    the way a name does, and rules out the same thing.

    The star inside `count(*)` is one level deeper and belongs to the call, so
    that item may still be aliased — which is the commonest reason to write AS
    in a select list at all.
    """
    assert 'AS' not in texts('SELECT * ⌶')
    assert 'AS' not in texts('SELECT e.* ⌶')
    assert 'AS' in texts('SELECT count(*) ⌶')
    assert 'AS' in texts('SELECT *, e.id ⌶'), 'the comma starts an item that can be named'
    assert 'AS' in texts('SELECT 1 ⌶'), 'and a literal is not a star'


def test_a_cast_takes_its_own_keyword_and_not_the_clause_s() -> None:
    """
    `CAST(x AS type)` is a call with a keyword inside it, and after the value
    only that keyword can follow. Nothing marked the interior as different, so
    the enclosing clause's continuations reached it and `SELECT cast(o.total `
    offered FROM, WHERE and GROUP BY.

    An ordinary call is left alone: in `SELECT count(o.total ` the closing paren
    may simply be unwritten, and the FROM the caret is offered there belongs to
    the query rather than to the argument list.
    """
    assert texts('SELECT cast(e.id ⌶') == ['as'], 'cased to match the `cast` the author wrote'
    assert texts('SELECT CAST(e.id ⌶') == ['AS']
    assert 'text' in texts('SELECT cast(e.id AS ⌶')
    assert 'FROM' in texts('SELECT count(e.id ⌶')
