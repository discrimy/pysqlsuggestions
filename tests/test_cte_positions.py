"""
The five carets a `WITH` clause has, and what belongs at each.

Two of them answer nothing and are right to: a CTE name is the author's to
invent, and no engine can guess it. The other three had no answer either, which
is what this fixes — `WITH` declared no `suggests`, no `followed_by`, and
nothing declared it `follows`, so its continuations were empty and every
position fell through.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
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


def test_a_name_is_the_authors_to_invent() -> None:
    """
    Both positions where a CTE name goes answer nothing, and both did before
    this change. An engine cannot guess a name, and offering keywords where one
    belongs would be worse than silence.
    """
    assert offered('WITH ') == []
    assert offered('WITH a AS (SELECT 1), ') == []


def test_after_the_name_comes_as() -> None:
    """
    `AS` is the only word that can follow a CTE name, and it leads. It had no
    answer at all before.

    And it is the only word offered. `followed_by` is one list serving this
    position and the one after the body, so the statement words rode along here
    too until the acceptance sweep caught `WITH recent SELECT` — a statement the
    server refuses. A clause that opens a group has a mandatory alias word, by
    definition: that word is what introduces the group.
    """
    assert offered('WITH a ') == ['AS']


def test_after_the_body_comes_the_statement_it_feeds() -> None:
    """
    `AS` is spent by now — it is in the item's words — so `_unspent_alias` drops
    it, which is the whole of what separates this position from the one above.
    """
    found = offered('WITH a AS (SELECT 1) ')
    assert 'SELECT' in found
    assert 'AS' not in found


def test_recursive_is_offered_behind_a_prefix() -> None:
    """
    Like `DISTINCT` after `SELECT`: it stands between the clause and its first
    item, it is rare, and a CTE name is what usually follows `WITH` — so it
    surfaces once something is typed rather than above every caret.
    """
    assert 'RECURSIVE' in offered('WITH rec')


def test_recursive_is_not_read_as_a_cte_name() -> None:
    """
    Without the word reserved, the analyser reads it as a name already written
    and offers `AS` — where another name belongs. All three shipped backends
    accept `WITH RECURSIVE`, and only Trino reserved the word.
    """
    assert offered('WITH RECURSIVE ') == []


def test_postgres_takes_a_data_modifying_cte() -> None:
    """
    `WITH a AS (INSERT INTO … RETURNING id) SELECT * FROM a` plans, and so do
    the UPDATE and DELETE forms. A Postgres extension: ClickHouse refuses the
    same statement with a syntax error.
    """
    found = offered('WITH a AS (')
    assert 'INSERT INTO' in found
    assert 'UPDATE' in found
    assert 'DELETE FROM' in found


def test_postgres_takes_one_after_the_list_too() -> None:
    """`WITH a AS (SELECT 1) INSERT INTO … SELECT x FROM a` plans."""
    assert 'INSERT INTO' in offered('WITH a AS (SELECT 1) ')


def test_clickhouse_keeps_the_conservative_body() -> None:
    """
    Inherited rather than declared, and the refusal is why: a dialect that
    cannot run the statement should not offer the word that starts it.
    """
    sql = 'WITH a AS ('
    found = [s.text for s in complete(sql, len(sql), CLICKHOUSE, MemoryCatalog(SNAPSHOT))]
    assert 'SELECT' in found
    assert 'INSERT INTO' not in found


@pytest.mark.parametrize(
    'statement',
    [
        'SELECT ',
        'SELECT id, ',
        'SELECT * FROM ',
        'SELECT * FROM auth_user ',
        'SELECT * FROM auth_user WHERE ',
        'SELECT * FROM auth_user WHERE id = ',
        'SELECT * FROM auth_user GROUP BY ',
        'SELECT * FROM auth_user ORDER BY ',
        'SELECT * FROM auth_user u WHERE u.',
    ],
)
def test_a_cte_body_answers_as_the_statement_it_is(statement: str) -> None:
    """
    Wrapping a statement in `WITH a AS (...)` changes its scope, never its positions.

    An invariant rather than a list of cases, and it earns that by having been
    broken: `opens_a_name_list` asked what introduced the paren the caret sits
    in, which at `WITH a AS (SELECT * FROM ⌶` is the CTE's own `AS` — so the
    whole body went quiet. The paren the caret sits in only means anything
    relative to the clause governing the caret, and there that clause is FROM,
    inside a paren belonging to WITH.

    Sampling instances would have caught that too; stating the rule says why it
    was wrong. The alias position is included because the CTE name occupies a
    name and the generator could reasonably have differed there — it does not.
    """
    assert offered(f'WITH a AS ({statement}') == offered(statement)
