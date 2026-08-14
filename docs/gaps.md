# Gaps

What this engine does not do yet, and what each omission would cost.

DBeaver is the yardstick throughout, because it is the most widely used
schema-aware SQL completion there is and its users are the ones with formed
expectations. Its docs and issue tracker are cited where a claim about its
behaviour needs one. Nothing here is a promise; it is a list with reasons, so a
later decision to build or refuse any of it starts from something.

Ordered by value per unit of work, not by size.

For `SELECT` specifically there is now a second, finer list that maintains
itself: `tests/grammar/` measures every position the official PostgreSQL
synopsis names and prints a burn-down on each run. This document stays the place
for decisions with reasons; that suite is the place for coverage, and neither
restates the other.

---

## 1. History ranking

Named in the v0.1 design as out of scope and still the honest answer to the
problem `engine/joins.py` refuses to solve by inference. A join mined from
queries that actually ran is evidence; `<singular>_id` matching `<table>.id` is
a guess that is wrong often enough to matter, and a wrong join condition is
valid SQL that silently returns the wrong rows.

It would also give ClickHouse and Trino join proposals, which declared
constraints never will, and it is the only ranking signal that improves without
anybody maintaining it.

The shape is a capability — a source of observed statements — and the hazards
are the interesting part: whose history, kept where, and readable by which role.
That last question is the same one `Cache` already answers by putting `role`
first in its key, and for the same reason.

---

## Closed since this list was written

Kept rather than deleted, because a list whose entries only ever disappear tells
a later reader nothing about what was decided.

- **CREATE TABLE.** `CREATE TABLE t (id ⌶` offers types, then the constraints
  that may follow one, and `TABLE t` is a statement form at last.

  This entry called it "the clause model and nothing else". It was not: a
  definition list has no opening word, so `opens_a_group` could not carry it and
  the alternation of name-then-type needed a position rule in `engine/`. What
  the entry did get right is that the candidates already existed —
  `dialect.types` answers the type position with no new plumbing at all.

  It also predicted why `TABLE` was blocked and what would unblock it, and both
  held: `clause_at` ranks by (end offset, word count), so modelling
  `CREATE TABLE` first is what stops the bare form capturing the definition
  list. `DROP TABLE` and `ALTER TABLE` were already relying on that same
  tiebreak — but `TRUNCATE` was not, because one word ends *earlier* than the
  `TABLE` after it rather than tying with it, so `TRUNCATE TABLE users ⌶` lost
  its `CASCADE` and `RESTRICT` until it got a two-word clause of its own.

  Which words each backend takes was measured, not read off the standard.
  ClickHouse rejects `TABLE t` outright and Trino rejects `ONLY`; of the column
  constraints only `NOT NULL` is common to all three, and Trino takes nothing
  else. The advice about being deliberate stands, and is why `CREATE VIEW`,
  `CREATE INDEX` and `CREATE TABLE … AS SELECT` are still not here.

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

- **Procedures and sequences.** `CALL ⌶` offers procedures, `nextval('⌶` offers
  sequences, and `DROP SEQUENCE ⌶` and `ALTER SEQUENCE ⌶` offer them too. Both
  halves are one filter over two records — the catalog reports a subtype, and
  the position admits only some subtypes — which is why they were built
  together rather than in sequence.

  This entry called the sequence half "the cheaper" one and it was not. What it
  did not say is that `prokind IN ('f', 'a', 'w')` was already load-bearing: a
  procedure in an expression is refused by the server outright, so widening
  that filter without a matching one downstream would have traded a missing
  answer for a wrong one. Nor that stock Postgres ships **no procedures at
  all**, which makes `CALL ⌶` invisible until somebody writes one — the seed
  grew two so the integration tests assert against something.

  The identifier keeps its quotes inside the string:
  `nextval('billing."MonthlyTotals_id_seq"')` runs and the bare spelling is
  refused, because the server reads that literal as a `regclass` rather than as
  text.

  ClickHouse now says what it *lacks* for the first time — it has no `CALL`, and
  inheriting one from ANSI would have offered a word its parser rejects.

- **DROP, TRUNCATE, ALTER TABLE and EXPLAIN.** They name a relation, or a
  statement, and now offer one. Every form still unmodelled — `GRANT`, `CALL`,
  `VACUUM`, `COMMENT` — answers with nothing.

  This entry said an unrecognised statement "completes as if it were an
  expression". It was worse than that: the position offered the words a
  statement may *begin* with, so `DROP TABLE ⌶` proposed `SELECT` and accepting
  wrote `DROP TABLE SELECT`. `EXPLAIN` was the opposite case — already correct,
  and only because nothing recognised it, which is why it is a clause now.

- **Relations outside the default namespace.** `FROM ord⌶` reaches a relation in
  any visible schema and writes it qualified. Postgres and ClickHouse ship the
  query; Trino does not, at 179ms per catalog.

  This entry claimed columns did not have the problem. They did: `column_search`
  filtered `pg_table_is_visible`, so `SELECT ema⌶` was as blind as `FROM ord⌶`,
  and the FROM clause a searched column wrote dropped its schema. Both are fixed
  here, and they had to be fixed together — lifting the filter alone would have
  turned a missing answer into `FROM invoices`, which the server refuses.

- **Star expansion.** `SELECT *⌶` offers the column list the star stands for, as
  one accept. A bare star expands qualified once more than one relation is in
  scope — `users` and `orders` both have `id`, and the unqualified list is a
  statement the server refuses. Cost one `Kind`, and one span per candidate:
  the same caret offers `FROM`, which inserts beside the star where the
  expansion replaces it.
- **Parameters and placeholders.** A caret inside `:name`, `$1`, `?` or a
  braced form suggests nothing, and one past a parameter reads it as a finished
  operand. Spelled per dialect in `Syntax`; Postgres deliberately excludes `?`,
  which is its JSONB existence operator. Bound parameter *names* are still not
  offered — that needs an argument on both entry points and is a feature of its
  own.

## Already named elsewhere

Carried from the v0.1 design's out-of-scope list and the README's status
paragraph, repeated here so the whole list lives in one place:

- **Physical layout ranking.** `Table.rows` is fetched and stored; nothing
  scores on it. A relation with millions of rows and one with dozens are
  currently indistinguishable in a ranked list.
- **Per-role availability.** The `Availability` idea — what the connected role
  may actually read — exists in the design and nowhere in the code. Postgres
  statistics are already role-filtered by the server, which covers value
  suggestions and nothing else.
- **Documentation at the caret.** No comment, column description or function
  signature is offered as hover text. `Function.args` is fetched and used only
  to decide where the caret lands.
- **Syntax extensions and the report macro**, both still exactly where the
  design left them.
- **Async.** Every catalog call is synchronous, and that is a decision rather
  than a gap — the port is documented, and the bridge for async callers is to
  pre-fetch into a `MemoryCatalog`.

  This entry used to say that a synchronous call "blocks its event loop on a
  slow introspection query", and named async as the fix. The blocking was real
  and the fix was wrong: pygls dispatches a thread-marked handler to a pool, and
  the completion handler simply was not marked. It is now, and the state that
  concurrency exposed is locked. Nothing here became asynchronous.

## Not gaps

Things DBeaver has that this should not grow, recorded so a later reader does
not mistake them for oversights:

- **Word completion from the open document** (DBeaver's Hippie engine). Word
  similarity against whatever text is in the file is noise in an engine whose
  claim is that it knows the schema, and it degrades the ranked list precisely
  when the schema knowledge is working.
- **A second, older engine kept alongside the new one.** DBeaver ships Legacy
  and Combined next to Semantic because it could not retire the first. That is
  a migration artefact, not a feature.
- **Join conditions inferred from column names.** Argued at length in
  `engine/joins.py` and `ports.py`; the answer is gap 1, not a heuristic.
- **AI anything.** Query execution, formatting, linting and full validation
  remain non-goals, and generating SQL from prose is further outside them than
  any of those.

## What the comparison did not find

No gap in the analysis half. Statement-wide scope, subqueries, CTEs, set
operations and per-branch clause state are all handled here and are where
DBeaver's own Legacy engine is documented as weak. Three things have no
counterpart there at all: value literals drawn from planner statistics,
composite foreign keys in join proposals — DBeaver's are non-compound only — and
a column offered before any `FROM` exists writing the `FROM` clause with it.

Worth stating because prioritisation cuts both ways: the list above is what to
build, and this paragraph is what not to trade away to build it.

## Sources

- [SQL Assist and Auto Complete](https://dbeaver.com/docs/dbeaver/SQL-Assist-and-Auto-Complete/) — engines, Hippie, star expansion.
- [DBeaver Ultimate 24.2](https://dbeaver.com/dbeaver-ultimate-24-2/) — semantic analysis for CREATE, ALTER and DROP.
- [dbeaver#37089](https://github.com/dbeaver/dbeaver/issues/37089), [dbeaver#35892](https://github.com/dbeaver/dbeaver/issues/35892) — foreign-key join proposals and their limits.
