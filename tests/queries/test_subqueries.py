"""Derived tables, LATERAL, and subqueries in an expression position."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, USER_COLUMNS, analyze, at, texts


def test_derived_table_columns(cur: MemoryCatalog) -> None:
    """
    A derived table has no catalog entry, so its columns are whatever its select
    list names. Nothing but reading the body can answer `s.`.
    """
    sql = 'SELECT * FROM (select id, email from auth_user) s WHERE s.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_derived_table_star(cur: MemoryCatalog) -> None:
    """
    A star inside it defers to the relation it stands for, which the catalog does
    know — so the two sources of truth have to meet.
    """
    sql = 'SELECT * FROM (select * from auth_group) s WHERE s.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_derived_table_with_as_keyword(cur: MemoryCatalog) -> None:
    """
    `AS` before the alias is optional and means nothing. Consuming it as the
    alias itself puts a relation called `as` in scope.
    """
    sql = 'SELECT * FROM (select id from orders) AS s WHERE s.'
    assert texts(cur, sql) == ['id']


def test_derived_table_does_not_leak(cur: MemoryCatalog) -> None:
    """
    Its body's FROM belongs to it. Leaking `auth_user` outward offers columns
    through a relation the outer query cannot name.
    """
    sql = 'SELECT * FROM (select id from auth_user) s WHERE '
    assert texts(cur, sql) == ['s.id']


def test_cursor_inside_derived_table(cur: MemoryCatalog) -> None:
    """
    Inside the body, the body's own FROM is what answers — and the parenthesis is
    still open, which is the state every derived table passes through while
    being typed.
    """
    sql = 'SELECT * FROM (select * from auth_group g where g.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_derived_table_with_column_list(cur: MemoryCatalog) -> None:
    """
    `s(a, b)` renames the outputs, so the body's own names are no longer
    reachable. Offering them anyway suggests identifiers the query has hidden.
    """
    sql = 'SELECT * FROM (SELECT id, email FROM auth_user) s(a, b) WHERE s.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


def test_lateral_subquery_columns(cur: MemoryCatalog) -> None:
    """
    LATERAL is the keyword that asks for what a plain derived table is denied:
    the FROM list it sits in. Failing to read it as a relation at all loses `l`
    entirely.
    """
    sql = 'SELECT * FROM auth_user u, LATERAL (SELECT total FROM orders WHERE user_id = u.id) l WHERE l.'
    assert texts(cur, sql) == ['total']


def test_lateral_join_keeps_outer_relation(cur: MemoryCatalog) -> None:
    """
    And it does not cost the outer relation its place — `u` is still there
    afterwards, which a scope that replaces rather than extends would lose.
    """
    sql = 'SELECT * FROM auth_user u LEFT JOIN LATERAL (SELECT total FROM orders) l ON true WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_correlated_subquery_in_select_list(cur: MemoryCatalog) -> None:
    """
    A subquery in an expression sees the outer query, unlike one in a FROM. The
    caret here is outside it again, and the outer scope has to be intact.
    """
    sql = 'SELECT (SELECT email FROM auth_user WHERE id = o.user_id) AS e FROM orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_cte_in_subquery_in_where(cur: MemoryCatalog) -> None:
    """
    A WITH can open anywhere a SELECT can, including inside a predicate. Reading
    CTEs only at the top of a statement misses this one.
    """
    sql = 'SELECT * FROM orders o WHERE o.user_id IN (WITH a AS (SELECT id FROM auth_user) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_deeply_nested_derived_tables(cur: MemoryCatalog) -> None:
    """
    A star expanded through two levels: the outer body's `*` resolves to a
    relation that is itself a derived table. One level is the common case and
    the one that hides a missing recursion.
    """
    sql = 'SELECT * FROM (SELECT * FROM (SELECT id, email FROM auth_user) inner_t) outer_t WHERE outer_t.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_derived_table_joined_to_a_cte(cur: MemoryCatalog) -> None:
    """
    Two relations the statement described itself, of different kinds, in one
    FROM. Neither is in the catalog and both have to be in scope.
    """
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a JOIN (SELECT total FROM orders) d ON true WHERE d.'
    assert texts(cur, sql) == ['total']


def test_cte_and_derived_table_both_in_scope(cur: MemoryCatalog) -> None:
    """
    And unqualified, where they are told apart only by the labels they were
    given.
    """
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a JOIN (SELECT total FROM orders) d ON true WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['a.id', 'd.total']


def test_subquery_relations_drop_out_once_it_closes(cur: MemoryCatalog) -> None:
    """
    A subquery's scope ends with its parenthesis. Tracking it by position rather
    than by depth leaves `auth_user` in view for the rest of the predicate.
    """
    got = at(cur, 'SELECT * FROM orders o WHERE o.user_id IN (SELECT id FROM auth_user) AND ‸', limit=50)
    assert sorted(got) == ['o.created', 'o.id', 'o.total', 'o.user_id']


def test_correlated_outer_relation_visible_inside_a_subquery(cur: MemoryCatalog) -> None:
    """
    The other direction: inside, the outer relation is still visible, because a
    subquery in an expression may reference it. `o.id` and `auth_user.id` are
    both offered and distinct, which is what qualifying them buys.
    """
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
    """
    An outer qualifier reaches out of the subquery, and the comparison it faces
    narrows what comes back: `created` is a date and `u.id` is an integer, so
    offering it would be offering a query that does not run.
    """
    got = at(cur, 'SELECT * FROM orders o WHERE EXISTS (SELECT 1 FROM auth_user u WHERE u.id = o.‸)')
    assert sorted(got) == ['id', 'total', 'user_id']


def test_scalar_subquery_in_select_list_does_not_leak(cur: MemoryCatalog) -> None:
    """
    A scalar subquery closes before the caret. Its relation must not survive into
    the select list that follows it.
    """
    got = at(cur, 'SELECT (SELECT name FROM auth_group), ‸ FROM orders', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_clause_after_a_closed_subquery_is_still_select(cur: MemoryCatalog) -> None:
    """
    And the clause is still the outer SELECT rather than the subquery's, which
    is what decides whether columns or relations are offered next.
    """
    sql = 'SELECT (SELECT name FROM auth_group), '
    assert analyze(sql).clause == 'SELECT'


def test_any_subquery_relations_drop_out(cur: MemoryCatalog) -> None:
    """
    `ANY (...)` is the same shape as `IN (...)` with a different keyword, and
    scope has to end at the parenthesis either way.
    """
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
