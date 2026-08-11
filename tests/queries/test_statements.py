"""Statement shape: where one ends, what a clause governs, what a span covers."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import analyze, suggestions, texts


def test_analyze_replace_from_is_after_the_dot(cur: MemoryCatalog) -> None:
    """
    The span decides what an insertion overwrites. Starting it before the dot
    would make accepting `email` produce `where email`, dropping the qualifier
    the author had already typed — the reason the span travels with the
    suggestion rather than being re-derived from a word boundary.
    """
    ctx = analyze('select * from auth_user u where u.em')
    assert ctx.replace_from == len('select * from auth_user u where u.')
    assert ctx.prefix == 'em'


def test_with_is_not_confused_by_other_uses(cur: MemoryCatalog) -> None:
    """
    `WITH` ends a `current_timestamp with time zone` as readily as it opens a
    CTE. Reading this one as a CTE header invents a relation from whatever
    follows, and every column suggestion after it is then wrong.
    """
    sql = 'select * from auth_user u where u.date_joined = current_timestamp with '
    # should not raise, and should not invent relations
    suggestions(cur, sql)
    ctx = analyze(sql)
    assert [r.name for r in ctx.relations] == ['auth_user']


def test_cursor_in_the_middle_of_the_statement(cur: MemoryCatalog) -> None:
    """
    Analysis reads the whole statement, not the text to the left. The trailing
    `AND 1 = 1` must neither be ignored nor mistaken for part of the reference
    being completed.
    """
    head = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.'
    sql = head + ' AND 1 = 1'
    assert sorted(texts(cur, sql, pos=len(head))) == ['email', 'id']


def test_second_statement_does_not_see_the_first(cur: MemoryCatalog) -> None:
    """
    An editor holds a file, not a statement. Merging the two scopes offers
    `auth_user`'s columns in a query that cannot reference them.
    """
    sql = 'SELECT * FROM auth_user; SELECT * FROM orders WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_excluded_offers_the_target_columns(cur: MemoryCatalog) -> None:
    """
    Postgres exposes the row it could not insert as a relation shaped like the
    target, so this is the one qualifier whose meaning comes from the statement
    form rather than from the FROM list. `created` is absent because a date
    cannot be assigned to `total`.
    """
    sql = 'INSERT INTO orders (id) VALUES (1) ON CONFLICT (id) DO UPDATE SET total = EXCLUDED.'
    assert sorted(texts(cur, sql)) == ['id', 'total', 'user_id']


def test_partition_by_sees_the_relation_after_the_cursor(cur: MemoryCatalog) -> None:
    """
    The window spec is written before the FROM clause it depends on, which is
    the clearest case for reading the whole statement: the relation that answers
    this is entirely to the right of the caret.
    """
    sql = 'SELECT row_number() OVER (PARTITION BY ) FROM auth_group'
    pos = sql.index(') FROM')
    assert sorted(texts(cur, sql, pos=pos, limit=50)) == ['auth_group.id', 'auth_group.name']
