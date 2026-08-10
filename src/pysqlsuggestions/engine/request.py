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
    after_as,
    after_cast,
    after_operand,
    case_position,
    clause_at,
    clauses_written,
    comparand_at,
    continues_a_keyword,
    depth_at,
    in_literal,
    predicate_complete,
    qualifier_and_prefix,
    scope_of,
    statement_at,
    statement_form,
    words_in_item,
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
    """
    What should be suggested at `caret`, decided without touching a catalog.

    A caret outside the text is clamped rather than rejected. Editors do send
    stale offsets, and every span this returns indexes `sql` — a negative one
    would splice through Python's wrap-around into the middle of the query
    instead of failing where a caller could see it.
    """
    caret = max(0, min(caret, len(sql)))
    tokens = lex(sql, dialect.syntax)
    lo, hi = statement_at(tokens, caret)
    clause = clause_at(tokens, lo, hi, caret, dialect.clauses)
    scope = scope_of(tokens, lo, hi, caret, dialect) if tokens else None

    if in_literal(tokens, caret):
        return Request(kinds=(), prefix='', replace_span=(caret, caret), clause=clause, scope=scope)

    qualifier, prefix, span = qualifier_and_prefix(tokens, caret)
    continues, only = _continues(tokens, lo, hi, caret, dialect)
    expecting = _expecting(tokens, lo, hi, caret, clause, dialect)
    comparand, comparand_type = comparand_at(tokens, caret, dialect)
    return Request(
        kinds=_continued_kinds(
            continues,
            only,
            _kinds_for(clause, qualifier, scope, dialect, expecting, depth_at(tokens, caret) > 0),
        ),
        prefix=prefix,
        replace_span=span,
        qualifier=qualifier,
        clause=clause,
        scope=scope,
        continues=continues,
        comparand=comparand,
        comparand_type=comparand_type,
        expecting=expecting,
        item_words=words_in_item(tokens, caret),
        statement=statement_form(tokens, lo, hi, caret, dialect),
        written=clauses_written(tokens, lo, hi, caret, dialect),
        keyword_case=_keyword_case(tokens, caret, dialect),
    )


def _expecting(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    clause: str | None,
    dialect: Dialect,
) -> Literal['operand', 'operator', 'connective', 'type', 'alias']:
    """
    Which expression position the caret is in.

    A clause with no operators has no predicates either — a select list, a GROUP
    BY — so a completed item there goes straight to 'connective', where its
    `followed_by` list lives.
    """
    # A cast reads first: `CAST(x AS <caret>)` is spelled with the same `AS` that
    # introduces an alias, and only the enclosing call tells them apart.
    if after_cast(tokens, caret, dialect):
        return 'type'
    if after_as(tokens, caret):
        return 'alias'
    if not after_operand(tokens, caret, dialect):
        return 'operand'
    if case_position(tokens, caret) == 'when':
        # A WHEN branch is a predicate however plain the enclosing clause is:
        # `SELECT CASE WHEN id ` wants `=`, and SELECT declares no operators.
        return 'connective' if predicate_complete(tokens, lo, hi, caret, dialect) else 'operator'
    found = dialect.clauses.get(clause) if clause else None
    if found is None or not found.operators:
        return 'connective'
    return 'connective' if predicate_complete(tokens, lo, hi, caret, dialect) else 'operator'


_CASE_CONTINUATIONS = {
    'start': ('WHEN',),
    'when': ('THEN',),
    'then': ('WHEN', 'ELSE', 'END'),
    'else': ('END',),
}


def _continued_kinds(continues: tuple[str, ...], only: bool, otherwise: tuple[Kind, ...]) -> tuple[Kind, ...]:
    """Kinds for a caret inside a construct: its own words, leading or alone."""
    if not continues:
        return otherwise
    return (Kind.KEYWORD,) if only else (Kind.KEYWORD, *otherwise)


def _continues(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> tuple[tuple[str, ...], bool]:
    """
    The words that finish a half-written construct here, and whether they are all.

    Two sources: a multi-word keyword the author has started — `IS `, `NULLS ` —
    and a CASE expression, whose branches are the same shape but tracked by
    position rather than by the words immediately to the left.

    Straight after `CASE` the words are a lead rather than the whole answer:
    `CASE WHEN` and `CASE x WHEN` are both real, so a column still belongs.
    Everywhere else nothing but these words can parse.
    """
    found = continues_a_keyword(tokens, caret)
    if found:
        return found, True

    where = case_position(tokens, caret)
    if where is None:
        return (), False
    if where == 'start':
        return _CASE_CONTINUATIONS['start'], after_operand(tokens, caret, dialect)
    if not after_operand(tokens, caret, dialect):
        # Mid-branch the ordinary rules apply: `THEN ` and `WHEN ` open operands.
        return (), False
    if where == 'when' and not predicate_complete(tokens, lo, hi, caret, dialect):
        return (), False
    return _CASE_CONTINUATIONS.get(where, ()), True


def _keyword_case(tokens: Sequence[Token], caret: int, dialect: Dialect) -> Literal['lower', 'upper'] | None:
    """
    How the author is writing keywords: the last complete one they finished.

    A half-typed word is only consulted when there are no complete keywords to
    go on. Two lowercase letters in a document of uppercase keywords means the
    shift key has not been pressed *yet*, not that the style has changed — so
    `GROUP BY d.id` followed by `or` completes to `ORDER BY`.

    It has to read `Token.text` rather than `Request.prefix`, because the lexer
    folds identifiers for a case-insensitive dialect — `WH` arrives as `wh` and
    the typed case is gone. The raw slice is the only place it survives.
    """
    partial: Literal['lower', 'upper'] | None = None
    for token in reversed(tokens):
        if token.type is not TokenType.IDENT or token.quoted or token.start >= caret:
            continue
        typed = token.text[: caret - token.start]
        if not typed.isalpha():
            continue
        written: Literal['lower', 'upper'] = 'lower' if typed.islower() else 'upper'
        if token.end >= caret:
            partial = written  # the caret sits at or inside this word: it is still being typed
        elif token.value.upper() in dialect.keywords:
            return written
    return partial


def _kinds_for(
    clause: str | None,
    qualifier: tuple[str, ...],
    scope: Scope | None,
    dialect: Dialect,
    expecting: str,
    inside_a_group: bool = False,
) -> tuple[Kind, ...]:
    """What the caret position admits, narrowed by any qualifier."""
    if not qualifier or expecting == 'type':
        return _clause_kinds(clause, scope, dialect, expecting, inside_a_group)
    return _qualified_kinds(qualifier, scope, dialect)


def _clause_kinds(
    clause: str | None,
    scope: Scope | None,
    dialect: Dialect,
    expecting: str,
    inside_a_group: bool = False,
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
    if expecting == 'alias':
        # Only what this engine can derive from the relation's own name. A table
        # or a keyword here would overwrite the name the author is inventing.
        return (Kind.ALIAS,)
    if expecting == 'type':
        return (Kind.TYPE,)
    if clause is None:
        # Nothing written yet: a statement may begin, and a whole shape is worth
        # offering alongside the single words that start one.
        return (Kind.SNIPPET, Kind.KEYWORD)
    if clause == 'INSERT INTO' and inside_a_group:
        # `INSERT INTO orders (<caret>` is the column list. The clause otherwise
        # names a relation, and offering another one there cannot parse.
        return (Kind.COLUMN,)
    found = dialect.clauses.get(clause)
    if found is None:
        return (Kind.KEYWORD,)

    kinds = found.suggests
    # Whether anything at all may follow this clause, before the caret's own
    # statement form and history narrow it. A clause with no continuations —
    # RETURNING, FETCH — ends the statement, so no keyword belongs after it.
    if not dialect.clauses.continuations(found.name):
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
    if kind is None:
        return ()
    if kind is Kind.TABLE:
        # The union plan.md 3.3 asks for. One segment above a table is a schema,
        # but it is also how a relation not in the FROM list is written, and a
        # name matching a real table is far likelier to be that than a schema
        # that happens to share the name.
        return (Kind.COLUMN, kind)
    return (kind,)


def _names_a_relation(segment: str, scope: Scope) -> bool:
    """Whether `segment` is an alias or relation name anywhere in the scope chain."""
    return any(relation.label == segment for relation in scope.visible()) or segment in scope.ctes
