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
    at_the_clause_start,
    case_position,
    clause_at,
    clauses_written,
    comparand_at,
    continues_a_keyword,
    depth_at,
    in_literal,
    in_placeholder,
    inside_a_cast_awaiting_as,
    predicate_complete,
    qualifier_and_prefix,
    scope_of,
    star_at,
    star_qualifier,
    star_relations,
    star_span,
    statement_at,
    statement_form,
    string_under,
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

    comparand, comparand_type = comparand_at(tokens, caret, dialect)
    if in_placeholder(tokens, caret):
        # Above the literal check rather than folded into it: a half-written
        # literal has an answer — the values that column holds — and a
        # half-written parameter has none. Keeping them apart is what stops an
        # edit to one silently changing the other.
        return Request(kinds=(), prefix='', replace_span=(caret, caret), clause=clause, scope=scope)
    if in_literal(tokens, caret):
        return _inside_a_literal(tokens, caret, clause, scope, comparand)

    qualifier, prefix, span = qualifier_and_prefix(tokens, caret)
    continues, only = _continues(tokens, lo, hi, caret, dialect, clause, prefix)
    expecting = _expecting(tokens, lo, hi, caret, clause, dialect)

    star = star_at(tokens, caret, dialect)
    star_of = star_relations(tokens, star, scope) if star is not None else ()
    if not star_of:
        # A star standing for no relation is not a star worth recording. `SELECT *`
        # before any FROM has nothing to expand, and dropping it here keeps the
        # kind out of the list rather than leaving resolve to answer nothing.
        star = None

    return Request(
        kinds=_continued_kinds(
            continues,
            only,
            _expansion_first(star)
            + _values_first(comparand, expecting, qualifier)
            + _kinds_for(clause, qualifier, scope, dialect, expecting, depth_at(tokens, caret) > 0),
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
        item_words=words_in_item(tokens, caret, dialect),
        statement=statement_form(tokens, lo, hi, caret, dialect),
        written=clauses_written(tokens, lo, hi, caret, dialect),
        keyword_case=_keyword_case(tokens, caret, dialect),
        star=star_span(tokens, star) if star is not None else None,
        star_of=star_of,
        star_qualifier=star_qualifier(tokens, star) if star is not None else None,
    )


def _inside_a_literal(
    tokens: Sequence[Token],
    caret: int,
    clause: str | None,
    scope: Scope | None,
    comparand: tuple[str, ...],
) -> Request:
    """
    What a caret inside a literal or a comment admits.

    Nothing, except in the one place a literal is being written *as* a value:
    `WHERE type = 'clic<caret>` is asking which values that column holds, and
    going silent the moment the opening quote is typed makes the feature look
    broken. The span covers the literal, so the answer replaces it rather than
    nesting inside it.
    """
    written = string_under(tokens, caret)
    if written is None or not comparand:
        return Request(kinds=(), prefix='', replace_span=(caret, caret), clause=clause, scope=scope)
    quote = written.text[0]
    typed = written.text[1 : caret - written.start]
    return Request(
        kinds=(Kind.VALUE,),
        prefix=typed.replace(quote * 2, quote),
        replace_span=(written.start, written.end if written.terminated else caret),
        clause=clause,
        scope=scope,
        comparand=comparand,
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


def _expansion_first(star: int | None) -> tuple[Kind, ...]:
    """
    A star under the caret leads, because putting it there is what asks for this.

    Beside `_values_first` and for the same reason: both are positions where one
    kind comes first on the strength of something in the text that the clause
    model cannot see.
    """
    return (Kind.EXPANSION,) if star is not None else ()


def _values_first(comparand: tuple[str, ...], expecting: str, qualifier: tuple[str, ...]) -> tuple[Kind, ...]:
    """
    Whether a literal belongs here, and it leads when it does.

    Only right of a comparison whose left side names a column, and only before
    a dot is typed: `= d.<caret>` is reaching for another column, not a value.
    Right of an operator a concrete value is what is usually wanted, so it goes
    above the columns rather than beside them.
    """
    return (Kind.VALUE,) if comparand and expecting == 'operand' and not qualifier else ()


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
    clause: str | None,
    prefix: str,
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
    found = continues_a_keyword(tokens, caret, dialect)
    if found:
        return found, True

    # A cast holds a keyword between its two halves, and after the value that
    # keyword is the only thing that can follow.
    if inside_a_cast_awaiting_as(tokens, caret) and after_operand(tokens, caret, dialect):
        return ('AS',), True

    # Words that stand between a clause and its first item — `SELECT DISTINCT`.
    # Only once something is typed: `SELECT ` is the commonest caret in the
    # language and a column is nearly always what belongs there, so putting a
    # rarely-wanted word above every column costs more than it can return.
    # Behind a prefix it costs nothing and `SELECT dis` still finds it.
    opening = dialect.clauses.get(clause) if clause else None
    if prefix and opening is not None and opening.before_the_item and at_the_clause_start(tokens, caret, opening.name):
        return opening.before_the_item, False

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
        if expecting == 'connective':
            # The relation is written out, and another cannot simply follow it —
            # a comma or a JOIN has to come between them. Offering one here
            # proposes `FROM flight_raw AS fr events`, which parses as nothing,
            # and on a three-level dialect the catalogs are what it proposes.
            return (Kind.KEYWORD,)
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

    The ambiguous Postgres `schema.table.column` case needs both readings, but
    that is a resolution concern rather than a kind one: each yields COLUMN, and
    which relation to fetch is resolve's problem.
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
        # Both readings, because both are legal. One segment above a table is a
        # schema, but it is also how a relation not in the FROM list is written,
        # and a name matching a real table is far likelier to be that than a
        # schema that happens to share the name.
        return (Kind.COLUMN, kind)
    return (kind,)


def _names_a_relation(segment: str, scope: Scope) -> bool:
    """Whether `segment` is an alias or relation name anywhere in the scope chain."""
    return any(relation.label == segment for relation in scope.visible()) or segment in scope.ctes
