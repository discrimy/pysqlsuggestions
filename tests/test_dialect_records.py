"""Dialects are composed data. These tests pin the composition mechanics."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Clause, ClauseModel, Namespace, Syntax
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import ForeignKey, Kind


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
