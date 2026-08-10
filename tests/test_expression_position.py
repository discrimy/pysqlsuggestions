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
    assert 'name' not in offered
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
    assert 'name' in texts('SELECT CASE WHEN id = 1 THEN ⌶ FROM events')
    assert 'name' in texts("SELECT CASE WHEN id = 1 THEN 'a' ELSE ⌶ FROM events")


def test_a_finished_case_is_a_completed_item() -> None:
    """`END` closes the expression, so what follows is AS or the next clause."""
    offered = texts("SELECT CASE WHEN id = 1 THEN 'a' END ⌶ FROM events")
    assert 'AS' in offered
    assert 'name' not in offered
