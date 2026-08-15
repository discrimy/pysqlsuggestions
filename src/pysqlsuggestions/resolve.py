"""
Stage four: the only code in this library that performs I/O.

Everything above produced a `Request` without touching a database. This module
turns that description into candidates, asking the `Catalog` for exactly what the
request narrowed to — and, when the statement already described a relation, not
asking at all.

Capability degradation lives here rather than in each adapter, so an adapter only
implements what its backend actually supports.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from inspect import getattr_static
from typing import Any, TypeVar

from pysqlsuggestions.dialects.base import EXCLUSIVE, Clause, Dialect
from pysqlsuggestions.engine import datatypes, joins
from pysqlsuggestions.engine.rank import MAX_POSITION_PENALTY, quote_if_needed
from pysqlsuggestions.ports import (
    Cache,
    Catalog,
    SupportsColumnSearch,
    SupportsColumnValues,
    SupportsForeignKeys,
    SupportsKeywords,
    SupportsRelationSearch,
)
from pysqlsuggestions.types import (
    Availability,
    Candidate,
    Column,
    ColumnValue,
    ForeignKey,
    Function,
    Kind,
    Projection,
    Relation,
    Request,
    Scope,
    Table,
)

_DEFAULT_SEARCH_LIMIT = 200
_MAX_VALUES = 30
"""How many frequent values are worth offering. `pg_stats` keeps up to a hundred."""

_MAX_STAR_DEPTH = 64
"""
How many stars the projection walk will follow through to their sources.

`analyse._MAX_NESTING` is the same number for the same reason, and deliberately
not imported: `engine/` may not import this module, and a shared constant would
have to live in one of them. They bound different walks — that one descends the
scope chain, this one follows `Projection.stars` from a CTE to whatever it
selected from — and a chain of CTEs is flat to the first and deep to the second.
"""

_SEQUENCE = 'sequence'
"""
The one relation kind that is not a relation to query.

Tested for negatively — "not a sequence" rather than "one of these kinds" —
because `Table.kind` is the storage engine name on ClickHouse (`mergetree`,
`log`) and the relation type on Postgres. No whitelist of ours could enumerate
the engines a ClickHouse installation has, and one that tried would empty its
FROM clause.
"""

_NOT_QUERYABLE = frozenset({_SEQUENCE, 'index'})
"""
Relation kinds that live in the catalog and cannot be read from.

A sequence and an index: `SELECT * FROM a_seq` returns its state and is merely
useless, while `SELECT * FROM an_idx` is `ERROR: cannot open relation`. Both are
in `pg_class` and neither is what anybody means by `FROM ⌶` — and indexes
outnumber tables in an ordinary schema, 31 against 19 in the fixture this
library develops against.

Still a negative test, for the reason the single-kind version was: `Table.kind`
is the storage engine name on ClickHouse — `mergetree`, `replacingmergetree` —
so no positive list of ours could enumerate what a given installation has, and
one that tried would empty its FROM clause.
"""


def _admits(table: Table, wanted: tuple[str, ...]) -> bool:
    """
    Whether this relation belongs where `wanted` kinds are admitted.

    Empty `wanted` is the default relation position: anything queryable. A
    clause that names kinds gets exactly those and does not consult the
    exclusion at all — `DROP INDEX` wants precisely what the exclusion exists
    to hide.
    """
    if wanted:
        return table.kind in wanted
    return table.kind not in _NOT_QUERYABLE


def _relation_kinds(request: Request, dialect: Dialect) -> tuple[str, ...]:
    """The kinds the governing clause admits, or none — which means the default."""
    clause = dialect.clauses.get(request.clause) if request.clause else None
    return clause.relation_kinds if clause else ()


_T = TypeVar('_T')


def resolve(
    request: Request,
    catalog: Catalog,
    dialect: Dialect,
    *,
    cache: Cache | None = None,
    identity: str | None = None,
    limit: int = _DEFAULT_SEARCH_LIMIT,
) -> list[Candidate]:
    """Fetch what `request` asked for. Returns candidates; ranking happens after."""
    if not request.kinds:
        return []
    reader = _Reader(catalog, dialect, cache, identity)
    fetch = _qualified if request.qualifier else _unqualified
    return _of_comparable_type(fetch(request, reader, dialect, limit), request, reader)


def _of_comparable_type(candidates: list[Candidate], request: Request, reader: _Reader) -> list[Candidate]:
    """
    Drop what cannot appear opposite the comparison the caret is completing.

    `WHERE r.dt_created > ` accepts a timestamp, a date or a function returning
    one; a bigint column there is an error, and offering it costs the most
    valuable row in the list. Anything whose type is unrecognised survives —
    silence about a type is not evidence against it.
    """
    wanted = _comparand_family(request, reader)
    if wanted is None:
        return candidates
    return [c for c in candidates if c.type is None or datatypes.comparable(datatypes.family(c.type), wanted)]


def _comparand_family(request: Request, reader: _Reader) -> str | None:
    """The type family of the left operand, from a cast if it names one and the catalog otherwise."""
    if request.comparand_type is not None:
        declared = datatypes.family(request.comparand_type)
        return None if declared == datatypes.UNKNOWN else declared

    path, scope = request.comparand, request.scope
    if not path or scope is None:
        return None

    named = _find_relation(path[0], scope) if len(path) > 1 else None
    relations = [named] if named is not None else list(scope.visible())
    for relation in relations:
        for column in _catalog_columns(relation, reader):
            if column.name == path[-1]:
                found = datatypes.family(column.type)
                return None if found == datatypes.UNKNOWN else found
    return None


def _catalog_columns(relation: Relation, reader: _Reader) -> Sequence[Column]:
    """Typed columns for a relation the catalog knows. A projection carries no types."""
    if relation.projection is not None:
        return ()
    schema, table = _split_path(relation.path)
    return reader.columns(schema, table) if table else ()


def _edges(scope: Scope | None, reader: _Reader) -> Sequence[ForeignKey]:
    """
    Constraints for every schema the statement names, and for the default namespace.

    Called only from the two positions that can use them, so a statement whose
    caret never reaches a JOIN or an ON pays nothing for this.
    """
    if scope is None:
        return ()
    wanted = {_split_path(r.path)[0] for r in scope.relations if r.projection is None and r.path}
    found: dict[tuple[str, ...], ForeignKey] = {}
    for schema in sorted(wanted, key=lambda name: (name is not None, name or '')):
        for edge in reader.foreign_keys(schema):
            # Built as a tuple first: a star expression directly inside a
            # subscript is 3.11 syntax, and this package supports 3.10.
            key = (edge.schema, edge.table, *edge.columns)
            found[key] = edge
    return list(found.values())


def _declared(catalog: object, method: str) -> bool:
    """
    Whether `catalog` really implements `protocol`, rather than merely answering to it.

    `isinstance` against a runtime-checkable Protocol asks a different question on
    either side of Python 3.12. Before it, the check is `hasattr`, which a
    `__getattr__` proxy satisfies for every name it is ever asked about — so a
    lazy wrapper, or any `MagicMock` in a downstream test suite, claimed every
    capability here and then failed on the first call with `TypeError: 'object'
    object is not iterable`. From 3.12 the check uses `inspect.getattr_static`
    and the same adapter degrades cleanly.

    Both are supported: `requires-python` is `>=3.10` and CI runs three. So the
    static half is asked here as well, which makes the answer the same on all of
    them — and makes it the honest one, since a capability that exists only when
    something asks for it is not a capability this can call.

    Presence, not callability. `getattr_static` finds a plain method, a
    `classmethod`, a `staticmethod`, an inherited one and one assigned in
    `__init__`, on 3.10 and 3.12 alike; it declines only the invented kind.
    Testing that the result is callable would reject `classmethod`, whose
    descriptor is not, and that is a shape an adapter is entitled to use.
    """
    try:
        getattr_static(catalog, method)
    except AttributeError:
        return False
    return True


class _Reader:
    """Catalog access with caching and capability detection in one place."""

    def __init__(self, catalog: Catalog, dialect: Dialect, cache: Cache | None, identity: str | None) -> None:
        self._catalog = catalog
        self._dialect = dialect
        self._cache = cache
        self._identity = identity
        self._memo: dict[tuple[str | None, ...], Any] = {}
        """
        Answers already given during this request.

        Distinct from `cache`, which belongs to the caller and may be absent. A
        single completion can ask the same question twice — the relation list
        serves both the TABLE candidates and the join proposals' availability —
        and with no cache supplied that would be two round trips for one answer.
        """

    def _key(self, *parts: str | None) -> tuple[str | None, ...]:
        """
        The documented cache key: (role, dialect, schema, table).

        `None` is carried through rather than folded to `''`, because to every
        one of these readers the two mean different things: `None` is "wherever
        the search path reaches" and `''` is a namespace actually named that.
        `SELECT "".` reads a quoted empty identifier as a namespace, so the two
        calls really do both happen — and while `tables('')` correctly answers
        with nothing, writing that nothing under `tables(None)`'s key silently
        emptied the relation list for as long as the cache lived.
        """
        return (self._identity, self._dialect.name, *parts)

    def _read(self, key: tuple[str | None, ...], produce: Callable[[], _T]) -> _T:
        if key in self._memo:
            remembered: _T = self._memo[key]
            return remembered
        value = self._read_through(key, produce)
        self._memo[key] = value
        return value

    def _read_through(self, key: tuple[str | None, ...], produce: Callable[[], _T]) -> _T:
        """The caller's cache, when there is one."""
        if self._cache is None:
            return produce()
        cached = self._cache.get(key)
        if cached is not None:
            found: _T = cached
            return found
        value = produce()
        self._cache[key] = value
        return value

    def schemas(self, catalog: str | None = None) -> Sequence[str]:
        """Namespace names one level below `catalog`."""
        return self._read(self._key(catalog, '\x00schemas'), lambda: self._catalog.schemas(catalog))

    def tables(self, schema: str | None) -> Sequence[Table]:
        """
        Relations in `schema`, or the default namespace.

        The sentinel is what keeps this clear of `columns`, and it is not
        decoration: a relation named `''` is reachable from ordinary text.
        `SELECT "".⌶` is a quoted empty identifier, so `columns(None, '')` used
        to compute the very key the relation list occupies — which either handed
        a `Table` to the column renderer or, in the other order, cached an empty
        column list as the answer to "what relations are there". The second is
        silent, and `lsp/` holds one cache per session, so a single such caret
        emptied the relation list for the rest of it.
        """
        return self._read(self._key(schema, '\x00tables'), lambda: self._catalog.tables(schema))

    def columns(self, schema: str | None, table: str) -> Sequence[Column]:
        """Columns of one relation."""
        return self._read(self._key(schema, table), lambda: self._catalog.columns(schema, table))

    def unreadable_relations(self, schema: str | None = None) -> frozenset[tuple[str, str]]:
        """
        Relations in `schema` the role may read nothing in, as (schema, name) pairs.

        Free at a JOIN caret: the same relation list already answers the TABLE
        candidates there, and `_read` remembers it within the request.
        """
        return frozenset(
            (table.schema, table.name) for table in self.tables(schema) if table.availability is Availability.RESTRICTED
        )

    def functions(self, schema: str | None = None) -> Sequence[Function]:
        """Functions in `schema`, or everywhere."""
        return self._read(self._key(schema, '\x00functions'), lambda: self._catalog.functions(schema))

    def loose_columns(self, prefix: str, limit: int) -> Sequence[Column]:
        """
        Columns with no relation in scope — `SELECT <caret>` before any FROM.

        Degrades to nothing when the catalog cannot answer, which is the
        documented behaviour when SupportsColumnSearch is absent.
        """
        if not isinstance(self._catalog, SupportsColumnSearch) or not _declared(self._catalog, 'all_columns'):
            return ()
        everything = self._catalog.all_columns()
        if everything is not None:
            return everything
        return self._catalog.search_columns(prefix, limit)

    def search_relations(self, prefix: str, limit: int) -> Sequence[Table]:
        """
        Relations matching `prefix` in any namespace.

        Degrades to nothing when the catalog cannot answer, which is the
        documented behaviour when SupportsRelationSearch is absent. Not cached:
        the result depends on the prefix, which changes on every keystroke.
        """
        if not prefix or not isinstance(self._catalog, SupportsRelationSearch):
            return ()
        if not _declared(self._catalog, 'search_relations'):
            return ()
        return self._catalog.search_relations(prefix, limit)

    def common_values(self, schema: str | None, table: str, column: str) -> Sequence[ColumnValue]:
        """
        Frequent values of one column, from the backend's planner statistics.

        Degrades to nothing when the catalog cannot answer, which is the
        documented behaviour when SupportsColumnValues is absent. Cached like
        everything else, keyed by the column: statistics change when ANALYZE
        runs, not between keystrokes.
        """
        catalog = self._catalog
        if not isinstance(catalog, SupportsColumnValues) or not _declared(catalog, 'common_values'):
            return ()
        key = self._key(schema, table, f'\x00values:{column}')
        return self._read(key, lambda: catalog.common_values(schema, table, column, _MAX_VALUES))

    def foreign_keys(self, schema: str | None) -> Sequence[ForeignKey]:
        """
        Declared relationships, for join proposals.

        Degrades to nothing when the catalog cannot answer, which is the documented
        behaviour when SupportsForeignKeys is absent. Cached like everything else:
        constraints change when someone runs DDL, not between keystrokes.
        """
        catalog = self._catalog
        if not isinstance(catalog, SupportsForeignKeys) or not _declared(catalog, 'foreign_keys'):
            return ()
        return self._read(self._key(schema, '\x00fk'), lambda: catalog.foreign_keys(schema))

    def keywords(self) -> Sequence[tuple[str, str]]:
        """Server keywords when available, otherwise the dialect's shipped set."""
        if isinstance(self._catalog, SupportsKeywords) and _declared(self._catalog, 'keywords'):
            return self._catalog.keywords()
        return [(word, '') for word in sorted(self._dialect.keywords)]


def _names_a_relation(scope: Scope | None) -> bool:
    """Whether the statement already says what it reads from, here or further out."""
    return scope is not None and any(scope.visible())


def _qualified(request: Request, reader: _Reader, dialect: Dialect, limit: int) -> list[Candidate]:
    """A dotted path narrows hard: either one relation's columns, or one namespace's contents."""
    scope = request.scope
    head = request.qualifier[0]

    if scope is not None:
        relation = _find_relation(head, scope)
        if relation is not None:
            columns = _columns_of(relation, reader, seen=set())
            if request.clause != 'ON':
                return columns
            # `ON r.<caret>` has committed the left side, so a whole condition is
            # no longer expressible: lift and annotate that relation's FK columns
            # instead. The name filter is what stops the column appearing twice —
            # rank dedups on (kind, text), and these two candidates differ in kind.
            lifted = joins.condition_columns(relation, _edges(scope, reader), dialect)
            names = {candidate.text for candidate in lifted}
            return lifted + [candidate for candidate in columns if candidate.text not in names]

    if Kind.PROCEDURE in request.kinds:
        # One namespace level up from a procedure is a schema, and that is the
        # only reading — a procedure is not a member of a relation.
        return [
            _function_candidate(f, Kind.PROCEDURE)
            for f in reader.functions(request.qualifier[-1])
            if f.kind == 'procedure'
        ]

    if Kind.SEQUENCE in request.kinds:
        return [
            _table_candidate(table, kind=Kind.SEQUENCE)
            for table in reader.tables(request.qualifier[-1])
            if table.kind == _SEQUENCE
        ]

    if Kind.COLUMN in request.kinds and len(request.qualifier) >= len(dialect.namespace.levels):
        # schema.table.<caret> — the deepest reading is a column of that relation.
        schema, table = request.qualifier[-2], request.qualifier[-1]
        return [_column_candidate(column) for column in reader.columns(schema, table)]

    candidates: list[Candidate] = []
    if Kind.COLUMN in request.kinds and not _names_a_relation(scope):
        # A name that is not in scope may still be the relation the author is
        # about to write, so long as the statement names none yet: `SELECT
        # auth_user.<caret>` has no FROM and the qualifier is a fair guess at
        # what it will be. Nothing comes back when it is only a schema name.
        #
        # Once the statement *does* name relations, a qualifier that is not among
        # them cannot resolve on any backend here — Postgres answers `missing
        # FROM-clause entry for table "auth_user"` — so offering that table's
        # columns is offering a reference the server will refuse. This used to
        # cite `WITH a AS (...) SELECT * FROM a WHERE auth_user.<caret>` as the
        # case it served; that statement is refused too, which is what settled it.
        #
        # The unqualified path has always answered this way, which is the other
        # half of the argument: `SELECT ema<caret> FROM orders` offers nothing
        # from a relation the query does not name.
        candidates += [_column_candidate(column) for column in reader.columns(None, request.qualifier[-1])]
    if Kind.TABLE in request.kinds:
        candidates += [
            _table_candidate(table)
            for table in reader.tables(request.qualifier[-1])
            if _admits(table, _relation_kinds(request, dialect))
        ]
    if Kind.SCHEMA in request.kinds:
        # The qualifier is the level above: `prod.<caret>` lists prod's schemas.
        candidates += [_schema_candidate(name) for name in reader.schemas(request.qualifier[-1])]
    return candidates


_OFF_SEARCH_PATH = MAX_POSITION_PENALTY
"""
How far to demote a column whose schema is not in the default namespace.

Equal to the largest penalty `position` can express, so every in-path column
outranks every out-of-path one — and deliberately not larger, because the
penalty saturates there and a bigger number would say something the scoring
cannot hear. A table with more than fifty columns can tie, which is a fair
price for not inventing a second ranking signal.
"""


def _loose_columns(request: Request, reader: _Reader, limit: int) -> list[Candidate]:
    """
    Columns with no relation in scope — `SELECT <caret>` before any FROM.

    Each carries the relation it would need there, which insertion writes as a
    FROM clause. Two relations of the same name in different schemas therefore
    produce two entries, and they have to be told apart: rendering both as
    `invoices.amount` is what made ranking drop one of them, silently and
    whichever the user wanted.

    Lengthened only where they would collide — the same `(table, column)` pair
    under more than one schema. A shared table name is not enough:
    `public.invoices.amount` and `billing.invoices.period` can never render
    alike, so neither is touched.
    """
    columns = list(reader.loose_columns(request.prefix, limit))
    schemas: dict[tuple[str, str], set[str]] = {}
    for column in columns:
        schemas.setdefault((column.table, column.name), set()).add(column.schema)
    here = {(table.schema, table.name) for table in reader.tables(None)}
    return [
        _column_candidate(
            column,
            qualify=(column.schema, column.table) if len(schemas[column.table, column.name]) > 1 else (column.table,),
            relation=(column.schema, column.table),
            position=column.position + (0 if (column.schema, column.table) in here else _OFF_SEARCH_PATH),
        )
        for column in columns
    ]


def _ambiguous_labels(relations: Sequence[Relation]) -> frozenset[str]:
    """
    Labels naming more than one catalog relation here.

    Only catalog relations can collide. A CTE or derived table has a name unique
    within the statement, and an aliased relation answers to its alias — so this
    is empty for every query but the one that puts two same-named relations from
    different schemas in the same FROM. Postgres accepts that and then refuses
    every bare reference to either, which is the whole reason this exists.
    """
    counted: dict[str, int] = {}
    for relation in relations:
        if relation.projection is None and relation.label:
            counted[relation.label] = counted.get(relation.label, 0) + 1
    return frozenset(label for label, count in counted.items() if count > 1)


def _qualifier_for(relation: Relation, ambiguous: frozenset[str]) -> tuple[str, ...]:
    """
    What a reference to this relation must be prefixed with.

    Its label, which is what the author would write — or its whole declared
    path, when that label names something else too. The full path rather than
    the shortest disambiguating one: what counts as short enough depends on the
    search path, which this engine models only in part.
    """
    if relation.label in ambiguous:
        return relation.path
    return (relation.label,) if relation.label else ()


def _unqualified(request: Request, reader: _Reader, dialect: Dialect, limit: int) -> list[Candidate]:
    """No dot typed: everything the clause admits, from whatever is in scope."""
    candidates: list[Candidate] = []
    scope = request.scope

    if request.clause == 'JOIN' and Kind.TABLE in request.kinds:
        candidates += joins.relation_joins(
            scope,
            _edges(scope, reader),
            dialect,
            restricted=reader.unreadable_relations(),
        )
    elif request.clause == 'ON' and Kind.COLUMN in request.kinds:
        candidates += joins.join_conditions(scope, _edges(scope, reader), dialect)

    if Kind.EXPANSION in request.kinds:
        candidates += _expansion(request, reader, dialect)

    if Kind.VALUE in request.kinds:
        candidates += _values(request, reader)

    if Kind.COLUMN in request.kinds:
        relations = scope.visible() if scope else ()
        if relations:
            # Always qualified, where the relation has a name to qualify with.
            # A bare name is ambiguous the moment a second relation joins — and
            # the caret is usually in a query that is still being written, so
            # "there is only one table right now" is a fact with a short life.
            # An unnamed relation, a derived table with no alias, has nothing to
            # prefix and stays bare.
            #
            # Matching is unaffected: it runs against the column name, so `na`
            # still finds `u.name`. The qualifier is about what gets inserted.
            seen: set[tuple[str, ...]] = set()
            ambiguous = _ambiguous_labels(relations)
            for relation in relations:
                candidates += _columns_of(relation, reader, seen, qualify=_qualifier_for(relation, ambiguous))
        else:
            # Nothing is in the FROM yet, so each column carries the relation it
            # would need there. Choosing one is choosing its table as well — and
            # the schema with it, because a searched column may live outside the
            # default namespace and `FROM invoices` would not resolve.
            #
            # The reference stays bare where it can: a qualified FROM entry
            # answers to its relation name, so `SELECT invoices.amount FROM
            # billing.invoices` is what this writes and what Postgres plans. It
            # lengthens only when two schemas would render the same reference.
            candidates += _loose_columns(request, reader, limit)

    if Kind.TABLE in request.kinds:
        wanted = _relation_kinds(request, dialect)
        listed = [table for table in reader.tables(None) if _admits(table, wanted)]
        candidates += [_table_candidate(table) for table in listed]
        # A relation in the default namespace comes back from both calls, and
        # the two render differently — `invoices` and `public.invoices` — so
        # rank's dedupe, which keys on the rendered text, cannot collapse them.
        here = {(table.schema, table.name) for table in listed}
        candidates += [
            _table_candidate(table, qualify=(table.schema,))
            for table in reader.search_relations(request.prefix, limit)
            if _admits(table, wanted) and (table.schema, table.name) not in here
        ]
        candidates += [
            Candidate(text=name, kind=Kind.CTE, detail='cte', origin='local') for name in (scope.ctes if scope else {})
        ]

    if Kind.SEQUENCE in request.kinds:
        candidates += _sequences(request, reader, dialect, limit)

    if Kind.SCHEMA in request.kinds:
        candidates += [_schema_candidate(name) for name in reader.schemas()]

    if Kind.FUNCTION in request.kinds:
        # Procedures are excluded rather than merely unranked. A procedure in an
        # expression is not a poor suggestion, it is one the server refuses:
        # `SELECT archive_old_reports(…)` answers `… is a procedure`.
        candidates += [_function_candidate(f) for f in reader.functions() if f.kind != 'procedure']

    if Kind.PROCEDURE in request.kinds:
        candidates += [_function_candidate(f, Kind.PROCEDURE) for f in reader.functions() if f.kind == 'procedure']

    if Kind.SNIPPET in request.kinds:
        candidates += [
            Candidate(
                text=template.label,
                kind=Kind.SNIPPET,
                detail=template.detail or None,
                position=index,
                origin='keyword',
                snippet=template.snippet,
                label=template.label,
            )
            for index, template in enumerate(dialect.templates)
        ]

    if Kind.TYPE in request.kinds:
        candidates += [
            Candidate(text=name, kind=Kind.TYPE, detail='type', position=index, origin='keyword', literal=True)
            for index, name in enumerate(dialect.types)
        ]

    if Kind.OPERATOR in request.kinds:
        clause = dialect.clauses.get(request.clause) if request.clause else None
        candidates += [
            Candidate(text=operator, kind=Kind.OPERATOR, detail='operator', position=index, origin='keyword')
            for index, operator in enumerate(_operators(clause, dialect))
        ]

    if Kind.KEYWORD in request.kinds:
        candidates += _keywords(request, reader, dialect)

    # Deliberately not truncated: ranking has to see every candidate, or a
    # perfect match sitting past the cut is dropped before it is ever scored.
    # `limit` reaches only the prefix-dependent column search, which is bounded
    # server-side because it cannot be cached.
    return candidates


def _keywords(request: Request, reader: _Reader, dialect: Dialect) -> list[Candidate]:
    """
    Keywords for this position.

    A clause that declares what follows it offers only those: after
    `FROM auth_user ` there are about ten legal continuations, and burying them
    in six hundred reserved words is the same as not offering them.

    `position` carries the declared order through to ranking. Without it every
    continuation ties and the tiebreak sorts them by name length, which puts
    `OR` before `AND` and `FETCH` before `WHERE`.
    """
    if request.continues:
        # The construct under the caret named its own continuations; a clause's
        # list would talk about the statement, which is not what is unfinished.
        return []
    clause = dialect.clauses.get(request.clause) if request.clause else None
    if clause is None and dialect.statement_start:
        # No clause means no statement yet, so nothing can say what follows.
        return [
            Candidate(text=word, kind=Kind.KEYWORD, detail='starts a statement', position=index, origin='keyword')
            for index, word in enumerate(dialect.statement_start)
        ]
    if clause is not None and request.expecting == 'operator':
        # What continues an unfinished predicate, and nothing if the clause has
        # no such words. Falling through to the whole reserved list put AS, BY,
        # DO, IN, IS and ON after `UPDATE t SET total `, where only `=` belongs.
        return [
            Candidate(text=word, kind=Kind.KEYWORD, detail=f'after {clause.name}', position=index, origin='keyword')
            for index, word in enumerate(clause.after_operand)
        ]
    if clause is not None:
        words = _unspent_alias(
            _only_where_an_item_begins(
                _unchosen(
                    dialect.clauses.continuations(clause.name, statement=request.statement, used=request.written),
                    request.item_words,
                ),
                dialect,
                request,
            ),
            clause,
            request,
        )
        if words:
            return [
                Candidate(
                    text=word,
                    kind=Kind.KEYWORD,
                    detail=f'after {clause.name}',
                    position=index,
                    origin='keyword',
                )
                for index, word in enumerate(words)
            ]
    return [
        Candidate(text=word, kind=Kind.KEYWORD, detail=description or None, origin='keyword')
        for word, description in reader.keywords()
    ]


def _only_where_an_item_begins(words: tuple[str, ...], dialect: Dialect, request: Request) -> tuple[str, ...]:
    """
    Drop the words that can only begin an item, where one is already written.

    `LATERAL` modifies the reference after it rather than joining to the one
    before, so `JOIN auth_user AS u LATERAL` parses as nothing while
    `JOIN LATERAL f(x)` is exactly right. Which position that is, the clause
    says; which position the caret is in, `expecting` says.
    """
    if request.expecting != 'connective':
        return words
    kept = []
    for word in words:
        found = dialect.clauses.get(word)
        if found is not None and found.opens_an_item:
            continue
        kept.append(word)
    return tuple(kept)


def _unspent_alias(words: tuple[str, ...], clause: Clause, request: Request) -> tuple[str, ...]:
    """
    Drop the alias keyword once the relation it would attach to has an alias.

    `FROM flight_raw AS fr ` offering `AS` again writes a statement no server
    accepts, and it led the list — the caret sits there in every finished query.

    What counts as spent depends on what is being named. A relation clause has
    to ask the relation: the most recent one is what an alias would attach to,
    so `FROM a AS x JOIN b ` still offers it while `FROM a JOIN b AS y ` does
    not, and joins are not separated by commas for the words to settle it. A
    select item is, so there the words of the item are the whole answer —
    `SELECT f.id AS x ` has spent its `AS`, and `…, f.number ` has not.
    """
    word = clause.aliases_with
    if not word or word not in words:
        return words
    if Kind.TABLE in clause.suggests:
        relations = request.scope.relations if request.scope else ()
        spent = not (relations and relations[-1].alias is None)
    else:
        # `*` stands in the item like a name and takes no alias: `SELECT * AS x`
        # and `SELECT t.* AS x` are both syntax errors.
        spent = word in request.item_words or '*' in request.item_words
    if not spent and clause.opens_a_group:
        # The alias word introduces a group, so for this clause it is not
        # optional: nothing may follow the item until it is written.
        # `WITH recent SELECT` is a statement the server refuses, and the
        # acceptance sweep is what found it.
        return (word,)
    return tuple(other for other in words if other != word) if spent else words


def _unchosen(words: tuple[str, ...], written: frozenset[str]) -> tuple[str, ...]:
    """
    Drop the alternatives to a choice this item has already made.

    `ORDER BY id ASC ` offered `DESC`, which is not a second modifier but the
    other half of the same decision.
    """
    if not written:
        return words
    settled: set[str] = set()
    for sequence in EXCLUSIVE:
        # A choice counts as made when every word of any of its options is there:
        # `NULLS LAST` is two tokens and both have to have been typed. The last
        # one made settles everything before it, because SQL writes them in order.
        made = [
            index for index, group in enumerate(sequence) if any(set(choice.split()) <= written for choice in group)
        ]
        for group in sequence[: max(made) + 1] if made else ():
            settled |= group
    return tuple(word for word in words if word not in settled)


def _expansion(request: Request, reader: _Reader, dialect: Dialect) -> list[Candidate]:
    """
    The column list a `*` stands for, as one accept.

    Qualified once the star covers more than one relation: two relations in a
    join very often share `id`, and the unqualified list is a statement the
    server refuses. A single relation expands bare, which is what `SELECT *` in
    a one-table query should read as.

    A star the author qualified themselves stays qualified however few relations
    it covers. The span covers the `u.` as well as the star — every expanded
    column needs its own — so expanding bare there would not simplify the
    reference, it would delete it.

    Two relations sharing a label are named in full, for the reason an ordinary
    reference is — and it fixes a second fault here. Rendering both as
    `invoices` made a star over them emit `invoices.amount` twice, so the list
    was not merely ambiguous but wrong about how many columns it had.

    Rendered here rather than in `rank` because the result is not an identifier.
    `literal` carries it through untouched, which makes quoting each name this
    function's job for the same reason it is `joins.py`'s — and `snippet` would
    be wrong, since `expand_snippet` strips `$1`-shaped runs and Postgres allows
    a `$` inside a column name.
    """
    relations = request.star_of
    qualify = request.star_qualifier is not None or len(relations) > 1
    ambiguous = _ambiguous_labels(relations)
    seen: set[tuple[str, ...]] = set()
    names: list[str] = []
    omitted = 0
    for relation in relations:
        path = _qualifier_for(relation, ambiguous) if qualify else ()
        prefix = '.'.join(quote_if_needed(part, dialect) for part in path)
        for column in _columns_of(relation, reader, seen):
            if column.availability is Availability.RESTRICTED:
                # Dropped rather than listed, because this is the only candidate
                # at this caret the server would accept. Over a partly-restricted
                # relation `SELECT *` is refused outright — table SELECT implies
                # every column, so withholding one means there is no table-level
                # grant — and the expansion turns a statement that errors into
                # one that runs.
                omitted += 1
                continue
            rendered = quote_if_needed(column.text, dialect)
            names.append(f'{prefix}.{rendered}' if prefix else rendered)
    if not names:
        # An expansion to nothing would delete the star and leave `SELECT  FROM t`.
        return []
    covered = ', '.join(dict.fromkeys(relation.declared_name for relation in relations))
    written = f'{request.star_qualifier}.*' if request.star_qualifier else '*'
    return [
        Candidate(
            text=', '.join(names),
            kind=Kind.EXPANSION,
            detail=f'{len(names)} columns of {covered}',
            # The star as the author wrote it, because a list a hundred
            # characters wide is not a thing to show in a popup. The detail
            # names the relations, which is what a reader cannot already see.
            label=f'expand {written}',
            match_text='*',
            literal=True,
            span=request.star,
            # Deliberately not RESTRICTED: accepting this works. `reason` says
            # why the list is shorter than the relation, `availability` says
            # whether accepting succeeds, and here it does — marking it
            # restricted would sink the one suggestion the server accepts here
            # underneath the columns it is assembled from.
            reason=_omission(omitted),
        ),
    ]


def _omission(omitted: int) -> str | None:
    """How many columns the expansion left out, or None when it left out none."""
    if not omitted:
        return None
    return f'{omitted} column{"" if omitted == 1 else "s"} omitted: {_NO_PRIVILEGE}'


def _sequences(request: Request, reader: _Reader, dialect: Dialect, limit: int) -> list[Candidate]:
    """
    Sequences by name, from the default namespace and from a prefix search.

    The same two sources a relation comes from, and for the same reason: a
    sequence outside the search path has to be written qualified, and slice 2
    already built the half that finds one.

    Written bare or into a string literal, because the two positions that want a
    sequence spell it differently. `DROP SEQUENCE <caret>` takes an identifier.
    `nextval('<caret>` takes a string the server parses as a `regclass`, which
    means the identifier keeps its own quotes inside it —
    `nextval('billing."MonthlyTotals_id_seq"')` runs where the unquoted spelling
    is refused. The kind cannot tell the two apart; only the request can.
    """
    listed = [table for table in reader.tables(None) if table.kind == _SEQUENCE]
    here = {(table.schema, table.name) for table in listed}
    found: list[tuple[Table, str | None]] = [(table, None) for table in listed]
    found += [
        (table, table.schema)
        for table in reader.search_relations(request.prefix, limit)
        if table.kind == _SEQUENCE and (table.schema, table.name) not in here
    ]
    if not request.writes_a_literal:
        return [
            _table_candidate(table, qualify=(qualify,) if qualify else (), kind=Kind.SEQUENCE)
            for table, qualify in found
        ]
    return [_sequence_literal(table, qualify, dialect) for table, qualify in found]


def _sequence_literal(table: Table, qualify: str | None, dialect: Dialect) -> Candidate:
    """
    One sequence, spelled as the string literal that names it.

    `literal=True` carries the text through insertion untouched, which makes the
    quoting this function's job — both kinds of it. The identifier is quoted by
    the dialect's rules because the server reads the string as a `regclass`, and
    then the whole thing is quoted as a string, doubling any interior quote.

    `label` and `match_text` carry the bare name: typing `mon` should find it by
    the word-prefix tier rather than the substring one, and a popup should show a
    name rather than a quoted string.
    """
    parts = (qualify, table.name) if qualify else (table.name,)
    written = '.'.join(quote_if_needed(part, dialect) for part in parts)
    return Candidate(
        text="'" + written.replace("'", "''") + "'",
        kind=Kind.SEQUENCE,
        detail=f'{table.schema}.{table.name} (sequence)',
        label=table.name,
        match_text=table.name,
        literal=True,
        position=1 if qualify else 0,
    )


def _values(request: Request, reader: _Reader) -> list[Candidate]:
    """
    Literals the compared column actually holds.

    `WHERE type = ` is the one position where a column name is rarely the
    answer. The values come from statistics the backend already keeps, so this
    costs a catalog read of the same shape as any other — never a scan.
    """
    scope, path = request.scope, request.comparand
    if scope is None or not path:
        return []
    relation = _find_relation(path[0], scope) if len(path) > 1 else None
    relations = [relation] if relation is not None else list(scope.visible())
    for candidate in relations:
        if candidate is None or candidate.projection is not None:
            continue
        schema, table = _split_path(candidate.path)
        if not table:
            continue
        column = next((c for c in reader.columns(schema, table) if c.name == path[-1]), None)
        if column is None:
            continue
        if column.availability is Availability.RESTRICTED:
            # The check sits here rather than in `_Reader.common_values` for two
            # reasons. The Column is already in hand, so it costs no lookup —
            # and this also covers `datatypes.literals`, whose values come from
            # the type rather than from statistics. Those leak nothing, but a
            # literal compared against a column the role cannot reference is a
            # statement the server refuses either way.
            #
            # In the resolver rather than in each adapter, because Postgres is
            # the only backend whose statistics the server already filters by
            # role: it is every *other* adapter that needs this rule, which is
            # exactly why it cannot live in them.
            return []
        # The type first: where it enumerates itself the answer is exhaustive,
        # and statistics could only narrow it to the frequent ones.
        listed = datatypes.literals(column.type)
        values = (
            [ColumnValue(text=v) for v in listed] if listed else list(reader.common_values(schema, table, column.name))
        )
        return [
            Candidate(
                text=_as_literal(value.text, column.type, dialect_quote="'"),
                kind=Kind.VALUE,
                detail=_value_detail(value, f'{table}.{column.name}'),
                position=index,
                origin='catalog',
                literal=True,
                # Matched against the value as it reads, not as it is written.
                # The prefix arrives un-doubled — `'o''b` is read back as `o'b` —
                # while `text` is the doubled, quoted form it will be inserted
                # as, so comparing the two dropped the suggestion the engine had
                # just offered the moment its quote was typed.
                match_text=value.text,
            )
            for index, value in enumerate(values)
        ]
    return []


def _value_detail(value: ColumnValue, column: str) -> str:
    """`42% of orders.status`, or just the column when nothing measured the share."""
    if value.frequency is None:
        return f'value of {column}'
    return f'{_as_share(value.frequency)} of {column}'


_BARE_BOOLEANS = frozenset({'true', 'false'})
"""
The only two boolean spellings that may go in unquoted.

Backends *print* a boolean differently from how they parse one — Postgres
statistics report `t` and `f`, and `WHERE is_superuser = f` reads `f` as a
column reference and fails with `column "f" does not exist`. Anything else is
quoted, which every backend here coerces correctly.
"""


def _as_literal(value: str, type_text: str, dialect_quote: str) -> str:
    """
    Spell `value` the way the column's type wants it written.

    A number goes bare, and so do the two boolean words. Everything else is a
    string literal, which is also the safe reading of a type this engine does
    not recognise: every backend here will coerce a quoted literal, and none
    will accept a bare word it did not expect.
    """
    family = datatypes.family(type_text)
    if family == 'numeric':
        return value
    if family == 'boolean' and value.lower() in _BARE_BOOLEANS:
        return value.lower()
    return f'{dialect_quote}{value.replace(dialect_quote, dialect_quote * 2)}{dialect_quote}'


def _operators(clause: object, dialect: Dialect) -> tuple[str, ...]:
    """
    Comparison operators for this position.

    A clause that declares its own wins. Otherwise the caret is in a predicate
    the clause model does not own — a `CASE WHEN` branch inside a select list —
    and WHERE's operators are the dialect's answer to "how are two values
    compared here", which is the same question.
    """
    declared = getattr(clause, 'operators', ())
    if declared:
        found: tuple[str, ...] = declared
        return found
    fallback = dialect.clauses.get('WHERE')
    return fallback.operators if fallback else ()


def _find_relation(label: str, scope: Scope) -> Relation | None:
    """Alias first, then CTE name, walking outward through enclosing scopes."""
    for relation in scope.visible():
        if relation.label == label:
            return relation
    return scope.ctes.get(label)


def _columns_of(
    relation: Relation,
    reader: _Reader,
    seen: set[tuple[str, ...]],
    label: str | None = None,
    qualify: tuple[str, ...] = (),
    remaining: int = _MAX_STAR_DEPTH,
) -> list[Candidate]:
    """
    The columns a relation offers.

    A relation the statement described itself needs no catalog call at all —
    which is the whole point of carrying projections through the pure stages.
    `seen` guards against a CTE that refers to itself.

    `label` names the relation in the detail text. It is the relation's own
    declared name rather than its alias — `FROM auth_user u` inserts `u.id` but
    describes it as `auth_user.id`, because the alias is already visible in the
    text being inserted and the table name is the part you cannot see.

    It is threaded down through star expansion so a CTE keeps its own name: the
    columns of `WITH a AS (SELECT * FROM auth_user)` describe as `a.email`, not
    `auth_user.email`, because `a` is what that relation is.
    """
    # `seen` stops a relation referring to *itself*; it does nothing for a chain
    # of distinct relations, and a WITH is a list of siblings rather than nested
    # scopes, so `analyse`'s `_MAX_NESTING` never reaches this walk either. 495
    # links of `aN AS (SELECT * FROM aN-1)` was enough to raise, with no catalog
    # involved at all. Truncating loses the tail of an absurd chain; raising
    # loses the whole request, and `lsp/` re-enters this same path in the
    # fallback it uses after a failure.
    key = (relation.label, *relation.path)
    if key in seen or remaining <= 0:
        return []
    seen.add(key)
    shown = label or relation.declared_name

    if relation.projection is None:
        schema, table = _split_path(relation.path)
        if table is None:
            return []
        return [_column_candidate(column, shown, qualify) for column in reader.columns(schema, table)]

    return _from_projection(relation.projection, shown, reader, seen, qualify, remaining)


def _from_projection(
    projection: Projection,
    label: str,
    reader: _Reader,
    seen: set[tuple[str, ...]],
    qualify: tuple[str, ...] = (),
    remaining: int = _MAX_STAR_DEPTH,
) -> list[Candidate]:
    """Named outputs need no fetch; unresolved stars are expanded against their sources."""
    candidates = [
        Candidate(
            text=name,
            kind=Kind.COLUMN,
            detail=f'{label}.{name}',
            position=index,
            origin='local',
            qualifier=qualify,
        )
        for index, name in enumerate(projection.columns)
    ]
    for star in projection.stars:
        candidates += _columns_of(star, reader, seen, label=label, qualify=qualify, remaining=remaining - 1)
    return candidates


def _split_path(path: tuple[str, ...]) -> tuple[str | None, str | None]:
    """
    A relation path as (schema, table).

    A three-segment Trino path drops its catalog: the Catalog port is bound to one
    catalog already, so `catalog.schema.table` reads as `schema.table` here.
    """
    if not path:
        return None, None
    if len(path) == 1:
        return None, path[0]
    return path[-2], path[-1]


_NO_PRIVILEGE = 'no SELECT privilege'
"""
Why a restricted candidate would fail, in the words the server uses.

One string because there is one cause. A column withheld individually and a
column inside a relation with no grant at all are the same refusal to whoever
typed it, and Postgres reports both as `permission denied`.
"""


def _restriction(state: Availability) -> tuple[Availability, str | None]:
    """
    The availability and reason a candidate carries, given what the catalog said.

    UNKNOWN travels through rather than becoming AVAILABLE. A suggestion drawn
    from a backend that cannot answer does not know it is readable, and saying
    so costs nothing: every consumer tests `is RESTRICTED`.
    """
    return state, _NO_PRIVILEGE if state is Availability.RESTRICTED else None


def _column_candidate(
    column: Column,
    label: str | None = None,
    qualify: tuple[str, ...] = (),
    relation: tuple[str, ...] = (),
    position: int | None = None,
) -> Candidate:
    availability, reason = _restriction(column.availability)
    return Candidate(
        text=column.name,
        kind=Kind.COLUMN,
        detail=f'{label or column.table}.{column.name} :: {column.type}',
        position=column.position if position is None else position,
        type=column.type,
        qualifier=qualify,
        relation=relation,
        availability=availability,
        reason=reason,
    )


def _table_candidate(table: Table, qualify: tuple[str, ...] = (), kind: Kind = Kind.TABLE) -> Candidate:
    """
    One relation, qualified when a bare reference would not reach it.

    `position` is the in-path preference and nothing subtler: rank charges 0.1
    per step, which settles a tie between two equally good matches and is far
    too small to outrank a better one. A relation you can write bare is worth
    one step over one that costs a schema prefix — no more than that, or a
    perfect match in another schema would lose to a poor match in this one.
    """
    size = f' ~{_as_count(table.rows)} rows' if table.rows is not None else ''
    availability, reason = _restriction(table.availability)
    return Candidate(
        text=table.name,
        kind=kind,
        detail=f'{table.schema}.{table.name} ({table.kind}){size}',
        qualifier=qualify,
        position=1 if qualify else 0,
        availability=availability,
        reason=reason,
    )


def _as_count(rows: int) -> str:
    """
    A row count at a glance: `81M`, `1.2k`, `340`.

    Two significant figures at most. The estimate is the planner's and is only
    as fresh as the last ANALYZE, so spelling out eight digits would claim a
    precision it does not have — and the decision it informs is only ever
    "millions or dozens".
    """
    for limit, suffix in ((1_000_000_000, 'B'), (1_000_000, 'M'), (1_000, 'k')):
        if rows >= limit:
            scaled = rows / limit
            return f'{scaled:.0f}{suffix}' if scaled >= 10 else f'{scaled:.1f}{suffix}'  # noqa: PLR2004
    return str(rows)


def _as_share(frequency: float) -> str:
    """A share of rows as a percentage, never rounding a real value down to `0%`."""
    percent = frequency * 100
    if percent >= 10:  # noqa: PLR2004
        return f'{percent:.0f}%'
    if percent >= 1:
        return f'{percent:.1f}%'
    return '<1%'


def _schema_candidate(name: str) -> Candidate:
    return Candidate(text=name, kind=Kind.SCHEMA, detail='schema')


def _function_candidate(function: Function, kind: Kind = Kind.FUNCTION) -> Candidate:
    """
    One callable, with as much of its signature as the backend reported.

    The arrow is dropped rather than left dangling when there is no result to
    put after it: `count() -> ` reads as a broken signature where `count()`
    reads as an unknown one, and ClickHouse reports no signatures at all.

    A kind other than `function` is named, because that is the part a reader
    cannot infer from the name — `count` being an aggregate and `rank` a window
    function is what decides whether either belongs where the caret is.
    """
    signature = f'{function.name}({function.args or ""})'
    if function.result:
        signature = f'{signature} -> {function.result}'
    if function.kind != 'function':
        signature = f'{signature}  {function.kind}'
    return Candidate(
        text=function.name,
        kind=kind,
        detail=signature,
        type=function.result,
        takes_arguments=function.takes_arguments,
    )
