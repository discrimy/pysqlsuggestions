# pysqlsuggestions for VS Code

Schema-aware SQL completion in `.sql` files: joins written from the foreign keys
your database declares, values from the statistics it already keeps, columns
that know their own types.

```
SELECT * FROM reports_report r JOIN ⌶

  auth_user au ON r.author_id = au.id                fk: auth_user.id
  reports_database rd ON r.database_id = rd.id       fk: reports_database.id
  reports_runlog rr ON r.id = rr.report_id           fk: reports_runlog.report_id
```

Accepting one writes the whole clause — relation, alias and condition.

## What you need

**Nothing.** The extension carries its own Python — a stripped CPython 3.13
built by [python-build-standalone](https://github.com/astral-sh/python-build-standalone),
with the language server already installed into it. It is unpacked once, into
the extension's own storage, the first time you open a `.sql` file.

Your machine's Python is never looked at, let alone used. There is no virtual
environment, no `pip` at install time, and no network: a first run on a train
works.

That is why there is one download per platform rather than one for everyone.
The marketplace picks the right build for your OS and architecture; installing
from a `.vsix` file by hand means picking it yourself.

**PostgreSQL, ClickHouse and Trino** all read a catalog, so completion is
schema-aware against any of them. Postgres additionally answers foreign keys,
column search and most-common-values, which is where join proposals and value
hints come from; the other two declare no constraints, so they get neither.

`ansi` is selectable too and completes from the statement alone — keywords,
aliases, CTE columns and select-list names, with no connection at all.

## Connections

The **SQL Connections** view in the Explorer manages them.

| icon | meaning |
| --- | --- |
| `$(circle-outline)` | configured, not tested this session |
| `$(sync~spin)` | being tested |
| `$(pass-filled)` | last test succeeded |
| `$(warning)` | last test failed, or the database stopped answering while in use |

The icon is **health**; the `· in use` suffix is which connection the server is
actually using. They are separate because the one in use may be the broken one,
and that is the case most worth seeing.

Health is remembered for the session only. A tick from last week is a claim
nobody checked today.

**Test connection** tells you what went wrong in words, which matters because
every kind of failure looks the same from the editor — completion simply stops
being schema-aware:

| what happened | what it says |
| --- | --- |
| working | `12 relations visible` |
| no password stored | `the server asked for a password and none is stored` |
| wrong password | `password authentication failed for user "report"` |
| wrong database | `database "nosuchdb" does not exist` |
| nothing listening | `Can't create a connection to host localhost and port 59999` |

**Passwords are never stored in settings.** There is no field for one, by
design. They live in the editor's secret storage, are asked for when a
connection first needs one, and are removed when you remove the connection.

New connections are saved to your user settings, so they follow you between
projects and cannot be committed by accident. A connection defined in a
workspace's `.vscode/settings.json` is edited where it lives.

## Settings

| setting | what it does |
| --- | --- |
| `pysqlsuggestions.connections` | the connections themselves — name, dialect, host, port, database, user, secure, verify |
| `pysqlsuggestions.defaultConnection` | which one to use |

## When something is wrong

The status bar, bottom right, names the connection in use and warns when the
database stopped answering. **pysqlsuggestions: Show logs** has the detail.

Completion never fails outright: with no connection, an unreachable one, or a
dialect that reads no schema, you still get keywords, CTE names, aliases and the
columns the statement itself defines. That is a narrower list, not a broken one
— which is exactly why the status bar and the connection view exist to tell you
which you are looking at.
