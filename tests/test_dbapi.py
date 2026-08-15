"""Paramstyle rewriting. No driver and no database: this is string handling."""

from __future__ import annotations

import pytest

from pysqlsuggestions.catalogs.dbapi import DbapiCatalog, render
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO

SQL = 'SELECT a FROM t WHERE s = $1 AND n = $2'


def test_qmark() -> None:
    """The `trino` client speaks qmark."""
    assert render(SQL, ('public', 'users'), 'qmark') == (
        'SELECT a FROM t WHERE s = ? AND n = ?',
        ('public', 'users'),
    )


def test_format() -> None:
    """psycopg2 speaks format."""
    assert render(SQL, ('public', 'users'), 'format') == (
        'SELECT a FROM t WHERE s = %s AND n = %s',
        ('public', 'users'),
    )


def test_pyformat() -> None:
    """clickhouse-driver speaks pyformat, which wants a mapping."""
    assert render(SQL, ('public', 'users'), 'pyformat') == (
        'SELECT a FROM t WHERE s = %(p1)s AND n = %(p2)s',
        {'p1': 'public', 'p2': 'users'},
    )


def test_numeric_and_named() -> None:
    """The two remaining PEP 249 styles."""
    assert render(SQL, ('a', 'b'), 'numeric')[0] == 'SELECT a FROM t WHERE s = :1 AND n = :2'
    assert render(SQL, ('a', 'b'), 'named') == ('SELECT a FROM t WHERE s = :p1 AND n = :p2', {'p1': 'a', 'p2': 'b'})


def test_repeated_marker_is_bound_once_per_occurrence() -> None:
    """`$1 = $1` appears twice in the SQL but names one value; positional styles need both."""
    sql, params = render('SELECT 1 WHERE $1 = $1 AND x = $2', ('a', 'b'), 'format')
    assert sql == 'SELECT 1 WHERE %s = %s AND x = %s'
    assert params == ('a', 'a', 'b')


def test_repeated_marker_in_a_named_style_is_bound_once() -> None:
    """Named styles reuse the key rather than repeating the value."""
    sql, params = render('SELECT 1 WHERE $1 = $1', ('a',), 'pyformat')
    assert sql == 'SELECT 1 WHERE %(p1)s = %(p1)s'
    assert params == {'p1': 'a'}


def test_literal_percent_is_escaped_for_percent_based_styles() -> None:
    r"""
    Introspection SQL contains literal `%` — `NOT LIKE 'pg\_%'` — which psycopg2
    would otherwise read as a placeholder and fail on with an opaque IndexError.
    """
    sql, params = render(r"SELECT 1 WHERE n NOT LIKE 'pg\_%' AND s = $1", ('public',), 'format')
    assert sql == r"SELECT 1 WHERE n NOT LIKE 'pg\_%%' AND s = %s"
    assert params == ('public',)


def test_literal_percent_is_left_alone_for_other_styles() -> None:
    """qmark and numeric have no quarrel with a percent sign."""
    sql, _ = render(r"SELECT 1 WHERE n NOT LIKE 'pg\_%' AND s = $1", ('public',), 'qmark')
    assert sql == r"SELECT 1 WHERE n NOT LIKE 'pg\_%' AND s = ?"


def test_unknown_paramstyle_is_rejected_loudly() -> None:
    """Silently emitting the wrong placeholder would produce valid-but-wrong SQL."""
    with pytest.raises(ValueError, match='unsupported paramstyle'):
        render(SQL, ('a', 'b'), 'sqlite')


class FakeCursor:
    """Records what it was asked to execute and replays canned rows."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, object]] = []

    def execute(self, operation: str, parameters: object = ()) -> None:
        """Record the statement."""
        self.executed.append((operation, parameters))

    def fetchall(self) -> list[tuple[object, ...]]:
        """The canned rows."""
        return self.rows


def test_catalog_maps_rows_through_the_dialect() -> None:
    """The adapter holds no schema knowledge; the dialect's row mapper does."""
    cursor = FakeCursor([('public', 'reports_report', 'id', 'bigint', 1, True)])
    catalog = DbapiCatalog(lambda: cursor, POSTGRES, paramstyle='format')
    columns = catalog.columns('public', 'reports_report')
    assert [(c.schema, c.table, c.name, c.type, c.position) for c in columns] == [
        ('public', 'reports_report', 'id', 'bigint', 1),
    ]


def test_catalog_binds_empty_string_for_a_missing_schema() -> None:
    """
    `schema=None` must reach the query as '', which the SQL reads as the default namespace.

    The Postgres columns query names $2 before $1 and repeats $1, and positional
    binding follows occurrence order in the SQL rather than argument order — so
    the tuple is ($2, $1, $1). Getting that backwards produces valid SQL that
    silently searches for the wrong relation.
    """
    cursor = FakeCursor([])
    catalog = DbapiCatalog(lambda: cursor, POSTGRES, paramstyle='format')
    catalog.columns(None, 'reports_report')
    sql, parameters = cursor.executed[0]
    assert sql.index('c.relname = %s') < sql.index("%s = ''")
    assert parameters == ('reports_report', '', '')


def test_catalog_defers_connecting_until_a_query_runs() -> None:
    """A warm cache means an editor session touches the database not at all."""
    opened = []

    def open_cursor() -> FakeCursor:
        opened.append(1)
        return FakeCursor([])

    DbapiCatalog(open_cursor, POSTGRES, paramstyle='format')
    assert opened == []


def test_search_relations_issues_no_query_without_a_prefix() -> None:
    """`FROM <caret>` must not enumerate the database, so nothing is asked at all."""
    cursor = FakeCursor([])
    catalog = DbapiCatalog(lambda: cursor, POSTGRES, paramstyle='format')
    assert catalog.search_relations('', 10) == []
    assert cursor.executed == []


def test_search_relations_is_inert_when_the_dialect_ships_no_query() -> None:
    """Trino's slot is None, and the capability goes quiet rather than failing."""
    cursor = FakeCursor([])
    catalog = DbapiCatalog(lambda: cursor, TRINO, paramstyle='qmark')
    assert catalog.search_relations('ord', 10) == []
    assert cursor.executed == []


def test_search_relations_maps_rows_through_the_dialect() -> None:
    """The schema travels with the row, because that is what makes the insertion qualifiable."""
    cursor = FakeCursor([('billing', 'invoices', 'r', 42, True)])
    catalog = DbapiCatalog(lambda: cursor, POSTGRES, paramstyle='format')
    [found] = catalog.search_relations('invo', 10)
    assert (found.schema, found.name, found.kind) == ('billing', 'invoices', 'table')


def test_a_zero_marker_is_rejected_rather_than_binding_the_last_value() -> None:
    """
    `$0` is the one index that failed silently.

    Markers are one-based, so `positional` subtracts one — which turns `$0` into
    Python's `-1` and binds the *last* value to it, producing valid SQL bound to
    the wrong parameter. Every other out-of-range marker raises. `Template.snippet`
    in this same package spells `$0` with a different meaning, so writing one into
    a `Query.sql` is a mistake a dialect author can plausibly make.
    """
    with pytest.raises(ValueError, match=r'\$0'):
        render('SELECT $0, $1', ('first', 'second'), 'format')


def test_a_query_with_no_markers_binds_no_parameters_in_any_style() -> None:
    """
    Trino's `SHOW FUNCTIONS` takes none, and three styles bound one anyway.

    `qmark` and `format` build their parameter list from the markers that occur;
    `numeric` returned every value it was handed and `named`/`pyformat` enumerated
    them, so all three described a parameter the SQL never asks for. A positional
    driver rejects that outright — sqlite3 answers `Incorrect number of bindings
    supplied` — and `trino_http._prepare` already special-cases the same shape,
    which is the sign the general rule was wrong rather than the query unusual.
    """
    functions = TRINO.catalog_queries.functions
    assert functions is not None
    for style in ('qmark', 'format', 'numeric', 'named', 'pyformat'):
        _, parameters = render(functions.sql, ('public',), style)
        assert not parameters, style


def test_a_marker_inside_a_literal_or_a_comment_is_left_alone() -> None:
    """
    The rewrite was a context-free regex over SQL, which is a lexer's job.

    A `$1` inside a string, a comment or a dollar-quoted body is text, not a
    parameter — but it was replaced all the same, and bound a value the query
    never asked for. Latent today, since no shipped introspection query holds
    one. Postgres is where it would land first: it is the dialect with dollar
    quoting, and the one whose catalog SQL is most likely to grow a `$$` body.
    """
    cases = [
        ("SELECT '$1 is text' WHERE s = $1", "SELECT '$1 is text' WHERE s = %s"),
        ('SELECT 1 -- costs $1\nWHERE s = $1', 'SELECT 1 -- costs $1\nWHERE s = %s'),
        ('SELECT $$ body $1 $$ WHERE s = $1', 'SELECT $$ body $1 $$ WHERE s = %s'),
        ('SELECT /* $1 */ x WHERE s = $1', 'SELECT /* $1 */ x WHERE s = %s'),
        ('SELECT "$1" WHERE s = $1', 'SELECT "$1" WHERE s = %s'),
    ]
    for sql, expected in cases:
        rendered, parameters = render(sql, ('a',), 'format', POSTGRES.syntax)
        assert rendered == expected, sql
        assert parameters == ('a',), sql


def test_a_marker_inside_a_literal_is_left_alone_for_every_style() -> None:
    """The named styles build their parameters from the markers too, so they agree."""
    sql = "SELECT '$2 is text' WHERE s = $1"
    for style in ('qmark', 'format', 'numeric', 'named', 'pyformat'):
        _, parameters = render(sql, ('a', 'b'), style, POSTGRES.syntax)
        assert len(parameters) == 1, style


def test_dollar_quoting_is_read_per_dialect() -> None:
    """
    Only Postgres has it, and a dialect without it must not gain it here.

    Trino reads `$$` as two operators, so a `$1` between them is an ordinary
    marker there and rewriting it is right.
    """
    sql = 'SELECT $$ body $2 $$ WHERE s = $1'
    assert render(sql, ('a', 'b'), 'format', POSTGRES.syntax)[1] == ('a',), 'the body is a literal'
    assert render(sql, ('a', 'b'), 'format', TRINO.syntax)[1] == ('b', 'a'), 'two operators, so both are markers'


def test_the_shipped_queries_are_unaffected() -> None:
    """No introspection query holds a marker in a literal, and this pins that."""
    for dialect in (POSTGRES, TRINO, CLICKHOUSE):
        for name in ('schemas', 'tables', 'columns', 'functions', 'foreign_keys', 'values'):
            query = getattr(dialect.catalog_queries, name, None)
            if query is None:
                continue
            plain = render(query.sql, ('a', 'b', 'c'), 'format')
            aware = render(query.sql, ('a', 'b', 'c'), 'format', dialect.syntax)
            assert plain == aware, (dialect.name, name)
