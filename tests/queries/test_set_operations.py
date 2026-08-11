"""UNION and EXCEPT: each branch has its own FROM, and its own scope."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, at, texts


def test_union_of_two_ctes(cur: MemoryCatalog) -> None:
    """
    The branches are separate queries that happen to share a result shape, so a
    WHERE belongs to the branch it was written in. Merging them offers `a`'s
    columns where only `b` can be referenced.
    """
    sql = (
        'WITH a AS (SELECT id FROM auth_user), b AS (SELECT id FROM orders)\n'
        'SELECT * FROM a UNION SELECT * FROM b WHERE '
    )
    assert texts(cur, sql, limit=50) == ['b.id']


def test_union_second_branch_scope(cur: MemoryCatalog) -> None:
    """
    The FROM that answers this is in the second branch, and the first branch's is
    not a fallback. The whole point of splitting on a set operator is that these
    two positions get different answers.
    """
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT ‸ FROM orders', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_union_first_branch_scope(cur: MemoryCatalog) -> None:
    """
    And the split works in both directions — a branch is not "everything before
    the caret", which would make the first branch see nothing at all.
    """
    got = at(cur, 'SELECT ‸ FROM auth_user UNION SELECT id FROM orders', limit=50)
    assert sorted(got) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_union_second_branch_where(cur: MemoryCatalog) -> None:
    """
    A clause inside a branch belongs to that branch. Scanning back for the
    nearest FROM without stopping at the set operator finds the wrong one.
    """
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT id FROM orders WHERE ‸', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_union_qualified_in_second_branch(cur: MemoryCatalog) -> None:
    """
    A qualifier resolves against the branch's relations too, so an alias declared
    in one branch is not visible from the other.
    """
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT o.‸ FROM orders o')
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_parenthesised_union_branches(cur: MemoryCatalog) -> None:
    """
    Parentheses around each branch are optional and change nothing semantically,
    so a splitter keyed on paren depth rather than on the operator gets this
    wrong while passing the unparenthesised case.
    """
    got = at(cur, '(SELECT id FROM auth_user) UNION (SELECT ‸ FROM orders)', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_except_second_branch(cur: MemoryCatalog) -> None:
    """
    EXCEPT and INTERSECT split scope exactly as UNION does. Handling only UNION
    is the easy mistake, and the other two are rarer precisely where a wrong
    answer would go unnoticed longest.
    """
    got = at(cur, 'SELECT id FROM auth_user EXCEPT SELECT ‸ FROM orders', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_union_inside_a_cte_body(cur: MemoryCatalog) -> None:
    """
    Branches nest: the split has to happen inside the CTE body, not once per
    statement, or the body's second branch inherits the first's relations.
    """
    got = at(cur, 'WITH a AS (SELECT id FROM auth_user UNION SELECT ‸ FROM orders) SELECT * FROM a', limit=50)
    assert sorted(got) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']
