"""
The Trino reader against a coordinator that actually demands a password.

`tests/test_reader_tls.py` proves the reader *builds* the right Authorization
header and gets it across a real TLS socket. It cannot prove Trino *accepts* it,
because the server on the other end is a stand-in written by the same person as
the client. This closes that: a real coordinator with `http-server.authentication.type=PASSWORD`
and a file-based authenticator, reached over its own TLS.

The fixture is `trino-secure` in `docker/docker-compose.yml`, carrying `tpch`
alone so it federates nothing and waits on no other service.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions.catalogs import trino_http
from pysqlsuggestions.catalogs._http import TransportError
from tests.integration.conftest import TRINO_HOST, TRINO_SECURE_PORT

pytestmark = pytest.mark.integration

USER, PASSWORD = 'report', 'report'


def secure(**overrides: object) -> trino_http.Connection:
    """A connection to the secured coordinator, verification off for its self-signed certificate."""
    options: dict[str, object] = {
        'host': TRINO_HOST,
        'port': TRINO_SECURE_PORT,
        'secure': True,
        'verify': False,
        'user': USER,
        'password': PASSWORD,
        # A cold coordinator queues, and this one is not on the `--wait` path of
        # every developer's compose invocation.
        'deadline': 40.0,
    }
    options.update(overrides)
    return trino_http.connect(**options)  # type: ignore[arg-type]


@pytest.fixture(scope='module', autouse=True)
def _secure_trino_up() -> None:
    """Skip the module when `trino-secure` is not running."""
    try:
        secure().cursor().execute('SELECT 1')
    except Exception as error:  # noqa: BLE001
        pytest.skip(f'trino-secure not reachable ({error}); run docker/docker-compose.yml')


def test_the_right_password_authenticates() -> None:
    """
    Trino accepts the Basic header the reader builds. The whole point of this module.

    `SHOW CATALOGS` rather than `SELECT 1` so the answer proves a session was
    established and not merely that a request was let through.
    """
    cursor = secure().cursor()
    cursor.execute('SHOW CATALOGS')
    assert ('tpch',) in cursor.fetchall()


def test_a_wrong_password_is_refused_by_the_server() -> None:
    """Not by us. The reader sends it and reports what came back."""
    with pytest.raises(trino_http.TrinoError, match='Invalid credentials'):
        secure(password='not-the-password').cursor().execute('SELECT 1')


def test_an_unknown_user_is_refused_the_same_way() -> None:
    """
    Deliberately indistinguishable from a wrong password — Trino does not say which.

    Asserted so that a future change which started distinguishing them would be
    noticed here rather than shipped as an oracle for valid usernames.
    """
    with pytest.raises(trino_http.TrinoError, match='Invalid credentials'):
        secure(user='nobody', password='anything').cursor().execute('SELECT 1')


def test_no_password_at_all_is_unauthorized() -> None:
    """
    401 rather than 403, and it must not be retried into the deadline.

    `Unauthorized` is the status line, not a message from the query engine —
    the request never reached one.
    """
    with pytest.raises(trino_http.TrinoError, match='Unauthorized'):
        secure(password=None).cursor().execute('SELECT 1')


def test_the_certificate_is_refused_unless_the_connection_says_otherwise() -> None:
    """
    The same server, the same credentials, verification left on. Against a real coordinator.

    `test_reader_tls.py` asserts this against a server this repository wrote.
    Here the certificate is one keytool generated for Trino, presented by Trino's
    own TLS stack, which is the arrangement a user would actually meet.
    """
    with pytest.raises(TransportError, match='CERTIFICATE_VERIFY_FAILED'):
        secure(verify=True).cursor().execute('SELECT 1')


def test_a_password_still_requires_tls_even_here() -> None:
    """
    The reader's own rule, unchanged by the server being willing to talk TLS.

    Trino refuses password authentication over plain HTTP, so sending it would
    leak a credential to buy an error. This is checked at connect time, before
    any socket is opened.
    """
    with pytest.raises(ValueError, match='TLS'):
        trino_http.connect(host=TRINO_HOST, port=TRINO_SECURE_PORT, user=USER, password=PASSWORD, secure=False)
