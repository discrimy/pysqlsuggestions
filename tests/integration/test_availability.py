"""
Availability against the real server, connected as the restricted role.

Everything here needs `analyst` rather than the owner, because
`has_column_privilege` evaluates against the connection's own role and the owner
sees nothing restricted. `docker/postgres/03-roles.sql` seeds the three cases:
a column withheld individually, a relation whose `SELECT *` errors while named
columns work, and a relation with no grant at all.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions import complete
from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Availability, Kind

pytestmark = pytest.mark.integration


def test_a_withheld_column_is_restricted(analyst_catalog: DbapiCatalog) -> None:
    """`reports_database.password` is granted to the owner and not to this role."""
    found = {c.name: c.availability for c in analyst_catalog.columns('public', 'reports_database')}
    assert found['password'] is Availability.RESTRICTED
    assert found['title'] is Availability.AVAILABLE


def test_the_owner_sees_nothing_restricted(postgres_catalog: DbapiCatalog) -> None:
    """The same column, the same query, a different connection: the server decides, not us."""
    found = {c.name: c.availability for c in postgres_catalog.columns('public', 'reports_database')}
    assert found['password'] is Availability.AVAILABLE


def test_a_relation_with_no_grant_is_restricted(analyst_catalog: DbapiCatalog) -> None:
    """has_any_column_privilege false — the relation half, which MemoryCatalog alone cannot prove."""
    tables = {t.name: t.availability for t in analyst_catalog.tables('public')}
    assert tables['reports_phonenumber'] is Availability.RESTRICTED
    assert tables['reports_database'] is Availability.AVAILABLE


def test_an_index_and_a_sequence_report_unknown(analyst_catalog: DbapiCatalog) -> None:
    """The relkind guard: the question does not apply, so the answer must not be a guess."""
    unfit = [t for t in analyst_catalog.tables('public') if t.kind in {'index', 'sequence'}]
    assert unfit, 'the fixture should hold both, or this asserts nothing'
    assert all(t.availability is Availability.UNKNOWN for t in unfit)


def test_the_expansion_omits_the_ungranted_columns(analyst_catalog: DbapiCatalog) -> None:
    """`mattermost_mattermostchannel` is seeded for this: SELECT * errors, id and name work."""
    sql = 'SELECT * FROM mattermost_mattermostchannel'
    found = [s for s in complete(sql, 8, POSTGRES, analyst_catalog) if s.kind is Kind.EXPANSION]
    assert found
    assert found[0].text == 'id, name'
    assert found[0].reason is not None
    assert found[0].availability is Availability.AVAILABLE


def test_a_restricted_column_is_offered_last(analyst_catalog: DbapiCatalog) -> None:
    """End to end against the server: the whole feature in one caret."""
    sql = 'SELECT * FROM reports_database d WHERE d.'
    found = complete(sql, len(sql), POSTGRES, analyst_catalog)
    assert found[-1].text == 'password'
    assert found[-1].availability is Availability.RESTRICTED
    assert found[-1].reason == 'no SELECT privilege'
