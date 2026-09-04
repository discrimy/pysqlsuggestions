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
from demo.payload import MAX_PENDING, MAX_SQL_LENGTH, backend_entry, respond

from pysqlsuggestions.caches import MemoryCache
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
        self._caches: dict[str, MemoryCache] = {key: MemoryCache() for key in self._catalogs}

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

    def suggest(self, body: str) -> str:
        """
        Suggestions plus the derived Request, as JSON, in the server's shape.

        One JSON argument rather than one argument per field, and the same body
        the server's route receives. Spelling the fields out here meant naming
        them again in the page's call, where a field could be — and was —
        forgotten silently: `pending` has a default, so dropping it crossing
        into Pyodide raised nothing and merely stopped the template advancing.
        Carrying the body whole leaves nothing to keep in step.
        """
        request = json.loads(body)
        backend = str(request.get('backend', 'postgres'))
        dialect = DIALECTS.get(backend)
        catalog = self._catalogs.get(backend)
        if dialect is None or catalog is None:
            return json.dumps({'error': f'unknown backend {backend!r}'})
        sql = str(request.get('sql', ''))
        # The same bound the server route declares, from the same constant. This
        # runs on the page's own thread, so a statement long enough to be slow
        # freezes the tab it was typed into — there is no process boundary here
        # to absorb it and no request timeout to end it.
        if len(sql) > MAX_SQL_LENGTH:
            return json.dumps({'error': f'statement longer than {MAX_SQL_LENGTH} characters'})
        found = respond(
            sql,
            int(request.get('caret', 0)),
            dialect,
            catalog,
            cache=self._caches[backend],
            limit=int(request.get('limit', 25)),
            pending=tuple(request.get('pending') or ())[:MAX_PENDING],
        )
        return json.dumps(found)
