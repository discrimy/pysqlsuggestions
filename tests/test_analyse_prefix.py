"""Statement isolation and the word under the caret."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import (
    at_the_clause_start,
    clause_at,
    depth_at,
    in_literal,
    opens_a_name_list,
    qualifier_and_prefix,
    statement_at,
)
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


def test_a_finished_quoted_name_is_replaced_whole() -> None:
    """
    A quoted identifier is one token; half of it is not a valid identifier.

    Ending the span at the caret would leave the closing quote stranded after
    the replacement, so the whole token goes — the only span that can produce
    balanced quotes.
    """
    assert at('SELECT * FROM "auth_u⌶ser"') == ((), 'auth_u', (14, 25))
    assert at('SELECT * FROM "auth_user"⌶') == ((), 'auth_user', (14, 25))


def test_a_closing_quote_is_not_part_of_the_prefix() -> None:
    """`"Period"` has been typed in full; the prefix is what was quoted, not the quotes."""
    assert at('SELECT m."Period"⌶')[1] == 'Period'


def test_a_doubled_quote_is_unescaped_in_the_prefix() -> None:
    """The lexer collapses `""` when reading the value; matching must see the same text."""
    assert at('SELECT "has""q⌶')[1] == 'has"q'


def test_an_unfinished_quoted_name_still_ends_at_the_caret() -> None:
    """With no closing quote there is nothing to strand, and the text right of the caret is not ours."""
    assert at('SELECT * FROM "auth_u⌶') == ((), 'auth_u', (14, 21))


def _at_start(marked: str, clause: str) -> bool:
    """Run at_the_clause_start on ⌶-marked SQL, for the postgres dialect."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    return at_the_clause_start(tokens, caret, clause)


def test_a_clause_that_does_not_begin_the_statement_still_has_a_start() -> None:
    """
    `_words_before` walks back through consecutive identifiers without stopping.

    So the run before `GROUP BY rol` was ('USERS', 'GROUP', 'BY'), which equals
    no clause name, and `before_the_item` was dead for every clause but the
    leading one. DISTINCT worked only because SELECT comes first.
    """
    assert _at_start('SELECT * FROM users GROUP BY rol⌶', 'GROUP BY')
    assert _at_start('SELECT * FROM users LIMIT al⌶', 'LIMIT')


def test_a_written_item_still_ends_the_clause_start() -> None:
    """The guard the equality check was providing, which the suffix check must keep."""
    assert not _at_start('SELECT id, dis⌶', 'SELECT')
    assert not _at_start('SELECT * FROM users GROUP BY id, rol⌶', 'GROUP BY')


def test_the_leading_clause_is_unchanged() -> None:
    """`SELECT dis` was the one case that worked, and it must go on working."""
    assert _at_start('SELECT dis⌶', 'SELECT')
    assert not _at_start('SELECT * FROM users ⌶', 'FROM')


def _name_list(marked: str) -> bool:
    """Run opens_a_name_list on ⌶-marked SQL, for the postgres dialect."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    clause = clause_at(tokens, lo, hi, caret, POSTGRES.clauses)
    return opens_a_name_list(tokens, caret, clause, POSTGRES.clauses)


@pytest.mark.parametrize(
    'marked',
    [
        'WITH x (⌶',
        'SELECT * FROM users AS u (⌶',
        'SELECT * FROM generate_series(1, 2) AS t (⌶',
        'SELECT * FROM generate_series(1, 2) AS (⌶',
    ],
)
def test_a_paren_that_opens_a_list_of_names(marked: str) -> None:
    """
    Four shapes where the author is inventing names and the catalog has nothing to say.

    Every one of them offered relations or the CTE body words before this
    existed, which is SQL the server refuses rather than a suggestion missing.
    """
    assert _name_list(marked)


@pytest.mark.parametrize(
    'marked',
    [
        # A group the clause itself declares — the alias word introduces it.
        'WITH x AS (⌶',
        'SELECT * FROM users WINDOW w AS (⌶',
        # Calls. `FROM f(` has an identifier left of the paren too, which is why
        # the rule is keyed on the clause and not on that shape alone.
        'SELECT * FROM generate_series(⌶',
        'SELECT count(⌶',
        'SELECT * FROM users TABLESAMPLE BERNOULLI (⌶',
        # Positions that answer well today and a broader rule would silence.
        'INSERT INTO users (⌶',
        'SELECT * FROM users u JOIN orders o USING (⌶',
        'SELECT * FROM users WHERE id IN (⌶',
        'SELECT * FROM users GROUP BY ROLLUP (⌶',
        'SELECT DISTINCT ON (⌶',
        # Ordinary grouping and subqueries.
        'SELECT * FROM (⌶',
        'SELECT * FROM users WHERE (⌶',
        'SELECT * FROM users GROUP BY (⌶',
        'SELECT (⌶',
        'SELECT * FROM users WHERE id = (⌶',
    ],
)
def test_every_other_paren_still_answers(marked: str) -> None:
    """
    The fifteen negatives, which carry more weight than the four positives.

    Four of these answer usefully today — INSERT's column list, a function's
    arguments, USING's join columns, IN's values — so a rule wide enough to
    catch the positives by shape alone would cost more than it gives.
    """
    assert not _name_list(marked)
