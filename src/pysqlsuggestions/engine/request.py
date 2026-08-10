"""
Stage three: turn the analysis into a Request.

This is the seam. Everything above is text; everything below is catalog access
and ranking. Kind narrowing happens here, and it is the main quality lever in a
completion engine — mediocre ones suggest everything all the time.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.engine.analyse import (
    after_operand,
    clause_at,
    comparand_at,
    in_literal,
    predicate_complete,
    qualifier_and_prefix,
    scope_of,
    statement_at,
)
from pysqlsuggestions.engine.lex import Token, TokenType, lex
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
    expecting = _expecting(tokens, lo, hi, caret, clause, dialect)
    return Request(
        kinds=_kinds_for(clause, qualifier, scope, dialect, expecting),
        prefix=prefix,
        replace_span=span,
        qualifier=qualifier,
        clause=clause,
        scope=scope,
        comparand=comparand_at(tokens, caret),
        expecting=expecting,
        keyword_case=_keyword_case(tokens, caret, dialect),
    )


def _expecting(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    clause: str | None,
    dialect: Dialect,
) -> Literal['operand', 'operator', 'connective']:
    """
    Which of the three expression positions the caret is in.

    A clause with no operators has no predicates either — a select list, a GROUP
    BY — so a completed item there goes straight to 'connective', where its
    `followed_by` list lives.
    """
    if not after_operand(tokens, caret, dialect):
        return 'operand'
    found = dialect.clauses.get(clause) if clause else None
    if found is None or not found.operators:
        return 'connective'
    return 'connective' if predicate_complete(tokens, lo, hi, caret, dialect) else 'operator'


def _keyword_case(tokens: Sequence[Token], caret: int, dialect: Dialect) -> Literal['lower', 'upper'] | None:
    """
    How the author is writing keywords: the half-typed word if there is one,
    otherwise the last complete keyword before the caret.

    It has to read `Token.text` rather than `Request.prefix`, because the lexer
    folds identifiers for a case-insensitive dialect — `WH` arrives as `wh` and
    the typed case is gone. The raw slice is the only place it survives.
    """
    for token in reversed(tokens):
        if token.type is not TokenType.IDENT or token.quoted or token.start >= caret:
            continue
        typed = token.text[: caret - token.start]
        partial = token.end > caret
        if not partial and token.value.upper() not in dialect.keywords:
            continue
        if not typed.isalpha():
            continue
        return 'lower' if typed.islower() else 'upper'
    return None


def _kinds_for(
    clause: str | None,
    qualifier: tuple[str, ...],
    scope: Scope | None,
    dialect: Dialect,
    expecting: str,
) -> tuple[Kind, ...]:
    """What the caret position admits, narrowed by any qualifier."""
    if not qualifier:
        return _clause_kinds(clause, scope, dialect, expecting)
    return _qualified_kinds(qualifier, scope, dialect)


def _clause_kinds(
    clause: str | None,
    scope: Scope | None,
    dialect: Dialect,
    expecting: str,
) -> tuple[Kind, ...]:
    """
    The kinds the governing clause admits.

    Once the clause has an item, what usually comes *next* is offered too: after
    `FROM auth_user ` the useful answer is WHERE or JOIN, not another table.

    In a relation position "the clause has an item" is exactly "a relation was
    read into scope", and tables stay on offer because a comma may still bring
    another.

    In an expression position the keywords *replace* the columns rather than
    joining them: after `WHERE r.id ` another column name would not parse, so
    offering one is worse than offering nothing. Which keywords depends on
    whether the predicate is finished — see `Request.expecting`.
    """
    if clause is None:
        return (Kind.KEYWORD,)
    found = dialect.clauses.get(clause)
    if found is None:
        return (Kind.KEYWORD,)

    kinds = found.suggests
    if not found.followed_by:
        return kinds
    if Kind.TABLE in kinds:
        return (*kinds, Kind.KEYWORD) if (scope and scope.relations) else kinds
    if expecting == 'operand':
        return kinds
    # An operator is the likeliest next token after `WHERE r.id `, so it leads.
    # A finished predicate takes a connective instead, and no second comparison.
    return (Kind.OPERATOR, Kind.KEYWORD) if expecting == 'operator' else (Kind.KEYWORD,)


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
