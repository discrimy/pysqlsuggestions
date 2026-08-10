"""Paramstyle rewriting. No driver and no database: this is string handling."""

from __future__ import annotations

import pytest

from pysqlsuggestions.catalogs.dbapi import DbapiCatalog, render
from pysqlsuggestions.dialects.postgres import POSTGRES

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
    cursor = FakeCursor([('public', 'reports_report', 'id', 'bigint', 1)])
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
