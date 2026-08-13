"""
Profile to catalog, without a database in sight.

The two properties that matter here are both about *when* things happen:
nothing connects until a completion asks it to, and what does connect is
reused. An editor session that opened a file must not have opened a socket.
"""

from __future__ import annotations

import threading
import time
from importlib import import_module
from typing import Any

from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions_lsp.connections import DRIVERS, Profile, open_catalog

PROFILE = Profile(dialect='postgres', host='localhost', port=5432, database='app', user='ana', password='secret')

PARAMSTYLES = ('qmark', 'format', 'numeric', 'named', 'pyformat')
"""What `dbapi.render` knows how to rewrite. Anything else raises mid-read."""


class FakeCursor:
    """Answers every query with nothing, which is a valid catalog answer."""

    def execute(self, operation: str, parameters: Any = None) -> None:
        """Record nothing; the catalog only cares that this does not raise."""

    def fetchall(self) -> list[Any]:
        """No rows."""
        return []


class FakeConnection:
    """Counts the cursors it was asked for."""

    def __init__(self) -> None:
        self.cursors = 0

    def cursor(self) -> FakeCursor:
        """A fresh cursor, as a driver would give."""
        self.cursors += 1
        return FakeCursor()


def test_options_become_a_profile() -> None:
    """The client sends a dict; the server needs a typed thing."""
    options = {'dialect': 'postgres', 'host': 'db', 'port': 5432, 'database': 'app', 'user': 'ana'}
    profile = Profile.from_options(options)
    assert profile is not None
    assert (profile.dialect, profile.host, profile.port) == ('postgres', 'db', 5432)


def test_options_without_a_dialect_are_no_profile() -> None:
    """No dialect means no catalog, which is the documented degraded mode."""
    assert Profile.from_options({'host': 'db'}) is None


def test_options_without_a_host_are_no_profile() -> None:
    """Half a profile is not a profile."""
    assert Profile.from_options({'dialect': 'postgres'}) is None


def test_no_options_at_all_are_no_profile() -> None:
    """A client that sent nothing gets a working server, not a failed one."""
    assert Profile.from_options(None) is None


def test_a_port_of_the_wrong_type_is_dropped_rather_than_raising() -> None:
    """initializationOptions is whatever the client sent, and is not to be trusted."""
    profile = Profile.from_options({'dialect': 'postgres', 'host': 'db', 'port': '5432'})
    assert profile is not None
    assert profile.port is None


def test_a_profile_does_not_print_its_password() -> None:
    """repr reaches logs and crash reports."""
    assert 'secret' not in repr(PROFILE)


def test_a_known_dialect_gives_a_catalog() -> None:
    """Postgres is registered and has a pure-Python driver, so it is served."""
    assert isinstance(open_catalog(PROFILE, connect=lambda profile: FakeConnection()), DbapiCatalog)


def test_an_unknown_dialect_gives_nothing() -> None:
    """Nothing here can serve it, and saying so is how the caller degrades."""
    profile = Profile(dialect='oracle', host='db')
    assert open_catalog(profile, connect=lambda p: FakeConnection()) is None


def test_a_registered_dialect_with_no_bundled_driver_gives_nothing() -> None:
    """
    A dialect resolving is not enough — something has to be able to read it.

    `ansi` is the case now that ClickHouse and Trino have readers: it describes
    a grammar and names no server, so there is nothing to connect to and no
    driver to name. The same shape is what a third-party dialect registered
    through the entry point gets.
    """
    profile = Profile(dialect='ansi', host='db')
    assert open_catalog(profile, connect=lambda p: FakeConnection()) is None


def test_connecting_is_deferred_until_a_query() -> None:
    """
    Opening a document must not open a socket.

    A database behind a VPN that happens to be down would otherwise hang the
    editor on file open rather than on the first completion.
    """
    opened: list[Profile] = []

    def connect(profile: Profile) -> FakeConnection:
        opened.append(profile)
        return FakeConnection()

    catalog = open_catalog(PROFILE, connect=connect)
    assert catalog is not None
    assert opened == []
    catalog.tables()
    assert opened == [PROFILE]


def test_the_connection_is_reused_across_queries() -> None:
    """One connection per server, not one per catalog read."""
    connections: list[FakeConnection] = []

    def connect(profile: Profile) -> FakeConnection:
        connections.append(FakeConnection())
        return connections[-1]

    catalog = open_catalog(PROFILE, connect=connect)
    assert catalog is not None
    catalog.tables()
    catalog.schemas()
    assert len(connections) == 1


def test_every_declared_driver_names_a_paramstyle_dbapi_accepts() -> None:
    """`render` raises on anything else, from inside a catalog read."""
    for dialect, (module, paramstyle) in DRIVERS.items():
        assert paramstyle in PARAMSTYLES, f'{dialect} ({module}) declares {paramstyle!r}'


def test_one_connection_is_opened_when_queries_arrive_together() -> None:
    """
    `open_cursor` checks whether it has connected and then connects — so two
    callers arriving together both connect, and only the first is ever used
    again. The second is leaked: never closed, still holding a session.

    Driven through the catalog rather than through a `Session`, because the
    fault is in this closure and would otherwise be masked by the server's own
    lock.
    """
    opened: list[FakeConnection] = []
    ready = threading.Barrier(8)

    def connect(profile: Profile) -> FakeConnection:
        time.sleep(0.05)
        connection = FakeConnection()
        opened.append(connection)
        return connection

    catalog = open_catalog(Profile(dialect='postgres', host='db'), connect=connect)
    assert catalog is not None

    def read() -> None:
        ready.wait()
        catalog.schemas()

    threads = [threading.Thread(target=read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(opened) == 1


def test_every_dialect_the_library_serves_has_a_catalog() -> None:
    """
    All three backends read a catalog now, and none of them needs a wheel.

    This was three dialects and one driver: pg8000 is pure and the other two
    clients were not, so ClickHouse and Trino resolved a dialect and no catalog.
    The stdlib readers close that, and the assertion is written against the
    whole set rather than the two additions so that a dialect added without a
    reader is visible here rather than as silence at a caret.
    """
    assert set(DRIVERS) == {'postgres', 'clickhouse', 'trino'}


def test_the_readers_are_reached_by_module_path_not_by_import() -> None:
    """DRIVERS names modules so `connections` itself imports no transport."""
    assert DRIVERS['clickhouse'] == ('pysqlsuggestions.catalogs.clickhouse_http', 'named')
    assert DRIVERS['trino'] == ('pysqlsuggestions.catalogs.trino_http', 'qmark')


def test_each_readers_paramstyle_matches_what_it_declares() -> None:
    """
    A paramstyle written twice is a paramstyle that can disagree with itself.

    `DbapiCatalog` is told the value from DRIVERS while the reader rewrites
    against the one it declares, and a mismatch produces valid-looking SQL with
    unsubstituted markers in it — which surfaces as an empty completion list,
    not as an error.
    """
    for dialect, (module, paramstyle) in DRIVERS.items():
        if module.startswith('pysqlsuggestions.'):
            assert import_module(module).paramstyle == paramstyle, dialect


def test_a_clickhouse_profile_opens_a_catalog() -> None:
    """Nothing is connected — this asserts the profile resolves to a catalog at all."""
    profile = Profile(dialect='clickhouse', host='localhost')
    assert open_catalog(profile, connect=lambda _: FakeConnection()) is not None
