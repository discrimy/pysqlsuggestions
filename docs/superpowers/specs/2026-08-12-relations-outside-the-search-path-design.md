# Relations outside the search path — design

Date: 2026-08-12
Status: **proposed**. Nothing built yet.

Closes gap 1 of `docs/gaps.md`, and corrects that entry: it describes a problem
with relations, and the same problem is in the column half it explicitly says is
unaffected.

---

## 1. Context

### What the engine does at these positions today

Measured against the docker fixture, whose `search_path` is `"$user", public`
and which holds a `billing` schema outside it:

| caret | offered | why |
| --- | --- | --- |
| `SELECT * FROM invo⌶` | nothing | `resolve._unqualified` calls `reader.tables(None)`, and the Postgres `tables` query answers `pg_table_is_visible` for an empty schema argument |
| `SELECT amou⌶` | nothing | the `column_search` query carries `AND pg_catalog.pg_table_is_visible(c.oid)` |
| `billing.invoices` exists with an `amount` column, and both positions are silent about it. |||

The second row contradicts `gaps.md`, which says "Columns do not have this
problem". They have exactly this problem, from a different line of SQL.

### The bug behind the second row

`resolve.py:292` builds a searched column's FROM clause as `relation=(c.table,)`
and drops `c.schema`. Today nothing reaches it, because the visibility filter
means a searched column is always in the search path and always writable bare.
Lift that filter without fixing this and `SELECT amou⌶` starts writing
`FROM invoices` — a clause that does not resolve.

That ordering matters enough to state plainly: the column half is a missing
answer today and would become a *wrong* answer if half-fixed. This spec fixes
both halves or neither.

### What it costs to look

Measured, because the whole question is whether a completion engine may run this
on a keystroke:

| backend | query | result |
| --- | --- | --- |
| PostgreSQL | `pg_class` join `pg_namespace`, `position()` filter, `LIMIT 200` | 0.4–2.3 ms over 228 relations |
| ClickHouse | `system.tables`, same shape | 1.8–4.2 ms, and it reaches `staging.` from a connection opened on `analytics.` |
| Trino | `postgresql.information_schema.tables`, **one** catalog | 179 ms — and a real answer needs one of these per catalog |

### Prior art in this codebase to follow

- **`SupportsColumnSearch`** is the same shape of thing and exists for the same
  reason: prefix-dependent, so it does not cache, so it is a capability rather
  than a `Catalog` method. `ports.py` carries that argument in full.
- **Optional capabilities degrade in `resolve`, not in adapters.** Absent, the
  position answers exactly what it answers today.
- **`Candidate.qualifier`** already exists to insert `u.id` while matching on
  `id`. A relation needing a schema prefix is the same problem.
- **`Candidate.position`** already costs `0.1` per step in `rank`, which is the
  size of tiebreak this needs and means no new constant.
- **Trino ships no `foreign_keys` query** and the capability is inert there.
  This follows that precedent rather than inventing a new one.

### Decisions taken during brainstorming

1. **Both halves in one slice**, per the ordering argument above.
2. **Merge, in-path first.** With a non-empty prefix the search runs and its
   results join the default-namespace listing. A relation that needs no
   qualifying ranks above one that does, by a margin small enough that a better
   name match still wins. The rejected alternative — search only when the
   default namespace matched nothing — hides `billing.report_archive` whenever
   `public.reports_report` happens to match, and gives the user no sign it did.
3. **Postgres and ClickHouse ship it; Trino does not.** 179 ms per catalog is
   not a keystroke budget. Trino keeps today's behaviour.
4. **An empty prefix searches nothing.** `FROM ⌶` would otherwise enumerate the
   database, which is the query `DbapiCatalog.all_columns` already refuses to
   make for columns and for the same reason.

### Rejected approaches

- **Widening `Catalog.tables(schema=None)` to mean "everywhere".** It is the
  documented default-namespace call, every adapter implements it, and every
  result is cached under a key that assumes it is prefix-independent. Changing
  its meaning would silently change what every existing deployment caches.
- **A `search_path` argument on `Catalog`.** The search path is the server's
  and the engine has no business restating it. What the engine needs is not
  "which schemas are visible" but "find this name anywhere", which is one call.
- **Qualifying every relation always.** It makes the common case ugly —
  `FROM public.reports_report` where `FROM reports_report` reads better — and
  the information is already in `Table.schema` when it is needed.

---

## 2. Scope

### In

- `SupportsRelationSearch`, with a `CatalogQueries.relation_search` slot.
- Postgres and ClickHouse queries; `DbapiCatalog` and `MemoryCatalog` support.
- The merge and the in-path preference in `resolve._unqualified`.
- The column half: the visibility filter lifted, and the schema carried into the
  FROM clause a searched column writes.
- A `search_path` for `MemoryCatalog`, without which none of this is testable
  offline (§6.1).

### Out, deliberately

- **Trino.** §1 decision 3.
- **Cross-catalog search of any kind.** A three-level namespace makes "anywhere"
  mean something much larger, and the one backend with three levels is the one
  that cannot afford the two-level version.
- **Schema ranking.** Which schema a user works in most is a history-ranking
  signal, which is gap 4.

### Non-goals

- Modelling the search path in the engine. The server resolves names; this asks
  it where a name lives and qualifies when the answer is "not where you are".

---

## 3. The port

```python
@runtime_checkable
class SupportsRelationSearch(Protocol):
    """
    Relations by name, across every visible namespace — `FROM ord<caret>` where
    `orders` lives outside the search path.

    Absent: that position offers the default namespace and nothing else, which
    is what it offered before this existed.
    """

    def search_relations(self, prefix: str, limit: int) -> Sequence[Table]:
        """
        The `limit` relations matching `prefix` most closely, in any namespace.

        Empty for an empty prefix: `FROM <caret>` is not a request for every
        relation in the database.

        Prefix-dependent, so unlike `Catalog.tables` it does not cache. Most
        closely, not merely the first found — the truncation happens before
        ranking sees the rows, so an adapter returning storage order can hide an
        exact match behind two hundred near-misses. The argument is the same one
        `SupportsColumnSearch.search_columns` carries.
        """
        ...
```

`Table` already has `schema`, so a result knows how to qualify itself and no new
type is needed.

`CatalogQueries` gains `relation_search: Query | None = None`, absent for ANSI
and Trino.

## 4. Resolution

In `_unqualified`, the `Kind.TABLE` branch becomes: the default-namespace
listing as today, plus — when `request.prefix` is non-empty — whatever
`reader.search_relations(prefix, limit)` returns.

**Dedupe on `(schema, name)`, preferring the unqualified entry.** A relation
inside the search path is returned by *both* calls, and the two render
differently — `invoices` from `tables`, `public.invoices` from the search — so
without this the same relation appears twice under two spellings. `rank`'s own
dedupe cannot catch it: it keys on rendered text, and the two texts differ.

"In-path" is decided by the default listing itself, not by asking the server
anything further: build `{(t.schema, t.name) for t in reader.tables(None)}` —
already fetched, already cached — and a searched relation absent from that set
is one that needs qualifying.

Such a relation gets `qualifier=schema`, which `rank._render` turns into
`billing.invoices` on insertion while matching still runs against `invoices`. It
also gets `position=1`, against `0` for the in-path entries: `rank` charges
`0.1` per position step, which settles a tie between two equally good matches
and is far too small to beat a better one.

## 5. The column half

Two changes, and they must land together.

`column_search` in `dialects/postgres.py` loses
`AND pg_catalog.pg_table_is_visible(c.oid)`. The system-schema exclusion stays —
`pg_%` and `information_schema` are not what anybody means by `SELECT amou`.

`resolve.py:292` becomes `relation=(c.schema, c.table)`. `api._relation_edit`
already renders a relation path by joining `quote_if_needed` over its parts, so
`FROM billing.invoices` falls out with no further change.

The `qualify=` argument stays `c.table`, so the pair of edits reads
`SELECT invoices.amount FROM billing.invoices`. That is deliberate and it is
valid: a qualified FROM entry answers to its bare relation name, so putting the
schema in the column reference too would be noise. The two fields govern
different halves of the same insertion.

Checked against the server rather than assumed: Postgres plans
`SELECT invoices.amount FROM billing.invoices`, and refuses
`SELECT amount FROM invoices` with `relation "invoices" does not exist` — which
is precisely what the engine writes today.

## 6. Testing

### 6.1 `MemoryCatalog` needs a search path

`MemoryCatalog.tables(None)` returns every relation in the snapshot, so the
fixture has no notion of a default namespace and cannot express the gap at all.
It gains one optional argument:

```python
search_path: Sequence[str] | None = None
```

`None` keeps today's behaviour exactly — every schema visible — so no existing
fixture changes. Given a value, `tables(None)` returns only relations in those
schemas and `search_relations` reaches all of them. That is the minimum needed
to write the failing test, and it makes the fixture more faithful to a server
rather than less.

### 6.2 Offline

A new `tests/test_relation_search.py`:

- A prefix matching a relation outside the search path finds it, and the
  suggestion's text is qualified.
- `apply_suggestion` writes `FROM billing.invoices`.
- The same relation is offered once, not twice, when it is inside the search
  path.
- Two equal matches rank in-path first; one better out-of-path match still wins.
- An empty prefix runs no search — asserted through `MemoryCatalog.calls`.
- A catalog without the capability answers exactly as it does today.
- A searched column carries its schema into the FROM clause it writes.

### 6.3 Conformance

`DialectConformance` gains a case, built from the dialect's own namespace: a
prefix reaches a relation in a schema the fixture does not put on the search
path. Skipped where `catalog_queries.relation_search` is `None`, which is how
Trino and ANSI already skip the FK cases.

### 6.4 Against the container

Postgres finds `billing.invoices` from `FROM invo⌶` and ClickHouse finds
`staging.report_executions` from a connection opened on `analytics`. In both
cases the written statement is planned by the server, because a qualified
reference that does not resolve is the failure this whole slice exists to
prevent. Trino is asserted to offer what it offers today.

---

## 7. Documentation

- `docs/gaps.md` loses gap 1, renumbers, and the "Closed" section gains an entry
  that states the correction: columns had this problem too.
- `CHANGELOG.md` names the capability, the two backends that ship it, and why
  Trino does not — with the measured figure, since "too slow" without one is an
  opinion.
- `README.md`'s status paragraph gains this alongside the other landed features,
  in the sentence the last slice already edited.

## 8. Open questions carried forward

1. **Trino.** Not refused on principle — refused at 179 ms per catalog. A
   backend-side metadata cache, or a Trino version whose `information_schema` is
   cheaper, would reopen it.
2. **Schema preference from history.** "In-path first" is a crude stand-in for
   "the schemas this person actually uses". That is gap 4's signal, and this
   slice deliberately does not invent a weaker version of it.
3. **`all_relations`.** `SupportsColumnSearch` has an `all_columns` companion
   for snapshots small enough to hand over whole. No caller needs the relation
   equivalent yet, and `Catalog.tables` already enumerates when a schema is
   named, so it is not added on speculation.
