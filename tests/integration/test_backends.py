"""
Completion against the real backends.

These are the only tests that can catch a wrong pg_proc join, a mis-parsed
system.columns type string, or a paramstyle rewrite that produces valid-but-wrong
SQL. A fixture cannot.
"""

from __future__ import annotations

import time

import pytest

from pysqlsuggestions.api import apply_suggestion, complete
from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import Kind
from tests.corpus.cases import split_caret
from tests.integration.conftest import POSTGRES_DSN

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


def test_postgres_finds_the_seeded_procedure(postgres_catalog: DbapiCatalog) -> None:
    """
    Stock Postgres 16 ships no procedures at all — pg_proc holds only 'f', 'a'
    and 'w' — so the seed is the only place this assertion can come from.
    """
    found = suggest('CALL ⌶', POSTGRES, postgres_catalog)
    assert 'recalculate_totals' in found


def test_postgres_keeps_the_procedure_out_of_an_expression(postgres_catalog: DbapiCatalog) -> None:
    """
    `SELECT recalculate_totals()` is refused by the server: `… is a procedure`.

    Asserted behind a prefix that matches it, and against a catalog read that
    proves the name is there to be found. Without both, an empty answer would
    pass whether the filter worked or the catalog simply had nothing — and at
    `SELECT ⌶` the ranked list is truncated long before three thousand
    pg_catalog functions are exhausted, so absence there means nothing at all.
    """
    assert 'recalculate_totals' in {f.name for f in postgres_catalog.functions()}
    assert 'recalculate_totals' not in suggest('SELECT recalc⌶', POSTGRES, postgres_catalog)


def test_postgres_offers_no_sequence_where_a_relation_belongs(postgres_catalog: DbapiCatalog) -> None:
    """The seed's bigserial columns create one sequence per table; none of them belongs here."""
    found = suggest('SELECT * FROM ⌶', POSTGRES, postgres_catalog)
    assert 'reports_report' in found
    assert not [name for name in found if name.endswith('_id_seq')]


def test_postgres_writes_a_sequence_literal_the_server_accepts(postgres_catalog: DbapiCatalog) -> None:
    """
    The fact the whole literal half rests on: the string is parsed as a
    regclass, so a mixed-case name keeps its identifier quotes inside it.
    `nextval('billing.MonthlyTotals_id_seq')` is refused with
    `relation "billing.monthlytotals_id_seq" does not exist`.
    """
    psycopg2 = pytest.importorskip('psycopg2')
    # A *terminated* literal in a *closed* call, because the applied statement
    # has to be one the server can parse. `SELECT nextval('Month` would splice
    # correctly and still be missing its closing paren, and EXPLAIN would fail
    # for a reason that has nothing to do with the suggestion.
    sql = "SELECT nextval('Month')"
    caret = sql.index('Month') + len('Month')
    [found] = [s for s in complete(sql, caret, POSTGRES, postgres_catalog) if 'MonthlyTotals' in s.text]
    written = apply_suggestion(sql, found, dialect=POSTGRES)[0]
    assert written == 'SELECT nextval(\'billing."MonthlyTotals_id_seq"\')'
    with psycopg2.connect(POSTGRES_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(f'EXPLAIN {written}')


def test_postgres_plans_a_reference_to_one_of_two_same_named_relations(
    postgres_catalog: DbapiCatalog,
) -> None:
    """
    The whole slice, end to end. `SELECT invoices.amount FROM public.invoices,
    billing.invoices` is refused with `table reference "invoices" is ambiguous`,
    and that is what the engine used to write.

    The acceptance sweep cannot catch this — it reports syntax errors only, and
    an ambiguous reference is semantic — so this is the guard.
    """
    psycopg2 = pytest.importorskip('psycopg2')
    sql = 'SELECT amou FROM public.invoices, billing.invoices'
    found = [s for s in complete(sql, 11, POSTGRES, postgres_catalog) if s.text.endswith('.amount')]
    assert {s.text for s in found} == {'public.invoices.amount', 'billing.invoices.amount'}
    with psycopg2.connect(POSTGRES_DSN) as connection, connection.cursor() as cursor:
        for suggestion in found:
            cursor.execute(f'EXPLAIN {apply_suggestion(sql, suggestion, dialect=POSTGRES)[0]}')


def test_postgres_drop_table_offers_no_view(postgres_catalog: DbapiCatalog) -> None:
    """
    `DROP TABLE public.reports_active` is refused with `"reports_active" is not
    a table`, and this position offered it. The vocabulary is the backend's, so
    only a live catalog settles this.
    """
    found = suggest('DROP TABLE ⌶', POSTGRES, postgres_catalog)
    assert 'reports_report' in found
    assert 'reports_active' not in found


def test_postgres_drop_view_offers_the_view(postgres_catalog: DbapiCatalog) -> None:
    """The opposite narrowing, against the same catalog."""
    found = suggest('DROP VIEW ⌶', POSTGRES, postgres_catalog)
    assert 'reports_active' in found
    assert 'reports_report' not in found


def test_postgres_drop_index_reaches_a_real_index(postgres_catalog: DbapiCatalog) -> None:
    """
    Indexes are fetched now, and this is the only position that wants them. The
    seed declares this one by name.
    """
    found = suggest('DROP INDEX ⌶', POSTGRES, postgres_catalog)
    assert 'reports_report_database_id_idx' in found
    assert 'reports_report' not in found


def test_postgres_drop_materialized_view_offers_the_seeded_one(
    postgres_catalog: DbapiCatalog,
) -> None:
    """Stock Postgres 16 ships none, so the seed is where this assertion comes from."""
    found = suggest('DROP MATERIALIZED VIEW ⌶', POSTGRES, postgres_catalog)
    assert 'reports_monthly' in found
    assert 'reports_active' not in found


def test_postgres_reaches_a_relation_off_the_search_path(postgres_catalog: DbapiCatalog) -> None:
    """
    `billing` is not on the fixture's search path, so `FROM invo` used to find nothing.

    The written statement is planned by the server, because a qualified
    reference that does not resolve is the failure this exists to prevent.
    """
    psycopg2 = pytest.importorskip('psycopg2')
    sql = 'SELECT * FROM invo'
    [found] = [s for s in complete(sql, len(sql), POSTGRES, postgres_catalog) if s.text == 'billing.invoices']
    written = apply_suggestion(sql, found, dialect=POSTGRES)[0]
    assert written == 'SELECT * FROM billing.invoices'

    connection = psycopg2.connect(POSTGRES_DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'EXPLAIN {written}')
    finally:
        connection.close()


def test_postgres_reaches_a_column_off_the_search_path(postgres_catalog: DbapiCatalog) -> None:
    """The column half of the same gap, and the FROM clause it writes for itself."""
    psycopg2 = pytest.importorskip('psycopg2')
    sql = 'SELECT amou'
    found = [s for s in complete(sql, len(sql), POSTGRES, postgres_catalog) if s.relation == ('billing', 'invoices')]
    assert found, 'no column from billing.invoices was offered'
    written = apply_suggestion(sql, found[0], dialect=POSTGRES)[0]

    connection = psycopg2.connect(POSTGRES_DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'EXPLAIN {written}')
    finally:
        connection.close()


def test_trino_is_unchanged(trino_catalog: DbapiCatalog) -> None:
    """
    Trino ships no relation-search query — 179ms per catalog is not a keystroke.

    Asserted rather than assumed, because "we chose not to" and "we broke it"
    look identical from the outside.
    """
    assert TRINO.catalog_queries.relation_search is None
    found = suggest('SELECT * FROM postgresql.public.reports_repo⌶', TRINO, trino_catalog)
    assert 'reports_report' in found


def test_postgres_accepts_an_expanded_star(postgres_catalog: DbapiCatalog) -> None:
    """
    The one thing only a server can settle: that the list we write is a query it runs.

    The acceptance sweep cannot reach this. It truncates each statement at the
    caret, so the FROM clause that gives the star its meaning is cut away and no
    expansion is ever offered there.

    Two relations, so every column comes out qualified — and `reports_database`
    has a column called `user`, which is reserved and must arrive quoted or the
    statement does not parse.
    """
    psycopg2 = pytest.importorskip('psycopg2')
    sql = 'SELECT * FROM reports_report r JOIN reports_database d ON r.database_id = d.id'
    caret = sql.index('*') + 1
    offered = [s for s in complete(sql, caret, POSTGRES, postgres_catalog) if s.kind is Kind.EXPANSION]
    assert len(offered) == 1

    written = apply_suggestion(sql, offered[0], dialect=POSTGRES)[0]
    assert 'r.id' in written, 'a two-relation expansion must qualify'
    assert 'd."user"' in written, 'a reserved column name must arrive quoted'

    connection = psycopg2.connect(POSTGRES_DSN)
    try:
        with connection.cursor() as cursor:
            # EXPLAIN plans and does not execute, so this stays a read.
            cursor.execute(f'EXPLAIN {written}')
    finally:
        connection.close()


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


def test_clickhouse_reaches_a_relation_in_another_database(clickhouse_catalog: DbapiCatalog) -> None:
    """The connection is opened on `analytics`; `staging` is a database it does not default to."""
    sql = 'SELECT * FROM report_exec'
    found = [s.text for s in complete(sql, len(sql), CLICKHOUSE, clickhouse_catalog)]
    assert 'report_executions' in found, 'the default database must still answer bare'
    assert 'staging.report_executions' in found, 'the other database must be reachable qualified'


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


def test_trino_unqualified_columns_stay_inside_the_session_catalog(trino_catalog: DbapiCatalog) -> None:
    """
    `columns(None, ...)` means the catalog this connection is bound to, not every catalog.

    The predicate used to read `($1 = '' OR table_schem = $1)`, and an empty `$1`
    makes that OR vacuously true — so nothing constrained `table_cat` and
    `system.jdbc.columns` answered from every connector at once. This fixture has
    `report_executions` in ClickHouse and nowhere in Postgres, so a
    postgresql-bound catalog came back with ClickHouse's twelve columns for a
    relation Postgres does not have: `FROM some_pg_table o WHERE o.<caret>`
    could offer columns that are not in the relation being queried.

    It was also the slowest read anywhere in the library — 9.8s against 0.06s
    once the catalog is named, because the scan reaches each connector's
    metadata in turn.

    Bound in this branch only. The qualified branch stays unconstrained on
    purpose, because federating across catalogs is what Trino is for; the test
    below joins two of them and would fail if this filter were applied there.
    """
    assert trino_catalog.columns(None, 'report_executions') == []


def test_trino_unqualified_columns_push_the_catalog_filter_down(trino_catalog: DbapiCatalog) -> None:
    """
    The catalog filter has to be a top-level conjunct, or it does not push down.

    A timing assertion, which needs justifying. The correct-looking spelling of
    the test above is one disjunction —

        ($1 = '' AND table_cat = current_catalog OR table_schem = $1)

    — and it passes that test while being *no faster than having no filter at
    all*: Trino pushes conjuncts into a connector and cannot push a disjunction,
    so the scan still reaches every catalog and `table_cat` is applied to the
    rows afterwards. The shipped form spells the same logic as two guarded
    conjuncts precisely so each one can be folded and pushed.

    Nothing but elapsed time can tell those two apart, and the gap they are told
    apart by is 46ms against 9.8s. The budget is therefore two orders of
    magnitude looser than the measurement, in the register `tests/test_scale.py`
    uses: it asserts the shape of the cost, not a benchmark figure, and only a
    genuine loss of pushdown can reach it.
    """
    started = time.perf_counter()
    columns = trino_catalog.columns(None, 'reports_report')
    elapsed = time.perf_counter() - started
    assert columns, 'the query came back empty, so the timing below means nothing'
    assert elapsed < 3.0, f'{elapsed:.1f}s: the catalog filter is no longer pushed down'


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


def test_postgres_offers_values_from_planner_statistics(postgres_catalog: DbapiCatalog) -> None:
    """
    `WHERE is_staff = ⌶` answers from `pg_stats.most_common_vals`.

    A boolean, so the literals go in bare — as `true` and `false`, not as the
    `t` and `f` the statistics report, which are how Postgres *prints* a boolean
    rather than how it parses one. Nothing is read from the table: the values
    are the ones the planner already recorded, which is also why they are only
    there once ANALYZE has run.
    """
    postgres_catalog.columns('public', 'auth_user')  # ensure the relation is reachable at all
    values = postgres_catalog.common_values('public', 'auth_user', 'is_staff', 30)
    if not values:
        pytest.skip('no statistics yet: the database has not been ANALYZEd')
    assert {v.text for v in values} <= {'t', 'f'}
    assert all(v.frequency is not None for v in values), 'statistics carry the share of rows'

    found = suggest(
        'SELECT * FROM auth_user u WHERE u.is_staff = ⌶',
        POSTGRES,
        postgres_catalog,
    )
    assert found[0] in {'true', 'false'}, found[:5]


def test_postgres_asks_the_statistics_view_not_the_table(postgres_catalog: DbapiCatalog) -> None:
    """
    The whole point: a completion engine may not start a scan.

    `pg_stat_statements` is not installed here, so the check is the query text
    itself — it must name `pg_stats` and no user relation.
    """
    query = POSTGRES.catalog_queries.values
    assert query is not None
    assert 'pg_stats' in query.sql
    assert 'auth_user' not in query.sql


def test_postgres_offers_enum_labels_without_statistics(postgres_catalog: DbapiCatalog) -> None:
    """
    An enum type lists every value it permits, which no statistic improves on.

    Postgres reports the column's type as the enum's *name*, so unlike
    ClickHouse the labels are a read of their own — of `pg_enum`, not the table.

    This column is also analysed, so both sources can answer and the query has to
    choose one of them entire: ranked rather than chosen, every label arrives
    twice — once named by the type, once measured by the planner.
    """
    assert any(c.name == 'status' for c in postgres_catalog.columns('public', 'reports_runlog')), (
        'the reports_runlog fixture predates this seed: docker compose -f docker/docker-compose.yml down -v && up'
    )
    assert [v.text for v in postgres_catalog.common_values('public', 'reports_runlog', 'status', 30)] == [
        'queued',
        'running',
        'succeeded',
        'failed',
    ]
    found = suggest('SELECT * FROM reports_runlog r WHERE r.status = ⌶', POSTGRES, postgres_catalog)
    assert found[:2] == ["'queued'", "'running'"]


def test_clickhouse_offers_enum_labels_from_the_type_alone(clickhouse_catalog: DbapiCatalog) -> None:
    """
    `Enum8('PostgreSQL' = 1, ...)` carries its values in the type text the
    columns query already returned, so this costs no query at all — and
    ClickHouse keeps no most-common-values to fall back on.
    """
    found = suggest('SELECT * FROM report_dim d WHERE d.db_type = ⌶', CLICKHOUSE, clickhouse_catalog)
    assert found[:3] == ["'PostgreSQL'", "'ClickHouse'", "'Trino'"]


def test_trino_offers_the_two_boolean_words(trino_catalog: DbapiCatalog) -> None:
    """
    Trino keeps neither most-common-values nor an enum type, so a boolean is
    all its types can enumerate — and that needs no catalog support at all.
    """
    found = suggest(
        'SELECT * FROM postgresql.public.auth_user u WHERE u.is_staff = ⌶',
        TRINO,
        trino_catalog,
    )
    assert found[:2] == ['true', 'false']


def test_postgres_estimates_how_big_a_relation_is(postgres_catalog: DbapiCatalog) -> None:
    """
    `pg_class.reltuples` is the planner's own figure, so it costs nothing and
    needs no count. Negative means never analysed, which is not the same as
    empty and must not be reported as a size.
    """
    sized = {t.name: t.rows for t in postgres_catalog.tables('public')}
    assert sized, 'no relations came back'
    assert all(rows is None or rows >= 0 for rows in sized.values())
    assert any(rows is not None for rows in sized.values()), 'nothing carried an estimate'


def test_clickhouse_estimates_how_big_a_relation_is(clickhouse_catalog: DbapiCatalog) -> None:
    """`system.tables.total_rows` is exact for MergeTree and null for engines that cannot say."""
    sized = {t.name: t.rows for t in clickhouse_catalog.tables('analytics')}
    assert sized, 'no relations came back'
    assert all(rows is None or rows >= 0 for rows in sized.values())


def test_postgres_finds_a_column_before_any_from(postgres_catalog: DbapiCatalog) -> None:
    """
    `SELECT ema⌶` with nothing in the FROM: the column, and the relation it
    would need. Substring rather than prefix, because `mail` finding `email` is
    behaviour the inherited suite already pins.
    """
    assert [(c.table, c.name) for c in postgres_catalog.search_columns('mail', 5)] == [('auth_user', 'email')]
    assert postgres_catalog.search_columns('', 5) == [], 'every column is not an answer'
    assert postgres_catalog.all_columns() is None, 'a live database is never enumerated'

    found = suggest('SELECT ema⌶', POSTGRES, postgres_catalog)
    assert 'auth_user.email' in found


def test_postgres_column_search_is_not_confused_by_a_wildcard(postgres_catalog: DbapiCatalog) -> None:
    """
    `_` is both a LIKE wildcard and the commonest character in a column name.

    Matching with LIKE would make `dt_c` match `dtxc`; this one does not, which
    is why it uses `position(... in ...)`.
    """
    found = {c.name for c in postgres_catalog.search_columns('dt_c', 20)}
    assert found, 'nothing matched at all'
    assert all('dt_c' in name for name in found), found


def test_clickhouse_finds_a_column_before_any_from(clickhouse_catalog: DbapiCatalog) -> None:
    """The same capability, from `system.columns`."""
    found = [(c.table, c.name) for c in clickhouse_catalog.search_columns('report_id', 5)]
    assert found, 'nothing matched'
    assert all(name == 'report_id' for _, name in found), found
    assert clickhouse_catalog.all_columns() is None


def test_trino_offers_no_column_search(trino_catalog: DbapiCatalog) -> None:
    """
    Deliberately absent. Finding out would mean asking every catalog's
    connector in turn, which is the same reason its `tables` is empty for an
    unqualified position.
    """
    assert TRINO.catalog_queries.column_search is None
    assert trino_catalog.search_columns('id', 5) == []


def test_postgres_reads_declared_foreign_keys(postgres_catalog: DbapiCatalog) -> None:
    """The query text itself — only a real server can say whether it runs."""
    edges = {(e.table, e.columns): (e.ref_table, e.ref_columns) for e in postgres_catalog.foreign_keys('public')}
    assert edges[('reports_report', ('author_id',))] == ('auth_user', ('id',))
    assert edges[('reports_report', ('database_id',))] == ('reports_database', ('id',))


def test_postgres_reads_a_composite_key_in_order(postgres_catalog: DbapiCatalog) -> None:
    """WITH ORDINALITY keeps the two sides aligned; a reordered array passes every single-column test."""
    edges = {(e.table, e.columns): (e.ref_table, e.ref_columns) for e in postgres_catalog.foreign_keys('public')}
    key = ('reports_queryfilter_usage', ('queryfilter_id', 'database_id'))
    assert edges[key] == ('reports_queryfilter_databases', ('queryfilter_id', 'database_id'))


def test_postgres_joins_a_real_schema(postgres_catalog: DbapiCatalog) -> None:
    """End to end against the server: the clause the engine writes is the one the schema implies."""
    found = suggest('SELECT * FROM reports_report r JOIN ⌶', POSTGRES, postgres_catalog)
    assert 'auth_user au ON r.author_id = au.id' in found[:5]


def test_postgres_joins_from_the_referenced_side(postgres_catalog: DbapiCatalog) -> None:
    """auth_user holds no FK columns and is referenced by seven tables here."""
    found = suggest('SELECT * FROM auth_user u JOIN ⌶', POSTGRES, postgres_catalog)
    assert [text for text in found if text.startswith('reports_report rr ON u.id = rr.author_id')]


def test_clickhouse_and_trino_declare_no_constraints(
    clickhouse_catalog: DbapiCatalog,
    trino_catalog: DbapiCatalog,
) -> None:
    """Neither backend keeps them, so neither offers a proposal and both positions are unchanged."""
    assert list(clickhouse_catalog.foreign_keys('analytics')) == []
    assert list(trino_catalog.foreign_keys('public')) == []


def test_clickhouse_row_counts_are_integers_not_strings(clickhouse_catalog: DbapiCatalog) -> None:
    """
    UInt64 over JSON is quoted unless the reader says otherwise.

    `Table.rows` feeds ranking, and a string would sort lexically — '9' above
    '10' — which is a wrong order in a list that still looks entirely healthy.
    """
    tables = clickhouse_catalog.tables('analytics')
    counted = [table for table in tables if table.rows is not None]
    assert counted, 'no ClickHouse table reported a row count'
    assert all(isinstance(table.rows, int) for table in counted)


def test_clickhouse_columns_bind_two_parameters(clickhouse_catalog: DbapiCatalog) -> None:
    """The `columns` query is the only one taking $1 and $2, so it is the one that proves binding."""
    columns = clickhouse_catalog.columns('analytics', 'report_executions')
    assert columns
    assert [column.position for column in columns] == sorted(column.position for column in columns)


def test_trino_pages_through_a_result_larger_than_one_response(trino_catalog: DbapiCatalog) -> None:
    """
    `SHOW FUNCTIONS` returns 846 rows on Trino 468, split across six pages.

    Measured, not assumed: one POST and six GETs. A reader that stopped at the
    first `nextUri` would return a plausible-looking subset, and nothing else in
    the suite is big enough to notice.
    """
    functions = trino_catalog.functions()
    assert len(functions) > 500


def test_a_join_reads_every_relation_in_one_query(
    counting_postgres: tuple[DbapiCatalog, list[str]],
) -> None:
    """
    The N+1 this capability exists to remove, asserted as round trips.

    A count rather than a duration. The cost is one query per relation, which is
    free against a fixture on this machine and is the whole latency budget
    against a real server — 55ms here and 495ms at a 20ms round trip — so the
    query count is the honest thing to hold, and it holds at any distance.

    Three relations, one `pg_attribute` read. Without the capability it is three,
    and `tests/test_bulk_columns.py` is where that fallback is pinned; this one
    is here because only a real server can say the spread marker renders to SQL
    Postgres will actually bind and plan.
    """
    catalog, log = counting_postgres
    sql = (
        'SELECT * FROM auth_user u '
        'JOIN reports_report r ON r.user_id = u.id '
        'JOIN reports_database d ON d.id = r.database_id '
        'WHERE '
    )
    found = complete(sql, len(sql), POSTGRES, catalog)

    reads = [text for text in log if 'pg_attribute' in text]
    assert len(reads) == 1, reads
    texts = {suggestion.text for suggestion in found}
    assert {'u.username', 'r.name', 'd.title'} <= texts, sorted(texts)[:20]
