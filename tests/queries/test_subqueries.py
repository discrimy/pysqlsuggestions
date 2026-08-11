"""Derived tables, LATERAL, and subqueries in an expression position."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, USER_COLUMNS, analyze, at, texts


def test_derived_table_columns(cur: MemoryCatalog) -> None:
    """A derived table's columns come from its select list."""
    sql = 'SELECT * FROM (select id, email from auth_user) s WHERE s.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_derived_table_star(cur: MemoryCatalog) -> None:
    """Including through a star."""
    sql = 'SELECT * FROM (select * from auth_group) s WHERE s.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_derived_table_with_as_keyword(cur: MemoryCatalog) -> None:
    """AS before the alias changes nothing."""
    sql = 'SELECT * FROM (select id from orders) AS s WHERE s.'
    assert texts(cur, sql) == ['id']


def test_derived_table_does_not_leak(cur: MemoryCatalog) -> None:
    """Its body's relations do not reach the outer query."""
    sql = 'SELECT * FROM (select id from auth_user) s WHERE '
    assert texts(cur, sql) == ['s.id']


def test_cursor_inside_derived_table(cur: MemoryCatalog) -> None:
    """Inside it, its own FROM is in scope."""
    sql = 'SELECT * FROM (select * from auth_group g where g.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_derived_table_with_column_list(cur: MemoryCatalog) -> None:
    """A derived table's column list renames its outputs."""
    sql = 'SELECT * FROM (SELECT id, email FROM auth_user) s(a, b) WHERE s.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


def test_lateral_subquery_columns(cur: MemoryCatalog) -> None:
    """A LATERAL subquery is a relation."""
    sql = 'SELECT * FROM auth_user u, LATERAL (SELECT total FROM orders WHERE user_id = u.id) l WHERE l.'
    assert texts(cur, sql) == ['total']


def test_lateral_join_keeps_outer_relation(cur: MemoryCatalog) -> None:
    """And LATERAL is what lets it see the FROM list it sits in."""
    sql = 'SELECT * FROM auth_user u LEFT JOIN LATERAL (SELECT total FROM orders) l ON true WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_correlated_subquery_in_select_list(cur: MemoryCatalog) -> None:
    """A correlated subquery in the select list sees the outer query."""
    sql = 'SELECT (SELECT email FROM auth_user WHERE id = o.user_id) AS e FROM orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_cte_in_subquery_in_where(cur: MemoryCatalog) -> None:
    """A CTE reached from a subquery inside WHERE."""
    sql = 'SELECT * FROM orders o WHERE o.user_id IN (WITH a AS (SELECT id FROM auth_user) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_deeply_nested_derived_tables(cur: MemoryCatalog) -> None:
    """A star expands through two levels of derived table."""
    sql = 'SELECT * FROM (SELECT * FROM (SELECT id, email FROM auth_user) inner_t) outer_t WHERE outer_t.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_derived_table_joined_to_a_cte(cur: MemoryCatalog) -> None:
    """A derived table joined to a CTE."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a JOIN (SELECT total FROM orders) d ON true WHERE d.'
    assert texts(cur, sql) == ['total']


def test_cte_and_derived_table_both_in_scope(cur: MemoryCatalog) -> None:
    """Both in scope at once."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a JOIN (SELECT total FROM orders) d ON true WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['a.id', 'd.total']


def test_subquery_relations_drop_out_once_it_closes(cur: MemoryCatalog) -> None:
    """A subquery's relations drop out when it closes."""
    got = at(cur, 'SELECT * FROM orders o WHERE o.user_id IN (SELECT id FROM auth_user) AND ‸', limit=50)
    assert sorted(got) == ['o.created', 'o.id', 'o.total', 'o.user_id']


def test_correlated_outer_relation_visible_inside_a_subquery(cur: MemoryCatalog) -> None:
    """The outer relation stays visible inside it."""
    got = at(cur, 'SELECT * FROM orders o WHERE o.user_id IN (SELECT ‸ FROM auth_user)', limit=50)
    assert sorted(got) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
        'o.created',
        'o.id',
        'o.total',
        'o.user_id',
    ]


def test_outer_qualifier_inside_an_exists_subquery(cur: MemoryCatalog) -> None:
    """An outer qualifier inside EXISTS, narrowed by the comparison it faces."""
    got = at(cur, 'SELECT * FROM orders o WHERE EXISTS (SELECT 1 FROM auth_user u WHERE u.id = o.‸)')
    assert sorted(got) == ['id', 'total', 'user_id']


def test_scalar_subquery_in_select_list_does_not_leak(cur: MemoryCatalog) -> None:
    """A scalar subquery in the select list does not leak outward."""
    got = at(cur, 'SELECT (SELECT name FROM auth_group), ‸ FROM orders', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_clause_after_a_closed_subquery_is_still_select(cur: MemoryCatalog) -> None:
    """The clause after a closed subquery is still the outer SELECT."""
    sql = 'SELECT (SELECT name FROM auth_group), '
    assert analyze(sql).clause == 'SELECT'


def test_any_subquery_relations_drop_out(cur: MemoryCatalog) -> None:
    """An ANY subquery's relations drop out too."""
    got = at(cur, 'SELECT * FROM orders o WHERE o.id = ANY (SELECT id FROM auth_user) AND ‸', limit=50)
    assert sorted(got) == ['o.created', 'o.id', 'o.total', 'o.user_id']


def test_nested_subquery_sees_every_enclosing_level(cur: MemoryCatalog) -> None:
    """A nested subquery sees every level enclosing it."""
    got = at(
        cur,
        'SELECT * FROM orders o WHERE o.id IN (SELECT user_id FROM orders WHERE user_id IN (SELECT ‸ FROM auth_user))',
        limit=50,
    )
    assert sorted(got) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
        'o.created',
        'o.id',
        'o.total',
        'o.user_id',
        'orders.created',
        'orders.id',
        'orders.total',
        'orders.user_id',
    ]
