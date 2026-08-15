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

from pysqlsuggestions.dialects.base import Dialect, Query, Syntax
from pysqlsuggestions.engine.lex import TokenType, lex
from pysqlsuggestions.types import Column, ColumnValue, ForeignKey, Function, Table

_MARKER = re.compile(r'\$(\d+)')

_PARAMSTYLES = {
    'qmark': '?',
    'format': '%s',
    'numeric': ':{}',
    'named': ':p{}',
    'pyformat': '%(p{})s',
}
"""How each PEP 249 style spells one parameter. `{}` takes the marker's number."""


def _escaped(text: str, *, doubles: bool) -> str:
    r"""
    `text` with `%` doubled for the styles that read it as a placeholder.

    Introspection SQL is full of literal percents — `nspname NOT LIKE 'pg\_%'` is
    the obvious one — and leaving them raises an opaque IndexError from inside
    the driver rather than anything a reader could act on.
    """
    return text.replace('%', '%%') if doubles else text


_DEFAULT_PARAMSTYLE = 'format'


class Cursor(Protocol):
    """The slice of PEP 249 this module uses."""

    def execute(self, operation: str, parameters: Any = ...) -> Any:
        """Run a statement."""
        ...

    def fetchall(self) -> Sequence[Any]:
        """Every remaining row."""
        ...


def _quoted_spans(sql: str, syntax: Syntax | None) -> tuple[tuple[int, int], ...]:
    """Where `sql` holds a literal or a comment, so a marker inside one is text."""
    if syntax is None:
        return ()
    return tuple(
        (token.start, token.end)
        for token in lex(sql, syntax)
        if token.type in (TokenType.STRING, TokenType.COMMENT) or (token.type is TokenType.IDENT and token.quoted)
    )


def _within(offset: int, spans: tuple[tuple[int, int], ...]) -> bool:
    """Whether `offset` falls inside one of `spans`."""
    return any(start <= offset < end for start, end in spans)


def _markers(sql: str, protected: tuple[tuple[int, int], ...]) -> list[re.Match[str]]:
    """Every `$N` that is a marker rather than text."""
    return [found for found in _MARKER.finditer(sql) if not _within(found.start(), protected)]


def render(sql: str, values: Sequence[str], paramstyle: str, syntax: Syntax | None = None) -> tuple[str, Any]:
    """
    Rewrite `$1`-style markers for `paramstyle`, returning (sql, parameters).

    A marker may repeat, and positional styles care about the order of
    occurrence rather than the order of `values`, so both are handled here
    rather than left to each dialect to remember.

    `syntax` says where the SQL's literals and comments are, so a `$1` written
    inside one is left as the text it is. Without it the rewrite is a
    context-free regex, which is a lexer's job done badly: `SELECT '$1 is text'`
    had its literal rewritten and bound a value the query never asked for.
    Optional because the shipped queries hold no such marker and every caller in
    this package passes it — a third-party dialect that omits it gets the old
    behaviour rather than an error.

    The engine's own scanner answers the question rather than a second one
    written here: it is dialect-driven, so `$$ ... $$` is a literal on Postgres
    and two operators on Trino, and a hand-rolled version would have to be told
    that separately and then kept in step.
    """
    protected = _quoted_spans(sql, syntax)
    if paramstyle not in _PARAMSTYLES:
        message = f'unsupported paramstyle: {paramstyle!r}'
        raise ValueError(message)

    # Checked once, for every paramstyle, because `$0` is malformed in the
    # neutral language rather than in any one driver's. Markers are one-based, so
    # treating it as an index turns it into Python's `-1` and silently binds the
    # *last* value — valid SQL against the wrong parameter, where every other
    # out-of-range marker raises loudly.
    if any(int(found.group(1)) == 0 for found in _markers(sql, protected)):
        message = f'markers are one-based, so $0 is not a parameter: {sql!r}'
        raise ValueError(message)

    # One pass over `sql`, escaping each stretch as it is emitted. The escaping
    # used to happen first, on a copy — and `_quoted_spans` measures `sql`, so
    # every `%` ahead of a marker shifted that marker's offset by one and the
    # protection slipped a place. A `$1` that is text got rewritten, and
    # `pyformat` then bound nothing while its SQL asked for `p1`.
    doubles = paramstyle in ('format', 'pyformat')
    order: list[int] = []
    wanted = sorted({int(found.group(1)) for found in _markers(sql, protected)})
    parts: list[str] = []
    cursor = 0

    for found in _MARKER.finditer(sql):
        parts.append(_escaped(sql[cursor : found.start()], doubles=doubles))
        if _within(found.start(), protected):
            parts.append(found.group(0))
        else:
            number = int(found.group(1))
            order.append(number - 1)
            parts.append(_PARAMSTYLES[paramstyle].format(number))
        cursor = found.end()
    parts.append(_escaped(sql[cursor:], doubles=doubles))
    rendered = ''.join(parts)

    if paramstyle in ('qmark', 'format'):
        return rendered, tuple(values[index] for index in order)
    if wanted and max(wanted) > len(values):
        # The bounds check the four keyed styles get for free from
        # `values[number - 1]`, which `numeric`'s slice does not perform. Without
        # it a marker past the end rendered happily and the driver was handed a
        # query it could not bind, diagnosed by the server as a parameter count
        # naming neither the query nor the marker.
        message = f'no value for ${max(wanted)}: {len(values)} supplied'
        raise IndexError(message)

    if paramstyle == 'numeric':
        # `:N` indexes the sequence, so this one cannot be compacted the way the
        # two keyed styles are: `:3` goes on meaning "the third value" however
        # few markers occur. Enough of the sequence to reach the highest marker,
        # and nothing at all when the query holds none.
        return rendered, tuple(values[: max(wanted)]) if wanted else ()
    return rendered, {f'p{number}': values[number - 1] for number in wanted}


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
        sql, parameters = render(query.sql, values, self._paramstyle, self._dialect.syntax)
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

    def search_relations(self, prefix: str, limit: int) -> Sequence[Table]:
        """
        Relations matching `prefix` anywhere in the name, closest first, in any namespace.

        Empty for an empty prefix, and empty when the dialect ships no query —
        which is how Trino declines the capability without any code here knowing
        that is what it is doing.
        """
        if not prefix:
            return []
        rows = self._rows(self._dialect.catalog_queries.relation_search, prefix)
        return [row for row in rows if isinstance(row, Table)][:limit]

    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """Declared relationships, when the dialect ships the query. Empty when it does not."""
        rows = self._rows(self._dialect.catalog_queries.foreign_keys, schema or '')
        return [row for row in rows if isinstance(row, ForeignKey)]

    def common_values(self, schema: str | None, table: str, column: str, limit: int) -> Sequence[ColumnValue]:
        """Frequent values of one column, from the dialect's statistics query."""
        rows = self._rows(self._dialect.catalog_queries.values, schema or '', table, column)
        return [row for row in rows if isinstance(row, ColumnValue)][:limit]
