"""
Connections to the docker backends.

Every fixture skips rather than fails when its backend is unreachable, so the
suite stays runnable without docker. Bring them up with:

    docker compose -f docker/docker-compose.yml up -d --wait
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from pysqlsuggestions.catalogs.dbapi import Cursor, DbapiCatalog
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO

POSTGRES_DSN = 'postgresql://report:report@localhost:57432/report_service'
CLICKHOUSE_HOST, CLICKHOUSE_PORT = 'localhost', 57900
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
    """A catalog over clickhouse-driver's DB-API layer, which speaks `pyformat`."""
    dbapi = pytest.importorskip('clickhouse_driver.dbapi')
    try:
        # The database matters: the introspection SQL falls back to currentDatabase()
        # when no schema is given, and that is `default` unless the connection says otherwise.
        connection = dbapi.connect(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user='report',
            password='report',
            database='analytics',
        )
        connection.cursor().execute('SELECT 1')
    except Exception as error:  # noqa: BLE001
        _skip('clickhouse', error)
    yield DbapiCatalog(connection.cursor, CLICKHOUSE, paramstyle=dbapi.paramstyle)
    connection.close()


@pytest.fixture(scope='session')
def trino_catalog() -> Iterator[DbapiCatalog]:
    """A catalog over the `trino` client, which speaks `qmark`."""
    trino = pytest.importorskip('trino.dbapi')
    try:
        connection = trino.connect(host=TRINO_HOST, port=TRINO_PORT, user='pysqlsuggestions', catalog='postgresql')
        connection.cursor().execute('SELECT 1')
    except Exception as error:  # noqa: BLE001
        _skip('trino', error)
    yield DbapiCatalog(_reconnecting_cursor(connection), TRINO, paramstyle=trino.paramstyle)
    connection.close()


def _reconnecting_cursor(connection: Any) -> Callable[[], Cursor]:
    """Trino cursors are single-use, so each query gets a fresh one."""
    return lambda: connection.cursor()
