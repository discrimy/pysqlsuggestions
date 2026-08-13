"""
Connections to the docker backends.

Every fixture skips rather than fails when its backend is unreachable, so the
suite stays runnable without docker. Bring them up with:

    docker compose -f docker/docker-compose.yml up -d --wait
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from pysqlsuggestions.catalogs import clickhouse_http, trino_http
from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO

POSTGRES_DSN = 'postgresql://report:report@localhost:57432/report_service'
CLICKHOUSE_HOST, CLICKHOUSE_PORT = 'localhost', 57123
TRINO_HOST, TRINO_PORT = 'localhost', 57080


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
