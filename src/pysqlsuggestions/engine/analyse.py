"""
Pure analysis over a token stream.

Every function here takes tokens and a caret offset and returns a plain value.
Nothing performs I/O, and nothing knows what a catalog is.
"""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.dialects.base import ClauseModel, Dialect
from pysqlsuggestions.engine.lex import Token, TokenType
from pysqlsuggestions.types import Relation, Scope

_SKIP = (TokenType.WHITESPACE, TokenType.COMMENT)
_RELATION_CLAUSES = frozenset({'FROM', 'JOIN', 'UPDATE', 'DELETE FROM', 'INSERT INTO'})
_JOIN_QUALIFIERS = frozenset(
    """
    LEFT RIGHT FULL INNER OUTER CROSS NATURAL LATERAL ANTI SEMI ASOF GLOBAL ANY ALL
    """.split(),
)


def _index_before(tokens: Sequence[Token], caret: int) -> int:
    """Index of the last token starting strictly before `caret`, or -1."""
    last = -1
    for index, token in enumerate(tokens):
        if token.start < caret:
            last = index
        else:
            break
    return last


def depth_at(tokens: Sequence[Token], caret: int) -> int:
    """The paren depth the caret sits at."""
    index = _index_before(tokens, caret)
    if index < 0:
        return 0
    token = tokens[index]
    if token.type is TokenType.PUNCT and token.text == '(' and token.end <= caret:
        return token.depth + 1
    return token.depth


def in_literal(tokens: Sequence[Token], caret: int) -> bool:
    """
    Whether the caret sits inside a string literal or a comment.

    A caret at the closing delimiter of a *terminated* literal is outside it —
    `'ab'<caret>` is back in ordinary SQL. A caret at the end of an unterminated
    one is inside, because there is no delimiter to have passed.
    """
    for token in tokens:
        if token.type not in (TokenType.STRING, TokenType.COMMENT):
            continue
        if token.start < caret < token.end or (caret == token.end and not token.terminated):
            return True
    return False


def statement_at(tokens: Sequence[Token], caret: int) -> tuple[int, int]:
    """
    The index range [lo, hi) of the statement containing `caret`.

    Statements are separated by semicolons at depth 0; a semicolon inside a
    string or inside parens does not split.
    """
    lo = 0
    for index, token in enumerate(tokens):
        if token.type is TokenType.PUNCT and token.text == ';' and token.depth == 0:
            if token.start >= caret:
                return lo, index
            lo = index + 1
    return lo, len(tokens)


def qualifier_and_prefix(
    tokens: Sequence[Token],
    caret: int,
) -> tuple[tuple[str, ...], str, tuple[int, int]]:
    """
    The dotted path and half-typed word immediately left of the caret.

    Returns (qualifier segments, prefix, replace_span). The span always ends at
    the caret, so choosing a suggestion replaces what was typed and nothing more.
    """
    index = _index_before(tokens, caret)
    prefix, span, cursor = '', (caret, caret), index

    if index >= 0 and tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        token = tokens[index]
        typed = token.text[: caret - token.start]
        prefix = _value_of(typed, token)
        span = (token.start, caret)
        cursor = index - 1
    elif index >= 0 and tokens[index].type in _SKIP:
        cursor = index

    segments: list[str] = []
    cursor = _skip_back(tokens, cursor)
    while cursor >= 0 and tokens[cursor].type is TokenType.PUNCT and tokens[cursor].text == '.':
        cursor = _skip_back(tokens, cursor - 1)
        if cursor < 0 or tokens[cursor].type is not TokenType.IDENT:
            break
        segments.append(tokens[cursor].value)
        cursor = _skip_back(tokens, cursor - 1)

    return tuple(reversed(segments)), prefix, span


def clause_at(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    clauses: ClauseModel,
) -> str | None:
    """
    The nearest clause keyword governing the caret.

    Scans back over tokens at the caret's own depth. A subquery that closed
    before the caret sits at a deeper level and is skipped, so
    `SELECT a, (SELECT b FROM t2), <caret>` is still the outer SELECT.

    When the caret's depth holds no clause keyword — `WHERE (a AND <caret>)`,
    `SELECT sum(<caret>` — the search widens to the enclosing depth.
    """
    words = clauses.names()
    if not words:
        return None
    depth = depth_at(tokens, caret)
    while depth >= 0:
        found = _scan_for_clause(tokens, lo, hi, caret, words, depth)
        if found is not None:
            return found
        depth -= 1
    return None


def _scan_for_clause(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    words: tuple[str, ...],
    depth: int,
) -> str | None:
    """
    The clause name ending nearest to the left of `caret` at exactly `depth`.

    Ranked by (end offset, word count), so `DELETE FROM <caret>` answers
    'DELETE FROM' rather than the bare 'FROM' that ends at the same token.
    """
    best: tuple[int, int, str] | None = None
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.IDENT or token.depth != depth or token.end >= caret:
            continue
        for name in words:
            parts = name.split()
            run = _ident_run(tokens, index, hi, len(parts))
            if run is None or [t.value.upper() for t in run] != parts or run[-1].end >= caret:
                continue
            candidate = (run[-1].end, len(parts), name)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
            break
    return best[2] if best is not None else None


def _ident_run(tokens: Sequence[Token], start: int, hi: int, count: int) -> list[Token] | None:
    """`count` consecutive IDENT tokens beginning at `start`, ignoring whitespace and comments."""
    run: list[Token] = []
    index = start
    while index < hi and len(run) < count:
        token = tokens[index]
        if token.type in _SKIP:
            index += 1
            continue
        if token.type is not TokenType.IDENT:
            return None
        run.append(token)
        index += 1
    return run if len(run) == count else None


def _skip_back(tokens: Sequence[Token], index: int) -> int:
    """The nearest index at or before `index` that is not whitespace or a comment."""
    while index >= 0 and tokens[index].type in _SKIP:
        index -= 1
    return index


def _value_of(typed: str, token: Token) -> str:
    """Fold a partially typed identifier the same way the lexer folded the whole one."""
    if token.quoted:
        return typed.lstrip('"`')
    folded = token.value
    return folded[: len(typed)] if len(folded) == len(token.text) else typed.lower()


def scope_of(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> Scope:
    """
    The relations visible at `caret`, built from the whole statement.

    Reading only the text left of the caret cannot work: in `SELECT na<caret>
    FROM users u` the relation that answers the question sits to the right.
    """
    return Scope(relations=tuple(_relations_in(tokens, lo, hi, caret, dialect)))


def _relations_in(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> list[Relation]:
    """Every table reference introduced between `lo` and `hi`."""
    relations: list[Relation] = []
    index = lo
    while index < hi:
        token = tokens[index]
        if token.type in _SKIP:
            index += 1
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect)
        if matched is None or matched[0] not in _RELATION_CLAUSES:
            index += 1
            continue
        index = _read_relation_list(tokens, matched[1], hi, caret, dialect, relations)
    return relations


def _clause_starting_at(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    dialect: Dialect,
) -> tuple[str, int] | None:
    """(clause name, index just past it) when a clause name starts at `index`."""
    for name in dialect.clauses.names():
        parts = name.split()
        run = _ident_run(tokens, index, hi, len(parts))
        if run is not None and [t.value.upper() for t in run] == parts:
            return name, _index_of(tokens, run[-1]) + 1
    return None


def _index_of(tokens: Sequence[Token], token: Token) -> int:
    """The position of `token` in `tokens`, located by its start offset."""
    for index, candidate in enumerate(tokens):
        if candidate.start == token.start:
            return index
    raise ValueError('token not in stream')


def _read_relation_list(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    out: list[Relation],
) -> int:
    """Read comma-separated relation references until the next clause keyword."""
    while index < hi:
        index = _skip_forward(tokens, index, hi)
        if index >= hi or _clause_starting_at(tokens, index, hi, dialect) is not None:
            break
        token = tokens[index]
        if token.type is TokenType.PUNCT and token.text == ',':
            index += 1
            continue
        if token.type is TokenType.IDENT and token.value.upper() in _JOIN_QUALIFIERS:
            index += 1
            continue
        if token.type is not TokenType.IDENT:
            break
        path, index = _read_dotted_path(tokens, index, hi)
        if _covers_caret(path, caret):
            continue
        alias, index = _read_alias(tokens, index, hi, dialect)
        out.append(Relation(alias=alias, path=tuple(t.value for t in path), source='table'))
    return index


def _skip_forward(tokens: Sequence[Token], index: int, hi: int) -> int:
    """The next index at or after `index` that is not whitespace or a comment."""
    while index < hi and tokens[index].type in _SKIP:
        index += 1
    return index


def _read_dotted_path(tokens: Sequence[Token], index: int, hi: int) -> tuple[list[Token], int]:
    """Read `ident (. ident)*` starting at `index`."""
    path = [tokens[index]]
    index += 1
    while True:
        probe = _skip_forward(tokens, index, hi)
        if probe >= hi or tokens[probe].type is not TokenType.PUNCT or tokens[probe].text != '.':
            return path, index
        after = _skip_forward(tokens, probe + 1, hi)
        if after >= hi or tokens[after].type is not TokenType.IDENT:
            return path, index
        path.append(tokens[after])
        index = after + 1


def _read_alias(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    dialect: Dialect,
) -> tuple[str | None, int]:
    """Read an optional alias, with or without AS."""
    probe = _skip_forward(tokens, index, hi)
    if probe >= hi or tokens[probe].type is not TokenType.IDENT:
        return None, index
    if tokens[probe].value.upper() == 'AS':
        probe = _skip_forward(tokens, probe + 1, hi)
        if probe >= hi or tokens[probe].type is not TokenType.IDENT:
            return None, index
        return tokens[probe].value, probe + 1
    word = tokens[probe].value.upper()
    if word in dialect.reserved_upper or _clause_starting_at(tokens, probe, hi, dialect) is not None:
        return None, index
    return tokens[probe].value, probe + 1


def _covers_caret(path: Sequence[Token], caret: int) -> bool:
    """Whether the caret sits inside this reference — a half-typed name is not a relation."""
    return any(token.covers(caret) for token in path)
