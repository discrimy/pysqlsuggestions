"""
Stage three: turn the analysis into a Request.

This is the seam. Everything above is text; everything below is catalog access
and ranking. Kind narrowing happens here, and it is the main quality lever in a
completion engine — mediocre ones suggest everything all the time.
"""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.engine.analyse import (
    clause_at,
    in_literal,
    qualifier_and_prefix,
    scope_of,
    statement_at,
)
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Kind, Request, Scope

_NAMESPACE_KINDS = {
    'schema': Kind.SCHEMA,
    'database': Kind.SCHEMA,
    'catalog': Kind.SCHEMA,
    'table': Kind.TABLE,
}


def derive_request(sql: str, caret: int, dialect: Dialect) -> Request:
    """What should be suggested at `caret`, decided without touching a catalog."""
    tokens = lex(sql, dialect.syntax)
    lo, hi = statement_at(tokens, caret)
    clause = clause_at(tokens, lo, hi, caret, dialect.clauses)
    scope = scope_of(tokens, lo, hi, caret, dialect) if tokens else None

    if in_literal(tokens, caret):
        return Request(kinds=(), prefix='', replace_span=(caret, caret), clause=clause, scope=scope)

    qualifier, prefix, span = qualifier_and_prefix(tokens, caret)
    return Request(
        kinds=_kinds_for(clause, qualifier, scope, dialect),
        prefix=prefix,
        replace_span=span,
        qualifier=qualifier,
        clause=clause,
        scope=scope,
    )


def _kinds_for(
    clause: str | None,
    qualifier: tuple[str, ...],
    scope: Scope | None,
    dialect: Dialect,
) -> tuple[Kind, ...]:
    """What the caret position admits, narrowed by any qualifier."""
    if not qualifier:
        return _clause_kinds(clause, dialect)
    return _qualified_kinds(qualifier, scope, dialect)


def _clause_kinds(clause: str | None, dialect: Dialect) -> tuple[Kind, ...]:
    """The kinds the governing clause admits."""
    if clause is None:
        return (Kind.KEYWORD,)
    found = dialect.clauses.get(clause)
    return found.suggests if found is not None else (Kind.KEYWORD,)


def _qualified_kinds(
    qualifier: tuple[str, ...],
    scope: Scope | None,
    dialect: Dialect,
) -> tuple[Kind, ...]:
    """
    Resolution order is alias first, then namespace.

    A qualifier naming something in scope collapses the answer to columns
    outright — no keywords, no functions, no tables. Only when it matches no
    relation is it read as a schema, database or catalog name, and how deep the
    qualifier reaches decides what the next segment can be. A qualifier deeper
    than the namespace has nowhere left to go but a column.

    The union plan.md §3.3 calls for in the ambiguous Postgres
    `schema.table.column` case is a resolution concern, not a kind one: both
    readings yield COLUMN, and which relation to fetch is resolve's problem.
    """
    if scope is not None and _names_a_relation(qualifier[0], scope):
        return (Kind.COLUMN,)

    level = dialect.namespace.level_of(len(qualifier) + 1)
    if level is None:
        return (Kind.COLUMN,)

    kind = _NAMESPACE_KINDS.get(level)
    return (kind,) if kind is not None else ()


def _names_a_relation(segment: str, scope: Scope) -> bool:
    """Whether `segment` is an alias or relation name anywhere in the scope chain."""
    return any(relation.label == segment for relation in scope.visible()) or segment in scope.ctes
