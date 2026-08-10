"""
A web demo autocompleting against the docker backends.

    docker compose -f docker/docker-compose.yml up -d --wait
    uv run uvicorn demo.app:app --reload --port 8000

Nothing here belongs in the library. It exists to show the pipeline working
against real servers, and to make the parts that are normally invisible — the
derived Request, the clause, the scope — visible while you type.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.catalogs.dbapi import Cursor, DbapiCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO

HERE = Path(__file__).parent
MAX_SQL_LENGTH = 20_000
DEFAULT_LIMIT = 25


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


def _postgres() -> Any:
    import psycopg2

    return psycopg2.connect('postgresql://report:report@localhost:57432/report_service')


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
_caches: dict[str, dict[Any, Any]] = {}


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
    caret: int = 0
    backend: str = 'postgres'
    limit: int = DEFAULT_LIMIT


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
    cache = _caches.setdefault(key, {})
    dialect = BACKENDS[key].dialect
    with suppress(Exception):
        for name in ('', *catalog.schemas()):
            cache[('demo', dialect.name, name, '\x00schemas')] = catalog.schemas(name or None)
            cache[('demo', dialect.name, name, '')] = catalog.tables(name or None)


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
            {
                'key': backend.key,
                'label': backend.label,
                'note': backend.note,
                'levels': list(backend.dialect.namespace.levels),
                'paramstyle': PARAMSTYLE[backend.key],
                'example': EXAMPLES[backend.key],
                'available': _catalog(backend.key) is not None,
            }
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

    caret = max(0, min(payload.caret, len(payload.sql)))
    started = time.perf_counter()
    request = derive_request(payload.sql, caret, backend.dialect)
    catalog = _catalog(backend.key)
    cache = _caches.setdefault(backend.key, {})

    suggestions = complete(
        payload.sql,
        caret,
        backend.dialect,
        catalog,
        cache=cache,
        identity='demo',
        limit=payload.limit,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    return JSONResponse(
        {
            'available': catalog is not None,
            'elapsed_ms': round(elapsed_ms, 2),
            'request': _describe(request),
            'suggestions': [
                {
                    'text': s.text,
                    'kind': s.kind.value,
                    'detail': s.detail,
                    'score': s.score,
                    'replace_span': list(s.replace_span),
                }
                for s in suggestions
            ],
        },
    )


def _describe(request: Any) -> dict[str, Any]:
    """The Request, flattened for the page."""
    return {
        'clause': request.clause,
        'expecting': request.expecting,
        'prefix': request.prefix,
        'qualifier': list(request.qualifier),
        'kinds': [kind.value for kind in request.kinds],
        'replace_span': list(request.replace_span),
        'relations': [
            {
                'label': relation.label,
                'path': list(relation.path),
                'source': relation.source,
                'projection': _projection(relation),
            }
            for relation in _visible(request.scope)
        ],
        'ctes': sorted(request.scope.ctes) if request.scope else [],
    }


def _visible(scope: Any) -> Iterator[Any]:
    if scope is None:
        return
    yield from scope.visible()


def _projection(relation: Any) -> dict[str, Any] | None:
    """How much of this relation the statement described itself."""
    if relation.projection is None:
        return None
    return {
        'columns': list(relation.projection.columns),
        'stars': [star.label for star in relation.projection.stars],
    }
