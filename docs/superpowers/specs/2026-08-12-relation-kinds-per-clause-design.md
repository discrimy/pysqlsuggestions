# A clause says which relations it means — design

Third and last of the slices clearing the recorded debts.

`Kind.TABLE` means "a relation that is not a sequence", one notch coarser than
several positions need. `DROP TABLE ⌶` offers views, which the server refuses;
`DROP VIEW ⌶`, `DROP MATERIALIZED VIEW ⌶` and `DROP INDEX ⌶` answer nothing.

---

## 1. Context

### What the servers say

Run against `docker/docker-compose.yml`.

- `DROP TABLE public.reports_active` → `ERROR: "reports_active" is not a table`.
  **And the engine offers `reports_active` at that caret today.** A wrong
  answer, not a missing one.
- `DROP VIEW public.auth_user` → `ERROR: "auth_user" is not a view`. The
  narrowing has to work in both directions.
- `EXPLAIN SELECT * FROM reports_report_database_id_idx` →
  `ERROR: cannot open relation`. An index belongs out of a relation position for
  the same reason a sequence does.
- The database holds 19 sequences, 31 indexes, 19 tables and 1 view. None of
  the indexes reach the engine: `tables` fetches
  `relkind IN ('r', 'p', 'v', 'm', 'f', 'S')` and `'i'` is not in it.

### The vocabulary each backend reports in `Table.kind`

This is what decides the whole design:

| dialect | values seen |
|---|---|
| Postgres | `table`, `view`, `sequence` — and `materialized view`, `partitioned table`, `foreign table` in the mapping |
| Trino | `table` |
| **ClickHouse** | **`mergetree`, `replacingmergetree`** — storage engine names |

ClickHouse reports engines, not relational categories. A clause declaring
`relation_kinds=('table',)` matches everything on Trino, the right things on
Postgres, and **nothing at all** on ClickHouse.

### Decisions taken during brainstorming

1. **A list of kinds on `Clause`**, not a `Kind` per relation type. §3.
2. **Positive, and therefore declared only where the vocabulary is known.** §2.
3. **`DROP INDEX` included**, which makes this a catalog change and not only a
   filter — chosen over leaving the debt half-closed.

### Rejected approaches

- **`Kind.VIEW`, `Kind.INDEX`, `Kind.MATERIALIZED_VIEW`.** A view is queryable,
  so at `FROM ⌶` it is a relation and must be offered as one. The same view at
  `DROP VIEW ⌶` would carry a different `Kind`, so one object would be drawn two
  ways by a front end colouring by kind — and every position would have to agree
  on which reading it wanted.
- **A positive list on ANSI's `DROP TABLE`.** It would empty the position on
  ClickHouse. This is the same trap `_SEQUENCE`'s docstring already records.
- **A second, negative field** (`excludes=('view',)`) so ANSI could narrow
  without naming a vocabulary. Two fields expressing one idea, to reach a
  backend whose `DROP TABLE` accepts a view anyway.

---

## 2. Scope

### In

- `Clause.relation_kinds`, read by `resolve` in the two relation positions.
- Indexes fetched, and excluded from relation positions by default.
- `DROP VIEW` in ANSI; `DROP MATERIALIZED VIEW`, `DROP INDEX` and `DROP TABLE`'s
  narrowing in Postgres.
- A materialized view in the seed, since stock Postgres has none.

### Out, deliberately

- **`DROP TABLE`'s narrowing anywhere but Postgres.** ClickHouse cannot express
  it and does not need it; Trino could and has no measured fault.
- **`CREATE INDEX`, `ALTER VIEW`, `REFRESH MATERIALIZED VIEW`.** DDL authoring,
  which this engine stops short of by a decision recorded in `docs/gaps.md`.
- **Offering indexes anywhere but `DROP INDEX`.** They are not queryable.

---

## 3. The field

```python
    relation_kinds: tuple[str, ...] = ()
    """
    Which `Table.kind` values this clause's relation position admits.

    Empty means the default: every relation that can be queried. A clause
    naming kinds gets exactly those — `DROP VIEW` takes a view and the server
    refuses it a table.

    Positive rather than negative, and therefore local to a dialect that knows
    its own vocabulary. `Table.kind` is whatever the backend reports:
    `table` and `view` on Postgres and Trino, `mergetree` on ClickHouse. A
    clause naming `table` in the shared baseline would empty that position on
    ClickHouse, which is why `DROP TABLE` declares this in `postgres.py` and not
    in `ansi.py`.
    """
```

`DROP VIEW` is the one that can live in the baseline: all three backends have
the statement and all three spell the kind `view` — ClickHouse's view engine
lowercases to exactly that.

---

## 4. Indexes enter the catalog

`_RELKIND` gains `'i': 'index'`, and both the `tables` and `relation_search`
queries add `'i'` to their `relkind IN (…)` lists.

They must then be excluded from relation positions, or `FROM ⌶` grows by 31
entries in this database and by thousands in a real one. `resolve`'s `_SEQUENCE`
constant becomes a set of the kinds that are in `pg_class` and cannot be read
from:

```python
_NOT_QUERYABLE = frozenset({'sequence', 'index'})
```

Still a negative test, for the reason its predecessor was: no positive list of
ours could enumerate the storage engines a ClickHouse installation has.

**The default and the declared list are different mechanisms**, and that is the
point of §3. `relation_kinds=()` means "not one of `_NOT_QUERYABLE`";
`relation_kinds=('view',)` means "kind is `view`". A clause that declares kinds
does not consult the exclusion at all — `DROP INDEX` wants precisely the things
the exclusion exists to hide.

---

## 5. Where it is read

`resolve`, in the two places a relation position is answered — `_unqualified`'s
`Kind.TABLE` branch and `_qualified`'s. Both already filter on kind; both gain
the clause's list when it declares one.

Nothing in `engine/` changes. The clause is already on the `Request`, and
`resolve` already has the dialect.

---

## 6. Testing

### Unit

`tests/test_relation_kinds.py`, with the two regressions first, because they are
what the design is shaped around:

- `FROM ⌶` is unchanged with indexes in the snapshot.
- `DROP TABLE ⌶` no longer offers the view — the wrong answer this fixes.

Then one test per new clause, and one that ClickHouse's `DROP TABLE` still
offers its `mergetree` relations, which is what says the positive list stayed
out of the baseline.

### Conformance

One case, built from `relation_kinds`: a clause declaring kinds must offer a
fixture relation of one of those kinds. Skipped for a dialect declaring none —
the bargain `parameter()` and `relation_search` already make. The shared fixture
grows a view, so the case has something to find.

### Integration

`DROP TABLE ⌶` offers no view and `DROP VIEW ⌶` offers exactly the seeded one,
against the live server — the vocabulary is the backend's, so a fixture cannot
settle it. The seed grows a materialized view, since stock Postgres 16 has none
and `DROP MATERIALIZED VIEW ⌶` would otherwise assert against an empty list, as
`CALL ⌶` would have without a seeded procedure.

---

## 7. Documentation

- `CHANGELOG.md`: an entry under Unreleased, leading with the wrong answer.
- The `### Statements that are not queries` entry says `DROP VIEW` and
  `DROP INDEX` "are among the silent ones" and that telling a view from a table
  "waits for a second consumer". Both sentences are now false and are rewritten
  where they stand.
- `docs/gaps.md`: the **Relation-kind filtering finer than one notch** bullet in
  "Already named elsewhere" comes out, and the closed-list entry records that
  the shape question — a `Kind` per type versus a list per clause — was answered
  by ClickHouse reporting storage engines.

---

## 8. Open questions carried forward

- **Trino's `DROP TABLE`** could narrow too; it reports `table` and `view`. Left
  until there is a measured fault, as Postgres's was.
- **`CREATE TABLE`** — gap 1, and the next thing on the list rather than a debt.
