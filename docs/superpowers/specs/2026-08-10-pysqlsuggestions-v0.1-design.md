# pysqlsuggestions v0.1 — design

Date: 2026-08-10
Status: **historical**. Built, shipped as 0.1.0 and 0.1.1, and overtaken in the
places listed below. Read it for why the shape is what it is, not for what the
code does now — where the two disagree, the code is right and this is old.

Supersedes nothing. Refines `plan.md` into a buildable v0.1.

`plan.md` was the product vision through v0.4. It has since been removed from
the working tree and is in git history at `f4cb9cd`. This document covers only
what v0.1 ships, and records where v0.1 deviates from the plan and why.

---

## 0. Where the implementation went elsewhere

Kept here rather than folded into the text above, so the document stays a record
of what was decided in August 2026 and this stays a record of what happened to
it. Rewriting the body would lose the first without improving the second.

### Specified and not built

- **`adapters/` with a clickhouse-connect adapter** (§3, M8). Superseded rather
  than skipped: `catalogs/dbapi.py` reaches ClickHouse through
  `clickhouse-driver`, and §7.4's integration suite proves the introspection SQL
  and the paramstyle rewriting against a real container. A second adapter would
  have to earn its place against that.
- **`dialects/introspection/`** as a package (§3). The query text and row
  mappers live inside each dialect module as its `CatalogQueries`. Same data,
  same hard rule about `relkind` letters never leaking; one file fewer.
- **`testing/` as a corpus loader** (§3, §7.3). `pysqlsuggestions.testing` is a
  single module exposing `DialectConformance` and `Case`. It builds its own
  fixture from the dialect under test rather than loading a corpus file,
  because the corpus has to be spelled differently for each namespace depth and
  a data file cannot do that.

### Specified one way and built the other, deliberately

- **§5.5 forbids matching looser than word-boundary subsequence** — *"Nothing
  looser"*. Rank ships five tiers and the last is substring, scored down by how
  late the match starts. The helper this library supersedes did substring for
  every identifier, so `mail` finding `email` is behaviour its users already
  depend on; four stronger tiers above it keep the failure mode the spec was
  guarding against out of reach. `engine/rank.py` carries the argument.
- **§7.5's differential test against the vendored old module** was to be deleted
  *when the report_service migration lands*. It was deleted before, once its
  cases had been recovered as ordinary tests in `tests/queries/`. The migration
  has not landed.
- **`Catalog.schemas()`** takes a `catalog` argument the spec's signature does
  not have. Trino's three levels need it; two-level dialects ignore it.

### Built and never specified

The spec predates all of these, so its absence of them says nothing:

- **Insertion planning.** `plan_insertion`, `Insertion`, `Edit`, and
  `Insertion.expects_more`. §4 has `Suggestion.replace_span` and stops there,
  which leaves separators, closing parens, namespace dots and template blanks to
  every front end separately. The demo drifted three times before this existed.
- **Value suggestions.** `Kind.VALUE`, `ColumnValue`, `SupportsColumnValues`,
  and the planner-statistics queries behind them — §2 puts value hints out of
  v0.1 explicitly.
- **Statement templates.** `Kind.SNIPPET`, `Template`, `Suggestion.stops`, and
  the LSP-subset `$1`/`$0` expansion.
- **Five more kinds.** §4 names six; there are eleven. `CTE`, `OPERATOR`,
  `TYPE`, `SNIPPET` and `VALUE` are each a position the spec's six could not
  describe — an operator is never cased or quoted, a type belongs only past a
  cast, and a front end that colours by kind needs to tell them apart.
- **Expression position.** `Request.expecting`, `comparand`, `continues`,
  `item_words`, `statement`, `written`, `keyword_case` — §4's Request has six
  fields and none of these. They are what let the engine tell an operand from an
  operator from a connective, which is most of what 0.1.1 fixed.
- **Clause model depth.** `statements`, `repeats`, `opens_an_item`,
  `aliases_with`, `before_the_item`, and a dialect folding its own vocabulary
  into `keywords` at construction.
- **Two correctness harnesses** neither §7 nor anything else asked for:
  `tests/integration/test_acceptance.py`, which accepts every suggestion and
  asks Postgres whether the result parses, and `tests/test_writable.py`, which
  asks offline whether a realistic statement can be written from suggestions
  alone. Between them they found twenty-seven defects.

---

## 1. Context

### Prior art in this codebase

`report_service/reports/pg_autocomplete.py` is a working, stdlib-only,
Postgres-only completion engine — 1,295 lines, backed by ~1,950 lines of tests
across `test_autocomplete.py`, `test_autocomplete_catalog.py` and
`test_autocomplete_catalog_integration.py`. It is the "ad-hoc Postgres helper"
`plan.md` supersedes.

Two facts about it shape this design:

- **It has no macro support.** `_mask()` handles comments, string literals and
  dollar-quoting only. `%userfield|type|default%` lexes as `%`, ident, `|`, …
  exactly as plan.md §8 warns. Migration parity therefore does not require §8.
- **It is Postgres-only, and that is a live problem.** `reports/views.py:997`
  reads `yield None  # pg_autocomplete only works with postgres`. report_service
  already has ClickHouse and Trino report databases receiving zero completion.
  Multi-dialect is the concrete driver, not a hypothetical.

### Consumer contract to preserve

`reports/views.py` calls exactly two things:

- `analyze(sql, pos) -> Context`, used **without a catalog** on the unsupported
  -database path, reading `.replace_from`, `.prefix`, `.clause`.
- `autocomplete(catalog, sql, pos, limit) -> list[Suggestion]`, reading
  `.text`, `.kind`, `.detail`.

The catalog is opened lazily through a context manager; connection is deferred
to the first cache miss. Errors are swallowed and degrade to empty suggestions.

### Decisions taken during brainstorming

| Question | Decision |
| --- | --- |
| Relationship to `pg_autocomplete.py` | It is superseded. Its test corpus and its hard-won edge cases carry over; its code does not |
| Does report_service migrate onto this? | Yes. It is the first consumer and will delete its helper |
| v0.1 dialect depth | Postgres deep; ClickHouse and Trino ship as dialect data passing conformance |
| Live test backends | All three available via docker |
| `Catalog` shape | Plan's 4 core methods + capability protocols (pulled forward from v0.2) |
| Sync vs effect-style (plan §11.1) | Sync core. Async callers pre-fetch into the snapshot catalog. Revisit for the web front end |
| Build approach | **Clean-room rewrite** (option B), with the ported corpus as the acceptance gate |

On the last row: an incremental port was offered and declined. The clean-room
path buys the right architecture from line one at the cost of a long red
period; §7 specifies the xfail burn-down that keeps that period measurable.

---

## 2. Scope

### In v0.1

- The five stages, synchronous: lex → analyse → request → resolve → rank.
- Dialects: `ansi`, `postgres` (deep), `clickhouse`, `trino` (conformance-passing).
- `Catalog` (4 methods), `Cache`, and two capability protocols.
- `catalogs/memory.py` (snapshot) and `catalogs/dbapi.py` (any PEP 249 cursor).
- `adapters/` with clickhouse-connect only.
- plan.md §6.1 free features: GROUP BY, ORDER BY, HAVING, alias generation,
  star expansion.
- `DialectConformance` suite, purity guards, docker-compose for all three backends.
- Entry-point registry for third-party dialects.

**"Deep" and "thin" defined**, since the distinction drives the milestones. All
four dialects ship a complete `Dialect` record and must pass
`DialectConformance`, and all three backends get their introspection SQL and row
mappers validated against a real container in M7 — docker is available, so there
is no excuse for unverified query text. What Postgres alone gets is depth beyond
that: the full ported edge-case corpus, `search_path` handling, the ambiguous
two-segment qualifier, and the differential test against the old module.
ClickHouse and Trino get correct syntax, namespace, clause vocabulary and
introspection — not a decade of edge cases.

### Out of v0.1, deliberately

`Availability` and all of plan.md §7; syntax extensions and the report macro
(§8); §6.2–6.7 (physical layout, joins, value hints, history ranking, doc at
caret, type narrowing); the LSP wrapper; sqlglot/ANTLR parser adapters; async.

The report_service migration is its own piece of work, after v0.1.

### Non-goals (unchanged from plan.md)

Query execution, formatting, linting, full SQL validation, being a language
server.

---

## 3. Repository shape

Fresh git repository at `/home/user/Projects/pysqlsuggestions`. src layout.
Zero runtime dependencies. `requires-python = ">=3.10"` — report_service's
`>=3.11,<3.12` pin sits inside that.

Tooling follows house style: uv with `[dependency-groups]` for dev deps (as in
`igreport`), ruff with line-length 120, single quotes and the `D` docstring
rules (as in `report_service`), mypy strict, pytest.

```
src/pysqlsuggestions/
  __init__.py       # pure API re-exports only
  api.py            # derive_request(), complete()
  types.py          # Kind, Request, Scope, Relation, Projection, Suggestion,
                    # Candidate, Table, Column, Function
  ports.py          # Catalog, Cache, capability protocols
  resolve.py        # the ONLY I/O stage
  engine/
    lex.py          # tolerant tokenizer, Syntax-driven
    analyse.py      # statement_at, clause_at, scope_of, qualifier_and_prefix
    request.py      # derive_request
    local.py        # §6.1 candidates from the Request alone
    rank.py         # scoring, casing, quoting
  dialects/
    ansi.py postgres.py clickhouse.py trino.py
    registry.py     # built-ins + importlib.metadata entry points
    introspection/  # query text + row mappers, no driver imports
  catalogs/
    memory.py
    dbapi.py
  adapters/         # clickhouse-connect only; lazy, never imported at import time
  testing/          # DialectConformance, corpus loader
  py.typed
```

`resolve.py` sits **outside** `engine/` so the purity rule is structural: a test
asserts nothing under `engine/` imports `ports` or `resolve`. This is a stronger
guard than plan.md §10's driver check, which only catches driver leakage.

`__init__.py` re-exports the pure API. Nothing under `adapters/` is imported at
package import time.

---

## 4. Types and the Request seam

### Kind

```python
class Kind(Enum):
    COLUMN = 'column'; TABLE = 'table'; SCHEMA = 'schema'
    FUNCTION = 'function'; ALIAS = 'alias'; KEYWORD = 'keyword'
```

**Deviation from plan.md §3.1:** explicit string values rather than `auto()`.
The consumer serialises `kind` straight into a JSON payload; integer enum values
would make that payload meaningless to the editor.

### Request

```python
@dataclass(frozen=True, slots=True)
class Request:
    kinds: tuple[Kind, ...]
    prefix: str                       # already typed, unquoted and case-folded
    replace_span: tuple[int, int]     # what the editor overwrites
    qualifier: tuple[str, ...] = ()   # segments left of the last dot
    clause: str | None = None
    scope: Scope | None = None
```

**Deviation: `clause` is added.** The consumer reads `ctx.clause` on the
no-catalog path and returns it to the editor. It is also the single most useful
field in a failing-completion bug report.

`replace_span` is `(start_of_prefix, caret)` — not the whole word under the
caret. This matches the existing `replace_from` semantics exactly, so editor
behaviour does not shift during the migration.

`kinds` is ordered most-relevant-first; rank consumes that order.

### Scope, Relation, Projection

```python
@dataclass(frozen=True, slots=True)
class Projection:
    columns: tuple[str, ...]       # explicit output names, in order
    stars: tuple[Relation, ...]    # unresolved * / t.*, expanded at resolve time

@dataclass(frozen=True, slots=True)
class Relation:
    alias: str | None
    path: tuple[str, ...]
    source: Literal['table', 'cte', 'subquery']
    projection: Projection | None  # None -> ask the catalog

@dataclass(frozen=True, slots=True)
class Scope:
    relations: tuple[Relation, ...]
    ctes: Mapping[str, Relation]
    parent: Scope | None
```

**Deviation from plan.md §3.2:** `projection` is a `Projection`, not
`tuple[Column, ...] | None`. The plan's two-state model cannot represent:

```sql
WITH a AS (SELECT * FROM users) SELECT a.⌶
```

The CTE's projection is unknowable without the catalog. A pure analysis stage
cannot produce `Column` values here; returning `None` instead would send resolve
looking for a table named `a`. Three states are needed and all are reachable
from real SQL:

- `projection is None` — a catalog object; ask the catalog.
- `Projection(columns=..., stars=())` — fully self-described; no catalog call.
- `Projection(columns=..., stars=(...))` — partly self-described; expand the
  stars against their source relations at resolve time.

The old implementation reached the same conclusion, modelling it as
`('star', qualifier)` entries alongside named outputs.

### Output and catalog types

```python
@dataclass(frozen=True, slots=True)
class Suggestion:
    text: str; kind: Kind; replace_span: tuple[int, int]
    score: float; detail: str | None = None

@dataclass(frozen=True, slots=True)
class Table:
    schema: str; name: str
    kind: str = 'table'      # normalised: table | view | materialized view | ...

@dataclass(frozen=True, slots=True)
class Column:
    schema: str; table: str; name: str; type: str
    position: int = 0        # attnum / ordinal_position

@dataclass(frozen=True, slots=True)
class Function:
    schema: str | None; name: str; args: str; result: str
```

`Column.position` is carried from day one because plan.md §6.5's "declaration
order beats alphabetical" is free from all three backends, and adding it later
would mean revisiting every introspection query and row mapper.

`Table.kind` is normalised by the dialect row mappers, so `pg_class.relkind`
letters never leak past the dialect package — plan.md §4's hard rule applied to
data as well as branches.

`Candidate` is the pre-rank value: text, kind, detail and ranking inputs, but no
score and no span. Rank turns candidates into suggestions.

---

## 5. The engine

All analysis operates on **tokens**. There is no masked-string pass.

### 5.1 Lexer

One pass, driven entirely by `Syntax`, never raises.

```python
@dataclass(frozen=True, slots=True)
class Token:
    type: TokenType          # IDENT NUMBER STRING COMMENT OPERATOR PUNCT WS UNKNOWN
    start: int; end: int
    text: str                # raw source slice
    value: str               # unquoted and case-folded, for IDENT
    quoted: bool = False
    terminated: bool = True
    depth: int = 0           # paren nesting, precomputed
```

Three properties earn their place:

- **Tolerance is an attribute, not an exception.** An unterminated string,
  quoted identifier, block comment or dollar-quote produces one token running to
  EOF with `terminated=False`. `WHERE name = 'ab⌶` yields no suggestions rather
  than a crash.
- **`depth` is computed during the scan.** Every downstream "nearest keyword at
  depth 0" question becomes a filter. This replaces `_match_paren`,
  `_paren_spans`, `_branch_span` and `_split_top_level` from the old module.
- **The lexer does not classify keywords.** It emits `IDENT`; analyse consults
  `dialect.keywords`. The lexer depends on dialect *syntax* only, never dialect
  *vocabulary*.

`value` is case-folded per `Syntax.unquoted_case` while `text` keeps the source
slice, so `replace_span` arithmetic stays exact.

### 5.2 Analyse

Four pure functions over the stream:

1. `statement_at(tokens, caret)` — slice to the statement containing the caret,
   splitting on depth-0 `;`.
2. `clause_at(tokens, caret, dialect)` — nearest clause keyword at the caret's
   depth, using `dialect.clauses`.
3. `scope_of(tokens, statement, dialect)` — walks the **whole statement**, not
   the text left of the caret. CTE bodies are recursively analysed into
   `Projection`s; FROM/JOIN items become `Relation`s; nested subqueries become
   child `Scope`s with `parent` links. Returns the innermost scope containing
   the caret.
4. `qualifier_and_prefix(tokens, caret)` — walks back over `ident (. ident)*`
   immediately left of the caret.

### 5.3 Request derivation

`request.py` combines the four. Kind narrowing is pure because `Request` already
holds scope:

- qualifier matching a scope alias or relation name → `(COLUMN,)`
- otherwise read as a namespace level per `dialect.namespace` → `(TABLE,)` or
  `(SCHEMA,)`
- Postgres's ambiguous two-segment qualifier (`schema.table.column` is legal) →
  the union of both readings, never a guess

Resolution order is alias → CTE → namespace, per plan.md §3.3.

### 5.4 Local candidates (§6.1)

`engine/local.py` exposes `local_candidates(request) -> list[Candidate]`,
producing GROUP BY / ORDER BY / HAVING / alias-generation / star-expansion
candidates from the `Request` alone. `complete()` merges its output with
resolve's before ranking.

Keeping these out of `Request` preserves the seam and makes §6.1 fully testable
with no database and no mocks — the property plan.md §10 is trying to buy.

### 5.5 Rank

Pure and totally ordered, so tests are stable. In order:

1. Match strength: exact prefix, then case-insensitive prefix, then subsequence
   on word boundaries (`oi` → `order_items`). Nothing looser — plan.md §6's
   rejection of fuzzy matching is a ranking rule, not a UI note.
2. Kind priority, in `request.kinds` order.
3. `Column.position` — declaration order.
4. Alphabetical, as a last-resort tiebreak only.

Rank also applies casing and quoting, using `dialect.reserved`,
`Syntax.identifier_quotes` and `Syntax.unquoted_case`, and stamps
`replace_span` from the request.

---

## 6. Dialects and ports

### 6.1 The dialect record

```python
@dataclass(frozen=True, slots=True)
class Syntax:
    identifier_quotes: tuple[str, ...] = ('"',)
    line_comments: tuple[str, ...] = ('--',)
    nested_block_comments: bool = False
    string_escape_backslash: bool = False
    unquoted_case: Literal['lower', 'upper', 'preserve'] = 'lower'
    dollar_quoting: bool = False
    cast_operator: str | None = '::'

@dataclass(frozen=True, slots=True)
class Dialect:
    name: str
    syntax: Syntax
    namespace: Namespace
    clauses: ClauseModel
    keywords: frozenset[str]
    reserved: frozenset[str]
    catalog_queries: CatalogQueries
```

**Deviations from plan.md §4:**

- `nested_block_comments` — Postgres nests `/* */`; ClickHouse and Trino do not.
- `string_escape_backslash` — ClickHouse honours `\'` inside string literals;
  Postgres with `standard_conforming_strings=on` does not.
  Both are pure lexer inputs. Getting either wrong produces wrong string spans,
  which corrupts everything downstream.
- `keywords` is split into `keywords` (offered as completions, ideally
  introspected) and `reserved` (drives quoting, ships offline because quoting
  decisions precede any connection — plan.md §4).

Dialects are composed with `dataclasses.replace`, never subclassed. The hard
rule stands: **no `dialect.name` comparisons outside the dialect package.**

### 6.2 Introspection SQL as data — paramstyle

```python
@dataclass(frozen=True, slots=True)
class Query:
    sql: str                          # $1, $2 markers
    row: Callable[[tuple], object]    # row -> Table | Column | Function
```

The three drivers disagree on placeholders: psycopg2 is `%s`, `trino` is `?`,
clickhouse-driver is `%(name)s`. Query text therefore uses neutral `$1`, `$2`
markers — the convention plan.md §7's own sample SQL already uses — and
`catalogs/dbapi.py` rewrites them for whatever `module.paramstyle` reports,
reordering the argument tuple as needed.

plan.md §9 does not mention this. Without it, "one DB-API adapter covers
psycopg2 and trino" is not true.

### 6.3 Ports

```python
class Catalog(Protocol):
    def schemas(self) -> Sequence[str]: ...
    def tables(self, schema: str | None = None) -> Sequence[Table]: ...
    def columns(self, schema: str | None, table: str) -> Sequence[Column]: ...
    def functions(self, schema: str | None = None) -> Sequence[Function]: ...
```

**Deviation from plan.md §5:** `schema` is `str | None` on `tables()` and
`columns()`. `None` means "visible on the search path" for Postgres and "current
database" for ClickHouse. The plan's `tables(schema: str)` cannot express an
unqualified `FROM users⌶`, which is the most common query shape there is.

```python
@runtime_checkable
class SupportsColumnSearch(Protocol):
    def all_columns(self) -> Sequence[Column] | None: ...   # None = too large
    def search_columns(self, prefix: str, limit: int) -> Sequence[Column]: ...

@runtime_checkable
class SupportsKeywords(Protocol):
    def keywords(self) -> Sequence[tuple[str, str]]: ...
```

Detection is `isinstance` against the runtime-checkable protocols. Documented
degradation, per plan.md §5's rule that every feature declares its absent
behaviour:

| Capability absent | Behaviour |
| --- | --- |
| `SupportsColumnSearch` | A bare `SELECT ⌶` before any FROM clause offers keywords and functions, no columns |
| `SupportsKeywords` | The static `dialect.keywords` set is used |

### 6.4 Cache

```python
class Cache(Protocol):
    def get(self, key, default=None): ...
    def __setitem__(self, key, value) -> None: ...
```

The documented key shape is `(role, dialect, schema, table)` **from v0.1**, with
`role` taken from an optional `identity` argument defaulting to `None` — even
though `Availability` is a v0.2 feature. plan.md §7 is right that the failure is
silent and reads like a database privilege bug; adding `role` to the key later
would silently change the meaning of every existing user's cache rather than
breaking loudly.

### 6.5 Resolve

The only I/O in the library. It:

- resolves qualifiers alias → CTE → namespace;
- expands `Projection.stars` by recursing into their source relations;
- detects capabilities and degrades per the table above;
- reads and writes through the `Cache`;
- returns `Candidate` values.

---

## 7. Testing

### 7.1 The ported corpus is the acceptance gate

pgcli's `test_sqlcompletion.py` and the 1,263 lines of `test_autocomplete.py`
are translated into two data files:

- golden requests — `(sql, caret, expected_request)`, for stages 1–3
- resolution cases — `(sql, caret, fixture, expected_texts)`, run against
  `catalogs.memory`

This is translation, not a copy: the existing tests run end-to-end through a
`FakeCatalog`, so each must be split into its pure-request assertion and its
resolution assertion.

Every ported case lands as `pytest.mark.xfail(strict=True)` and flips to passing
as stages land. The burn-down count appears in CI on every commit, so progress
through the clean-room red period is a number rather than a feeling.
`strict=True` fails the build if a case starts passing while still marked, which
is what keeps the count honest.

### 7.2 Purity guards

- No driver in `sys.modules` after `import pysqlsuggestions`.
- An AST scan asserting nothing under `engine/` imports `ports` or `resolve`.
- A CI job running `pip install .` with no extras, then importing the package.

### 7.3 Conformance

`pysqlsuggestions.testing.DialectConformance` — the shared corpus every dialect
must pass: alias resolution, CTE visibility, quoted identifiers, dotted paths at
each namespace level. Parametrised so `ansi`, `postgres`, `clickhouse` and
`trino` all run it in our own suite, which is what makes "ClickHouse and Trino
are proven-thin rather than aspirational" a checkable claim.

### 7.4 Adapter integration

docker-compose with Postgres, ClickHouse and Trino. Tests are marked and skipped
when a backend is unreachable. These are the only tests that can catch a wrong
`pg_proc` join, a mis-parsed `system.columns` type string, or a paramstyle
rewrite that produces valid-but-wrong SQL.

### 7.5 Differential test against the old module

`pg_autocomplete.py` is vendored into `tests/_reference/` and both engines are
run over the whole corpus with the same fixture catalog, diffing output. It is
stdlib-only so it costs nothing to run, and it catches behaviour drift the
translated assertions miss. Deleted when the report_service migration lands.

---

## 8. Milestones

| | Deliverable | Gate |
| --- | --- | --- |
| M1 | Repo, pyproject, ruff/mypy/pytest, layout, purity guard, CI | Green build; corpus loaded as xfails |
| M2 | Lexer + `Syntax` records for all three backends | Lexer tests; tolerance and depth proven |
| M3 | `clause_at`, `qualifier_and_prefix`, `scope_of`, `derive_request` | Golden-request corpus burns down |
| M4 | `engine/local.py` (§6.1) + rank | Still zero I/O |
| M5 | Ports, `catalogs/memory`, `resolve` | Resolution corpus burns down |
| M6 | Dialect data ×4 + `DialectConformance` | Conformance green for all four |
| M7 | `catalogs/dbapi` + paramstyle rewriting + introspection SQL | Integration green against all three containers |
| M8 | clickhouse-connect adapter, entry-point registry, README, 0.1.0 | `pip install .` clean |

---

## 9. Deviations from plan.md, collected

| plan.md | v0.1 | Reason |
| --- | --- | --- |
| §3.1 `Kind` uses `auto()` | Explicit string values | Consumer serialises `kind` to JSON |
| §3.1 `Request` has no `clause` | `clause` added | Consumer reads it on the no-catalog path |
| §3.2 `projection: tuple[Column, ...] \| None` | `Projection` with `columns` and `stars` | Two states cannot represent `WITH a AS (SELECT * FROM users)` |
| §4 `Syntax` | `+ nested_block_comments`, `+ string_escape_backslash` | Real lexer divergence between the three backends |
| §4 `Dialect.keywords` | Split into `keywords` and `reserved` | Completion vocabulary and quoting rules have different lifetimes |
| §5 `tables(schema: str)` | `schema: str \| None` | Cannot otherwise express unqualified `FROM users⌶` |
| §5 `Catalog` 4 methods only | + `SupportsColumnSearch`, `SupportsKeywords` | The bare-`SELECT` case needs them; capability protocols pulled forward from v0.2 |
| §7 cache key from v0.2 | `(role, dialect, schema, table)` from v0.1 | Adding `role` later changes existing caches silently |
| §9 introspection SQL | `$1` markers + paramstyle rewriting | psycopg2, trino and clickhouse-driver disagree on placeholders |
| §12 v0.1 = three dialects | Postgres deep, others conformance-only | Depth where it can be validated and is needed |

## 10. Open questions carried forward

These stay open past v0.1 and are not blocked by it:

1. Sync vs effect-style engine — revisit before the web front end ships.
2. antlr4-c3 vs the hand-written clause model — hand-written stands for v0.1.
3. Macro escaping rules — needed before §8, from the reports side.
4. Query history port — shape, storage, opt-out.
5. ClickHouse effective privileges — needed before §7 covers ClickHouse.
