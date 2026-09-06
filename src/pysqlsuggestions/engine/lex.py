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
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from pysqlsuggestions.dialects.base import Placeholder, Syntax

_OPERATOR_CHARS = frozenset('+-*/%=<>|&^~!@#?:')
_MULTI_CHAR_OPERATORS: tuple[str, ...] = ('<=', '>=', '<>', '!=', '||', '->>', '->', '#>>', '#>')
_PUNCTUATION = frozenset('.,();[]')


class TokenType(Enum):
    """The token categories analyse distinguishes."""

    IDENT = 'ident'
    NUMBER = 'number'
    STRING = 'string'
    PARAM = 'param'
    """A bound parameter: `:name`, `$1`, `?`, `${var}`. Never an identifier, whatever it spells."""
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


_NAME_MEMO = 1 << 16
"""
How many names the quoting decision remembers having read back.

Entries are a short string against a bool, so this is a few megabytes at worst —
a different order of thing from `MemoryCache`, whose 1024 entries are column
lists. The number is chosen to cover a large schema's names comfortably: the
memo only earns anything when this keystroke ranks names the last one also
ranked, and a 5000-relation catalog offers about 5000 of them at a FROM caret.

Bounded rather than unbounded because the argument is not always a catalog name.
Whatever the user has typed reaches here too, and `lsp/` keeps one process alive
for a working day.
"""


@lru_cache(maxsize=_NAME_MEMO)
def reads_as_one_identifier(text: str) -> bool:
    """
    Whether this scanner would read `text`, written bare, back as a single name.

    The other half of the quoting decision, and the half that was missing.
    `Syntax` says what the *server* accepts unquoted; this says what the engine
    can parse back. Postgres accepts far more than is read here — its scanner is
    byte-based, so nearly everything above ASCII goes bare — and a name left
    unquoted on the server's authority alone came back as two identifiers, after
    which every completion in that statement worked from the wrong prefix.

    Exposed rather than duplicated in `rank`, because a second copy of these
    predicates is a second thing to keep in step with the scan below.

    Memoised because `rank` asks it once per candidate and it walks the string a
    character at a time: on a 5000-relation schema that is 5000 walks per
    keystroke over names that did not change, and it measured as 45% of the
    ranking cost — the largest single reason a warm cache still spent 23ms on a
    completion that did no I/O at all.

    Safe to memoise because the answer depends on `text` and nothing else. That
    is worth stating, because the neighbouring quoting predicates take a
    `Dialect` and memoising *those* on the dialect costs more than it saves:
    `Dialect` is a frozen dataclass over large frozensets, and hashing one to
    look up a cached bool is more work than recomputing the bool.
    """
    return bool(text) and _is_ident_start(text[0]) and all(_is_ident_char(ch) for ch in text[1:])


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


def _scan_body(src: str, pos: int, body: str) -> int:
    """The end of a `name` or `digits` run starting at `pos`, or `pos` when there is none."""
    i = pos
    if body == 'digits':
        while i < len(src) and src[i].isdigit():
            i += 1
        return i
    if pos >= len(src) or not _is_ident_start(src[pos]):
        return pos
    i = pos + 1
    while i < len(src) and _is_ident_char(src[i]):
        i += 1
    return i


def _placeholder_openers(syntax: Syntax) -> tuple[Placeholder, ...]:
    """
    The declared placeholders that can actually begin one.

    An empty `opens` matches at every position and consumes nothing, so the
    scanner emitted a zero-width token and left `pos` where it was — a total
    function that never returns, which is worse than one that raises. Dropped
    here rather than guarded at the call site because a placeholder that opens
    with nothing is not a placeholder; `DialectConformance.structure` reports it.
    """
    return tuple(placeholder for placeholder in syntax.placeholders if placeholder.opens)


def _scan_placeholder(src: str, pos: int, syntax: Syntax) -> tuple[int, bool] | None:
    """
    Scan a bound parameter at `pos`. Returns (end, terminated), or None for no match.

    Longest `opens` first, so `${` is tried before `$` and the brace form is not
    read as a numbered one that failed.

    `terminated` says whether anything more could extend this token, which is
    what decides whether a caret at its end is inside it. `?` is finished as
    written and `${var}` once its brace arrives; a name or a digit run never is,
    because the next keystroke could always be another character of it. That is
    the difference between `= ?<caret>`, which wants a connective, and
    `= :us<caret>`, which wants nothing at all.
    """
    for placeholder in sorted(_placeholder_openers(syntax), key=_opener_length, reverse=True):
        if not src.startswith(placeholder.opens, pos):
            continue
        start = pos + len(placeholder.opens)
        if placeholder.closes:
            end = src.find(placeholder.closes, start)
            return (len(src), False) if end == -1 else (end + len(placeholder.closes), True)
        if placeholder.body == 'none':
            return start, True
        if placeholder.body == 'any':
            continue  # no end without `closes`; DialectConformance reports the declaration
        end = _scan_body(src, start, placeholder.body)
        if end > start:
            return end, False
    return None


def _opener_length(placeholder: Placeholder) -> int:
    """How long this spelling's opener is. Sort key, named so the sort reads as one."""
    return len(placeholder.opens)


class Tokens(tuple):  # type: ignore[type-arg]
    """
    A scanned statement, carrying room for what gets derived from it.

    A tuple, so every existing caller is unaffected — and a subclass of one, so
    it can hold a memo. The analysis above `lex` asks the same question of the
    same tokens over and over: the scope walk descends a level at a time and each
    level rescans ranges the level above already scanned, which for a query of
    nested derived tables meant 235,245 clause lookups answering 324 distinct
    questions.

    The memo lives here rather than in a module-level cache because that is what
    makes its lifetime right. A token stream is derived from one text and is
    immutable, so an entry can never go stale; a new keystroke produces new
    tokens and, with them, an empty memo. Nothing has to decide when to clear it,
    nothing is shared between threads, and a slice — which is an ordinary tuple —
    simply has none.
    """

    memo: dict[object, object]
    """
    Answers already derived from these tokens.

    No `__slots__`: a tuple subclass cannot have a non-empty one, so the instance
    carries a `__dict__` — one per scan, which is the granularity this is for.
    """

    def __new__(cls, tokens: Iterable[Token]) -> Tokens:
        """A token stream with an empty memo."""
        found = super().__new__(cls, tokens)
        found.memo = {}
        return found


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

        # Above dollar quoting so `$1` is reliable: `_scan_dollar_quote` reads
        # `$1$2` as a tag it never finds again and swallows the rest of the
        # statement. Costs dollar quoting nothing — `$$body$$` and `$tag$b$tag$`
        # both fail the digits body at their second character.
        scanned = _scan_placeholder(src, pos, syntax)
        if scanned is not None:
            end, terminated = scanned
            tokens.append(
                Token(TokenType.PARAM, pos, end, src[pos:end], src[pos:end], terminated=terminated, depth=depth),
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

    return Tokens(tokens)
