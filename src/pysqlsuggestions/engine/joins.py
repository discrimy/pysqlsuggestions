"""
Join proposals from declared foreign keys.

Pure: the edges arrive as data, so this module never imports `ports` and the
purity guard holds. `resolve` does the fetching and calls in here.

Two positions are answered. `JOIN <caret>` takes a whole clause — relation, alias
and condition in one accept — and `ON <caret>` takes the condition alone. Both
are built only from constraints the backend declares; nothing here guesses an
edge from a column name, because a wrong join condition is valid SQL that returns
wrong rows, and that is a worse failure than offering nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.engine.local import alias_forms
from pysqlsuggestions.engine.rank import quote_if_needed
from pysqlsuggestions.types import Candidate, ForeignKey, Kind, Relation, Scope

_FORWARD = 0
"""Many-to-one: the relation in scope holds the FK column. Ranked first — it cannot multiply rows."""
_REVERSE = 1
"""One-to-many: the relation in scope is the referenced side."""

_MIN_JOINABLE = 2
"""A condition needs two relations. One has nothing to be joined to."""

_Link = tuple[str, str, str, tuple[tuple[str, str], ...], int]
"""(source schema, target schema, target table, (source column, target column) pairs, direction)."""


def relation_joins(scope: Scope | None, edges: Sequence[ForeignKey], dialect: Dialect) -> list[Candidate]:
    """
    Whole `JOIN` clauses for `JOIN <caret>`: relation, alias and condition together.

    Fires from either end of a constraint. A join is undirected even though a
    constraint is not, and a query starting from a relation that holds no FK
    columns — `auth_user` is referenced by seven tables in the docker fixture and
    references none — would otherwise be offered nothing at all.
    """
    if scope is None:
        return []
    taken = {relation.label.lower() for relation in scope.visible() if relation.label}
    candidates: list[Candidate] = []
    for relation in _catalog_relations(scope):
        source = _split(relation.path)
        for link in _links(source, edges):
            candidates.append(_clause_candidate(relation, source, link, taken, dialect))
    return candidates


def join_conditions(scope: Scope | None, edges: Sequence[ForeignKey], dialect: Dialect) -> list[Candidate]:
    """
    Whole conditions for `ON <caret>`, pairing the relation just joined with an earlier one.

    One accept finishes the join. The columns stay underneath, so a condition the
    constraints do not describe is still reachable by writing it out.
    """
    if scope is None or len(scope.relations) < _MIN_JOINABLE:
        return []
    latest = scope.relations[-1]
    if latest.projection is not None:
        return []
    target = _split(latest.path)
    candidates: list[Candidate] = []
    for earlier in _catalog_relations(scope)[:-1]:
        source = _split(earlier.path)
        for link in _links(source, edges):
            if link[2] != target[1] or not _same_schema(target[0], link[1]):
                continue
            condition = _condition(earlier.label, latest.label, link[3], dialect)
            candidates.append(
                Candidate(
                    text=condition,
                    kind=Kind.JOIN,
                    detail=f'joins {earlier.declared_name}',
                    position=link[4],
                    snippet=condition,
                    label=_fk_column(link),
                    note=_note(link),
                ),
            )
    return candidates


def condition_columns(relation: Relation, edges: Sequence[ForeignKey], dialect: Dialect) -> list[Candidate]:
    """
    FK columns of one relation, for `ON r.<caret>` where the qualifier has committed the left side.

    A whole condition is no longer expressible there — the text already says which
    relation the left operand belongs to — so the feature degrades to lifting that
    relation's FK columns and annotating them.
    """
    if relation.projection is not None or not relation.path:
        return []
    candidates: list[Candidate] = []
    for link in _links(_split(relation.path), edges):
        name = link[3][0][0]
        candidates.append(
            Candidate(
                text=name,
                kind=Kind.JOIN,
                detail=f'joins {link[2]}',
                position=link[4],
                snippet=quote_if_needed(name, dialect),
                label=name,
                note=_note(link),
            ),
        )
    return candidates


def _catalog_relations(scope: Scope) -> list[Relation]:
    """Relations the catalog knows. A CTE or a derived table has no constraints to read."""
    return [relation for relation in scope.relations if relation.projection is None and relation.path]


def _split(path: tuple[str, ...]) -> tuple[str, str]:
    """(schema, table) from a relation path, with `''` for a schema the text did not name."""
    if len(path) >= _MIN_JOINABLE:
        return path[-2], path[-1]
    return '', path[-1] if path else ''


def _links(source: tuple[str, str], edges: Sequence[ForeignKey]) -> list[_Link]:
    """Every edge touching `source`, from either end, forward first."""
    schema, table = source
    forward: list[_Link] = []
    reverse: list[_Link] = []
    for edge in edges:
        pairs = tuple(zip(edge.columns, edge.ref_columns, strict=False))
        if edge.table == table and _same_schema(schema, edge.schema):
            forward.append((edge.schema, edge.ref_schema, edge.ref_table, pairs, _FORWARD))
        elif edge.ref_table == table and _same_schema(schema, edge.ref_schema):
            flipped = tuple((target, origin) for origin, target in pairs)
            reverse.append((edge.ref_schema, edge.schema, edge.table, flipped, _REVERSE))
    return forward + reverse


def _same_schema(named: str, declared: str) -> bool:
    """An unqualified reference matches whatever schema the edge names; the search path decided it."""
    return not named or named == declared


def _clause_candidate(
    relation: Relation,
    source: tuple[str, str],
    link: _Link,
    taken: set[str],
    dialect: Dialect,
) -> Candidate:
    """One whole `JOIN` clause, with an alias that collides with nothing already in scope."""
    source_schema, target_schema, target_table, pairs, direction = link
    alias = _free_alias(target_table, taken)
    taken.add(alias.lower())
    reference = _reference(source[0], source_schema, target_schema, target_table, dialect)
    condition = _condition(relation.label, alias, pairs, dialect)
    snippet = f'{reference} {alias} ON {condition}'
    return Candidate(
        text=snippet,
        kind=Kind.JOIN,
        detail=f'joins {relation.declared_name}',
        position=direction,
        snippet=snippet,
        label=target_table,
        note=_note(link),
    )


def _reference(named: str, source_schema: str, target_schema: str, target_table: str, dialect: Dialect) -> str:
    """
    The target's name, qualified only where the bare one would not reach it.

    Compared against the schema the *constraint* declares rather than the one the
    statement spelled: `FROM reports_report` names no schema, and whatever search
    path resolved it resolves a sibling in the same schema too. A target in
    another schema is qualified, and so is every target of a source the author
    qualified themselves — having written one out, they may be reaching past the
    search path entirely.
    """
    name = quote_if_needed(target_table, dialect)
    if target_schema and (target_schema != source_schema or named):
        return f'{quote_if_needed(target_schema, dialect)}.{name}'
    return name


def _condition(left: str, right: str, pairs: tuple[tuple[str, str], ...], dialect: Dialect) -> str:
    """`a.x = b.x`, or an AND chain when the constraint names more than one column."""
    left_label = quote_if_needed(left, dialect)
    right_label = quote_if_needed(right, dialect)
    return ' AND '.join(
        f'{left_label}.{quote_if_needed(source, dialect)} = {right_label}.{quote_if_needed(target, dialect)}'
        for source, target in pairs
    )


def _free_alias(name: str, taken: set[str]) -> str:
    """
    The first idiomatic alias nothing in scope answers to.

    Falls through to a numbered form, which is what a self-join and a second edge
    to the same target both need — `auth_user au` written twice would leave a
    statement where neither reference resolves.
    """
    forms = alias_forms(name)
    for form in forms:
        if form.lower() not in taken:
            return form
    stem = forms[0] if forms else name[:1].lower()
    suffix = 2
    while f'{stem}{suffix}' in taken:
        suffix += 1
    return f'{stem}{suffix}'


def _fk_column(link: _Link) -> str:
    """The referencing side's first column — the name a user would type looking for this join."""
    *_, pairs, direction = link
    return pairs[0][0] if direction == _FORWARD else pairs[0][1]


def _note(link: _Link) -> str:
    """
    Where the constraint lands on the far side: `fk: auth_user.id`.

    Names the target rather than the source, in both directions, because the
    source is already visible in the statement being written.
    """
    _, _, table, pairs, _ = link
    columns = [target for _, target in pairs]
    rendered = columns[0] if len(columns) == 1 else f'({", ".join(columns)})'
    return f'fk: {table}.{rendered}'
