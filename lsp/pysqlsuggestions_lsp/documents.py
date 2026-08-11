"""
Where one statement ends and the next begins, and where an offset sits.

`derive_request` builds scope from the whole statement — the FROM answering a
caret in the SELECT list is to the *right* of it — so a document of several
statements must be cut to the one holding the caret. Handing over the whole
document would put every relation in every statement into scope for all of them.

Splitting on the `;` character is wrong: it occurs inside string literals,
comments and quoted identifiers, and which delimiters those are is a property of
the dialect. So this splits on semicolon *tokens*. That is the one place this
package reaches into the engine's internals rather than its API, and it is worth
it: a hand-rolled splitter would be a second, untested, dialect-unaware lexer
whose disagreements with the real one surface as scope silently missing from a
completion list.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

from pysqlsuggestions.dialects.base import Syntax
from pysqlsuggestions.engine.lex import TokenType, lex


def statement_at(text: str, offset: int, syntax: Syntax) -> tuple[str, int]:
    """
    The statement containing `offset`, and where it starts in `text`.

    The caret within the returned statement is `offset - start`. The terminating
    semicolon belongs to neither side, and leading whitespace is kept: trimming
    it would shift every span the engine hands back by an amount the caller
    would have to remember to undo.
    """
    start = 0
    for token in lex(text, syntax):
        if token.type is not TokenType.PUNCT or token.text != ';':
            continue
        if offset <= token.start:
            return text[start : token.start], start
        start = token.end
    return text[start:], start


def line_starts(text: str) -> list[int]:
    """The offset of each line's first character, `[0]` for text with no newline."""
    starts = [0]
    for index, character in enumerate(text):
        if character == '\n':
            starts.append(index + 1)
    return starts


def to_position(starts: Sequence[int], offset: int) -> tuple[int, int]:
    """
    Zero-based `(line, character)` for `offset`, given `line_starts(text)`.

    LSP speaks line and character; every span in this library is an offset. The
    line starts are computed once per request and shared, because a document of
    any size would otherwise be rescanned for each of forty suggestions.
    """
    line = bisect_right(starts, offset) - 1
    return line, offset - starts[line]
