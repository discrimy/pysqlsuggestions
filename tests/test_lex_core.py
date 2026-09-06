"""Lexer: identifiers, numbers, operators, punctuation, whitespace, paren depth."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import TEMPLATE_PLACEHOLDER, Placeholder, Syntax
from pysqlsuggestions.engine.lex import Token, TokenType, lex, reads_as_one_identifier


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


NAMED = Syntax(placeholders=(Placeholder(opens=':'),))
NUMBERED = Syntax(placeholders=(Placeholder(opens='$', body='digits'),))
BARE = Syntax(placeholders=(Placeholder(opens='?', body='none'),))
BRACED = Syntax(placeholders=(Placeholder(opens='${', body='any', closes='}'),))


def test_a_named_parameter_is_one_token() -> None:
    """`:user_id` is a parameter, not a colon followed by a column name."""
    tokens = significant('WHERE id = :user_id', NAMED)
    assert (tokens[-1].type, tokens[-1].text) == (TokenType.PARAM, ':user_id')


def test_a_named_parameter_is_never_terminated() -> None:
    """Another keystroke could always extend the name, so the caret at its end is inside it."""
    assert significant(':us', NAMED)[0].terminated is False


def test_a_numbered_parameter_takes_only_digits() -> None:
    """`$1` is a parameter; `$x` is not, and falls through to whatever else the syntax says."""
    assert significant('$1', NUMBERED)[0].type is TokenType.PARAM
    assert significant('$x', NUMBERED)[0].type is not TokenType.PARAM


def test_a_bare_parameter_terminates_itself() -> None:
    """`?` admits nothing more, so a caret at its end is past it, not in it."""
    token = significant('?', BARE)[0]
    assert (token.type, token.terminated) == (TokenType.PARAM, True)


def test_a_braced_parameter_runs_to_its_close() -> None:
    """`${var}` is one token whose interior may hold anything."""
    token = significant('${my var}', BRACED)[0]
    assert (token.type, token.text, token.terminated) == (TokenType.PARAM, '${my var}', True)


def test_an_unclosed_braced_parameter_runs_to_end_of_input() -> None:
    """The lexer never raises; an unterminated token reaches the end with terminated false."""
    token = significant('${re', BRACED)[0]
    assert (token.type, token.text, token.terminated) == (TokenType.PARAM, '${re', False)


def test_the_longest_opener_wins() -> None:
    """`${` is tried before `$`, or the brace form would lex as a numbered one that failed."""
    syntax = Syntax(placeholders=(Placeholder(opens='$', body='digits'), TEMPLATE_PLACEHOLDER))
    assert significant('${region}', syntax)[0].text == '${region}'


def test_a_placeholder_inside_a_literal_is_text() -> None:
    """Delimiters are read first, so a colon inside a string stays inside it."""
    assert [t.type for t in significant("':user_id'", NAMED)] == [TokenType.STRING]


def test_a_dialect_declaring_none_lexes_as_it_always_did() -> None:
    """The default is an empty tuple, and it must change nothing."""
    assert [t.type for t in significant(':us')] == [TokenType.OPERATOR, TokenType.IDENT]


def test_reading_a_name_back_is_memoised_within_a_bound() -> None:
    """
    The hot path asks this about the same catalog names on every keystroke.

    `rank` calls it once per candidate to decide whether a name survives
    unquoted, and it walks the string a character at a time. On a 5000-relation
    schema that is 5000 walks over names that have not changed since the last
    keystroke — 45% of the whole ranking cost, measured, and the reason a warm
    cache still spent 23ms per completion with no I/O at all.

    Bounded rather than unbounded, which is the half that can actually go wrong.
    The names come from a catalog and so are bounded in principle, but this is
    also asked about whatever the user has typed, and `lsp/` keeps one process
    alive for a working day. 0.10.0 spent a release putting a bound on
    `MemoryCache` for that exact reason; an unbounded memo here would reintroduce
    it one module lower down.
    """
    memo = reads_as_one_identifier.cache_info()
    assert memo.maxsize is not None, 'an unbounded memo is a slow leak in a long session'

    for index in range(memo.maxsize * 2):
        reads_as_one_identifier(f'name_{index}')

    assert reads_as_one_identifier.cache_info().currsize <= memo.maxsize


def test_memoising_does_not_change_what_reads_as_one_identifier_answers() -> None:
    """The names the quoting decision turns on, asked twice so a memo has to agree with itself."""
    names = ['orders', '_leading', 'a$b', 'café', 'total\xa0due', 'a\u200bb', '', ' ', '9lives', 'a\nb']
    first = [reads_as_one_identifier(name) for name in names]
    assert first == [reads_as_one_identifier(name) for name in names]
    assert first == [True, True, True, True, False, False, False, False, False, False]
