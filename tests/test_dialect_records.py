"""Dialects are composed data. These tests pin the composition mechanics."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Clause, ClauseModel, Namespace, Query, Syntax
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import Availability, Column, ForeignKey, Kind, Table


def test_extend_appends_without_mutating() -> None:
    """A dialect adding a clause must not disturb the model it extended."""
    base = ClauseModel(clauses=(Clause(name='WHERE', suggests=(Kind.COLUMN,)),))
    extended = base.extend(Clause(name='PREWHERE', suggests=(Kind.COLUMN,)))
    assert [c.name for c in base.clauses] == ['WHERE']
    assert [c.name for c in extended.clauses] == ['WHERE', 'PREWHERE']


def test_get_finds_by_name() -> None:
    """Lookup is by exact uppercased name."""
    model = ClauseModel(clauses=(Clause(name='GROUP BY', suggests=(Kind.COLUMN,)),))
    found = model.get('GROUP BY')
    assert found is not None
    assert found.suggests == (Kind.COLUMN,)
    assert model.get('ORDER BY') is None


def test_names_are_sorted_longest_first() -> None:
    """clause_at matches greedily, so multi-word names must be tried before their prefixes."""
    model = ClauseModel(clauses=(Clause(name='BY'), Clause(name='GROUP BY'), Clause(name='ORDER BY')))
    assert model.names()[0] in {'GROUP BY', 'ORDER BY'}
    assert model.names()[-1] == 'BY'


def test_replace_composes_a_variant() -> None:
    """The documented way to build a dialect: replace fields, never subclass."""
    variant = replace(
        ANSI,
        name='clickhouse',
        syntax=replace(ANSI.syntax, identifier_quotes=('"', '`'), unquoted_case='preserve'),
        namespace=Namespace(levels=('database', 'table')),
    )
    assert variant.name == 'clickhouse'
    assert variant.syntax.identifier_quotes == ('"', '`')
    assert ANSI.syntax.identifier_quotes == ('"',)
    assert ANSI.namespace.levels == ('schema', 'table')


def test_ansi_defaults() -> None:
    """
    The fallback dialect must be conservative: no dollar quoting, no :: cast.

    Conservative means every extension left off, not every field left at its
    default — the parameter spellings are declared here because a dialect
    without them offers column names inside `:param`, which is worse than
    offering nothing. Asserted field by field rather than against a bare
    `Syntax()`, so a real divergence is named instead of appearing as a diff.
    """
    assert ANSI.name == 'ansi'
    assert ANSI.syntax == replace(Syntax(), placeholders=ANSI.syntax.placeholders)
    assert ANSI.syntax.dollar_quoting is False
    assert ANSI.syntax.cast_operator is None
    assert ANSI.syntax.placeholders
    assert 'select' in ANSI.reserved


def test_only_postgres_ships_a_foreign_key_query() -> None:
    """ClickHouse and Trino keep no constraints, so the slot stays empty and the capability is inert."""
    assert POSTGRES.catalog_queries.foreign_keys is not None
    assert CLICKHOUSE.catalog_queries.foreign_keys is None
    assert TRINO.catalog_queries.foreign_keys is None
    assert ANSI.catalog_queries.foreign_keys is None


def test_the_foreign_key_row_mapper_builds_an_edge() -> None:
    """Arrays in, ForeignKey out. The mapper is the only place a driver's shape is visible."""
    query = POSTGRES.catalog_queries.foreign_keys
    assert query is not None
    edge = query.row(('public', 'reports_report', ['author_id'], 'public', 'auth_user', ['id']))
    assert edge == ForeignKey(
        schema='public',
        table='reports_report',
        columns=('author_id',),
        ref_schema='public',
        ref_table='auth_user',
        ref_columns=('id',),
    )


def test_only_the_affordable_backends_search_relations() -> None:
    """
    Trino declines on a measurement, not a principle.

    One `information_schema` query per catalog costs ~179ms against the docker
    fixture, and a real answer needs one per catalog. Postgres is 0.4-2.3ms and
    ClickHouse 1.8-4.2ms over the same data.
    """
    assert POSTGRES.catalog_queries.relation_search is not None
    assert CLICKHOUSE.catalog_queries.relation_search is not None
    assert TRINO.catalog_queries.relation_search is None
    assert ANSI.catalog_queries.relation_search is None


def _column(query: Query | None, row: tuple[object, ...]) -> Column:
    """One mapped column. `Query.row` is typed `object`, so narrowing is the caller's job."""
    assert query is not None
    found = query.row(row)
    assert isinstance(found, Column)
    return found


def _relation(query: Query | None, row: tuple[object, ...]) -> Table:
    """One mapped relation, narrowed the same way."""
    assert query is not None
    found = query.row(row)
    assert isinstance(found, Table)
    return found


def test_the_column_mapper_reads_the_privilege_flag() -> None:
    """True, False and None are three different answers and none of them is a guess."""
    query = POSTGRES.catalog_queries.columns
    assert _column(query, ('public', 'users', 'id', 'bigint', 1, True)).availability is Availability.AVAILABLE
    assert _column(query, ('public', 'users', 'pw', 'text', 2, False)).availability is Availability.RESTRICTED
    assert _column(query, ('public', 'users', 'pw', 'text', 2, None)).availability is Availability.UNKNOWN


def test_a_relation_whose_columns_are_not_the_question_reports_unknown() -> None:
    """An index has no grantable columns and SELECT on a sequence means something else."""
    query = POSTGRES.catalog_queries.tables
    assert _relation(query, ('public', 'users', 'r', 100, False)).availability is Availability.RESTRICTED
    assert _relation(query, ('public', 'users_pkey', 'i', 0, False)).availability is Availability.UNKNOWN
    assert _relation(query, ('public', 'users_id_seq', 'S', 0, False)).availability is Availability.UNKNOWN


def test_the_search_queries_report_it_too() -> None:
    """Or a column found across schemas would know less than the same column fetched by relation."""
    columns = POSTGRES.catalog_queries.column_search
    relations = POSTGRES.catalog_queries.relation_search
    assert _column(columns, ('public', 'users', 'pw', 'text', 2, False)).availability is Availability.RESTRICTED
    assert _relation(relations, ('public', 'users', 'r', 100, False)).availability is Availability.RESTRICTED


def test_the_other_dialects_say_nothing_rather_than_guessing() -> None:
    """ClickHouse has no has_column_privilege equivalent and Trino exposes nothing through SQL."""
    for dialect in (CLICKHOUSE, TRINO):
        query = dialect.catalog_queries.columns
        assert query is not None
        assert 'privilege' not in query.sql
        assert _column(query, ('db', 'users', 'id', 'bigint', 1)).availability is Availability.UNKNOWN
