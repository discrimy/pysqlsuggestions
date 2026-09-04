"""
What the engine does with a column the connected role may not read.

The rule throughout is that it is still offered. A name that vanishes reads as
the engine not knowing about it, where one that arrives last carrying `no SELECT
privilege` says what is actually true — the column exists, and asking for the
grant is a move the user can make.
"""

from __future__ import annotations

from pysqlsuggestions import complete
from pysqlsuggestions.caches import MemoryCache
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Availability, ForeignKey, Kind

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


def test_the_expansion_omits_what_the_role_cannot_read() -> None:
    """`SELECT *` over a partly-restricted relation is refused outright, so the expansion is the fix."""
    sql = 'SELECT * FROM users'
    found = [s for s in complete(sql, 8, POSTGRES, _catalog()) if s.kind is Kind.EXPANSION]
    assert [s.text for s in found] == ['id, email']
    assert found[0].reason == '1 column omitted: no SELECT privilege'


def test_the_expansion_stays_available_despite_the_reason() -> None:
    """It is the one statement at that caret the server accepts; sinking it would bury the answer."""
    sql = 'SELECT * FROM users'
    found = [s for s in complete(sql, 8, POSTGRES, _catalog()) if s.kind is Kind.EXPANSION]
    assert found[0].availability is Availability.AVAILABLE


def test_an_unreadable_relation_expands_to_nothing() -> None:
    """The existing guard covers it: an expansion to nothing would delete the star."""
    catalog = MemoryCatalog({('public', 'secrets'): [('id', 'bigint')]}, restricted={('public', 'secrets'): None})
    sql = 'SELECT * FROM secrets'
    assert not [s for s in complete(sql, 8, POSTGRES, catalog) if s.kind is Kind.EXPANSION]


def test_an_expansion_with_nothing_withheld_says_nothing() -> None:
    """The reason appears only when something was actually dropped."""
    sql = 'SELECT * FROM users'
    found = [s for s in complete(sql, 8, POSTGRES, MemoryCatalog(USERS)) if s.kind is Kind.EXPANSION]
    assert [s.text for s in found] == ['id, email, password']
    assert found[0].reason is None


def test_no_literal_is_drawn_from_a_column_the_role_cannot_read() -> None:
    """The one knock-on where the old behaviour leaked data rather than wasting a keystroke."""
    catalog = MemoryCatalog(
        {('public', 'users'): [('id', 'bigint'), ('password', 'text')]},
        restricted={('public', 'users'): ['password']},
        values={('public', 'users', 'password'): ['hunter2', 'letmein']},
    )
    sql = 'SELECT * FROM users u WHERE u.password = '
    assert not [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.VALUE]


def test_a_self_enumerating_type_is_refused_too() -> None:
    """A boolean's values come from the type rather than the rows — but the comparison still fails."""
    catalog = MemoryCatalog(
        {('public', 'users'): [('id', 'bigint'), ('is_admin', 'boolean')]},
        restricted={('public', 'users'): ['is_admin']},
    )
    sql = 'SELECT * FROM users u WHERE u.is_admin = '
    assert not [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.VALUE]


def test_an_unrestricted_column_still_offers_its_values() -> None:
    """The rule must not cost the feature it guards."""
    catalog = MemoryCatalog(
        {('public', 'users'): [('state', 'text')]},
        restricted={('public', 'users'): ['nothing_by_this_name']},
        values={('public', 'users', 'state'): ['active']},
    )
    sql = 'SELECT * FROM users u WHERE u.state = '
    found = [s.text for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.VALUE]
    assert found == ["'active'"]


JOINED = {
    ('public', 'orders'): [('id', 'bigint'), ('user_id', 'bigint')],
    ('public', 'users'): [('id', 'bigint')],
    ('public', 'secrets'): [('id', 'bigint'), ('order_id', 'bigint')],
}

EDGES = [
    ForeignKey('public', 'orders', ('user_id',), 'public', 'users', ('id',)),
    ForeignKey('public', 'secrets', ('order_id',), 'public', 'orders', ('id',)),
]


def test_a_join_to_an_unreadable_relation_sinks_but_stays() -> None:
    """The constraint is real and the user's next move may be to ask for the grant."""
    catalog = MemoryCatalog(JOINED, restricted={('public', 'secrets'): None}, foreign_keys=EDGES)
    sql = 'SELECT * FROM orders o JOIN '
    found = [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.JOIN]
    assert 'users' in found[0].text
    assert 'secrets' in found[-1].text
    assert found[-1].availability is Availability.RESTRICTED
    assert found[-1].reason == 'no SELECT privilege'
    assert found[-1].note is not None, 'the fk annotation must survive alongside the reason'


def test_a_join_to_a_readable_relation_is_untouched() -> None:
    """The degradation again: no `restricted` means join proposals behave exactly as before."""
    catalog = MemoryCatalog(JOINED, foreign_keys=EDGES)
    sql = 'SELECT * FROM orders o JOIN '
    found = [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.JOIN]
    assert all(s.availability is Availability.AVAILABLE for s in found)
    assert all(s.reason is None for s in found)


def test_one_request_reads_the_relation_list_once() -> None:
    """Availability must not cost a second round trip on a caller with no cache."""
    catalog = MemoryCatalog(JOINED, foreign_keys=EDGES)
    sql = 'SELECT * FROM orders o JOIN '
    complete(sql, len(sql), POSTGRES, catalog)
    assert len([call for call in catalog.calls if call[0] == 'tables']) == 1


def _permissive() -> MemoryCatalog:
    """A role that may read the password column. The mapping names the relation, so nothing is UNKNOWN."""
    return MemoryCatalog(USERS, restricted={('public', 'users'): []})


def _restrictive() -> MemoryCatalog:
    """The same database, a role that may not."""
    return MemoryCatalog(USERS, restricted={('public', 'users'): ['password']})


def test_one_cache_two_roles_do_not_leak() -> None:
    """
    `role` has led the documented cache key since v0.1 on an argument alone.

    This is the first feature that gives it meaning, and the failure it prevents
    is silent: user A's readable set served to user B reads as a database
    privilege bug rather than a caching one, which is why it belongs in CI
    rather than in a paragraph.
    """
    shared = MemoryCache()
    sql = 'SELECT * FROM users u WHERE u.'
    for catalog, identity, expected in (
        (_permissive(), 'alice', Availability.AVAILABLE),
        (_restrictive(), 'bob', Availability.RESTRICTED),
        (_permissive(), 'alice', Availability.AVAILABLE),
    ):
        found = complete(sql, len(sql), POSTGRES, catalog, cache=shared, identity=identity)
        password = next(s for s in found if s.text == 'password')
        assert password.availability is expected, f'{identity} saw the wrong readable set'


def test_an_unnamed_role_still_gets_its_own_line_in_the_key() -> None:
    """identity=None is a role like any other, not a wildcard matching every entry."""
    shared = MemoryCache()
    sql = 'SELECT * FROM users u WHERE u.'
    complete(sql, len(sql), POSTGRES, _permissive(), cache=shared)
    found = complete(sql, len(sql), POSTGRES, _restrictive(), cache=shared, identity='bob')
    assert next(s for s in found if s.text == 'password').availability is Availability.RESTRICTED
