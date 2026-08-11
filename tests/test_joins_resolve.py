"""Foreign keys reaching the engine: capability detection, caching, degradation."""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.resolve import _Reader
from pysqlsuggestions.types import Candidate, ForeignKey, Kind, Request

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


def test_a_join_proposal_outranks_the_tables_it_sits_among() -> None:
    """At `JOIN <caret>` the proposal is a better answer than the bare name it contains."""
    request = Request(kinds=(Kind.TABLE, Kind.SCHEMA, Kind.KEYWORD), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(text='auth_user', kind=Kind.TABLE),
        Candidate(
            text='auth_user au ON r.author_id = au.id',
            kind=Kind.JOIN,
            snippet='auth_user au ON r.author_id = au.id',
            label='auth_user',
            note='fk: auth_user.id',
        ),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'auth_user au ON r.author_id = au.id'
    assert found[0].note == 'fk: auth_user.id'


def test_a_join_proposal_scores_as_a_column_where_columns_belong() -> None:
    """At `ON <caret>` there is no TABLE kind to borrow, so it takes COLUMN's place."""
    request = Request(kinds=(Kind.COLUMN, Kind.FUNCTION), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(text='id', kind=Kind.COLUMN),
        Candidate(text='r.author_id = u.id', kind=Kind.JOIN, snippet='r.author_id = u.id', label='author_id'),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'r.author_id = u.id'


def test_forward_outranks_reverse() -> None:
    """Many-to-one is more often wanted and cannot multiply the result set."""
    request = Request(kinds=(Kind.TABLE,), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(text='b ON u.id = b.user_id', kind=Kind.JOIN, snippet='b ON u.id = b.user_id', label='b', position=1),
        Candidate(text='a ON r.a_id = a.id', kind=Kind.JOIN, snippet='a ON r.a_id = a.id', label='a', position=0),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'a ON r.a_id = a.id'


def test_typed_prefix_still_decides() -> None:
    """Match strength stays dominant, so a proposal for another table falls away."""
    request = Request(kinds=(Kind.TABLE,), prefix='auth', replace_span=(0, 4))
    candidates = [
        Candidate(
            text='orders o ON r.o_id = o.id',
            kind=Kind.JOIN,
            snippet='orders o ON r.o_id = o.id',
            label='orders',
        ),
        Candidate(text='auth_user', kind=Kind.TABLE),
    ]
    found = rank(candidates, request, POSTGRES)
    assert [s.text for s in found] == ['auth_user']
