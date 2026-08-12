# Procedures and sequences — design

Gap 1 of `docs/gaps.md`, whole. `CALL ⌶` and `nextval('⌶` are ordinary SQL and
answer with nothing, because the catalog knows functions and relations and has
no word for anything else.

Both halves turn out to be one move applied twice — *the catalog reports a
subtype, and the position admits only some subtypes* — which is why they are
designed together rather than in sequence.

---

## 1. Context

### What the engine does at these positions today

Measured, not assumed:

| caret | today |
|---|---|
| `CALL ⌶`, `CALL arc⌶` | nothing |
| `SELECT nextval('⌶`, `SELECT nextval('aut⌶` | nothing |
| `DROP SEQUENCE ⌶`, `ALTER SEQUENCE ⌶` | nothing |
| `DROP ⌶` | `TABLE` |
| `ALTER ⌶` | `TABLE` |

The `CALL` and `DROP SEQUENCE` silences are deliberate — the previous slice made
an unrecognised statement form answer with nothing rather than proposing the
words a statement may begin with. The `nextval('⌶` silence is the general rule
that a caret inside a literal admits nothing unless a comparison gives it a
column to draw values from.

### What the servers say

Every claim below was run against the backends in `docker/docker-compose.yml`.

- **Stock Postgres 16 ships zero procedures.** `pg_proc` holds 3125 `f`, 157
  `a`, 15 `w` and no `p`. Every procedure is user-defined, so `CALL ⌶` answers
  with names only where somebody wrote one.
- **A procedure cannot appear in an expression.** `SELECT archive_old_reports(current_date)`
  → `ERROR: archive_old_reports(date) is a procedure. HINT: To call a procedure,
  use CALL.` The existing `prokind IN ('f', 'a', 'w')` filter is load-bearing:
  adding `'p'` to it without a matching filter downstream would replace a
  missing answer with a wrong one.
- **`pg_get_function_result` is NULL for a procedure.** The current row mapper
  does `str(row[3])`, which would render the string `'None'` into the detail
  column.
- **A sequence is queryable.** `SELECT * FROM auth_user_id_seq` returns
  `last_value | log_cnt | is_called`. Sequences in a `FROM` list would be noise
  rather than an error — but the demo database has 19 of them, all `bigserial`
  machinery, and 19 dead entries in the commonest caret in the language is a
  real cost.
- **A sequence name inside the literal needs its identifier quotes.**
  `nextval('billing."MonthlyTotals_id_seq"')` works;
  `nextval('billing.MonthlyTotals_id_seq')` fails with
  `relation "billing.monthlytotals_id_seq" does not exist`. The demo fixture has
  exactly such a sequence.
- **ClickHouse has no `CALL`.** Syntax error, whose message lists every form it
  accepts. (That list also shows ANSI's `UPDATE` already overstates ClickHouse,
  which is pre-existing and not fixed here.)
- **Trino has `CALL`.** `CALL foo()` fails on schema resolution, not on parsing.
- **Trino has no sequences.** `DROP SEQUENCE foo` → `mismatched input
  'SEQUENCE'. Expecting: 'CATALOG', 'FUNCTION', 'MATERIALIZED', 'ROLE',
  'SCHEMA', 'TABLE', 'VIEW'`.
- **Trino reports no procedures.** `system.jdbc.procedures` is empty.

### What the code already has

- `Table.kind` exists and is documented as "normalised by the dialect row
  mappers: table, view, materialized view, foreign table…". Sequences need no
  new field, only relkind `S` let through.
- `_enclosing_call(tokens, index)` in `analyse.py` already answers "the name of
  the function whose argument list encloses this token" — written for `CAST`.
- `_inside_a_literal` already builds a request whose span covers the whole
  literal and whose candidates render themselves with `literal=True`. The value
  suggestions take that path; sequences take the same one.
- `_table_candidate(table, qualify=...)` already writes a relation qualified
  when a bare reference would not reach it, from slice 2.
- `_half_written_clauses` already answers a head shared by two phrases, as long
  as the head is not a phrase in its own right. `DROP` and `ALTER` qualify.

### A misuse this fixes

ClickHouse's `functions` query sets `result='aggregate' if row[1] else 'function'`,
so `count()` renders as `count() -> aggregate` — a kind word in the return-type
field, because there was nowhere else to put it. Trino's `SHOW FUNCTIONS`
returns a real kind column at `row[3]` (`scalar`) that nothing reads.
`Function.kind` is less a new field than the field this misuse has been standing
in for.

### Decisions taken during brainstorming

1. **The whole gap in one slice**, because both halves are the same filter over
   two records, and splitting means designing it twice with the second half
   inheriting a shape chosen without it in view.
2. **`nextval` is dialect data**, not signature inference. Inferring from the
   declared argument type would work — `nextval` takes `regclass` — but
   `regclass` means *any* relation, so it would offer all forty tables where
   only a sequence is valid.
3. **New kinds, narrowed old ones.** `Kind.SEQUENCE` and `Kind.PROCEDURE` join
   the enum, following `Kind.CTE`'s precedent of being distinct so a front end
   can say so. `Kind.TABLE` narrows to mean "a relation you can query" and
   `Kind.FUNCTION` keeps excluding procedures.

### Rejected approaches

- **Signature inference for `nextval`.** See decision 2. Self-maintaining and
  wrong.
- **Reusing `Kind.TABLE` and `Kind.FUNCTION` with a `detail` word.** No enum
  change, no LSP mapping, no conformance churn — but a front end colouring by
  kind would then claim a sequence is a table, and the filter would still have
  to exist, merely invisibly.
- **A general `Clause.relation_kinds` filter**, so `DROP VIEW`, `DROP INDEX` and
  `DROP MATERIALIZED VIEW` fall out later. Machinery with exactly one user in
  this slice. See §7.
- **A positive whitelist of queryable relation kinds.** See §4.
- **`CALL` in Postgres only.** Trino has it, and a form real in two of the three
  shipped backends belongs in the baseline they share.

---

## 2. Scope

### In

- `Function.kind` and a nullable `Function.result`; `Kind.PROCEDURE`.
- Sequences reaching the catalog through relkind `S`; `Kind.SEQUENCE`.
- Kind-filtered reads in `resolve`, one place, both records.
- A `CALL` clause in ANSI, subtracted by ClickHouse; `DROP SEQUENCE` and
  `ALTER SEQUENCE` in Postgres.
- `nextval('⌶`, `currval('⌶`, `setval('⌶` — declared per dialect.
- A procedure in the Postgres seed, so integration tests have one to find.

### Out, deliberately

- **`CREATE PROCEDURE`, `CREATE SEQUENCE`.** DDL authoring, which is gap 2.
- **Procedure argument lists.** `CALL proc(⌶` parks the caret inside the
  parentheses and offers nothing. There is no `FROM`, so no column is in scope,
  and offering one would be a wrong answer.
- **`Kind.VIEW`, `Kind.INDEX`.** `DROP VIEW ⌶` still answers nothing.
- **A dotted path inside the literal.** `nextval('billing.⌶` reads as a prefix
  containing a dot, not as a qualifier. The unqualified prefix reaches those
  sequences anyway, because `relation_search` does.
- **`pg_get_serial_sequence('table', 'column')`.** Its first argument names a
  table and its second a column, which needs argument positions past zero.

### Non-goals

- Introspecting ClickHouse's or Trino's procedure sets. Neither reports any.
- Changing what `SELECT ⌶` or `FROM ⌶` offer. Both must be byte-identical
  after this slice; that is what the filters are for.

---

## 3. Two records gain a kind

```python
@dataclass(frozen=True, slots=True)
class Function:
    schema: str | None
    name: str
    args: str | None
    result: str | None
    kind: str = 'function'
```

`kind` is `function` | `aggregate` | `window` | `procedure`, defaulting to
`function` so every existing construction keeps working and a backend that
cannot distinguish says the safe thing.

`result` becomes `str | None`. None means "the backend does not say", exactly as
it already does for `args` — and it is what Postgres actually returns for a
procedure. The rendered detail drops its arrow rather than showing an empty one:
`count(…)` where the type is unknown, `now() -> timestamptz` where it is not.

Row mappers, by dialect:

| dialect | source | mapping |
|---|---|---|
| Postgres | `pg_proc.prokind` | `f`→function, `a`→aggregate, `w`→window, `p`→procedure; `result` NULL→None |
| ClickHouse | `system.functions.is_aggregate` | true→aggregate, false→function; `result` becomes None, vacating the field it was misusing |
| Trino | `SHOW FUNCTIONS` row 3 | `scalar`→function, `aggregate`, `window`; `result` from row 1 as today |

The Postgres query's `prokind IN ('f', 'a', 'w')` becomes
`prokind IN ('f', 'a', 'w', 'p')`. That change is only safe once §4's filter
exists, so the two land in the same task.

`Table.kind` needs no change. `_RELKIND` gains `'S': 'sequence'`, and the
`relkind IN (…)` lists in both `tables` and `relation_search` gain `'S'`.

---

## 4. Where the filter lives

`resolve.py`, once, for both records — never in an adapter, which is the
documented home of every other capability degradation:

- `Kind.FUNCTION` → every function whose kind is not `procedure`.
- `Kind.PROCEDURE` → only those whose kind is `procedure`.
- `Kind.TABLE` → every relation whose kind is not `sequence`.
- `Kind.SEQUENCE` → only those whose kind is `sequence`.

**Both relation tests are negative, deliberately.** A positive whitelist of
queryable kinds would empty ClickHouse's `FROM` clause: its `Table.kind` is the
storage engine name — `mergetree`, `log`, `distributed` — not a relational
category, and no fixed list of ours could enumerate the engines a ClickHouse
installation has. Only Postgres emits `sequence`, so "not a sequence" is the
only rule that is true for every backend, including ones this package has never
heard of. The same reasoning gives functions a negative test, though there the
positive list would have been tractable; keeping the two rules the same shape is
worth more than the marginal precision.

Sequences enter through **both** `tables` and `relation_search`, so
`nextval('bil⌶` reaches one outside the search path and writes it qualified.
That is slice 2's work inherited rather than repeated.

---

## 5. `CALL`, and who gets it

An ANSI clause, since Postgres and Trino both have it:

```python
Clause(name='CALL', suggests=(Kind.PROCEDURE, Kind.SCHEMA))
```

plus `CALL` in `STATEMENT_START`. ClickHouse overrides `statement_start` to
subtract it. That is the first time a dialect here states what it *lacks*, and
the alternative is offering a word whose statement the server rejects outright.

No `followed_by`. A procedure call ends the statement, and `_clause_kinds`
already reads an empty continuation list as "nothing follows this clause" — the
same rule that stops `RETURNING` and `FETCH` proposing a successor.

`_qualified_kinds` needs one narrowing. A clause suggesting something that is
not a relation keeps suggesting it past a dot, so `CALL billing.⌶` reads
`billing` as a schema and answers with the procedures in it — rather than the
columns and tables the namespace rule gives today, neither of which can be
called. The rule is keyed on the clause's own `suggests`, so it applies to
`DROP SEQUENCE public.⌶` for free and to nothing else.

`DROP SEQUENCE` and `ALTER SEQUENCE` live in `postgres.py` rather than ANSI:
Trino's parser lists what `DROP` accepts and `SEQUENCE` is not among it, and
ClickHouse has none either. A form only one shipped backend implements belongs
to that one.

```python
Clause(name='DROP SEQUENCE', suggests=(Kind.SEQUENCE, Kind.SCHEMA), followed_by=('CASCADE', 'RESTRICT')),
Clause(name='ALTER SEQUENCE', suggests=(Kind.SEQUENCE, Kind.SCHEMA), followed_by=('RENAME TO', 'OWNED BY')),
```

Two-word continuations, for the reason last slice recorded: a bare `RENAME`
among them would make `('RENAME',)` a phrase in its own right, and
`_half_written_clauses` skips a head that is already a phrase.

Both join Postgres's `statement_start`, which is not optional — `DialectConformance.structure`
already reports a `statement_start` phrase that no clause declares, and the
converse mistake, a clause declared and never started, is what would make them
dead. Postgres therefore overrides `statement_start` as well as extending its
clause model, which is the first time it has needed to.

`DROP ⌶` answering both `TABLE` and `SEQUENCE`, and `ALTER ⌶` likewise, falls
out of `_half_written_clauses` unchanged — two phrases sharing a head that is
not itself a phrase is precisely the case it handles. The `ALTER TABLE` trap
from last slice does not apply here, because neither `DROP` nor `ALTER` becomes
a clause name.

---

## 6. `nextval('⌶` — a literal inside a call

### The dialect record

```python
@dataclass(frozen=True, slots=True)
class LiteralArgument:
    """What the first string argument of a call names."""

    function: str
    suggests: tuple[Kind, ...]
```

on `Dialect.literal_arguments: tuple[LiteralArgument, ...] = ()`. Postgres
declares three:

```python
literal_arguments=(
    LiteralArgument(function='nextval', suggests=(Kind.SEQUENCE,)),
    LiteralArgument(function='currval', suggests=(Kind.SEQUENCE,)),
    LiteralArgument(function='setval', suggests=(Kind.SEQUENCE,)),
)
```

A record rather than a bare tuple of names, so a second family — a table name in
a literal — is another entry rather than another field.

### The seam

`derive_request` says *where*: the caret is inside a string literal that is
argument zero of a call to a function the dialect declared. `_enclosing_call`
answers the call name; argument zero is checked by looking for a comma at that
depth before the literal. resolve says *what*: the declared kinds.

Argument zero only. `setval('seq', 1)` names its sequence first, and so does
everything declared here; a caret in a later argument keeps the silence it has
today.

### What is inserted

The candidate replaces the whole literal, `literal=True`, on the path
`WHERE type = 'clic⌶` already takes. Its text is
`'billing."MonthlyTotals_id_seq"'` — `quote_if_needed` on the identifier, the
schema prefix when the relation search found it outside the path, then the whole
thing wrapped in single quotes with any interior quote doubled. The server
refuses the unquoted spelling, so this is correctness rather than tidiness.

`match_text` and `label` carry the bare name, so typing `mon` finds it by the
word-prefix tier rather than the substring tier, and the popup shows a name
rather than a quoted string.

---

## 7. Why `DROP VIEW` is still not here

`DROP VIEW ⌶` wants relations of kind `view`, which is the same filter one notch
finer. It is not built because the mechanism it needs is a *choice*, not an
omission: either a third and fourth `Kind`, or a relation-kind list on `Clause`.
Two kinds is what this slice has users for, and picking between those two shapes
with one hypothetical consumer is how a field gets designed wrong.

`Table.kind` already carries `view`, `materialized view` and `foreign table`, so
the catalog half is done. The entry in `docs/gaps.md` gets updated to say that.

---

## 8. Testing

### Unit

A new `tests/test_procedures.py` and `tests/test_sequences.py`, plus additions
to the existing files whose subject changes:

- `CALL ⌶` offers procedures and not functions; `SELECT ⌶` offers functions and
  not procedures. Two assertions, one fixture, and between them they pin the
  filter in both directions.
- `FROM ⌶` is unchanged by sequences existing in the snapshot. This is the
  regression test the whole of §4 exists to pass.
- `nextval('⌶` offers sequences; `nextval('` inside a dialect that declares no
  literal arguments offers nothing; a caret in `setval('seq', ⌶` is not the
  literal position.
- The mixed-case sequence renders as `'billing."MonthlyTotals_id_seq"'`.
- `DROP ⌶` offers `TABLE` and `SEQUENCE`; `ALTER ⌶` likewise. Both are
  guarding against last slice's phrase-head trap recurring.
- ClickHouse's empty editor does not offer `CALL`.
- `MemoryCatalog` grows sequences and procedures, so every one of the above runs
  without a database.

### Conformance

The fixture in `DialectConformance.catalog` gains a sequence — via the
`table_kinds` argument `MemoryCatalog` already takes — and a procedure among its
functions. That is what lets the corpus make propositions about either.

Three behavioural cases, each built from what the dialect says about itself:

1. **A relation position never offers a sequence.** Runs for every dialect,
   including ones that have none, because the fixture always has one. This is
   §4's entire purpose pinned in the shipped corpus rather than in our own
   tests — a third-party dialect that reintroduces the leak finds out.
2. **A caret inside a declared literal argument offers what it declares.** Built
   by taking the dialect's first `LiteralArgument` and writing `SELECT fn('`.
   Skipped, not failed, by a dialect declaring none — the same bargain
   `parameter()` already makes for `?`.
3. **A clause suggesting `Kind.PROCEDURE` offers procedures and not functions.**
   Built by finding that clause rather than by naming `CALL`, so a dialect
   spelling it differently is still covered. Skipped where no clause suggests
   it.

One structure check: a `LiteralArgument` must name a single bare word and
suggest at least one kind. A name with a dot, a space or parentheses can never
equal what `_enclosing_call` returns, and an empty `suggests` can never produce
a candidate — both are silent, which is the only kind of mistake `structure`
exists to catch.

Deliberately **not** checked: that a clause suggesting these kinds is reachable.
Reachability would mean "in `statement_start`, or in some clause's
`followed_by`, or declaring `follows`" — and `PARTITION BY` satisfies none of
those today while being perfectly correct, since it is recognised when typed
rather than offered. The check would report a false positive on the dialect that
ships with this library, which is disqualifying.

### Golden corpus

`CALL ⌶` and `SELECT nextval('⌶` join `tests/corpus/cases.py`, which asserts
requests and never touches a server.

**Corrected twice during planning**, and neither correction is a detail.

First: this section claimed both were fit for the acceptance sweep. `CALL` is
not — `EXPLAIN CALL probe_proc()` is `syntax error at or near "CALL"`, exactly
as `EXPLAIN DROP TABLE t` was last slice.

Second, and the one that actually decides this: **the acceptance sweep cannot
reach a caret inside a literal at all.** Its `carets()` generator stops at each
end of each space and at the end of the statement, and a string literal has
neither. `SELECT nextval('auth_user_id_seq')` would be swept at offsets 6, 7 and
the end — none of them the position under test. The entry would pass while
proving nothing, which is worse than no entry.

So **nothing joins the acceptance `CORPUS`.** The literal position gets a direct
integration test instead, of the shape
`test_postgres_reaches_a_relation_off_the_search_path` already uses: complete,
apply, then have the server plan the result. That is the only thing that can
prove `nextval('billing."MonthlyTotals_id_seq"')` runs where the unquoted
spelling does not — which is the fact this whole half of the design rests on.

`misplaced()`'s docstring is still extended to name `CALL` beside the DDL, so
the next person to reach for the sweep meets the rule rather than rediscovering
it.

`Kind.PROCEDURE` joins `UNJUDGEABLE` in the same file, for the identical reason
`Kind.FUNCTION` is there: a procedure arrives as `proc()` with the caret between
the parentheses, which is illegal SQL on purpose and which the harness would
otherwise report as a misplaced token.

### Integration

`docker/postgres/01-schema.sql` gains a procedure, because stock Postgres 16
ships none and the assertion would otherwise be against an empty list. It goes
in `billing` as well as `public`, so the schema-qualified path has something to
find.

Integration tests: `CALL ⌶` finds the seeded procedure against a live server;
`SELECT ⌶` does not; `nextval('⌶` finds a named sequence the seed's `bigserial`
columns create; `FROM ⌶` finds no sequence at all. Named rather than counted —
a count would break the next time a table joins the seed, and would be asserting
the fixture rather than the behaviour.

### LSP

`Kind.PROCEDURE` → `CompletionItemKind.Method`, `Kind.SEQUENCE` →
`CompletionItemKind.Reference`. The guard test that fails when a kind is added
and not mapped is what will force this, and it is right that it does.

Neither is a natural fit, because LSP has no sequence and no procedure. The
choice is made on *visual distinctness*, which is what the mapping is for: every
closer name is taken by something the new kind would be confused with — `Class`
is a table, `Function` is a function, `Value` is a literal. `Method` and
`Reference` are the least-wrong pair that renders differently from all three.

---

## 9. Documentation

- `docs/gaps.md`: gap 1 moves to "Closed since this list was written", recording
  what the entry got right — the sequence half being cheaper, the `CALL` clause
  being cheap once statement forms existed — and what it did not say: that
  `prokind IN ('f','a','w')` was already load-bearing, and that stock Postgres
  ships no procedures at all. The `DROP VIEW` note from §7 goes in the remaining
  list.
- `CHANGELOG.md`: the new kinds, the `Function` fields, and the ClickHouse
  `result` change, which is visible to anyone reading the detail column. The
  Unreleased section already says of `DROP VIEW` that "filtering by relation
  kind needs a set of kinds per clause"; that sentence is now half-wrong and is
  corrected rather than left standing — a *kind per clause* is what §7 defers,
  but the filter itself is built here.
- `README.md`: checked, and needs nothing. It names `Kind.COLUMN`, `Kind.TABLE`,
  `Kind.SCHEMA`, `Kind.FUNCTION` and `Kind.VALUE` in worked examples and
  nowhere enumerates the enum, so no list there can fall behind.

---

## 10. Open questions carried forward

- **Whether `Clause` should carry a relation-kind list.** §7. Deferred until
  there is a second consumer, which is `DROP VIEW`.
- **Bound parameter names**, from the placeholders slice, still unbuilt.
- **Argument positions past zero**, which `pg_get_serial_sequence` would need.
