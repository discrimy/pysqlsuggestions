# pysqlsuggestions

Context-aware, schema-aware SQL completion for Python. A library, not a CLI and
not a language server — importable into a FastAPI service, a notebook kernel or
an internal reporting tool without dragging a process boundary along.

Zero runtime dependencies. PostgreSQL, ClickHouse and Trino, plus an `ansi`
fallback so an unknown backend degrades instead of failing.

## Status

The whole pipeline works end to end against real servers: lex, analyse, request,
resolve, rank. Value hints landed since; still to come are physical layout
ranking, FK-derived joins and history ranking, plus per-role availability and
the syntax extensions.

## Usage

```python
from pysqlsuggestions import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint'), ('name', 'text')]})

sql = 'SELECT * FROM users u WHERE u.'
[s.text for s in complete(sql, len(sql), POSTGRES, catalog)]
# ['id', 'name']
```

Any PEP 249 cursor works as a catalog, with no driver imported by the library:

```python
import psycopg2
from pysqlsuggestions.catalogs.dbapi import DbapiCatalog

connection = psycopg2.connect(...)
catalog = DbapiCatalog(connection.cursor, POSTGRES, paramstyle=psycopg2.paramstyle)
```

The pure half is usable on its own, which is what a caller with no reachable
catalog wants:

```python
from pysqlsuggestions import derive_request

request = derive_request('SELECT id, na FROM users u', 13, POSTGRES)

request.prefix        # 'na'
request.clause        # 'SELECT'
request.replace_span  # (11, 13) — what the editor overwrites
request.kinds         # (Kind.COLUMN, Kind.FUNCTION)
request.scope         # relations in view, built from the whole statement
```

The scope comes from the entire statement, not the text left of the caret — the
`FROM` clause that answers the question above sits to the right of it.

A qualifier collapses the answer:

```python
derive_request('SELECT * FROM users u WHERE u.', 30, POSTGRES).kinds
# (Kind.COLUMN,)  — no keywords, no functions, no tables
```

And one tuple per dialect gives three different answers to the same text:

```python
from pysqlsuggestions.dialects.trino import TRINO

sql = 'SELECT * FROM analytics.'
derive_request(sql, len(sql), POSTGRES).kinds  # (Kind.COLUMN, Kind.TABLE)  a schema, or a relation
derive_request(sql, len(sql), TRINO).kinds     # (Kind.SCHEMA,)  analytics is a catalog
```

## Demo

```bash
docker compose -f docker/docker-compose.yml up -d --wait
uv run uvicorn demo.app:app --port 8000
```

Completion against real PostgreSQL, ClickHouse and Trino, with a panel showing
the derived `Request` as you type. See `demo/README.md` for what to try.

## Value suggestions

Right of a comparison, a literal is usually what is wanted, so `WHERE type = `
offers the values that column actually holds:

```python
complete("SELECT * FROM reports_database d WHERE d.type = ", 48, POSTGRES, catalog)
# [Suggestion(text="'postgres'", kind=Kind.VALUE, ...), ...]
```

Nothing reads the table — a completion engine may not start a scan. There are
two sources, and the exhaustive one wins:

| source | where it comes from | cost |
| --- | --- | --- |
| boolean | the type: `true` / `false` | free, every dialect |
| enum | ClickHouse writes its labels into the type text; Postgres keeps them in `pg_enum` | free / one read |
| frequent values | Postgres `pg_stats.most_common_vals` | one read |

A type that enumerates itself is complete, so statistics could only narrow it.
Everything else falls back to whatever the planner already recorded, which for
Postgres is also filtered to what the connected role may read.

Statistics appear once `ANALYZE` has run and only for columns whose values
repeat, so a column of distinct values has none — that is the feature working,
not failing. Fetching them is a capability (`SupportsColumnValues`): a catalog
that cannot answer offers columns and functions there instead. ClickHouse and
Trino keep no most-common-values, so ClickHouse answers from its enums and
Trino from booleans alone.

## Browser demo

The same page, with no server and no database — the library has no runtime
dependencies and its core is pure, so the whole pipeline loads into the page
under Pyodide and completes against a snapshot of the docker fixtures:

```bash
uv build --wheel
uv run python scripts/build_pages.py
python -m http.server -d site 8001
```

`.github/workflows/pages.yml` publishes `site/` to GitHub Pages on every push
to `main`.

The schema is `demo/schema.py` — a small flight-booking database invented for
the demo, written as data rather than exported from anywhere. That matters:
value suggestions come from statistics, statistics are literal values out of
the rows, and this page is published. There is deliberately no step that could
be pointed at a real database.

It is shaped to exercise the engine rather than to be realistic — enums and
booleans so values come from the type, skewed columns so they come from
statistics, relations three orders of magnitude apart in size, two schemas, a
materialized view and a mixed-case name that only Postgres has to quote.

All three backends are there, Trino included. The `Catalog` port passes one
name at each level — a catalog names the schemas below it, a schema names its
relations — so a snapshot with a catalog mapping serves three levels as readily
as two:

```python
MemoryCatalog(tables, catalogs={'warehouse': ['public', 'revenue']})
```

That is a `MemoryCatalog` feature rather than a demo one: anyone pre-fetching a
Trino schema into a snapshot needed it.

## Design

See `docs/request-pipeline.md` for how the stages fit together,
`docker/README.md` for what each fixture exercises, and
`docs/superpowers/specs/` for the full design.

## Development

```bash
uv sync
./scripts/check.sh                      # ruff format, ruff check, mypy strict, pytest
uv run pytest -m 'not integration'      # without the docker backends
```
