"""Statement isolation and the word under the caret."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import depth_at, in_literal, qualifier_and_prefix, statement_at
from pysqlsuggestions.engine.lex import lex
from tests.corpus.cases import split_caret


def at(marked: str) -> tuple[tuple[str, ...], str, tuple[int, int]]:
    """Run qualifier_and_prefix on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return qualifier_and_prefix(lex(sql, POSTGRES.syntax), caret)


def test_bare_prefix() -> None:
    """A half-typed word with no dot."""
    assert at('SELECT na⌶') == ((), 'na', (7, 9))


def test_no_prefix_after_whitespace() -> None:
    """The caret after a space starts a fresh word."""
    assert at('SELECT ⌶') == ((), '', (7, 7))


def test_qualified_empty_prefix() -> None:
    """Immediately after a dot: qualifier known, nothing typed, nothing to replace."""
    assert at('SELECT u.⌶') == (('u',), '', (9, 9))


def test_qualified_with_prefix() -> None:
    """replace_span covers only the part after the dot — the qualifier keeps its place."""
    assert at('SELECT u.em⌶') == (('u',), 'em', (9, 11))


def test_two_segment_qualifier() -> None:
    """schema.table.column is legal in Postgres."""
    assert at('SELECT public.users.i⌶') == (('public', 'users'), 'i', (20, 21))


def test_whitespace_around_the_dot() -> None:
    """`u . id` is legal SQL and must not break qualifier detection."""
    assert at('SELECT u . em⌶') == (('u',), 'em', (11, 13))


def test_quoted_qualifier_and_prefix_keep_their_case() -> None:
    """Quoted identifiers are not folded."""
    assert at('SELECT "My Table".Col⌶') == (('My Table',), 'col', (18, 21))


def test_prefix_is_folded_for_the_dialect() -> None:
    """Postgres folds unquoted words to lower; the corpus compares folded values."""
    assert at('SELECT NA⌶')[1] == 'na'


def test_caret_in_the_middle_of_a_word_replaces_only_what_precedes_it() -> None:
    """replace_span ends at the caret, matching the existing editor behaviour."""
    assert at('SELECT nam⌶e FROM t') == ((), 'nam', (7, 10))


def test_no_prefix_after_an_operator() -> None:
    """`=` is not the start of an identifier."""
    assert at('WHERE a =⌶') == ((), '', (9, 9))


@pytest.mark.parametrize(
    ('marked', 'expected'),
    [
        ("SELECT 'ab⌶", True),
        ("SELECT 'ab'⌶", False),
        ('SELECT a -- note ⌶', True),
        ('SELECT /* x ⌶ */ a', True),
        ('SELECT a⌶', False),
    ],
)
def test_in_literal(marked: str, expected: bool) -> None:
    """A caret inside a string or comment suppresses every suggestion."""
    sql, caret = split_caret(marked)
    assert in_literal(lex(sql, POSTGRES.syntax), caret) is expected


def test_statement_isolation() -> None:
    """Only the statement containing the caret is analysed."""
    sql, caret = split_caret('SELECT * FROM t1; SELECT * FROM t2 WHERE ⌶')
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    texts = [t.text for t in tokens[lo:hi] if not t.text.isspace()]
    assert 't1' not in texts
    assert 't2' in texts


def test_semicolon_inside_parens_does_not_split() -> None:
    """Only depth-0 semicolons end a statement."""
    sql, caret = split_caret("SELECT f('a;b') , ⌶ FROM t")
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    assert [t.text for t in tokens[lo:hi] if t.text == 'FROM'] == ['FROM']


def test_depth_at() -> None:
    """The caret's paren depth drives clause matching."""
    sql, caret = split_caret('SELECT * FROM (SELECT ⌶)')
    assert depth_at(lex(sql, POSTGRES.syntax), caret) == 1
