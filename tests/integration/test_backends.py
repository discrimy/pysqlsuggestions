"""
Completion against the real backends.

These are the only tests that can catch a wrong pg_proc join, a mis-parsed
system.columns type string, or a paramstyle rewrite that produces valid-but-wrong
SQL. A fixture cannot.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import Kind
from tests.corpus.cases import split_caret

pytestmark = pytest.mark.integration


def suggest(marked: str, dialect: object, catalog: DbapiCatalog) -> list[str]:
    """Suggestion texts for ⌶-marked SQL against a live backend."""
    sql, caret = split_caret(marked)
    return [s.text for s in complete(sql, caret, dialect, catalog)]  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# PostgreSQL
# --------------------------------------------------------------------------- #


def test_postgres_columns_in_declaration_order(postgres_catalog: DbapiCatalog) -> None:
    """attnum order, straight from pg_attribute."""
    found = suggest('SELECT * FROM reports_database d WHERE d.⌶', POSTGRES, postgres_catalog)
    assert found[:5] == ['id', 'dt_created', 'dt_modified', 'title', 'type']


def test_postgres_types_are_readable(postgres_catalog: DbapiCatalog) -> None:
    """format_type gives the type a user would recognise."""
    columns = postgres_catalog.columns('public', 'reports_database')
    types = {c.name: c.type for c in columns}
    assert types['title'] == 'character varying(256)'
    assert types['port'] == 'integer'


def test_postgres_schema_qualifier(postgres_catalog: DbapiCatalog) -> None:
    """`billing.` lists that schema, and the mixed-case relation comes back quoted."""
    found = suggest('SELECT * FROM billing.⌶', POSTGRES, postgres_catalog)
    assert 'invoices' in found
    assert '"MonthlyTotals"' in found


def test_postgres_unqualified_position_hides_the_system_catalog(postgres_catalog: DbapiCatalog) -> None:
    """pg_table_is_visible is true for pg_catalog, so `FROM <caret>` would open with pg_aggregate."""
    found = suggest('SELECT * FROM ⌶', POSTGRES, postgres_catalog)
    assert 'reports_report' in found
    assert not [name for name in found if name.startswith('pg_')]


def test_postgres_system_schema_still_reachable_when_named(postgres_catalog: DbapiCatalog) -> None:
    """Hiding it by default must not make it unreachable."""
    assert 'pg_class' in [t.name for t in postgres_catalog.tables('pg_catalog')]


def test_postgres_search_path_relation(postgres_catalog: DbapiCatalog) -> None:
    """An unqualified relation resolves through pg_table_is_visible."""
    columns = postgres_catalog.columns(None, 'reports_report')
    assert 'executions' in {c.name for c in columns}


def test_postgres_views_are_relations_too(postgres_catalog: DbapiCatalog) -> None:
    """relkind is normalised inside the dialect; 'v' never leaks out."""
    kinds = {t.name: t.kind for t in postgres_catalog.tables('public')}
    assert kinds['reports_active'] == 'view'
    assert kinds['reports_report'] == 'table'


def test_postgres_join_across_the_real_foreign_key(postgres_catalog: DbapiCatalog) -> None:
    """
    The ON clause narrows to the joined relation's columns, and then to the
    ones that can face a bigint: `d.title = r.database_id` does not typecheck
    in Postgres any more than `d.title > 1` does.
    """
    found = suggest(
        'SELECT * FROM reports_report r JOIN reports_database d ON r.database_id = d.⌶',
        POSTGRES,
        postgres_catalog,
    )
    assert 'id' in found
    assert 'title' not in found, 'varchar cannot be compared with the bigint on the left'
    assert 'executions' not in found, 'that column belongs to r, not d'


def test_postgres_schemas_survive_the_literal_percent(postgres_catalog: DbapiCatalog) -> None:
    r"""The schemas query filters with LIKE 'pg\_%', which psycopg2 would misread unescaped."""
    found = postgres_catalog.schemas()
    assert 'public' in found
    assert 'billing' in found
    assert not [name for name in found if name.startswith('pg_')]


def test_postgres_functions(postgres_catalog: DbapiCatalog) -> None:
    """pg_proc, with real signatures."""
    names = {f.name for f in postgres_catalog.functions()}
    assert 'count' in names
    assert 'now' in names


# --------------------------------------------------------------------------- #
# ClickHouse
# --------------------------------------------------------------------------- #


def test_clickhouse_columns(clickhouse_catalog: DbapiCatalog) -> None:
    """system.columns, in position order."""
    found = suggest('SELECT * FROM report_executions e WHERE e.⌶', CLICKHOUSE, clickhouse_catalog)
    assert found[:4] == ['started_at', 'report_id', 'database_id', 'user_login']


def test_clickhouse_enum_type_carries_its_values(clickhouse_catalog: DbapiCatalog) -> None:
    """The values are embedded in the type string — free value hints, later."""
    columns = clickhouse_catalog.columns('analytics', 'report_executions')
    status = next(c for c in columns if c.name == 'status')
    assert "'ok' = 1" in status.type
    assert 'timeout' in status.type


def test_clickhouse_database_qualifier(clickhouse_catalog: DbapiCatalog) -> None:
    """A ClickHouse database occupies the same namespace level a Postgres schema does."""
    found = suggest('SELECT * FROM staging.⌶', CLICKHOUSE, clickhouse_catalog)
    assert found == ['report_executions']


def test_clickhouse_preserves_case_without_quoting(clickhouse_catalog: DbapiCatalog) -> None:
    """
    ClickHouse does not fold identifiers, so a mixed-case name needs no quotes.

    Matching stays case-insensitive as a ranking fallback — `report_N` finds
    `report_name` — because case preservation governs the text that gets
    inserted, not what the user has to type to find it.
    """
    assert suggest('SELECT * FROM report_dim d WHERE d.report_N⌶', CLICKHOUSE, clickhouse_catalog) == ['report_name']
    assert suggest('SELECT * FROM report_dim d WHERE d.group_⌶', CLICKHOUSE, clickhouse_catalog) == ['group_name']


def test_clickhouse_functions_are_introspected(clickhouse_catalog: DbapiCatalog) -> None:
    """Thousands of them, so they are read rather than shipped."""
    names = {f.name for f in clickhouse_catalog.functions()}
    assert 'toYYYYMM' in names
    assert len(names) > 100


# --------------------------------------------------------------------------- #
# Trino — three namespace levels, against two federated backends
# --------------------------------------------------------------------------- #


def test_trino_catalog_qualifier_yields_schemas(trino_catalog: DbapiCatalog) -> None:
    """The divergence the whole dialect design exists for."""
    found = suggest('SELECT * FROM clickhouse.⌶', TRINO, trino_catalog)
    assert 'analytics' in found
    assert 'staging' in found


def test_trino_unqualified_position_offers_catalogs_not_schemas(trino_catalog: DbapiCatalog) -> None:
    """
    With three levels, the first thing to write is a catalog.

    Offering schemas here would put the second level in the first position, and
    enumerating every table in every catalog would scan each connector's metadata
    on a keystroke.
    """
    found = suggest('SELECT * FROM ⌶', TRINO, trino_catalog)
    assert 'postgresql' in found
    assert 'clickhouse' in found
    assert 'reports_report' not in found


def test_the_first_qualifier_segment_means_a_different_level_per_dialect(
    trino_catalog: DbapiCatalog,
    postgres_catalog: DbapiCatalog,
) -> None:
    """
    The same caret position, one segment deep, resolves to a different level.

    In Postgres that segment is a schema, so the answer is its tables. In Trino
    it is a catalog, so the answer is its schemas. Both queries are the same
    shape; only `Namespace.levels` differs.
    """
    postgres_sql, postgres_caret = split_caret('SELECT * FROM billing.⌶')
    trino_sql, trino_caret = split_caret('SELECT * FROM postgresql.⌶')

    postgres_found = complete(postgres_sql, postgres_caret, POSTGRES, postgres_catalog)
    trino_found = complete(trino_sql, trino_caret, TRINO, trino_catalog)

    assert {s.kind for s in postgres_found} == {Kind.TABLE}
    assert 'invoices' in [s.text for s in postgres_found]

    assert {s.kind for s in trino_found} == {Kind.SCHEMA}
    assert 'billing' in [s.text for s in trino_found]


def test_a_name_that_is_not_a_catalog_yields_nothing_in_trino(trino_catalog: DbapiCatalog) -> None:
    """`billing` is a schema, not a catalog, so at Trino's first level it names nothing."""
    assert suggest('SELECT * FROM billing.⌶', TRINO, trino_catalog) == []


def test_trino_three_segment_path_reaches_columns(trino_catalog: DbapiCatalog) -> None:
    """catalog.schema.table.<caret> has nowhere left to go but a column."""
    found = suggest(
        'SELECT p.⌶ FROM postgresql.public.reports_report p',
        TRINO,
        trino_catalog,
    )
    assert 'executions' in found
    assert 'name' in found


def test_trino_federated_join_across_catalogs(trino_catalog: DbapiCatalog) -> None:
    """Both relations are in scope, each from a different backend."""
    found = suggest(
        'SELECT * FROM postgresql.public.reports_report p JOIN clickhouse.analytics.report_executions c ON c.⌶',
        TRINO,
        trino_catalog,
    )
    assert 'report_id' in found
    assert 'duration_ms' in found
    assert 'executions' not in found


def test_trino_columns_have_positions(trino_catalog: DbapiCatalog) -> None:
    """ordinal_position from system.jdbc.columns, so ranking keeps declaration order."""
    columns = trino_catalog.columns('public', 'reports_database')
    assert [c.name for c in columns][:4] == ['id', 'dt_created', 'dt_modified', 'title']


def test_trino_functions_are_introspected(trino_catalog: DbapiCatalog) -> None:
    """
    Trino has no `system.metadata.table_functions`, so asking for one raises
    rather than degrading — and every other Trino test uses a position that
    never wants a function, which is how that stayed invisible.
    """
    found = trino_catalog.functions()
    assert found, 'no functions came back'
    assert any(f.name == 'lower' for f in found), sorted({f.name for f in found})[:20]
