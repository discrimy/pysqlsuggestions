"""Clause detection: scan back to the nearest clause keyword at the caret's depth."""

from __future__ import annotations

import pytest

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
