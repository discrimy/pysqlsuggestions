"""
Pure analysis over a token stream.

Every function here takes tokens and a caret offset and returns a plain value.
Nothing performs I/O, and nothing knows what a catalog is.
"""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.engine.lex import Token, TokenType

_SKIP = (TokenType.WHITESPACE, TokenType.COMMENT)


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
