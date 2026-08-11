"""UNION and EXCEPT: each branch has its own FROM, and its own scope."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, at, texts


def test_union_of_two_ctes(cur: MemoryCatalog) -> None:
    """A UNION of two CTEs."""
    sql = (
        'WITH a AS (SELECT id FROM auth_user), b AS (SELECT id FROM orders)\n'
        'SELECT * FROM a UNION SELECT * FROM b WHERE '
    )
    assert texts(cur, sql, limit=50) == ['b.id']


def test_union_second_branch_scope(cur: MemoryCatalog) -> None:
    """A UNION's second branch sees only its own FROM."""
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT ‸ FROM orders', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_union_first_branch_scope(cur: MemoryCatalog) -> None:
    """As does the first."""
    got = at(cur, 'SELECT ‸ FROM auth_user UNION SELECT id FROM orders', limit=50)
    assert sorted(got) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_union_second_branch_where(cur: MemoryCatalog) -> None:
    """Including in its WHERE."""
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT id FROM orders WHERE ‸', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_union_qualified_in_second_branch(cur: MemoryCatalog) -> None:
    """And through a qualifier."""
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT o.‸ FROM orders o')
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_parenthesised_union_branches(cur: MemoryCatalog) -> None:
    """Parenthesised branches behave the same."""
    got = at(cur, '(SELECT id FROM auth_user) UNION (SELECT ‸ FROM orders)', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_except_second_branch(cur: MemoryCatalog) -> None:
    """So does EXCEPT."""
    got = at(cur, 'SELECT id FROM auth_user EXCEPT SELECT ‸ FROM orders', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_union_inside_a_cte_body(cur: MemoryCatalog) -> None:
    """And a UNION inside a CTE body."""
    got = at(cur, 'WITH a AS (SELECT id FROM auth_user UNION SELECT ‸ FROM orders) SELECT * FROM a', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']
