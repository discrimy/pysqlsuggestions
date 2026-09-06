# Catalog reads on large schemas — design

Date: 2026-09-06
Status: **specified**. Nothing built.

Two of the four findings that prompted this are already fixed and shipped on
this branch; they are recorded in §2 as evidence rather than as work. What is
specified here is the remaining three: a bulk column read, a kind-scoped
relation read, and the candidate build that still grows with the catalog.

---

## 1. Context

Nothing in this library was measured against a catalog larger than the docker
fixture, which has nineteen relations. `tests/test_scale.py` measures the pure
stages against a thousand-join query and says nothing about I/O, because
`engine/` performs none.

So the shape of the cost above nineteen relations was not known. It was measured
on 2026-09-06 against three generated Postgres schemas — 100, 1 000 and 5 000
tables, the largest being 5 000 tables of 30 columns with two declared indexes
and a primary key each and a foreign key chain through all of them — 20 000
relations in `pg_class` against 5 000 queryable ones. Also against the real
ClickHouse and Trino fixtures. The numbers below are from that run and are
reproducible from the generator described in §9.

Three separate costs came out of it, and they are separate: one is round trips,
one is rows fetched and cached, one is Python work per keystroke that no cache
touches. A single "make it faster" change would have addressed at most one.

---

## 2. What was measured

### 2.1 Fixed already, and recorded here as the evidence

**Trino answered from every federated catalog.** `system.jdbc.columns` was asked
for a relation by name with nothing constraining `table_cat`, because the
predicate `($1 = '' OR table_schem = $1)` is vacuously true for an empty `$1`. A
postgresql-bound catalog returned ClickHouse's columns for a relation Postgres
does not have. 9.8 s against 0.05 s once bounded — the slowest read anywhere in
the library, at the commonest caret shape there is.

The repair had a trap worth keeping in view for everything below: the natural
spelling is one disjunction, which is *correct and exactly as slow*, because
Trino pushes conjuncts into a connector and cannot push a disjunction. Only
elapsed time distinguishes the two, which is why that one has a timing test.

**Two pure functions were recomputed per candidate per keystroke.**
`lex.reads_as_one_identifier` and `rank._words`, memoised on the string,
took a warm completion on the 5 000-table schema from 38.0 ms to 28.6 ms at
`FROM ⌶`. Ranking itself fell 48%.

### 2.2 Still open

| | cost | where |
| --- | --- | --- |
| §4 | 21 queries for a 20-relation statement; 498 ms at 20 ms RTT | `resolve._columns_of` |
| §5 | `tables()` returns 20 000 rows to use 5 000; 2 MB cached, 26 ms to decode per keystroke | `Catalog.tables` |
| §6 | 5 000 candidates built and rendered to show 40; ~19 ms of a 28.6 ms warm completion | `resolve` / `rank` seam |

Two further measurements bound the problem and are the reason §7 refuses some
obvious work:

- **A warm cache issues no queries at all.** Every position measured 0 queries
  against a warm `MemoryCache` except the prefix searches, which are uncached by
  contract. The cold path matters at session start and after a TTL expiry; the
  warm path matters on every keystroke.
- **`foreign_keys()` is the slowest Postgres read at 111 ms**, and a cold `JOIN`
  completion costs 203 ms. It is schema-scoped by a decision recorded in
  `ports.py`, and §7 explains why that is left alone.

---

## 3. What all three changes have in common

Each is a case of the port being coarser than the question. `Catalog` has four
methods on purpose — `CLAUDE.md` states the rule and `ports.py` argues it — and
the rule is not in question here. Each change below is therefore a `Supports*`
capability with its absence handled in `resolve.py`, and no existing adapter has
to change.

The absence behaviour is the same in every case: **exactly what happens today**.
That is what makes these safe to add one at a time.

---

## 4. Bulk column reads

### 4.1 The problem

`_columns_of` calls `reader.columns(schema, table)` once per relation in scope.
Measured, cold, on the 5 000-table schema:

| relations in scope | queries | local | +5 ms RTT | +20 ms RTT |
| --- | --- | --- | --- | --- |
| 1 | 2 | 26 ms | 41 ms | 87 ms |
| 20 | 21 | 55 ms | 178 ms | 498 ms |

It is linear in the join count, and the join count is exactly what makes a query
worth completing. Against Trino, whose per-query floor is 60 ms even when
correct, five relations in scope cost six queries.

**It is also flat in the size of the catalog**, which the ladder is what showed.
The same twenty-relation statement at a 20 ms round trip costs 490 ms against a
100-table schema, 491 ms against 1 000 and 495 ms against 5 000: the cost is the
join count and nothing else. So this is not a large-schema problem at all, and
filing it under one — which is what the first draft of this document did — gets
the priority wrong. Every user with a wide query pays it, on any schema, and it
is the only item here that a small database does not grow out of.

`docs/gaps.md` §5 names batching and calls it unreachable, because `_Reader`
discovers its keys as the request resolves. **That reasoning does not hold for
columns.** `request.scope` names every relation before any I/O happens; the keys
are known up front. It holds for `common_values`, which is why §7 leaves that
one alone.

### 4.2 The capability

```python
@runtime_checkable
class SupportsBulkColumns(Protocol):
    def columns_for(
        self,
        relations: Sequence[tuple[str | None, str]],
    ) -> Mapping[tuple[str | None, str], Sequence[Column]]:
        """Columns for several relations at once, keyed as asked."""
```

Absent, or fewer than two relations are wanted: today's loop.

Returning a mapping rather than a flat sequence is load-bearing. A relation the
role cannot see comes back as an absent key rather than an empty list, which is
the distinction `Availability` already draws elsewhere, and a flat sequence
would make "asked for, has no columns" and "not asked for" identical.

### 4.3 Caching stays per relation

The capability is a *transport* optimisation and must not become a cache key.
`_Reader` keeps one entry per relation, as now, and uses the bulk call only to
fill the misses:

1. answer from `_memo`, then from the caller's cache, per relation as today;
2. collect what is still missing;
3. one `columns_for` call if two or more are missing and the capability is
   present, else the existing loop;
4. store each result under its own existing per-relation key.

This is what keeps the win compounding rather than competing with the cache: a
second statement sharing three of its five relations pays for two, and a
bulk-keyed cache entry would have paid for five. It also means no new
`ReadKind`, and no change to the key grammar in `caches/keys.py`.

### 4.4 The part that is not obvious: the marker language

`CatalogQueries` uses fixed `$1`, `$2` markers, and `catalogs/dbapi.py:render`
rewrites them per paramstyle. There is no way to spell "a list of unknown
length", and all three backends need one — `WHERE (n.nspname, c.relname) IN (...)`.

Three options were considered:

1. **Expand at render time.** A new marker, `$1...`, becomes as many
   placeholders as there are values, bound positionally. `render` already owns
   paramstyle translation, which is the same kind of concern, and this works for
   every paramstyle and every backend unchanged. It varies the SQL text with the
   number of relations, which costs server-side statement caching.
2. **One delimited string, split in SQL** — `= ANY(string_to_array($1, ','))`.
   Keeps the text stable and the marker language untouched. Needs an escape rule
   for names containing the delimiter, and a relation may contain anything.
3. **Array-typed parameters.** Cleanest on Postgres, not portable, and `render`
   types values as `Sequence[str]`.

**Recommendation: (1).** (2) reintroduces a quoting problem this library has
already been bitten by once, in a place where getting it wrong returns the wrong
relation's columns rather than failing.

### 4.5 Testing

- `render` expands `$1...` correctly for all five paramstyles, including zero
  and one value, and leaves a `$1...` inside a literal alone — the existing
  `_quoted_spans` machinery already covers that and must keep doing so.
- A counting cursor asserts a 20-relation statement issues 2 queries, not 21.
  This is the honest test: it asserts the round trips, which is the thing being
  changed, and needs no timing.
- An adapter without the capability issues 21 and returns identical suggestions.
- Integration: the same completion against all three backends returns what it
  returns today.

---

## 5. Kind-scoped relation reads

### 5.1 The problem

The Postgres `tables` query fetches relkinds `r p v m f S i`. On the 5 000-table
schema that is 20 000 rows to serve 5 000 queryable ones: 15 000 indexes fetched,
mapped to `Table` objects, cached, and then discarded by `_admits` on every
completion.

| | rows | query |
| --- | --- | --- |
| today | 20 000 | 37.6 ms |
| without indexes | 5 000 | 12.0 ms |

Cached, it is 2 MB of JSON on the `ByteCache` path, which costs 26 ms to decode
**per keystroke** — a warm redis completion measured 75 ms against 42 ms
in-process on a 0.23 ms loopback, and a real redis is further away than that.

### 5.2 Why the indexes cannot simply be dropped

They are used. `DROP INDEX ⌶` declares `relation_kinds=('index',)` and reads
them out of this same list, and `_sequences` filters the same list for
`kind == 'sequence'`. `_admits` is explicit that a clause naming kinds "does not
consult the exclusion at all — `DROP INDEX` wants precisely what the exclusion
exists to hide".

So this is not a filter to tighten. The read has to become answerable by kind.

### 5.3 The capability

```python
@runtime_checkable
class SupportsRelationKinds(Protocol):
    def tables_of_kinds(self, schema: str | None, kinds: tuple[str, ...]) -> Sequence[Table]:
        """Relations of exactly these kinds, or every queryable kind for ()."""
```

Absent: `tables()` as today, filtered in `resolve` as today.

Present, the shipped dialects narrow `tables` to the queryable relkinds and
answer `DROP INDEX` and the sequence positions through the capability. Still
prefix-independent, so still cacheable — under a new `ReadKind`, keyed by schema
and the sorted kinds.

Two consequences to state plainly:

- **A new `ReadKind` changes the key grammar**, which orphans existing cache
  entries. That is already the documented behaviour on upgrade — `FINGERPRINT`
  exists for it and the TTL bounds how long orphans live — but it should be in
  the changelog rather than discovered.
- **`kinds` must be sorted into the key**, or `('index',)` and `('index',)`
  arriving in different orders occupy two entries. Cheap to get wrong, silent
  when wrong.

### 5.4 Testing

- `DROP INDEX ⌶` still offers indexes, and `FROM ⌶` still does not.
- A sequence position still offers sequences.
- A catalog without the capability behaves identically at every position.
- The key for `('index',)` and for `('index', 'table')` differ, and the sorted
  form collapses to one entry.

---

## 6. The candidate build

### 6.1 The problem

A `FROM ⌶` on the 5 000-table schema builds 5 000 `Candidate`s in `resolve`,
then `rank` scores every one of them, renders every survivor into a `Suggestion`
and a four-element sort tuple, sorts all 5 000, and returns 40.

After the memoisation in §2.1 the remaining cost is flat and diffuse. The loop
body in `rank` is the largest line item at 26%, and the largest single callee is
`Suggestion` construction at 14%, followed by the sort-tuple build and `_render`;
nothing else clears 12%. That is the signature of a structural cost rather than a
slow function: the fix is to build fewer, not to compute faster.

It is the only one of the three that a warm cache does not touch, so it is the
floor on every keystroke, and it grows linearly with the catalog for as long as
it is left.

### 6.2 Shape

`rank` selects before it renders. The sort key is
`(availability, -score, len(text), text.lower())`, and only the fourth element
needs `_render`; the first three are available from the `Candidate` alone. So:

1. score every candidate, cheaply, into the first three key elements;
2. keep the best `limit * margin` by bounded selection rather than a full sort;
3. `_render` only those, complete the fourth key element, sort, dedupe, truncate.

The margin exists because `_render` decides the final tiebreak and the dedupe
key. Too small and a rendered tie could reorder across the boundary; the
existing dedupe on `(kind, text)` also collapses rows only after rendering, so
the margin must exceed the worst-case collapse. It has to be justified by
measurement, not chosen.

**This is the one change here that can alter output**, in tie cases, which is
why it is specified last and separately. Everything in §4 and §5 is
output-identical by construction.

### 6.3 An adjunct, measured

`_plain_identifier` is `@cache`d on `(syntax, first_case)` and hashing a `Syntax`
per candidate costs 300 000 hash calls per ten rankings — about 10% of what
remains. Hoisting the dialect-dependent pattern out of the per-candidate loop is
independent of the above and much smaller. Worth doing while in the file; not
worth a design of its own.

### 6.4 Testing

- Golden ranking output is unchanged across the corpus, the grammar suite and
  the report_service suite. Those are 1 989 tests and they are the real
  specification of ranking; any reordering shows up there.
- A test that constructs more candidates than the margin, all tying on the first
  three key elements, and asserts the same order as a full sort.
- `tests/test_scale.py` gains a case asserting the cost grows sub-linearly in
  catalog size, in the loose register that file already uses.

---

## 7. Deliberately not doing

**Batching the cache across a socket.** `docs/gaps.md` §5. Still correct, still
unreached: `_Reader` discovers most of its keys as the request resolves, and
`_memo` already collapses repeats within one. §4 works only because scope names
the relations up front, which is not true of `common_values`.

**Making `foreign_keys` per relation.** It is the slowest Postgres read at
111 ms, and `ports.py` explains why it is schema-scoped: a join is undirected, so
the proposal at `JOIN ⌶` needs the edges pointing *at* the relations in scope,
and no per-relation call could find those without walking the database. The
capability that would fix it is a different one, and it is not this document's.

**Seeding a prefix search from a shorter prefix's cached answer.** Substring
matching is monotone, so every column containing `user` contains `use`, and
filtering a cached shorter answer locally looks free. It is unsound at the size
where it would matter: on the 5 000-table schema `search_columns('u')` matched
55 000 columns and returned 500, so the shorter answer is truncated almost
always, and filtering it silently returns a subset of the right answer. Recorded
so it is not rediscovered.

**Anything about the prefix searches themselves.** `search_columns('u')` costs
247 ms and even a prefix matching nothing costs 28 ms, because the cost is the
unindexable `position(lower($1) in lower(attname))` scan rather than the result
size. The fix is a minimum prefix length or a different index strategy, both of
which change what the user is offered rather than how fast it arrives. It is a
product decision and belongs in its own document.

---

## 8. Order

§4, §5, §6 — largest measured effect first, and least risk first, which here
agree. Each is independently shippable and independently revertible, and only §6
can change output.

The generated schemas are the acceptance harness for all three; none of these
can be honestly reviewed against the nineteen-relation fixture.

---

## 9. Reproducing the measurements

`scripts/bench_catalog.py` builds the ladder and prints every layer above:

    docker compose -f docker/docker-compose.yml up -d --wait
    uv run python -m scripts.bench_catalog --build     # once; a few minutes
    uv run python -m scripts.bench_catalog --rtt 20

It was committed before any of this was designed rather than after, because
every number here is a claim someone will want to re-check, and a benchmark
reconstructed later measures whatever the reconstruction happened to do. The
`--rtt` figure is the one to watch for §4: it reproduces the 498 ms in §4.1,
which is otherwise invisible against a server on the same machine.

The three rungs matter more than the largest one. A single schema gives a
number; the ladder is what says whether a cost grows with the catalog, which is
the difference between a slow query and a design that will not hold.
