"""Lexer: identifiers, numbers, operators, punctuation, whitespace, paren depth."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Syntax
from pysqlsuggestions.engine.lex import Token, TokenType, lex


def significant(src: str, syntax: Syntax | None = None) -> list[Token]:
    """Tokens with whitespace dropped — the shape every analysis function works with."""
    return [t for t in lex(src, syntax or Syntax()) if t.type is not TokenType.WHITESPACE]


def test_spans_cover_the_source_exactly() -> None:
    """Every character belongs to exactly one token, in order. replace_span depends on this."""
    src = 'SELECT a, b FROM t'
    tokens = lex(src, Syntax())
    assert ''.join(t.text for t in tokens) == src
    assert [t.start for t in tokens] == [0] + [t.end for t in tokens][:-1]


def test_identifiers_and_punctuation() -> None:
    """A dotted reference is three tokens, not one."""
    tokens = significant('users.id')
    assert [(t.type, t.value) for t in tokens] == [
        (TokenType.IDENT, 'users'),
        (TokenType.PUNCT, '.'),
        (TokenType.IDENT, 'id'),
    ]


def test_unquoted_identifiers_fold_to_lower_by_default() -> None:
    """value is folded; text keeps the source slice so offsets stay exact."""
    token = significant('SELECT')[0]
    assert token.value == 'select'
    assert token.text == 'SELECT'


def test_quoted_identifiers_preserve_case_and_strip_quotes() -> None:
    """A quoted identifier is one token whose value is the unquoted content."""
    token = significant('"Mixed Case"')[0]
    assert token.type is TokenType.IDENT
    assert token.value == 'Mixed Case'
    assert token.quoted is True
    assert token.text == '"Mixed Case"'


def test_doubled_quote_is_an_escape() -> None:
    """'"a""b"' is one identifier containing a quote character."""
    token = significant('"a""b"')[0]
    assert token.value == 'a"b'


def test_numbers() -> None:
    """Integers, decimals and exponents are single NUMBER tokens."""
    assert [t.value for t in significant('1 1.5 1e10 2.5E-3')] == ['1', '1.5', '1e10', '2.5E-3']


def test_multi_character_operators_win() -> None:
    """<= is one token, not < followed by =."""
    assert [t.text for t in significant('a <= b <> c || d')] == ['a', '<=', 'b', '<>', 'c', '||', 'd']


def test_cast_operator_is_lexed_when_the_dialect_has_one() -> None:
    """:: is an operator for Postgres-like dialects and two unknowns for strict ANSI."""
    with_cast = significant('a::int', Syntax(cast_operator='::'))
    assert [t.text for t in with_cast] == ['a', '::', 'int']
    assert with_cast[1].type is TokenType.OPERATOR

    without = significant('a::int', Syntax(cast_operator=None))
    assert [t.text for t in without] == ['a', ':', ':', 'int']


def test_depth_is_precomputed() -> None:
    """Parens carry the outer depth; their contents carry the inner one."""
    tokens = significant('SELECT (a + b) FROM t')
    assert [(t.text, t.depth) for t in tokens] == [
        ('SELECT', 0),
        ('(', 0),
        ('a', 1),
        ('+', 1),
        ('b', 1),
        (')', 0),
        ('FROM', 0),
        ('t', 0),
    ]


def test_unbalanced_close_paren_does_not_go_negative() -> None:
    """Completion runs on broken input by definition; depth must stay sane."""
    assert [t.depth for t in significant('a) b')] == [0, 0, 0]


def test_empty_source() -> None:
    """Lexing nothing yields nothing."""
    assert lex('', Syntax()) == ()
