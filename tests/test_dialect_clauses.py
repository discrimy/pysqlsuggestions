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
    """PREWHERE is the canonical clause only one dialect has, and the others must not know it."""
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


@pytest.mark.parametrize('clause', ['ON', 'USING', 'FROM', 'JOIN'])
def test_every_later_clause_can_follow_a_join_block(clause: str) -> None:
    """
    A finished ON takes anything a FROM does. Curating each list by hand meant
    ON offered ORDER BY but not HAVING, LIMIT or OFFSET.
    """
    found = ANSI.clauses.get(clause)
    assert found is not None
    tail = found.followed_by if clause != 'JOIN' else ANSI.clauses.get('ON').followed_by  # type: ignore[union-attr]
    assert {'WHERE', 'GROUP BY', 'HAVING', 'WINDOW', 'ORDER BY', 'LIMIT', 'OFFSET', 'UNION'} <= set(tail)


def test_a_clause_does_not_offer_itself_or_anything_earlier() -> None:
    """The order is a sequence: nothing before GROUP BY may follow it."""
    group_by = ANSI.clauses.get('GROUP BY')
    assert group_by is not None
    assert 'WHERE' not in group_by.followed_by
    assert 'GROUP BY' not in group_by.followed_by
    assert 'HAVING' in group_by.followed_by


def test_extending_did_not_disturb_ansi() -> None:
    """
    ANSI is shared by all three; extend() must never mutate it.

    Asserted against the names each dialect adds rather than a count, so that
    moving a clause out of the base — RETURNING, which ClickHouse and Trino do
    not have — is not mistaken for a leak.
    """
    base = {clause.name for clause in ANSI.clauses.clauses}
    assert 'PREWHERE' not in base
    assert 'RETURNING' not in base
    assert {clause.name for clause in POSTGRES.clauses.clauses} - base == {
        'LATERAL',
        'DISTINCT ON',
        'ON CONFLICT',
        'RETURNING',
        # Sequences are Postgres's alone here: Trino's parser lists what DROP
        # accepts and SEQUENCE is not among it, and ClickHouse has none at all.
        'DROP SEQUENCE',
        'ALTER SEQUENCE',
        'DROP MATERIALIZED VIEW',
        'DROP INDEX',
        # Row locking. Four two-word names rather than one `FOR`, because a bare
        # head that is already a phrase is skipped by `_half_written_clauses`.
        'FOR UPDATE',
        'FOR NO KEY UPDATE',
        'FOR SHARE',
        'FOR KEY SHARE',
        'OF',
        # Declared to make a caret stop answering rather than to make it answer:
        # until each was a clause, the position after it stayed governed by the
        # clause before and offered relations or the CTE body words.
        'TABLESAMPLE',
        'SEARCH',
        'CYCLE',
    }
    assert {clause.name for clause in ANSI.clauses.clauses} == base


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


@pytest.mark.parametrize('dialect', [ANSI, POSTGRES, CLICKHOUSE, TRINO])
def test_the_fetch_tail_reaches_every_dialect(dialect: Dialect) -> None:
    """
    Promoted to ANSI, so ClickHouse and Trino inherit it and no grammar case covers them.

    All three backends accept `SELECT 1 ORDER BY 1 FETCH FIRST 1 ROWS ONLY`,
    verified against the containers rather than argued from the standard.
    ClickHouse refuses the tail without an ORDER BY — a constraint on the shape
    of the statement, not on the vocabulary this offers.
    """
    fetch = dialect.clauses.get('FETCH')
    assert fetch is not None
    assert {'FIRST', 'NEXT', 'ROW', 'ROWS', 'ONLY', 'WITH TIES'} <= set(fetch.followed_by)


@pytest.mark.parametrize('name', ['UNION', 'INTERSECT', 'EXCEPT'])
def test_a_set_operator_does_not_claim_the_word_DISTINCT(name: str) -> None:
    """
    All three backends take `UNION DISTINCT`, and offering it costs more than it gives.

    `_half_written_clauses` treats every `followed_by` entry as a phrase and
    skips a head that is already one, so naming DISTINCT here makes
    ('DISTINCT',) a phrase and `SELECT DISTINCT ⌶` stops completing to
    `DISTINCT ON`. This test is the guard on that trade, not on the vocabulary.
    """
    clause = ANSI.clauses.get(name)
    assert clause is not None
    assert 'DISTINCT' not in clause.followed_by


@pytest.mark.parametrize('name', ['LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN', 'CROSS JOIN'])
def test_every_join_spelling_is_offered_after_a_relation(name: str) -> None:
    """
    Promoted to ANSI, so the two dialects the grammar suite does not cover inherit them.

    Asserted through FROM's continuations rather than through `clauses.get`,
    because none of these is a clause of its own — `clause_at` matches `JOIN`
    and the modifier rides along, which is why widening the list was the whole
    change.
    """
    for dialect in (ANSI, POSTGRES, CLICKHOUSE, TRINO):
        assert name in dialect.clauses.continuations('FROM'), f'{dialect.name} does not offer {name}'
