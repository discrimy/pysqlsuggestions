"""Set-returning functions in a FROM list."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, USER_COLUMNS, texts


def test_function_in_from_does_not_swallow_the_rest_of_the_list(cur: MemoryCatalog) -> None:
    """
    A call in a relation position is not a relation, and its argument list is not
    more of them. Read as one, `generate_series(1, 10)` sends the catalog looking
    for a table of that name and leaves `1, 10` to be read as further relations —
    taking `auth_user` down with it.
    """
    sql = 'SELECT * FROM generate_series(1, 10) g, auth_user u WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_function_in_from_unqualified_scope(cur: MemoryCatalog) -> None:
    """
    Its rows have no columns this engine can name, so it contributes none. A
    relation in scope that can answer nothing still counts towards "more than
    one relation", which would qualify every other column for no reason.
    """
    sql = 'SELECT * FROM generate_series(1, 10) g, auth_group WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['auth_group.id', 'auth_group.name']


def test_function_column_definition_list(cur: MemoryCatalog) -> None:
    """
    `AS t(a int, b text)` is the one place a caller says what such a source
    returns. Those names are the only thing anyone can know about it, so they
    are what `t.` answers with — and the types beside them are not columns.
    """
    sql = 'SELECT * FROM jsonb_to_recordset(x) AS t(a int, b text), orders o WHERE t.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


def test_function_column_definition_list_keeps_later_items(cur: MemoryCatalog) -> None:
    """
    The definition list is parenthesised and comma-separated, exactly like a
    relation list. Consuming it as one would swallow `orders` and leave `o.`
    answering with nothing.
    """
    sql = 'SELECT * FROM jsonb_to_recordset(x) AS t(a int, b text), orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_function_without_alias_is_harmless(cur: MemoryCatalog) -> None:
    """
    Nothing to qualify with and nothing to offer, so it enters scope not at all
    rather than as a nameless relation — which would qualify `auth_group`'s
    columns and answer `.column` for its own.
    """
    sql = 'SELECT * FROM generate_series(1, 10), auth_group WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['auth_group.id', 'auth_group.name']
