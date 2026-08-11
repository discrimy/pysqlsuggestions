"""
The clause model as an extension point, and the three rules that use it.

`Clause.follows` was carefully populated by every dialect and read by nothing,
which is why a dialect could add PREWHERE to its vocabulary and never see it
offered. These pin the wiring in both directions: what a dialect adds becomes
reachable, and what belongs to another statement form stays out.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Clause, Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import Kind
from tests.corpus.cases import split_caret

SNAPSHOT = {('public', 'events'): [('id', 'bigint'), ('name', 'varchar')]}


def catalog() -> MemoryCatalog:
    """The fixture catalog."""
    return MemoryCatalog(SNAPSHOT)


def words(marked: str, dialect: Dialect = POSTGRES) -> list[str]:
    """Keyword suggestions for ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return [s.text for s in complete(sql, caret, dialect, catalog()) if s.kind is Kind.KEYWORD]


def test_a_dialect_clause_is_offered_after_the_clause_it_declares_it_follows() -> None:
    """
    `PREWHERE.follows = {FROM, SAMPLE, FINAL}` is the whole declaration needed.

    The base model does not know the word exists, so nothing in ANSI can list
    it — deriving continuations from `follows` is what makes the vocabulary and
    the offer lists the same data.
    """
    assert 'PREWHERE' in words('SELECT * FROM events ⌶', CLICKHOUSE)
    assert 'ARRAY JOIN' in words('SELECT * FROM events ⌶', CLICKHOUSE)
    assert 'LIMIT BY' in words('SELECT * FROM events ORDER BY id LIMIT 1 ⌶', CLICKHOUSE)
    # After a comma rather than after the relation: both of these begin a
    # reference rather than following one, and the server rejects
    # `FROM events LATERAL ...` exactly as it rejects `FROM events UNNEST(...)`.
    assert 'LATERAL' in words('SELECT * FROM events, ⌶', POSTGRES)
    assert 'UNNEST' in words('SELECT * FROM events, ⌶', TRINO)


def test_a_dialect_clause_stays_out_of_the_dialects_that_lack_it() -> None:
    """The counterpart. A shared base list is how `PREWHERE` would leak everywhere."""
    assert 'PREWHERE' not in words('SELECT * FROM events ⌶', POSTGRES)
    assert 'LATERAL' not in words('SELECT * FROM events ⌶', CLICKHOUSE)
    assert 'UNNEST' not in words('SELECT * FROM events ⌶', ANSI)


@pytest.mark.parametrize('dialect', [ANSI, POSTGRES, CLICKHOUSE, TRINO])
def test_a_statement_form_does_not_borrow_another_forms_clauses(dialect: Dialect) -> None:
    """`RETURNING` after a SELECT's WHERE is a syntax error in every one of them."""
    assert 'RETURNING' not in words('SELECT * FROM events WHERE id = 1 ⌶', dialect)
    assert 'SET' not in words('SELECT * FROM events ⌶', dialect)
    assert 'ON CONFLICT' not in words('SELECT * FROM events ⌶', dialect)


def test_the_form_that_owns_a_clause_still_gets_it() -> None:
    """Gating must not silence the statement the clause belongs to."""
    assert 'RETURNING' in words('UPDATE events SET name = 1 ⌶')
    assert 'RETURNING' in words('DELETE FROM events WHERE id = 1 ⌶')
    assert 'SET' in words('UPDATE events ⌶')
    assert 'ON CONFLICT' in words('INSERT INTO events VALUES (1) ⌶')


def test_a_clause_already_written_is_not_offered_again() -> None:
    """`SELECT id ⌶ FROM events` accepting FROM gives `FROM FROM`."""
    assert 'FROM' not in words('SELECT id ⌶ FROM events')
    assert 'WHERE' not in words('SELECT * FROM events ⌶ WHERE id = 1')
    assert 'GROUP BY' not in words('SELECT * FROM events WHERE id = 1 ⌶ GROUP BY id')


def test_a_clause_that_repeats_is_still_offered() -> None:
    """A join may follow a join, and each branch of a set operation has its own SELECT and FROM."""
    assert 'JOIN' in words('SELECT * FROM events e JOIN events f ON e.id = f.id ⌶')
    assert 'SELECT' in words('SELECT id FROM events UNION ⌶')
    assert 'FROM' in words('SELECT id FROM events UNION SELECT id ⌶')


def test_a_set_operation_comes_before_order_by_not_after() -> None:
    """
    `ORDER BY` and `LIMIT` bind to the whole set operation, so nothing may
    follow them but the end of the statement.
    """
    assert 'UNION' in words('SELECT * FROM events WHERE id = 1 ⌶')
    assert 'UNION' not in words('SELECT * FROM events ORDER BY id ⌶')
    assert 'UNION' not in words('SELECT * FROM events LIMIT 10 ⌶')


def test_a_window_spec_does_not_offer_the_statement_tail() -> None:
    """`OVER (PARTITION BY id ⌶` takes ORDER BY, ROWS or RANGE — not LIMIT."""
    offered = words('SELECT count(*) OVER (PARTITION BY id ⌶) FROM events')
    assert 'ORDER BY' in offered
    assert 'LIMIT' not in offered
    assert 'UNION' not in offered


def test_extending_with_a_known_name_refines_it_rather_than_shadowing_it() -> None:
    """Appending a second clause of the same name would leave the first one answering lookups."""
    refined = ANSI.clauses.extend(Clause(name='WHERE', suggests=(Kind.COLUMN,), operators=('~',)))
    assert [c.name for c in refined.clauses].count('WHERE') == 1
    found = refined.get('WHERE')
    assert found is not None
    assert found.operators == ('~',)
