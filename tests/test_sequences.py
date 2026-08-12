"""
A sequence is a relation you may not put in a FROM list.

Not because the server refuses it — `SELECT * FROM auth_user_id_seq` returns
`last_value | log_cnt | is_called` quite happily — but because a schema created
by Django has one sequence per table, and doubling the commonest caret in the
language with names nobody is reaching for is a cost paid on every keystroke.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Table

SNAPSHOT = {
    ('public', 'auth_user'): [('id', 'bigint'), ('email', 'varchar')],
    ('public', 'auth_user_id_seq'): [('last_value', 'bigint')],
    ('billing', 'MonthlyTotals_id_seq'): [('last_value', 'bigint')],
}
KINDS = {('public', 'auth_user_id_seq'): 'sequence', ('billing', 'MonthlyTotals_id_seq'): 'sequence'}


def catalog() -> MemoryCatalog:
    """A snapshot holding one table and two sequences, one of them off the search path."""
    return MemoryCatalog(SNAPSHOT, table_kinds=KINDS, search_path=('public',))


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def test_a_relation_position_never_offers_a_sequence() -> None:
    """
    The assertion the whole filter exists to pass. A Django schema has one
    sequence per table, so without this the commonest caret in the language
    doubles in length with names nobody is reaching for.
    """
    found = offered('SELECT * FROM ')
    assert 'auth_user' in found
    assert 'auth_user_id_seq' not in found


def test_a_prefix_search_does_not_reach_one_either() -> None:
    """
    The search path is not what hides a sequence, so reaching past it must not
    reveal one.

    Asserted as a substring rather than against the list: this name is mixed
    case, so it arrives quoted and qualified — `billing."MonthlyTotals_id_seq"`
    — and an equality check would have passed while the sequence was on offer.
    """
    assert not [text for text in offered('SELECT * FROM Month') if 'MonthlyTotals' in text]


def test_a_schema_qualifier_does_not_list_sequences() -> None:
    """`billing.` lists what you can query in `billing`, which is not everything in it."""
    assert not [text for text in offered('SELECT * FROM billing.') if 'MonthlyTotals' in text]


def test_the_postgres_queries_now_fetch_sequences() -> None:
    """Both paths, because a sequence outside the search path has to be reachable by prefix."""
    tables = POSTGRES.catalog_queries.tables
    search = POSTGRES.catalog_queries.relation_search
    assert tables is not None
    assert search is not None
    assert "'S'" in tables.sql
    assert "'S'" in search.sql
    found = tables.row(('public', 'auth_user_id_seq', 'S', 1))
    assert isinstance(found, Table)
    assert found.kind == 'sequence'
