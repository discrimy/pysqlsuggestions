"""
Statement boundaries, and the offset arithmetic every span depends on.

Scope comes from the whole statement, so handing the engine a document of
several would put every relation in every one of them into scope for all of
them. Everything here is about cutting that document down to one statement
without moving anything the caller then has to move back.
"""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions_lsp.documents import line_starts, statement_at, to_position

SYNTAX = POSTGRES.syntax


def test_a_lone_statement_is_returned_whole() -> None:
    """A document with no terminator is one statement, starting at zero."""
    text = 'SELECT id FROM users'
    assert statement_at(text, 9, SYNTAX) == (text, 0)


def test_the_caret_picks_the_second_of_two() -> None:
    """The statement returned is the one the caret is in, not the first."""
    text = 'SELECT 1;SELECT 2'
    assert statement_at(text, 15, SYNTAX) == ('SELECT 2', 9)


def test_the_terminator_is_not_part_of_the_statement() -> None:
    """The semicolon belongs to neither side."""
    text = 'SELECT 1;SELECT 2'
    assert statement_at(text, 3, SYNTAX) == ('SELECT 1', 0)


def test_a_caret_just_past_a_terminator_belongs_to_what_follows() -> None:
    """Typing at the very start of a new statement completes in that statement."""
    text = 'SELECT 1;SELECT 2'
    assert statement_at(text, 9, SYNTAX) == ('SELECT 2', 9)


def test_whitespace_after_a_terminator_is_kept() -> None:
    """Trimming it would shift every offset the engine hands back."""
    text = 'SELECT 1;\nSELECT 2'
    assert statement_at(text, 17, SYNTAX) == ('\nSELECT 2', 9)


def test_a_semicolon_in_a_string_does_not_end_a_statement() -> None:
    """The character is a boundary; the token is what decides."""
    text = "SELECT ';' AS s FROM users"
    assert statement_at(text, 20, SYNTAX) == (text, 0)


def test_a_semicolon_in_a_comment_does_not_end_a_statement() -> None:
    """A commented-out clause must not truncate the statement around it."""
    text = 'SELECT 1 -- ; not a boundary\nFROM users'
    assert statement_at(text, 33, SYNTAX) == (text, 0)


def test_a_semicolon_in_a_quoted_identifier_does_not_end_a_statement() -> None:
    """Which quotes an identifier takes is the dialect's business, and the lexer's."""
    text = 'SELECT "odd;name" FROM users'
    assert statement_at(text, 22, SYNTAX) == (text, 0)


def test_a_trailing_terminator_leaves_an_empty_statement_after_it() -> None:
    """A caret past the last semicolon is in a new, empty statement."""
    text = 'SELECT 1;'
    assert statement_at(text, 9, SYNTAX) == ('', 9)


def test_an_empty_document_is_an_empty_statement() -> None:
    """The completion request on a fresh file must not be a special case."""
    assert statement_at('', 0, SYNTAX) == ('', 0)


def test_the_caret_within_the_statement_is_recoverable() -> None:
    """The whole reason the base offset is returned at all."""
    text = 'SELECT 1;SELECT na FROM users'
    statement, base = statement_at(text, 18, SYNTAX)
    assert statement[: 18 - base] == 'SELECT na'


def test_line_starts_marks_every_line() -> None:
    """Including the empty one, which has a start like any other."""
    assert line_starts('a\nbb\n\nc').starts == [0, 2, 5, 6]


def test_an_offset_on_the_first_line() -> None:
    """Line zero, character equal to the offset."""
    assert to_position(line_starts('SELECT 1'), 7) == (0, 7)


def test_an_offset_on_a_later_line() -> None:
    """The line start is subtracted, so the character is within the line."""
    assert to_position(line_starts('SELECT 1\nFROM t'), 11) == (1, 2)


def test_an_offset_at_end_of_input() -> None:
    """A caret past a trailing newline sits at the start of the line after it."""
    text = 'SELECT 1\n'
    assert to_position(line_starts(text), len(text)) == (1, 0)


def test_a_character_column_is_counted_in_utf16_units() -> None:
    """
    LSP counts `character` in UTF-16 code units, and the server says so.

    Its `initialize` result advertises `positionEncoding: utf-16`, and 3.17 makes
    that the default regardless. An emoji is one code point and two units, so a
    column reported as code points is short by one for every astral character
    before it — and the edit range built from it lands inside the word, which is
    how `FROM rec` became `FROMrecentc` when the completion was applied.
    """
    text = 'SELECT \U0001f642 x'
    assert to_position(line_starts(text), len(text)) == (0, len(text) + 1)


def test_a_column_is_unaffected_by_a_basic_plane_character() -> None:
    """One code point, one unit: an accent must not be counted twice."""
    text = 'SELECT é x'
    assert to_position(line_starts(text), len(text)) == (0, len(text))


def test_a_lone_carriage_return_starts_a_line() -> None:
    """
    A bare CR ends a line for the client and for pygls, and used not to here.

    `line_starts` counted the newline alone while the inbound half of the same
    request goes through `TextDocument.lines`, which splits on CR too. One
    request, two line models: the caret decoded correctly and the range came
    back naming a line above it, at a column longer than that line.
    """
    assert line_starts('a\rbb\r\nc\nd').starts == [0, 2, 6, 8]
