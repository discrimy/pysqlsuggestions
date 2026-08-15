"""UNION and EXCEPT: each branch has its own FROM, and its own scope."""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
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


def test_the_tail_of_a_set_operation_offers_nothing() -> None:
    """
    Measured against all three backends, which do not agree with each other.

    `ORDER BY` after a UNION binds to the *result* on Postgres and Trino, where
    only the first branch's output names and ordinals resolve — and to the *last
    branch* on ClickHouse, which accepts that branch's own columns and then does
    not sort the union at all. The engine offered the last branch's columns
    everywhere: SQL that errors on two backends and silently mis-sorts on the
    third.

    Nothing is the one answer that is not wrong on any of them, and it is what
    this position's neighbour `LIMIT` already said.
    """
    catalog = MemoryCatalog(
        {
            ('public', 'users'): [('id', 'bigint'), ('name', 'text')],
            ('public', 'orders'): [('id', 'bigint'), ('total', 'numeric')],
        }
    )
    for dialect in (POSTGRES, CLICKHOUSE, TRINO):
        for tail in ('ORDER BY ', 'ORDER BY n'):
            sql = f'SELECT name AS nm FROM users UNION SELECT total FROM orders {tail}'
            assert complete(sql, len(sql), dialect, catalog) == [], (dialect.name, tail)


def test_a_plain_order_by_is_untouched() -> None:
    """The suppression is about the set operation, not about ORDER BY."""
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint'), ('name', 'text')]})
    sql = 'SELECT name AS nm FROM users ORDER BY '
    assert 'nm' in [s.text for s in complete(sql, len(sql), POSTGRES, catalog)]


def test_an_order_by_inside_a_branch_of_a_set_operation_is_untouched() -> None:
    """A parenthesised branch orders itself, and that ORDER BY is its own."""
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint'), ('name', 'text')]})
    sql = 'SELECT * FROM (SELECT name AS nm FROM users ORDER BY ) x'
    caret = sql.index(') x')
    assert 'nm' in [s.text for s in complete(sql, caret, POSTGRES, catalog)]


def test_a_clause_of_the_last_branch_still_answers() -> None:
    """Only the tail is suppressed; the branch's own clauses keep their scope."""
    catalog = MemoryCatalog(
        {
            ('public', 'users'): [('id', 'bigint'), ('name', 'text')],
            ('public', 'orders'): [('id', 'bigint'), ('total', 'numeric')],
        }
    )
    sql = 'SELECT name FROM users UNION SELECT total FROM orders WHERE '
    assert [s.text for s in complete(sql, len(sql), POSTGRES, catalog)][:2] == ['orders.id', 'orders.total']


def test_the_tail_still_offers_the_words_that_finish_its_own_clause() -> None:
    """
    The suppression was about which *names* resolve, and it silenced keywords too.

    `ASC`, `DESC`, `NULLS LAST`, `LIMIT`, and the six words that finish
    `FETCH FIRST n ROWS ONLY` carry no column reference, so the three-way
    disagreement that made this position answer nothing does not reach them —
    all three servers accept `... UNION ... ORDER BY id DESC LIMIT 5`. The engine
    was refusing to complete a clause it had just suggested.
    """
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint')], ('public', 'orders'): [('id', 'bigint')]})
    head = 'SELECT id FROM users UNION SELECT id FROM orders '
    assert 'DESC' in [s.text for s in complete(f'{head}ORDER BY id ', len(head) + 12, POSTGRES, catalog, limit=40)]
    assert 'FIRST' in [s.text for s in complete(f'{head}FETCH ', len(head) + 6, POSTGRES, catalog, limit=40)]


def test_the_tail_begins_after_its_own_keyword() -> None:
    """A caret on or inside `ORDER BY` is completing that keyword, not sitting past it."""
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint')], ('public', 'orders'): [('id', 'bigint')]})
    sql = 'SELECT id FROM users UNION SELECT id FROM orders ORDER BY id'
    assert complete(sql, sql.index('ORDER BY'), POSTGRES, catalog), 'a caret before the keyword is not in the tail'
    assert 'BY' in [s.text for s in complete(sql, sql.index('ORDER BY') + 6, POSTGRES, catalog, limit=40)]


def test_the_tail_stays_suppressed_inside_parentheses() -> None:
    """
    This asserted the opposite, on a premise the server does not share.

    It read: inside a parenthesised subquery the caret is in an ordinary query
    with its own FROM, so the guard should step aside. Postgres refuses the whole
    shape — `ORDER BY (SELECT 1)` after a UNION is `invalid
    UNION/INTERSECT/EXCEPT ORDER BY clause: only result column names can be used,
    not expressions or functions` — so there was nothing to step aside for.

    And `depth_at` is true of *any* group, not only a query's, so the escape
    reopened `ORDER BY abs(<caret>)` as well, where the last branch's columns came
    back: the exact answer this refuses, and one ClickHouse accepts and then
    sorts the wrong rows by.
    """
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint')], ('public', 'orders'): [('id', 'bigint')]})
    head = 'SELECT id FROM users UNION SELECT id FROM orders ORDER BY '
    for tail in ('abs()', '(SELECT )'):
        sql = f'{head}{tail}'
        assert complete(sql, len(sql) - 1, POSTGRES, catalog) == [], tail
