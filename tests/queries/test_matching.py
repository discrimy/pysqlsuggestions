"""Which suggestions a prefix reaches, and in what order."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import CTE_SQL, fake_catalog, texts


def test_cte_qualified_prefix_filters(cur: MemoryCatalog) -> None:
    """A prefix narrows a qualified CTE reference like any other."""
    sql = CTE_SQL + 'em'
    assert texts(cur, sql) == ['email']


def test_cte_name_ranks_before_catalog_tables(cur: MemoryCatalog) -> None:
    """It ranks above catalog tables: the statement declared it."""
    sql = 'WITH orders_x as (select * from orders)\nSELECT * FROM o'
    assert texts(cur, sql)[0] == 'orders_x'


def test_prefix_filtering_on_plain_table(cur: MemoryCatalog) -> None:
    """A prefix narrows them."""
    assert texts(cur, 'select * from auth_user u where u.em') == ['email']


def test_cte_column_prefix_is_case_insensitive(cur: MemoryCatalog) -> None:
    """Matching a CTE column ignores case."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.EM'
    assert texts(cur, sql) == ['email']


def test_substring_match_offers_the_table(cur: MemoryCatalog) -> None:
    """A substring finds a relation."""
    assert 'auth_user' in texts(cur, 'select * from use')


def test_prefix_hits_rank_above_substring_hits(cur: MemoryCatalog) -> None:
    """A prefix outranks a substring."""
    got = texts(cur, 'select * from use')
    assert got.index('users_log') < got.index('auth_user')


def test_exact_case_prefix_ranks_first(cur: MemoryCatalog) -> None:
    """An exact-case prefix outranks a folded one."""
    got = texts(cur, 'select * from Us')
    assert got[0] == 'users_log'


def test_substring_match_on_columns(cur: MemoryCatalog) -> None:
    """A substring finds a column."""
    assert texts(cur, 'select * from auth_user u where u.mail') == ['email']


def test_column_prefix_hit_ranks_above_substring_hit(cur: MemoryCatalog) -> None:
    """And a prefix outranks it there too."""
    got = texts(cur, 'select * from orders where id', limit=10)
    assert got.index('orders.id') < got.index('orders.user_id')


def test_substring_match_on_cte_names(cur: MemoryCatalog) -> None:
    """A substring finds a CTE name."""
    sql = 'WITH monthly_totals AS (SELECT id FROM orders)\nSELECT * FROM total'
    assert 'monthly_totals' in texts(cur, sql)


def test_earlier_substring_position_ranks_higher(cur: MemoryCatalog) -> None:
    """An earlier substring outranks a later one."""
    got = texts(cur, 'select * from auth_user u where u.e', limit=10)
    assert got == ['email', 'username', 'date_joined']


def test_keywords_stay_prefix_only(cur: MemoryCatalog) -> None:
    """Keywords match by prefix only: `her` must not reach WHERE."""
    assert 'where' in texts(cur, 'select * from auth_user w', limit=50)
    assert 'WHERE' not in texts(cur, 'select * from auth_user her', limit=50)


def test_empty_prefix_is_unchanged(cur: MemoryCatalog) -> None:
    """An empty prefix filters nothing."""
    got = texts(cur, 'select * from ', limit=50)
    assert {'auth_user', 'auth_group', 'orders', 'users_log'} <= set(got)


def test_no_match_returns_nothing(cur: MemoryCatalog) -> None:
    """Nothing matching means nothing offered."""
    assert texts(cur, 'select * from zzzqqq') == []


def test_substring_match_does_not_cross_the_dot(cur: MemoryCatalog) -> None:
    """A substring match does not run across the dot of a qualified name."""
    assert texts(cur, 'select * from auth_group g where g.mail') == []


def test_columns_before_any_from_use_the_whole_schema_read(cur: MemoryCatalog) -> None:
    """Before any FROM, a small schema is read whole."""
    got = texts(cur, 'select ema', limit=20)
    assert 'auth_user.email' in got
    assert ('all_columns',) in cur.calls
    assert not any(call[0] == 'search_columns' for call in cur.calls)


def test_columns_before_any_from_are_prefix_filtered(cur: MemoryCatalog) -> None:
    """And narrowed by what was typed."""
    got = texts(cur, 'select user_i', limit=20)
    assert got == ['orders.user_id']


def test_an_oversized_schema_falls_back_to_the_prefix_query() -> None:
    """A schema too large to enumerate falls back to the prefix query."""
    cur = fake_catalog(oversized=True)
    got = texts(cur, 'select ema', limit=20)
    assert 'auth_user.email' in got
    assert any(call[0] == 'search_columns' for call in cur.calls)
