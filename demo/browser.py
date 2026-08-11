"""
The demo with no server: the same pipeline, running in the page.

Loaded under Pyodide by `static/browser.js`. GitHub Pages serves static files
and nothing else, so there is no FastAPI process and no database — but the
library's core is pure and `MemoryCatalog` exists to be handed a pre-fetched
snapshot, which is exactly this situation. `demo/payload.py` builds the same
JSON the server does, so the page cannot tell which one answered.

Trino is absent. Its namespace has three levels and `MemoryCatalog` is keyed by
two; a faithful static Trino would mean bending the library to suit a demo.
"""

from __future__ import annotations

import json
from typing import Any

from demo.payload import backend_entry, respond

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import ColumnValue, Function

DIALECTS: dict[str, Dialect] = {'postgres': POSTGRES, 'clickhouse': CLICKHOUSE}

LABELS = {
    'postgres': ('PostgreSQL', 'schema.table — two levels'),
    'clickhouse': ('ClickHouse', 'database.table — two levels, case preserved'),
}

EXAMPLES = {
    'postgres': (
        'SELECT r.name, d.title\nFROM reports_report r\nJOIN reports_database d ON d.id = r.database_id\nWHERE r.'
    ),
    'clickhouse': 'SELECT report_id, count() AS runs\nFROM report_executions e\nWHERE e.',
}


class Demo:
    """Everything the page asks for, answered from a snapshot."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._catalogs = {key: _catalog(part) for key, part in snapshot.items() if key in DIALECTS}
        self._caches: dict[str, dict[Any, Any]] = {key: {} for key in self._catalogs}

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
            if key in LABELS
        ]
        return json.dumps({'backends': rows})

    def suggest(self, sql: str, caret: int, backend: str, limit: int) -> str:
        """Suggestions plus the derived Request, as JSON, in the server's shape."""
        dialect = DIALECTS.get(backend)
        catalog = self._catalogs.get(backend)
        if dialect is None or catalog is None:
            return json.dumps({'error': f'unknown backend {backend!r}'})
        found = respond(sql, caret, dialect, catalog, cache=self._caches[backend], limit=limit)
        return json.dumps(found)


def _catalog(part: dict[str, Any]) -> MemoryCatalog:
    """Rebuild a MemoryCatalog from the exported shape."""
    snapshot: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
    kinds: dict[tuple[str, str], str] = {}
    rows: dict[tuple[str, str], int] = {}
    values: dict[tuple[str, str, str], list[ColumnValue]] = {}

    for table in part['tables']:
        key = (table['schema'], table['name'])
        snapshot[key] = [(c['name'], c['type'], c['position']) for c in table['columns']]
        kinds[key] = table['kind']
        if table.get('rows') is not None:
            rows[key] = int(table['rows'])
        for column, found in (table.get('values') or {}).items():
            values[table['schema'], table['name'], column] = [
                ColumnValue(text=v['text'], frequency=v['frequency']) for v in found
            ]

    return MemoryCatalog(
        snapshot,
        functions=[
            Function(schema=f['schema'], name=f['name'], args=f['args'], result=f['result']) for f in part['functions']
        ],
        table_kinds=kinds,
        table_rows=rows,
        values=values,
    )
