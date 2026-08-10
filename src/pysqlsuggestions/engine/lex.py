"""
A tolerant, dialect-driven tokenizer.

This is the only module that reads raw source text. It never raises: an
unterminated string, quote or comment yields a token running to end of input
with `terminated` false, because completion works on invalid input by
definition.

It deliberately does not classify keywords. Every word is an IDENT; analyse
consults the dialect's vocabulary. That keeps this module dependent on dialect
*syntax* only.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum

from pysqlsuggestions.dialects.base import Syntax

_OPERATOR_CHARS = frozenset('+-*/%=<>|&^~!@#?:')
_MULTI_CHAR_OPERATORS: tuple[str, ...] = ('<=', '>=', '<>', '!=', '||', '->>', '->', '#>>', '#>')
_PUNCTUATION = frozenset('.,();[]')


class TokenType(Enum):
    """The token categories analyse distinguishes."""

    IDENT = 'ident'
    NUMBER = 'number'
    STRING = 'string'
    COMMENT = 'comment'
    OPERATOR = 'operator'
    PUNCT = 'punct'
    WHITESPACE = 'whitespace'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class Token:
    """One lexical unit, located in the source."""

    type: TokenType
    start: int
    end: int
    text: str
    """The raw source slice. sum(len(text)) equals len(src)."""
    value: str = ''
    """For IDENT: unquoted and case-folded. For others: the raw text."""
    quoted: bool = False
    terminated: bool = True
    """False when the token ran to end of input looking for its closing delimiter."""
    depth: int = 0
    """Paren nesting. An open paren carries the outer depth, its contents the inner."""

    def covers(self, caret: int) -> bool:
        """Whether `caret` sits inside this token (exclusive of the very start)."""
        return self.start < caret <= self.end


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == '_'


def _is_ident_char(ch: str) -> bool:
    # A combining mark is not alphanumeric but belongs to the letter before it:
    # `café` typed on macOS arrives as `cafe` plus U+0301, and splitting there
    # loses the half of the name the user has typed.
    return ch.isalnum() or ch in '_$' or unicodedata.combining(ch) != 0


def _fold(value: str, syntax: Syntax) -> str:
    if syntax.unquoted_case == 'lower':
        return value.lower()
    if syntax.unquoted_case == 'upper':
        return value.upper()
    return value


def _scan_quoted_ident(src: str, pos: int, quote: str) -> tuple[int, str, bool]:
    """Scan from the opening quote. Returns (end, unquoted value, terminated)."""
    i, out = pos + 1, []
    while i < len(src):
        if src[i] == quote:
            if i + 1 < len(src) and src[i + 1] == quote:
                out.append(quote)
                i += 2
                continue
            return i + 1, ''.join(out), True
        out.append(src[i])
        i += 1
    return len(src), ''.join(out), False


def _scan_number(src: str, pos: int) -> int:
    i = pos
    while i < len(src) and (src[i].isdigit() or src[i] == '.'):
        i += 1
    if i < len(src) and src[i] in 'eE':
        j = i + 1
        if j < len(src) and src[j] in '+-':
            j += 1
        if j < len(src) and src[j].isdigit():
            i = j
            while i < len(src) and src[i].isdigit():
                i += 1
    return i


def _match_operator(src: str, pos: int, syntax: Syntax) -> str | None:
    """The longest operator starting at `pos`, or None."""
    candidates = _MULTI_CHAR_OPERATORS
    if syntax.cast_operator:
        candidates = (syntax.cast_operator, *candidates)
    for op in sorted(candidates, key=len, reverse=True):
        if src.startswith(op, pos):
            return op
    return src[pos] if src[pos] in _OPERATOR_CHARS else None


def _scan_string(src: str, pos: int, syntax: Syntax, *, escapes: bool = False) -> tuple[int, bool]:
    """
    Scan a single-quoted literal from its opening quote. Returns (end, terminated).

    `escapes` forces backslash handling on for a prefixed literal, whatever the
    dialect's default: Postgres processes escapes inside `E'...'` however
    `standard_conforming_strings` is set.
    """
    i = pos + 1
    while i < len(src):
        ch = src[i]
        if ch == '\\' and (escapes or syntax.string_escape_backslash):
            i += 2
            continue
        if ch == "'":
            if i + 1 < len(src) and src[i + 1] == "'":
                i += 2
                continue
            return i + 1, True
        i += 1
    return len(src), False


def _scan_line_comment(src: str, pos: int) -> tuple[int, bool]:
    """Scan to the newline. Returns (end, terminated); a comment reaching EOF is unterminated."""
    end = src.find('\n', pos)
    return (len(src), False) if end == -1 else (end, True)


def _scan_block_comment(src: str, pos: int, syntax: Syntax) -> tuple[int, bool]:
    """Scan from '/*'. Returns (end, terminated), honouring nesting when supported."""
    i, level = pos + 2, 1
    while i < len(src):
        if syntax.nested_block_comments and src.startswith('/*', i):
            level += 1
            i += 2
            continue
        if src.startswith('*/', i):
            level -= 1
            i += 2
            if level == 0:
                return i, True
            continue
        i += 1
    return len(src), False


def _scan_dollar_quote(src: str, pos: int) -> tuple[int, bool] | None:
    """Scan a $tag$...$tag$ literal. Returns None when `pos` does not open one."""
    close = src.find('$', pos + 1)
    if close == -1:
        return None
    tag = src[pos : close + 1]
    if not all(_is_ident_char(c) for c in tag[1:-1]):
        return None
    end = src.find(tag, close + 1)
    return (len(src), False) if end == -1 else (end + len(tag), True)


def _opens_escape_string(src: str, pos: int, syntax: Syntax) -> bool:
    r"""Whether an escape-string prefix sits at `pos`, as in Postgres `E'a\nb'`."""
    prefix = syntax.escape_string_prefix
    return bool(prefix) and src[pos].upper() == prefix and src.startswith("'", pos + 1)


def lex(src: str, syntax: Syntax) -> tuple[Token, ...]:
    """Tokenize `src`. Total, never raises, and preserves every offset."""
    tokens: list[Token] = []
    pos, depth, length = 0, 0, len(src)

    while pos < length:
        ch = src[pos]

        if ch.isspace():
            end = pos
            while end < length and src[end].isspace():
                end += 1
            tokens.append(Token(TokenType.WHITESPACE, pos, end, src[pos:end], src[pos:end], depth=depth))
            pos = end
            continue

        # Delimiter handling comes first: '--' and '#' are otherwise operator
        # characters, and a literal's contents must never reach the scanner below.
        comment_marker = next((m for m in syntax.line_comments if src.startswith(m, pos)), None)
        if comment_marker is not None:
            end, terminated = _scan_line_comment(src, pos)
            tokens.append(
                Token(TokenType.COMMENT, pos, end, src[pos:end], src[pos:end], terminated=terminated, depth=depth),
            )
            pos = end
            continue

        if src.startswith('/*', pos):
            end, terminated = _scan_block_comment(src, pos, syntax)
            tokens.append(
                Token(TokenType.COMMENT, pos, end, src[pos:end], src[pos:end], terminated=terminated, depth=depth),
            )
            pos = end
            continue

        if ch == "'" or _opens_escape_string(src, pos, syntax):
            start = pos
            escaped = ch != "'"
            end, terminated = _scan_string(src, start + (1 if escaped else 0), syntax, escapes=escaped)
            tokens.append(
                Token(TokenType.STRING, start, end, src[start:end], src[start:end], terminated=terminated, depth=depth),
            )
            pos = end
            continue

        if ch == '$' and syntax.dollar_quoting:
            scanned = _scan_dollar_quote(src, pos)
            if scanned is not None:
                end, terminated = scanned
                tokens.append(
                    Token(TokenType.STRING, pos, end, src[pos:end], src[pos:end], terminated=terminated, depth=depth),
                )
                pos = end
                continue

        if ch in syntax.identifier_quotes:
            end, value, terminated = _scan_quoted_ident(src, pos, ch)
            tokens.append(
                Token(TokenType.IDENT, pos, end, src[pos:end], value, quoted=True, terminated=terminated, depth=depth),
            )
            pos = end
            continue

        if _is_ident_start(ch):
            end = pos
            while end < length and _is_ident_char(src[end]):
                end += 1
            raw = src[pos:end]
            tokens.append(Token(TokenType.IDENT, pos, end, raw, _fold(raw, syntax), depth=depth))
            pos = end
            continue

        if ch.isdigit():
            end = _scan_number(src, pos)
            tokens.append(Token(TokenType.NUMBER, pos, end, src[pos:end], src[pos:end], depth=depth))
            pos = end
            continue

        if ch in _PUNCTUATION:
            if ch == '(':
                tokens.append(Token(TokenType.PUNCT, pos, pos + 1, ch, ch, depth=depth))
                depth += 1
            elif ch == ')':
                depth = max(0, depth - 1)
                tokens.append(Token(TokenType.PUNCT, pos, pos + 1, ch, ch, depth=depth))
            else:
                tokens.append(Token(TokenType.PUNCT, pos, pos + 1, ch, ch, depth=depth))
            pos += 1
            continue

        operator = _match_operator(src, pos, syntax)
        if operator is not None:
            end = pos + len(operator)
            tokens.append(Token(TokenType.OPERATOR, pos, end, operator, operator, depth=depth))
            pos = end
            continue

        tokens.append(Token(TokenType.UNKNOWN, pos, pos + 1, ch, ch, depth=depth))
        pos += 1

    return tuple(tokens)
