"""
What the readers do when a real backend refuses.

Every message asserted here was measured against the docker fixtures rather
than guessed, because the whole point of these tests is that a hand-written
reader gets error handling wrong in ways a unit test with a canned body cannot
show — a status code that is not the one you assumed, a failure reported inside
a 200, an authentication refusal that is not JSON.

The verdict a user sees comes from `check.py::describe`, which passes anything
without a pg8000-shaped `dict` argument straight through. So the sentence these
raise *is* the sentence in the tooltip, and a wall of JSON here would be a wall
of JSON there.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

from pysqlsuggestions.catalogs import clickhouse_http, trino_http
from pysqlsuggestions.catalogs._http import TransportError
from tests.integration.conftest import (
    CLICKHOUSE_HOST,
    CLICKHOUSE_PORT,
    TRINO_HOST,
    TRINO_PORT,
)

pytestmark = pytest.mark.integration

CLICKHOUSE: dict[str, Any] = {
    'host': CLICKHOUSE_HOST,
    'port': CLICKHOUSE_PORT,
    'user': 'report',
    'password': 'report',
    'database': 'analytics',
}
TRINO: dict[str, Any] = {
    'host': TRINO_HOST,
    'port': TRINO_PORT,
    'user': 'pysqlsuggestions',
    'database': 'postgresql',
}

READERS: dict[str, tuple[ModuleType, dict[str, Any]]] = {
    'clickhouse': (clickhouse_http, CLICKHOUSE),
    'trino': (trino_http, TRINO),
}
"""Each reader with the options that reach its docker fixture."""


def _up(name: str) -> None:
    """Skip when that backend is not up, matching every other fixture here."""
    reader, options = READERS[name]
    try:
        reader.connect(**options).cursor().execute('SELECT 1')
    except Exception as error:  # noqa: BLE001
        pytest.skip(f'{name} not reachable ({error}); run docker/docker-compose.yml')


@pytest.fixture(scope='module')
def clickhouse_up() -> None:
    """ClickHouse answers, or the module skips."""
    _up('clickhouse')


@pytest.fixture(scope='module')
def trino_up() -> None:
    """Trino answers, or the module skips."""
    _up('trino')


# --------------------------------------------------------------------------- #
# ClickHouse
# --------------------------------------------------------------------------- #


def test_clickhouse_names_the_relation_it_could_not_find(clickhouse_up: None) -> None:
    """A 404 whose body is JSON — the `exception` is extracted, the envelope is not shown."""
    cursor = clickhouse_http.connect(**CLICKHOUSE).cursor()
    with pytest.raises(clickhouse_http.ClickHouseError) as raised:
        cursor.execute('SELECT * FROM system.definitely_not_a_table')
    message = str(raised.value)
    assert 'definitely_not_a_table' in message
    assert not message.lstrip().startswith('{'), f'the JSON envelope leaked into the message: {message[:80]}'


def test_clickhouse_reports_a_syntax_error_as_one(clickhouse_up: None) -> None:
    """Code 62, and the position it failed at, which is what a user acts on."""
    cursor = clickhouse_http.connect(**CLICKHOUSE).cursor()
    with pytest.raises(clickhouse_http.ClickHouseError, match='Syntax error'):
        cursor.execute('SELEKT 1')


def test_clickhouse_refuses_a_wrong_password_in_plain_text(clickhouse_up: None) -> None:
    """
    Authentication failures are not JSON — the status branch is what reports them.

    Measured: HTTP 403 with a `Code: 516` text body, no JSON envelope at all. A
    reader that only ever parsed JSON would raise a ValueError here instead.
    """
    cursor = clickhouse_http.connect(**{**CLICKHOUSE, 'password': 'wrong'}).cursor()
    with pytest.raises(clickhouse_http.ClickHouseError, match='Authentication failed'):
        cursor.execute('SELECT 1')


def test_clickhouse_refuses_an_unknown_user(clickhouse_up: None) -> None:
    """Same shape, and deliberately the same message from the server — it does not say which."""
    options = {**CLICKHOUSE, 'user': 'nobody', 'password': 'nothing'}
    cursor = clickhouse_http.connect(**options).cursor()
    with pytest.raises(clickhouse_http.ClickHouseError, match='Authentication failed'):
        cursor.execute('SELECT 1')


# --------------------------------------------------------------------------- #
# Trino
# --------------------------------------------------------------------------- #


def test_trino_names_the_relation_it_could_not_find(trino_up: None) -> None:
    """Trino reports query failure inside a 200, in the payload's `error` object."""
    cursor = trino_http.connect(**TRINO).cursor()
    with pytest.raises(trino_http.TrinoError, match='does not exist'):
        cursor.execute('SELECT * FROM public.definitely_not_a_table')


def test_trino_reports_a_syntax_error_as_one(trino_up: None) -> None:
    """The parser's own message, line and column included."""
    cursor = trino_http.connect(**TRINO).cursor()
    with pytest.raises(trino_http.TrinoError, match='mismatched input'):
        cursor.execute('SELEKT 1')


def test_trino_names_a_catalog_that_is_not_configured(trino_up: None) -> None:
    """`database` is the catalog, so a typo there fails on the first read rather than at connect."""
    cursor = trino_http.connect(**{**TRINO, 'database': 'not_a_catalog'}).cursor()
    with pytest.raises(trino_http.TrinoError, match="Catalog 'not_a_catalog' not found"):
        cursor.execute('SHOW SCHEMAS')


# --------------------------------------------------------------------------- #
# Neither
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('reader', [clickhouse_http, trino_http], ids=['clickhouse', 'trino'])
def test_a_refused_connection_is_a_transport_error(reader: ModuleType) -> None:
    """
    Not a backend error: nothing answered, so there is no message to quote.

    Port 1 rather than a random high one — nothing listens there, and a port
    that happened to be in use would make this test depend on the machine.
    Needs no backend running, which is why it takes no fixture.
    """
    connection = reader.connect(host='127.0.0.1', port=1, user='u', timeout=2.0)
    with pytest.raises(TransportError):
        connection.cursor().execute('SELECT 1')
