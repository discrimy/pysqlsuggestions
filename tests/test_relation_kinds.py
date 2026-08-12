"""
Which relations a position means, when "not a sequence" is too coarse.

`DROP TABLE reports_active` is refused — `"reports_active" is not a table` —
and the engine offered it. `DROP VIEW` wants the opposite set, and neither can
be expressed by a filter with one exclusion in it.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
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
