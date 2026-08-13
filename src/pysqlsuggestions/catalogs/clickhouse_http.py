"""
A ClickHouse catalog reader over the HTTP interface. No driver, no wheels.

ClickHouse's own clients ship a C extension and pull in lz4 and a zstd backport.
Every one of those exists to compress a wire, and this reads seven introspection
queries against a cache that is warm for the rest of an editor session — so
there is nothing here worth compressing, and nothing worth compiling. Removing
them is what lets one build of the extension serve `linux-armhf` and Alpine.

Parameters are bound server-side: ClickHouse takes `param_p1=` query arguments
against `{p1:String}` markers, so no value is ever interpolated into a statement.
That is the fact that makes writing this reasonable rather than reckless.

That style is not one of PEP 249's five, so `DbapiCatalog` is told `named`,
`render()` produces `:p1`, and the rewrite lives here. Adding a sixth paramstyle
to `render()` would put a non-standard style into code every adapter shares, to
serve one adapter.

The surface is the slice of PEP 249 `connections.py` actually calls: a module
`connect(**kwargs)`, a `Connection.cursor()`, and a cursor that executes and
fetches. Not a driver — there are no transactions here, and by project rule
never a read of table data.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

from pysqlsuggestions.catalogs import _http

apilevel = '2.0'
threadsafety = 2
"""
Connections may be shared between threads; cursors may not.

True for free rather than by care: a `Connection` here holds settings and no
socket, and every request opens and closes its own. `connections.py` states this
level for the drivers it opens, and this one has to be able to make the claim.
"""
paramstyle = 'named'

DEFAULT_PORT = 8123
"""The plaintext HTTP port. TLS is 8443, and `connect` follows `secure`."""

SECURE_PORT = 8443

_MARKER = re.compile(r':p(\d+)\b')


class ClickHouseError(Exception):
    """ClickHouse refused the statement, carrying its own message."""


class Cursor:
    """The two methods `DbapiCatalog` calls, and nothing else."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, operation: str, parameters: Any = None) -> Cursor:
        """
        Run `operation`, holding its rows for `fetchall`.

        `parameters` is the mapping `render()` produces for the `named` style —
        `{'p1': ...}` — or None. Returning self follows PEP 249, which lets a
        caller chain; `DbapiCatalog` does not, and nothing here depends on it.
        """
        self._rows = self._connection.run(operation, parameters if isinstance(parameters, Mapping) else {})
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
        database: str | None,
        headers: Mapping[str, str],
        timeout: float,
        verify: bool,
        transport: _http.Transport,
    ) -> None:
        self._base = base
        self._database = database
        self._headers = dict(headers)
        self._timeout = timeout
        self._verify = verify
        self._transport = transport

    def cursor(self) -> Cursor:
        """A cursor on this connection. Each is independent of every other."""
        return Cursor(self)

    def close(self) -> None:
        """Nothing to close. Present because `connections.py` and `check.py` call it."""

    def run(self, operation: str, parameters: Mapping[str, Any]) -> list[tuple[Any, ...]]:
        """Post one statement and return its rows as tuples."""
        arguments: dict[str, str] = {
            'default_format': 'JSONCompact',
            # ClickHouse JSON-quotes 64-bit integers by default, so `total_rows`
            # and `position` would arrive as strings. The row mappers would
            # survive it — `int('7')` works — but a column whose Python type
            # depends on a server setting is not something to leave to luck.
            'output_format_json_quote_64bit_integers': '0',
        }
        if self._database is not None:
            arguments['database'] = self._database
        arguments.update({f'param_{name}': str(value) for name, value in parameters.items()})

        answer = self._transport(
            f'{self._base}?{urllib.parse.urlencode(arguments)}',
            method='POST',
            data=_typed(operation).encode('utf-8'),
            headers=self._headers,
            timeout=self._timeout,
            verify=self._verify,
        )
        if answer.status != 200:
            raise ClickHouseError(answer.text())
        payload = answer.json()
        return [tuple(row) for row in payload.get('data', ())]


def _typed(sql: str) -> str:
    """
    `:p1` markers as ClickHouse's `{p1:String}`.

    Every value this library binds is a schema, relation, column or prefix name,
    so `String` is the whole type vocabulary needed. A reader that had to infer
    types from values would be guessing about the query it was handed.
    """
    return _MARKER.sub(lambda match: f'{{p{match.group(1)}:String}}', sql)


def connect(
    *,
    host: str,
    port: int | None = None,
    database: str | None = None,
    user: str | None = None,
    password: str | None = None,
    secure: bool = False,
    verify: bool = True,
    timeout: float = _http.DEFAULT_TIMEOUT,
    transport: _http.Transport = _http.request,
) -> Connection:
    """
    A connection to a ClickHouse HTTP endpoint. Opens nothing yet.

    Keyword-only, and named for what `connections.py` and `check.py` already
    build: both assemble a dict of host, port, database, user, password and
    timeout and splat it into whatever module `DRIVERS` names. Matching that
    shape is what lets this be a drop-in for a driver they used to import.

    Credentials go in headers rather than the URL. A URL reaches the output
    channel, proxy access logs and crash reports; a header reaches none of them.

    Unlike Trino, a password over plaintext is permitted here: ClickHouse itself
    accepts one, the docker fixture uses one, and refusing would break a local
    setup to protect a remote one the user has not described to us.

    `verify=False` accepts any certificate — see `_http.tls_context`. It means
    nothing without `secure`, and is not rejected in that pairing: a user who
    sets it while turning TLS off has expressed no contradiction, only a
    setting that does not apply yet.
    """
    scheme = 'https' if secure else 'http'
    resolved = port if port is not None else (SECURE_PORT if secure else DEFAULT_PORT)
    headers = {'Content-Type': 'text/plain; charset=UTF-8'}
    if user is not None:
        headers['X-ClickHouse-User'] = user
    if password is not None:
        headers['X-ClickHouse-Key'] = password
    return Connection(
        base=f'{scheme}://{host}:{resolved}/',
        database=database,
        headers=headers,
        timeout=timeout,
        verify=verify,
        transport=transport,
    )
