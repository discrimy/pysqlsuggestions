# pysqlsuggestions

Context-aware, schema-aware SQL completion for Python. A library, not a CLI and
not a language server — importable into a FastAPI service, a notebook kernel or
an internal reporting tool without dragging a process boundary along.

Zero runtime dependencies. PostgreSQL, ClickHouse and Trino, plus an `ansi`
fallback so an unknown backend degrades instead of failing.

## Status

The whole pipeline works end to end against real servers: lex, analyse, request,
resolve, rank. Value hints, FK-derived joins, star expansion and bound
parameters landed since; still to come are physical layout ranking and history
ranking, plus per-role availability and the syntax extensions.

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

## Columns before a FROM

`SELECT ema⌶` with nothing in the FROM offers the column *and* the relation it
belongs to, because choosing one is choosing the other:

```
SELECT ema⌶   ->   SELECT auth_user.email FROM auth_user
```

The suggestion carries two edits, and `plan_insertion` returns both. The FROM
goes where a FROM goes — after the select list, before whatever follows it.

It needs `SupportsColumnValues`'s sibling, `SupportsColumnSearch`. Postgres and
ClickHouse ship the query; Trino does not, since answering would mean asking
every catalog's connector in turn — the same reason its unqualified `tables` is
empty. A prefix is required: every column in the database is not an answer.

## Qualified columns

A column is offered as `<alias>.<column>`, or `<relation>.<column>` when there
is no alias — always, not only when two relations are in view:

```
SELECT * FROM auth_user u WHERE ⌶       u.id  u.username  u.email
SELECT * FROM auth_user WHERE ⌶         auth_user.id  auth_user.username
SELECT * FROM auth_user u WHERE u.⌶     id  username  email
```

A bare name is unambiguous only until a second relation joins, and the caret is
usually in a query still being written. Where the qualifier is already typed the
column comes back bare — it is in the text already — and a relation with no name
to qualify with, an unaliased derived table, stays bare too.

Matching is unaffected: it runs against the column name, so `usern` still finds
`u.username`.

## Joins

Type `JOIN` and the whole clause comes back — relation, alias and condition in
one accept — from the foreign keys the database already declares:

```
SELECT * FROM booking b JOIN ⌶

  flight f ON b.flight_id = f.id              fk: flight.id
  passenger p ON b.passenger_id = p.id        fk: passenger.id
  baggage bag ON b.id = bag.booking_id        fk: baggage.booking_id
  revenue.refund r ON b.id = r.booking_id     fk: refund.booking_id
```

At `ON ⌶` the whole condition arrives the same way, and once a qualifier has
committed the left side — `ON b.⌶` — it degrades to ranking that relation's
foreign key columns up, since a condition is no longer expressible there.

A constraint is directed and a join is not, so proposals fire from both ends: a
query starting at `airline` is offered the tables that reference *it*.
Many-to-one ranks above one-to-many, being both more often wanted and unable to
multiply the result set. Two constraints to the same target stay two proposals
with different aliases, because choosing between them is the user's to make.

**Postgres only, and deliberately.** ClickHouse and Trino declare no
constraints, so both positions there behave exactly as they always have. The
tempting fallback — matching `<singular>_id` against `<table>.id` — is rejected
rather than unbuilt: it is right often enough to be inviting and wrong often
enough to matter, and a wrong join condition is valid SQL that silently returns
the wrong rows. No parser catches that, and neither does the person reading the
result. Observed joins mined from query history would be a real answer here; an
inferred one is not.

## Browser demo

The same page, with no server and no database — the library has no runtime
dependencies and its core is pure, so the whole pipeline loads into the page
under Pyodide and completes against a schema carried as data:

```bash
uv build --wheel
uv run python -m scripts.build_pages
python3 -m http.server -d site 8001
```

The page reaches nothing. Pyodide is carried in `site/` rather than fetched from
a CDN, pinned by digest in `scripts/pyodide.lock`, and the build refuses to
assemble a site whose files name any absolute URL. That costs 11.7 MiB against a
demo payload of 135 kB, and buys a page that works on an air-gapped laptop and
cannot be broken by somebody else's outage — which is the claim the demo exists
to make.

`.github/workflows/pages.yml` publishes `site/` to GitHub Pages when a `v*` tag
is pushed, and refuses to if the tag and `pyproject.toml` disagree about the
version. The page installs a wheel, so the published demo is a released
version's behaviour rather than whatever `main` reached this morning:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

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

## In an editor

The engine speaks LSP, so any client can drive it:

```bash
uv run python -m pysqlsuggestions_lsp
```

The connection profile arrives in `initializationOptions`. The database is not
contacted until the first completion request — opening a document opens no
socket — and an unreachable one degrades to completing from the statement alone
rather than failing the request.

It is a separate distribution in `lsp/`, not part of the library: a server needs
pygls and a driver, and the library's promise is that importing it pulls in
neither. See `lsp/README.md`.

`editors/vscode/` is a VS Code extension over that server. It builds its own
Python environment from wheels shipped inside it — nothing is downloaded, and
the project's own environment is untouched — and manages connections from a view
in the Explorer, passwords in secret storage rather than settings. It needs
Python 3.10+ on PATH, and reads a schema from PostgreSQL only: the other
backends' drivers are not pure Python, so bundling them would mean a separate
download per operating system. See `editors/vscode/README.md`.

## Design

See `docs/request-pipeline.md` for how the stages fit together,
`docker/README.md` for what each fixture exercises, and
`docs/superpowers/specs/` for the full design. `docs/gaps.md` records what is
missing and why, measured against DBeaver.

## Development

```bash
uv sync
./scripts/check.sh                      # ruff format, ruff check, mypy strict, pytest
uv run pytest -m 'not integration'      # without the docker backends
```
