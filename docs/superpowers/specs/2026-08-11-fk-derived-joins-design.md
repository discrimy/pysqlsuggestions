# FK-derived join completion — design

Date: 2026-08-11
Status: **proposed**. Nothing built yet.

Implements `plan.md` §6.3, the third of the five features `README.md` names as
still to come. `plan.md` is the product vision through v0.4; it was removed from
the working tree and is in git history at `f4cb9cd`.

---

## 1. Context

The plan asks for joins at two levels: a condition, `JOIN users u ON ⌶` →
`o.user_id = u.id`, and a whole clause, `FROM orders o ⌶` → the entire
`JOIN … ON …` with tables ranked by reachability. This document narrows both and
takes a position on the parts the plan left open.

### What the engine does at these positions today

Measured, not assumed, against a two-table `MemoryCatalog`:

| caret | kinds | offered |
| --- | --- | --- |
| `… JOIN users u ON ⌶` | COLUMN, FUNCTION | `o.id`, `u.id`, `u.email`, `o.user_id`, `u.name`, `o.total` |
| `FROM orders o ⌶` | KEYWORD | `JOIN`, `LEFT JOIN`, `INNER JOIN`, `CROSS JOIN`, `WHERE`, … |
| `FROM orders o JOIN ⌶` | TABLE, SCHEMA, KEYWORD | `users`, `orders`, `public`, `ON`, `USING`, … |

At the `ON` position the candidate set is already right and the order is not:
the answer, `o.user_id`, sits fourth behind three columns nobody would join on.

### Prior art in this codebase to follow

- **Capability protocols.** `SupportsColumnSearch` and `SupportsColumnValues`
  are optional and named for what they provide. Absent, the position degrades
  to what it offered before. `ports.py` says outright that a backend without an
  equivalent should not implement one.
- **Introspection SQL as data.** `CatalogQueries` holds `Query` records with
  `$1` markers, rewritten per paramstyle by `catalogs/dbapi.py`. A dialect that
  cannot answer leaves the slot `None`, and `_rows` returns `[]` for it.
- **The engine stays pure.** `tests/test_purity.py` forbids `engine/` importing
  `pysqlsuggestions.ports` or `pysqlsuggestions.resolve`. I/O lives in
  `resolve.py`, which calls pure builders with data.
- **Rank composes small signals.** `_match_strength` dominates; `_kind_bonus`,
  a `position` penalty and a `_LOCAL_BONUS` adjust around it.

### Decisions taken during brainstorming

1. **Declared constraints only.** No `<singular>_id` ↔ `<table>.id` inference in
   this cut. A wrong join condition is valid SQL that silently returns wrong
   rows, which is a worse failure than staying quiet, and the plan itself rates
   inferred pairs far below observed ones. Postgres gets the feature; ClickHouse
   and Trino behave exactly as they do today.
2. **Both levels ship.**
3. **The whole clause fires once `JOIN` is typed**, not at `FROM orders o ⌶`.
   That position keeps its keyword list unchanged. The user has signalled intent
   by typing `JOIN`, so nothing is guessed at them, and the question of how many
   generated clauses may crowd out `WHERE` never arises.
4. **Both directions, forward ranked first.** A constraint is directed and a
   join is not. `auth_user` holds no FK columns and is referenced by seven
   tables in the docker fixture, so forward-only would give a query starting
   there nothing at all. Many-to-one ranks above one-to-many: it is more often
   what is wanted and cannot multiply the result set.
5. **The `ON` position offers the whole condition**, `r.author_id = u.id`, as
   one candidate rather than a better-ranked column. One accept finishes the
   join. Once a qualifier commits the left side it degrades to ranking that
   relation's FK columns up.

### Rejected approaches

- **Folding the reference onto `Column`** as `references: tuple[str, ...] |
  None`, populated by the `columns()` read that already happens. Decision 4
  kills it: answering "what points at `auth_user`" would mean scanning every
  table's columns, which the per-table cache cannot serve without reading the
  whole schema one relation at a time. It also puts a permanently-`None` field
  on a public dataclass for two of four dialects.
- **A caller-supplied join graph**, with no catalog involvement. Works on every
  backend and would take history-mined pairs later, but nothing works out of the
  box: a Postgres user whose constraints are sitting in `pg_constraint` would
  have to write that query themselves, against plan.md §4's "introspect, do not
  hardcode".

The chosen shape keeps that second option available. One `ForeignKey` edge type
behind a protocol means a history-mined source later implements the *same*
protocol rather than a parallel one — the way `SupportsColumnValues` is one
method that Postgres answers from `pg_stats` and ClickHouse from its type text.

---

## 2. Scope

### In

`ForeignKey`; `note` on `Candidate` and `Suggestion`; `Kind.JOIN`;
`SupportsForeignKeys`; `CatalogQueries.foreign_keys` and the Postgres query;
`engine/joins.py`; the two resolve call sites; the rank changes;
`MemoryCatalog(foreign_keys=…)`; one composite FK added to the docker fixture;
demo schema edges and `note` in the demo panel; README.

### Out, deliberately

Name-and-type inference; history-mined pairs (history ranking, plan.md §6.5,
which needs its own port); `USING` proposals; multi-hop reachability;
`Availability` interaction beyond carrying the shared `note` field; ClickHouse
and Trino introspection, which have no constraints to read.

### Non-goals

Validating that a proposed join is semantically sensible, or that the user wants
an inner rather than an outer one. The proposal writes `JOIN`; changing it to
`LEFT JOIN` is one word the user edits.

---

## 3. Types

`types.py` gains one frozen dataclass, in the file's existing style:

```python
@dataclass(frozen=True, slots=True)
class ForeignKey:
    """One declared relationship: `columns` of `table` reference `ref_columns` of `ref_table`."""

    schema: str
    table: str
    columns: tuple[str, ...]
    ref_schema: str
    ref_table: str
    ref_columns: tuple[str, ...]
```

Tuples on both sides, positionally corresponding. A composite key is
representable from the first commit and renders as an `AND` chain; nothing
downstream needs to treat it as unusual.

`Candidate` and `Suggestion` each gain `note: str | None = None`, carrying
`fk: auth_user.id`. This is the annotation slot plan.md §7 specified and is what
physical layout ranking (plan.md §6.2) will use for `sort key`. Both default to
`None`, so the change is additive and no existing construction site moves.

`Kind` gains `JOIN = 'join'`, for a candidate that is a whole clause or a whole
condition rather than a name. A front end can give it its own icon; ranking
treats it as the thing it completes (§6).

---

## 4. The port

```python
@runtime_checkable
class SupportsForeignKeys(Protocol):
    """
    Declared relationships between relations, for join completion.

    Absent: `JOIN <caret>` offers relation names and `ON <caret>` offers columns,
    which is what they offer today. Only declared constraints belong here — a
    backend that keeps none should not implement this rather than guess from
    column names, because a wrong join condition is valid SQL that returns wrong
    rows.
    """

    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]: ...
```

Schema-scoped rather than per-table, which decision 4 forces: the reverse
direction needs the whole edge set, and a per-table call could not answer it
without walking every relation. Prefix-independent like every other `Catalog`
read, so an implementation may cache it for a whole database.

`_Reader` in `resolve.py` gains a `foreign_keys` method following `common_values`
exactly — an `isinstance` guard, then `_read` under the identity-led key:

```python
key = self._key(schema or '', '\x00fk')
```

The `\x00` prefix is the existing convention for a non-table key segment.

---

## 5. Introspection

`CatalogQueries` gains `foreign_keys: Query | None = None`. Postgres fills it;
ClickHouse and Trino leave it `None`, so `dbapi.py:_rows` returns `[]` and the
capability is inert without a second mechanism.

The Postgres query reads `pg_constraint` where `contype = 'f'` and resolves
`conkey` and `confkey` attnums to names through `unnest(…) WITH ORDINALITY`, so
that the two sides stay positionally aligned — the correspondence is the whole
content of a composite key and array order is not guaranteed otherwise. Visibility
follows the existing `tables` query: `$1 = ''` means the search path, a named
schema means that schema.

`array_agg` returns real arrays, which psycopg2 and psycopg3 hand back as lists.
The alternative, `string_agg` with a delimiter, breaks on a column name
containing that delimiter, and a quoted identifier may contain anything.

Row mapping happens in the `Query.row` mapper as everywhere else, so no raw
backend shape escapes the dialect module.

---

## 6. The builders

A new module `engine/joins.py`, pure: it takes `Scope` and `Sequence[ForeignKey]`
as data and imports neither `ports` nor `resolve`, so the purity guard passes
unchanged. `resolve.py` fetches and calls.

```python
def relation_joins(scope, edges, dialect) -> list[Candidate]   # JOIN <caret>
def join_conditions(scope, edges, dialect) -> list[Candidate]  # ON <caret>
```

### 6.1 `relation_joins`

Fires when `clause == 'JOIN'`, `expecting == 'operand'`, and no qualifier has
been typed — a qualifier has committed to a namespace, and that position keeps
today's relation list.

For each `source='table'` relation in scope — a CTE or a derived table has no
constraints — collect every edge touching it, forward where it holds the FK
columns and reverse where it is the referenced side. Each edge becomes one
candidate:

| field | value |
| --- | --- |
| `kind` | `Kind.JOIN` |
| `snippet` | the whole clause, e.g. `auth_user u ON r.author_id = u.id` |
| `label` | the target relation name, so matching runs on what the user types |
| `note` | `fk: auth_user.id` |
| `position` | `0` forward, `1` reverse |

`label` rather than `text` is what `_match_strength` reads (`rank.py:62`), which
is how a snippet already behaves — nobody types the expanded form.

The alias comes from the existing generator in `engine/local.py`, deduped
against aliases already in scope. That dedup is also what makes the self-join
work: `reports_reportgroup.parent_id` references its own table, and the proposed
copy must not answer to the same name as the outer one.

Two edges to one target produce two candidates whose rendered text differs, so
the `(kind, text)` dedup at `rank.py:101` keeps both — `reports_databaseaccess`
references `auth_user` through `user_created_id` and `user_id`, and both are
real answers.

An edge into another schema renders qualified, `billing.invoices i ON …`, since
the bare name would not resolve from a default search path.

### 6.2 `join_conditions`

Fires when `clause == 'ON'` and `expecting == 'operand'`. Pairs the most
recently joined relation with any earlier relation an edge connects, and emits
the whole condition as one candidate: `snippet` `r.author_id = u.id`, `label`
the FK column name, same `note`, same forward-first `position`.

With a qualifier typed the left side is committed and a whole condition is no
longer expressible, so it degrades to ranking that relation's FK columns up,
carrying the note.

### 6.3 Quoting

The snippet path in `_render` returns `expand_snippet(body)` and never reaches
`quote_if_needed`, so **the builder quotes**. `quote_if_needed` is already
exported from `engine/rank.py` for exactly this kind of caller. The docker
fixture's `billing."MonthlyTotals"` exists to catch a builder that forgets.

### 6.4 Accepted limitation

After `ON r.author_id = u.id AND ⌶` the position is indistinguishable from a
fresh `ON`: same clause, same `expecting`, and the `Request` does not carry
statement text by design. The same edge is therefore offered again. The
redundancy is mild and the proposals remain correct for a genuine second
condition, so this is accepted rather than worked around; closing it would mean
widening the `Request` seam, which is a larger decision than this feature should
take.

---

## 7. Ranking

Three changes in `engine/rank.py`, all additive:

1. `_kind_bonus` resolves `Kind.JOIN` to whichever of `TABLE` or `COLUMN` the
   position offers, preferring `TABLE`. This follows the `Kind.CTE` precedent
   already in that function — a CTE occupies a relation position, so it scores
   as one. A join proposal occupies the position of the thing it completes: a
   relation where relations go, a condition where columns go.
2. A `_JOIN_BONUS` lifts a `Kind.JOIN` candidate above the plain names it sits
   among, the way `_LOCAL_BONUS` already lifts in-scope names.
3. Nothing else. Forward versus reverse needs no new mechanism: `position` is
   already a penalty input, so `0` and `1` order them.

Match strength stays dominant, which is what keeps this from being annoying —
a proposal matches on its target's name, so typing `auth_g` drops the proposals
for every other table exactly as it drops the tables themselves.

`plan_insertion` is unchanged. A proposal is one replacement over
`request.replace_span`, and `expand_snippet` returns empty stops for a snippet
with no blanks, so the caret lands at the end of the inserted clause.

---

## 8. Cost and failure

One query per schema per session, cached under the identity-led key like every
other read. It is issued only at the two positions that can consume it, so a
statement whose caret never reaches a `JOIN` or `ON` pays nothing. Constraints
change on DDL rather than on `ANALYZE`, so the cache needs no invalidation
beyond what callers already do.

Degradation needs no new code and has two layers, both already in place: a
catalog that does not implement the protocol fails the `isinstance` guard, and a
dialect with `foreign_keys=None` yields an empty list from `_rows`. Either way
the position behaves as it does today.

A failing introspection query propagates, as every other one does. A broken
catalog is not something this feature should swallow.

---

## 9. Testing

### 9.1 Offline

`MemoryCatalog` gains a `foreign_keys` keyword, needed by unit tests
independently of the demo. Cases: forward; reverse; the self-join alias;
two edges to one target; composite rendered as an `AND` chain; cross-schema
qualification; the mixed-case name quoted; qualifier degradation at `ON r.⌶`;
and a catalog with no constraints behaving exactly as today.

That last case is also a property of the whole change: every existing test uses
a catalog with no foreign keys, so **no current expectation moves**.

### 9.2 The acceptance harnesses

`tests/test_writable.py` offline and `tests/integration/test_acceptance.py`
against a real server. This is the first feature that synthesizes multi-token
SQL rather than one identifier, so an accepted proposal leaving a statement
Postgres still parses is the property most worth proving. Those harnesses found
twenty-seven defects; a feature that writes `JOIN … ON …` belongs in them.

With one correction, found while planning. `test_acceptance.py` walks every
caret in *complete* statements, so any multi-token insertion collides with the
text after the caret whatever its own merits — a correct join clause spliced in
front of an existing one is a syntax error about the collision, not about the
proposal. That is why `Kind.SNIPPET` is already excluded there, and `Kind.JOIN`
has to be excluded for the same reason. The real judgement happens in a
dedicated test in the same file, over prefixes with nothing following the caret,
which is where a synthesized clause can be judged on its own.

### 9.3 Against the container

The fixture already carries the awkward shapes: seven reverse edges into
`auth_user`, self-references on `reports_reportgroup.parent_id` and
`reports_database.alternative_database_id`, and `reports_databaseaccess`
referencing `auth_user` twice.

It carries **no composite foreign key**, and one is added. The
`unnest … WITH ORDINALITY` correspondence is exactly the query text only a real
server can validate, and this project's rule is that docker being available
leaves no excuse for unverified SQL.

### 9.4 Conformance

`DialectConformance` gains one case: a dialect whose `foreign_keys` slot is
`None` offers no proposals. The capability is optional, so nothing else in the
suite moves.

---

## 10. Demo

`demo/schema.py` declares edges — it already has `airline_id`, `flight_id`,
`booking_id` and `passenger_id` columns with nothing joining them — and the
panel renders `note`. The browser demo is how anyone sees this feature without a
database, the `MemoryCatalog` keyword is being built for tests regardless, and
the published page stays what it is: invented relationships over invented data,
with no step that could be pointed at a real server.

`README.md` gains a section in the shape of the existing ones, stating plainly
that Postgres has this and ClickHouse and Trino do not, and why.

---

## 11. Open questions carried forward

1. **Name-and-type inference** for backends with no constraints. Deferred, not
   rejected; it should be judged against history-mined pairs rather than against
   nothing, which means after history ranking (plan.md §6.5).
2. **`USING` proposals** where the columns share a name. Cheap once edges exist,
   but it competes with `ON` for the same position and the ranking question is
   unanswered.
3. **Multi-hop reachability.** The plan says "ranked by FK reachability" without
   saying how far. One hop ships here.
4. **`Availability` interaction.** When per-role availability (plan.md §7)
   lands, a join whose condition touches a restricted column should sink rather
   than disappear. The shared `note` field is the seam; the rule belongs in that
   spec.
