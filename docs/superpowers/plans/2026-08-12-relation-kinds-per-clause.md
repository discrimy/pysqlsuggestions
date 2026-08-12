# A Clause Says Which Relations It Means — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `DROP TABLE ⌶` stops offering views, and `DROP VIEW`,
`DROP MATERIALIZED VIEW` and `DROP INDEX` start offering the right ones.

**Architecture:** One `Clause` field naming the `Table.kind` values a relation
position admits, read by one helper in `resolve` that both relation branches
call. Positive, so it is declared only where the dialect knows its own
vocabulary. Indexes enter the catalog and join sequences in the default
exclusion.

**Tech Stack:** Python 3.10+, no runtime dependencies. `uv run pytest`,
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.

## Global Constraints

- **Python 3.10 floor.** No `match`, no `X | Y` in `isinstance`.
- **Zero runtime dependencies.** Standard library only in `src/`.
- **Line length 120, single quotes**; docstrings required on every public
  module, class, function, method and dataclass field.
- **`engine/` may not import `ports` or `resolve`.** Nothing here needs to.
- **A dialect's grammar belongs in the dialect.** A positive kind list is only
  true where the vocabulary is known, which is why `DROP TABLE`'s narrowing goes
  in `postgres.py`.
- **`FROM ⌶` must not change.** Indexes enter the catalog in Task 3 and must not
  reach a relation position — the regression this slice is shaped around, and
  the same one the sequence slice was.
- **Every task ends green:** `uv run pytest`, `ruff check`,
  `ruff format --check`, `mypy`.
- Backends: `docker compose -f docker/docker-compose.yml up --wait`.

---

## File Structure

| file | change |
|---|---|
| `src/pysqlsuggestions/dialects/base.py` | `Clause.relation_kinds` |
| `src/pysqlsuggestions/resolve.py` | `_NOT_QUERYABLE`, `_admits`, both relation branches |
| `src/pysqlsuggestions/dialects/ansi.py` | `DROP VIEW` clause and statement start |
| `src/pysqlsuggestions/dialects/postgres.py` | `DROP TABLE` narrowed; `DROP MATERIALIZED VIEW`, `DROP INDEX`; `relkind 'i'` |
| `src/pysqlsuggestions/testing/__init__.py` | a view in the fixture; one case |
| `tests/test_relation_kinds.py` | new |
| `docker/postgres/01-schema.sql`, `tests/integration/test_backends.py` | a materialized view, two tests |
| `docs/gaps.md`, `CHANGELOG.md` | as described |

**`_SEQUENCE` is used six times in `resolve.py` and only four are exclusions.**
Lines 275, 693 and 699 are `_sequences` and `_qualified`'s sequence branch,
which want `== 'sequence'` exactly and must keep using it. Lines 291, 418 and
427 are the relation positions and become `_admits` calls. Do not replace the
constant globally.

---

## Task 1: a clause can name the kinds it admits

**Files:**
- Modify: `src/pysqlsuggestions/dialects/base.py`, `src/pysqlsuggestions/resolve.py`, `src/pysqlsuggestions/dialects/ansi.py`
- Test: `tests/test_relation_kinds.py` (new)

**Interfaces:**
- Produces: `Clause.relation_kinds: tuple[str, ...] = ()`;
  `resolve._admits(table: Table, wanted: tuple[str, ...]) -> bool`;
  `resolve._NOT_QUERYABLE: frozenset[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_relation_kinds.py`:

```python
"""
Which relations a position means, when "not a sequence" is too coarse.

`DROP TABLE reports_active` is refused — `"reports_active" is not a table` —
and the engine offered it. `DROP VIEW` wants the opposite set, and neither can
be expressed by a filter with one exclusion in it.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {
    ('public', 'auth_user'): [('id', 'bigint')],
    ('public', 'reports_active'): [('id', 'bigint')],
    ('public', 'auth_user_id_seq'): [('last_value', 'bigint')],
}
KINDS = {('public', 'reports_active'): 'view', ('public', 'auth_user_id_seq'): 'sequence'}


def catalog() -> MemoryCatalog:
    """A table, a view and a sequence — the three kinds a position must tell apart."""
    return MemoryCatalog(SNAPSHOT, table_kinds=KINDS, search_path=('public',))


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def test_a_relation_position_is_unchanged() -> None:
    """
    The regression this is shaped around. A view is queryable, so `FROM ⌶` must
    keep offering it; a sequence is not, and must keep being left out.
    """
    found = offered('SELECT * FROM ')
    assert 'auth_user' in found
    assert 'reports_active' in found
    assert 'auth_user_id_seq' not in found


def test_dropping_a_view_offers_views_only() -> None:
    """`DROP VIEW auth_user` is refused: `"auth_user" is not a view`."""
    found = offered('DROP VIEW ')
    assert 'reports_active' in found
    assert 'auth_user' not in found
```

- [ ] **Step 2: Run the tests to verify one fails**

Run: `uv run pytest tests/test_relation_kinds.py -v`
Expected: `test_dropping_a_view_offers_views_only` FAILS — `DROP VIEW` is not a
clause, so the position answers nothing. `test_a_relation_position_is_unchanged`
passes and must keep passing through every task.

- [ ] **Step 3: Add the field**

In `src/pysqlsuggestions/dialects/base.py`, add to `Clause` after
`opens_a_group`:

```python
    relation_kinds: tuple[str, ...] = ()
    """
    Which `Table.kind` values this clause's relation position admits.

    Empty means the default: every relation that can be queried. A clause
    naming kinds gets exactly those — `DROP VIEW` takes a view, and the server
    refuses it a table.

    Positive rather than negative, and therefore local to a dialect that knows
    its own vocabulary. `Table.kind` is whatever the backend reports: `table`
    and `view` on Postgres and Trino, `mergetree` on ClickHouse. A clause naming
    `table` in the shared baseline would empty that position on ClickHouse,
    which is why `DROP TABLE` declares this in `postgres.py` and not here.
    """
```

- [ ] **Step 4: Replace the exclusion constant with a set and a helper**

In `src/pysqlsuggestions/resolve.py`, keep `_SEQUENCE` — three call sites want
that exact string — and add below it:

```python
_NOT_QUERYABLE = frozenset({_SEQUENCE})
"""
Relation kinds that live in the catalog and cannot be read from.

Still a negative test, for the reason the single-kind version was: `Table.kind`
is the storage engine name on ClickHouse — `mergetree`, `replacingmergetree` —
so no positive list of ours could enumerate what a given installation has, and
one that tried would empty its FROM clause.
"""


def _admits(table: Table, wanted: tuple[str, ...]) -> bool:
    """
    Whether this relation belongs where `wanted` kinds are admitted.

    Empty `wanted` is the default relation position: anything queryable. A
    clause that names kinds gets exactly those and does not consult the
    exclusion at all — `DROP INDEX` wants precisely what the exclusion exists
    to hide.
    """
    if wanted:
        return table.kind in wanted
    return table.kind not in _NOT_QUERYABLE
```

- [ ] **Step 5: Read it in both relation positions**

In `_unqualified`, replace the two `Kind.TABLE` filters (the `listed = …` line
and the `search_relations` comprehension's condition):

```python
    if Kind.TABLE in request.kinds:
        wanted = _relation_kinds(request, dialect)
        listed = [table for table in reader.tables(None) if _admits(table, wanted)]
        candidates += [_table_candidate(table) for table in listed]
        # A relation in the default namespace comes back from both calls, and
        # the two render differently — `invoices` and `public.invoices` — so
        # rank's dedupe, which keys on the rendered text, cannot collapse them.
        here = {(table.schema, table.name) for table in listed}
        candidates += [
            _table_candidate(table, qualify=(table.schema,))
            for table in reader.search_relations(request.prefix, limit)
            if _admits(table, wanted) and (table.schema, table.name) not in here
        ]
```

In `_qualified`, replace its `Kind.TABLE` branch:

```python
    if Kind.TABLE in request.kinds:
        wanted = _relation_kinds(request, dialect)
        candidates += [
            _table_candidate(table) for table in reader.tables(request.qualifier[-1]) if _admits(table, wanted)
        ]
```

and add the small accessor beside `_admits`:

```python
def _relation_kinds(request: Request, dialect: Dialect) -> tuple[str, ...]:
    """The kinds the governing clause admits, or none — which means the default."""
    clause = dialect.clauses.get(request.clause) if request.clause else None
    return clause.relation_kinds if clause else ()
```

- [ ] **Step 6: Declare `DROP VIEW` in ANSI**

In `src/pysqlsuggestions/dialects/ansi.py`, add after the `DROP TABLE` entry:

```python
        # The one kind-narrowed clause that belongs in the baseline: all three
        # backends have the statement and all three spell the kind `view` —
        # ClickHouse's view engine lowercases to exactly that.
        Clause(
            name='DROP VIEW',
            suggests=RELATION_REFERENCE,
            followed_by=('CASCADE', 'RESTRICT'),
            relation_kinds=('view',),
        ),
```

and add it to `STATEMENT_START`:

```python
STATEMENT_START = (*EXPLAINABLE, 'DROP TABLE', 'DROP VIEW', 'TRUNCATE', 'ALTER TABLE', 'CALL')
```

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. `tests/test_statement_forms.py` asserts
`offered('DROP ') == ['SEQUENCE', 'TABLE']` in two places; both become
`['SEQUENCE', 'TABLE', 'VIEW']` — the continuations of a shared head are sorted,
so `VIEW` lands last. Update both, and read any other failure before changing
it.

- [ ] **Step 8: Commit**

```bash
git add -A src tests
git commit -m "feat: a clause can name the relation kinds it admits"
```

---

## Task 2: `DROP TABLE` stops offering views

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py`
- Test: `tests/test_relation_kinds.py`

**This is the wrong answer, not the feature.** `DROP TABLE public.reports_active`
→ `ERROR: "reports_active" is not a table`, and the engine offers
`reports_active` at that caret today.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_relation_kinds.py`:

```python
def test_dropping_a_table_no_longer_offers_a_view() -> None:
    """
    Server-verified: `DROP TABLE public.reports_active` is refused with
    `"reports_active" is not a table`, and this position offered it.
    """
    found = offered('DROP TABLE ')
    assert 'auth_user' in found
    assert 'reports_active' not in found


def test_dropping_a_materialized_view_wants_that_kind() -> None:
    """A materialized view is not a view to `DROP VIEW`, nor a table to `DROP TABLE`."""
    snapshot = dict(SNAPSHOT)
    snapshot[('public', 'monthly_totals')] = [('total', 'numeric')]
    kinds = dict(KINDS)
    kinds[('public', 'monthly_totals')] = 'materialized view'
    sql = 'DROP MATERIALIZED VIEW '
    found = [s.text for s in complete(sql, len(sql), POSTGRES, MemoryCatalog(snapshot, table_kinds=kinds))]
    assert found == ['monthly_totals']


def test_clickhouse_keeps_every_relation_at_drop_table() -> None:
    """
    ClickHouse reports storage engines — `mergetree`, `replacingmergetree` — so
    a positive list naming `table` would empty this position there. It inherits
    ANSI's unnarrowed clause, which is why the narrowing lives in postgres.py.
    """
    engines = MemoryCatalog(
        {('analytics', 'report_events'): [('id', 'bigint')]},
        table_kinds={('analytics', 'report_events'): 'mergetree'},
    )
    sql = 'DROP TABLE '
    assert 'report_events' in [s.text for s in complete(sql, len(sql), CLICKHOUSE, engines)]
```

and extend the imports:

```python
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
```

- [ ] **Step 2: Run the tests to verify two fail**

Run: `uv run pytest tests/test_relation_kinds.py -v`
Expected: `no_longer_offers_a_view` and `materialized_view` FAIL.
`test_clickhouse_keeps_every_relation_at_drop_table` passes and is the guard
that Task 2 does not push the narrowing into the baseline.

- [ ] **Step 3: Narrow `DROP TABLE` and add the materialized view**

In `src/pysqlsuggestions/dialects/postgres.py`, add to the `ANSI.clauses.extend(`
call, after the `WITH` entry:

```python
        # Postgres's own relkind vocabulary, so the narrowing is expressible
        # here and not in ANSI — ClickHouse reports storage engines and a
        # positive list naming `table` would empty the position there.
        # `DROP TABLE` takes all three of these and refuses a view:
        # `"reports_active" is not a table`.
        Clause(
            name='DROP TABLE',
            suggests=RELATION_REFERENCE,
            followed_by=('CASCADE', 'RESTRICT'),
            relation_kinds=('table', 'partitioned table', 'foreign table'),
        ),
        Clause(
            name='DROP MATERIALIZED VIEW',
            suggests=RELATION_REFERENCE,
            followed_by=('CASCADE', 'RESTRICT'),
            relation_kinds=('materialized view',),
        ),
```

and extend `statement_start`:

```python
    statement_start=(*ANSI.statement_start, 'DROP SEQUENCE', 'ALTER SEQUENCE', 'DROP MATERIALIZED VIEW'),
```

`RELATION_REFERENCE` is **not** imported in this module yet — checked, having
first written that it was. Add it to the existing
`from pysqlsuggestions.dialects.ansi import ANSI, COLUMN_EXPRESSION, EXPLAINABLE`
line, keeping the names alphabetical.

- [ ] **Step 4: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. `DROP MATERIALIZED VIEW` adds `MATERIALIZED` as a
continuation of `DROP`, so the two `offered('DROP ')` assertions become
`['MATERIALIZED VIEW', 'SEQUENCE', 'TABLE', 'VIEW']` — sorted, so the two-word
phrase leads. Update both.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "fix: DROP TABLE stops offering the views the server refuses"
```

---

## Task 3: indexes enter, and stay out of the way

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py`, `src/pysqlsuggestions/resolve.py`
- Test: `tests/test_relation_kinds.py`

**The risk this task carries.** 31 indexes exist in the demo database and
thousands would in a real one. They must reach `DROP INDEX ⌶` and nowhere else.
`EXPLAIN SELECT * FROM reports_report_database_id_idx` →
`ERROR: cannot open relation`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_relation_kinds.py`:

```python
INDEXED = {**SNAPSHOT, ('public', 'auth_user_pkey'): [('id', 'bigint')]}
INDEX_KINDS = {**KINDS, ('public', 'auth_user_pkey'): 'index'}


def indexed() -> MemoryCatalog:
    """The same fixture with an index in it, which most positions must ignore."""
    return MemoryCatalog(INDEXED, table_kinds=INDEX_KINDS, search_path=('public',))


def test_an_index_is_not_a_relation_position() -> None:
    """
    `SELECT * FROM auth_user_pkey` is `ERROR: cannot open relation`, so an index
    belongs out of a FROM list for the reason a sequence does — and there are
    far more of them: 31 in the demo database against 19 tables.
    """
    sql = 'SELECT * FROM '
    found = [s.text for s in complete(sql, len(sql), POSTGRES, indexed())]
    assert 'auth_user' in found
    assert 'auth_user_pkey' not in found


def test_dropping_an_index_offers_indexes_only() -> None:
    """The one position that wants precisely what every other position hides."""
    sql = 'DROP INDEX '
    found = [s.text for s in complete(sql, len(sql), POSTGRES, indexed())]
    assert found == ['auth_user_pkey']


def test_the_postgres_query_fetches_indexes() -> None:
    """Both paths, because a prefix search must reach one outside the search path."""
    tables = POSTGRES.catalog_queries.tables
    search = POSTGRES.catalog_queries.relation_search
    assert tables is not None
    assert search is not None
    assert "'i'" in tables.sql
    assert "'i'" in search.sql
```

- [ ] **Step 2: Run the tests to verify two fail**

Run: `uv run pytest tests/test_relation_kinds.py -v`
Expected: `dropping_an_index` and `the_postgres_query_fetches_indexes` FAIL.
`test_an_index_is_not_a_relation_position` passes already — the fixture's index
is filtered by nothing yet because `_admits` with an empty `wanted` excludes
only sequences, so this one is genuinely at risk in Step 3 and is the guard.

- [ ] **Step 3: Fetch them**

In `src/pysqlsuggestions/dialects/postgres.py`, add to `_RELKIND`:

```python
    'i': 'index',
```

and add `'i'` to the `relkind IN (…)` list in **both** the `tables` and
`relation_search` queries, so each reads:

```python
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 'i')
```

- [ ] **Step 4: Keep them out of relation positions**

In `src/pysqlsuggestions/resolve.py`, widen the exclusion:

```python
_NOT_QUERYABLE = frozenset({_SEQUENCE, 'index'})
```

and extend its docstring's first line to name both:

```python
"""
Relation kinds that live in the catalog and cannot be read from.

A sequence and an index: `SELECT * FROM a_seq` returns its state and is merely
useless, while `SELECT * FROM an_idx` is `ERROR: cannot open relation`. Both are
in `pg_class` and neither is what anybody means by `FROM ⌶` — and indexes
outnumber tables in an ordinary schema, 31 to 19 in the fixture this library
develops against.

Still a negative test, for the reason the single-kind version was: `Table.kind`
is the storage engine name on ClickHouse — `mergetree`, `replacingmergetree` —
so no positive list of ours could enumerate what a given installation has, and
one that tried would empty its FROM clause.
"""
```

- [ ] **Step 5: Declare `DROP INDEX`**

In `src/pysqlsuggestions/dialects/postgres.py`, add after `DROP MATERIALIZED VIEW`:

```python
        Clause(
            name='DROP INDEX',
            suggests=RELATION_REFERENCE,
            followed_by=('CASCADE', 'RESTRICT'),
            relation_kinds=('index',),
        ),
```

and add `'DROP INDEX'` to `statement_start`:

```python
    statement_start=(
        *ANSI.statement_start,
        'DROP SEQUENCE',
        'ALTER SEQUENCE',
        'DROP MATERIALIZED VIEW',
        'DROP INDEX',
    ),
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. The `offered('DROP ')` assertions become
`['INDEX', 'MATERIALIZED VIEW', 'SEQUENCE', 'TABLE', 'VIEW']`. Also check the
integration suite: `test_postgres_offers_no_sequence_where_a_relation_belongs`
asserts no `_id_seq` at `FROM ⌶` and now shares that position with 31 indexes —
if it fails, Step 4 did not take.

- [ ] **Step 7: Commit**

```bash
git add -A src tests
git commit -m "feat: indexes reach DROP INDEX and no other position"
```

---

## Task 4: the corpus asks every dialect

**Files:**
- Modify: `src/pysqlsuggestions/testing/__init__.py`
- Test: `tests/test_conformance.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conformance.py`:

```python
def test_the_corpus_asks_a_dialect_about_the_kinds_it_narrows_to() -> None:
    """
    A clause naming kinds no relation in the catalog has is silent rather than
    wrong — a misspelt kind, or one this backend does not report. Only a
    behavioural case sees it.
    """
    for dialect in SHIPPED:
        narrowed = [c for c in dialect.clauses.clauses if c.relation_kinds]
        cases = [case for case in DialectConformance.cases(dialect) if 'kinds' in case.name]
        assert bool(cases) == bool(narrowed), dialect.name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_conformance.py -k kinds -v`
Expected: FAIL — no case mentions kinds, while ANSI, Postgres, ClickHouse and
Trino all declare `DROP VIEW` with `relation_kinds=('view',)`.

- [ ] **Step 3: Put a view in the fixture and add the case**

In `src/pysqlsuggestions/testing/__init__.py`, add to `snapshot` in `catalog`:

```python
            (SCHEMA, 'active_users'): list(USERS),
```

and to `kinds`:

```python
        kinds = {(SCHEMA, SEQUENCE): 'sequence', (SCHEMA, 'active_users'): 'view'}
```

Then in `cases`, after the group case:

```python
        # Found by what the clause declares rather than by the name `DROP VIEW`,
        # and asserting the fixture relation of that kind — so the case tests
        # the dialect's own claim against the catalog it will really read.
        narrowed = next((c for c in dialect.clauses.clauses if c.relation_kinds == ('view',)), None)
        if narrowed is not None:
            cases.append(
                Case(
                    name='a clause narrowed to view kinds offers only those',
                    sql=f'{narrowed.name} ',
                    expect=('active_users',),
                    forbid=('users',),
                ),
            )
```

- [ ] **Step 4: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. Adding a relation to the shared fixture changes what
`SELECT * FROM ⌶` returns for every dialect — if a case that expects a schema
name fails, read it before adjusting, because that fixture backs the whole
corpus.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "test: the corpus asks a dialect about the kinds it narrows to"
```

---

## Task 5: the live vocabulary

**Files:**
- Modify: `docker/postgres/01-schema.sql`, `tests/integration/test_backends.py`

**The seed needs a rebuild:** `docker compose -f docker/docker-compose.yml down -v`
then `up --wait`. Stock Postgres 16 has no materialized view, so the assertion
would otherwise be against an empty list — the same reason the seed grew
procedures.

- [ ] **Step 1: Write the failing tests**

Add to `tests/integration/test_backends.py`, in the PostgreSQL section:

```python
def test_postgres_drop_table_offers_no_view(postgres_catalog: DbapiCatalog) -> None:
    """
    `DROP TABLE public.reports_active` is refused with `"reports_active" is not
    a table`, and this position offered it. The vocabulary is the backend's, so
    only a live catalog settles this.
    """
    found = suggest('DROP TABLE ⌶', POSTGRES, postgres_catalog)
    assert 'reports_report' in found
    assert 'reports_active' not in found


def test_postgres_drop_view_offers_the_view(postgres_catalog: DbapiCatalog) -> None:
    """The opposite narrowing, against the same catalog."""
    found = suggest('DROP VIEW ⌶', POSTGRES, postgres_catalog)
    assert 'reports_active' in found
    assert 'reports_report' not in found


def test_postgres_drop_index_reaches_a_real_index(postgres_catalog: DbapiCatalog) -> None:
    """
    Indexes are fetched now, and this is the only position that wants them. The
    seed declares this one by name.
    """
    found = suggest('DROP INDEX ⌶', POSTGRES, postgres_catalog)
    assert 'reports_report_database_id_idx' in found
    assert 'reports_report' not in found


def test_postgres_drop_materialized_view_offers_the_seeded_one(
    postgres_catalog: DbapiCatalog,
) -> None:
    """Stock Postgres 16 ships none, so the seed is where this assertion comes from."""
    found = suggest('DROP MATERIALIZED VIEW ⌶', POSTGRES, postgres_catalog)
    assert 'reports_monthly' in found
    assert 'reports_active' not in found
```

- [ ] **Step 2: Run them to verify the last fails**

Run: `uv run pytest tests/integration/test_backends.py -k "drop_" -v`
Expected: the first three PASS — Tasks 1 to 3 built them — and
`drop_materialized_view` FAILS, because the seed has no materialized view.

- [ ] **Step 3: Seed one**

In `docker/postgres/01-schema.sql`, after the `CREATE VIEW public.reports_active`
block:

```sql
-- Stock PostgreSQL ships no materialized view, so `DROP MATERIALIZED VIEW `
-- would be asserted against an empty list and would pass however broken the
-- narrowing was.
CREATE MATERIALIZED VIEW public.reports_monthly AS
SELECT r.database_id, count(*) AS runs
FROM reports_report r
GROUP BY r.database_id;
```

- [ ] **Step 4: Rebuild and run**

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up --wait
uv run pytest tests/integration -v
```
Expected: PASS. Watch `test_postgres_offers_no_sequence_where_a_relation_belongs`
and `test_postgres_unqualified_position_hides_the_system_catalog`, which both
assert on `FROM ⌶` and now share it with 31 indexes and a materialized view — a
materialized view *is* queryable and belongs there.

- [ ] **Step 5: Run everything and commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add -A tests docker
git commit -m "test: the backend's own vocabulary, against the backend"
```

---

## Task 6: the record

**Files:**
- Modify: `docs/gaps.md`, `CHANGELOG.md`

- [ ] **Step 1: Close the gaps entry**

In `docs/gaps.md`, delete the **Relation-kind filtering finer than one notch**
bullet from "Already named elsewhere" (it begins at line 118), and add to the
top of "Closed since this list was written":

```markdown
- **Relation kinds finer than one notch.** `DROP VIEW ⌶`, `DROP INDEX ⌶` and
  `DROP MATERIALIZED VIEW ⌶` offer what they mean, and `DROP TABLE ⌶` stopped
  offering views — which the server refuses, so that half was a wrong answer
  rather than a missing one.

  The entry said the shape was undecided: a `Kind` per relation type, or a list
  of kinds on `Clause`. ClickHouse decided it. Its `Table.kind` holds storage
  engine names — `mergetree`, `replacingmergetree` — so a positive list naming
  `table` would empty that position there, which is why `DROP TABLE`'s
  narrowing lives in `postgres.py` and only `DROP VIEW` reaches the baseline.
  A `Kind` per type was rejected for a different reason: a view is queryable, so
  the same relation would carry one kind in a `FROM` list and another in a
  `DROP VIEW`.

  Indexes are fetched now and reach exactly one position. There are more of them
  than tables — 31 against 19 in the fixture — so the default exclusion covers
  them beside sequences.
```

- [ ] **Step 2: Correct the entry that said this was waiting**

In `CHANGELOG.md`, the `### Statements that are not queries` entry contains a
paragraph beginning "`DROP VIEW` and `DROP INDEX` are among the silent ones"
and ending "that choice waits for a second consumer". Replace that paragraph:

```markdown
`DROP VIEW` and `DROP INDEX` were among the silent ones when this shipped, and
are not any longer — see *A clause says which relations it means* above. The
choice that was waiting for a second consumer got one, and ClickHouse settled
it: reporting storage engines rather than relational categories is what makes a
positive kind list dialect-local rather than universal.
```

- [ ] **Step 3: Write the new entry**

In `CHANGELOG.md`, directly under `## Unreleased`:

```markdown
### A clause says which relations it means

`DROP TABLE ⌶` used to offer views. `DROP TABLE public.reports_active` is
refused — `"reports_active" is not a table` — so that was a wrong answer, and it
is the reason this landed rather than staying a nicety.

`DROP VIEW ⌶`, `DROP MATERIALIZED VIEW ⌶` and `DROP INDEX ⌶` now offer what they
mean. Indexes reach the catalog for the first time, and reach no other position:
`SELECT * FROM an_index` is `cannot open relation`, and there are more indexes
than tables in an ordinary schema.

`Clause` gains `relation_kinds`. It is a positive list, so it is only true where
the vocabulary is known — `DROP TABLE`'s narrowing is declared for Postgres,
which wrote its own `relkind` mapping, while ClickHouse reports storage engine
names and keeps the unnarrowed clause. `DROP VIEW` is the one that reaches the
baseline: all three backends have the statement and all three spell that kind
`view`.

**`FROM ⌶` is unchanged**, which is most of the work: a view is queryable and
still belongs there, a sequence and an index are not and still do not.
```

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
git add -A docs CHANGELOG.md
git commit -m "docs: the last debt, and what decided its shape"
```

---

## Self-review notes

**Spec coverage.** §3 the field → Task 1. §4 indexes and the exclusion → Task 3.
§5 where it is read → Task 1 Step 5. §6 unit → Tasks 1–3; conformance → Task 4;
integration → Task 5. §7 → Task 6, all three documents.

**Ordering.** Task 1 before all: the field and `_admits` are what the rest
declares into. Task 3 after Task 2 so `DROP `'s continuation list grows once per
task and each task updates it knowing what it added. Task 5 last of the code
tasks, because it asserts against all three.

**Three places a task disturbs its neighbours**, each named in the step that
causes it:
- `offered('DROP ')` is asserted in three places and grows in Tasks 1, 2 and 3.
  `tests/test_statement_forms.py:106` and `:204` compare with `==` and must be
  updated each time; `tests/test_sequences.py:98` uses `>=` on a set and is
  unaffected — worth knowing so it is not 'fixed' too.
- The shared conformance fixture gains a relation in Task 4, which every case
  built on it sees.
- `FROM ⌶` gains 31 indexes and a materialized view in the live database in
  Tasks 3 and 5; two integration tests assert on that position.

**`_SEQUENCE` stays.** Three call sites want that exact string — `_sequences`
and `_qualified`'s sequence branch — and only the relation positions become
`_admits`. A global replace would break `nextval('⌶`.
