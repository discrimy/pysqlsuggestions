"""
The demo with no server: the same pipeline, running in the page.

Loaded under Pyodide by `static/browser.js`. GitHub Pages serves static files
and nothing else, so there is no FastAPI process and no database — but the
library's core is pure and `MemoryCatalog` exists to be handed a pre-fetched
snapshot, which is exactly this situation. `demo/payload.py` builds the same
JSON the server does, so the page cannot tell which one answered.

The schema is `demo/schema.py`, invented for the demo and carried as Python
rather than exported from anywhere. Trino is absent: its namespace has three
levels and `MemoryCatalog` is keyed by two, so a faithful static Trino would
mean bending the library to suit a demo.
"""

from __future__ import annotations

import json

from demo import schema
from demo.payload import backend_entry, respond

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES

DIALECTS: dict[str, Dialect] = {'postgres': POSTGRES, 'clickhouse': CLICKHOUSE}

LABELS = {
    'postgres': ('PostgreSQL', 'schema.table — two levels'),
    'clickhouse': ('ClickHouse', 'database.table — two levels, case preserved'),
}

EXAMPLES = {
    'postgres': (
        'SELECT f.number, a.name, b.cabin\n'
        'FROM flight f\n'
        'JOIN airline a ON a.id = f.airline_id\n'
        'JOIN booking b ON b.flight_id = f.id\n'
        'WHERE f.'
    ),
    'clickhouse': ('SELECT airport, count() AS events\nFROM flight_event e\nWHERE e.'),
}


class Demo:
    """Everything the page asks for, answered from the demo schema."""

    def __init__(self) -> None:
        self._catalogs: dict[str, MemoryCatalog] = {
            'postgres': schema.postgres(),
            'clickhouse': schema.clickhouse(),
        }
        self._caches: dict[str, dict[object, object]] = {key: {} for key in self._catalogs}

    def backends(self) -> str:
        """The tab strip, as JSON."""
        rows = [
            backend_entry(
                key,
                LABELS[key][0],
                DIALECTS[key],
                LABELS[key][1],
                EXAMPLES[key],
                available=key in self._catalogs,
            )
            for key in DIALECTS
        ]
        return json.dumps({'backends': rows})

    def suggest(self, sql: str, caret: int, backend: str, limit: int) -> str:
        """Suggestions plus the derived Request, as JSON, in the server's shape."""
        dialect = DIALECTS.get(backend)
        catalog = self._catalogs.get(backend)
        if dialect is None or catalog is None:
            return json.dumps({'error': f'unknown backend {backend!r}'})
        return json.dumps(respond(sql, caret, dialect, catalog, cache=self._caches[backend], limit=limit))
