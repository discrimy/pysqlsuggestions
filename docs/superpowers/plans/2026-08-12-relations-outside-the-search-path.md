# Relations outside the search path — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `FROM ord⌶` and `SELECT ema⌶` reach relations and columns outside the connection's default namespace, and qualify what they write.

**Architecture:** A new optional capability, `SupportsRelationSearch`, shaped exactly like the existing `SupportsColumnSearch` — prefix-dependent, so it does not cache, so it is a capability rather than a `Catalog` method. `resolve` merges its results with the default-namespace listing, dedupes on `(schema, name)`, and marks what needs qualifying. The column half is a two-line fix to a query and a candidate, which must land together because half of it turns a missing answer into a wrong one.

**Tech Stack:** Python 3.10+, no runtime dependencies. pytest, ruff, mypy strict. `uv` runs everything. Docker for the integration suite.

Implements `docs/superpowers/specs/2026-08-12-relations-outside-the-search-path-design.md`.

## Global Constraints

- **Python 3.10 floor.** No `*` unpacking directly inside a subscript, no `match`.
- **Zero runtime dependencies.** Nothing under `src/pysqlsuggestions/` may import outside the standard library.
- **`engine/` stays pure.** No module under `src/pysqlsuggestions/engine/` may import `pysqlsuggestions.ports` or `pysqlsuggestions.resolve`. `tests/test_purity.py` enforces it. All the work here is in `resolve.py`, `ports.py`, `catalogs/` and `dialects/` — none of which is under `engine/`.
- **Line length 120. Single quotes.** `ruff format` with `quote-style = 'single'`.
- **Every public function, class and module needs a docstring.** Ruff's `D` rules are on. House style: a one-line summary, then — where the decision was not obvious — a paragraph saying *why*, naming the failure the code prevents.
- **mypy strict** over `src`, `tests` and `lsp`.
- **Run tests with** `uv run pytest`. Lint with `uv run ruff check .` and `uv run ruff format --check .`. Types with `uv run mypy`.
- **A `%` inside dialect SQL must be doubled** when it reaches a `%`-paramstyle driver. The existing queries write `LIKE 'pg\\_%'` and `tests/test_dbapi.py` guards the rewrite; `catalogs/dbapi.py:54` explains it. Copy the existing spelling exactly rather than inventing one.
- **Integration tests need docker:** `docker compose -f docker/docker-compose.yml up -d --wait`. They skip rather than fail when it is down.
- **Commit after every task.** Message style is `type: lowercase phrase` saying what changed for a reader. See `git log`.

---

## File Structure

**Modified:**

- `src/pysqlsuggestions/ports.py` — `SupportsRelationSearch`.
- `src/pysqlsuggestions/dialects/base.py` — `CatalogQueries.relation_search` slot.
- `src/pysqlsuggestions/dialects/postgres.py` — the relation-search query; the visibility filter lifted from `column_search`.
- `src/pysqlsuggestions/dialects/clickhouse.py` — the relation-search query.
- `src/pysqlsuggestions/catalogs/dbapi.py` — `search_relations`.
- `src/pysqlsuggestions/catalogs/memory.py` — `search_path`, `search_relations`.
- `src/pysqlsuggestions/resolve.py` — `_Reader.search_relations`, the merge in `_unqualified`, the searched-column relation path.
- `src/pysqlsuggestions/testing/__init__.py` — one conformance case.
- `tests/integration/test_backends.py`, `docs/gaps.md`, `CHANGELOG.md`, `README.md`.

**Created:**

- `tests/test_relation_search.py` — everything about reaching past the default namespace, offline.

---

### Task 1: The capability and its query slot

**Files:**
- Modify: `src/pysqlsuggestions/ports.py`
- Modify: `src/pysqlsuggestions/dialects/base.py`
- Test: `tests/test_types.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SupportsRelationSearch` with `search_relations(self, prefix: str, limit: int) -> Sequence[Table]`; `CatalogQueries.relation_search: Query | None = None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_types.py`. Add `from pysqlsuggestions.ports import SupportsRelationSearch` and `from pysqlsuggestions.dialects.base import CatalogQueries` to its imports if absent.

```python
def test_relation_search_is_detected_structurally() -> None:
    """A capability is recognised by shape, so an adapter need not import the protocol."""

    class Answers:
        def search_relations(self, prefix: str, limit: int) -> list[object]:
            """Enough of the shape to be recognised."""
            del prefix, limit
            return []

    assert isinstance(Answers(), SupportsRelationSearch)
    assert not isinstance(object(), SupportsRelationSearch)


def test_a_dialect_may_ship_no_relation_search() -> None:
    """The slot is optional, which is how Trino and ANSI decline it."""
    assert CatalogQueries().relation_search is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_types.py -k relation_search -v`
Expected: FAIL — `ImportError: cannot import name 'SupportsRelationSearch'`.

- [ ] **Step 3: Add the protocol to `ports.py`**

Insert directly below `SupportsColumnSearch`, so the two read together:

```python
@runtime_checkable
class SupportsRelationSearch(Protocol):
    """
    Relations by name across every visible namespace — `FROM ord<caret>` where
    `orders` lives outside the search path.

    Absent: that position offers the default namespace and nothing else, which
    is what it offered before this existed.
    """

    def search_relations(self, prefix: str, limit: int) -> Sequence[Table]:
        """
        The `limit` relations matching `prefix` most closely, in any namespace.

        Empty for an empty prefix. `FROM <caret>` is not a request for every
        relation in the database, and answering it as one is the query a
        completion engine must not make.

        Prefix-dependent, so unlike `Catalog.tables` it does not cache — which
        is why this is a capability and not a fifth `Catalog` method.

        Most closely, not merely the first found: the truncation happens before
        ranking sees the rows, so an adapter returning storage order can hide an
        exact match behind two hundred near-misses. `Table.schema` travels with
        each row, because a relation the search path does not cover has to be
        written qualified.
        """
        ...
```

- [ ] **Step 4: Add the query slot to `dialects/base.py`**

In `CatalogQueries`, after `column_search`:

```python
    relation_search: Query | None = None
    """
    Relations matching a substring, across every visible namespace. `$1` is what has been typed.

    For `FROM ord<caret>` where `orders` is outside the search path. Absent means
    that position sees the default namespace only — the right answer for a
    backend where looking further costs more than a keystroke can spend, which
    on Trino means one `information_schema` query per catalog.
    """
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_types.py -k relation_search -v`
Expected: PASS.

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/ports.py src/pysqlsuggestions/dialects/base.py tests/test_types.py
git commit -m "feat: a capability for finding a relation the search path hides"
```

---

### Task 2: `MemoryCatalog` grows a search path

**Files:**
- Modify: `src/pysqlsuggestions/catalogs/memory.py`
- Test: `tests/test_memory_catalog.py`

**Interfaces:**
- Consumes: `SupportsRelationSearch` from Task 1.
- Produces: `MemoryCatalog(..., search_path: Sequence[str] | None = None)` and `MemoryCatalog.search_relations(prefix, limit) -> Sequence[Table]`.

Without this the fixture has no default namespace — `tables(None)` returns every relation in the snapshot — so the gap cannot be written down offline at all.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_catalog.py`:

```python
SPLIT = {
    ('public', 'reports'): [('id', 'bigint')],
    ('billing', 'invoices'): [('id', 'bigint'), ('amount', 'numeric')],
}


def test_no_search_path_means_everything_is_visible() -> None:
    """The default must not move: every existing fixture relies on it."""
    catalog = MemoryCatalog(SPLIT)
    assert {t.name for t in catalog.tables(None)} == {'reports', 'invoices'}


def test_a_search_path_hides_what_it_does_not_cover() -> None:
    """This is the whole gap, expressed in a fixture."""
    catalog = MemoryCatalog(SPLIT, search_path=('public',))
    assert {t.name for t in catalog.tables(None)} == {'reports'}


def test_naming_a_schema_still_reaches_it() -> None:
    """A search path hides a relation from the bare position, not from the database."""
    catalog = MemoryCatalog(SPLIT, search_path=('public',))
    assert {t.name for t in catalog.tables('billing')} == {'invoices'}


def test_search_relations_reaches_past_the_search_path() -> None:
    """The capability's entire purpose."""
    catalog = MemoryCatalog(SPLIT, search_path=('public',))
    found = catalog.search_relations('invo', 10)
    assert [(t.schema, t.name) for t in found] == [('billing', 'invoices')]


def test_search_relations_answers_nothing_for_an_empty_prefix() -> None:
    """`FROM <caret>` is not a request for every relation in the database."""
    assert MemoryCatalog(SPLIT).search_relations('', 10) == []


def test_search_relations_orders_before_truncating() -> None:
    """`limit` rows in storage order can leave the exact match behind the near-misses."""
    snapshot = {('s', f'orders_variant_{n}'): [('id', 'bigint')] for n in range(20)}
    snapshot[('s', 'orders')] = [('id', 'bigint')]
    assert MemoryCatalog(snapshot).search_relations('orders', 1)[0].name == 'orders'
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_memory_catalog.py -k 'search_path or search_relations or visible or naming_a_schema' -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'search_path'`.

- [ ] **Step 3: Add the parameter**

In `MemoryCatalog.__init__`, add to the keyword-only block after `catalogs`:

```python
        search_path: Sequence[str] | None = None,
```

and after `self._catalogs = ...`:

```python
        self._search_path = tuple(search_path) if search_path is not None else None
        """
        Schemas a bare relation reference reaches, or None for all of them.

        None keeps the fixture's original behaviour — every schema visible —
        because that is what every existing test assumes. Given a value, this
        models the one thing a snapshot otherwise cannot: a relation that exists
        and that `FROM <caret>` does not offer.
        """
```

- [ ] **Step 4: Honour it in `tables`**

Replace the body of `MemoryCatalog.tables` after the `self.calls.append` line:

```python
        if schema is not None:
            return [t for t in self._tables if t.schema == schema]
        if self._catalogs:
            return []
        if self._search_path is None:
            return list(self._tables)
        return [t for t in self._tables if t.schema in self._search_path]
```

- [ ] **Step 5: Add `search_relations`**

Insert directly after `search_columns`, so the two read together:

```python
    def search_relations(self, prefix: str, limit: int) -> Sequence[Table]:
        """
        The `limit` relations matching `prefix` most closely, in any schema.

        Ordered before truncating, which is the port's contract: `limit` rows in
        storage order can leave `orders` behind twenty `orders_variant_NNN`, and
        nothing downstream can recover a row that was never fetched.
        """
        self.calls.append(('search_relations', prefix))
        if not prefix:
            return []
        folded = prefix.lower()
        found = [table for table in self._tables if rank.matches(table.name, folded)]
        found.sort(key=lambda table: (not table.name.lower().startswith(folded), len(table.name), table.name))
        return found[:limit]
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_memory_catalog.py -v`
Expected: PASS, including every pre-existing test — `search_path=None` must change nothing.

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/pysqlsuggestions/catalogs/memory.py tests/test_memory_catalog.py
git commit -m "feat: a snapshot can have a search path, and so can hide a relation"
```

---

### Task 3: The two dialect queries and the DB-API adapter

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py`
- Modify: `src/pysqlsuggestions/dialects/clickhouse.py`
- Modify: `src/pysqlsuggestions/catalogs/dbapi.py`
- Test: `tests/test_dbapi.py`, `tests/test_dialect_records.py`

**Interfaces:**
- Consumes: `CatalogQueries.relation_search` from Task 1.
- Produces: `DbapiCatalog.search_relations(prefix, limit) -> Sequence[Table]`; `POSTGRES.catalog_queries.relation_search` and `CLICKHOUSE.catalog_queries.relation_search` non-None; `TRINO`'s and `ANSI`'s still None.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dialect_records.py`:

```python
def test_only_the_affordable_backends_search_relations() -> None:
    """
    Trino declines on a measurement, not a principle.

    One `information_schema` query per catalog costs ~179ms against the docker
    fixture, and a real answer needs one per catalog. Postgres is 0.4-2.3ms and
    ClickHouse 1.8-4.2ms over the same data.
    """
    assert POSTGRES.catalog_queries.relation_search is not None
    assert CLICKHOUSE.catalog_queries.relation_search is not None
    assert TRINO.catalog_queries.relation_search is None
    assert ANSI.catalog_queries.relation_search is None
```

Append to `tests/test_dbapi.py`. It already has a `FakeCursor` that records `executed` and replays canned rows — use it. Add `from pysqlsuggestions.dialects.trino import TRINO` to the imports.

```python
def test_search_relations_issues_no_query_without_a_prefix() -> None:
    """`FROM <caret>` must not enumerate the database, so nothing is asked at all."""
    cursor = FakeCursor([])
    catalog = DbapiCatalog(lambda: cursor, POSTGRES, paramstyle='format')
    assert catalog.search_relations('', 10) == []
    assert cursor.executed == []


def test_search_relations_is_inert_when_the_dialect_ships_no_query() -> None:
    """Trino's slot is None, and the capability goes quiet rather than failing."""
    cursor = FakeCursor([])
    catalog = DbapiCatalog(lambda: cursor, TRINO, paramstyle='qmark')
    assert catalog.search_relations('ord', 10) == []
    assert cursor.executed == []


def test_search_relations_maps_rows_through_the_dialect() -> None:
    """The schema travels with the row, because that is what makes the insertion qualifiable."""
    cursor = FakeCursor([('billing', 'invoices', 'r', 42)])
    catalog = DbapiCatalog(lambda: cursor, POSTGRES, paramstyle='format')
    [found] = catalog.search_relations('invo', 10)
    assert (found.schema, found.name, found.kind) == ('billing', 'invoices', 'table')
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_dialect_records.py tests/test_dbapi.py -k 'relation_search or search_relations' -v`
Expected: FAIL — the slots are `None` and `DbapiCatalog` has no such method.

- [ ] **Step 3: Add the Postgres query**

In `dialects/postgres.py`, in the `QUERIES = CatalogQueries(...)` block, after `column_search`:

```python
    relation_search=Query(
        sql="""
            SELECT n.nspname, c.relname, c.relkind, c.reltuples
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
              AND position(lower($1) in lower(c.relname)) > 0
            ORDER BY position(lower($1) in lower(c.relname)), length(c.relname), n.nspname, c.relname
            LIMIT 200
        """,
        row=lambda row: Table(
            schema=str(row[0]),
            name=str(row[1]),
            kind=_RELKIND.get(str(row[2]), 'table'),
            # -1 is "never analysed", which is not the same as empty.
            rows=int(row[3]) if row[3] is not None and float(row[3]) >= 0 else None,
        ),
    ),
```

The mapper is character-for-character the one on the `tables` query above it, `float()` included — `reltuples` is a `real`, and `row[3] >= 0` on a driver that hands back `Decimal` or `str` is a different comparison.

No `pg_table_is_visible` here — reaching past visibility is the entire point. The system-schema exclusion stays, because `pg_%` is not what anybody means by `FROM ord`. `ORDER BY` before `LIMIT` is the port's contract, not decoration.

- [ ] **Step 4: Add the ClickHouse query**

In `dialects/clickhouse.py`, after `column_search`:

```python
    relation_search=Query(
        sql=f"""
            SELECT database, name, engine, total_rows FROM system.tables
            WHERE database NOT IN {_INTERNAL}
              AND position(lower(name), lower($1)) > 0
            ORDER BY position(lower(name), lower($1)), length(name), database, name
            LIMIT 200
        """,
        row=lambda row: Table(
            schema=str(row[0]),
            name=str(row[1]),
            kind=str(row[2]).lower(),
            rows=int(row[3]) if row[3] is not None else None,
        ),
    ),
```

ClickHouse's `position` takes `(haystack, needle)`, the opposite of Postgres's `position(needle in haystack)` — the existing `column_search` in this file already spells it that way; copy it rather than the Postgres one.

- [ ] **Step 5: Add the adapter method**

In `catalogs/dbapi.py`, directly after `search_columns`:

```python
    def search_relations(self, prefix: str, limit: int) -> Sequence[Table]:
        """
        Relations matching `prefix` anywhere in the name, closest first, in any namespace.

        Empty for an empty prefix, and empty when the dialect ships no query —
        which is how Trino declines the capability without any code here
        knowing that is what it is doing.
        """
        if not prefix:
            return []
        rows = self._rows(self._dialect.catalog_queries.relation_search, prefix)
        return [row for row in rows if isinstance(row, Table)][:limit]
```

`_rows` already returns `[]` for a `None` query, which is what makes the second sentence true.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_dialect_records.py tests/test_dbapi.py -v`
Expected: PASS.

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/pysqlsuggestions/dialects/ src/pysqlsuggestions/catalogs/dbapi.py tests/test_dialect_records.py tests/test_dbapi.py
git commit -m "feat: two backends that can afford to look past the default namespace"
```

---

### Task 4: Merging the search into the relation position

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py`
- Create: `tests/test_relation_search.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: `_Reader.search_relations(prefix: str, limit: int) -> Sequence[Table]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_relation_search.py`:

```python
"""
Relations the connection's default namespace does not cover.

`FROM invo⌶` found nothing when `invoices` lived in a schema outside the search
path, because the only question the engine asked was "what is visible by
default". The answer is a second question — "where does this name live" — and a
result that knows its own schema, so the insertion can qualify.
"""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Kind

SNAPSHOT = {
    ('public', 'reports'): [('id', 'bigint'), ('name', 'text')],
    ('public', 'report_runs'): [('id', 'bigint')],
    ('billing', 'invoices'): [('id', 'bigint'), ('amount', 'numeric')],
    ('billing', 'reports_archive'): [('id', 'bigint')],
}


def catalog() -> MemoryCatalog:
    """Two schemas, one of them off the search path."""
    return MemoryCatalog(SNAPSHOT, search_path=('public',))


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def test_a_relation_outside_the_search_path_is_found() -> None:
    """The gap itself: this offered nothing at all."""
    assert 'billing.invoices' in offered('SELECT * FROM invo')


def test_it_is_offered_qualified_and_inserts_qualified() -> None:
    """A bare `invoices` would not resolve, which is why the schema travels with the row."""
    sql = 'SELECT * FROM invo'
    [found] = [s for s in complete(sql, len(sql), POSTGRES, catalog()) if s.text == 'billing.invoices']
    assert apply_suggestion(sql, found, dialect=POSTGRES)[0] == 'SELECT * FROM billing.invoices'


def test_matching_runs_against_the_bare_name() -> None:
    """Nobody types the schema to find a relation; the qualifier is about insertion."""
    assert 'billing.invoices' in offered('SELECT * FROM voic')


def test_an_in_path_relation_stays_bare() -> None:
    """`FROM public.reports` reads worse than `FROM reports` and says nothing more."""
    found = offered('SELECT * FROM repo')
    assert 'reports' in found
    assert 'public.reports' not in found


def test_a_relation_is_offered_once() -> None:
    """
    It comes back from both calls — the default listing and the search — and the
    two render differently, so rank's own dedupe cannot catch it.
    """
    assert offered('SELECT * FROM repo').count('reports') == 1


def test_what_needs_no_qualifying_leads() -> None:
    """Both match equally well, and one costs a schema prefix to use."""
    found = offered('SELECT * FROM repo')
    assert found.index('reports') < found.index('billing.reports_archive')


def test_a_better_match_still_wins() -> None:
    """The in-path preference is a tiebreak, not a veto: match quality dominates."""
    found = offered('SELECT * FROM invo')
    assert found[0] == 'billing.invoices'


def test_an_empty_prefix_runs_no_search() -> None:
    """`FROM <caret>` would otherwise ask for every relation in the database."""
    source = catalog()
    complete('SELECT * FROM ', 14, POSTGRES, source)
    assert not [call for call in source.calls if call[0] == 'search_relations']


def test_a_catalog_without_the_capability_is_unchanged() -> None:
    """Absent, the position answers exactly what it answered before this existed."""

    class Plain:
        """A catalog with the four required methods and no capabilities."""

        def __init__(self, inner: MemoryCatalog) -> None:
            self._inner = inner

        def schemas(self, catalog: str | None = None) -> list[str]:
            """Delegate."""
            return list(self._inner.schemas(catalog))

        def tables(self, schema: str | None = None) -> list[object]:
            """Delegate."""
            return list(self._inner.tables(schema))

        def columns(self, schema: str | None, table: str) -> list[object]:
            """Delegate."""
            return list(self._inner.columns(schema, table))

        def functions(self, schema: str | None = None) -> list[object]:
            """Delegate."""
            return list(self._inner.functions(schema))

    sql = 'SELECT * FROM invo'
    assert [s.text for s in complete(sql, len(sql), POSTGRES, Plain(catalog()))] == []  # type: ignore[arg-type]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_relation_search.py -v`
Expected: FAIL — `billing.invoices` is offered nowhere.

- [ ] **Step 3: Add the reader method**

In `resolve.py`, import `SupportsRelationSearch` alongside the other capabilities, and add to `_Reader` directly after `loose_columns`:

```python
    def search_relations(self, prefix: str, limit: int) -> Sequence[Table]:
        """
        Relations matching `prefix` in any namespace.

        Degrades to nothing when the catalog cannot answer, which is the
        documented behaviour when SupportsRelationSearch is absent. Not cached:
        the result depends on the prefix, which changes on every keystroke.
        """
        if not prefix or not isinstance(self._catalog, SupportsRelationSearch):
            return ()
        return self._catalog.search_relations(prefix, limit)
```

- [ ] **Step 4: Merge in `_unqualified`**

Replace the `Kind.TABLE` block:

```python
    if Kind.TABLE in request.kinds:
        listed = reader.tables(None)
        candidates += [_table_candidate(table) for table in listed]
        # A relation in the default namespace comes back from both calls, and
        # the two render differently — `invoices` and `public.invoices` — so
        # rank's dedupe, which keys on the rendered text, cannot collapse them.
        here = {(table.schema, table.name) for table in listed}
        candidates += [
            _table_candidate(table, qualify=table.schema)
            for table in reader.search_relations(request.prefix, limit)
            if (table.schema, table.name) not in here
        ]
        candidates += [
            Candidate(text=name, kind=Kind.CTE, detail='cte', origin='local') for name in (scope.ctes if scope else {})
        ]
```

- [ ] **Step 5: Teach `_table_candidate` to qualify**

```python
def _table_candidate(table: Table, qualify: str | None = None) -> Candidate:
    """
    One relation, qualified when a bare reference would not reach it.

    `position` is the in-path preference and nothing subtler: rank charges 0.1
    per step, which settles a tie between two equally good matches and is far
    too small to outrank a better one. A relation you can write bare is worth
    one step over one that costs a schema prefix — no more than that, or a
    perfect match in another schema would lose to a poor match in this one.
    """
    size = f' ~{_as_count(table.rows)} rows' if table.rows is not None else ''
    return Candidate(
        text=table.name,
        kind=Kind.TABLE,
        detail=f'{table.schema}.{table.name} ({table.kind}){size}',
        qualifier=qualify,
        position=1 if qualify else 0,
    )
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_relation_search.py -v`
Expected: PASS.

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/pysqlsuggestions/resolve.py tests/test_relation_search.py
git commit -m "feat: a relation prefix reaches past the search path, and qualifies what it writes"
```

---

### Task 5: The column half

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (the `column_search` query)
- Modify: `src/pysqlsuggestions/resolve.py` (the searched-column branch of `_unqualified`)
- Test: `tests/test_relation_search.py`

**Interfaces:**
- Consumes: `MemoryCatalog(search_path=...)` from Task 2.
- Produces: nothing later tasks rely on.

These two changes must land in one commit. Lifting the filter alone turns a missing answer into a wrong one — `FROM invoices`, which Postgres refuses with `relation "invoices" does not exist`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_relation_search.py`:

```python
def test_a_searched_column_carries_its_schema_into_the_from_clause() -> None:
    """
    Without this the engine writes `FROM invoices`, which Postgres refuses with
    `relation "invoices" does not exist` — a wrong answer where there had been a
    missing one.
    """
    sql = 'SELECT amou'
    [found] = [s for s in complete(sql, len(sql), POSTGRES, catalog()) if s.kind is Kind.COLUMN]
    assert found.relation == ('billing', 'invoices')
    written = apply_suggestion(sql, found, dialect=POSTGRES)[0]
    assert written == 'SELECT invoices.amount FROM billing.invoices'


def test_the_column_reference_stays_bare() -> None:
    """
    A qualified FROM entry answers to its bare relation name, so the schema in
    the column reference too would be noise. Postgres plans
    `SELECT invoices.amount FROM billing.invoices` and this test pins that shape.
    """
    sql = 'SELECT amou'
    [found] = [s for s in complete(sql, len(sql), POSTGRES, catalog()) if s.kind is Kind.COLUMN]
    assert found.text == 'invoices.amount'
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_relation_search.py -k searched_column -v`
Expected: FAIL — `assert ('invoices',) == ('billing', 'invoices')`.

- [ ] **Step 3: Carry the schema**

In `resolve.py`, in the `else` branch of the `Kind.COLUMN` block:

```python
        else:
            # Nothing is in the FROM yet, so each column carries the relation it
            # would need there. Choosing one is choosing its table as well — and
            # the schema with it, because a searched column may live outside the
            # default namespace and `FROM invoices` would not resolve.
            #
            # The reference itself stays bare: a qualified FROM entry answers to
            # its relation name, so `SELECT invoices.amount FROM billing.invoices`
            # is what this writes and what Postgres plans.
            candidates += [
                _column_candidate(c, qualify=c.table, relation=(c.schema, c.table))
                for c in reader.loose_columns(request.prefix, limit)
            ]
```

- [ ] **Step 4: Lift the Postgres visibility filter**

In `dialects/postgres.py`, in `column_search`, delete this line:

```
              AND pg_catalog.pg_table_is_visible(c.oid)
```

Leave the `pg\\_%` and `information_schema` exclusions exactly as they are. ClickHouse's `column_search` never had a database filter, so it needs no change — which is why the schema-dropping bug was already reachable there and only the fixture's lack of a database-unique column name hid it.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_relation_search.py -v`
Expected: PASS.

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all green. If a test in `tests/test_complete.py` or `tests/test_insertion.py` asserts a one-element `relation` tuple, read it before changing it: the assertion was recording the bug.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/resolve.py src/pysqlsuggestions/dialects/postgres.py tests/test_relation_search.py
git commit -m "fix: a column found by searching writes the schema it was found in"
```

---

### Task 6: Conformance

**Files:**
- Modify: `src/pysqlsuggestions/testing/__init__.py`
- Test: `tests/test_conformance.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: nothing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conformance.py`:

```python
def test_a_dialect_that_cannot_search_relations_gets_no_case() -> None:
    """
    The corpus asks a dialect only what it claims to do.

    Trino ships no relation-search query, so the proposition does not apply —
    the same way it gets no foreign-key case. A corpus that failed it would be
    asserting a capability nobody claimed.
    """
    assert not DialectConformance.check(TRINO)
    assert not [case for case in DialectConformance.cases(TRINO) if 'search path' in case.name]


def test_the_relation_search_case_exists_where_the_query_does() -> None:
    """Postgres and ClickHouse claim it, so the corpus holds them to it."""
    for dialect in (POSTGRES, CLICKHOUSE):
        assert [case for case in DialectConformance.cases(dialect) if 'search path' in case.name]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_conformance.py -k search_path -v`
Expected: FAIL — no case names contain "search path".

- [ ] **Step 3: Give the fixture a schema off the search path**

In `DialectConformance.catalog`, the snapshot gains one relation in a second
schema, and the fixture gains a search path covering only the first:

```python
    @staticmethod
    def catalog(dialect: Dialect) -> MemoryCatalog:
        """
        A fixture shaped to this dialect's namespace depth.

        `OTHER` sits outside the search path on purpose: without a relation the
        bare position cannot see, no case can tell a dialect that searches from
        one that only lists.
        """
        snapshot = {
            (SCHEMA, 'users'): list(USERS),
            (SCHEMA, 'orders'): list(ORDERS),
            (OTHER, 'archived_orders'): list(ORDERS),
        }
        if len(dialect.namespace.levels) >= 3:  # noqa: PLR2004
            return MemoryCatalog(snapshot, catalogs={CATALOG: [SCHEMA, OTHER]})
        return MemoryCatalog(snapshot, search_path=(SCHEMA,))
```

with `OTHER = 'vault'` beside the existing `SCHEMA = 'shop'`.

- [ ] **Step 4: Add the case**

In `DialectConformance.cases`, before the dotted-path loop:

```python
        if dialect.catalog_queries.relation_search is not None:
            cases.append(
                Case(
                    name='a prefix reaches a relation outside the search path',
                    sql='SELECT * FROM archiv',
                    expect=('archived_orders',),
                ),
            )
```

`check` compares against `text.rsplit('.', 1)[-1]`, so the expectation is the
bare name and the qualified insertion still satisfies it.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_conformance.py -v`
Expected: PASS for all four shipped dialects. If a pre-existing case now fails because the fixture gained a relation, read the failure: `test_a_shipped_dialect_conforms` builds its SQL from the dialect, and an extra relation in a hidden schema should not reach any other case.

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/testing/ tests/test_conformance.py
git commit -m "test: the corpus asks whether a dialect can find what it cannot see"
```

---

### Task 7: Against the real servers

**Files:**
- Modify: `tests/integration/test_backends.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: nothing.

- [ ] **Step 1: Bring the backends up**

Run: `docker compose -f docker/docker-compose.yml up -d --wait`

- [ ] **Step 2: Write the failing tests**

Append to `tests/integration/test_backends.py`, each in its own backend's section. The file already imports `apply_suggestion`, `complete`, `Kind`, `pytest` and `POSTGRES_DSN`, and carries `pytestmark = pytest.mark.integration`.

```python
def test_postgres_reaches_a_relation_off_the_search_path(postgres_catalog: DbapiCatalog) -> None:
    """
    `billing` is not on the fixture's search path, so `FROM invo` used to find nothing.

    The written statement is planned by the server, because a qualified
    reference that does not resolve is the failure this exists to prevent.
    """
    psycopg2 = pytest.importorskip('psycopg2')
    sql = 'SELECT * FROM invo'
    [found] = [s for s in complete(sql, len(sql), POSTGRES, postgres_catalog) if s.text == 'billing.invoices']
    written = apply_suggestion(sql, found, dialect=POSTGRES)[0]
    assert written == 'SELECT * FROM billing.invoices'

    connection = psycopg2.connect(POSTGRES_DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'EXPLAIN {written}')
    finally:
        connection.close()


def test_postgres_reaches_a_column_off_the_search_path(postgres_catalog: DbapiCatalog) -> None:
    """The column half of the same gap, and the FROM clause it writes for itself."""
    psycopg2 = pytest.importorskip('psycopg2')
    sql = 'SELECT amou'
    found = [s for s in complete(sql, len(sql), POSTGRES, postgres_catalog) if s.relation == ('billing', 'invoices')]
    assert found, 'no column from billing.invoices was offered'
    written = apply_suggestion(sql, found[0], dialect=POSTGRES)[0]

    connection = psycopg2.connect(POSTGRES_DSN)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'EXPLAIN {written}')
    finally:
        connection.close()


def test_clickhouse_reaches_a_relation_in_another_database(clickhouse_catalog: DbapiCatalog) -> None:
    """The connection is opened on `analytics`; `staging` is a database it does not default to."""
    sql = 'SELECT * FROM report_exec'
    found = [s.text for s in complete(sql, len(sql), CLICKHOUSE, clickhouse_catalog)]
    assert 'report_executions' in found, 'the default database must still answer bare'
    assert 'staging.report_executions' in found, 'the other database must be reachable qualified'


def test_trino_is_unchanged(trino_catalog: DbapiCatalog) -> None:
    """
    Trino ships no relation-search query — 179ms per catalog is not a keystroke.

    Asserted rather than assumed, because "we chose not to" and "we broke it"
    look identical from the outside.
    """
    assert TRINO.catalog_queries.relation_search is None
    found = suggest('SELECT * FROM postgresql.public.reports_repo⌶', TRINO, trino_catalog)
    assert 'reports_report' in found
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/integration/test_backends.py -k 'search_path or another_database or trino_is_unchanged' -v`
Expected: the two Postgres tests and the ClickHouse test fail; `test_trino_is_unchanged` passes from the start, which is the point of it.

If they pass before any change, stop — the fixture's `billing` schema may be on the search path, and the test proves nothing. Check with `SHOW search_path`.

- [ ] **Step 4: Run them for real**

Run: `uv run pytest tests/integration/test_backends.py -k 'search_path or another_database or trino_is_unchanged' -v`
Expected: PASS.

- [ ] **Step 5: Run the whole integration suite**

Run: `uv run pytest -m integration -v`
Expected: PASS. Nothing may regress — in particular `test_postgres_unqualified_position_hides_the_system_catalog`, which asserts `FROM ⌶` opens with no `pg_` relation. The search is prefix-gated, so an empty prefix cannot reach one; if that test fails, the gate is wrong.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_backends.py
git commit -m "test: two servers find what their default namespace hides, and one declines"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/gaps.md`, `CHANGELOG.md`, `README.md`

**Interfaces:**
- Consumes: everything.
- Produces: nothing.

- [ ] **Step 1: Close the gap in `docs/gaps.md`**

Delete `## 1. Relations outside the default namespace` entirely and renumber the rest: `2 → 1`, `3 → 2`, `4 → 3`. Then check every cross-reference — the surviving text names gap numbers in two places:

- in what becomes §1 (procedures and sequences): "a dependency of gap 3" → gap 2.
- in *Not gaps*: "the answer is gap 4, not a heuristic" → gap 3.

- [ ] **Step 2: Record the correction**

Add to the *Closed since this list was written* section, and say plainly that the entry was wrong:

```markdown
- **Relations outside the default namespace.** `FROM ord⌶` reaches a relation in
  any visible schema and writes it qualified. Postgres and ClickHouse ship the
  query; Trino does not, at 179ms per catalog.

  This entry claimed columns did not have the problem. They did: `column_search`
  filtered `pg_table_is_visible`, so `SELECT ema⌶` was as blind as `FROM ord⌶`,
  and the FROM clause a searched column wrote dropped its schema. Both are fixed
  here, and they had to be fixed together — lifting the filter alone would have
  turned a missing answer into `FROM invoices`, which the server refuses.
```

- [ ] **Step 3: Add the CHANGELOG entry**

Under `## Unreleased`, above the star-expansion entry. Adapt the wording to the file's voice, but keep every fact:

```markdown
### A name is found wherever it lives, not only where the search path looks

`FROM invo⌶` found nothing when `invoices` lived in a schema the connection does
not default to. It now finds it and writes `billing.invoices`. Matching still
runs against the bare name, so typing `invo` — or `voic` — reaches it; the
schema is about what gets inserted, not what you have to type.

A relation you can write bare ranks above one that needs a schema prefix, by a
margin small enough that a better name match still wins.

The same gap had a second half nobody had noticed. `SELECT amou⌶` was equally
blind, because the column-search query filtered on visibility too — and the
`FROM` clause a searched column wrote dropped its schema, so lifting that filter
alone would have produced `FROM invoices`, which the server refuses. Both are
fixed: `SELECT amou⌶` now writes `SELECT invoices.amount FROM billing.invoices`.

Optional, and per backend, because the cost is what decides it:

| backend | ships it | measured against the docker fixture |
| --- | --- | --- |
| PostgreSQL | yes | 0.4–2.3 ms over 228 relations |
| ClickHouse | yes | 1.8–4.2 ms, and it reaches another database |
| Trino | no | 179 ms for *one* catalog's `information_schema` |

An empty prefix searches nothing: `FROM ⌶` is not a request for every relation
in the database.

**One known limitation.** Two columns with the same name, in same-named tables,
in different schemas still collapse to a single suggestion — ranking dedupes on
the text to be inserted, and both render `invoices.amount`. Telling them apart
needs a qualifier that can hold a path rather than a name, which is not in this
change.
```

- [ ] **Step 4: Update the README status paragraph**

The sentence currently reads:

> Value hints, FK-derived joins, star expansion and bound parameters landed
> since; still to come are physical layout ranking and history ranking, plus
> per-role availability and the syntax extensions.

Replace with:

> Value hints, FK-derived joins, star expansion, bound parameters and
> cross-schema search landed since; still to come are physical layout ranking
> and history ranking, plus per-role availability and the syntax extensions.

- [ ] **Step 5: Verify**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all green. Then re-read `docs/gaps.md` end to end and confirm no sentence describes behaviour that now exists.

- [ ] **Step 6: Commit**

```bash
git add docs/gaps.md CHANGELOG.md README.md
git commit -m "docs: a gap closed, and the half of it the list got wrong"
```

---

## Verification

After Task 8, from a clean tree:

```bash
docker compose -f docker/docker-compose.yml up -d --wait
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

All four must pass before the branch is offered for review.
