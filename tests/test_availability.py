"""
What the engine does with a column the connected role may not read.

The rule throughout is that it is still offered. A name that vanishes reads as
the engine not knowing about it, where one that arrives last carrying `no SELECT
privilege` says what is actually true — the column exists, and asking for the
grant is a move the user can make.
"""

from __future__ import annotations

from pysqlsuggestions import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Availability, Kind

USERS = {('public', 'users'): [('id', 'bigint'), ('email', 'text'), ('password', 'text')]}


def _catalog() -> MemoryCatalog:
    """`analyst`'s view of the fixture: everything but the password."""
    return MemoryCatalog(USERS, restricted={('public', 'users'): ['password']})


def test_a_restricted_column_is_offered_last_and_says_why() -> None:
    """Still offered: the name exists, and vanishing reads as the engine not knowing it."""
    sql = 'SELECT * FROM users u WHERE u.'
    found = complete(sql, len(sql), POSTGRES, _catalog())
    assert [s.text for s in found] == ['id', 'email', 'password']
    assert found[-1].availability is Availability.RESTRICTED
    assert found[-1].reason == 'no SELECT privilege'
    assert all(s.availability is Availability.AVAILABLE for s in found[:-1])


def test_an_unreadable_relation_sinks_in_a_from_list() -> None:
    """has_any_column_privilege false: nothing in it can be read, so it loses to what can."""
    catalog = MemoryCatalog(
        {('public', 'users'): [('id', 'bigint')], ('public', 'secrets'): [('id', 'bigint')]},
        restricted={('public', 'secrets'): None},
    )
    sql = 'SELECT * FROM '
    found = [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.TABLE]
    assert [s.text for s in found] == ['users', 'secrets']
    assert found[-1].availability is Availability.RESTRICTED


def test_a_catalog_that_says_nothing_changes_nothing() -> None:
    """The degradation, asserted: a snapshot with no `restricted` behaves as it always did."""
    sql = 'SELECT * FROM users u WHERE u.'
    found = complete(sql, len(sql), POSTGRES, MemoryCatalog(USERS))
    assert [s.text for s in found] == ['id', 'email', 'password']
    assert all(s.availability is Availability.UNKNOWN for s in found)
    assert all(s.reason is None for s in found)
