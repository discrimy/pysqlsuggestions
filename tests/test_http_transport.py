"""The readers' shared transport: every HTTP answer is a value, only transport failure raises."""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from typing import Any

import pytest

from pysqlsuggestions.catalogs import _http


class _Answer(io.BytesIO):
    """Enough of an `http.client.HTTPResponse` for `urlopen`'s context manager."""

    def __init__(self, status: int, body: bytes) -> None:
        super().__init__(body)
        self.status = status

    def __enter__(self) -> _Answer:
        """Enter the context manager."""
        return self

    def __exit__(self, *_: object) -> None:
        """Leave it, closing nothing that matters."""
        self.close()


def test_a_success_is_a_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 comes back as a Response carrying its body."""
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *_, **__: _Answer(200, b'{"a": 1}'))
    answer = _http.request('http://localhost:8123/')
    assert answer.status == 200
    assert answer.json() == {'a': 1}


def test_an_error_status_is_also_a_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """urllib raises on 4xx and hands the body back on the exception. That is backwards for us."""

    def raising(*_: Any, **__: Any) -> None:
        raise urllib.error.HTTPError(
            'http://x/',
            400,
            'Bad Request',
            {},  # type: ignore[arg-type]
            io.BytesIO(b'Code: 62.\n  DB::Exception'),
        )

    monkeypatch.setattr(urllib.request, 'urlopen', raising)
    answer = _http.request('http://localhost:8123/')
    assert answer.status == 400
    assert answer.text() == 'Code: 62. DB::Exception'


def test_a_refused_connection_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """No HTTP answer at all is the one case a caller cannot turn into a message itself."""

    def raising(*_: Any, **__: Any) -> None:
        raise urllib.error.URLError('connection refused')

    monkeypatch.setattr(urllib.request, 'urlopen', raising)
    with pytest.raises(_http.TransportError, match='connection refused'):
        _http.request('http://localhost:8123/')


def test_the_request_carries_method_body_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """What is sent is what the caller asked for — the readers' whole surface depends on it."""
    seen: dict[str, Any] = {}

    def capture(request: urllib.request.Request, **_: Any) -> _Answer:
        seen['method'] = request.get_method()
        seen['data'] = request.data
        seen['user'] = request.get_header('X-clickhouse-user')
        return _Answer(200, b'{}')

    monkeypatch.setattr(urllib.request, 'urlopen', capture)
    _http.request(
        'http://localhost:8123/?a=1',
        method='POST',
        data=b'SELECT 1',
        headers={'X-ClickHouse-User': 'report'},
    )
    assert seen == {'method': 'POST', 'data': b'SELECT 1', 'user': 'report'}
