"""Which suggestions a prefix reaches, and in what order."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import CTE_SQL, fake_catalog, texts


def test_cte_qualified_prefix_filters(cur: MemoryCatalog) -> None:
    """
    A CTE's columns come from the statement rather than the catalog, and they
    still have to go through the same matching. A separate path for them is how
    one of the two silently stops filtering.
    """
    sql = CTE_SQL + 'em'
    assert texts(cur, sql) == ['email']


def test_cte_name_ranks_before_catalog_tables(cur: MemoryCatalog) -> None:
    """
    The author declared it three lines up; the catalog's `orders` they may never
    have seen. Recency of intent beats alphabetical order, and a plain name
    match would put `orders` first.
    """
    sql = 'WITH orders_x as (select * from orders)\nSELECT * FROM o'
    assert texts(cur, sql)[0] == 'orders_x'


def test_prefix_filtering_on_plain_table(cur: MemoryCatalog) -> None:
    """The baseline the other matching tests are measured against."""
    assert texts(cur, 'select * from auth_user u where u.em') == ['email']


def test_cte_column_prefix_is_case_insensitive(cur: MemoryCatalog) -> None:
    """
    `EM` finds `email` because unquoted identifiers fold. Matching before folding
    — or folding only the catalog side — makes shouted typing find nothing.
    """
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.EM'
    assert texts(cur, sql) == ['email']


def test_substring_match_offers_the_table(cur: MemoryCatalog) -> None:
    """
    `use` finds `auth_user`. Prefix-only matching would not, and the suite this
    library replaced already had users relying on it: nobody types `auth_` to
    reach a table they think of as "user".
    """
    assert 'auth_user' in texts(cur, 'select * from use')


def test_prefix_hits_rank_above_substring_hits(cur: MemoryCatalog) -> None:
    """
    Which is why substring is the weakest tier rather than no tier at all. Both
    match; the one that starts with what was typed is the one meant.
    """
    got = texts(cur, 'select * from use')
    assert got.index('users_log') < got.index('auth_user')


def test_exact_case_prefix_ranks_first(cur: MemoryCatalog) -> None:
    """
    Case that was typed is evidence too. `Us` matching `users_log` exactly
    outranks the same match folded, so the shift key is not wasted.
    """
    got = texts(cur, 'select * from Us')
    assert got[0] == 'users_log'


def test_substring_match_on_columns(cur: MemoryCatalog) -> None:
    """
    `mail` finds `email` — the case that makes substring matching worth its
    risks, and the one most often cited by the users of the suite this replaced.
    """
    assert texts(cur, 'select * from auth_user u where u.mail') == ['email']


def test_column_prefix_hit_ranks_above_substring_hit(cur: MemoryCatalog) -> None:
    """
    The same ordering among columns. `id` is a prefix of one and inside the
    other, and a three-character query should not bury the exact answer.
    """
    got = texts(cur, 'select * from orders where id', limit=10)
    assert got.index('orders.id') < got.index('orders.user_id')


def test_substring_match_on_cte_names(cur: MemoryCatalog) -> None:
    """
    Every kind goes through the same tiers, so a CTE name is reachable by
    substring exactly as a table is.
    """
    sql = 'WITH monthly_totals AS (SELECT id FROM orders)\nSELECT * FROM total'
    assert 'monthly_totals' in texts(cur, sql)


def test_earlier_substring_position_ranks_higher(cur: MemoryCatalog) -> None:
    """
    Among substring matches, how late the match starts is the only signal left.
    `e` opens `email`, sits inside `username`, and is later still in
    `date_joined` — which is the order they come back in.
    """
    got = texts(cur, 'select * from auth_user u where u.e', limit=10)
    assert got == ['email', 'username', 'date_joined']


def test_keywords_stay_prefix_only(cur: MemoryCatalog) -> None:
    """
    There are a few hundred keywords and they are not what anyone is hunting for.
    Letting them through the substring tier means `her` offers WHERE, which
    buries the columns that were actually wanted.
    """
    assert 'where' in texts(cur, 'select * from auth_user w', limit=50)
    assert 'WHERE' not in texts(cur, 'select * from auth_user her', limit=50)


def test_empty_prefix_is_unchanged(cur: MemoryCatalog) -> None:
    """
    With nothing typed, nothing is filtered. A matcher that scores an empty
    prefix as a weak match rather than a free pass drops everything.
    """
    got = texts(cur, 'select * from ', limit=50)
    assert {'auth_user', 'auth_group', 'orders', 'users_log'} <= set(got)


def test_no_match_returns_nothing(cur: MemoryCatalog) -> None:
    """
    And a prefix matching nothing offers nothing, rather than falling back to the
    unfiltered list — which is the failure that makes a completion popup useless
    exactly when it appears to be working.
    """
    assert texts(cur, 'select * from zzzqqq') == []


def test_substring_match_does_not_cross_the_dot(cur: MemoryCatalog) -> None:
    """
    `g.mail` is a column of `auth_group`, which has none. Matching the whole
    dotted text would find `email` on another relation and offer a reference
    that does not resolve.
    """
    assert texts(cur, 'select * from auth_group g where g.mail') == []


def test_columns_before_any_from_use_the_whole_schema_read(cur: MemoryCatalog) -> None:
    """
    Before a FROM exists there is no relation to ask, so the catalog is read
    whole — once, and only when it is small enough to hand over. The call
    recorded here is the difference between one read and one per keystroke.
    """
    got = texts(cur, 'select ema', limit=20)
    assert 'auth_user.email' in got
    assert ('all_columns',) in cur.calls
    assert not any(call[0] == 'search_columns' for call in cur.calls)


def test_columns_before_any_from_are_prefix_filtered(cur: MemoryCatalog) -> None:
    """
    And the result is still narrowed by what was typed, rather than handing the
    whole schema to the ranker and hoping.
    """
    got = texts(cur, 'select user_i', limit=20)
    assert got == ['orders.user_id']


def test_an_oversized_schema_falls_back_to_the_prefix_query() -> None:
    """
    When the schema is too large to enumerate, the prefix goes to the backend
    instead. A completion engine may not scan a database to answer a keystroke,
    and this is the seam where that rule is kept.
    """
    cur = fake_catalog(oversized=True)
    got = texts(cur, 'select ema', limit=20)
    assert 'auth_user.email' in got
    assert any(call[0] == 'search_columns' for call in cur.calls)
