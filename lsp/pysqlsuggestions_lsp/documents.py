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
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class Lines:
    """
    Where each line of a document begins, and the text those offsets index.

    The two travel together because a position is not an offset twice over. The
    line needs the starts; the `character` needs the text, since LSP counts it in
    UTF-16 code units rather than in code points, and nothing but the characters
    themselves says how many units a stretch of them is.
    """

    text: str
    starts: list[int]


def line_starts(text: str) -> Lines:
    """
    Where each line begins, `[0]` for text with no break in it.

    LSP's line terminators are LF, CRLF and a lone CR. Counting only LF put this
    at odds with the inbound half of the same request, which reaches the document
    through pygls's `TextDocument.lines` — `str.splitlines`, which does break on
    a bare CR. One request cannot hold two ideas of where a line starts: the
    caret decoded against one of them and the range came back against the other,
    naming a line above the caret at a column longer than that line is.
    """
    starts = [0]
    index = 0
    while index < len(text):
        character = text[index]
        if character == '\n':
            starts.append(index + 1)
        elif character == '\r':
            # CRLF is one terminator, not two: consume the LF here so it does
            # not open a second, empty line between the two halves of a pair.
            index += text.startswith('\n', index + 1)
            starts.append(index + 1)
        index += 1
    return Lines(text=text, starts=starts)


def to_position(lines: Lines, offset: int) -> tuple[int, int]:
    """
    Zero-based `(line, character)` for `offset`, given `line_starts(text)`.

    LSP speaks line and character; every span in this library is an offset. The
    line starts are computed once per request and shared, because a document of
    any size would otherwise be rescanned for each of forty suggestions.

    `character` is a count of UTF-16 code units, which is what the protocol means
    by it and what this server advertises in its `initialize` result. Every code
    point outside the basic plane — an emoji, `𝐀`, most CJK extensions — is two
    of them, so a column measured in code points is short by one per astral
    character on that line, and an editor applying the resulting edit splices
    inside a word rather than over it.
    """
    line = bisect_right(lines.starts, offset) - 1
    start = lines.starts[line]
    # Counted rather than encoded: `len(prefix.encode('utf-16-le')) // 2` says the
    # same thing and allocates a copy of the line for every suggestion in the list.
    prefix = lines.text[start:offset]
    return line, len(prefix) + sum(1 for character in prefix if ord(character) > 0xFFFF)
