"""Public value types. Everything here is a frozen dataclass or an enum."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Kind(Enum):
    """
    What a suggestion is.

    Values are explicit strings rather than auto() because consumers serialise
    them straight into JSON payloads for an editor.
    """

    COLUMN = 'column'
    TABLE = 'table'
    CTE = 'cte'
    """A relation the statement defined itself. Distinct from TABLE so a UI can say so."""
    SCHEMA = 'schema'
    FUNCTION = 'function'
    ALIAS = 'alias'
    KEYWORD = 'keyword'
    OPERATOR = 'operator'
    """`=`, `<>`, `>=`. Separate from KEYWORD because it has no case to follow."""


@dataclass(frozen=True, slots=True)
class Column:
    """A column as the catalog reports it."""

    schema: str
    table: str
    name: str
    type: str
    position: int = 0
    """attnum / ordinal_position. Declaration order outranks alphabetical when ranking."""


@dataclass(frozen=True, slots=True)
class Table:
    """A relation as the catalog reports it."""

    schema: str
    name: str
    kind: str = 'table'
    """Normalised by the dialect row mappers: table, view, materialized view, foreign table..."""


@dataclass(frozen=True, slots=True)
class Function:
    """A function, aggregate or window function as the catalog reports it."""

    schema: str | None
    name: str
    args: str
    result: str


@dataclass(frozen=True, slots=True)
class Projection:
    """
    The output columns of a relation the statement defines itself.

    `stars` holds relations that a bare `*` or `t.*` referred to; they cannot be
    expanded without the catalog, so resolve finishes the job. A projection with
    empty `stars` needs no catalog call at all.
    """

    columns: tuple[str, ...] = ()
    stars: tuple[Relation, ...] = ()


@dataclass(frozen=True, slots=True)
class Relation:
    """
    A relation referenced by the statement.

    `projection is None` means the relation lives in the catalog. Otherwise the
    statement described it: a CTE, a derived table, or a VALUES list.
    """

    alias: str | None
    path: tuple[str, ...]
    source: Literal['table', 'cte', 'subquery']
    projection: Projection | None = None

    @property
    def label(self) -> str:
        """The name this relation answers to: its alias, else the last path segment."""
        return self.alias or (self.path[-1] if self.path else '')


@dataclass(frozen=True, slots=True)
class Scope:
    """The relations visible at one point in a statement."""

    relations: tuple[Relation, ...] = ()
    ctes: Mapping[str, Relation] = field(default_factory=dict)
    parent: Scope | None = None
    projection: Projection | None = None
    """This query level's own select list. GROUP BY and ORDER BY are answered from it alone."""

    def visible(self) -> tuple[Relation, ...]:
        """This scope's relations plus every enclosing scope's, innermost first."""
        return self.relations + (self.parent.visible() if self.parent else ())


@dataclass(frozen=True, slots=True)
class Request:
    """
    What the engine decided should be suggested, before anything is fetched.

    This is the seam: everything upstream is pure text analysis, everything
    downstream is catalog access and ranking.
    """

    kinds: tuple[Kind, ...]
    """Most relevant first; rank consumes this order."""
    prefix: str
    """Already typed, unquoted and case-folded."""
    replace_span: tuple[int, int]
    """(start of prefix, caret). What the editor overwrites."""
    qualifier: tuple[str, ...] = ()
    """Segments left of the last dot, unquoted and case-folded."""
    clause: str | None = None
    """Nearest clause keyword, uppercased."""
    scope: Scope | None = None
    keyword_case: Literal['lower', 'upper'] | None = None
    """
    How the author has been writing keywords, from the last one they typed.

    `SELECT * FROM t ` should offer `where`, not `WHERE`, when everything before
    it is lowercase — and the prefix is empty there, so the casing has to come
    from somewhere else.
    """


@dataclass(frozen=True, slots=True)
class Candidate:
    """A pre-ranking suggestion. No score, no span."""

    text: str
    kind: Kind
    detail: str | None = None
    position: int = 0
    origin: str = 'catalog'
    """catalog | local | keyword. Ranking treats locally derived candidates differently."""
    literal: bool = False
    """Insert verbatim, never quoted. An ORDER BY ordinal is not an identifier."""
    qualifier: str | None = None
    """
    Relation label to prefix on insertion, when a bare name would be ambiguous.

    Matching still runs against `text`, so typing `na` finds `r.name`: the
    qualifier is about what gets inserted, not what has to be typed to find it.
    """


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A ranked suggestion, ready for an editor."""

    text: str
    kind: Kind
    replace_span: tuple[int, int]
    score: float
    detail: str | None = None
