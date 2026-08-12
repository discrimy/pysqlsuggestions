"""
Relations the connection's default namespace does not cover.

`FROM invo⌶` found nothing when `invoices` lived in a schema outside the search
path, because the only question the engine asked was "what is visible by
default". The answer is a second question — "where does this name live" — and a
result that knows its own schema, so the insertion can qualify.
"""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {
    ('public', 'reports'): [('id', 'bigint'), ('name', 'text')],
    ('public', 'report_runs'): [('id', 'bigint')],
    ('billing', 'invoices'): [('id', 'bigint'), ('amount', 'numeric')],
    ('billing', 'reports_archive'): [('id', 'bigint')],
}


def catalog() -> MemoryCatalog:
    """Two schemas, one of them off the search path."""
    return MemoryCatalog(SNAPSHOT, search_path=('public',))


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def test_a_relation_outside_the_search_path_is_found() -> None:
    """The gap itself: this offered nothing at all."""
    assert 'billing.invoices' in offered('SELECT * FROM invo')


def test_it_is_offered_qualified_and_inserts_qualified() -> None:
    """A bare `invoices` would not resolve, which is why the schema travels with the row."""
    sql = 'SELECT * FROM invo'
    [found] = [s for s in complete(sql, len(sql), POSTGRES, catalog()) if s.text == 'billing.invoices']
    assert apply_suggestion(sql, found, dialect=POSTGRES)[0] == 'SELECT * FROM billing.invoices'


def test_matching_runs_against_the_bare_name() -> None:
    """Nobody types the schema to find a relation; the qualifier is about insertion."""
    assert 'billing.invoices' in offered('SELECT * FROM voic')


def test_an_in_path_relation_stays_bare() -> None:
    """`FROM public.reports` reads worse than `FROM reports` and says nothing more."""
    found = offered('SELECT * FROM repo')
    assert 'reports' in found
    assert 'public.reports' not in found


def test_a_relation_is_offered_once() -> None:
    """
    It comes back from both calls — the default listing and the search — and the
    two render differently, so rank's own dedupe cannot catch it.
    """
    assert offered('SELECT * FROM repo').count('reports') == 1


def test_what_needs_no_qualifying_leads() -> None:
    """Both match equally well, and one costs a schema prefix to use."""
    found = offered('SELECT * FROM repo')
    assert found.index('reports') < found.index('billing.reports_archive')


def test_a_better_match_still_wins() -> None:
    """The in-path preference is a tiebreak, not a veto: match quality dominates."""
    found = offered('SELECT * FROM invo')
    assert found[0] == 'billing.invoices'


def test_an_empty_prefix_runs_no_search() -> None:
    """`FROM <caret>` would otherwise ask for every relation in the database."""
    source = catalog()
    complete('SELECT * FROM ', 14, POSTGRES, source)
    assert not [call for call in source.calls if call[0] == 'search_relations']


def test_a_catalog_without_the_capability_is_unchanged() -> None:
    """Absent, the position answers exactly what it answered before this existed."""

    class Plain:
        """A catalog with the four required methods and no capabilities."""

        def __init__(self, inner: MemoryCatalog) -> None:
            self._inner = inner

        def schemas(self, catalog: str | None = None) -> list[str]:
            """Delegate."""
            return list(self._inner.schemas(catalog))

        def tables(self, schema: str | None = None) -> list[object]:
            """Delegate."""
            return list(self._inner.tables(schema))

        def columns(self, schema: str | None, table: str) -> list[object]:
            """Delegate."""
            return list(self._inner.columns(schema, table))

        def functions(self, schema: str | None = None) -> list[object]:
            """Delegate."""
            return list(self._inner.functions(schema))

    sql = 'SELECT * FROM invo'
    assert [s.text for s in complete(sql, len(sql), POSTGRES, Plain(catalog()))] == []  # type: ignore[arg-type]
