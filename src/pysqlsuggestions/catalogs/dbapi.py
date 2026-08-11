"""
A catalog over any PEP 249 cursor. Imports no driver.

This is what actually breaks the driver tie: each dialect exports query text plus
row mappers, and anything that can hand back a DB-API cursor — psycopg2, the
`trino` client, a connection pool, an HTTP proxy — can serve it.

The one thing the dialects cannot do for themselves is placeholders. psycopg2
speaks `%s`, `trino` speaks `?`, clickhouse-driver speaks `%(name)s`. Query text
therefore uses neutral `$1`, `$2` markers and this module rewrites them for
whatever paramstyle the driver reports. Without that step, "one adapter covers
psycopg2 and trino" would not be true.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from pysqlsuggestions.dialects.base import Dialect, Query
from pysqlsuggestions.types import Column, ColumnValue, Function, Table

_MARKER = re.compile(r'\$(\d+)')
_DEFAULT_PARAMSTYLE = 'format'


class Cursor(Protocol):
    """The slice of PEP 249 this module uses."""

    def execute(self, operation: str, parameters: Any = ...) -> Any:
        """Run a statement."""
        ...

    def fetchall(self) -> Sequence[Any]:
        """Every remaining row."""
        ...


def render(sql: str, values: Sequence[str], paramstyle: str) -> tuple[str, Any]:
    """
    Rewrite `$1`-style markers for `paramstyle`, returning (sql, parameters).

    A marker may repeat, and positional styles care about the order of
    occurrence rather than the order of `values`, so both are handled here
    rather than left to each dialect to remember.
    """
    order: list[int] = []

    def positional(match: re.Match[str], token: str) -> str:
        order.append(int(match.group(1)) - 1)
        return token

    # The %-based styles read `%` as the start of a placeholder, so a literal one
    # in the query text has to be doubled. Introspection SQL is full of them —
    # `nspname NOT LIKE 'pg\_%'` is the obvious case — and forgetting this raises
    # an opaque IndexError from inside the driver rather than anything readable.
    escaped = sql.replace('%', '%%') if paramstyle in ('format', 'pyformat') else sql

    if paramstyle in ('qmark', 'format'):
        token = '?' if paramstyle == 'qmark' else '%s'
        rendered = _MARKER.sub(lambda m: positional(m, token), escaped)
        return rendered, tuple(values[index] for index in order)

    if paramstyle == 'numeric':
        return _MARKER.sub(lambda m: f':{m.group(1)}', sql), tuple(values)

    if paramstyle in ('named', 'pyformat'):
        template = ':p{}' if paramstyle == 'named' else '%(p{})s'
        rendered = _MARKER.sub(lambda m: template.format(m.group(1)), escaped)
        return rendered, {f'p{index + 1}': value for index, value in enumerate(values)}

    message = f'unsupported paramstyle: {paramstyle!r}'
    raise ValueError(message)


class DbapiCatalog:
    """
    A catalog backed by a PEP 249 cursor.

    `open_cursor` is called per query and may defer connecting, so a warm cache
    means an editor session touches the database not at all.
    """

    def __init__(
        self,
        open_cursor: Callable[[], Cursor],
        dialect: Dialect,
        *,
        paramstyle: str = _DEFAULT_PARAMSTYLE,
    ) -> None:
        self._open_cursor = open_cursor
        self._dialect = dialect
        self._paramstyle = paramstyle

    def _rows(self, query: Query | None, *values: str) -> list[Any]:
        if query is None:
            return []
        sql, parameters = render(query.sql, values, self._paramstyle)
        cursor = self._open_cursor()
        cursor.execute(sql, parameters)
        return [query.row(tuple(row)) for row in cursor.fetchall()]

    def schemas(self, catalog: str | None = None) -> Sequence[str]:
        """Namespace names one level below `catalog`."""
        return [str(name) for name in self._rows(self._dialect.catalog_queries.schemas, catalog or '')]

    def tables(self, schema: str | None = None) -> Sequence[Table]:
        """Relations in `schema`, or those visible by default."""
        return [row for row in self._rows(self._dialect.catalog_queries.tables, schema or '') if isinstance(row, Table)]

    def columns(self, schema: str | None, table: str) -> Sequence[Column]:
        """Columns of one relation, in declaration order."""
        rows = self._rows(self._dialect.catalog_queries.columns, schema or '', table)
        return [row for row in rows if isinstance(row, Column)]

    def functions(self, schema: str | None = None) -> Sequence[Function]:
        """Functions, aggregates and window functions."""
        rows = self._rows(self._dialect.catalog_queries.functions, schema or '')
        return [row for row in rows if isinstance(row, Function)]

    def all_columns(self) -> Sequence[Column] | None:
        """
        Always None: a live database is never worth enumerating on a keystroke.

        The port offers this for catalogs small enough to hand over whole — a
        snapshot, a fixture. A database of consequence has tens of thousands of
        columns across hundreds of relations, and asking for all of them to
        answer one keypress is the kind of query a completion engine must not
        make. `search_columns` narrows first instead.
        """
        del self
        return None

    def search_columns(self, prefix: str, limit: int) -> Sequence[Column]:
        """
        Columns matching `prefix` anywhere in the name, closest first.

        Empty for an empty prefix. Every column in the database is not an
        answer to `SELECT <caret>`, and narrowing is the entire reason this
        query exists rather than `all_columns`.
        """
        if not prefix:
            return []
        rows = self._rows(self._dialect.catalog_queries.column_search, prefix)
        return [row for row in rows if isinstance(row, Column)][:limit]

    def common_values(self, schema: str | None, table: str, column: str, limit: int) -> Sequence[ColumnValue]:
        """Frequent values of one column, from the dialect's statistics query."""
        rows = self._rows(self._dialect.catalog_queries.values, schema or '', table, column)
        return [row for row in rows if isinstance(row, ColumnValue)][:limit]
