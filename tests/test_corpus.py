"""The corpus is data; these tests keep the data itself honest."""

from __future__ import annotations

import pytest

from tests.corpus.cases import CARET, CASES, GoldenRequest, split_caret


def test_corpus_is_not_empty() -> None:
    """A silently empty corpus would make the burn-down meaningless."""
    assert len(CASES) >= 20


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.sql)
def test_every_case_marks_exactly_one_caret(case: GoldenRequest) -> None:
    """Two markers or none would produce a nonsense offset."""
    assert case.sql.count(CARET) == 1


def test_split_caret_strips_the_marker() -> None:
    """The marker must never reach the lexer."""
    sql, caret = split_caret('SELECT a⌶ FROM t')
    assert sql == 'SELECT a FROM t'
    assert caret == 8
    assert CARET not in sql


def test_every_case_names_a_known_dialect() -> None:
    """A typo in a dialect name would silently skip the case."""
    assert {case.dialect for case in CASES} <= {'ansi', 'postgres', 'clickhouse', 'trino'}
