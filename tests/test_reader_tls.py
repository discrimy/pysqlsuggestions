"""
Both readers against a real HTTPS server with a real self-signed certificate.

A canned transport cannot test TLS: the whole question is what `ssl` does with a
certificate nothing signed, and that only has an answer when a handshake really
happens. So this starts an HTTPS server on a loopback port, points the readers
at it, and asserts on the two outcomes the `verify` flag exists to choose
between.

No docker. `openssl` on PATH is the only thing needed, and the module skips
without it rather than failing — the same rule the integration fixtures follow.

What is *not* covered here, because it cannot be: that a properly signed
certificate is accepted. That needs a certificate the machine's trust store
already trusts, which a test cannot arrange without installing one. The claim
this module can make is the narrow one — a certificate nothing signed is
refused by default and accepted when a connection asks for it to be.
"""

from __future__ import annotations

import base64
import json
import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from pysqlsuggestions.catalogs import clickhouse_http, trino_http
from pysqlsuggestions.catalogs._http import TransportError

CLICKHOUSE_ROWS = {'meta': [], 'data': [['analytics'], ['default']], 'rows': 2}
TRINO_ROWS = {'id': 'q1', 'data': [['postgresql']], 'stats': {'state': 'FINISHED'}}

REJECTED_USER = 'nobody'
REJECTED_PASSWORD = 'wrong'
"""The one credential each backend's stand-in refuses. Everything else passes."""


class _Handler(BaseHTTPRequestHandler):
    """Answers as either backend would, and records what it was sent."""

    seen: list[dict[str, Any]] = []  # noqa: RUF012

    def do_POST(self) -> None:  # noqa: N802
        """Record the request, then answer in the shape the path implies."""
        length = int(self.headers.get('Content-Length', '0'))
        _Handler.seen.append(
            {
                'path': self.path.split('?')[0],
                'body': self.rfile.read(length),
                'authorization': self.headers.get('Authorization'),
                'clickhouse_user': self.headers.get('X-ClickHouse-User'),
                'clickhouse_key': self.headers.get('X-ClickHouse-Key'),
                'trino_user': self.headers.get('X-Trino-User'),
            }
        )
        # One credential each is rejected by name, so a test can tell "the
        # header arrived and was accepted" from "this server takes anything".
        # `connect` always sends X-Trino-User, so there is no anonymous request
        # to refuse — a named refusal is what a real server does anyway.
        if self.path.startswith('/v1/statement'):
            if self.headers.get('X-Trino-User') == REJECTED_USER:
                self._answer(401, b'Unauthorized')
                return
            self._answer(200, json.dumps(TRINO_ROWS).encode())
            return
        if self.headers.get('X-ClickHouse-Key') == REJECTED_PASSWORD:
            # Plain text, not JSON — which is what the real server does, and
            # what `_decoded` has to keep tolerating.
            self._answer(403, b'Code: 516. DB::Exception: Authentication failed')
            return
        self._answer(200, json.dumps(CLICKHOUSE_ROWS).encode())

    def _answer(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silent. The server's own logging would interleave with pytest's output."""


def _certificate(directory: Path) -> tuple[Path, Path]:
    """A self-signed certificate and key for `localhost`, valid for a day."""
    key, cert = directory / 'key.pem', directory / 'cert.pem'
    subprocess.run(
        [
            'openssl',
            'req',
            '-x509',
            '-newkey',
            'rsa:2048',
            '-nodes',
            '-keyout',
            str(key),
            '-out',
            str(cert),
            '-days',
            '1',
            '-subj',
            '/CN=localhost',
            '-addext',
            'subjectAltName=DNS:localhost,IP:127.0.0.1',
        ],
        check=True,
        capture_output=True,
    )
    return key, cert


@pytest.fixture(scope='module')
def https_port(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    """A running HTTPS server with a self-signed certificate. Yields its port."""
    if shutil.which('openssl') is None:
        pytest.skip('openssl not on PATH; cannot make a certificate to serve')

    key, cert = _certificate(tmp_path_factory.mktemp('tls'))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert, keyfile=key)

    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _forget_requests() -> Iterator[None]:
    """Each test asserts on its own requests, not on the module's history."""
    _Handler.seen.clear()
    yield
    _Handler.seen.clear()


# --------------------------------------------------------------------------- #
# The default: a certificate nothing signed is refused
# --------------------------------------------------------------------------- #


def test_clickhouse_refuses_a_self_signed_certificate_by_default(https_port: int) -> None:
    """Verification is on unless a connection turns it off, and this is what that means."""
    connection = clickhouse_http.connect(host='localhost', port=https_port, secure=True, user='report')
    with pytest.raises(TransportError, match='CERTIFICATE_VERIFY_FAILED'):
        connection.cursor().execute('SELECT name FROM system.databases')


def test_trino_refuses_a_self_signed_certificate_by_default(https_port: int) -> None:
    """Same default, same failure, on the other reader."""
    connection = trino_http.connect(host='localhost', port=https_port, secure=True, user='u')
    with pytest.raises(TransportError, match='CERTIFICATE_VERIFY_FAILED'):
        connection.cursor().execute('SHOW CATALOGS')


def test_the_refusal_happens_before_anything_is_sent(https_port: int) -> None:
    """
    The handshake fails, so no credential ever reaches the wire.

    This is the property that makes the default worth having: a user who has not
    opted out has not leaked a password to whatever answered on that port.
    """
    connection = clickhouse_http.connect(
        host='localhost', port=https_port, secure=True, user='report', password='secret'
    )
    with pytest.raises(TransportError):
        connection.cursor().execute('SELECT 1')
    assert _Handler.seen == []


# --------------------------------------------------------------------------- #
# The opt-out: the same certificate, accepted
# --------------------------------------------------------------------------- #


def test_clickhouse_reads_over_tls_when_verification_is_off(https_port: int) -> None:
    """A real handshake, a real read — the flag's whole purpose."""
    connection = clickhouse_http.connect(
        host='localhost', port=https_port, secure=True, verify=False, user='report', password='report'
    )
    cursor = connection.cursor()
    cursor.execute('SELECT name FROM system.databases')
    assert cursor.fetchall() == [('analytics',), ('default',)]


def test_trino_reads_over_tls_when_verification_is_off(https_port: int) -> None:
    """The nextUri loop and the prepared-statement headers all work over TLS too."""
    connection = trino_http.connect(
        host='localhost', port=https_port, secure=True, verify=False, user='pysqlsuggestions'
    )
    cursor = connection.cursor()
    cursor.execute('SHOW CATALOGS')
    assert cursor.fetchall() == [('postgresql',)]


def test_the_port_is_still_the_one_the_connection_named(https_port: int) -> None:
    """`secure` moves the default port; an explicit one must survive it."""
    connection = trino_http.connect(
        host='localhost', port=https_port, secure=True, verify=False, user='u', password='pw'
    )
    connection.cursor().execute('SHOW CATALOGS')
    assert len(_Handler.seen) == 1


# --------------------------------------------------------------------------- #
# Credentials, over the connection that can carry them
# --------------------------------------------------------------------------- #


def test_trino_sends_basic_auth_over_tls(https_port: int) -> None:
    """
    Base64 of `user:password`, in an Authorization header, on a real socket.

    Asserted by decoding what the server received rather than what the reader
    built: the unit test already covers the latter, and the thing that can still
    be wrong is whether it survives the trip.
    """
    connection = trino_http.connect(
        host='localhost', port=https_port, secure=True, verify=False, user='ana', password='hunter2'
    )
    connection.cursor().execute('SHOW CATALOGS')
    header = _Handler.seen[0]['authorization']
    assert header.startswith('Basic ')
    assert base64.b64decode(header.removeprefix('Basic ')).decode() == 'ana:hunter2'
    assert _Handler.seen[0]['trino_user'] == 'ana'


def test_clickhouse_sends_its_credential_headers_over_tls(https_port: int) -> None:
    """The same two headers as over plaintext, and still not in the URL."""
    connection = clickhouse_http.connect(
        host='localhost', port=https_port, secure=True, verify=False, user='report', password='report'
    )
    connection.cursor().execute('SELECT 1')
    assert _Handler.seen[0]['clickhouse_user'] == 'report'
    assert _Handler.seen[0]['clickhouse_key'] == 'report'


def test_a_rejected_trino_credential_surfaces_rather_than_retrying(https_port: int) -> None:
    """
    401 is not one of the retry statuses, so it must raise rather than loop.

    A reader that retried it would spend its whole deadline re-sending a
    credential the server has already refused, and then report a timeout — which
    tells the user nothing about what is actually wrong. The count is the
    assertion that matters; the message only proves it got that far.
    """
    connection = trino_http.connect(host='localhost', port=https_port, secure=True, verify=False, user=REJECTED_USER)
    with pytest.raises(trino_http.TrinoError, match='Unauthorized'):
        connection.cursor().execute('SHOW CATALOGS')
    assert len(_Handler.seen) == 1, 'a 401 was retried'


def test_a_rejected_clickhouse_password_surfaces_over_tls(https_port: int) -> None:
    """A plain-text 403 across a TLS socket still reaches `_decoded`'s non-JSON path."""
    connection = clickhouse_http.connect(
        host='localhost',
        port=https_port,
        secure=True,
        verify=False,
        user='report',
        password=REJECTED_PASSWORD,
    )
    with pytest.raises(clickhouse_http.ClickHouseError, match='Authentication failed'):
        connection.cursor().execute('SELECT 1')
