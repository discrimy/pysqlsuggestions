"""
The record types a dialect is made of.

A dialect is data you compose with dataclasses.replace, not a class you
subclass — ClickHouse and Trino each share different subsets with ANSI, a shape
no MRO expresses. Instances live in the sibling modules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

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
    unquoted_extra: str = ''
    """
    Characters legal inside an unquoted identifier beyond letters, digits and `_`.

    Postgres allows `$` after the first character; Trino allows nothing extra
    and rejects `a$b` outright.
    """
    unquoted_non_ascii: bool = False
    """
    Whether a non-ASCII letter may go unquoted.

    Only Postgres reads `отчёты` back as written. ClickHouse answers
    `Unrecognized token` and Trino `mismatched input`, so a suggestion inserted
    bare there produces a query that does not run — which a Russian-language
    schema discovers on its first column. Off by default: quoting a name that
    did not need it still runs.
    """


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
    followed_by: tuple[str, ...] = ()
    """
    What usually comes next, once this clause has an item.

    Offering the whole reserved-word list after `FROM auth_user ` is useless —
    there are hundreds and only a handful can legally follow. This is the same
    per-clause table the helper this supersedes carried, kept as dialect data so
    ClickHouse can add PREWHERE after FROM without touching the engine.
    """
    operators: tuple[str, ...] = ()
    """
    Operators that may follow a completed operand here.

    Kept apart from `followed_by` rather than inferred from the spelling: these
    are emitted as Kind.OPERATOR, which is never case-folded or quoted, and a
    dialect may add its own — ClickHouse's `::`, Postgres's `~`.

    A clause having any of these is what marks it a predicate clause, and so
    what gives `after_operand` and `followed_by` their separate meanings.
    """
    after_operand: tuple[str, ...] = ()
    """
    Keywords that continue an unfinished predicate: `IS NULL`, `IN`, `BETWEEN`.

    Distinct from `followed_by`, which is what comes after a *finished* one.
    `WHERE r.id ` takes these; `WHERE r.id > 1 ` takes AND, OR or the next
    clause. Offering both everywhere suggests `AND` where no comparison has been
    written yet, and `=` where one already has.
    """


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
class Template:
    """
    A whole statement shape, offered where a statement can begin.

    `snippet` marks the places to fill in with `$1`, `$2` and so on, `$0` last.
    The syntax is deliberately the bare LSP subset — no default values, no
    choices — because everything downstream only needs the offsets, and a
    smaller format is one fewer thing for a third-party dialect to get wrong.
    """

    label: str
    snippet: str
    detail: str = ''


@dataclass(frozen=True, slots=True)
class Query:
    """
    Introspection SQL as data.

    Placeholders are neutral $1, $2 markers; the DB-API catalog rewrites them
    for whatever paramstyle the driver reports.
    """

    sql: str
    row: Callable[[tuple[Any, ...]], object]
    """
    Maps one driver row to a value type.

    The row is `Any` because DB-API hands back untyped values whose Python types
    vary by driver — the mapper is where that gets pinned down, and it is the
    only place a backend's raw shape is allowed to be visible.
    """


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
    statement_start: tuple[str, ...] = ()
    """
    Keywords a statement may begin with.

    Without these an empty editor answers nothing: there is no clause yet, so no
    clause can say what follows it.
    """
    templates: tuple[Template, ...] = ()
    """Whole statement shapes, offered in the same position as `statement_start`."""
    types: tuple[str, ...] = ()
    """
    Data type names, for a cast position. Ordered by how often they are wanted.

    Static here, as the offline fallback plan.md §4 asks for; a backend that can
    introspect its types should prefer that, since a user's own composite and
    enum types belong in this list too.
    """
    catalog_queries: CatalogQueries = field(default_factory=CatalogQueries)

    @property
    def reserved_upper(self) -> frozenset[str]:
        """`reserved`, uppercased. Alias detection compares against folded-then-uppercased words."""
        return frozenset(word.upper() for word in self.reserved)
