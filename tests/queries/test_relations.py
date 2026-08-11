"""Plain relation references, qualifiers, and the clauses that introduce them."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, USER_COLUMNS, texts


def test_plain_alias_columns(cur: MemoryCatalog) -> None:
    """An alias qualifier."""
    assert sorted(texts(cur, 'select * from auth_user u where u.')) == sorted(USER_COLUMNS)


def test_plain_table_name_qualifier(cur: MemoryCatalog) -> None:
    """A table name used as the qualifier."""
    sql = 'select * from auth_user where auth_user.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_plain_unqualified_columns(cur: MemoryCatalog) -> None:
    """No qualifier typed."""
    assert sorted(texts(cur, 'select * from auth_group where ')) == ['auth_group.id', 'auth_group.name']


def test_join_brings_both_relations(cur: MemoryCatalog) -> None:
    """A join puts both relations in scope."""
    sql = 'select * from auth_user u join orders o on o.user_id = u.id where '
    got = texts(cur, sql, limit=50)
    assert 'u.username' in got
    assert 'o.total' in got


def test_schema_qualified_table_columns(cur: MemoryCatalog) -> None:
    """A schema-qualified relation."""
    sql = 'select * from billing.invoices where invoices.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_from_offers_tables(cur: MemoryCatalog) -> None:
    """An empty FROM offers relations."""
    assert 'auth_user' in texts(cur, 'select * from ')


def test_insert_column_list_uses_the_target_table(cur: MemoryCatalog) -> None:
    """An INSERT column list takes the target's columns."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nINSERT INTO orders ('
    assert sorted(texts(cur, sql, limit=50)) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_delete_using_relation_is_in_scope(cur: MemoryCatalog) -> None:
    """DELETE ... USING brings a relation into scope."""
    sql = 'DELETE FROM orders o USING auth_user u WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_join_using_column_list_is_not_a_relation(cur: MemoryCatalog) -> None:
    """The join's USING names columns, not relations."""
    sql = 'SELECT * FROM auth_user u JOIN orders o USING (id) WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_natural_join_keeps_both_relations(cur: MemoryCatalog) -> None:
    """A NATURAL JOIN keeps both."""
    sql = 'SELECT * FROM auth_user u NATURAL JOIN orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS
