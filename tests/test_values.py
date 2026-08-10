"""
Value suggestions: the literals a column actually holds.

`WHERE type = ⌶` is the one position where a column name is the wrong answer,
and the values are knowable without reading the table — Postgres already keeps
the frequent ones in `pg_stats` for the planner, filtered by what the role may
read. A backend without such statistics simply offers nothing here.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions.api import apply_suggestion, complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Kind
from tests.corpus.cases import split_caret

SNAPSHOT = {
    ('public', 'reports_database'): [
        ('id', 'bigint'),
        ('title', 'varchar(256)'),
        ('type', 'varchar(256)'),
        ('is_archived', 'boolean'),
        ('port', 'integer'),
    ],
}

VALUES = {
    ('public', 'reports_database', 'type'): ['postgres', 'clickhouse', "o'brien"],
    ('public', 'reports_database', 'is_archived'): ['false', 'true'],
    ('public', 'reports_database', 'port'): ['5432', '9000'],
}


def catalog() -> MemoryCatalog:
    """The fixture catalog, with statistics for three columns."""
    return MemoryCatalog(SNAPSHOT, values=VALUES)


def texts(marked: str, dialect: Dialect = POSTGRES, cat: MemoryCatalog | None = None) -> list[str]:
    """Suggestion texts for ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return [s.text for s in complete(sql, caret, dialect, catalog() if cat is None else cat)]


def test_a_comparison_offers_the_values_that_column_holds() -> None:
    """The frequent ones first: `pg_stats` orders them by frequency, and so does this."""
    assert texts('SELECT * FROM reports_database d WHERE d.type = ⌶')[:3] == [
        "'postgres'",
        "'clickhouse'",
        "'o''brien'",
    ]


def test_a_value_is_quoted_by_its_column_type() -> None:
    """A varchar needs quotes, a boolean and an integer must not have them."""
    assert texts('SELECT * FROM reports_database d WHERE d.is_archived = ⌶')[:2] == ['false', 'true']
    assert texts('SELECT * FROM reports_database d WHERE d.port = ⌶')[:2] == ['5432', '9000']


def test_values_lead_the_columns_they_are_compared_against() -> None:
    """A concrete value is the likelier answer right of an operator; the columns stay."""
    found = texts('SELECT * FROM reports_database d WHERE d.type = ⌶')
    assert found[0].startswith("'")
    assert 'title' in found


def test_values_are_offered_only_where_one_belongs() -> None:
    """Not in a select list, not left of the operator, not for an unknown column."""
    assert not any(t.startswith("'") for t in texts('SELECT ⌶ FROM reports_database d'))
    assert not any(t.startswith("'") for t in texts('SELECT * FROM reports_database d WHERE ⌶'))
    assert not any(t.startswith("'") for t in texts('SELECT * FROM reports_database d WHERE d.title = ⌶'))


def test_a_half_typed_literal_still_completes() -> None:
    """
    Typing the opening quote is the natural next keystroke, and going silent
    there makes the feature look broken. The span covers the whole literal, so
    what is inserted replaces it rather than nesting inside it.
    """
    sql, caret = split_caret("SELECT * FROM reports_database d WHERE d.type = 'clic⌶")
    request = derive_request(sql, caret, POSTGRES)
    assert request.kinds == (Kind.VALUE,)
    assert request.prefix == 'clic'
    found = complete(sql, caret, POSTGRES, catalog())
    assert [s.text for s in found] == ["'clickhouse'"]
    assert apply_suggestion(sql, found[0])[0].endswith("d.type = 'clickhouse'")


def test_a_finished_literal_is_not_completed_over() -> None:
    """`= 'postgres'` is written; the caret after it is back in ordinary SQL."""
    assert texts("SELECT * FROM reports_database d WHERE d.type = 'postgres'⌶")[:2] == ['AND', 'OR']


@pytest.mark.parametrize('dialect', [POSTGRES, CLICKHOUSE])
def test_a_catalog_without_statistics_offers_no_values(dialect: Dialect) -> None:
    """The documented degradation: the position still works, it just has no values."""
    bare = MemoryCatalog(SNAPSHOT)
    found = texts('SELECT * FROM reports_database d WHERE d.type = ⌶', dialect, bare)
    assert not any(t.startswith("'") for t in found)
    assert 'title' in found


def test_a_boolean_is_spelled_as_a_literal_not_as_postgres_prints_it() -> None:
    """
    `pg_stats` reports a boolean the way Postgres *prints* it — `t` and `f` —
    and neither is a literal. `WHERE is_superuser = f` parses `f` as a column
    reference and fails with `column "f" does not exist`.
    """
    printed = MemoryCatalog(SNAPSHOT, values={('public', 'reports_database', 'is_archived'): ['f', 't']})
    assert texts('SELECT * FROM reports_database d WHERE d.is_archived = ⌶', cat=printed)[:2] == ['false', 'true']


def test_a_boolean_already_spelled_out_is_left_alone() -> None:
    """A backend that reports `true` needs no translating."""
    spelled = MemoryCatalog(SNAPSHOT, values={('public', 'reports_database', 'is_archived'): ['TRUE', 'false']})
    assert texts('SELECT * FROM reports_database d WHERE d.is_archived = ⌶', cat=spelled)[:2] == ['true', 'false']


def test_an_unrecognised_boolean_is_quoted_rather_than_guessed() -> None:
    """
    Every backend here coerces a quoted literal to boolean, so quoting is the
    honest answer to a spelling this table does not know — better than emitting
    a bare word that may not parse.
    """
    odd = MemoryCatalog(SNAPSHOT, values={('public', 'reports_database', 'is_archived'): ['maybe']})
    assert texts('SELECT * FROM reports_database d WHERE d.is_archived = ⌶', cat=odd)[:1] == ["'maybe'"]
