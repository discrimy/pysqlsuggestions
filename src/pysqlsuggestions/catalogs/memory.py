"""
A snapshot catalog built from a plain dict. Standard library only.

This is what makes resolution testable without a database, and it is also the
bridge for async callers: pre-fetch into one of these and the synchronous engine
works unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from pysqlsuggestions.engine import rank
from pysqlsuggestions.types import Column, Function, Table

ColumnSpec = tuple[str, str] | tuple[str, str, int]
Snapshot = Mapping[tuple[str, str], Iterable[ColumnSpec]]

_MAX_ENUMERABLE_COLUMNS = 5_000


class MemoryCatalog:
    """
    A catalog over an in-memory snapshot.

    Built from `{(schema, table): [(column, type), ...]}`, which is the shape a
    test fixture wants to be written in:

        MemoryCatalog({('public', 'reports_report'): [('id', 'bigint'), ('name', 'varchar')]})

    Column positions default to declaration order in the list, so ranking gets
    the `attnum` signal for free without the fixture having to spell it out.
    """

    def __init__(
        self,
        snapshot: Snapshot,
        *,
        functions: Iterable[Function] = (),
        keywords: Iterable[tuple[str, str]] = (),
        values: Mapping[tuple[str, str, str], Sequence[str]] | None = None,
        table_kinds: Mapping[tuple[str, str], str] | None = None,
        oversized: bool = False,
    ) -> None:
        self._columns: dict[tuple[str, str], tuple[Column, ...]] = {}
        kinds = table_kinds or {}
        for (schema, table), specs in snapshot.items():
            self._columns[schema, table] = tuple(
                Column(
                    schema=schema,
                    table=table,
                    name=spec[0],
                    type=spec[1],
                    position=spec[2] if len(spec) > 2 else index,  # noqa: PLR2004
                )
                for index, spec in enumerate(specs)
            )
        self._tables = tuple(
            Table(schema=schema, name=table, kind=kinds.get((schema, table), 'table'))
            for schema, table in self._columns
        )
        self._functions = tuple(functions)
        self._keywords = tuple(keywords)
        self._values = dict(values or {})
        self._oversized = oversized
        self.calls: list[tuple[str, ...]] = []
        """Recorded call names, so tests can assert a CTE cost no catalog reads."""

    def schemas(self, catalog: str | None = None) -> Sequence[str]:
        """Distinct schema names, sorted. A snapshot holds one catalog, so `catalog` is ignored."""
        self.calls.append(('schemas', catalog or ''))
        return sorted({schema for schema, _ in self._columns})

    def tables(self, schema: str | None = None) -> Sequence[Table]:
        """Relations in `schema`; with None, every relation in the snapshot."""
        self.calls.append(('tables', schema or ''))
        return [t for t in self._tables if schema is None or t.schema == schema]

    def columns(self, schema: str | None, table: str) -> Sequence[Column]:
        """
        Columns of `table`.

        With `schema=None` the first matching relation wins, which stands in for
        a search path without the fixture needing to model one.
        """
        self.calls.append(('columns', schema or '', table))
        if schema is not None:
            return self._columns.get((schema, table), ())
        for (candidate_schema, candidate_table), columns in self._columns.items():
            if candidate_table == table:
                del candidate_schema
                return columns
        return ()

    def functions(self, schema: str | None = None) -> Sequence[Function]:
        """Functions, optionally restricted to one schema."""
        self.calls.append(('functions', schema or ''))
        return [f for f in self._functions if schema is None or f.schema == schema]

    def all_columns(self) -> Sequence[Column] | None:
        """Every column, or None when the snapshot is marked oversized."""
        self.calls.append(('all_columns',))
        if self._oversized:
            return None
        flat = [column for columns in self._columns.values() for column in columns]
        return None if len(flat) > _MAX_ENUMERABLE_COLUMNS else flat

    def search_columns(self, prefix: str, limit: int) -> Sequence[Column]:
        """
        The `limit` columns matching `prefix` most closely, across every relation.

        Ordered before truncating, which is the whole of the port's contract:
        `limit` rows taken in storage order can leave `created` behind three
        hundred `created_at_variant_NNN`, and nothing downstream can recover a
        row that was never fetched.
        """
        self.calls.append(('search_columns', prefix))
        folded = prefix.lower()
        found = [
            column for columns in self._columns.values() for column in columns if rank.matches(column.name, folded)
        ]
        found.sort(key=lambda column: (not column.name.lower().startswith(folded), len(column.name), column.name))
        return found[:limit]

    def common_values(self, schema: str | None, table: str, column: str, limit: int) -> Sequence[str]:
        """Frequent values, when the fixture supplied any. Keyed (schema, table, column)."""
        self.calls.append(('common_values', schema or '', table, column))
        if schema is not None:
            return self._values.get((schema, table, column), ())[:limit]
        for (candidate_schema, candidate_table, candidate_column), found in self._values.items():
            if candidate_table == table and candidate_column == column:
                del candidate_schema
                return found[:limit]
        return ()

    def keywords(self) -> Sequence[tuple[str, str]]:
        """Server keywords, when the fixture supplied any."""
        self.calls.append(('keywords',))
        return self._keywords
