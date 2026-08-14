# Per-role availability — design

Date: 2026-08-14
Status: **built**. Three departures from this document are marked in place, and `docs/gaps.md` records them.

Implements `plan.md` §7, "Restricted objects", one of the four features
`README.md` names as still to come. `plan.md` is the product vision through
v0.4; it was removed from the working tree and is in git history at `f4cb9cd`.

---

## 1. Context

Some columns are readable as metadata but not as data. Postgres separates the
two deliberately: `pg_attribute` lists a column to every role regardless of
column grants, so an engine that introspects sees names its connection could
never select. Today this library shows them exactly as it shows every other
column, which means it offers suggestions the server will refuse.

### What the engine does at these positions today

The docker fixture already contains the case, seeded ahead of this work:
`analyst` may read every column of `reports_database` except `password`.

| caret | offered today | what happens on accept |
| --- | --- | --- |
| `SELECT * FROM reports_database d WHERE d.pass⌶` | `password` | `ERROR: permission denied for table reports_database` |
| `SELECT *⌶ FROM mattermost_mattermostchannel` | an expansion naming every column | the same error |
| `SELECT * FROM reports_database d WHERE d.password = ⌶` | value literals, if statistics exist | data the role may not read |

The third is the only one that is worse than a wasted keystroke. Postgres
filters `pg_stats` by the connected role, so on that backend it does not
currently fire — but the rule belongs in the resolver rather than in one
dialect's luck, because `MemoryCatalog` and every third-party adapter have no
such filter.

### Prior art in this codebase to follow

- **`SupportsForeignKeys`.** A backend that cannot answer honestly does not
  implement the capability, and the absence is handled once in `resolve.py`
  rather than in each adapter. Availability takes the same posture.
- **The `note` field.** `2026-08-11-fk-derived-joins-design.md` §3 added an
  annotation slot saying *why* a candidate outranks its neighbours, rendered by
  `convert.py:_detail` into the single field LSP gives a client.
- **`Cache`'s key.** `(role, dialect, schema, table)` has led with `role` since
  v0.1 on the strength of this feature's argument, unused until now.

### Decisions taken during brainstorming

1. **Scope is all of §7**, including the three knock-on effects. The value-hint
   interaction is the reason: it is the only one where the current behaviour is
   a data leak rather than a wasted keystroke, and §7 names it as the
   interaction most likely to be missed.
2. **Postgres answers; ClickHouse and Trino report `UNKNOWN`.** Postgres has
   `has_column_privilege`, which the server evaluates against the connected
   role — free, correct, one column added to queries that already run.
3. **A restricted item stays in the list**, sunk and annotated, rather than
   being dropped.
4. **Availability rides on the rows `Catalog` already returns** rather than
   arriving through a capability method of its own.

### Rejected approaches

**Dropping restricted items entirely.** `tests/grammar/cases.py` gives up a
valid `FOR UPDATE` position with the note that *a suggestion the server refuses
costs more than one never made*, and a restricted column is such a suggestion.
The rule does not carry here. It was written about grammar the parser rejects,
where silence and refusal are indistinguishable to the user. A privilege error
names its own cause: the column exists, the user can ask for the grant, and a
name that vanishes instead reads as the engine not knowing about it — which is
the failure §7 opens by rejecting.

**A `SupportsAvailability` protocol** with a method of its own. It matches the
documented capability pattern exactly and would be the natural seam for a
pluggable policy source, but it queries `pg_attribute` a second time for data
the first query could have carried, and it has no good answer for
`SupportsColumnSearch` or `SupportsRelationSearch`: those return rows from many
relations at once, so joining privilege to them would mean a lookup per row —
precisely the prefix-dependent uncacheable read the port design refuses.

**A marker protocol beside the row fields**, declaring that a catalog's rows
mean something. The `UNKNOWN` default already carries that signal.

**Sinking to the bottom of the kind group**, which is what §7 asks for. See §7
below: no penalty constant expresses it.

**A caller-facing flag** choosing between sinking and dropping. The library has
so far preferred one defensible answer to a knob, and this would double the
behaviour every front end and every test has to reason about.

## 2. Scope

### In

`Availability`; the field on `Column`, `Table`, `Candidate` and `Suggestion`;
the privilege column on four Postgres queries and their row mappers;
`MemoryCatalog(restricted=…)`; the lift in `resolve.py` and the three knock-on
rules; the sort key in `rank.py`; `CompletionItemTag.Deprecated` and `reason` in
`convert.py`; one wholly-unreadable relation added to `03-roles.sql`; an
`analyst` integration fixture; the role-separated cache test; README, gaps and
CHANGELOG.

### Out, deliberately

ClickHouse's `system.grants`, which has no `has_column_privilege` equivalent —
effective privileges through role inheritance would have to be reconstructed by
hand, and getting that wrong means either wrongly greying a readable column or
wrongly promising a restricted one. A pluggable policy source for Trino, whose
access control lives outside SQL entirely. `INSERT`, `UPDATE` and `DELETE`
privileges: this engine completes reads, and a fourth state per verb is a
different feature. Row-level security, which restricts rows and never columns.

### Non-goals

Enforcing anything. The server enforces; this reports. A front end that inserts
a restricted suggestion produces a query that fails exactly as it does today,
and the engine's job is to have said so first.

## 3. Types

```python
class Availability(Enum):
    AVAILABLE = 'available'      # the role may read it
    RESTRICTED = 'restricted'    # it exists, the role may not read it
    UNKNOWN = 'unknown'          # the backend cannot tell us
```

Explicit strings rather than §7's `auto()`, following `Kind` in the same file:
consumers serialise these straight into an editor payload, where an integer
would mean nothing.

| record | fields | default |
| --- | --- | --- |
| `Column`, `Table` | `availability` | `UNKNOWN` |
| `Candidate`, `Suggestion` | `availability`, `reason` | `AVAILABLE`, `None` |

The two defaults differ, and that difference is the whole argument for a third
state. On the catalog side `AVAILABLE` would be a claim: every `MemoryCatalog`
row, every ClickHouse column and every third-party adapter's output would assert
that the connected role may read it, on no evidence at all. `UNKNOWN` is what
those actually know. On the engine side the default is `AVAILABLE` because a
keyword, an operator or a generated alias has no privilege question — it is
insertable by construction, and defaulting it to `UNKNOWN` would make the common
case the uncertain one.

At every decision point in §6 and §7 the test is `is RESTRICTED`, so `UNKNOWN`
and `AVAILABLE` behave identically. That is intended: `UNKNOWN` earns its place
as the honest default rather than as a behaviour, and it is the state a policy
source would later replace.

`reason` is separate from `note` rather than reusing it. The FK design guessed
`note` would be the seam here (§11.4 of that document); it is the wrong one. A
join proposal to a restricted column carries both `note='fk: users.id'`, which
says why it ranks high, and `reason='no SELECT privilege'`, which says why it
will fail. One field cannot hold both without silently overwriting one, and the
two are not merely different strings — they point in opposite directions.

## 4. The port

Nothing is added to `Catalog`, which stays at four methods, and no capability
protocol is defined. Availability is data on rows the four existing methods
already return, which is why it reaches `SupportsColumnSearch` and
`SupportsRelationSearch` results for free.

The absence rule the port documentation states — every capability defines what
happens when it is absent — is satisfied by the `UNKNOWN` default rather than by
an `isinstance` check: a catalog that says nothing produces suggestions
identical to today's. There is no branch in `resolve.py` for "availability is
unsupported", because there is no such state to detect.

## 5. Introspection

Four Postgres queries grow one column each. Nothing else about them changes.

| query | expression | reports |
| --- | --- | --- |
| `tables`, `relation_search` | `has_any_column_privilege(c.oid, 'SELECT')` | nothing in the relation is readable |
| `columns`, `column_search` | `has_column_privilege(c.oid, a.attnum, 'SELECT')` | this column is not readable |

The search queries have to grow it too, or §4 loses its point: a column
found by `search_columns` would arrive knowing nothing while the same column
fetched through `columns()` knew.

Row mappers read `True → AVAILABLE`, `False → RESTRICTED`, `NULL → UNKNOWN`.

### What §7 asked for and this does not need

§7 wanted two table-level booleans: `has_any_column_privilege` false to grey the
whole relation, and `has_table_privilege` false while the former is true to mark
a relation that is queryable but whose `SELECT *` will error.

The second needs no field. `has_table_privilege(t, 'SELECT')` false is *exactly*
"at least one column is restricted" — table-level `SELECT` implies every column,
so losing it means some column was not granted individually, which is what the
column rows already say. The star rule in §6 derives from them. `03-roles.sql`
documents the same equivalence from the other side, in the comment explaining
why a column-level `REVOKE` cannot subtract from a table-level `GRANT`.

`Table.availability` does need its own field despite also being derivable, and
the reason is cost rather than logic: `FROM ⌶` lists every relation in the
namespace, and finding out by fetching each one's columns is the read a
completion engine must not make.

### The relkind hazard

`tables` fetches `relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 'i')` — indexes and
sequences included, because both are relations in every sense `pg_class` knows.
`has_any_column_privilege` is not the right question for either: an index has no
grantable columns, and `SELECT` on a sequence means something else entirely.

The mapper reports `UNKNOWN` for those relkinds rather than passing through
whatever the server answers. Asserted against a container rather than assumed —
the failure mode is a wrong answer, not an error, which is the kind this
codebase treats as worse than silence.

### MemoryCatalog

Gains a way to declare restricted columns, so every rule below is testable
without docker. A snapshot carries privilege the same way it carries foreign
keys: as data somebody wrote down, with no claim about where it came from.

## 6. The knock-on rules

Availability is lifted from `Column` and `Table` onto `Candidate` at the
existing builders — one place per record type, no new traversal.

### 6.1 Star expansion

`_star_expansion` (`resolve.py:686`) drops restricted columns from `names` and
sets `reason='3 columns omitted: no SELECT privilege'`.

This one is not a filter but a repair. Over a partly-restricted relation
`SELECT *` is a statement the server refuses outright, so the expansion stops
being a convenience and becomes the fix: it turns a query that errors into one
that runs. The fixture's `mattermost_mattermostchannel` exists to demonstrate
exactly this.

Which is why the expansion itself stays `AVAILABLE` and carries `reason` alone.
It is the only candidate that sets one without being restricted, and the
distinction is load-bearing: marking it `RESTRICTED` would sink the single
suggestion at that caret the server accepts, underneath the columns it was
assembled from. `reason` explains the omission; `availability` says whether
accepting works, and here it does.

The all-restricted case already works. Line 697 returns no candidate when the
name list is empty, with the comment that an expansion to nothing would delete
the star and leave `SELECT  FROM t`.

### 6.2 Value hints

`_values` returns `[]` when the compared column is `RESTRICTED`.

**Moved during implementation**, from `_Reader.common_values` where this
document first put it. `_values` already holds the `Column` — it fetched it to
learn the type — so the check costs no lookup at all, and it covers a path the
capability wrapper cannot see: `datatypes.literals`, where a boolean's or an
enum's values come from the type rather than from statistics. Those leak no
rows, but a literal compared against a column the role cannot reference is a
statement the server refuses either way, so the position is better silent.

The check lives in the resolver rather than in each adapter, per §7, and on
Postgres it is defence in depth — `pg_stats` is role-filtered by the server, so
the one backend that implements `SupportsColumnValues` today would not have
leaked. It is every other adapter that needs the rule, which is precisely why it
cannot live in the adapters.

### 6.3 Join proposals

A proposal to a relation the role may read nothing in keeps its `note`, gains a
`reason`, and sinks. It does not disappear: the join is real, the constraint is
declared, and the user's next move may well be to ask for the grant.

**Narrowed during implementation.** This document first said "whose condition
touches a restricted column on either side", which is column level. At `JOIN ⌶`
the target's columns have not been fetched and fetching them would be one read
per proposal — the cost this design refuses everywhere else. The relation-level
rule is free, because `reader.tables()` already answers the TABLE candidates at
that same caret. It is also the case that occurs: an FK column is a key column
and key columns are granted, while the columns withheld individually — a
password, a phone number — are not the ones constraints are declared on.

`relation_joins` therefore takes `restricted: frozenset[tuple[str, str]]`, which
`resolve` builds from rows it has in hand. `_Reader` gains a per-request memo so
that asking for the relation list twice in one completion costs one read even
when the caller supplied no cache.

### 6.4 Restricted relations

No special case. `has_any_column_privilege` false implies every column
restricted, so `FROM ⌶` sinks the relation and every other caret sinks its
columns, both by the ordinary rule.

## 7. Ranking

One change, at `rank.py:120`. The sort key gains a leading term:

```python
scored.sort(key=lambda row: (row[0], row[1], row[2]))
#                      →    (restricted, -score, length, text)
```

That is "restricted comes after everything available", not §7's "bottom of its
kind group". The kind-group version cannot be expressed: `_KIND_STEP` is 5.0
while match strengths span 25 to 100, so any penalty small enough to keep an
item inside its kind group leaves a restricted exact-prefix match ranked above
an available substring match in that same kind — which is not sunk. A leading
sort key states the rule in one sentence, and one sentence is what a test can
assert.

It also buys a property that was not asked for. `rank.py:126` dedups by
`(kind, text)`, keeping the first survivor of the sort. When two relations in
scope both have `id` and the role may read only one of them, the readable one
now wins that dedup rather than whichever happened to score higher.

§7's "never preselected" and "excluded from single-exact-match auto-insert" need
no work: nothing in this repository sets `preselect` or auto-inserts, so both
clauses hold by construction. Recorded rather than built, so a later reader does
not look for the code.

## 8. Front ends, and the honest constraint

**LSP** needs less than §7 feared. `convert.py:154` already sets `sort_text`
from the engine's ranking index, so the sink arrives with no change at all.
`reason` joins `detail` and `note` in `_detail`, which exists to merge exactly
this sort of annotation into the one field a client has. The single addition is
`tags=[CompletionItemTag.Deprecated]` for a restricted item — strikethrough, and
the closest thing the protocol has to a disabled state.

A client will insert whatever we return. The temptation is to fake blocking with
an empty `textEdit` plus a command; §7 rejects it and so does this, because it
produces an item that silently does nothing, which reads as a bug in the server
rather than as a privilege the user lacks. The difference between what our own
front ends can do and what LSP can is documented in `lsp/README.md` rather than
papered over.

**The VS Code extension** changes in no way — it drives the server, and the
server's items already carry everything.

**The demos** get one restricted column in `demo/schema.py`, so the panel shows
the annotation. The schema is invented rather than exported from anywhere, which
is the standing rule for a page that gets published.

## 9. Cost and failure

No new round trip. Four queries return one more column each, all of them
`has_*_privilege` calls the server answers from cached ACL data without touching
a table.

The failure mode worth naming is the shared connection pool. `has_column_privilege`
evaluates against the *current* connection's role, so a service account shared
across end users reports the service account's privileges: usually nothing looks
restricted, and at worst the engine displays column names to somebody whose own
role could not see them. The library cannot detect this. What it can do is
document it, and prove that the cache key which has anticipated it since v0.1
actually holds — §10.2.

## 10. Testing

### 10.1 Offline

`MemoryCatalog` declares restricted columns, and each rule gets a test: the sink
in `rank`, the omission and annotation in star expansion, `common_values`
refusing, a join proposal sinking with both annotations intact, and a restricted
relation sinking in a `FROM` list.

The strongest evidence is negative. Every existing test must pass **unchanged**.
Defaults of `UNKNOWN` on catalog records and `AVAILABLE` on engine records mean
nothing moves for a catalog that cannot answer, and an existing assertion that
does move is proof the degradation is less honest than §3 claims.

### 10.2 The cache, which is the point

`role` has led the cache key since v0.1 on the strength of an argument alone.
This is the first feature that gives it meaning, so it gets the test the
argument implies: two identities driven through one shared cache against
different readable sets, asserting neither sees the other's.

§7's warning is that this failure is silent and reads as a database privilege
bug rather than a caching bug. It should fail loudly in CI instead.

### 10.3 Against the container

`03-roles.sql` already seeds both column cases — `reports_database.password`
withheld individually, and `mattermost_mattermostchannel` arranged so
`has_any_column_privilege` is true while `has_table_privilege` is false.

It needs one addition: a relation with no grant at all, so
`Table.availability = RESTRICTED` has something real to detect. Without it the
table half of the feature ships tested only against `MemoryCatalog`.

A second Postgres fixture connects as `analyst` rather than `report` and
asserts: `password` restricted while its siblings are not; the star expansion
over `mattermost_mattermostchannel` omitting the ungranted columns; the
unreadable relation sunk in a `FROM` list; and sequences and indexes reporting
`UNKNOWN` per §5.

### 10.4 The row mappers

**Relocated during implementation.** This document assigned the check to
`DialectConformance`, which cannot host it: that suite runs completion cases
against a `MemoryCatalog` and never sees a row mapper. It lives in
`tests/test_dialect_records.py` instead, which already exercises mappers with
fabricated rows — including the assertion that ClickHouse's and Trino's produce
`UNKNOWN`, which they do without a line changing.

Third-party dialects that never heard of this feature stay conformant by
construction, for the same reason.

## 11. Documentation

`README.md` gains a section in the shape of the existing ones and drops
availability from the still-to-come paragraph. `docs/gaps.md` moves the entry
into "Closed since this list was written", recording what §7 got wrong: the
second table-level boolean that was already implied by the column rows, and the
kind-group sink no constant can express. `CHANGELOG.md` gets an entry grouped by
what changes at a caret. `lsp/README.md` states what a client can and cannot be
made to do with a restricted item.

## 12. Open questions carried forward

1. **A policy source for Trino**, whose access control lives in the connector, a
   file rule set, Ranger or OPA. `UNKNOWN` exists so Trino columns are neither
   wrongly greyed nor wrongly promised; a port that lets an embedder supply the
   answer is a feature of its own, and `SupportsAvailability` — rejected here —
   is its natural shape.
2. **ClickHouse's `system.grants`**, deferred rather than refused. It becomes
   tractable if role inheritance can be resolved in one query rather than by
   hand.
3. **Write privileges.** `INSERT INTO ⌶` completing only relations the role may
   write to is the same machinery against a different verb. Not obviously worth
   a second privilege column on every query.
4. **`SET ROLE` per request.** §7 says connections must carry end-user identity.
   This document documents the hazard; making the LSP or a pooled embedder
   actually do it is a connection-management change, not an engine one.
