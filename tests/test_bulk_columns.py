"""
One column read for every relation in scope, instead of one per relation.

`_columns_of` asked the catalog once per relation, so a twenty-way join issued
twenty-one queries for a single keystroke. Free against a local server and the
whole latency budget against a real one: measured at 55ms locally and 495ms with
a 20ms round trip, and flat in the size of the catalog — the cost is the join
count, so a hundred-table database pays exactly what a warehouse does.

Unlike most of what `resolve` fetches, these keys are known before any I/O
happens: `request.scope` names every relation. That is the whole reason this is
reachable when `docs/gaps.md` §5 says batching is not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pysqlsuggestions.api import complete
from pysqlsuggestions.caches import MemoryCache
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.ports import SupportsBulkColumns
from pysqlsuggestions.types import Column

SNAPSHOT = {
    ('public', 'orders'): [('id', 'bigint'), ('customer_id', 'bigint'), ('total', 'numeric')],
    ('public', 'customers'): [('id', 'bigint'), ('email', 'text')],
    ('public', 'invoices'): [('id', 'bigint'), ('paid_at', 'timestamptz')],
    ('billing', 'ledger'): [('id', 'bigint'), ('balance', 'numeric')],
}

JOINED = 'SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id JOIN invoices i ON i.id = o.id WHERE '


class Counting(MemoryCatalog):
    """A catalog that records how it was asked, without the bulk capability."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.singles: list[tuple[str | None, str]] = []
        self.bulk: list[tuple[tuple[str | None, str], ...]] = []

    def columns(self, schema: str | None, table: str) -> Sequence[Column]:
        """Record, then answer as the snapshot does."""
        self.singles.append((schema, table))
        return super().columns(schema, table)


class Bulk(Counting):
    """The same catalog, answering several relations at once."""

    def columns_for(
        self,
        relations: Sequence[tuple[str | None, str]],
    ) -> Mapping[tuple[str | None, str], Sequence[Column]]:
        """Every relation asked for, keyed as asked."""
        self.bulk.append(tuple(relations))
        return {key: MemoryCatalog.columns(self, *key) for key in relations}


def test_the_capability_is_detected_at_runtime() -> None:
    """The `isinstance` check every capability here is found by."""
    assert isinstance(Bulk(SNAPSHOT), SupportsBulkColumns)
    assert not isinstance(Counting(SNAPSHOT), SupportsBulkColumns)


def test_three_relations_in_scope_cost_one_read() -> None:
    """The gap itself: this was one query per relation."""
    catalog = Bulk(SNAPSHOT)
    complete(JOINED, len(JOINED), POSTGRES, catalog)
    assert len(catalog.bulk) == 1
    assert sorted(catalog.bulk[0]) == [(None, 'customers'), (None, 'invoices'), (None, 'orders')]
    assert catalog.singles == []


def test_a_catalog_without_the_capability_still_answers() -> None:
    """
    Absence costs round trips and nothing else.

    The rule every capability in `ports.py` is held to, and the reason adding one
    is safe: the suggestions are the same, and only the number of reads differs.
    """
    plain, bulk = Counting(SNAPSHOT), Bulk(SNAPSHOT)
    assert [s.text for s in complete(JOINED, len(JOINED), POSTGRES, plain)] == [
        s.text for s in complete(JOINED, len(JOINED), POSTGRES, bulk)
    ]
    assert len(plain.singles) == 3
    assert plain.bulk == []


def test_one_relation_in_scope_does_not_use_the_bulk_read() -> None:
    """
    A batch of one is a single read spelled the long way.

    Worth pinning because it is the commonest statement there is, and routing it
    through a query whose text varies with the batch size would cost every
    single-relation caret its server-side plan cache for nothing.
    """
    catalog = Bulk(SNAPSHOT)
    sql = 'SELECT * FROM orders o WHERE '
    complete(sql, len(sql), POSTGRES, catalog)
    assert catalog.bulk == []
    assert catalog.singles == [(None, 'orders')]


def test_the_bulk_read_fills_the_cache_one_relation_at_a_time() -> None:
    """
    A batch is a transport detail, not a cache key.

    Keyed per relation, a second statement sharing two of its three relations
    pays for one. Keyed per batch it would pay for three, and the two
    optimisations would be competing rather than compounding.
    """
    cache = MemoryCache(default_ttl=None, maxsize=None)
    first = Bulk(SNAPSHOT)
    complete(JOINED, len(JOINED), POSTGRES, first, cache=cache)

    second = Bulk(SNAPSHOT)
    warm = 'SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id WHERE '
    complete(warm, len(warm), POSTGRES, second, cache=cache)
    assert second.bulk == []
    assert second.singles == []


def test_only_the_relations_still_missing_are_asked_for() -> None:
    """A partly warm cache asks for the remainder, not for the whole scope again."""
    cache = MemoryCache(default_ttl=None, maxsize=None)
    warm = 'SELECT * FROM orders o WHERE '
    complete(warm, len(warm), POSTGRES, Bulk(SNAPSHOT), cache=cache)

    catalog = Bulk(SNAPSHOT)
    complete(JOINED, len(JOINED), POSTGRES, catalog, cache=cache)
    assert sorted(catalog.bulk[0]) == [(None, 'customers'), (None, 'invoices')]


def test_a_relation_the_catalog_omits_is_not_asked_for_again() -> None:
    """
    An absent key means "no columns", which is what a role that cannot see it gets.

    A mapping rather than a flat sequence is what makes that expressible: asked
    for and absent, and never asked for, would otherwise look identical.
    """

    class Partial(Bulk):
        """Answers for everything but `invoices`."""

        def columns_for(
            self,
            relations: Sequence[tuple[str | None, str]],
        ) -> Mapping[tuple[str | None, str], Sequence[Column]]:
            """Drop one relation from the answer, the way a privilege filter would."""
            found = super().columns_for(relations)
            return {key: value for key, value in found.items() if key[1] != 'invoices'}

    catalog = Partial(SNAPSHOT)
    found = [s.text for s in complete(JOINED, len(JOINED), POSTGRES, catalog)]
    assert 'o.total' in found
    assert not any(text.startswith('i.') for text in found), found
    assert catalog.singles == []
