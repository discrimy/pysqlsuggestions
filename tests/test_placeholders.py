"""
Bound parameters: the one position where this engine used to give a wrong answer.

`WHERE u.id = :us⌶` proposed `u.user_id`, and accepting it wrote valid SQL that
ran a different query. A missing suggestion costs a keystroke; this cost
correctness, which is why it is the only entry on the gaps list described as an
active wrong answer rather than a missing one.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import TEMPLATE_PLACEHOLDER, Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO

SNAPSHOT = {
    ('public', 'users'): [
        ('id', 'bigint'),
        ('user_id', 'bigint'),
        ('usage_count', 'bigint'),
    ],
}


def catalog() -> MemoryCatalog:
    """Three columns, two of which a naive completion offers for the prefix `us`."""
    return MemoryCatalog(SNAPSHOT)


def offered(sql: str, dialect: Dialect = POSTGRES) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), dialect, catalog())]


def test_a_caret_inside_a_named_parameter_offers_nothing() -> None:
    """The name is the author's to write, and the engine does not know the binding."""
    assert offered('SELECT * FROM users u WHERE u.id = :us') == []


def test_a_caret_inside_a_numbered_parameter_offers_nothing() -> None:
    """`$1` could always become `$12`, so the caret at its end is still inside it."""
    assert offered('SELECT * FROM users u WHERE u.id = $1') == []


def test_a_caret_inside_a_braced_parameter_offers_nothing() -> None:
    """ClickHouse spells it `{name:Type}` and an unclosed one is still a parameter."""
    assert offered('SELECT * FROM users u WHERE u.id = {us', CLICKHOUSE) == []


def test_the_request_inside_a_parameter_asks_for_no_kinds() -> None:
    """Silence is a decision the pure half makes, so a caller with no catalog gets it too."""
    sql = 'SELECT * FROM users u WHERE u.id = :us'
    assert derive_request(sql, len(sql), POSTGRES).kinds == ()


def test_a_finished_parameter_ends_an_operand() -> None:
    """`= ? ⌶` cannot take a column: two operands in a row is not valid SQL."""
    found = offered('SELECT * FROM users u WHERE u.id = ? ', TRINO)
    assert 'AND' in found
    assert 'u.user_id' not in found


def test_a_bare_marker_is_past_itself_the_moment_it_is_written() -> None:
    """`?` admits nothing more, so the caret at its end wants a connective, not silence."""
    assert 'AND' in offered('SELECT * FROM users u WHERE u.id = ?', TRINO)


def test_a_named_parameter_followed_by_a_space_ends_an_operand() -> None:
    """Past the name the ordinary rules apply again."""
    assert 'AND' in offered('SELECT * FROM users u WHERE u.id = :user_id ')


def test_a_closed_braced_parameter_ends_an_operand() -> None:
    """The brace is the delimiter, so the caret past it is past the parameter."""
    dialect = replace(POSTGRES, syntax=replace(POSTGRES.syntax, placeholders=(TEMPLATE_PLACEHOLDER,)))
    assert 'AND' in offered('SELECT * FROM users u WHERE u.id = ${region}', dialect)


def test_a_parameter_on_the_left_proposes_no_values() -> None:
    """Nothing in the text says what `:p` holds, so nothing can be narrowed against it."""
    sql = 'SELECT * FROM users u WHERE :p = '
    assert derive_request(sql, len(sql), POSTGRES).comparand == ()


def test_a_parameter_inside_a_literal_is_text() -> None:
    """A literal being written is still a value position, and a colon in it is a colon."""
    sql = "SELECT * FROM users u WHERE u.id = ':us"
    assert derive_request(sql, len(sql), POSTGRES).kinds != ()


def test_the_template_form_is_not_on_by_default() -> None:
    """`${var}` is a templating convention, not a backend's syntax. A caller composes it in."""
    assert 'u.usage_count' in offered('SELECT * FROM users u WHERE u.id = ${us')
    dialect = replace(POSTGRES, syntax=replace(POSTGRES.syntax, placeholders=(TEMPLATE_PLACEHOLDER,)))
    assert offered('SELECT * FROM users u WHERE u.id = ${us', dialect) == []


@pytest.mark.parametrize('dialect', [POSTGRES, CLICKHOUSE, TRINO], ids=lambda d: d.name)
def test_a_statement_with_no_parameter_is_unaffected(dialect: Dialect) -> None:
    """The whole feature must be invisible to SQL that contains none."""
    assert 'u.usage_count' in offered('SELECT * FROM users u WHERE u.id = us', dialect)
