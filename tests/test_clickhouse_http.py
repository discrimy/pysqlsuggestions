"""The ClickHouse reader: what it sends, and what it makes of what comes back."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

import pytest

from pysqlsuggestions.catalogs import clickhouse_http
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

    def query(self, index: int = 0) -> dict[str, str]:
        """The query arguments of call `index`, decoded."""
        parsed = urllib.parse.urlparse(str(self.calls[index]['url']))
        return {key: value[0] for key, value in urllib.parse.parse_qs(parsed.query).items()}


def rows(*data: list[Any]) -> Response:
    """A JSONCompact body carrying `data`."""
    return Response(status=200, body=json.dumps({'meta': [], 'data': list(data), 'rows': len(data)}).encode())


def test_the_statement_is_the_body_and_the_method_is_post() -> None:
    """ClickHouse takes SQL as the request body, not as a query argument."""
    transport = Recorder(rows(['analytics']))
    connection = clickhouse_http.connect(host='localhost', transport=transport)
    cursor = connection.cursor()
    cursor.execute('SELECT name FROM system.databases')
    assert cursor.fetchall() == [('analytics',)]
    assert transport.calls[0]['method'] == 'POST'
    assert transport.calls[0]['data'] == b'SELECT name FROM system.databases'


def test_named_markers_become_typed_clickhouse_parameters() -> None:
    """`render()` gives us `:p1`; ClickHouse wants `{p1:String}` and a `param_p1` argument."""
    transport = Recorder(rows())
    cursor = clickhouse_http.connect(host='localhost', transport=transport).cursor()
    cursor.execute('SELECT 1 WHERE database = :p1 AND table = :p2', {'p1': 'analytics', 'p2': 'events'})
    assert transport.calls[0]['data'] == b'SELECT 1 WHERE database = {p1:String} AND table = {p2:String}'
    assert transport.query()['param_p1'] == 'analytics'
    assert transport.query()['param_p2'] == 'events'


def test_a_repeated_marker_is_rewritten_everywhere() -> None:
    """The ClickHouse `schemas` query spells its no-op predicate `$1 = $1`."""
    transport = Recorder(rows())
    cursor = clickhouse_http.connect(host='localhost', transport=transport).cursor()
    cursor.execute('SELECT name FROM system.databases WHERE :p1 = :p1', {'p1': ''})
    assert transport.calls[0]['data'] == b'SELECT name FROM system.databases WHERE {p1:String} = {p1:String}'


def test_sixty_four_bit_integers_arrive_as_numbers() -> None:
    """Left alone, ClickHouse quotes UInt64 in JSON and `total_rows` becomes a string."""
    transport = Recorder(rows())
    clickhouse_http.connect(host='localhost', transport=transport).cursor().execute('SELECT 1')
    assert transport.query()['output_format_json_quote_64bit_integers'] == '0'
    assert transport.query()['default_format'] == 'JSONCompact'


def test_the_database_travels_as_a_query_argument() -> None:
    """The introspection SQL falls back to currentDatabase(), so the connection has to set it."""
    transport = Recorder(rows())
    connection = clickhouse_http.connect(host='localhost', database='analytics', transport=transport)
    connection.cursor().execute('SELECT 1')
    assert transport.query()['database'] == 'analytics'


def test_credentials_travel_as_headers_not_in_the_url() -> None:
    """A URL reaches logs and proxy access logs; a header does not."""
    transport = Recorder(rows())
    connection = clickhouse_http.connect(host='h', user='report', password='secret', transport=transport)
    connection.cursor().execute('SELECT 1')
    headers = transport.calls[0]['headers']
    assert headers['X-ClickHouse-User'] == 'report'
    assert headers['X-ClickHouse-Key'] == 'secret'
    assert 'secret' not in str(transport.calls[0]['url'])


def test_secure_selects_https_and_the_port_default_follows() -> None:
    """8123 is the plaintext port; 8443 is the TLS one, and defaulting to 8123 over TLS never works."""
    transport = Recorder(rows())
    clickhouse_http.connect(host='h', secure=True, transport=transport).cursor().execute('SELECT 1')
    assert str(transport.calls[0]['url']).startswith('https://h:8443/')


def test_a_refusal_carries_clickhouses_own_message() -> None:
    """`Code: 60. DB::Exception: Table ... does not exist` is the sentence a user wants."""
    transport = Recorder(Response(status=404, body=b'Code: 60. DB::Exception: Table system.nope does not exist.'))
    cursor = clickhouse_http.connect(host='h', transport=transport).cursor()
    with pytest.raises(clickhouse_http.ClickHouseError, match='does not exist'):
        cursor.execute('SELECT * FROM system.nope')


def test_the_paramstyle_is_what_the_catalog_must_be_told() -> None:
    """DbapiCatalog takes paramstyle as a constructor argument; this is the value for it."""
    assert clickhouse_http.paramstyle == 'named'
