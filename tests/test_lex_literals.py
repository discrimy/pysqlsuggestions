"""Lexer: string literals, comments, dollar quoting, and tolerance of unterminated input."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Syntax
from pysqlsuggestions.engine.lex import TokenType, lex

PG = Syntax(dollar_quoting=True, nested_block_comments=True, cast_operator='::')
CH = Syntax(line_comments=('--', '#'), string_escape_backslash=True)


def only(src: str, syntax: Syntax) -> list[tuple[TokenType, str, bool]]:
    """(type, text, terminated) for every non-whitespace token."""
    return [(t.type, t.text, t.terminated) for t in lex(src, syntax) if t.type is not TokenType.WHITESPACE]


def test_string_literal_is_one_token() -> None:
    """Contents never leak into parsing — LIKE '%smith%' must not produce operators."""
    assert only("LIKE '%smith%'", Syntax()) == [
        (TokenType.IDENT, 'LIKE', True),
        (TokenType.STRING, "'%smith%'", True),
    ]


def test_doubled_quote_inside_a_string() -> None:
    """'it''s' is a single literal."""
    assert only("'it''s'", Syntax()) == [(TokenType.STRING, "'it''s'", True)]


def test_unterminated_string_runs_to_end_of_input() -> None:
    """The caret sitting inside an open literal is the case that must not crash."""
    tokens = only("WHERE name = 'ab", Syntax())
    assert tokens[-1] == (TokenType.STRING, "'ab", False)


def test_backslash_escape_only_when_the_dialect_says_so() -> None:
    r"""ClickHouse honours \'; Postgres with standard_conforming_strings does not."""
    assert only(r"'a\'b'", CH) == [(TokenType.STRING, r"'a\'b'", True)]
    postgres = only(r"'a\'b'", PG)
    assert postgres[0] == (TokenType.STRING, r"'a\'", True)


def test_line_comment() -> None:
    """A line comment ends at the newline, which stays whitespace."""
    assert only('-- hi\nSELECT', Syntax()) == [
        (TokenType.COMMENT, '-- hi', True),
        (TokenType.IDENT, 'SELECT', True),
    ]


def test_clickhouse_hash_comment() -> None:
    """ClickHouse adds # as a line comment marker; other dialects treat it as an operator."""
    assert only('# hi\nSELECT', CH)[0][0] is TokenType.COMMENT
    assert only('# hi\nSELECT', Syntax())[0][0] is TokenType.OPERATOR


def test_block_comment() -> None:
    """/* */ spans newlines."""
    assert only('/* a\nb */ SELECT', Syntax())[0] == (TokenType.COMMENT, '/* a\nb */', True)


def test_nested_block_comments_only_where_supported() -> None:
    """Postgres nests; ANSI stops at the first close."""
    src = '/* a /* b */ c */ SELECT'
    assert only(src, PG)[0] == (TokenType.COMMENT, '/* a /* b */ c */', True)
    assert only(src, Syntax())[0] == (TokenType.COMMENT, '/* a /* b */', True)


def test_unterminated_block_comment() -> None:
    """Runs to end of input rather than raising."""
    assert only('/* open', Syntax()) == [(TokenType.COMMENT, '/* open', False)]


def test_dollar_quoting() -> None:
    """$$ and $tag$ bodies are opaque string tokens where the dialect allows them."""
    assert only('$$ any ' + "'" + ' text $$', PG)[0][0] is TokenType.STRING
    assert only('$fn$ body $fn$', PG) == [(TokenType.STRING, '$fn$ body $fn$', True)]
    assert only('$fn$ body', PG) == [(TokenType.STRING, '$fn$ body', False)]


def test_dollar_quoting_off_by_default() -> None:
    """Without the flag, $ is not a literal delimiter."""
    assert only('$$ x $$', Syntax())[0][0] is not TokenType.STRING


def test_unterminated_quoted_identifier() -> None:
    """Same tolerance as strings."""
    assert only('SELECT "unclosed', Syntax())[-1] == (TokenType.IDENT, '"unclosed', False)


def test_depth_ignores_parens_inside_literals() -> None:
    """A paren in a string must not shift depth for the rest of the statement."""
    tokens = [t for t in lex("SELECT '(' , a FROM t", Syntax()) if t.type is not TokenType.WHITESPACE]
    assert all(t.depth == 0 for t in tokens)
