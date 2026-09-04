"""
A web demo autocompleting against the docker backends.

    docker compose -f docker/docker-compose.yml up -d --wait
    uv run uvicorn demo.app:app --reload --port 8000

Point the Postgres backend somewhere else with PYSQLSUGGESTIONS_PG_DSN:

    PYSQLSUGGESTIONS_PG_DSN=postgresql://user:pw@host:5432/db \
        uv run uvicorn demo.app:app --port 8000

Nothing here belongs in the library. It exists to show the pipeline working
against real servers, and to make the parts that are normally invisible — the
derived Request, the clause, the scope — visible while you type.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from demo.payload import MAX_PENDING, MAX_SQL_LENGTH, backend_entry, respond
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from pysqlsuggestions.caches import MemoryCache, cache_key
from pysqlsuggestions.catalogs.dbapi import Cursor, DbapiCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import Availability

HERE = Path(__file__).parent
DEFAULT_LIMIT = 25
MAX_LIMIT = 200
"""
The most suggestions a request may ask for.

`complete` forwards `limit * 5` to `resolve`, so an unbounded field here is an
unbounded read there — and the page shows a list, which nobody scrolls two
hundred of.
"""


@dataclass(frozen=True)
class Backend:
    """One selectable backend in the demo."""

    key: str
    label: str
    dialect: Dialect
    connect: Callable[[], Any]
    note: str

    def open_cursor(self, connection: Any) -> Callable[[], Cursor]:
        """A fresh cursor per query — Trino's are single-use."""
        del self
        return lambda: connection.cursor()


_DOCKER_PG_DSN = 'postgresql://report:report@localhost:57432/report_service'

POSTGRES_DSN = os.environ.get('PYSQLSUGGESTIONS_PG_DSN', _DOCKER_PG_DSN)
"""
Where the Postgres backend connects, overridable so the demo can be pointed at
a real database.

From the environment rather than a file, because a DSN carries a password and
this repository has no ignored place to keep one. The default is the docker
fixture, so `docker compose up` still needs no configuration at all.

Everything this issues is a read of `pg_catalog` and `pg_stats`. No user table
is queried, and nothing is written.
"""


def _postgres() -> Any:
    import psycopg2

    return psycopg2.connect(POSTGRES_DSN, connect_timeout=10)


def _clickhouse() -> Any:
    from clickhouse_driver import dbapi

    return dbapi.connect(host='localhost', port=57900, user='report', password='report', database='analytics')


def _trino() -> Any:
    from trino import dbapi

    return dbapi.connect(host='localhost', port=57080, user='pysqlsuggestions', catalog='postgresql')


BACKENDS = {
    backend.key: backend
    for backend in (
        Backend('postgres', 'PostgreSQL', POSTGRES, _postgres, 'schema.table — two levels'),
        Backend('clickhouse', 'ClickHouse', CLICKHOUSE, _clickhouse, 'database.table — two levels, case preserved'),
        Backend('trino', 'Trino', TRINO, _trino, 'catalog.schema.table — three levels, federated'),
    )
}

PARAMSTYLE = {'postgres': 'format', 'clickhouse': 'pyformat', 'trino': 'qmark'}

EXAMPLES = {
    'postgres': (
        'SELECT r.name, d.title\nFROM reports_report r\nJOIN reports_database d ON d.id = r.database_id\nWHERE r.'
    ),
    'clickhouse': 'SELECT report_id, count() AS runs\nFROM report_executions e\nWHERE e.',
    'trino': (
        'SELECT p.name, c.duration_ms\n'
        'FROM postgresql.public.reports_report p\n'
        'JOIN clickhouse.analytics.report_executions c ON c.report_id = p.id\n'
        'WHERE c.'
    ),
}

app = FastAPI(title='pysqlsuggestions demo')

_connections: dict[str, Any] = {}
_caches: dict[str, MemoryCache] = {}
_examples: dict[str, str] = {}
"""Examples discovered from the connected database, overriding the fixture ones."""


def _discovered_example(key: str, catalog: DbapiCatalog) -> str | None:
    """
    An opening query built from a relation this database actually has.

    The shipped examples name the docker fixture's tables, which say nothing on
    someone else's database. Rather than show a query that cannot resolve, pick
    a relation and start one.

    A relation holding a column this role may not read wins, where there is one.
    Otherwise the first relation with any columns at all.

    The preference matters more than it looks. Opening on a relation nothing is
    withheld from shows a correct list that demonstrates nothing, and the first
    relation alphabetically is exactly as likely to be that one as not — which
    is how pointing the demo at a restricted role produced a page with no
    evidence of the restriction anywhere on it.
    """
    if key != 'postgres' or POSTGRES_DSN == _DOCKER_PG_DSN:
        return None
    with suppress(Exception):
        fallback: str | None = None
        for table in catalog.tables(None):
            columns = catalog.columns(table.schema, table.name)
            if not columns:
                continue
            opening = f'SELECT *\nFROM {table.name} t\nWHERE t.'
            if any(column.availability is Availability.RESTRICTED for column in columns):
                return opening
            fallback = fallback or opening
        return fallback
    return None


def _catalog(key: str) -> DbapiCatalog | None:
    """A catalog for `key`, or None when that backend is not reachable."""
    backend = BACKENDS[key]
    if key not in _connections:
        with suppress(Exception):
            _connections[key] = backend.connect()
    connection = _connections.get(key)
    if connection is None:
        return None
    return DbapiCatalog(backend.open_cursor(connection), backend.dialect, paramstyle=PARAMSTYLE[key])


class SuggestRequest(BaseModel):
    """What the editor sends on each keystroke."""

    sql: str = Field(default='', max_length=MAX_SQL_LENGTH)
    caret: int = Field(default=0, ge=0, le=MAX_SQL_LENGTH)
    backend: str = 'postgres'
    limit: int = Field(default=DEFAULT_LIMIT, ge=0, le=MAX_LIMIT)
    pending: list[int] = Field(default_factory=list, max_length=MAX_PENDING)
    """
    Template blanks still outstanding, as the last insertion handed them back.

    Bounded like the rest of them. The library is total over these — a caret
    past the end clamps, a negative limit now clamps too, and a nonsense blank
    is ignored — so none of this is a correctness guard. It is a public HTTP
    surface declining to allocate whatever was posted to it, which is a
    different question and one the route has to answer for itself.
    """


def _warm(key: str) -> None:
    """
    Pull the expensive metadata into the cache before anyone types.

    Trino's ClickHouse connector takes about ten seconds to answer its first
    metadata query — `SHOW SCHEMAS FROM clickhouse` costs the same, so this is
    connector startup rather than anything the query text can fix. Paying it on a
    keystroke would blow the latency budget by two orders of magnitude; paying it
    at boot means the first keystroke is already warm.
    """
    catalog = _catalog(key)
    if catalog is None:
        return
    cache = _caches.setdefault(key, MemoryCache())
    dialect = BACKENDS[key].dialect
    found = _discovered_example(key, catalog)
    if found is not None:
        _examples[key] = found
    with suppress(Exception):
        for name in ('', *catalog.schemas()):
            # `name or None` on both sides: the reader asks for the default namespace as
            # `None`, and this wrote it under `''`, so neither half of the warm-up was
            # ever read back. `cache_key` is what keeps the two ends from drifting again.
            schema = name or None
            cache.set(cache_key('demo', dialect.name, 'schemas', schema), catalog.schemas(schema))
            cache.set(cache_key('demo', dialect.name, 'tables', schema), catalog.tables(schema))


@app.on_event('startup')
def warm_all() -> None:
    """Warm every reachable backend in the background, so startup does not block."""
    for key in BACKENDS:
        threading.Thread(target=_warm, args=(key,), daemon=True).start()


@app.get('/')
def index() -> FileResponse:
    """The editor page."""
    return FileResponse(HERE / 'static' / 'index.html')


@app.get('/api/backends')
def backends() -> dict[str, Any]:
    """Which backends exist and which are currently reachable."""
    return {
        'backends': [
            backend_entry(
                backend.key,
                backend.label,
                backend.dialect,
                backend.note,
                _examples.get(backend.key, EXAMPLES[backend.key]),
                available=_catalog(backend.key) is not None,
                paramstyle=PARAMSTYLE[backend.key],
            )
            for backend in BACKENDS.values()
        ],
    }


@app.post('/api/suggest')
def suggest(payload: SuggestRequest) -> JSONResponse:
    """
    Suggestions for the caret, plus the Request that produced them.

    The Request is returned alongside so the page can show what the pure stages
    decided before anything was fetched — which is the part of a completion
    engine you normally cannot see.
    """
    backend = BACKENDS.get(payload.backend)
    if backend is None:
        return JSONResponse({'error': f'unknown backend {payload.backend!r}'}, status_code=400)

    caret = payload.caret
    catalog = _catalog(backend.key)
    cache = _caches.setdefault(backend.key, MemoryCache())

    return JSONResponse(
        respond(
            payload.sql,
            caret,
            backend.dialect,
            catalog,
            cache=cache,
            limit=payload.limit,
            pending=tuple(payload.pending),
        ),
    )
