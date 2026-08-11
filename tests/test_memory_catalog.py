"""
The shipped snapshot catalog, including the three-level case.

`MemoryCatalog` is documented as the bridge for callers who pre-fetch — an
async application, a browser, anything that cannot block on I/O mid-keystroke.
A Trino user pre-fetching into one hits a namespace with three levels, and the
`Catalog` port passes exactly one name at each: the catalog names the schemas
below it, and a schema names its relations.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import ForeignKey
from tests.corpus.cases import split_caret

SNAPSHOT = {
    ('public', 'flight'): [('id', 'bigint'), ('number', 'varchar')],
    ('revenue', 'invoice'): [('id', 'bigint'), ('amount', 'numeric')],
    ('analytics', 'flight_event'): [('flight_id', 'bigint'), ('gate', 'varchar')],
}

CATALOGS = {'warehouse': ['public', 'revenue'], 'events': ['analytics']}


def federated() -> MemoryCatalog:
    """A snapshot whose schemas belong to named catalogs."""
    return MemoryCatalog(SNAPSHOT, catalogs=CATALOGS)


def texts(marked: str, catalog: MemoryCatalog, dialect: object = TRINO) -> list[str]:
    """Suggestion texts for ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return [s.text for s in complete(sql, caret, dialect, catalog)]  # type: ignore[arg-type]


def test_one_level_down_from_nothing_is_a_catalog() -> None:
    """
    With three levels a bare `FROM ` wants catalogs.

    Offering schemas there would put the second level in the first position —
    `public` where `warehouse` belongs.
    """
    assert federated().schemas() == ['events', 'warehouse']
    assert texts('SELECT * FROM ⌶', federated()) == ['events', 'warehouse']


def test_a_catalog_names_the_schemas_below_it() -> None:
    """`warehouse.` is the second level, and only that catalog's schemas belong there."""
    assert federated().schemas('warehouse') == ['public', 'revenue']
    assert federated().schemas('events') == ['analytics']
    assert texts('SELECT * FROM warehouse.⌶', federated()) == ['public', 'revenue']


def test_a_schema_names_its_relations() -> None:
    """`warehouse.public.` reaches the third level, where the port passes the schema alone."""
    assert [t.name for t in federated().tables('public')] == ['flight']
    assert texts('SELECT * FROM warehouse.public.⌶', federated()) == ['flight']


def test_there_is_no_default_relation_set() -> None:
    """
    Three levels leave no useful "visible by default": a bare position wants
    catalogs, and enumerating every relation of every catalog is what the live
    adapter deliberately declines to do.
    """
    assert federated().tables() == []


def test_columns_are_reached_through_the_deepest_two_segments() -> None:
    """`catalog.schema.table.` is a column position; the catalog has done its work by then."""
    assert texts('SELECT * FROM warehouse.public.flight f WHERE f.⌶', federated()) == ['id', 'number']


def test_a_snapshot_without_catalogs_is_unchanged() -> None:
    """The two-level case is the common one and must not have moved."""
    plain = MemoryCatalog(SNAPSHOT)
    assert plain.schemas() == ['analytics', 'public', 'revenue']
    assert [t.name for t in plain.tables()] == ['flight', 'invoice', 'flight_event']
    assert texts('SELECT * FROM ⌶', plain, POSTGRES)[:3] == ['flight', 'invoice', 'flight_event']


def test_foreign_keys_are_declared_and_filtered_by_schema() -> None:
    """A fixture declares edges; the port hands back the ones the schema owns."""
    edge = ForeignKey(
        schema='public',
        table='orders',
        columns=('user_id',),
        ref_schema='public',
        ref_table='users',
        ref_columns=('id',),
    )
    billing = ForeignKey(
        schema='billing',
        table='invoices',
        columns=('order_id',),
        ref_schema='public',
        ref_table='orders',
        ref_columns=('id',),
    )
    catalog = MemoryCatalog(
        {('public', 'orders'): [('id', 'bigint')], ('public', 'users'): [('id', 'bigint')]},
        foreign_keys=[edge, billing],
    )
    assert list(catalog.foreign_keys('public')) == [edge]
    assert list(catalog.foreign_keys(None)) == [edge, billing]


def test_foreign_keys_default_to_none_declared() -> None:
    """The overwhelming majority of fixtures declare none, and must behave exactly as before."""
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint')]})
    assert list(catalog.foreign_keys(None)) == []
