"""
The record types a dialect is made of.

A dialect is data you compose with dataclasses.replace, not a class you
subclass — ClickHouse and Trino each share different subsets with ANSI, a shape
no MRO expresses. Instances live in the sibling modules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pysqlsuggestions.types import Kind


@dataclass(frozen=True, slots=True)
class Syntax:
    """Everything the lexer needs. No other stage reads this record."""

    identifier_quotes: tuple[str, ...] = ('"',)
    line_comments: tuple[str, ...] = ('--',)
    nested_block_comments: bool = False
    """Postgres nests /* /* */ */. ClickHouse and Trino stop at the first close."""
    string_escape_backslash: bool = False
    r"""ClickHouse honours \' inside literals; Postgres with standard_conforming_strings does not."""
    unquoted_case: Literal['lower', 'upper', 'preserve'] = 'lower'
    dollar_quoting: bool = False
    cast_operator: str | None = None


@dataclass(frozen=True, slots=True)
class Namespace:
    """How many levels a dotted path has, and what each level means."""

    levels: tuple[str, ...] = ('schema', 'table')

    def level_of(self, segments: int) -> str | None:
        """What a qualifier of `segments` parts names, or None if it is too deep."""
        return self.levels[segments - 1] if 0 < segments <= len(self.levels) else None


@dataclass(frozen=True, slots=True)
class Clause:
    """One clause keyword and what it implies."""

    name: str
    """Uppercased. May contain single spaces: 'GROUP BY', 'ARRAY JOIN'."""
    follows: frozenset[str] = frozenset()
    """Clauses this one may appear after. Empty means unconstrained."""
    suggests: tuple[Kind, ...] = ()
    """Most relevant first."""


@dataclass(frozen=True, slots=True)
class ClauseModel:
    """The clause vocabulary of a dialect."""

    clauses: tuple[Clause, ...] = ()

    def extend(self, *clauses: Clause) -> ClauseModel:
        """A new model with `clauses` appended. The receiver is untouched."""
        return ClauseModel(clauses=self.clauses + clauses)

    def get(self, name: str) -> Clause | None:
        """The clause called `name`, or None. Linear scan over a few dozen entries."""
        for clause in self.clauses:
            if clause.name == name:
                return clause
        return None

    def names(self) -> tuple[str, ...]:
        """Clause names ordered longest first, so greedy matching tries 'GROUP BY' before 'BY'."""
        return tuple(sorted((c.name for c in self.clauses), key=lambda n: (-len(n.split()), -len(n), n)))


@dataclass(frozen=True, slots=True)
class Query:
    """
    Introspection SQL as data.

    Placeholders are neutral $1, $2 markers; the DB-API catalog rewrites them
    for whatever paramstyle the driver reports.
    """

    sql: str
    row: Callable[[tuple[object, ...]], object]


@dataclass(frozen=True, slots=True)
class CatalogQueries:
    """The introspection queries a dialect provides. Populated in a later plan."""

    schemas: Query | None = None
    tables: Query | None = None
    columns: Query | None = None
    functions: Query | None = None


@dataclass(frozen=True, slots=True)
class Dialect:
    """A backend, as data."""

    name: str
    syntax: Syntax = field(default_factory=Syntax)
    namespace: Namespace = field(default_factory=Namespace)
    clauses: ClauseModel = field(default_factory=ClauseModel)
    keywords: frozenset[str] = frozenset()
    """Offered as completions. Ideally introspected; the static set is the offline fallback."""
    reserved: frozenset[str] = frozenset()
    """Lowercased. Drives quoting decisions, which must be made before any connection exists."""
    catalog_queries: CatalogQueries = field(default_factory=CatalogQueries)
