"""Clause detection: scan back to the nearest clause keyword at the caret's depth."""

from __future__ import annotations

import pytest

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import clause_at, statement_at
from pysqlsuggestions.engine.lex import lex
from tests.corpus.cases import split_caret


def clause(marked: str) -> str | None:
    """Run clause_at on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    return clause_at(tokens, lo, hi, caret, POSTGRES.clauses)


@pytest.mark.parametrize(
    ('marked', 'expected'),
    [
        ('SELECT ⌶', 'SELECT'),
        ('SELECT a FROM ⌶', 'FROM'),
        ('SELECT a FROM t WHERE ⌶', 'WHERE'),
        ('SELECT a FROM t JOIN u ON ⌶', 'ON'),
        ('SELECT a FROM t GROUP BY ⌶', 'GROUP BY'),
        ('SELECT a FROM t ORDER BY ⌶', 'ORDER BY'),
        ('SELECT a FROM t GROUP BY a HAVING ⌶', 'HAVING'),
        ('INSERT INTO ⌶', 'INSERT INTO'),
        ('UPDATE t SET ⌶', 'SET'),
        ('DELETE FROM ⌶', 'DELETE FROM'),
        ('WITH x AS (SELECT ⌶', 'SELECT'),
        ('⌶', None),
    ],
)
def test_clause_detection(marked: str, expected: str | None) -> None:
    """The nearest clause keyword wins, and multi-word names beat their prefixes."""
    assert clause(marked) == expected


def test_multi_word_clause_beats_its_last_word() -> None:
    """`GROUP BY` must not be read as the single word `BY`."""
    assert clause('SELECT a FROM t GROUP BY ⌶') == 'GROUP BY'


def test_a_closed_subquery_does_not_capture_the_clause() -> None:
    """`SELECT a, (SELECT b FROM t2), ⌶` is still in the outer SELECT."""
    assert clause('SELECT a, (SELECT b FROM t2), ⌶ FROM t1') == 'SELECT'


def test_inside_an_open_subquery_the_inner_clause_wins() -> None:
    """Depth equality is what separates the two."""
    assert clause('SELECT * FROM (SELECT b FROM t2 WHERE ⌶)') == 'WHERE'


def test_non_subquery_parens_fall_back_to_the_enclosing_clause() -> None:
    """`WHERE (a AND ⌶)` has no clause keyword at depth 1, so WHERE is the answer."""
    assert clause('SELECT * FROM t WHERE (a AND ⌶)') == 'WHERE'


def test_function_call_parens_fall_back_too() -> None:
    """`SELECT sum(⌶` is still the SELECT clause."""
    assert clause('SELECT sum(⌶') == 'SELECT'


def test_clause_ignores_the_word_being_typed() -> None:
    """A half-typed `fro` is not the FROM clause."""
    assert clause('SELECT a fro⌶') == 'SELECT'


def test_a_quoted_name_is_never_a_clause_word() -> None:
    """
    `FROM "limit" ⌶` is in the FROM clause; `limit` there is a table.

    Quoting is how you say "this is a name, not syntax", and reading it as a
    clause loses the relation as well as the clause.
    """
    assert clause('SELECT * FROM "limit" ⌶') == 'FROM'
    assert clause('SELECT ⌶ FROM "limit"') == 'SELECT'
    assert clause('SELECT * FROM "values" v ⌶') == 'FROM'


def test_an_unclosed_paren_does_not_merge_two_statements() -> None:
    """
    A `;` token at depth greater than zero only ever means a dangling paren.

    The rule used to require depth zero, for a reason that does not hold: a
    semicolon inside a string, a comment, a quoted identifier or a dollar-quoted
    function body is swallowed by that token and never reaches the scan as
    punctuation at all. So the depth test guarded nothing reachable, while one
    missing `)` above the caret merged the statements and leaked the earlier
    one's relations into the later one's scope.

    `lsp/documents.py` has always split on any semicolon, so this is also the
    two layers agreeing on where a statement ends.
    """
    catalog = MemoryCatalog({('public', 'users'): [('uname', 'varchar')], ('public', 'orders'): [('total', 'numeric')]})
    for head in ('SELECT count(*) FROM users;', 'SELECT count(* FROM users;'):
        sql = head + '\nSELECT  FROM orders'
        caret = len(head) + len('\nSELECT ')
        assert [s.text for s in complete(sql, caret, POSTGRES, catalog, limit=10)] == ['orders.total'], head


def test_a_semicolon_inside_a_literal_still_does_not_split() -> None:
    """The half of the old rule that was real, and is done by the lexer."""
    catalog = MemoryCatalog({('public', 'users'): [('uname', 'varchar')]})
    sql = "SELECT 'a;b' AS x, ⌶ FROM users"
    caret = sql.index('⌶')
    assert 'uname' in ' '.join(s.text for s in complete(sql.replace('⌶', ''), caret, POSTGRES, catalog, limit=10))
