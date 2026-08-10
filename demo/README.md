# Demo

SQL completion against real PostgreSQL, ClickHouse and Trino.

```bash
docker compose -f docker/docker-compose.yml up -d --wait
uv run uvicorn demo.app:app --port 8000
# http://localhost:8000
```

Nothing here belongs in the library — it imports drivers, which the core never
does. It exists to show the pipeline against real servers and to make the
normally invisible middle of a completion engine visible while you type.

## What to try

| Type this | And notice |
| --- | --- |
| `SELECT * FROM reports_report r WHERE r.` | Columns in `attnum` order, not alphabetical — authors put important columns first |
| `SELECT * FROM billing.` on Postgres, then Trino | Same text, different level: Postgres reads a schema and offers tables, Trino reads a catalog and offers schemas |
| `SELECT * FROM ` on Trino | Catalogs, because with three levels that is what you write first |
| `SELECT * FROM billing."MonthlyTotals" m WHERE m.` | `"Period"` comes back quoted for Postgres and bare for ClickHouse |
| `WITH a AS (SELECT id, name FROM reports_report) SELECT a.` | Answered with no catalog call at all — watch the timing |
| `WITH a AS (SELECT * FROM reports_database) SELECT a.` | The star is expanded through the relation it came from |
| `SELECT name, count(*) AS runs FROM reports_report GROUP BY name ORDER BY ` | `runs` and the ordinals `1`, `2` — no catalog can supply either |
| `SELECT * FROM reports_database ` | `rd`, `r`, `rep` — generated aliases |
| `SELECT * FROM t WHERE name = 'ab` | Nothing, because the caret is inside a string |
| `SELECT * FROM postgresql.public.reports_report p JOIN clickhouse.analytics.report_executions c ON c.` | A join whose two relations live in different databases |

The right-hand panel is the `Request` — clause, prefix, qualifier, kinds, and the
relations in scope with how much of each the statement described itself. That is
the output of the pure stages, before anything is fetched.

## On the timings

Warm reads are well under a millisecond; the page reports each one.

Trino's ClickHouse connector takes about ten seconds to answer its *first*
metadata query — `SHOW SCHEMAS FROM clickhouse` costs the same, so it is
connector startup rather than anything the query text can fix. The demo warms
each backend in a background thread at boot rather than paying that on a
keystroke, which is the same "background fetch, cached hard" rule the design
applies to value hints.

The cache is keyed `(role, dialect, schema, table)` here as everywhere, with
`identity='demo'`.
