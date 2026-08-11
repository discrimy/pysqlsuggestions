"""
Freeze the docker fixtures into a JSON snapshot the browser demo can carry.

    docker compose -f docker/docker-compose.yml up -d --wait
    uv run python scripts/export_snapshot.py

The static demo has no server and no database, so it runs the whole pipeline
over `MemoryCatalog` — which is exactly what that class is for. Only the fixture
schemas are exported. Nothing here should ever be pointed at a real database:
`most_common_vals` holds literal values out of the rows, and this file is
published.

Trino is left out. Its namespace has three levels and `MemoryCatalog` is keyed
by two, so a faithful static Trino would mean bending the library to suit a
demo. It stays a live-server backend.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES

OUT = Path(__file__).resolve().parent.parent / 'demo' / 'snapshot.json'

MAX_VALUES = 12
"""Enough for the dropdown to be worth reading, few enough to keep the file small."""


def _postgres() -> Any:
    import psycopg2

    return psycopg2.connect('postgresql://report:report@localhost:57432/report_service')


def _clickhouse() -> Any:
    from clickhouse_driver import dbapi

    return dbapi.connect(host='localhost', port=57900, user='report', password='report', database='analytics')


def _export(catalog: DbapiCatalog, dialect: Dialect, schemas: list[str]) -> dict[str, Any]:
    """Every relation in `schemas`, with its columns, size and frequent values."""
    tables: list[dict[str, Any]] = []
    for schema in schemas:
        for table in catalog.tables(schema):
            columns = catalog.columns(table.schema, table.name)
            if not columns:
                continue
            tables.append(
                {
                    'schema': table.schema,
                    'name': table.name,
                    'kind': table.kind,
                    'rows': table.rows,
                    'columns': [{'name': c.name, 'type': c.type, 'position': c.position} for c in columns],
                    'values': {
                        c.name: [
                            {'text': v.text, 'frequency': v.frequency}
                            for v in catalog.common_values(table.schema, table.name, c.name, MAX_VALUES)
                        ]
                        for c in columns
                        if catalog.common_values(table.schema, table.name, c.name, MAX_VALUES)
                    },
                },
            )
    return {
        'dialect': dialect.name,
        'tables': tables,
        'functions': [
            {'schema': f.schema, 'name': f.name, 'args': f.args, 'result': f.result}
            for f in sorted(catalog.functions(), key=lambda f: f.name)[:400]
        ],
    }


def main() -> None:
    """Write demo/snapshot.json from whichever fixtures are reachable."""
    out: dict[str, Any] = {}
    for key, connect, dialect, schemas in (
        ('postgres', _postgres, POSTGRES, ['public', 'billing']),
        ('clickhouse', _clickhouse, CLICKHOUSE, ['analytics', 'staging']),
    ):
        connection = connect()
        catalog = DbapiCatalog(connection.cursor, dialect, paramstyle=_paramstyle(key))
        out[key] = _export(catalog, dialect, schemas)
        connection.close()
        counts = (len(out[key]['tables']), len(out[key]['functions']))
        print(f'{key}: {counts[0]} relations, {counts[1]} functions')  # noqa: T201

    OUT.write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + '\n')
    print(f'wrote {OUT} ({OUT.stat().st_size // 1024} kB)')  # noqa: T201


def _paramstyle(key: str) -> str:
    """The driver's paramstyle, read from the driver rather than assumed."""
    if key == 'postgres':
        import psycopg2

        return str(psycopg2.paramstyle)
    from clickhouse_driver import dbapi

    return str(dbapi.paramstyle)


if __name__ == '__main__':
    main()
