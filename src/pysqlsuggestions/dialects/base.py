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
class Placeholder:
    """
    One way a dialect spells a bound parameter.

    The spellings in the wild differ structurally rather than in text, which is
    why this is a record and not a string: `?` is complete on its own, `$1`
    takes digits, `:name` takes an identifier, and `${var}` takes anything up to
    a closing brace.
    """

    opens: str
    """The literal text that begins one."""
    body: Literal['name', 'digits', 'none', 'any'] = 'name'
    """
    What may follow `opens`.

    'any' runs to `closes` and is meaningless without it — a placeholder
    declaring one and not the other can never end, and `DialectConformance`
    reports it rather than letting it silently never lex.
    """
    closes: str = ''
    """The delimiter that ends it. Empty where the body ends itself."""


TEMPLATE_PLACEHOLDER = Placeholder(opens='${', body='any', closes='}')
"""
The `${var}` of a templating layer — dbt, Metabase, Jinja over any backend.

Shipped and wired into no dialect, because it is not a backend's syntax and
putting it in `ANSI` would state something false about the standard. A caller
whose SQL is templated composes it in, which is how a dialect is extended here:

    syntax = replace(POSTGRES.syntax, placeholders=(*POSTGRES.syntax.placeholders, TEMPLATE_PLACEHOLDER))
    DIALECT = replace(POSTGRES, syntax=syntax)
"""


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
    escape_string_prefix: str = ''
    """
    A letter that turns the literal after it into an escape string.

    Postgres `E'a\\'b'` processes backslash escapes however
    `standard_conforming_strings` is set, so the escaped quote is not the
    closing one — reading it as one opens a second literal that swallows the
    rest of the statement.
    """
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
    placeholders: tuple[Placeholder, ...] = ()
    """
    How this dialect spells a bound parameter. Longest `opens` is tried first.

    Without one, `:param` is punctuation followed by an identifier, and that
    identifier is offered column names — the one active wrong answer this engine
    gives, since accepting `user_settings` for `:us` writes valid SQL that runs a
    different query.
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
    """
    Clauses this one may appear after. Empty means unconstrained.

    Read backwards by `ClauseModel.continuations`: declaring it here is how a
    clause gets itself offered, and it is the only declaration a dialect needs
    to make. Not used as a filter — these sets say where a clause is *typical*,
    not everywhere it is legal, and `HAVING` after a bare `FROM` is valid SQL
    however unusual.
    """
    statements: frozenset[str] = frozenset()
    """
    Statement forms this clause belongs to. Empty means every form.

    `RETURNING` after a SELECT's WHERE is a syntax error, but the clause model
    is otherwise blind to which statement it is inside: WHERE is one entry
    shared by SELECT, UPDATE and DELETE.
    """
    repeats: bool = False
    """
    Whether the clause may appear more than once in a query branch.

    A join follows a join, and each branch of a set operation brings its own
    SELECT and FROM. Everything else appears once, so offering it again after
    it has been written produces `SELECT id FROM FROM events`.
    """
    suggests: tuple[Kind, ...] = ()
    """Most relevant first."""
    followed_by: tuple[str, ...] = ()
    """
    What usually comes next, once this clause has an item.

    Offering the whole reserved-word list after `FROM auth_user ` is useless —
    there are hundreds and only a handful can legally follow. Holds the words
    that are not clauses in their own right — `AS`, `AND`, `ASC` — plus the
    canonical clause order; anything a dialect adds arrives through `follows`.
    """
    before_the_item: tuple[str, ...] = ()
    """
    Words that stand between this clause and its first item.

    `DISTINCT` may only follow SELECT itself: `SELECT * DISTINCT` and
    `SELECT id, DISTINCT` are both syntax errors. Listing it in `followed_by`
    put it in the one place it cannot go and nowhere it can — it was offered
    after an item was written, and typing `dis` at the start of a select list
    found nothing.

    Offered alongside what the clause suggests rather than instead of it, since
    a column is the likelier answer in that position either way.
    """
    opens_an_item: bool = False
    """
    Whether this clause can only begin an item, never follow a finished one.

    `LATERAL` modifies the relation reference after it rather than joining to
    the one before: `JOIN LATERAL f(x)` and `FROM a, LATERAL f(x)` are right,
    and `JOIN auth_user AS u LATERAL` parses as nothing. `JOIN` itself carries
    its own separator and so may follow a relation, which is why this is a
    property of the clause and not of relation clauses in general.
    """
    aliases_with: str = ''
    """
    The word that gives this clause's relation an alias, where it takes one.

    Named separately from `followed_by` because it is spent: `FROM flight_raw `
    may take `AS`, and `FROM flight_raw AS fr ` may not — a second one parses as
    nothing. `followed_by` says what may follow the clause, which is not the
    same as what has not been used yet.

    The words already written cannot settle it the way they settle `ASC`/`DESC`.
    Those are one item apart, and joins are not: `FROM a AS x JOIN b ` is a
    single item containing an `AS` that belongs to a different relation.
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
        """
        A new model with `clauses` added. The receiver is untouched.

        A name already present is *replaced* rather than appended: two entries
        called WHERE would leave the first one answering every lookup, so a
        dialect refining a shared clause would silently change nothing.
        """
        added = {clause.name: clause for clause in clauses}
        kept = tuple(added.pop(clause.name, clause) for clause in self.clauses)
        return ClauseModel(clauses=kept + tuple(added.values()))

    def get(self, name: str) -> Clause | None:
        """The clause called `name`, or None. Linear scan over a few dozen entries."""
        for clause in self.clauses:
            if clause.name == name:
                return clause
        return None

    def continuations(
        self,
        name: str,
        *,
        statement: str | None = None,
        used: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        """
        What may be written after clause `name` has an item, most likely first.

        Three sources of truth, combined here so no dialect has to restate any
        of them: the clause's own `followed_by`, every clause declaring it
        `follows` this one, and the statement form. `used` names the clauses
        already written in this branch, which cannot come again unless they
        repeat.
        """
        clause = self.get(name)
        if clause is None:
            return ()
        derived = tuple(
            other.name for other in self.clauses if name in other.follows and other.name not in clause.followed_by
        )
        offered = (*clause.followed_by, *derived)
        return tuple(word for word in offered if self._admits(word, statement, used))

    def _admits(self, word: str, statement: str | None, used: frozenset[str]) -> bool:
        """Whether `word` can be written here. A word that is not a clause is always allowed."""
        found = self.get(word)
        if found is None:
            return True
        if found.statements and statement is not None and statement not in found.statements:
            return False
        return found.repeats or word not in used

    def names(self) -> tuple[str, ...]:
        """Clause names ordered longest first, so greedy matching tries 'GROUP BY' before 'BY'."""
        return tuple(sorted((c.name for c in self.clauses), key=lambda n: (-len(n.split()), -len(n), n)))


EXCLUSIVE = (
    (frozenset({'ASC', 'DESC'}), frozenset({'NULLS FIRST', 'NULLS LAST'})),
    (frozenset({'DISTINCT', 'ALL'}),),
    # Two spellings of the same limit. `LIMIT 10 FETCH FIRST 2 ROWS ONLY` names
    # a row count twice and no server takes it, so writing either settles both.
    (frozenset({'LIMIT', 'FETCH'}),),
)
"""
Choices made once per list item, each sequence written in the order SQL takes it.

Not clauses, so the once-per-branch rule does not reach them, and `ORDER BY id
ASC ` was still offering `DESC`. Making a later choice settles the earlier ones
too: a direction precedes a nulls placement, so `NULLS LAST ` cannot be followed
by `ASC`. A new item after a comma gets every choice back.
"""


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
    values: Query | None = None
    """Frequent values of one column, from the backend's own planner statistics."""
    column_search: Query | None = None
    """
    Columns matching a substring, across every visible relation.

    For `SELECT <caret>` before any FROM exists. `$1` is what has been typed.
    Absent means that position offers no columns, which is the right answer for
    a backend where finding out would mean asking every catalog in turn.
    """
    foreign_keys: Query | None = None
    """
    Declared relationships whose referencing side is in one schema. `$1` is the schema.

    Absent means the backend keeps no constraints, which is the truth for
    ClickHouse and Trino — and the reason join proposals are Postgres-only. A
    dialect must not fill this in with a name-matching heuristic: a wrong join
    condition is valid SQL that returns wrong rows.
    """


@dataclass(frozen=True, slots=True)
class Dialect:
    """A backend, as data."""

    name: str
    syntax: Syntax = field(default_factory=Syntax)
    namespace: Namespace = field(default_factory=Namespace)
    clauses: ClauseModel = field(default_factory=ClauseModel)
    keywords: frozenset[str] = frozenset()
    """
    Offered as completions. Ideally introspected; the static set is the offline
    fallback.

    Every word the clause model can put on screen is added to it at
    construction, because this set is also how the analyser tells a keyword from
    a name. A word the model suggests and this set omits reads as an identifier
    the moment the author writes it: `UPDATE ` looked like a finished operand,
    so the caret after it was offered the clauses that follow a relation instead
    of the relation itself, and `SET ` was offered an operator rather than the
    column to assign to. The two sets have to agree, so they are not maintained
    separately.
    """
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

    Static here, so a cast position still answers with no connection at all. A
    backend that can introspect its types should prefer that, since a user's own
    composite and enum types belong in this list too.
    """
    catalog_queries: CatalogQueries = field(default_factory=CatalogQueries)

    def __post_init__(self) -> None:
        """Fold the clause model's own vocabulary into `keywords`."""
        spoken = {
            word.upper()
            for clause in self.clauses.clauses
            for phrase in (clause.name, *clause.followed_by, *clause.after_operand)
            for word in phrase.split()
        }
        spoken |= {word.upper() for phrase in self.statement_start for word in phrase.split()}
        # Runs on `dataclasses.replace` too, which is how every dialect is built,
        # so a clause added by one cannot leave its words unrecognised.
        object.__setattr__(self, 'keywords', self.keywords | spoken)

    @property
    def reserved_upper(self) -> frozenset[str]:
        """`reserved`, uppercased. Alias detection compares against folded-then-uppercased words."""
        return frozenset(word.upper() for word in self.reserved)
