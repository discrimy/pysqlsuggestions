# Stdlib Catalog Readers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Trino and ClickHouse catalog readers written against the stdlib, so both backends are schema-aware with no compiled wheel anywhere in the dependency tree.

**Architecture:** `catalogs/dbapi.py` already reduces a backend to a two-method `Cursor` — `execute(operation, parameters)` and `fetchall()`. Both backends speak HTTP/JSON natively, so each reader is a request builder and a JSON parse behind that protocol, presenting the small slice of PEP 249 that `lsp/pysqlsuggestions_lsp/connections.py` calls: a module-level `connect(**kwargs)` returning a `Connection` with `.cursor()`. Nothing above the `Cursor` line changes — the seven `CatalogQueries`, the row mappers, `render()`, capability detection and ranking are all untouched.

**Tech Stack:** Python 3.10+, stdlib only (`urllib.request`, `json`, `ssl`, `base64`). pytest. TypeScript for the two settings-schema tasks.

**Spec:** `docs/superpowers/specs/2026-08-13-bundled-runtime-design.md` (§5 is this plan; §3, §4 and §6 are the sibling plan `2026-08-13-bundled-runtime.md`)

## Global Constraints

- **Zero runtime dependencies.** `import pysqlsuggestions` must pull in no driver. `tests/test_purity.py::test_import_pulls_in_no_drivers` enforces it.
- **The readers must not be reachable from `src/pysqlsuggestions/__init__.py`.** It imports `api`, `ports` and `types` and no adapter; keep it that way.
- **Ruff with `D` enabled and mypy `strict`** over `src`, `tests` and `lsp`. Every function and every module needs a docstring and full annotations.
- **Single quotes. 120 columns.** `from __future__ import annotations` at the top of every module.
- **Prose is the point.** Docstrings record *why* a shape was chosen and which alternative was rejected. A change that adds behaviour without saying what it refused is out of keeping. See `types.py` or `dialects/base.py` for the register.
- **Never read table data.** These readers run introspection queries only.
- **Missing capability → fewer suggestions, never an error.**
- **The gate** is `./scripts/check.sh` — `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`.
- **Commits** are `feat:`/`fix:`/`test:`/`docs:`/`refactor:`/`chore:` with a lowercase prose summary and a body explaining the decision. No co-author trailers.
- **Integration tests** are marked `@pytest.mark.integration` and must *skip*, never fail, when a backend is unreachable.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/pysqlsuggestions/catalogs/_http.py` | one HTTP round trip, normalised: any status is a `Response`, only transport failure raises |
| `src/pysqlsuggestions/catalogs/clickhouse_http.py` | ClickHouse HTTP interface: `JSONCompact`, `param_` binding, `:p1` → `{p1:String}` |
| `src/pysqlsuggestions/catalogs/trino_http.py` | Trino REST: `/v1/statement`, `nextUri` paging, prepared-statement headers, deadline |
| `tests/test_http_transport.py` | `_http.request` against a patched `urlopen` |
| `tests/test_clickhouse_http.py` | request construction and response parsing, injected transport |
| `tests/test_trino_http.py` | paging, errors, retries, deadline, literal escaping |
| `lsp/pysqlsuggestions_lsp/connections.py` | `DRIVERS` gains the two readers; `Profile` gains `secure` |
| `editors/vscode/src/profiles.ts` | `Profile` gains `secure` |
| `editors/vscode/package.json` | `secure` in the schema; the dialect description stops saying only Postgres works |

`_http.py` is private (leading underscore) because it is plumbing for these two
modules and not a public API surface the library promises.

---

## Task 1: The shared HTTP transport

**Files:**
- Create: `src/pysqlsuggestions/catalogs/_http.py`
- Test: `tests/test_http_transport.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Response(status: int, body: bytes)` with `.json() -> Any` and `.text() -> str`; `TransportError(Exception)`; `DEFAULT_TIMEOUT: float = 10.0`; `request(url: str, *, method: str = 'GET', data: bytes | None = None, headers: Mapping[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT) -> Response`; the type alias `Transport = Callable[..., Response]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_http_transport.py`:

```python
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
        raise urllib.error.HTTPError('http://x/', 400, 'Bad Request', {}, io.BytesIO(b'Code: 62.\n  DB::Exception'))  # type: ignore[arg-type]

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_http_transport.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pysqlsuggestions.catalogs._http'`

- [ ] **Step 3: Write the implementation**

Create `src/pysqlsuggestions/catalogs/_http.py`:

```python
"""
The catalog readers' shared transport. Stdlib only, and deliberately thin.

`urllib` raises on a non-2xx status and hands the body back on the exception
rather than the response, which is exactly backwards for a caller whose best
error message is the database's own words. This normalises it: every HTTP answer
is a `Response`, and only a failure to get an answer at all raises.

The transport is a parameter wherever it is used, so both readers are testable
without a socket. That is the pattern `runtime.ts` already follows, for the same
reason — the behaviour that matters is what gets *sent*, and asserting on it
should not need a server.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT = 10.0
"""Seconds for one request. A completion that has not arrived by then is noise."""


class TransportError(Exception):
    """No HTTP answer was reached: DNS, TCP, TLS or a timeout."""


@dataclass(frozen=True, slots=True)
class Response:
    """One HTTP answer, whatever its status."""

    status: int
    body: bytes

    def json(self) -> Any:
        """The body parsed as JSON. Raises `ValueError` when it is not JSON."""
        return json.loads(self.body.decode('utf-8'))

    def text(self) -> str:
        """
        The body as text, whitespace collapsed.

        Collapsed because this becomes an error message in a tooltip, and both
        backends answer with multi-line text — ClickHouse's `DB::Exception`
        carries a stack, and a wrapped sentence reads as truncated.
        """
        return ' '.join(self.body.decode('utf-8', 'replace').split())


Transport = Callable[..., Response]
"""What both readers call, and what a test substitutes wholesale."""


def request(
    url: str,
    *,
    method: str = 'GET',
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Response:
    """
    One HTTP round trip.

    TLS uses `ssl.create_default_context()` — the platform trust store, with
    hostname checking and certificate verification on. There is deliberately no
    option to turn either off: wanting completions is not a reason to teach this
    codebase how to skip certificate verification, and a user who needs a private
    CA can install it where every other tool on their machine already looks.
    """
    built = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    context = ssl.create_default_context() if url.startswith('https://') else None
    try:
        with urllib.request.urlopen(built, timeout=timeout, context=context) as answer:
            return Response(status=int(answer.status), body=answer.read())
    except urllib.error.HTTPError as error:
        # A 4xx or 5xx is an answer, and its body is the database's own message.
        # HTTPError is caught before URLError because it is a subclass of it.
        return Response(status=int(error.code), body=error.read())
    except (urllib.error.URLError, OSError) as error:
        raise TransportError(str(error)) from error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_http_transport.py -q`
Expected: PASS, 4 tests

- [ ] **Step 5: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/pysqlsuggestions/catalogs/_http.py tests/test_http_transport.py
git commit -m "feat: an HTTP round trip where the error body is a value

urllib raises on a non-2xx and puts the body on the exception, which is
backwards for a caller whose best message is the database's own words.
Every answer is a Response; only failing to reach one raises."
```

---

## Task 2: The ClickHouse reader

**Files:**
- Create: `src/pysqlsuggestions/catalogs/clickhouse_http.py`
- Test: `tests/test_clickhouse_http.py`

**Interfaces:**
- Consumes: `_http.Response`, `_http.Transport`, `_http.request`, `_http.DEFAULT_TIMEOUT`.
- Produces: module attributes `apilevel = '2.0'`, `threadsafety = 2`, `paramstyle = 'named'`; `DEFAULT_PORT = 8123`; `ClickHouseError(Exception)`; `connect(*, host: str, port: int | None = None, database: str | None = None, user: str | None = None, password: str | None = None, secure: bool = False, timeout: float = _http.DEFAULT_TIMEOUT, transport: _http.Transport = _http.request) -> Connection`; `Connection.cursor() -> Cursor`; `Connection.close() -> None`; `Cursor.execute(operation: str, parameters: Any = None) -> Cursor`; `Cursor.fetchall() -> Sequence[tuple[Any, ...]]`.

The keyword-only `connect` signature is not a style choice: `connections.py::_connect` and `check.py::_timed_connect` both build a `dict` and call `driver.connect(**arguments)` with `host`, `port`, `database`, `user`, `password` and — in `check.py` — `timeout`. Every one of those names must be accepted.

- [ ] **Step 1: Write the failing test**

Create `tests/test_clickhouse_http.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_clickhouse_http.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pysqlsuggestions.catalogs.clickhouse_http'`

- [ ] **Step 3: Write the implementation**

Create `src/pysqlsuggestions/catalogs/clickhouse_http.py`:

```python
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
        transport: _http.Transport,
    ) -> None:
        self._base = base
        self._database = database
        self._headers = dict(headers)
        self._timeout = timeout
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
        transport=transport,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_clickhouse_http.py -q`
Expected: PASS, 9 tests

- [ ] **Step 5: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/pysqlsuggestions/catalogs/clickhouse_http.py tests/test_clickhouse_http.py
git commit -m "feat: read the ClickHouse catalog over HTTP, with no wheels

The native clients ship a C extension and pull in lz4 and a zstd
backport, all of it to compress a wire that carries seven introspection
queries. Parameters bind server-side through param_/{p:String}, so
nothing is interpolated into a statement; render() stays untouched and
the rewrite from :p1 lives here rather than in shared code."
```

---

## Task 3: The Trino reader

**Files:**
- Create: `src/pysqlsuggestions/catalogs/trino_http.py`
- Test: `tests/test_trino_http.py`

**Interfaces:**
- Consumes: `_http.Response`, `_http.Transport`, `_http.request`, `_http.DEFAULT_TIMEOUT`, `_http.TransportError`.
- Produces: `apilevel = '2.0'`, `threadsafety = 2`, `paramstyle = 'qmark'`; `DEFAULT_PORT = 8080`; `SECURE_PORT = 443`; `DEFAULT_DEADLINE = 15.0`; `TrinoError(Exception)`; `connect(*, host: str, port: int | None = None, database: str | None = None, schema: str | None = None, user: str | None = None, password: str | None = None, secure: bool = False, timeout: float = _http.DEFAULT_TIMEOUT, deadline: float = DEFAULT_DEADLINE, transport: _http.Transport = _http.request, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> Connection`; `Connection.cursor() -> Cursor`; `Cursor.execute(operation: str, parameters: Any = ()) -> Cursor`; `Cursor.fetchall() -> Sequence[tuple[Any, ...]]`.

Note `database` means the Trino *catalog*. `Profile` has no `catalog` field and `connections.py` passes `database=`, so this is the name that arrives. The current `trino.dbapi` driver has no `database` parameter at all, which means a Trino profile that sets one raises today — this reader fixes that as a side effect.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trino_http.py`:

```python
"""The Trino reader: paging, prepared statements, retries and the deadline."""

from __future__ import annotations

import json
import urllib.parse
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


def open_connection(transport: Recorder, **options: Any) -> trino_http.Connection:
    """A connection whose clock and sleep are inert, so tests never wait."""
    ticks = iter(range(0, 10_000))
    return trino_http.connect(
        host='localhost',
        user='pysqlsuggestions',
        transport=transport,
        clock=lambda: float(next(ticks)) / 1000.0,
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
    header = transport.calls[0]['headers']['X-Trino-Prepared-Statement']
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
    cursor = trino_http.connect(
        host='localhost',
        user='u',
        deadline=0.002,
        transport=transport,
        clock=lambda: next(_ticking),
        sleep=lambda _: None,
    ).cursor()
    with pytest.raises(trino_http.TrinoError, match='did not answer'):
        cursor.execute('SHOW CATALOGS')


_ticking = iter(float(tick) / 1000.0 for tick in range(0, 10_000))


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
    assert transport.calls[0]['headers']['Authorization'].startswith('Basic ')
    assert str(transport.calls[0]['url']).startswith('https://h:443/v1/statement')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trino_http.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pysqlsuggestions.catalogs.trino_http'`

- [ ] **Step 3: Write the implementation**

Create `src/pysqlsuggestions/catalogs/trino_http.py`:

```python
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
first catalog read that gives up at ten seconds would report an unreachable
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
        transport: _http.Transport,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._base = base
        self._headers = dict(headers)
        self._timeout = timeout
        self._deadline = deadline
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
                raise TrinoError(f'trino did not answer within {self._deadline:g}s')
            answer = self._transport(
                url,
                method=method,
                data=data,
                headers=headers,
                timeout=min(self._timeout, remaining),
            )
            if answer.status not in RETRY_STATUSES:
                break
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
        'X-Trino-User': user or 'pysqlsuggestions',
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
        transport=transport,
        clock=clock,
        sleep=sleep,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trino_http.py -q`
Expected: PASS, 12 tests

- [ ] **Step 5: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/pysqlsuggestions/catalogs/trino_http.py tests/test_trino_http.py
git commit -m "feat: read the Trino catalog over its REST API, with no wheels

The client hard-requires lz4, orjson and zstandard, all three for the
spooled protocol the server only uses when asked. Not asking gives inline
JSON, which is all a catalog reader needs.

Parameters use the same prepared-statement headers the official client
does. The nextUri loop is bounded by a total deadline rather than a retry
count: a busy coordinator answers 503 and the question is how long a
completion may wait, not how many times we may ask."
```

---

## Task 4: The structural guard

**Files:**
- Modify: `tests/test_purity.py:18` and after `test_import_pulls_in_no_drivers` (line 39-44)

**Interfaces:**
- Consumes: `clickhouse_http`, `trino_http` module paths.
- Produces: nothing other tasks read.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_purity.py`, immediately after `test_import_pulls_in_no_drivers`:

```python
READERS = {'pysqlsuggestions.catalogs.trino_http', 'pysqlsuggestions.catalogs.clickhouse_http'}


def test_import_pulls_in_no_catalog_readers() -> None:
    """
    The stdlib readers are adapters, and no adapter is imported by the package root.

    `test_import_pulls_in_no_drivers` guards the same property against
    third-party drivers, and cannot see these: they take no dependency, so a
    reader reaching `sys.modules` on a bare import would leak past every check
    the project has. Two backends now have their transport inside this library,
    which is the reason the guard needs restating rather than assuming.
    """
    code = 'import sys, pysqlsuggestions; print(" ".join(sorted(sys.modules)))'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    assert not (READERS & loaded), f'a catalog reader leaked into import: {sorted(READERS & loaded)}'
```

- [ ] **Step 2: Run test to verify it passes for the right reason**

Run: `uv run pytest tests/test_purity.py::test_import_pulls_in_no_catalog_readers -q`
Expected: PASS. Then prove the guard actually bites — temporarily add `from pysqlsuggestions.catalogs import trino_http  # noqa: F401` to the end of `src/pysqlsuggestions/__init__.py`, rerun, and expect FAIL with `a catalog reader leaked into import`. Remove the line.

- [ ] **Step 3: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_purity.py
git commit -m "test: the readers must not reach sys.modules on a bare import

They take no dependency, so the existing driver guard cannot see them —
a reader imported from the package root would leak past every check the
project has."
```

---

## Task 5: Wire the readers into the server

**Files:**
- Modify: `lsp/pysqlsuggestions_lsp/connections.py:8-12` (module docstring), `:27-31` (`DRIVERS`)
- Test: `tests/lsp/test_connections.py`

**Interfaces:**
- Consumes: `pysqlsuggestions.catalogs.trino_http`, `pysqlsuggestions.catalogs.clickhouse_http`.
- Produces: `DRIVERS['clickhouse'] == ('pysqlsuggestions.catalogs.clickhouse_http', 'named')` and `DRIVERS['trino'] == ('pysqlsuggestions.catalogs.trino_http', 'qmark')`.

`_connect` and `check.py::_timed_connect` need no change: both `import_module(module)` then call `connect(**arguments)`, and both readers accept every name they pass.

- [ ] **Step 1: Write the failing test**

Add to `tests/lsp/test_connections.py`:

```python
def test_every_dialect_the_library_serves_has_a_catalog() -> None:
    """
    All three backends read a catalog now, and none of them needs a wheel.

    This was three dialects and one driver: pg8000 was pure and the other two
    clients were not, so ClickHouse and Trino resolved a dialect and no catalog.
    The stdlib readers close that, and the assertion is written against the
    whole set rather than the two additions so that a dialect added without a
    reader is visible here rather than as silence at a caret.
    """
    assert set(connections.DRIVERS) == {'postgres', 'clickhouse', 'trino'}


def test_the_readers_are_reached_by_module_path_not_by_import() -> None:
    """DRIVERS names modules so `connections` itself imports no transport."""
    assert connections.DRIVERS['clickhouse'] == ('pysqlsuggestions.catalogs.clickhouse_http', 'named')
    assert connections.DRIVERS['trino'] == ('pysqlsuggestions.catalogs.trino_http', 'qmark')


def test_each_readers_paramstyle_matches_what_it_declares() -> None:
    """
    A paramstyle written twice is a paramstyle that can disagree with itself.

    `DbapiCatalog` is told the value from DRIVERS while the reader rewrites
    against the one it declares, and a mismatch produces valid-looking SQL with
    unsubstituted markers in it — which surfaces as an empty completion list,
    not as an error.
    """
    for dialect, (module, paramstyle) in connections.DRIVERS.items():
        if module.startswith('pysqlsuggestions.'):
            assert import_module(module).paramstyle == paramstyle, dialect


def test_a_clickhouse_profile_opens_a_catalog() -> None:
    """Nothing is connected — this asserts the profile resolves to a catalog at all."""
    profile = connections.Profile(dialect='clickhouse', host='localhost')
    assert connections.open_catalog(profile, connect=lambda _: object()) is not None
```

Add `from importlib import import_module` to that file's imports if it is not already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/lsp/test_connections.py -q`
Expected: FAIL — `AssertionError` on the DRIVERS set, which is `{'postgres', 'trino'}`

- [ ] **Step 3: Write the implementation**

In `lsp/pysqlsuggestions_lsp/connections.py`, replace the module docstring's third paragraph (lines 8-12):

```python
"""
A connection profile, as a catalog.

The dialect comes from the entry-point registry rather than a hard-coded map, so
a third-party dialect works here without this file knowing it exists. The driver
does not, because a driver is a module to import and a paramstyle to declare.

Nothing in `DRIVERS` needs a compiled wheel. Postgres uses pg8000, which is pure;
Trino and ClickHouse use the library's own HTTP readers, because both clients
hard-require compression codecs that ship compiled. That is not incidental — it
is what lets the extension ship one interpreter per platform and the same wheel
set to all of them, and it is why every dialect the library serves is now served
here too.
"""
```

Replace `DRIVERS` (lines 27-31):

```python
DRIVERS: dict[str, tuple[str, str]] = {
    'postgres': ('pg8000.dbapi', 'format'),
    'trino': ('pysqlsuggestions.catalogs.trino_http', 'qmark'),
    'clickhouse': ('pysqlsuggestions.catalogs.clickhouse_http', 'named'),
}
"""
Dialect name to (module, paramstyle). Nothing here is compiled.

Named by module rather than imported so this file pulls in no transport at all,
and so `check.py` can reach the same table without a second list.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/lsp/ -q`
Expected: PASS

- [ ] **Step 5: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lsp/pysqlsuggestions_lsp/connections.py tests/lsp/test_connections.py
git commit -m "feat: every dialect the library serves now reads a catalog

ClickHouse and Trino resolved a dialect and no catalog, because their
clients ship compiled and the extension bundles pure wheels only. The
stdlib readers close that with no dependency added on either side."
```

---

## Task 6: TLS as a connection setting

**Files:**
- Modify: `lsp/pysqlsuggestions_lsp/connections.py` (`Profile`, `from_options`, `_connect`)
- Modify: `lsp/pysqlsuggestions_lsp/check.py` (`_timed_connect`)
- Modify: `editors/vscode/src/profiles.ts:13-20` (`Profile`), `:44-53` (`readProfiles`)
- Modify: `editors/vscode/package.json` connections schema
- Modify: `editors/vscode/src/extension.ts` where `initializationOptions` is built
- Test: `tests/lsp/test_connections.py`, `editors/vscode/src/test/unit/profiles.test.ts`

**Interfaces:**
- Consumes: `connect(secure=...)` from Tasks 2 and 3.
- Produces: `Profile.secure: bool = False` in Python; `Profile.secure?: boolean` in TypeScript; `"secure"` in the settings schema.

Without this the readers only ever reach a plaintext endpoint, which makes Trino
with a password unusable — Task 3's `connect` raises for exactly that
combination.

- [ ] **Step 1: Write the failing Python test**

Add to `tests/lsp/test_connections.py`:

```python
def test_secure_defaults_to_false_and_survives_the_wire() -> None:
    """
    A profile that says nothing is plaintext, and one that says so is not.

    Defaulting the other way would be safer in the abstract and wrong here: the
    docker fixtures are plaintext, and a default that breaks every local setup
    to protect a remote one nobody described is a default people turn off.
    """
    assert connections.Profile(dialect='trino', host='h').secure is False
    options = {'dialect': 'trino', 'host': 'h', 'secure': True}
    profile = connections.Profile.from_options(options)
    assert profile is not None
    assert profile.secure is True


def test_a_non_boolean_secure_is_ignored_like_every_other_bad_field() -> None:
    """This is whatever the client put on the wire, and `"true"` is not True."""
    profile = connections.Profile.from_options({'dialect': 'trino', 'host': 'h', 'secure': 'yes'})
    assert profile is not None
    assert profile.secure is False


def test_secure_reaches_the_driver() -> None:
    """A flag that is parsed and not passed is worse than one that does not exist."""
    seen: dict[str, object] = {}

    def fake_connect(**arguments: object) -> object:
        seen.update(arguments)
        return object()

    monkeypatched = connections.Profile(dialect='clickhouse', host='h', secure=True)
    connections._connect(monkeypatched, opener=fake_connect)  # noqa: SLF001
    assert seen['secure'] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/lsp/test_connections.py -q`
Expected: FAIL — `TypeError: Profile.__init__() got an unexpected keyword argument 'secure'`

- [ ] **Step 3: Write the Python implementation**

In `connections.py`, add to `Profile` after `password`:

```python
    secure: bool = False
    """
    Whether to speak TLS.

    Default false because the docker fixtures and most local backends are
    plaintext, and a default that breaks every local setup to protect a remote
    one nobody described is a default people turn off rather than one that
    protects anybody. Trino refuses password authentication without it, so a
    remote profile that needs one is told at connect time rather than left to
    leak a credential.
    """
```

In `from_options`, add to the constructor call:

```python
            secure=options.get('secure') is True,
```

`is True` rather than `bool(...)`: the field is type-checked like every other,
and `"yes"` arriving from hand-edited settings is not a truthy boolean here.

Refactor `_connect` so a test can substitute the opener without patching
`import_module`, and pass `secure`:

```python
def _connect(profile: Profile, opener: Callable[..., Any] | None = None) -> Any:
    """
    Open a connection with the driver the dialect names.

    `opener` exists so a test can see what would be passed. Patching
    `import_module` instead would assert on a mock rather than on the arguments,
    which is the part that can be wrong.
    """
    module, _ = DRIVERS[profile.dialect]
    connect_to = opener or import_module(module).connect
    arguments: dict[str, Any] = {'host': profile.host, 'secure': profile.secure}
    for name, value in (
        ('port', profile.port),
        ('database', profile.database),
        ('user', profile.user),
        ('password', profile.password),
    ):
        if value is not None:
            arguments[name] = value
    return connect_to(**arguments)
```

pg8000's `connect` has no `secure` parameter, so guard it — replace the
`arguments` line with:

```python
    arguments: dict[str, Any] = {'host': profile.host}
    # pg8000 takes `ssl_context`, not `secure`; only the readers understand this
    # flag, and passing it to a driver that has never heard of it is a TypeError
    # on the first catalog read rather than at configuration time.
    if module.startswith('pysqlsuggestions.'):
        arguments['secure'] = profile.secure
```

Apply the same two changes to `check.py::_timed_connect`.

- [ ] **Step 4: Run the Python test**

Run: `uv run pytest tests/lsp/ -q`
Expected: PASS

- [ ] **Step 5: Write the failing TypeScript test**

Add to `editors/vscode/src/test/unit/profiles.test.ts`:

```ts
test('secure is read when it is a boolean and dropped when it is not', () => {
  const profiles = readProfiles([
    { name: 'a', dialect: 'trino', host: 'h', secure: true },
    { name: 'b', dialect: 'trino', host: 'h', secure: 'yes' },
    { name: 'c', dialect: 'trino', host: 'h' },
  ]);
  assert.deepStrictEqual(
    profiles.map((profile) => profile.secure),
    [true, undefined, undefined],
  );
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd editors/vscode && npm run check`
Expected: FAIL — `Property 'secure' does not exist on type 'Profile'`

- [ ] **Step 7: Write the TypeScript implementation**

In `editors/vscode/src/profiles.ts`, add to the `Profile` interface:

```ts
  /** TLS. Undefined and false mean the same thing; the server defaults to plaintext. */
  secure?: boolean;
```

and to the object pushed in `readProfiles`:

```ts
      secure: typeof record.secure === 'boolean' ? record.secure : undefined,
```

In `editors/vscode/package.json`, add to the connection item's `properties`:

```json
              "secure": { "type": "boolean", "default": false, "description": "Speak TLS. Trino requires it before it will accept a password." }
```

In `extension.ts`, add `secure: profile.secure` where `initializationOptions` is
assembled alongside `dialect`, `host`, `port`, `database` and `user`.

- [ ] **Step 8: Run both suites**

Run: `cd editors/vscode && npm run check` then `cd ../.. && ./scripts/check.sh`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add lsp/pysqlsuggestions_lsp/connections.py lsp/pysqlsuggestions_lsp/check.py tests/lsp/test_connections.py editors/vscode/src/profiles.ts editors/vscode/src/test/unit/profiles.test.ts editors/vscode/package.json editors/vscode/src/extension.ts
git commit -m "feat: a connection can say it speaks TLS

The readers reach a plaintext endpoint or none, which makes a remote
Trino unusable — it refuses password authentication without TLS, so the
reader raises for that pair rather than leaking the credential.

Default false: the docker fixtures are plaintext, and a default that
breaks every local setup to protect a remote one nobody described is one
people turn off rather than one that protects anybody."
```

---

## Task 7: The readers against the real backends

**Files:**
- Modify: `tests/integration/conftest.py:43-79`
- Modify: `tests/integration/test_backends.py` (add three tests at the end)

**Interfaces:**
- Consumes: `connect` from Tasks 2 and 3.
- Produces: the `clickhouse_catalog` and `trino_catalog` fixtures, now reader-backed.

The ClickHouse HTTP port is already published as `57123` in
`docker/docker-compose.yml`; Trino's `57080` is its HTTP port already.

- [ ] **Step 1: Rewrite the two fixtures**

In `tests/integration/conftest.py`, change the port constant and both fixtures:

```python
POSTGRES_DSN = 'postgresql://report:report@localhost:57432/report_service'
CLICKHOUSE_HOST, CLICKHOUSE_PORT = 'localhost', 57123
TRINO_HOST, TRINO_PORT = 'localhost', 57080
```

```python
@pytest.fixture(scope='session')
def clickhouse_catalog() -> Iterator[DbapiCatalog]:
    """
    A catalog over the library's own HTTP reader, which speaks `named`.

    Port 57123, not 57900: this is the HTTP interface, not the native protocol
    the compiled client used. Nothing is skip-guarded on an import any more —
    the reader is part of the library — so the only reason to skip is a backend
    that is not up.
    """
    from pysqlsuggestions.catalogs import clickhouse_http

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

    `_reconnecting_cursor` is gone: it existed because a `trino` client cursor is
    single-use, and `Connection.cursor()` here returns a fresh one every time by
    construction.
    """
    from pysqlsuggestions.catalogs import trino_http

    connection = trino_http.connect(host=TRINO_HOST, port=TRINO_PORT, user='pysqlsuggestions', database='postgresql')
    try:
        connection.cursor().execute('SELECT 1')
    except Exception as error:  # noqa: BLE001
        _skip('trino', error)
    yield DbapiCatalog(connection.cursor, TRINO, paramstyle=trino_http.paramstyle)
    connection.close()
```

Delete `_reconnecting_cursor` and the now-unused `Callable` / `Cursor` / `Any`
imports.

- [ ] **Step 2: Run the existing backend tests against the readers**

Run: `docker compose -f docker/docker-compose.yml up -d --wait && uv run pytest tests/integration -m integration -q`
Expected: PASS. Every existing ClickHouse and Trino assertion in
`test_backends.py`, `test_acceptance.py` and `test_lsp_backends.py` now runs
through the readers. **This is the real conformance test for Tasks 2 and 3** —
if a row mapper receives a differently-shaped value than it did from the
compiled client, it fails here.

- [ ] **Step 3: Add the three tests the readers can fail that the old clients could not**

Append to `tests/integration/test_backends.py`:

```python
def test_clickhouse_row_counts_are_integers_not_strings(clickhouse_catalog: DbapiCatalog) -> None:
    """
    UInt64 over JSON is quoted unless the reader says otherwise.

    `Table.rows` feeds ranking, and a string would sort lexically — '9' above
    '10' — which is a wrong order in a list that still looks entirely healthy.
    """
    tables = {table.name: table for table in clickhouse_catalog.tables('analytics')}
    counted = [table for table in tables.values() if table.rows is not None]
    assert counted, 'no ClickHouse table reported a row count'
    assert all(isinstance(table.rows, int) for table in counted)


def test_clickhouse_columns_bind_two_parameters(clickhouse_catalog: DbapiCatalog) -> None:
    """The `columns` query is the only one taking $1 and $2, so it is the one that proves binding."""
    columns = clickhouse_catalog.columns('analytics', 'report_executions')
    assert columns
    assert [column.position for column in columns] == sorted(column.position for column in columns)


def test_trino_pages_through_a_result_larger_than_one_response(trino_catalog: DbapiCatalog) -> None:
    """
    `SHOW FUNCTIONS` returns well over a thousand rows, which Trino splits.

    A reader that stopped at the first page would return a plausible-looking
    subset, and nothing else in the suite is big enough to notice.
    """
    functions = trino_catalog.functions()
    assert len(functions) > 500
```

- [ ] **Step 4: Run them**

Run: `uv run pytest tests/integration/test_backends.py -m integration -q`
Expected: PASS

- [ ] **Step 5: Confirm they skip without docker**

Run: `docker compose -f docker/docker-compose.yml down -v && uv run pytest tests/integration -m integration -q`
Expected: all skipped, none failed. Then bring docker back up.

- [ ] **Step 6: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/integration/conftest.py tests/integration/test_backends.py
git commit -m "test: the backend suite is the readers' conformance suite

Both fixtures now open the library's own readers, so every ClickHouse
and Trino assertion the suite already had runs through them. Three new
ones cover what only a reader can get wrong: JSON-quoted UInt64 sorting
lexically in ranking, two-parameter binding, and a Trino result large
enough to be paged."
```

---

## Task 8: Say so in the docs

**Files:**
- Modify: `editors/vscode/package.json` (the `dialect` enum `markdownDescription`)
- Modify: `editors/vscode/README.md:25`
- Modify: `README.md:7` if it claims a driver requirement per backend — check first
- Modify: `CHANGELOG.md` under `## Unreleased`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Replace the settings description that is now false**

In `editors/vscode/package.json`, the `dialect` property currently says only
`postgres` reads a catalog. Replace its `markdownDescription` with:

```json
                "markdownDescription": "`postgres`, `clickhouse` and `trino` all read a catalog. `ansi` completes from the statement alone."
```

- [ ] **Step 2: Fix the README claim**

`editors/vscode/README.md:25` reads "**PostgreSQL**, for anything schema-aware.
ClickHouse, Trino and `ansi` are…". Replace that paragraph with:

```markdown
**PostgreSQL, ClickHouse and Trino** all read a catalog, so completion is
schema-aware against any of them. Postgres additionally answers foreign keys,
column search and most-common-values, which is where join proposals and value
hints come from — the other two declare no constraints, so they get neither.

`ansi` completes from the statement alone: keywords, aliases, CTE columns and
select-list names, with no connection at all.
```

- [ ] **Step 3: Check and fix the library README**

Run: `grep -n "pure\|driver\|psycopg\|pg8000" README.md`
If any line claims a backend needs a driver the library does not have, or that
only Postgres is schema-aware in the extension, correct it in place. The
library's own extras are unchanged — `trino` still installs the real client —
so a line describing *extras* stays as it is.

- [ ] **Step 4: Write the changelog entry**

Under `## Unreleased` in `CHANGELOG.md`, add:

```markdown
### ClickHouse and Trino answer from a catalog in the editor

Both read one now, so `FROM ⌶`, `db.⌶` and `alias.⌶` offer real relations and
columns against either backend instead of keywords alone. Neither declares
foreign keys, so join proposals stay Postgres-only; Trino ships no
relation-search query, so a bare prefix still finds nothing there. Both were
already true for library users and are now visible in the extension.

The readers are the library's own, over each backend's HTTP interface. Their
clients hard-require lz4, orjson, zstandard or a C extension — every one of
them to compress a wire carrying seven introspection queries — and dropping
them is what lets the same wheel set install on every platform.

A connection can now say `secure` to speak TLS. Trino refuses password
authentication without it and says so at connect time rather than sending the
password anyway.
```

- [ ] **Step 5: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add editors/vscode/package.json editors/vscode/README.md README.md CHANGELOG.md
git commit -m "docs: three backends read a catalog, not one

The settings description and the extension README both told users that
only Postgres was schema-aware, which stopped being true two commits ago."
```

---

## Self-Review

**Spec coverage (§5 of the design):**

| Spec requirement | Task |
| --- | --- |
| `catalogs/trino_http.py`, `nextUri` paging, prepared-statement headers, total deadline | 3 |
| `catalogs/clickhouse_http.py`, `JSONCompact`, header credentials, `param_` binding | 2 |
| `render()` unchanged; `:p1` → `{p1:String}` owned by the reader | 2 (`_typed`), asserted in Task 5 |
| Readers additive — `trino` extra and real clients still supported | untouched; `lsp/pyproject.toml` not modified by any task |
| Readers not imported by the package root; new structural guard | 4 |
| Auth scope: username, password over TLS; TLS via `ssl.create_default_context()` | 1 (`request`), 3 (`connect` refusal), 6 (the setting) |
| Existing integration suite becomes the conformance suite | 7 |
| Recorded payload tests: paging, `error` body, absent `data`, `param_` rewriting, `EXECUTE … USING` with an embedded quote, the deadline | 2 and 3 — every one has a named test |

**Type consistency:** `Transport` is defined in Task 1 and consumed by name in
Tasks 2 and 3. `Response.status`/`.body`/`.json()`/`.text()` are used exactly as
declared. `connect(...)` keyword names match what `connections.py::_connect` and
`check.py::_timed_connect` splat, including `timeout`; Task 6 adds `secure` to
both call sites and both readers accept it. `paramstyle` is declared at module
level in Tasks 2 and 3 and cross-checked against `DRIVERS` in Task 5.

**Two things left deliberately undone**, neither a placeholder:

- `pysqlsuggestions_lsp`'s `trino` extra stays in `lsp/pyproject.toml`. The
  spec says the readers are additive, and removing it would break a library
  user who wants Kerberos.
- `check.py`'s `describe()` maps pg8000's error shapes. Reader errors carry a
  readable `str` already, so they fall through its final `' '.join(...)` branch,
  which is correct — no change is needed and none is made.
