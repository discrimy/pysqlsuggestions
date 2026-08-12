"""
Which relations a position means, when "not a sequence" is too coarse.

`DROP TABLE reports_active` is refused — `"reports_active" is not a table` —
and the engine offered it. `DROP VIEW` wants the opposite set, and neither can
be expressed by a filter with one exclusion in it.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {
    ('public', 'auth_user'): [('id', 'bigint')],
    ('public', 'reports_active'): [('id', 'bigint')],
    ('public', 'auth_user_id_seq'): [('last_value', 'bigint')],
}
KINDS = {('public', 'reports_active'): 'view', ('public', 'auth_user_id_seq'): 'sequence'}


def catalog() -> MemoryCatalog:
    """A table, a view and a sequence — the three kinds a position must tell apart."""
    return MemoryCatalog(SNAPSHOT, table_kinds=KINDS, search_path=('public',))


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def test_a_relation_position_is_unchanged() -> None:
    """
    The regression this is shaped around. A view is queryable, so `FROM ⌶` must
    keep offering it; a sequence is not, and must keep being left out.
    """
    found = offered('SELECT * FROM ')
    assert 'auth_user' in found
    assert 'reports_active' in found
    assert 'auth_user_id_seq' not in found


def test_dropping_a_view_offers_views_only() -> None:
    """`DROP VIEW auth_user` is refused: `"auth_user" is not a view`."""
    found = offered('DROP VIEW ')
    assert 'reports_active' in found
    assert 'auth_user' not in found


def test_dropping_a_table_no_longer_offers_a_view() -> None:
    """
    Server-verified: `DROP TABLE public.reports_active` is refused with
    `"reports_active" is not a table`, and this position offered it.
    """
    found = offered('DROP TABLE ')
    assert 'auth_user' in found
    assert 'reports_active' not in found


def test_dropping_a_materialized_view_wants_that_kind() -> None:
    """A materialized view is not a view to `DROP VIEW`, nor a table to `DROP TABLE`."""
    snapshot = dict(SNAPSHOT)
    snapshot['public', 'monthly_totals'] = [('total', 'numeric')]
    kinds = dict(KINDS)
    kinds['public', 'monthly_totals'] = 'materialized view'
    sql = 'DROP MATERIALIZED VIEW '
    found = [s.text for s in complete(sql, len(sql), POSTGRES, MemoryCatalog(snapshot, table_kinds=kinds))]
    # The schema is offered too, and belongs: `DROP MATERIALIZED VIEW public.x`
    # is how you name one outside the search path.
    assert 'monthly_totals' in found
    assert 'auth_user' not in found
    assert 'reports_active' not in found


def test_clickhouse_keeps_every_relation_at_drop_table() -> None:
    """
    ClickHouse reports storage engines — `mergetree`, `replacingmergetree` — so
    a positive list naming `table` would empty this position there. It inherits
    ANSI's unnarrowed clause, which is why the narrowing lives in postgres.py.
    """
    engines = MemoryCatalog(
        {('analytics', 'report_events'): [('id', 'bigint')]},
        table_kinds={('analytics', 'report_events'): 'mergetree'},
    )
    sql = 'DROP TABLE '
    assert 'report_events' in [s.text for s in complete(sql, len(sql), CLICKHOUSE, engines)]


def test_postgres_replaces_ansis_drop_table_rather_than_adding_one() -> None:
    """
    Invisible to the set-difference test next door, because `extend` replaces a
    clause of the same name. The replacement is the whole point here: ANSI's
    `DROP TABLE` names no kinds, because the baseline cannot know a backend's
    vocabulary, and Postgres's names the three relkinds the statement accepts.
    """
    ansi = ANSI.clauses.get('DROP TABLE')
    postgres = POSTGRES.clauses.get('DROP TABLE')
    assert ansi is not None
    assert postgres is not None
    assert ansi.relation_kinds == ()
    assert postgres.relation_kinds == ('table', 'partitioned table', 'foreign table')
