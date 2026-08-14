"""The Trino reader: paging, prepared statements, retries and the deadline."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator
from typing import Any

import pytest

from pysqlsuggestions.catalogs import trino_http
from pysqlsuggestions.catalogs._http import Response


class Recorder:
    """A transport that answers from a queue and remembers every call."""

    def __init__(self, *answers: Response) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **options: Any) -> Response:
        """Record the call and hand back the next queued answer."""
        self.calls.append({'url': url, **options})
        return self.answers.pop(0)


def page(*, data: list[list[Any]] | None = None, next_uri: str | None = None) -> Response:
    """One `/v1/statement` payload."""
    payload: dict[str, Any] = {'id': 'q1', 'stats': {'state': 'RUNNING'}}
    if data is not None:
        payload['data'] = data
    if next_uri is not None:
        payload['nextUri'] = next_uri
    return Response(status=200, body=json.dumps(payload).encode())


def milliseconds() -> Iterator[float]:
    """A clock that advances a millisecond per reading, so tests never wait."""
    return (float(tick) / 1000.0 for tick in range(100_000))


def open_connection(transport: Recorder, **options: Any) -> trino_http.Connection:
    """A connection whose clock and sleep are inert."""
    ticks = milliseconds()
    return trino_http.connect(
        host='localhost',
        user='pysqlsuggestions',
        transport=transport,
        clock=lambda: next(ticks),
        sleep=lambda _: None,
        **options,
    )


def test_the_first_page_may_carry_no_rows_at_all() -> None:
    """Trino answers immediately with a nextUri and no data. Stopping there returns nothing."""
    transport = Recorder(page(next_uri='http://localhost:8080/v1/statement/q1/1'), page(data=[['analytics']]))
    cursor = open_connection(transport).cursor()
    cursor.execute('SHOW CATALOGS')
    assert cursor.fetchall() == [('analytics',)]


def test_rows_accumulate_across_pages() -> None:
    """Every page's data belongs to the same result."""
    transport = Recorder(
        page(data=[['a']], next_uri='http://x/2'),
        page(data=[['b']], next_uri='http://x/3'),
        page(data=[['c']]),
    )
    cursor = open_connection(transport).cursor()
    cursor.execute('SHOW CATALOGS')
    assert cursor.fetchall() == [('a',), ('b',), ('c',)]


def test_paging_follows_with_get_not_post() -> None:
    """nextUri is a GET. Posting to it starts nothing and returns nothing useful."""
    transport = Recorder(page(next_uri='http://x/2'), page(data=[]))
    open_connection(transport).cursor().execute('SHOW CATALOGS')
    assert transport.calls[0]['method'] == 'POST'
    assert transport.calls[1]['method'] == 'GET'
    assert transport.calls[1]['url'] == 'http://x/2'


def test_an_error_in_the_payload_raises_with_its_message() -> None:
    """Trino reports query failure inside a 200, not as a status."""
    failed = Response(
        status=200,
        body=json.dumps({'error': {'message': "Table 'x' does not exist", 'errorName': 'TABLE_NOT_FOUND'}}).encode(),
    )
    cursor = open_connection(Recorder(failed)).cursor()
    with pytest.raises(trino_http.TrinoError, match='does not exist'):
        cursor.execute('SELECT * FROM x')


def test_parameters_go_through_a_prepared_statement_header() -> None:
    """The official client prepares by header and executes by literal. So does this."""
    transport = Recorder(page(data=[]))
    cursor = open_connection(transport).cursor()
    cursor.execute('SELECT * FROM t WHERE s = ? AND n = ?', ('public', 'events'))
    header = str(transport.calls[0]['headers']['X-Trino-Prepared-Statement'])
    name, _, encoded = header.partition('=')
    assert urllib.parse.unquote(encoded) == 'SELECT * FROM t WHERE s = ? AND n = ?'
    assert transport.calls[0]['data'] == f"EXECUTE {name} USING 'public', 'events'".encode()


def test_a_quote_in_a_value_is_doubled() -> None:
    """Trino has no backslash escapes in string literals, so doubling is the whole escape."""
    transport = Recorder(page(data=[]))
    open_connection(transport).cursor().execute('SELECT ?', ("O'Brien",))
    assert b"USING 'O''Brien'" in bytes(transport.calls[0]['data'])


def test_no_parameters_means_no_prepared_statement() -> None:
    """`SHOW FUNCTIONS` takes none, and wrapping it in EXECUTE would only add a way to fail."""
    transport = Recorder(page(data=[]))
    open_connection(transport).cursor().execute('SHOW FUNCTIONS')
    assert 'X-Trino-Prepared-Statement' not in transport.calls[0]['headers']
    assert transport.calls[0]['data'] == b'SHOW FUNCTIONS'


def test_a_busy_coordinator_is_retried() -> None:
    """503 while the coordinator starts or queues is an invitation to come back, not a failure."""
    transport = Recorder(Response(status=503, body=b''), page(data=[['a']]))
    cursor = open_connection(transport).cursor()
    cursor.execute('SHOW CATALOGS')
    assert cursor.fetchall() == [('a',)]
    assert len(transport.calls) == 2


def test_retrying_stops_at_the_deadline() -> None:
    """The question is how long a completion may wait, not how many times we may ask."""
    transport = Recorder(*[Response(status=503, body=b'') for _ in range(50)])
    ticks = milliseconds()
    cursor = trino_http.connect(
        host='localhost',
        user='u',
        deadline=0.002,
        transport=transport,
        clock=lambda: next(ticks),
        sleep=lambda _: None,
    ).cursor()
    with pytest.raises(trino_http.TrinoError, match='did not answer'):
        cursor.execute('SHOW CATALOGS')


def test_the_catalog_and_schema_travel_as_headers() -> None:
    """`database` is Trino's catalog — the name Profile uses and connections.py passes."""
    transport = Recorder(page(data=[]))
    connection = open_connection(transport, database='postgresql', schema='public')
    connection.cursor().execute('SHOW TABLES')
    headers = transport.calls[0]['headers']
    assert headers['X-Trino-Catalog'] == 'postgresql'
    assert headers['X-Trino-Schema'] == 'public'
    assert headers['X-Trino-User'] == 'pysqlsuggestions'


def test_a_password_over_plaintext_is_refused() -> None:
    """Trino itself rejects password auth without TLS. Sending it anyway only leaks it."""
    with pytest.raises(ValueError, match='TLS'):
        trino_http.connect(host='h', user='u', password='secret', secure=False)


def test_a_password_over_tls_becomes_basic_auth() -> None:
    """The one auth scheme this reader offers, and it says so."""
    transport = Recorder(page(data=[]))
    connection = trino_http.connect(host='h', user='u', password='pw', secure=True, transport=transport)
    connection.cursor().execute('SHOW CATALOGS')
    assert str(transport.calls[0]['headers']['Authorization']).startswith('Basic ')
    assert str(transport.calls[0]['url']).startswith('https://h:443/v1/statement')
