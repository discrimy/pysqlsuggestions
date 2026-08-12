# Gaps

What this engine does not do yet, and what each omission would cost.

DBeaver is the yardstick throughout, because it is the most widely used
schema-aware SQL completion there is and its users are the ones with formed
expectations. Its docs and issue tracker are cited where a claim about its
behaviour needs one. Nothing here is a promise; it is a list with reasons, so a
later decision to build or refuse any of it starts from something.

Ordered by value per unit of work, not by size.

---

## 1. Relations outside the default namespace

`FROM ord⌶` finds nothing when `orders` lives in a schema outside the search
path. `resolve._unqualified` calls `reader.tables(None)`, which is the default
namespace and only that, so an unqualified prefix can only ever reach what the
search path already covers.

Columns do not have this problem: `SupportsColumnSearch` exists precisely
because `SELECT ema⌶` has no relation to look inside, and it returns the
relation alongside the column so the FROM can be written. The same shape applied
to relations — a prefix-scoped, server-bounded search across every visible
schema — would answer this, and it would come back with the schema, so the
insertion can qualify.

Two hazards, both already understood elsewhere in this codebase. The result is
prefix-dependent and so does not cache, which is why `SupportsColumnSearch` is a
capability rather than a `Catalog` method. And a truncation happens before
ranking sees the rows, so the adapter has to order by match quality, not storage
order — `ports.py` carries that argument in full.

## 2. Procedures and sequences

`CALL ⌶` and `nextval('⌶')` are ordinary SQL and answer with nothing. The
catalog knows functions and not much else: `Catalog.functions` is one method and
`Function` has no notion of a procedure that returns nothing, a sequence, or a
package.

A sequence is the cheaper half — it is a name in a namespace, so it is a `Table`
with a different `kind` in everything but spelling, and `pg_class` already
reports it. A procedure needs a `Kind` and a position: `CALL` is a statement
form this engine does not have, which makes it a dependency of gap 3 rather than
independent of it.

Trino has neither, ClickHouse has no sequences, so this is largely a Postgres
feature and should be built as one.

## 3. Statement forms beyond DML

`statement_start` is `SELECT`, `WITH`, `INSERT INTO`, `UPDATE`, `DELETE FROM`
(`dialects/ansi.py:195`). Everything else — `CREATE`, `ALTER`, `DROP`, `GRANT`,
`EXPLAIN`, `CALL` — is unrecognised, and an unrecognised first word means no
clause, which means the whole statement completes as if it were an expression.
DBeaver added semantic analysis for `CREATE`, `ALTER` and `DROP` in 24.2.

`DROP` and `EXPLAIN` are nearly free: `DROP TABLE ⌶` wants relations, which is
the `FROM` position under a different name, and `EXPLAIN ⌶` wants a statement,
which is the empty-editor position. `CREATE TABLE` is the real work, and it
wants type names — `dialect.types` already ships them for cast positions, so the
candidates exist and only the clause model is missing.

Worth being deliberate about how far this goes. DDL completion shades into DDL
authoring, and a completion engine that knows `ALTER TABLE … ADD CONSTRAINT`
well enough to be useful is a different size of thing than one that knows
`SELECT`.

## 4. History ranking

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
  `engine/joins.py` and `ports.py`; the answer is gap 4, not a heuristic.
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
