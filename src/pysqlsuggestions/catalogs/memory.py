"""
A snapshot catalog built from a plain dict. Standard library only.

This is what makes resolution testable without a database, and it is also the
bridge for async callers: pre-fetch into one of these and the synchronous engine
works unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from pysqlsuggestions.engine import rank
from pysqlsuggestions.types import Column, ColumnValue, ForeignKey, Function, Table

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
        values: Mapping[tuple[str, str, str], Sequence[str | ColumnValue]] | None = None,
        table_kinds: Mapping[tuple[str, str], str] | None = None,
        table_rows: Mapping[tuple[str, str], int] | None = None,
        catalogs: Mapping[str, Sequence[str]] | None = None,
        foreign_keys: Iterable[ForeignKey] = (),
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
        sizes = table_rows or {}
        self._tables = tuple(
            Table(
                schema=schema,
                name=table,
                kind=kinds.get((schema, table), 'table'),
                rows=sizes.get((schema, table)),
            )
            for schema, table in self._columns
        )
        self._functions = tuple(functions)
        self._keywords = tuple(keywords)
        self._catalogs = {name: tuple(schemas) for name, schemas in (catalogs or {}).items()}
        self._values = {
            key: tuple(v if isinstance(v, ColumnValue) else ColumnValue(text=v) for v in found)
            for key, found in (values or {}).items()
        }
        self._edges = tuple(foreign_keys)
        self._oversized = oversized
        self.calls: list[tuple[str, ...]] = []
        """Recorded call names, so tests can assert a CTE cost no catalog reads."""

    def schemas(self, catalog: str | None = None) -> Sequence[str]:
        """
        Namespace names one level down, sorted.

        Without `catalogs` the snapshot is two-level and holds one catalog, so
        the argument is ignored and every schema comes back. With them it is
        three-level, as Trino is: one level down from nothing is a *catalog*,
        and naming one gives the schemas below it. Returning schemas for an
        empty argument would offer the second level at the first position.
        """
        self.calls.append(('schemas', catalog or ''))
        if not self._catalogs:
            return sorted({schema for schema, _ in self._columns})
        if not catalog:
            return sorted(self._catalogs)
        return sorted(self._catalogs.get(catalog, ()))

    def tables(self, schema: str | None = None) -> Sequence[Table]:
        """
        Relations in `schema`.

        With None and two levels, every relation in the snapshot. With three
        there is no useful default: a bare position wants catalogs, and
        enumerating every relation of every catalog is what the live Trino
        adapter declines to do for the same reason.
        """
        self.calls.append(('tables', schema or ''))
        if schema is None:
            return [] if self._catalogs else list(self._tables)
        return [t for t in self._tables if t.schema == schema]

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

    def common_values(self, schema: str | None, table: str, column: str, limit: int) -> Sequence[ColumnValue]:
        """Frequent values, when the fixture supplied any. Keyed (schema, table, column)."""
        self.calls.append(('common_values', schema or '', table, column))
        if schema is not None:
            return self._values.get((schema, table, column), ())[:limit]
        for (candidate_schema, candidate_table, candidate_column), found in self._values.items():
            if candidate_table == table and candidate_column == column:
                del candidate_schema
                return found[:limit]
        return ()

    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """
        Declared relationships, when the fixture supplied any.

        Filtered by the *referencing* side's schema, matching what the Postgres
        query does — an edge is owned by the table that carries the constraint.
        """
        self.calls.append(('foreign_keys', schema or ''))
        if schema is None:
            return list(self._edges)
        return [edge for edge in self._edges if edge.schema == schema]

    def keywords(self) -> Sequence[tuple[str, str]]:
        """Server keywords, when the fixture supplied any."""
        self.calls.append(('keywords',))
        return self._keywords
