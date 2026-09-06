"""
How many rows a prefix search asks for, and why one number rather than two.

A cross-relation search truncates on the server, and the truncation is applied by
the one component that cannot rank: the server orders by match position, name
length and then the alphabet, while the engine ranks by match strength, kind,
declaration order and — most decisively — whether the relation is reachable
without qualifying it.

So the rows the server keeps are the rows the engine gets to rank, and a column
that would have been ranked first can be thrown away by an alphabetical
tie-break. Measured: 700 relations named `aaa_*` in a schema off the search path
hide the one `public.zzz_orders` column that a bare `SELECT user_ref` could
actually reference.

Raising the count does not fix that, and is not pretended to. It moves the
boundary — the defect above disappears at exactly 701 rows — which is worth
something and is not a guarantee. What this module pins is narrower and does
hold: the two places that truncate must agree, so no row is fetched and then
discarded unranked.
"""

from __future__ import annotations

import re

import pytest

from pysqlsuggestions.dialects.base import SEARCH_ROWS, Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO

_LIMIT = re.compile(r'LIMIT\s+(\d+)')


@pytest.mark.parametrize('dialect', [POSTGRES, CLICKHOUSE, TRINO], ids=lambda d: d.name)
def test_a_search_query_asks_for_exactly_what_the_engine_will_keep(dialect: Dialect) -> None:
    """
    The server's LIMIT and the row count `resolve` asks for are one number.

    They used to be two. The shipped queries stopped at 500 while `resolve` passed
    `limit * 5`, which is 200 at the default — so the server ranked and returned
    five hundred rows, the adapter kept two hundred, and three hundred were
    computed and thrown away. The engine never saw them, so they bought nothing.

    Built from the constant rather than checked against it, so the two cannot
    drift apart; this asserts that the substitution actually happened.
    """
    for name in ('column_search', 'relation_search'):
        query = getattr(dialect.catalog_queries, name)
        if query is None:
            continue
        found = _LIMIT.findall(query.sql)
        assert found, f'{dialect.name}.{name} truncates nowhere'
        assert [int(value) for value in found] == [SEARCH_ROWS], f'{dialect.name}.{name}'


def test_the_row_count_is_not_the_suggestion_limit() -> None:
    """
    Two different questions, and tying them together was what made them disagree.

    `limit` is how many suggestions to show a person. `SEARCH_ROWS` is how many
    candidates to rank in order to choose them, and it wants to be far larger:
    ranking is cheap now and the extra rows are the only defence against the
    server's ordering discarding something the engine would have ranked first.
    """
    assert SEARCH_ROWS >= 1000


def test_a_small_display_limit_does_not_narrow_the_search() -> None:
    """
    Asking for five suggestions must consider the same candidates as asking for forty.

    These were tied together and it was the whole bug: the rows fetched came from
    `limit * 5`, so a caller who wanted a short list silently searched a smaller
    part of the database and could be handed a worse best answer, not merely
    fewer of them.

    Asserted through `complete`, because that is where the coupling lived.
    """
    from pysqlsuggestions.api import complete
    from pysqlsuggestions.catalogs.memory import MemoryCatalog

    snapshot = {
        ('public', f'relation_{index:03d}'): [('id', 'bigint'), (f'user_ref_{index}', 'bigint')] for index in range(60)
    }
    catalog = MemoryCatalog(snapshot, search_path=('public',))

    sql = 'SELECT user_ref'
    short = [s.text for s in complete(sql, len(sql), POSTGRES, catalog, limit=5)]
    long = [s.text for s in complete(sql, len(sql), POSTGRES, catalog, limit=40)]
    assert short == long[:5]
