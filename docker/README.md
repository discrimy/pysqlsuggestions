# Development backends

Three real backends for integration tests and the web demo.

```bash
docker compose -f docker/docker-compose.yml up -d --wait
docker compose -f docker/docker-compose.yml down -v      # and to tear down
```

| Service | Port | Credentials | Contents |
| --- | --- | --- | --- |
| PostgreSQL 16 | `57432` | `report` / `report`, db `report_service` | report_service's own Django schema, plus a `billing` schema |
| ClickHouse 24.8 | `57123` (HTTP), `57900` (native) | `report` / `report` | `analytics` and `staging` databases |
| Trino 468 | `57080` | any user, no password | catalogs `postgresql` and `clickhouse`, federating the other two |

Ports are offset into the 57xxx range because this machine already runs several
other stacks; check `docker ps` before changing them.

## Why the schema looks like this

It mirrors **report_service**, the first consumer. Table and column names are the
real ones — default Django naming (`reports_report`, `reports_database`,
`auth_user`), `AutoDateMixin`'s `dt_created` / `dt_modified`, and the real
foreign keys report → database, report → group, report → author.

Trino is deliberately **not seeded**. Its two catalogs point at the other
containers, so the three-level namespace is exercised against real data rather
than a fixture:

```sql
SELECT p.name, count(c.report_id) AS runs
FROM postgresql.public.reports_report p
JOIN clickhouse.analytics.report_executions c ON c.report_id = p.id
GROUP BY p.name;
```

That query runs. It is also why `analytics.` means three different things across
the three dialects, which is the single clearest demonstration of `Namespace.levels`.

## What each fixture is for

| Fixture | Exercises |
| --- | --- |
| `billing` schema, `analytics` / `staging` databases | schema-, database- and catalog-qualified completion |
| `billing."MonthlyTotals"`, `"Period"`, `"Amount"` | quoted identifiers and case folding — Postgres folds, ClickHouse preserves |
| `reports_database.password` | a column readable as metadata but not as data |
| `mattermost_mattermostchannel` | `has_any_column_privilege` true, `has_table_privilege` false — queryable, but `SELECT *` errors |
| `report_executions` `ORDER BY (report_id, started_at)`, `PARTITION BY toYYYYMM` | physical layout ranking, read from `system.tables` |
| `status Enum8('ok', 'error', 'timeout', 'cancelled')` | value hints embedded in the ClickHouse type string |
| `user_login LowCardinality(String)` | the signal that `SELECT DISTINCT` is affordable |
| `reports_report.text` containing `%с_даты\|date\|2024-01-01%` | the report macro syntax, for the syntax-extension work |
| `reports_active` view | relation kinds beyond plain tables |

## The restricted role

`analyst` / `analyst` exists to make privilege detection testable:

```bash
psql "postgresql://analyst:analyst@localhost:57432/report_service" \
  -c "SELECT has_column_privilege('analyst','reports_database','password','SELECT')"   -- f
```

Note a Postgres subtlety the seed script had to work around: a column-level
`REVOKE` **cannot** subtract from a table-level `GRANT`, because table `SELECT`
implies every column. Withholding one column means dropping the table grant and
enumerating the rest.
