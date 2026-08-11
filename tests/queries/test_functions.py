"""Set-returning functions in a FROM list."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, USER_COLUMNS, texts


def test_function_in_from_does_not_swallow_the_rest_of_the_list(cur: MemoryCatalog) -> None:
    """A set-returning function in FROM leaves the rest of the list alone."""
    sql = 'SELECT * FROM generate_series(1, 10) g, auth_user u WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_function_in_from_unqualified_scope(cur: MemoryCatalog) -> None:
    """And contributes no columns of its own."""
    sql = 'SELECT * FROM generate_series(1, 10) g, auth_group WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['auth_group.id', 'auth_group.name']


def test_function_column_definition_list(cur: MemoryCatalog) -> None:
    """Unless a column definition list says what they are."""
    sql = 'SELECT * FROM jsonb_to_recordset(x) AS t(a int, b text), orders o WHERE t.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


def test_function_column_definition_list_keeps_later_items(cur: MemoryCatalog) -> None:
    """Which does not disturb the relations after it."""
    sql = 'SELECT * FROM jsonb_to_recordset(x) AS t(a int, b text), orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_function_without_alias_is_harmless(cur: MemoryCatalog) -> None:
    """A function with no alias breaks nothing."""
    sql = 'SELECT * FROM generate_series(1, 10), auth_group WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['auth_group.id', 'auth_group.name']
