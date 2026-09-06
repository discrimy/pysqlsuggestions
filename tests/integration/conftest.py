"""
Connections to the docker backends.

Every fixture skips rather than fails when its backend is unreachable, so the
suite stays runnable without docker. Bring them up with:

    docker compose -f docker/docker-compose.yml up -d --wait
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from pysqlsuggestions.catalogs import clickhouse_http, trino_http
from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO

POSTGRES_DSN = 'postgresql://report:report@localhost:57432/report_service'

ANALYST_DSN = 'postgresql://analyst:analyst@localhost:57432/report_service'
"""
The restricted role from `docker/postgres/03-roles.sql`.

A second connection rather than `SET ROLE` on the first, because
`has_column_privilege` evaluates against the role the connection currently has:
a fixture that must remember to reset it is one that will eventually leak one
test's privileges into another's, and the symptom would be a privilege
assertion passing for the wrong reason.
"""
CLICKHOUSE_HOST, CLICKHOUSE_PORT = 'localhost', 57123
TRINO_HOST, TRINO_PORT = 'localhost', 57080
TRINO_SECURE_PORT = 57443
"""
The `trino-secure` coordinator: TLS, and file-based password authentication.

A second server rather than a flag on the first, because enabling PASSWORD
authentication makes Trino refuse plain HTTP — so one coordinator cannot be both
the unauthenticated fixture the catalog tests read from and the authenticated
one the credential tests need.
"""


def _skip(backend: str, error: Exception) -> None:
    pytest.skip(f'{backend} not reachable ({error}); run docker/docker-compose.yml')


@pytest.fixture(scope='session')
def postgres_catalog() -> Iterator[DbapiCatalog]:
    """A catalog over psycopg2, which speaks the `format` paramstyle."""
    psycopg2 = pytest.importorskip('psycopg2')
    try:
        connection = psycopg2.connect(POSTGRES_DSN)
    except Exception as error:  # noqa: BLE001
        _skip('postgres', error)
    yield DbapiCatalog(connection.cursor, POSTGRES, paramstyle=psycopg2.paramstyle)
    connection.close()


@pytest.fixture(scope='session')
def analyst_catalog() -> Iterator[DbapiCatalog]:
    """The same database seen by a role that may not read all of it."""
    psycopg2 = pytest.importorskip('psycopg2')
    try:
        connection = psycopg2.connect(ANALYST_DSN)
    except Exception as error:  # noqa: BLE001
        _skip('postgres', error)
    yield DbapiCatalog(connection.cursor, POSTGRES, paramstyle=psycopg2.paramstyle)
    connection.close()


@pytest.fixture(scope='session')
def clickhouse_catalog() -> Iterator[DbapiCatalog]:
    """
    A catalog over the library's own HTTP reader, which speaks `named`.

    Port 57123, not 57900: this is the HTTP interface, not the native protocol
    the compiled client used. Nothing is skip-guarded on an import any more —
    the reader is part of the library — so the only reason to skip is a backend
    that is not up.
    """
    # The database matters: the introspection SQL falls back to currentDatabase()
    # when no schema is given, and that is `default` unless the connection says otherwise.
    connection = clickhouse_http.connect(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        user='report',
        password='report',
        database='analytics',
    )
    try:
        connection.cursor().execute('SELECT 1')
    except Exception as error:  # noqa: BLE001
        _skip('clickhouse', error)
    yield DbapiCatalog(connection.cursor, CLICKHOUSE, paramstyle=clickhouse_http.paramstyle)
    connection.close()


@pytest.fixture(scope='session')
def trino_catalog() -> Iterator[DbapiCatalog]:
    """
    A catalog over the library's own HTTP reader, which speaks `qmark`.

    `_reconnecting_cursor` is gone: it existed because a `trino` client cursor
    is single-use, and `Connection.cursor()` here returns a fresh one every
    time by construction.
    """
    connection = trino_http.connect(host=TRINO_HOST, port=TRINO_PORT, user='pysqlsuggestions', database='postgresql')
    try:
        connection.cursor().execute('SELECT 1')
    except Exception as error:  # noqa: BLE001
        _skip('trino', error)
    yield DbapiCatalog(connection.cursor, TRINO, paramstyle=trino_http.paramstyle)
    connection.close()


class CountingCursor:
    """
    A cursor that records every statement, so a test can assert on round trips.

    A wrapper rather than a patched attribute: a psycopg2 cursor's `execute` is
    read-only, so the obvious approach raises `AttributeError` at assignment
    rather than failing a test.
    """

    def __init__(self, inner: Any, log: list[str]) -> None:
        self._inner = inner
        self._log = log

    def execute(self, operation: str, parameters: Any = None) -> Any:
        """Record, then run."""
        self._log.append(operation)
        return self._inner.execute(operation, parameters)

    def fetchall(self) -> Any:
        """Every remaining row."""
        return self._inner.fetchall()


@pytest.fixture
def counting_postgres() -> Iterator[tuple[DbapiCatalog, list[str]]]:
    """
    Postgres, plus the list of statements it was asked to run.

    A fixture rather than a connection opened inside the test, because the skip
    is what fixtures here are *for*: a test that connects on its own fails the
    suite on a machine with no docker instead of standing aside, and CI is the
    machine with no docker. That is exactly how this arrived — the round-trip
    assertion for `SupportsBulkColumns` was written against a bare
    `psycopg2.connect` and passed locally for a day before failing the moment it
    ran anywhere else.
    """
    psycopg2 = pytest.importorskip('psycopg2')
    try:
        connection = psycopg2.connect(POSTGRES_DSN)
    except Exception as error:  # noqa: BLE001
        _skip('postgres', error)
    log: list[str] = []
    catalog = DbapiCatalog(
        lambda: CountingCursor(connection.cursor(), log),
        POSTGRES,
        paramstyle=psycopg2.paramstyle,
    )
    yield catalog, log
    connection.close()
