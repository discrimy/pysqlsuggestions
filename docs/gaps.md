# Gaps

What this engine does not do yet, and what each omission would cost.

DBeaver is the yardstick throughout, because it is the most widely used
schema-aware SQL completion there is and its users are the ones with formed
expectations. Its docs and issue tracker are cited where a claim about its
behaviour needs one. Nothing here is a promise; it is a list with reasons, so a
later decision to build or refuse any of it starts from something.

Ordered by value per unit of work, not by size.

---

## 1. Procedures and sequences

`CALL ⌶` and `nextval('⌶')` are ordinary SQL and answer with nothing. The
catalog knows functions and not much else: `Catalog.functions` is one method and
`Function` has no notion of a procedure that returns nothing, a sequence, or a
package.

`CALL ⌶` now answers with nothing deliberately rather than by omission — it is
an unrecognised statement form, and those are silent. That removes the wrong
answer and leaves the missing one.

A sequence is the cheaper half — it is a name in a namespace, so it is a `Table`
with a different `kind` in everything but spelling, and `pg_class` already
reports it. A procedure needs two things this engine still lacks: a `Kind` of
its own, and a `CALL` clause to put it in. The clause is cheap now that the
statement-form machinery exists — `DROP TABLE` is the same shape — so this is no
longer blocked on anything but the catalog work.

Trino has neither, ClickHouse has no sequences, so this is largely a Postgres
feature and should be built as one.

## 2. CREATE TABLE

`CREATE TABLE t (id ⌶` has nothing to say. `DROP TABLE`, `TRUNCATE`,
`ALTER TABLE` and `EXPLAIN` are modelled now, and every other unrecognised form
answers with nothing rather than with the words a statement may begin with.
DBeaver added semantic analysis for `CREATE`, `ALTER` and `DROP` in 24.2.

What is missing is a clause model for a parenthesised definition list: where a
type belongs rather than a name, and the words that may follow one. The
candidates already exist — `dialect.types` ships them for cast positions — so
this is the clause model and nothing else.

Worth being deliberate about how far this goes. DDL completion shades into DDL
authoring, and a completion engine that knows `ALTER TABLE … ADD CONSTRAINT`
well enough to be useful is a different size of thing than one that knows
`SELECT`. `ALTER TABLE` stops at `ADD COLUMN` and `RENAME TO` here for exactly
that reason — and because a bare `DROP` among its continuations would make
`DROP ⌶` stop answering `TABLE`, the way `ON ⌶` does not answer `CONFLICT`.

## 3. History ranking

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
- **Async.** Every catalog call is synchronous. An LSP server that blocks its
  event loop on a slow introspection query is a real cost now that `lsp/`
  exists.

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
  `engine/joins.py` and `ports.py`; the answer is gap 3, not a heuristic.
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
