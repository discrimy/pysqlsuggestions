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
    assert texts('SELECT * FROM events ORDER BY id LIMIT 10 ⌶') == ['OFFSET']
    assert texts('SELECT id FROM events UNION ⌶') == ['ALL', 'SELECT']


def test_two_spellings_of_the_same_limit_are_one_choice() -> None:
    """
    `LIMIT 10 FETCH FIRST 2 ROWS ONLY` names a row count twice and no server
    takes it, so writing either settles both — the same rule that stops
    `ORDER BY id ASC ` offering DESC.
    """
    assert 'FETCH' not in texts('SELECT * FROM events ORDER BY id LIMIT 10 ⌶')
    assert 'OFFSET' in texts('SELECT * FROM events ORDER BY id LIMIT 10 ⌶')
