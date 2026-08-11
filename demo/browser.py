"""
The demo with no server: the same pipeline, running in the page.

Loaded under Pyodide by `static/browser.js`. GitHub Pages serves static files
and nothing else, so there is no FastAPI process and no database — but the
library's core is pure and `MemoryCatalog` exists to be handed a pre-fetched
snapshot, which is exactly this situation. `demo/payload.py` builds the same
JSON the server does, so the page cannot tell which one answered.

The schema is `demo/schema.py`, invented for the demo and carried as Python
rather than exported from anywhere. Trino is here too: the `Catalog` port
passes one name at each level, so a snapshot with a catalog mapping serves
three levels as readily as two.
"""

from __future__ import annotations

import json

from demo import schema
from demo.payload import backend_entry, respond

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO

DIALECTS: dict[str, Dialect] = {'postgres': POSTGRES, 'clickhouse': CLICKHOUSE, 'trino': TRINO}

LABELS = {
    'postgres': ('PostgreSQL', 'schema.table — two levels'),
    'clickhouse': ('ClickHouse', 'database.table — two levels, case preserved'),
    'trino': ('Trino', 'catalog.schema.table — three levels, federated'),
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
    'trino': (
        'SELECT f.number, e.delay_minutes\n'
        'FROM warehouse.public.flight f\n'
        'JOIN events.analytics.flight_event e ON e.flight_id = f.id\n'
        'WHERE f.'
    ),
}


class Demo:
    """Everything the page asks for, answered from the demo schema."""

    def __init__(self) -> None:
        self._catalogs: dict[str, MemoryCatalog] = {
            'postgres': schema.postgres(),
            'clickhouse': schema.clickhouse(),
            'trino': schema.trino(),
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

    def suggest(self, sql: str, caret: int, backend: str, limit: int, pending: list[int] | None = None) -> str:
        """Suggestions plus the derived Request, as JSON, in the server's shape."""
        dialect = DIALECTS.get(backend)
        catalog = self._catalogs.get(backend)
        if dialect is None or catalog is None:
            return json.dumps({'error': f'unknown backend {backend!r}'})
        found = respond(
            sql,
            caret,
            dialect,
            catalog,
            cache=self._caches[backend],
            limit=limit,
            pending=tuple(pending or ()),
        )
        return json.dumps(found)
