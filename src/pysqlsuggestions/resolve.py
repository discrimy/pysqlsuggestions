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
from typing import TypeVar

from pysqlsuggestions.dialects.base import EXCLUSIVE, Dialect
from pysqlsuggestions.engine import datatypes
from pysqlsuggestions.ports import Cache, Catalog, SupportsColumnSearch, SupportsColumnValues, SupportsKeywords
from pysqlsuggestions.types import (
    Candidate,
    Column,
    ColumnValue,
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


class _Reader:
    """Catalog access with caching and capability detection in one place."""

    def __init__(self, catalog: Catalog, dialect: Dialect, cache: Cache | None, identity: str | None) -> None:
        self._catalog = catalog
        self._dialect = dialect
        self._cache = cache
        self._identity = identity

    def _key(self, *parts: str) -> tuple[str | None, ...]:
        """The documented cache key: (role, dialect, schema, table)."""
        return (self._identity, self._dialect.name, *parts)

    def _read(self, key: tuple[str | None, ...], produce: Callable[[], _T]) -> _T:
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
        return self._read(self._key(catalog or '', '\x00schemas'), lambda: self._catalog.schemas(catalog))

    def tables(self, schema: str | None) -> Sequence[Table]:
        """Relations in `schema`, or the default namespace."""
        return self._read(self._key(schema or '', ''), lambda: self._catalog.tables(schema))

    def columns(self, schema: str | None, table: str) -> Sequence[Column]:
        """Columns of one relation."""
        return self._read(self._key(schema or '', table), lambda: self._catalog.columns(schema, table))

    def functions(self, schema: str | None = None) -> Sequence[Function]:
        """Functions in `schema`, or everywhere."""
        return self._read(self._key(schema or '', '\x00functions'), lambda: self._catalog.functions(schema))

    def loose_columns(self, prefix: str, limit: int) -> Sequence[Column]:
        """
        Columns with no relation in scope — `SELECT <caret>` before any FROM.

        Degrades to nothing when the catalog cannot answer, which is the
        documented behaviour when SupportsColumnSearch is absent.
        """
        if not isinstance(self._catalog, SupportsColumnSearch):
            return ()
        everything = self._catalog.all_columns()
        if everything is not None:
            return everything
        return self._catalog.search_columns(prefix, limit)

    def common_values(self, schema: str | None, table: str, column: str) -> Sequence[ColumnValue]:
        """
        Frequent values of one column, from the backend's planner statistics.

        Degrades to nothing when the catalog cannot answer, which is the
        documented behaviour when SupportsColumnValues is absent. Cached like
        everything else, keyed by the column: statistics change when ANALYZE
        runs, not between keystrokes.
        """
        catalog = self._catalog
        if not isinstance(catalog, SupportsColumnValues):
            return ()
        key = self._key(schema or '', table, f'\x00values:{column}')
        return self._read(key, lambda: catalog.common_values(schema, table, column, _MAX_VALUES))

    def keywords(self) -> Sequence[tuple[str, str]]:
        """Server keywords when available, otherwise the dialect's shipped set."""
        if isinstance(self._catalog, SupportsKeywords):
            return self._catalog.keywords()
        return [(word, '') for word in sorted(self._dialect.keywords)]


def _qualified(request: Request, reader: _Reader, dialect: Dialect, limit: int) -> list[Candidate]:
    """A dotted path narrows hard: either one relation's columns, or one namespace's contents."""
    scope = request.scope
    head = request.qualifier[0]

    if scope is not None:
        relation = _find_relation(head, scope)
        if relation is not None:
            return _columns_of(relation, reader, seen=set())

    if Kind.COLUMN in request.kinds and len(request.qualifier) >= len(dialect.namespace.levels):
        # schema.table.<caret> — the deepest reading is a column of that relation.
        schema, table = request.qualifier[-2], request.qualifier[-1]
        return [_column_candidate(column) for column in reader.columns(schema, table)]

    candidates: list[Candidate] = []
    if Kind.COLUMN in request.kinds:
        # A name that is not in scope may still be a relation the catalog knows:
        # `WITH a AS (...) SELECT * FROM a WHERE auth_user.<caret>` reads as the
        # table. Nothing comes back when it is only a schema name.
        candidates += [_column_candidate(column) for column in reader.columns(None, request.qualifier[-1])]
    if Kind.TABLE in request.kinds:
        candidates += [_table_candidate(table) for table in reader.tables(request.qualifier[-1])]
    if Kind.SCHEMA in request.kinds:
        # The qualifier is the level above: `prod.<caret>` lists prod's schemas.
        candidates += [_schema_candidate(name) for name in reader.schemas(request.qualifier[-1])]
    return candidates


def _unqualified(request: Request, reader: _Reader, dialect: Dialect, limit: int) -> list[Candidate]:
    """No dot typed: everything the clause admits, from whatever is in scope."""
    candidates: list[Candidate] = []
    scope = request.scope

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
            for relation in relations:
                candidates += _columns_of(relation, reader, seen, qualify=relation.label or None)
        else:
            # Nothing is in the FROM yet, so each column carries the relation it
            # would need there. Choosing one is choosing its table as well.
            candidates += [
                _column_candidate(c, qualify=c.table, relation=(c.table,))
                for c in reader.loose_columns(request.prefix, limit)
            ]

    if Kind.TABLE in request.kinds:
        candidates += [_table_candidate(table) for table in reader.tables(None)]
        candidates += [
            Candidate(text=name, kind=Kind.CTE, detail='cte', origin='local') for name in (scope.ctes if scope else {})
        ]

    if Kind.SCHEMA in request.kinds:
        candidates += [_schema_candidate(name) for name in reader.schemas()]

    if Kind.FUNCTION in request.kinds:
        candidates += [_function_candidate(function) for function in reader.functions()]

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
    if clause is not None:
        words = (
            clause.after_operand
            if request.expecting == 'operator'
            else _unchosen(
                dialect.clauses.continuations(clause.name, statement=request.statement, used=request.written),
                request.item_words,
            )
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
    qualify: str | None = None,
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
    key = (relation.label, *relation.path)
    if key in seen:
        return []
    seen.add(key)
    shown = label or relation.declared_name

    if relation.projection is None:
        schema, table = _split_path(relation.path)
        if table is None:
            return []
        return [_column_candidate(column, shown, qualify) for column in reader.columns(schema, table)]

    return _from_projection(relation.projection, shown, reader, seen, qualify)


def _from_projection(
    projection: Projection,
    label: str,
    reader: _Reader,
    seen: set[tuple[str, ...]],
    qualify: str | None = None,
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
        candidates += _columns_of(star, reader, seen, label=label, qualify=qualify)
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


def _column_candidate(
    column: Column,
    label: str | None = None,
    qualify: str | None = None,
    relation: tuple[str, ...] = (),
) -> Candidate:
    return Candidate(
        text=column.name,
        kind=Kind.COLUMN,
        detail=f'{label or column.table}.{column.name} :: {column.type}',
        position=column.position,
        type=column.type,
        qualifier=qualify,
        relation=relation,
    )


def _table_candidate(table: Table) -> Candidate:
    size = f' ~{_as_count(table.rows)} rows' if table.rows is not None else ''
    return Candidate(text=table.name, kind=Kind.TABLE, detail=f'{table.schema}.{table.name} ({table.kind}){size}')


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


def _function_candidate(function: Function) -> Candidate:
    signature = f'{function.name}({function.args or ""}) -> {function.result}'
    return Candidate(
        text=function.name,
        kind=Kind.FUNCTION,
        detail=signature,
        type=function.result,
        takes_arguments=function.takes_arguments,
    )
