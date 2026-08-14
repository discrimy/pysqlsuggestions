"""
A Trino catalog reader over the REST API. No driver, no wheels.

The `trino` client hard-requires lz4, orjson and zstandard — plain
`Requires-Dist`, not extras — and all three ship compiled. All three exist for
the *spooled* protocol, which the server uses only when a client asks for it
with `X-Trino-Query-Data-Encoding`. Not asking yields inline JSON, and inline
JSON is the whole of what a catalog reader needs.

Parameters go through Trino's prepared-statement headers, which is what the
official client does too: the statement travels URL-encoded in a header and the
values are rendered into `EXECUTE … USING`. Client-side literal rendering is
unavoidable on this endpoint and is not a weakening — it is the same mechanism,
not a lesser one. Every value this library binds is a schema, relation or prefix
name, and Trino has no backslash escapes in string literals, so doubling the
quote is the complete escape rather than the first half of one.

The `nextUri` loop is the only part of either reader with real behaviour in it:
a query answers before it has rows, `error` can arrive inside a 200, and a busy
coordinator answers 503 expecting the client to come back. It is bounded by a
total deadline rather than a retry count, because the question a completion asks
is how long it may wait.
"""

from __future__ import annotations

import base64
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from pysqlsuggestions.catalogs import _http

apilevel = '2.0'
threadsafety = 2
paramstyle = 'qmark'

DEFAULT_PORT = 8080
SECURE_PORT = 443

DEFAULT_DEADLINE = 15.0
"""
Seconds for a whole statement, paging included.

Longer than one request's timeout on purpose: a cold coordinator queues, and a
first catalog read that gave up at ten seconds would report an unreachable
database that is merely starting.
"""

RETRY_STATUSES = frozenset({502, 503, 504})
_RETRY_PAUSE = 0.1
_STATEMENT_NAME = 'pysqlsuggestions'


class TrinoError(Exception):
    """Trino refused the statement, or never finished it."""


class Cursor:
    """The two methods `DbapiCatalog` calls, and nothing else."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, operation: str, parameters: Any = ()) -> Cursor:
        """
        Run `operation`, holding its rows for `fetchall`.

        `parameters` is the tuple `render()` produces for the `qmark` style.
        """
        values = tuple(str(value) for value in parameters) if isinstance(parameters, Iterable) else ()
        statement, headers = _prepare(operation, values)
        self._rows = self._connection.run(statement, headers)
        return self

    def fetchall(self) -> Sequence[tuple[Any, ...]]:
        """Every row of the last statement. Empty before one has run."""
        return self._rows

    def close(self) -> None:
        """Nothing is held open. Present because PEP 249 callers expect it."""
        self._rows = []


class Connection:
    """Where to send statements, and as whom. Holds no socket."""

    def __init__(
        self,
        *,
        base: str,
        headers: Mapping[str, str],
        timeout: float,
        deadline: float,
        verify: bool,
        transport: _http.Transport,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._base = base
        self._headers = dict(headers)
        self._timeout = timeout
        self._deadline = deadline
        self._verify = verify
        self._transport = transport
        self._clock = clock
        self._sleep = sleep

    def cursor(self) -> Cursor:
        """A cursor on this connection. Each is independent of every other."""
        return Cursor(self)

    def close(self) -> None:
        """Nothing to close. Present because `connections.py` and `check.py` call it."""

    def run(self, statement: str, extra_headers: Mapping[str, str]) -> list[tuple[Any, ...]]:
        """Post `statement` and follow `nextUri` to the end, returning every row."""
        expires = self._clock() + self._deadline
        answer = self._fetch(
            f'{self._base}/v1/statement',
            method='POST',
            data=statement.encode('utf-8'),
            extra_headers=extra_headers,
            expires=expires,
        )
        rows: list[tuple[Any, ...]] = []
        while True:
            payload = answer.json()
            if 'error' in payload:
                raise TrinoError(_message(payload['error']))
            rows.extend(tuple(row) for row in payload.get('data', ()))
            following = payload.get('nextUri')
            if not isinstance(following, str):
                return rows
            answer = self._fetch(following, method='GET', data=None, extra_headers={}, expires=expires)

    def _fetch(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None,
        extra_headers: Mapping[str, str],
        expires: float,
    ) -> _http.Response:
        """One request, retried while the coordinator says it is busy."""
        headers = {**self._headers, **extra_headers}
        while True:
            remaining = expires - self._clock()
            if remaining <= 0:
                message = f'trino did not answer within {self._deadline:g}s'
                raise TrinoError(message)
            answer = self._transport(
                url,
                method=method,
                data=data,
                headers=headers,
                timeout=min(self._timeout, remaining),
                verify=self._verify,
            )
            if answer.status not in RETRY_STATUSES:
                break
            # A coordinator that is starting, or queuing, answers 503 and expects
            # the client to come back rather than to give up.
            self._sleep(_RETRY_PAUSE)
        if answer.status != 200:
            raise TrinoError(answer.text())
        return answer


def _message(error: object) -> str:
    """The readable part of Trino's error object, or its whole shape when it has none."""
    if isinstance(error, Mapping):
        message = error.get('message')
        if isinstance(message, str):
            return message
    return str(error)


def _prepare(operation: str, values: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    """
    The statement to post, and the prepared-statement header it needs.

    No parameters means no wrapping: `SHOW FUNCTIONS` takes none, and putting it
    through EXECUTE would add a way to fail and nothing else.
    """
    if not values:
        return operation, {}
    rendered = ', '.join(_literal(value) for value in values)
    encoded = urllib.parse.quote(operation, safe='')
    return (
        f'EXECUTE {_STATEMENT_NAME} USING {rendered}',
        {'X-Trino-Prepared-Statement': f'{_STATEMENT_NAME}={encoded}'},
    )


def _literal(value: str) -> str:
    """`value` as a SQL string literal, quote doubled."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def connect(
    *,
    host: str,
    port: int | None = None,
    database: str | None = None,
    schema: str | None = None,
    user: str | None = None,
    password: str | None = None,
    secure: bool = False,
    verify: bool = True,
    timeout: float = _http.DEFAULT_TIMEOUT,
    deadline: float = DEFAULT_DEADLINE,
    transport: _http.Transport = _http.request,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Connection:
    """
    A connection to a Trino coordinator. Opens nothing yet.

    `database` is Trino's *catalog*. `Profile` has no catalog field and
    `connections.py` passes `database=`, so that is the name that arrives here —
    and the `trino` client has no such parameter at all, which is why a Trino
    profile naming a database raises today and stops doing so with this reader.

    A password without TLS is refused rather than sent. Trino rejects password
    authentication over plaintext itself, so sending it would leak a credential
    to buy an error — the opposite trade from ClickHouse, which accepts one.

    `verify=False` accepts any certificate — see `_http.tls_context`. It does not
    relax the rule above: a password still requires `secure`, because that rule
    is about whether the credential is encrypted at all, not about who signed
    the certificate.

    `clock` and `sleep` are injected so the deadline is testable without waiting.
    """
    if password is not None and not secure:
        message = 'trino refuses password authentication without TLS; set secure on the connection'
        raise ValueError(message)
    scheme = 'https' if secure else 'http'
    resolved = port if port is not None else (SECURE_PORT if secure else DEFAULT_PORT)
    headers = {
        'Content-Type': 'text/plain; charset=UTF-8',
        # Trino requires a user on every request and answers 400 without one.
        'X-Trino-User': user or _STATEMENT_NAME,
        # Shows up in the coordinator's query log, which is where a DBA asks
        # what has been running small metadata queries all afternoon.
        'X-Trino-Source': 'pysqlsuggestions',
    }
    if database is not None:
        headers['X-Trino-Catalog'] = database
    if schema is not None:
        headers['X-Trino-Schema'] = schema
    if password is not None:
        credentials = base64.b64encode(f'{user or ""}:{password}'.encode()).decode('ascii')
        headers['Authorization'] = f'Basic {credentials}'
    return Connection(
        base=f'{scheme}://{host}:{resolved}',
        headers=headers,
        timeout=timeout,
        deadline=deadline,
        verify=verify,
        transport=transport,
        clock=clock,
        sleep=sleep,
    )
