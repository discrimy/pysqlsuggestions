"""Dialect clause vocabulary. Adding a clause must cost one line, not a parser change."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.analyse import clause_at, statement_at
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Kind
from tests.corpus.cases import split_caret


def clause(marked: str, dialect: Dialect) -> str | None:
    """Run clause_at on ⌶-marked SQL for one dialect."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, dialect.syntax)
    lo, hi = statement_at(tokens, caret)
    return clause_at(tokens, lo, hi, caret, dialect.clauses)


def test_clickhouse_prewhere() -> None:
    """PREWHERE is the canonical example from plan.md §4."""
    assert clause('SELECT * FROM t PREWHERE ⌶', CLICKHOUSE) == 'PREWHERE'
    assert clause('SELECT * FROM t PREWHERE ⌶', POSTGRES) == 'FROM'


@pytest.mark.parametrize('name', ['PREWHERE', 'FINAL', 'ARRAY JOIN', 'SETTINGS', 'SAMPLE', 'LIMIT BY'])
def test_clickhouse_clause_vocabulary(name: str) -> None:
    """Each ClickHouse-only clause is present exactly once."""
    assert CLICKHOUSE.clauses.get(name) is not None
    assert ANSI.clauses.get(name) is None


@pytest.mark.parametrize('name', ['UNNEST', 'MATCH_RECOGNIZE', 'TABLESAMPLE'])
def test_trino_clause_vocabulary(name: str) -> None:
    """Each Trino-only clause is present exactly once."""
    assert TRINO.clauses.get(name) is not None
    assert ANSI.clauses.get(name) is None


@pytest.mark.parametrize('name', ['LATERAL', 'ON CONFLICT', 'DISTINCT ON'])
def test_postgres_clause_vocabulary(name: str) -> None:
    """Each Postgres-only clause is present exactly once."""
    assert POSTGRES.clauses.get(name) is not None
    assert ANSI.clauses.get(name) is None


def test_extending_did_not_disturb_ansi() -> None:
    """ANSI is shared by all three; extend() must never mutate it."""
    assert len(ANSI.clauses.clauses) == 24


def test_array_join_suggests_columns() -> None:
    """ARRAY JOIN takes an array-valued column, not a table."""
    array_join = CLICKHOUSE.clauses.get('ARRAY JOIN')
    assert array_join is not None
    assert array_join.suggests == (Kind.COLUMN, Kind.FUNCTION)


def test_settings_suggests_keywords_only() -> None:
    """SETTINGS takes setting names, which are neither columns nor tables."""
    settings = CLICKHOUSE.clauses.get('SETTINGS')
    assert settings is not None
    assert settings.suggests == (Kind.KEYWORD,)
