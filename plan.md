# pysqlsuggestions

A context-aware, schema-aware SQL completion engine for Python. Embeddable as a
library, not bundled into a CLI or a language server binary.

Status: design document. Supersedes the ad-hoc Postgres helper.

---

## 1. What this is

Given a SQL string, a caret position, and a source of catalog metadata,
`pysqlsuggestions` returns a ranked list of completions: columns, tables,
schemas, functions, keywords, join conditions, and values.

It targets **read-only analytical work first** — exploring data with SELECT,
not scaffolding DDL. Three backends at launch: PostgreSQL, ClickHouse, Trino.

### Non-goals

- Query execution. The library never opens a connection of its own.
- Query formatting, linting, or optimisation.
- Being a language server. An LSP wrapper is an optional extra, not the core.
- Full SQL validation. Completion must work on syntactically invalid input by
  definition; a parser that rejects incomplete text is useless here.

---

## 2. Why it should exist

The existing landscape splits cleanly, and nothing occupies the middle.

| Project | Language | Dialects | Shape |
| --- | --- | --- | --- |
| `pgcli.packages.sqlcompletion` | Python | Postgres | Coupled to pgcli |
| `dbcli/sqlcomplete` | Python | — | Abandoned stub, empty usage section |
| `dt-sql-parser` | TypeScript | 7, incl. Trino | Library, ANTLR4 + antlr4-c3 |
| `sql-language-server` | TypeScript | MySQL, PG, SQLite | LSP binary |
| `sqls` | Go | MySQL, PG, SQLite | LSP binary |
| `postgres-language-server` | Rust | Postgres | LSP binary |

Every Python option is Postgres-only and welded to a host application. Every
multi-dialect option is in the JavaScript ecosystem, where the editor lives.
Nothing is importable into a FastAPI service, a notebook kernel, or an internal
reporting tool without dragging a process boundary along with it.

That gap is the product. **The differentiator is the library shape**, and the
README should lead with it.

Prior art worth mining rather than avoiding: pgcli's `test_sqlcompletion.py`
encodes a decade of edge cases and ports directly to our test corpus;
`postgres-language-server` validates the tolerant-parser approach; `dt-sql-parser`
demonstrates ANTLR + antlr4-c3 as the grammar-driven alternative to a hand-written
clause model.

---

## 3. Architecture

Five stages. The first three are pure functions over text. The fourth is the
only code in the library that performs I/O. The fifth is pure again.

```
  Lex        tolerant scan, caret located          pure
  Analyse    clause, qualifier, scope              pure
  Request    plain value: kinds, prefix, scope     pure   <-- the seam
  Resolve    Catalog port, lazy fetch              I/O
  Rank       scoring, casing, quoting              pure
```

### 3.1 `Request` — the central value

Everything upstream of resolution produces this, and it is the boundary that
makes the engine testable without a database.

```python
class Kind(Enum):
    COLUMN = auto(); TABLE = auto(); SCHEMA = auto()
    FUNCTION = auto(); ALIAS = auto(); KEYWORD = auto()

@dataclass(frozen=True, slots=True)
class Request:
    kinds: tuple[Kind, ...]
    prefix: str                       # what is already typed
    replace_span: tuple[int, int]     # what the editor should overwrite
    qualifier: tuple[str, ...] = ()   # segments left of the last dot
    scope: Scope | None = None
```

### 3.2 Scope

```python
@dataclass(frozen=True, slots=True)
class Relation:
    alias: str | None
    path: tuple[str, ...]
    source: Literal["table", "cte", "subquery"]
    projection: tuple[Column, ...] | None   # None -> ask the catalog

@dataclass(frozen=True, slots=True)
class Scope:
    relations: tuple[Relation, ...]
    ctes: Mapping[str, Relation]
    parent: Scope | None            # nested subqueries
```

`projection` is optional because CTEs, subqueries and `VALUES` lists produce
columns that exist in no system catalog. A resolver that only knows how to
query a database gets these wrong, and users notice immediately — CTEs are
where they spend their time.

### 3.3 Worked traces

**Columns from a FROM clause that has not been read yet.**

```
SELECT id, na⌶ FROM users u
```

Analyse scans back to the nearest clause keyword at depth 0 and lands on
`SELECT`. The preceding token is `,`, so the qualifier is empty. Scope is built
from the **whole statement**, not the text left of the caret — this is the
constraint that a left-to-right-only design cannot satisfy.

```python
Request(kinds=(COLUMN, FUNCTION, KEYWORD), prefix="na", replace_span=(11, 13),
        scope=Scope(relations=(Relation(alias="u", path=("users",),
                                        source="table", projection=None),), ...))
```

**The qualifier collapses the answer.**

```
SELECT * FROM orders o JOIN users u ON o.user_id = u.⌶
```

`qualifier=("u",)`, which resolves against scope aliases. `kinds` narrows to
`(COLUMN,)` — no keywords, no functions, no tables — and only `users` is
fetched. That narrowing is the main quality lever in a completion engine;
mediocre ones suggest everything all the time.

Resolution order is **alias first, then namespace.** Only if `u` matches no
alias is it read as a schema, database, or catalog name.

**No catalog call at all.**

```
WITH recent AS (SELECT id, total FROM orders) SELECT r.⌶ FROM recent r
```

Analyse recurses into the CTE body, runs the same scope-and-projection
analysis, and registers `Relation(alias="r", source="cte",
projection=(Column("id"), Column("total")))`. Resolve returns it directly.

**Where the dialect actually shows up.**

```
SELECT * FROM analytics.⌶
```

Identical tokens, identical qualifier, no alias match. Resolve hands the
qualifier to `dialect.namespace`:

| Dialect | `levels` | Segment 1 reads as | Suggests |
| --- | --- | --- | --- |
| Postgres | `("schema", "table")` | schema | tables in `analytics` |
| ClickHouse | `("database", "table")` | database | tables in `analytics` |
| Trino | `("catalog", "schema", "table")` | catalog | **schemas** in `analytics` |

One code path, three answers, driven by one tuple. Postgres adds a wrinkle:
`schema.table.column` is legal, so a two-segment qualifier is genuinely
ambiguous and the resolver emits the union of both readings rather than
guessing.

---

## 4. Dialects

A dialect is **data you compose**, not a class you subclass. Dialects do not
form a tree — ClickHouse and Trino each share different subsets with ANSI —
so flat records with overrides handle the shape that an MRO cannot.

```python
@dataclass(frozen=True, slots=True)
class Syntax:
    identifier_quotes: tuple[str, ...] = ('"',)
    line_comments: tuple[str, ...] = ("--",)
    unquoted_case: Literal["lower", "upper", "preserve"] = "lower"
    dollar_quoting: bool = False
    cast_operator: str | None = "::"

@dataclass(frozen=True, slots=True)
class Dialect:
    name: str
    syntax: Syntax
    namespace: Namespace
    clauses: ClauseModel
    keywords: frozenset[str]
    catalog_queries: CatalogQueries
```

```python
CLICKHOUSE = replace(
    ANSI,
    name="clickhouse",
    syntax=replace(ANSI.syntax, identifier_quotes=('"', "`"),
                   unquoted_case="preserve", line_comments=("--", "#")),
    namespace=Namespace(levels=("database", "table")),
    clauses=ANSI.clauses.extend(PREWHERE, FINAL, ARRAY_JOIN, SETTINGS),
)
```

### What actually varies

Less than it looks. Lexing (quotes, case folding, comment markers,
dollar-quoting, cast syntax), namespace depth, clause vocabulary
(`PREWHERE`, `FINAL`, `ARRAY JOIN`, `LIMIT n BY`, `SETTINGS`; `UNNEST`,
`MATCH_RECOGNIZE`; `DISTINCT ON`, `LATERAL`, `FILTER`), introspection SQL, and
keyword sets. Everything else — clause shape, alias scoping, CTE visibility,
subquery resolution — is shared, and it is the bulk of the engine.

### The clause model

Each clause is a record, so adding ClickHouse's `PREWHERE` costs one line
rather than a parser change:

```python
PREWHERE = Clause(name="PREWHERE", follows={"FROM", "SAMPLE"},
                  suggests=(Kind.COLUMN, Kind.FUNCTION))
```

### Hard rule

**No `dialect.name` comparisons outside the dialect package.** If the engine
needs to know something, it becomes a field. This is the rule that keeps
dialect count from turning into branch count, and it belongs in the review
checklist.

### Introspect, do not hardcode

ClickHouse exposes thousands of functions in `system.functions`; Trino has
`SHOW FUNCTIONS`; Postgres has `pg_proc`. Treat functions, types and table
engines as catalog data with a static offline fallback. Reserved words are the
exception — those ship, because quoting decisions must be made before a
connection exists.

### Fallback

Ship an `ansi` dialect so an unknown backend degrades to keyword-and-catalog
completion instead of failing.

---

## 5. Ports

The core defines protocols; callers bring connections. This is what keeps the
package driver-agnostic.

```python
class Catalog(Protocol):
    def schemas(self) -> Sequence[str]: ...
    def tables(self, schema: str) -> Sequence[Table]: ...
    def columns(self, schema: str, table: str) -> Sequence[Column]: ...
    def functions(self, schema: str | None = None) -> Sequence[Function]: ...

class Cache(Protocol):          # a plain dict satisfies this
    def get(self, key: str, default=None): ...
    def __setitem__(self, key: str, value) -> None: ...
```

Methods are lazy (per-schema, per-table). Trino across catalogs and
ClickHouse's `system.columns` are too expensive to preload.

### Capability protocols

Richer features need more than schemas/tables/columns, but a fifteen-method
`Catalog` would force every adapter to stub out what its backend lacks. Split
by capability and detect at runtime:

```python
class SupportsForeignKeys(Protocol):
    def foreign_keys(self, schema: str, table: str) -> Sequence[ForeignKey]: ...

class SupportsPhysicalLayout(Protocol):
    def layout(self, schema: str, table: str) -> TableLayout: ...

class SupportsValueHints(Protocol):
    def common_values(self, schema: str, table: str, column: str) -> Sequence[str]: ...

class SupportsPrivileges(Protocol):
    def availability(self, schema: str, table: str) -> Mapping[str, Availability]: ...
```

The resolver requests a capability, receives `None` when unsupported, and
degrades: FK-derived joins become heuristic ones, layout ranking falls back to
declaration order. **Every feature must define its behaviour when its source is
absent**, because at least one of the three backends will be missing something.

### Introspection SQL as data

This is what actually breaks the driver tie. Each dialect module exports query
text plus row-to-dataclass mappers and imports no driver:

```python
COLUMNS_SQL = "SELECT table_schema, table_name, column_name, data_type ..."

def parse_column_row(row: tuple) -> Column: ...
```

Anyone can run that string through psycopg, asyncpg, SQLAlchemy, a DB-API
cursor, or an HTTP proxy. A generic PEP 249 adapter needing **zero**
third-party imports covers psycopg2, `trino`, and `clickhouse-driver`; only
`clickhouse-connect` needs a bespoke adapter.

### Parser port

The default is a stdlib-only tolerant scanner. A `Parser` protocol lets
sqlglot or an ANTLR4 + antlr4-c3 grammar be dropped in as an optional extra for
deeper expression analysis — grammar-derived candidate tokens instead of a
hand-maintained clause model. Available to users without becoming a dependency,
and without our dialect list being capped by theirs.

### Async

The core is sync. Callers needing async either pre-fetch into a snapshot
`Catalog`, or we adopt an effect-style engine that yields metadata requests and
receives rows — one implementation serving both. Decide before the web front
end ships; retrofitting is expensive.

---

## 6. Suggestion features

Ordered by value per unit of work.

### 6.1 Free — no catalog access

Falls out of scope analysis alone, and users notice these first.

- `GROUP BY ⌶` — exactly the non-aggregated SELECT-list expressions.
- `ORDER BY ⌶` — SELECT-list aliases and ordinals, not raw columns.
- `HAVING ⌶` — aggregates already in the SELECT list.
- Alias generation: `FROM order_items ⌶` → `oi`, convention learned from history.
- Star expansion: `SELECT o.*⌶` → the explicit column list.

### 6.2 Physical layout awareness

The highest-value feature for analytical backends, and mostly free metadata.
ClickHouse `system.tables` exposes `partition_key`, `sorting_key`,
`primary_key`; Trino has `$partitions` on Hive and Iceberg; Postgres has
`pg_index`. Rank those columns to the top in WHERE clauses **and annotate
them** — filtering on the sort key versus not is 50ms versus 50 seconds, and
the annotation teaches table shape while the user types. A Postgres-only tool
cannot follow us here.

### 6.3 Join completion

Condition level: `JOIN users u ON ⌶` → `o.user_id = u.id`, from Postgres FK
constraints. Whole-clause level: after `FROM orders o ⌶` → the full
`JOIN ... ON ...`, with tables ranked by FK reachability.

ClickHouse and Trino have no foreign keys. Fallback: `<singular>_id` ↔
`<table>.id` naming plus type compatibility, and — much stronger — join pairs
mined from query history. Observed joins outrank inferred ones.

### 6.4 Value completion

`WHERE status = '⌶` → `active`, `pending`, `cancelled`. Sources that do not
require touching user tables:

- Postgres: `pg_stats.most_common_vals`, already computed by ANALYZE, with
  `most_common_freqs` giving frequency ordering for free. Check constraints too.
- ClickHouse: `Enum8('active'=1, ...)` values are embedded in the type string in
  `system.columns`. `LowCardinality(String)` signals that an opt-in
  `SELECT DISTINCT ... LIMIT 50` is affordable.
- Trino: nothing generic. `SHOW STATS FOR` gives distinct counts.

Never run a value query synchronously on a keystroke. Explicit trigger or
background fetch, cached hard. Always label the source in the UI — these are
inferred, not authoritative, and the column may have drifted since ANALYZE.

### 6.5 Ranking

Alphabetical ordering is what makes an engine feel dumb. In order of strength:
local query history with frecency scoring (strongest signal, needs no server
support, belongs behind its own port for lifetime and privacy reasons);
declaration order via `attnum` beats alphabetical because authors put important
columns first; `pg_stat_user_tables` reveals which of 400 tables anyone
actually queries.

### 6.6 Documentation at the caret

Column and table comments in the detail pane — `col_description()`,
`system.columns.comment`, connector comments. Trivial to plumb, and it makes
completion the fastest documentation lookup in the tool.

### 6.7 Type-aware narrowing

`SUM(⌶` → numeric columns. `WHERE created_at > ⌶` → date functions ahead of
string columns. Needs signatures from `pg_proc` / `SHOW FUNCTIONS` /
`system.functions`, the last of which is thin. Worst effort-to-payoff ratio on
this list; schedule it last.

### Explicitly rejected

**LLM calls per keystroke.** The latency budget is roughly 50ms and users
abandon tools that miss it. "Explain this query" is a different feature with a
different interaction model.

**Loose fuzzy matching.** Demos well, degrades badly on a 400-table schema — a
three-character prefix matching sixty things is worse than matching nothing.
Prefix plus subsequence-on-word-boundaries (`oi` → `order_items`) is the right
aggressiveness.

---

## 7. Restricted objects

Some columns are readable as metadata but not as data. Postgres separates these
concerns: `pg_attribute` rows are visible to everyone regardless of column
grants. We show restricted items greyed and non-insertable rather than hiding
them, because seeing *why* something is unavailable beats it silently not
existing.

```python
class Availability(Enum):
    AVAILABLE = auto()
    RESTRICTED = auto()   # exists, no read privilege
    UNKNOWN = auto()      # backend cannot tell us

@dataclass(frozen=True, slots=True)
class Suggestion:
    text: str
    kind: Kind
    replace_span: tuple[int, int]
    score: float
    availability: Availability = Availability.AVAILABLE
    reason: str | None = None       # "no SELECT privilege"
    note: str | None = None         # "sort key", "fk: users.id"
    detail: str | None = None       # column comment
```

### Detection

Fold the check into introspection rather than querying per suggestion:

```sql
SELECT c.relname,
       a.attname,
       format_type(a.atttypid, a.atttypmod) AS type,
       has_column_privilege(c.oid, a.attnum, 'SELECT') AS readable
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND a.attnum > 0 AND NOT a.attisdropped
```

Two distinct table-level questions, both worth capturing:
`has_any_column_privilege` false means grey out the whole table;
`has_table_privilege` false while the former is true means the table is
queryable but `SELECT *` will error. Row-level security is unrelated — it
restricts rows, never columns.

ClickHouse supports column grants readable from `system.grants`, but effective
privileges through role inheritance must be reconstructed by hand with no
`has_column_privilege` equivalent. Trino exposes nothing through SQL; access
control lives in the connector, a file rule set, Ranger, or OPA — so the
realistic answer is a pluggable policy source the embedder supplies. This is
why `UNKNOWN` exists as a third state: without it, Trino columns get either
wrongly greyed or wrongly promised.

### Ranking rules

Restricted items sink to the bottom of their kind group, are never
preselected, and are excluded from single-exact-match auto-insert. They still
match prefixes and still appear under qualifiers.

### Rendering — the honest constraint

Our own front ends can fully block insertion: prompt_toolkit's `Completion`
takes a style, and Monaco and Ace control both rendering and the accept handler.

**LSP has no disabled state.** The closest is
`tags: [CompletionItemTag.Deprecated]` (strikethrough), plus `detail` carrying
the reason and `sortText` sinking the item. A client will insert whatever we
return. Do not fake it with an empty `textEdit` plus a command — that produces
an item that silently does nothing, which reads as a bug. The core emits
`availability`; each front end renders it as faithfully as its host allows, and
we document the difference rather than papering over it.

### Connection identity — design for this now

`has_column_privilege()` evaluates against the current connection's role. A
shared pool means every user gets the service account's privileges: usually
nothing looks restricted, and at worst we display column names to someone whose
own role could not see them.

Two consequences. Connections must carry end-user identity (`SET ROLE` per
request, or a connection per user). And **the cache key must include the
role** — otherwise user A's readable set leaks into user B's session. Since
users supply their own cache, the documented key shape is a contract:

```
cache_key = (role, dialect, schema, table)
```

The failure is silent and looks like a database privilege bug rather than a
caching bug. Say so in the docs.

### Knock-on effects

Star expansion emits readable columns only, and annotates the plain `*`
suggestion when `has_table_privilege` is false. Join suggestions produce a
restricted join condition rather than silently omitting it.

**Value hints must respect availability.** `most_common_vals` leaks actual
data, so `SupportsValueHints` returns nothing for any column whose availability
is not `AVAILABLE`. That check belongs in the resolver, not in each adapter.
This needs a test — it is the interaction most likely to be missed.

---

## 8. Syntax extensions

Templated SQL is the normal case in analytics tooling and no existing engine
handles it well. One hook covers our internal report macros
(`%userfield|type|default%`), dbt and Superset Jinja (`{{ ref('x') }}`),
Metabase `{{var}}`, psycopg `%(name)s`, and `:named` params.

A macro is a **token-level** concern, not a grammar extension. It occupies a
value position, so clause detection and scope analysis are untouched.

### Four hooks

1. **Claim the span during lexing.** Without this,
   `%userfield|type|default%` lexes as `%`, ident, `|`, ident, `|`, ident, `%`
   — the `|` confuses operator handling and the identifiers pollute alias
   resolution.
2. **Declare what the token stands for** — a typed literal, which buys
   type-aware narrowing for free.
3. **Own completion when the caret is inside it.**
4. **Register trigger characters**, or editors will not invoke completion there.

```python
@dataclass(frozen=True, slots=True)
class MacroContext:
    text: str
    span: tuple[int, int]
    caret: int
    segment: Literal["name", "type", "default"]
    scope: Scope | None
    enclosing: Request | None        # what the SQL engine would have asked here
    catalog: Catalog

class SyntaxExtension(Protocol):
    trigger_chars: frozenset[str]
    def scan(self, src: str, pos: int) -> Token | None: ...
    def value_type(self, tok: Token) -> str | None: ...
    def complete(self, ctx: MacroContext) -> Sequence[Suggestion]: ...
```

`enclosing` is what makes this more than autocomplete-on-a-string. With the
caret in the default segment of `WHERE status = %st|string|⌶%`, the extension
reads what the SQL engine would have suggested and pulls candidate values
through `SupportsValueHints` — no report-specific value logic needed.

`replace_span` on returned suggestions must cover the **segment**, not the
whole macro. Easy to get wrong, very visible when it is.

### Lexing hazards

`%` is genuinely ambiguous in SQL:

- `LIKE '%smith%'` — inside a string. Run extensions only at token boundaries
  outside strings; the scanner already tracks string state.
- `SELECT id % 10` — modulo. Requiring no internal whitespace and an identifier
  start handles the common case.
- `SELECT a%b%c` — legal chained modulo that matches the pattern. Undecidable
  from syntax alone.

Resolution rule: **prefer the macro reading only when the name matches a
declared variable, or the match contains at least one `|`.** A bare `%foo%`
with unknown `foo` falls through to modulo.

### Open spec questions for the report macro

- What is the escaping rule when a default contains `%`, `|`, or a quote? If
  the answer is "it cannot", a regex suffices; otherwise a small hand-written
  scanner is needed.
- Does the existing runner's pattern match this specification? If it is more
  permissive, that is a latent bug there rather than a difference of opinion.

### Shared scanner

The report execution layer already parses these to substitute `?` placeholders.
**Extract that scanner into one module both projects import.** Two independent
regexes will drift, and completion will start offering macros the runner
rejects — an annoying class of bug to trace.

### Document scope

Reports declare a variable once and reference it repeatedly, sometimes across
statements. Extensions need the whole document, not the current statement —
worth confirming the analysis entry point passes both, since everything else is
deliberately statement-scoped. This is also where useful diagnostics live: a
reference whose type contradicts its declaration, or a variable declared and
never used.

---

## 9. Packaging

### Layout

```
src/pysqlsuggestions/
  types.py        # Suggestion, Table, Column, Availability
  ports.py        # Catalog, Cache, Parser, capability protocols
  engine/         # lex, analyse, request, rank — pure
  dialects/       # keywords, functions, introspection SQL
  catalogs/
    memory.py     # snapshot impl, stdlib only
    dbapi.py      # any PEP 249 cursor, no driver import
  adapters/       # the ONLY place a driver may be imported, lazily
  py.typed
```

`__init__.py` re-exports the pure API only. Nothing under `adapters/` is
imported at package import time.

### pyproject

```toml
[project]
name = "pysqlsuggestions"
requires-python = ">=3.10"
dependencies = []                      # the whole point

[project.optional-dependencies]
psycopg2           = ["psycopg2-binary>=2.9"]
clickhouse-connect = ["clickhouse-connect>=0.7"]
trino              = ["trino>=0.328"]
demo               = ["prompt_toolkit>=3.0", "fastapi", "uvicorn"]

[project.entry-points."pysqlsuggestions.dialects"]
postgres   = "pysqlsuggestions.dialects.postgres:dialect"
clickhouse = "pysqlsuggestions.dialects.clickhouse:dialect"
trino      = "pysqlsuggestions.dialects.trino:dialect"
```

Extras are named after the driver they install rather than the backend, so
`pip install pysqlsuggestions[psycopg2]` says exactly which package lands in
the environment. That matters here because more than one driver can serve the
same backend — a future psycopg 3 extra sits alongside `psycopg2` rather than
replacing a `postgres` extra whose meaning would silently change.

Per PEP 685, `clickhouse-connect` normalises to the same extra name it is
written as; a user typing `clickhouse_connect` resolves to it too.

Two of the three drivers need no adapter code at all. psycopg2 and `trino` are
both PEP 249 compliant, so they go through `catalogs/dbapi.py` and the extra
exists purely to install the dependency. clickhouse-connect is the exception:
its primary client is not DB-API, so it gets a bespoke adapter under
`adapters/`. (It does ship `clickhouse_connect.dbapi`, but the native client
gives better type fidelity on the `system.*` tables we read.)

Deferred to v0.4 and deliberately absent above: `sqlglot` and `lsp`. Adding an
extra is cheap; removing one is a breaking change, so they land when the code
behind them does.

Two entry point groups: `pysqlsuggestions.dialects` and
`pysqlsuggestions.extensions`. Third parties add DuckDB, Snowflake, or a
proprietary macro syntax without forking. Discovery via
`importlib.metadata.entry_points` — still stdlib.

Ship the LSP wrapper as an extra when it exists. Most people will want to
consume it that way, but it must not be the only door in.

---

## 10. Testing

**Golden requests.** Stages 1–3 test with no database and no mocks:

```python
def test_alias_qualifier_narrows_to_columns():
    req = derive_request("SELECT * FROM users u WHERE u.", 29, POSTGRES)
    assert req.kinds == (Kind.COLUMN,)
    assert req.qualifier == ("u",)
```

Port pgcli's `test_sqlcompletion.py` corpus as `(sql, caret, expected_request)`
tuples. Resolution tests run against `catalogs.memory` built from a dict; only
adapters need a live server.

**Purity guard.** This rots the first time someone adds a convenience import:

```python
def test_core_imports_no_drivers():
    import sys, pysqlsuggestions
    assert not {"psycopg2", "trino", "clickhouse_connect"} & sys.modules.keys()
```

Plus a CI job running `pip install .` with no extras.

**Conformance suite.** Third-party dialects need a way to know they got it
right, and it is our regression net when the resolver is refactored:

```python
from pysqlsuggestions.testing import DialectConformance

class TestDuckDB(DialectConformance):
    dialect = DUCKDB
```

The base class runs a shared corpus every SQL dialect must pass — alias
resolution, CTE visibility, quoted identifiers, dotted paths at each namespace
level. Dialect-specific cases go on top.

---

## 11. Decisions still open

1. **Sync-only core, or effect-style engine?** Blocks the web front end.
   Decide before it ships.
2. **antlr4-c3 as the default parser, or the hand-written clause model?**
   Grammar-derived candidates cut per-dialect handwritten data substantially but
   add a runtime dependency and a grammar to maintain per dialect. Current
   position: hand-written default, ANTLR behind the `Parser` port.
3. **Macro escaping rules** (§8) — needs a decision from the reports side.
4. **Query history port** — shape, storage, and how users opt out.
5. **ClickHouse effective privileges** — worth building, or leave as `UNKNOWN`
   until someone asks?

---

## 12. Sequencing

**v0.1** — core pipeline, three dialects, `Catalog` and `Cache` ports, DB-API
adapter, memory catalog, §6.1 free features, conformance suite.

**v0.2** — capability protocols, physical layout awareness, FK joins,
documentation at the caret, `Availability`.

**v0.3** — syntax extensions, report macro plugin, value hints, history-based
ranking.

**v0.4** — LSP wrapper, sqlglot/ANTLR parser adapters, async story.
