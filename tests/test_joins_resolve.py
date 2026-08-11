"""Foreign keys reaching the engine: capability detection, caching, degradation."""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.resolve import _Reader
from pysqlsuggestions.types import ForeignKey

EDGE = ForeignKey(
    schema='public',
    table='reports_report',
    columns=('author_id',),
    ref_schema='public',
    ref_table='auth_user',
    ref_columns=('id',),
)


class _Constrained:
    """A catalog that answers only the foreign-key question. Nothing else is needed here."""

    def __init__(self) -> None:
        self.calls = 0

    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """Record the call so the test can prove the cache stopped the second one."""
        del schema
        self.calls += 1
        return [EDGE]


class _Bare:
    """
    The four `Catalog` methods and nothing else — an adapter for a backend with no constraints.

    `MemoryCatalog` cannot stand in for this: it implements `foreign_keys`
    unconditionally and so satisfies the protocol even when a fixture declares no
    edges. The distinction under test is the capability being *absent*.
    """

    def schemas(self, catalog: str | None = None) -> Sequence[str]:
        """No namespaces."""
        del catalog
        return []

    def tables(self, schema: str | None = None) -> Sequence[object]:
        """No relations."""
        del schema
        return []

    def columns(self, schema: str | None, table: str) -> Sequence[object]:
        """No columns."""
        del schema, table
        return []

    def functions(self, schema: str | None = None) -> Sequence[object]:
        """No functions."""
        del schema
        return []


def test_reader_returns_nothing_without_the_capability() -> None:
    """A catalog that cannot answer degrades to silence, as every other capability does."""
    reader = _Reader(_Bare(), POSTGRES, None, None)  # type: ignore[arg-type]
    assert reader.foreign_keys('public') == ()


def test_reader_reads_through_the_capability() -> None:
    """Present: the edges come back as the catalog reported them."""
    reader = _Reader(_Constrained(), POSTGRES, None, None)  # type: ignore[arg-type]
    assert list(reader.foreign_keys('public')) == [EDGE]


def test_reader_caches_edges_under_the_identity_led_key() -> None:
    """Constraints change on DDL, not between keystrokes, so one read serves the session."""
    catalog = _Constrained()
    cache: dict[object, object] = {}
    reader = _Reader(catalog, POSTGRES, cache, 'analyst')  # type: ignore[arg-type]
    reader.foreign_keys('public')
    reader.foreign_keys('public')
    assert catalog.calls == 1
    assert ('analyst', 'postgres', 'public', '\x00fk') in cache
