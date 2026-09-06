"""
The relation list a FROM clause needs, without the ones it can never name.

`tables()` returns everything in the catalog, indexes and sequences included,
because `DROP INDEX ⌶` reads that same list and wants precisely what every other
position exists to hide. On a 5000-table schema that is 20 000 rows fetched to
serve 5000: three times the query, four times the cached payload, and 24 ms of
JSON decode on every keystroke against a `ByteCache`.

So the narrow read is a capability and the broad one stays exactly as it was.
Nothing is taken away from `Catalog.tables`, which is what keeps `DROP INDEX`
and the sequence positions working on an adapter that knows nothing about this.
"""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.ports import SupportsQueryableRelations
from pysqlsuggestions.types import Table

SNAPSHOT = {
    ('public', 'orders'): [('id', 'bigint'), ('total', 'numeric')],
    ('public', 'customers'): [('id', 'bigint')],
    ('public', 'orders_pkey'): [('id', 'bigint')],
    ('public', 'orders_id_seq'): [('last_value', 'bigint')],
}
KINDS = {
    ('public', 'orders'): 'table',
    ('public', 'customers'): 'table',
    ('public', 'orders_pkey'): 'index',
    ('public', 'orders_id_seq'): 'sequence',
}


class Counting(MemoryCatalog):
    """Records which read answered, without the capability."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.broad = 0
        self.narrow = 0

    def tables(self, schema: str | None = None) -> Sequence[Table]:
        """Everything, the way `Catalog.tables` always has."""
        self.broad += 1
        return super().tables(schema)


class Narrowing(Counting):
    """The same catalog, able to answer without the unqueryable kinds."""

    def queryable_tables(self, schema: str | None = None) -> Sequence[Table]:
        """What a FROM clause could name."""
        self.narrow += 1
        return [row for row in MemoryCatalog.tables(self, schema) if row.kind not in ('index', 'sequence')]


def catalog(narrowing: bool = True) -> Counting:
    """A snapshot holding two tables, an index and a sequence."""
    builder = Narrowing if narrowing else Counting
    return builder(SNAPSHOT, table_kinds=KINDS)


def offered(sql: str, source: Counting) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, source)]


def test_the_capability_is_detected_at_runtime() -> None:
    """The `isinstance` check every capability here is found by."""
    assert isinstance(catalog(), SupportsQueryableRelations)
    assert not isinstance(catalog(narrowing=False), SupportsQueryableRelations)


def test_a_from_clause_uses_the_narrow_read() -> None:
    """The position this exists for, and the one that runs on every keystroke."""
    source = catalog()
    found = offered('SELECT * FROM ', source)
    assert 'orders' in found
    assert 'orders_pkey' not in found
    assert 'orders_id_seq' not in found
    assert (source.narrow, source.broad) == (1, 0)


def test_drop_index_still_reads_the_whole_catalog() -> None:
    """
    The reason the broad read cannot simply be narrowed.

    `DROP INDEX ⌶` wants exactly what every other position exists to hide, and it
    is the one caret for which fetching twenty thousand rows is the right answer.
    """
    source = catalog()
    found = offered('DROP INDEX ', source)
    assert 'orders_pkey' in found
    assert 'orders' not in found
    assert source.broad == 1


def test_a_sequence_position_still_reads_the_whole_catalog() -> None:
    """
    Sequences are hidden from FROM by the same exclusion, and wanted here.

    Offered already quoted: the caret is inside a string literal, which is where
    `nextval` takes its argument, so the suggestion carries the quotes it needs.
    """
    source = catalog()
    assert "'orders_id_seq'" in offered("SELECT nextval('", source)
    assert source.broad == 1


def test_a_catalog_without_the_capability_offers_the_same_thing() -> None:
    """
    Absence costs rows over the wire and nothing else.

    The rule every capability in `ports.py` is held to. `MemoryCatalog` has no
    narrow read, and every position must answer as it did before this existed.
    """
    for sql in ('SELECT * FROM ', 'DROP INDEX ', 'SELECT * FROM orders o WHERE '):
        assert offered(sql, catalog()) == offered(sql, catalog(narrowing=False)), sql


def test_the_two_reads_are_cached_apart() -> None:
    """
    One key each, or the narrow answer would be served where the broad one was asked for.

    The failure this prevents is silent and one-directional: a `FROM ⌶` writing
    its index-free list under the key `DROP INDEX ⌶` reads would empty that
    position for as long as the entry lived, and nothing about the suggestions
    would say why.
    """
    from pysqlsuggestions.caches import MemoryCache

    cache = MemoryCache(default_ttl=None, maxsize=None)
    source = catalog()
    assert 'orders' in offered('SELECT * FROM ', catalog())

    complete('SELECT * FROM ', len('SELECT * FROM '), POSTGRES, source, cache=cache)
    dropped = 'DROP INDEX '
    found = [s.text for s in complete(dropped, len(dropped), POSTGRES, source, cache=cache)]
    assert 'orders_pkey' in found
