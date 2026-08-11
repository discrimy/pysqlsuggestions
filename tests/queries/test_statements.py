"""Statement shape: where one ends, what a clause governs, what a span covers."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import analyze, suggestions, texts


def test_analyze_replace_from_is_after_the_dot(cur: MemoryCatalog) -> None:
    """The span starts after the dot, so the qualifier survives insertion."""
    ctx = analyze('select * from auth_user u where u.em')
    assert ctx.replace_from == len('select * from auth_user u where u.')
    assert ctx.prefix == 'em'


def test_with_is_not_confused_by_other_uses(cur: MemoryCatalog) -> None:
    """WITH outside a CTE position is not read as one."""
    sql = 'select * from auth_user u where u.date_joined = current_timestamp with '
    # should not raise, and should not invent relations
    suggestions(cur, sql)
    ctx = analyze(sql)
    assert [r.name for r in ctx.relations] == ['auth_user']


def test_cursor_in_the_middle_of_the_statement(cur: MemoryCatalog) -> None:
    """A caret in the middle of the statement, with text after it."""
    head = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.'
    sql = head + ' AND 1 = 1'
    assert sorted(texts(cur, sql, pos=len(head))) == ['email', 'id']


def test_second_statement_does_not_see_the_first(cur: MemoryCatalog) -> None:
    """A semicolon separates scopes."""
    sql = 'SELECT * FROM auth_user; SELECT * FROM orders WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_excluded_offers_the_target_columns(cur: MemoryCatalog) -> None:
    """EXCLUDED in ON CONFLICT DO UPDATE mirrors the insert target."""
    sql = 'INSERT INTO orders (id) VALUES (1) ON CONFLICT (id) DO UPDATE SET total = EXCLUDED.'
    assert sorted(texts(cur, sql)) == ['id', 'total', 'user_id']


def test_partition_by_sees_the_relation_after_the_cursor(cur: MemoryCatalog) -> None:
    """PARTITION BY sees a relation named after the caret."""
    sql = 'SELECT row_number() OVER (PARTITION BY ) FROM auth_group'
    pos = sql.index(') FROM')
    assert sorted(texts(cur, sql, pos=pos, limit=50)) == ['auth_group.id', 'auth_group.name']
