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
    TYPE = 'type'
    """A data type name, wanted after a cast: `'7 days'::interval`."""
    SNIPPET = 'snippet'
    """A whole statement shape with places to fill in, offered where one can start."""
    VALUE = 'value'
    """A literal the compared column actually holds: `WHERE type = 'postgres'`."""
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
    rows: int | None = None
    """
    Roughly how many rows, as the backend already estimates it. None when unknown.

    The planner's own figure — Postgres `pg_class.reltuples`, ClickHouse
    `system.tables.total_rows` — so it costs nothing and is only as fresh as the
    last ANALYZE. Approximate on purpose: knowing a relation has millions of
    rows rather than dozens is what changes which one you pick, and an exact
    count would mean counting them.
    """


@dataclass(frozen=True, slots=True)
class Function:
    """A function, aggregate or window function as the catalog reports it."""

    schema: str | None
    name: str
    args: str | None
    """
    The argument list, `''` for none and None when the backend does not say.

    The distinction decides where the caret lands: `now()` is finished on
    insertion, `count(` is not. ClickHouse's system.functions carries no
    signatures, so None there means unknown rather than empty.
    """
    result: str

    @property
    def takes_arguments(self) -> bool:
        """Whether to expect an argument list. Unknown counts as yes, which is the safe guess."""
        return self.args != ''


@dataclass(frozen=True, slots=True)
class ColumnValue:
    """One value a column holds, with how much of the column it accounts for."""

    text: str
    frequency: float | None = None
    """
    Share of rows holding this value, 0 to 1. None when nothing measured it.

    A type that enumerates itself — a boolean, an enum — lists every value
    without saying how often each occurs, so those arrive with None. Statistics
    carry the figure, and it is what separates a value covering most of the
    table from one covering a thousandth of it.
    """


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

    @property
    def declared_name(self) -> str:
        """
        What this relation actually is, ignoring any alias.

        The counterpart to `label`. `FROM auth_user u` answers to `u` but *is*
        `auth_user`, and a suggestion's detail should say the latter — the alias
        is already visible in the text being inserted. A derived table has no
        name of its own, so it falls back to whatever it was aliased as.
        """
        return self.path[-1] if self.path else (self.alias or '')


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
    comparand: tuple[str, ...] = ()
    """
    The reference on the left of the comparison the caret is completing.

    `WHERE r.dt_created > <caret>` records `('r', 'dt_created')`. Analysis can
    say *what* was compared but not what type it has; resolve looks that up and
    drops the columns that cannot face it.
    """
    comparand_type: str | None = None
    """
    The type of that left operand, when the text says so outright.

    A cast names its own type — `'7 days'::interval > <caret>` is a temporal
    comparison however the literal is spelled — so no catalog lookup is needed
    and `comparand` stays empty. A bare literal sets nothing: an unadorned
    `'7 days'` is of unknown type in Postgres and coerces to whatever it is
    compared against, so narrowing on it would be wrong.
    """
    expecting: Literal['operand', 'operator', 'connective', 'type', 'alias'] = 'operand'
    """
    What the caret position wants next.

    `WHERE ` wants an operand, `WHERE r.id ` an operator, `WHERE r.id > 1 ` a
    connective. Carried on the Request because resolve needs it to pick between
    a clause's `after_operand` and `followed_by` lists, and resolve has no
    tokens to work it out for itself.
    """
    continues: tuple[str, ...] = ()
    """
    Words that finish the construct under the caret, when it is not a clause.

    `WHERE id IS ` and `SELECT CASE WHEN id = 1 ` are both mid-construct, and
    neither the clause model nor the catalog has anything to say about them —
    the clause is WHERE or SELECT either way. Non-empty means these words are
    the whole answer.
    """
    item_words: frozenset[str] = frozenset()
    """
    Keywords already written in the caret's own list item.

    Some words are one choice made once — a sort direction, a nulls placement.
    They are not clauses, so the once-per-branch rule does not reach them.
    """
    statement: str | None = None
    """
    Which kind of statement this is: SELECT, UPDATE, INSERT INTO...

    A clause is one entry shared by every form that uses it — WHERE belongs to
    three — so this is what tells `RETURNING` it has no business after a
    SELECT's WHERE.
    """
    written: frozenset[str] = frozenset()
    """
    Clause names already present in this branch, on both sides of the caret.

    A clause that appears once is not offered twice: `SELECT id <caret> FROM t`
    has its FROM, and accepting another gives `FROM FROM t`.
    """
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
    type: str | None = None
    """The backend's type text, for comparison checking. None when there is none to know."""
    takes_arguments: bool = False
    """A function needing an argument list, so insertion parks the caret inside its parentheses."""
    snippet: str | None = None
    """
    A template with `$1`, `$2`, `$0` marking where the caret should stop.

    Rank expands it into plain text plus offsets, so nothing downstream has to
    understand the placeholder syntax.
    """
    label: str | None = None
    """What to show in a list, when the text to insert would read poorly there."""
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
    takes_arguments: bool = False
    """A function needing an argument list. `apply_suggestion` parks the caret inside for these."""
    stops: tuple[int, ...] = ()
    """
    Offsets *within* `text` where a caret should stop, in visiting order.

    Relative rather than absolute so a caller can splice at any position:
    `replace_span[0] + stops[0]` is the first. `apply_suggestion` uses the first
    and leaves the rest to a front end that can cycle them.
    """
    label: str | None = None
    """What to show in a list. Falls back to `text`, which is usually the same thing."""
