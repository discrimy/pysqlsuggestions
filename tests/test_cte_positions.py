"""
The five carets a `WITH` clause has, and what belongs at each.

Two of them answer nothing and are right to: a CTE name is the author's to
invent, and no engine can guess it. The other three had no answer either, which
is what this fixes — `WITH` declared no `suggests`, no `followed_by`, and
nothing declared it `follows`, so its continuations were empty and every
position fell through.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {('public', 'auth_user'): [('id', 'bigint'), ('email', 'varchar')]}


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, MemoryCatalog(SNAPSHOT))]


def test_a_cte_body_offers_the_statements_it_may_contain() -> None:
    """
    Every one of these plans on Postgres — the data-modifying CTEs and the
    nested `WITH` included — so this is the position's whole answer rather than
    a selection from it.
    """
    found = offered('WITH a AS (')
    assert 'SELECT' in found
    assert 'VALUES' in found
    assert 'WITH' in found


def test_the_body_offers_nothing_else() -> None:
    """
    A whole statement belongs there and nothing smaller. Offering a column or a
    relation would propose `WITH a AS (auth_user`, which parses as nothing.
    """
    assert 'auth_user' not in offered('WITH a AS (')
    assert 'id' not in offered('WITH a AS (')


def test_a_typed_body_belongs_to_the_statement_in_it() -> None:
    """
    The rule cannot fire twice: once a word is typed the governing clause is
    that statement's, not `WITH`'s, so the body's word list is unreachable and
    needs no guard against being offered again.
    """
    assert 'auth_user' in offered('WITH a AS (SELECT * FROM ')
