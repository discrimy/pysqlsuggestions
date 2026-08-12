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
    SEQUENCE = 'sequence'
    """
    A generator of numbers, which lives in the relation namespace and is not one.

    Selectable — `SELECT * FROM a_seq` returns its state — and never what
    anybody means by `FROM ⌶`, since a schema has one per serial column. Named
    where it is wanted instead: `nextval('⌶`, `DROP SEQUENCE ⌶`.
    """
    SCHEMA = 'schema'
    FUNCTION = 'function'
    PROCEDURE = 'procedure'
    """
    A callable that a statement invokes rather than evaluates.

    Distinct from FUNCTION because the two are not interchangeable in either
    direction: `SELECT my_procedure()` is refused outright, and `CALL now()` is
    too. A front end that colours by kind should say which one it found.
    """
    ALIAS = 'alias'
    KEYWORD = 'keyword'
    OPERATOR = 'operator'
    """`=`, `<>`, `>=`. Separate from KEYWORD because it has no case to follow."""
    TYPE = 'type'
    """A data type name, wanted after a cast: `'7 days'::interval`."""
    SNIPPET = 'snippet'
    """A whole statement shape with places to fill in, offered where one can start."""
    VALUE = 'value'
    """A literal the compared column actually holds: `WHERE type = 'postgres'`."""
    JOIN = 'join'
    """
    A whole join clause or join condition, derived from a declared foreign key.

    Not a TABLE: accepting it writes `auth_user au ON r.author_id = au.id`, not a
    name. Ranking treats it as whatever the position wanted — see `_kind_bonus`.
    """
    EXPANSION = 'expansion'
    """
    The column list a `*` stands for, as one accept.

    Not a COLUMN: a front end colouring by kind should not claim that a
    comma-separated list of six names is a column. Not a SNIPPET either, which
    means a statement shape with blanks to fill.
    """


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
    result: str | None
    """
    The type it returns, or None where there is nothing to report.

    None means two different true things and neither is a lie: a backend that
    keeps no signatures (ClickHouse), and a callable that returns nothing at all
    (a procedure, where `pg_get_function_result` is NULL). Both render the same
    way — without an arrow — because both mean "no return type to show".
    """
    kind: str = 'function'
    """
    Which sort of callable: function, aggregate, window, procedure.

    Defaulted so that every existing construction keeps working and a backend
    that cannot distinguish says the safe thing. `procedure` is the one value
    that changes behaviour: a procedure cannot appear in an expression —
    Postgres answers `… is a procedure. HINT: To call a procedure, use CALL.` —
    so the expression positions filter it out and `CALL` filters everything
    else out.
    """

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
class ForeignKey:
    """
    One declared relationship: `columns` of `table` reference `ref_columns` of `ref_table`.

    Both sides are tuples and correspond positionally, so a composite key is
    representable from the start and renders as an `AND` chain. A backend with no
    constraints reports none rather than guessing from column names: a wrong join
    condition is valid SQL that returns wrong rows, which is a worse failure than
    offering nothing.
    """

    schema: str
    table: str
    columns: tuple[str, ...]
    ref_schema: str
    ref_table: str
    ref_columns: tuple[str, ...]


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
    star: tuple[int, int] | None = None
    """
    The span of a `*` the caret sits on, qualifier included, when it stands for something.

    `u.*` is replaced whole rather than in part: each expanded column carries its
    own `u.`, so leaving the written one in place would emit the first column
    bare and the rest qualified. None when the caret is not on a star, and also
    when it is on one that stands for no relation — `SELECT *` before any FROM
    has nothing to expand, and saying so here keeps the kind out of the list.
    """
    star_of: tuple[Relation, ...] = ()
    """
    The relations that star stands for. Non-empty exactly when `star` is set.

    `t.*` names one — the relation answering to the label left of the dot. A bare
    `*` names every relation of its own query level, which is `Scope.relations`
    and not `visible()`: a star does not reach into an enclosing query.
    """
    star_qualifier: str | None = None
    """
    The label written left of the star's dot, as in `u.*`. None for a bare star.

    Carried because `star_of` cannot stand in for it: a qualified star names
    exactly one relation, and so does a bare star over a one-relation FROM.
    Without it `u.*` expanded bare — and since the span covers the qualifier
    too, the `u.` the author wrote was deleted rather than repeated.
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
    match_text: str | None = None
    """
    What matching runs against, when that is neither the text nor the label.

    A join proposal inserts a whole clause and shows one, but is hunted for by the
    name of the relation it joins — `flight`, not `flight f ON b.flight_id = f.id`.
    Without a field of its own that name had to go in `label`, which is what a
    front end displays, so the list showed a bare relation name and two proposals
    to the same table were indistinguishable.
    """
    qualifier: str | None = None
    """
    Relation label to prefix on insertion, when a bare name would be ambiguous.

    Matching still runs against `text`, so typing `na` finds `r.name`: the
    qualifier is about what gets inserted, not what has to be typed to find it.
    """
    relation: tuple[str, ...] = ()
    """The relation this column needs in the FROM clause, when the statement has none."""
    note: str | None = None
    """
    Why this candidate is worth more than its neighbours: `fk: auth_user.id`.

    Distinct from `detail`, which says what the thing *is*. A front end may render
    it differently — the annotation is the teaching part of a ranked list.
    """
    span: tuple[int, int] | None = None
    """
    What to replace, when that is not what the rest of the position replaces.

    A star expansion overwrites the star; the `FROM` offered at the same caret
    is inserted beside it. `Request.replace_span` belongs to the position, and
    one span cannot serve both — accepting `FROM` would delete the star.
    """


@dataclass(frozen=True, slots=True)
class Edit:
    """One replacement: put `text` where `span` is."""

    span: tuple[int, int]
    text: str


@dataclass(frozen=True, slots=True)
class Insertion:
    """
    A suggestion turned into an edit, with no decisions left for the caller.

    Splice `text` over `span` and put the caret at `caret`. That is the whole
    contract: whether a separator was needed, whether parentheses closed,
    whether a namespace continued, which of a template's blanks comes next —
    all of it is already decided. Every such rule that leaks into a front end
    is one that has to be reimplemented there and then kept in step.
    """

    edits: tuple[Edit, ...]
    """
    The replacements to make, latest in the text first.

    Usually one. A column chosen before any FROM exists needs two — itself, and
    the relation it belongs to — and there is no single span covering both.
    Ordering them last-first means applying them in sequence needs no offset
    arithmetic: an earlier edit cannot move a later one that has already been
    made.
    """
    caret: int
    """Where the caret goes afterwards, as an offset into the spliced text."""
    pending: tuple[int, ...] = ()
    """
    Template blanks still to visit, as absolute offsets into the spliced text.

    Carried through rather than recomputed: a blank that was not filled keeps
    its place, and the ones after it move by however much the text grew.
    """
    expects_more: bool = False
    """
    Whether the caret was left where completion should carry straight on.

    True past a namespace's dot, inside a function's parentheses, and at a
    template blank — places where accepting one suggestion asks for the next
    rather than finishing. An editor that closes its list on every accept makes
    the user retype a trigger to continue a reference the engine already knows
    is incomplete.

    Not inferable from `caret` and `edits`: a namespace whose dot had to be
    written and one whose dot was already there leave the caret in the same
    place relative to different text, and only this says they mean the same
    thing.
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
    relation: tuple[str, ...] = ()
    """
    A relation the statement does not have yet and this suggestion needs.

    Set only for a column offered before any FROM exists. Choosing one there is
    choosing its table as well — a column reference to a relation the query does
    not name is not a smaller mistake than no suggestion at all — so insertion
    writes the FROM clause in the same edit.
    """
    note: str | None = None
    """
    Why this suggestion is worth more than its neighbours: `fk: auth_user.id`.

    Distinct from `detail`, which says what the thing *is*. A front end may render
    it differently — the annotation is the teaching part of a ranked list.
    """
