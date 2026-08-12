# Changelog

Grouped by what changes for someone using the library rather than by commit.
The engine's whole job is what it offers at a caret, so that is what this
records: the positions where it now answers differently.

## Unreleased

## 0.3.0

Two new surfaces, and six changes to what the engine answers.

The surfaces are a language server and a VS Code extension, so the library is
usable now without writing an editor integration first.

The rest is one theme seen from six positions: a caret that used to answer with
something plausible and wrong now answers correctly, or not at all. A caret
inside `:name` no longer offers column names. A statement form the engine does
not model no longer proposes `SELECT`. A procedure is not offered where the
server refuses one, a sequence is not offered a `FROM` list, and a column
reference is no longer written in a form the server calls ambiguous.

**Breaking, for callers constructing these types by hand.**
`Candidate.qualifier` is `tuple[str, ...]` rather than `str | None`;
`Function.result` may be `None`; `Function` carries a `kind`. `Suggestion`, the
`Catalog` protocol and every capability protocol are unchanged.

### A column reference that resolves

Two relations with the same name in different schemas can both be in scope —
`FROM public.invoices, billing.invoices` is legal, and Postgres aliases the
second internally. Every column reference the engine wrote for them was
`invoices.amount`, which the server refuses: `table reference "invoices" is
ambiguous`. Each now carries its relation's whole path.

`SELECT *` over the two used to expand to `invoices.amount, invoices.id,
invoices.amount, invoices.period` — ambiguous, and naming `amount` twice.

Before any `FROM` exists, a column that several schemas have is now offered once
per schema instead of once in total. Previously the others were unreachable at
that caret however much you typed, because ranking dedupes on the text to be
inserted and all of them rendered alike. In a database with a schema per tenant
this makes that list longer; the schema on the search path sorts first.

**Nothing changes without a collision.** A single-schema database gets exactly
what it got before, and that is asserted rather than assumed.

`Candidate.qualifier` is now `tuple[str, ...]` rather than `str | None` — a path
is not a name, and a dotted string in the old field would have been quoted as
one name containing a dot. Only callers constructing a `Candidate` by hand are
affected; `Suggestion` is unchanged, since the qualifier is already part of its
`text`.

### Procedures and sequences

`CALL ⌶` offers procedures. `SELECT ⌶` does not — a procedure in an expression
is refused by the server, so this is a wrong answer kept out rather than a
missing one added. `CALL billing.⌶` still means a procedure, where the namespace
rule would have answered with columns and tables.

`nextval('⌶`, `currval('⌶` and `setval('⌶` offer sequences, written into the
literal with their identifier quotes intact —
`nextval('billing."MonthlyTotals_id_seq"')`, because the server parses that
string as a `regclass` and refuses the bare spelling. Which functions name a
sequence is dialect data, so a dialect can declare its own.

`DROP SEQUENCE ⌶` and `ALTER SEQUENCE ⌶` offer sequences, and `DROP ⌶` now
answers `TABLE` and `SEQUENCE`.

**`SELECT ⌶` and `FROM ⌶` are unchanged**, which is the point of most of the
work: sequences reach the catalog now, and a schema has one per serial column.

`Function` carries a `kind` — function, aggregate, window or procedure — and a
`result` that may be `None`. ClickHouse used to report `count() -> aggregate`,
a kind in the return-type field for want of anywhere else; it now reports
`count()  aggregate` and no return type, which is the truth about what
`system.functions` knows. Postgres marks its aggregates and window functions
for the first time.

ClickHouse no longer offers `CALL`, which its parser rejects.

### Statements that are not queries

`DROP TABLE ⌶` used to offer `SELECT`, `WITH` and `INSERT INTO` — the words a
statement may *begin* with, inside a statement that had already begun. Accepting
one wrote `DROP TABLE SELECT`.

`DROP TABLE`, `TRUNCATE` and `ALTER TABLE` now offer relations, and are offered
themselves where a statement may begin. `DROP ⌶` offers `TABLE`. `EXPLAIN` takes
the statements a planner accepts — not `DROP`, which is a syntax error.

**Every other unrecognised form now answers with nothing.** `GRANT`, `VACUUM`,
`COMMENT`, `SET`, `BEGIN` and anything a third-party dialect has not modelled
are silent where they used to propose `SELECT`. (`CALL` was on this list and is
modelled now — see *Procedures and sequences* above.) A half-typed keyword is
not an unrecognised form: `SELEC⌶` still completes to `SELECT`, and so do an
empty editor, the position after a `;`, and the position after a comment.

`DROP VIEW` and `DROP INDEX` are among the silent ones. Offering them relations
would mean offering tables for `DROP VIEW`, which the server refuses. Filtering
by relation kind exists now — it is what keeps sequences out of `FROM` — but
only one notch coarse: `Kind.TABLE` means "not a sequence". Telling a view from
a table needs either a kind per relation type or a list of kinds per clause, and
that choice waits for a second consumer.

`ALTER TABLE` offers `ADD COLUMN` and `RENAME TO` and stops there. A bare `DROP`
among its continuations would make `DROP ⌶` stop answering `TABLE`, for the same
reason `ON ⌶` does not answer `CONFLICT` alone.

### A name is found wherever it lives, not only where the search path looks

`FROM invo⌶` found nothing when `invoices` lived in a schema the connection does
not default to. It now finds it and writes `billing.invoices`. Matching still
runs against the bare name, so typing `invo` — or `voic` — reaches it; the schema
is about what gets inserted, not what you have to type.

A relation you can write bare ranks above one that needs a schema prefix, by a
margin small enough that a better name match still wins.

The same gap had a second half nobody had noticed. `SELECT amou⌶` was equally
blind, because the column-search query filtered on visibility too — and the
`FROM` clause a searched column wrote dropped its schema, so lifting that filter
alone would have produced `FROM invoices`, which the server refuses. Both are
fixed: `SELECT amou⌶` now writes `SELECT invoices.amount FROM billing.invoices`.

Optional, and per backend, because the cost is what decides it:

| backend | ships it | measured against the docker fixture |
| --- | --- | --- |
| PostgreSQL | yes | 0.4–2.3 ms over 228 relations |
| ClickHouse | yes | 1.8–4.2 ms, and it reaches another database |
| Trino | no | 179 ms for *one* catalog's `information_schema` |

An empty prefix searches nothing: `FROM ⌶` is not a request for every relation
in the database.

**A limitation this entry recorded is now fixed.** It said two columns with the
same name, in same-named tables, in different schemas "still collapse to a
single suggestion", and that telling them apart "needs a qualifier that can hold
a path rather than a name". That qualifier exists — see *A column reference that
resolves* above.

It also understated the fault. The collapse was the visible half; the invisible
half was that the surviving suggestion is itself refused once both relations are
in scope, so the position was writing SQL that does not run rather than merely
offering one answer where two were due.

### `SELECT *` expands to the columns it stands for

Put the caret directly on a star and the top suggestion is the column list,
accepted in one go. One space further along is still the position that wants
`FROM`, and it still answers with `FROM`.

A bare star expands **qualified** as soon as more than one relation is in scope.
Two relations in a join very often share `id`, and the unqualified list is a
statement Postgres refuses with `column reference "id" is ambiguous`. One
relation expands bare. A star the author qualified — `u.*` — stays qualified
however few relations it covers, because the edit replaces the `u.` too.

Nothing is capped. A forty-column relation expands to forty columns, which is
what somebody who asked to expand a star asked for.

`Kind.EXPANSION` is new, so a front end colouring by kind should give it a
colour; `lsp/` reports it as a snippet. Reserved and mixed-case names are quoted
inside the list, so a column called `user` arrives as `d."user"`.

### Bound parameters are no longer read as column names

`WHERE id = :us⌶` used to propose `users` — or any column starting `us` — and
accepting one wrote valid SQL that ran a different query. The lexer now has a
token for a parameter. A caret inside one suggests nothing, and a caret past one
reads it as a finished operand, so `WHERE id = ? ⌶` offers `AND` rather than a
second column.

Spelled per dialect on `Syntax.placeholders`:

| dialect | spellings |
| --- | --- |
| ANSI | `?`, `:name` |
| PostgreSQL | `$1`, `:name` |
| Trino | `?` |
| ClickHouse | `{name:Type}` |

**PostgreSQL deliberately does not treat `?` as a parameter.** It is the JSONB
existence operator, and `data ? 'key'` is a predicate people write.

`${var}` is a templating convention rather than any backend's syntax, so it
ships as `TEMPLATE_PLACEHOLDER` wired into no dialect. A caller whose SQL is
templated composes it in:

```python
from dataclasses import replace
from pysqlsuggestions.dialects.base import TEMPLATE_PLACEHOLDER
from pysqlsuggestions.dialects.postgres import POSTGRES

syntax = replace(POSTGRES.syntax, placeholders=(*POSTGRES.syntax.placeholders, TEMPLATE_PLACEHOLDER))
DIALECT = replace(POSTGRES, syntax=syntax)
```

Bound parameter *names* are still not offered inside a placeholder. That needs
the caller to supply the binding, and it is a feature of its own.

### A VS Code extension

`editors/vscode/` drives the language server from an editor. It builds its own
Python environment from wheels shipped inside the VSIX — no network, and the
project's own environment is never touched — and needs Python 3.10+ on PATH.

PostgreSQL only, for anything that reads a schema. The other backends' drivers
are not pure Python, so bundling them would mean one build per operating system;
their dialects still select, and still bring the right keywords and quoting.

- **Connections are managed from a view**, not by editing JSON: add, edit,
  remove, set and clear a password, choose which one is in use.

- **A connection can be asked whether it works, and answers in words.** Every
  kind of failure looks identical from an editor — completion simply stops
  being schema-aware — so the message is the feature. A missing password says
  so; pg8000's own answer is `'NoneType' object has no attribute 'decode'`,
  which sent this project's author debugging in the wrong direction. A rejected
  password, a database that is not there and a port with nothing behind it are
  each named distinctly.

- **Health and use are shown separately.** The icon is the last test result;
  the label says which connection the server holds. The one in use may be the
  broken one, and that is the case most worth seeing.

- **Verdicts are never remembered across sessions.** A tick from last week is a
  claim nobody checked today.

- **Passwords have nowhere to live but secret storage.** The settings schema has
  no field for one, a test asserts it stays that way, and removing a connection
  removes its password — an orphan would be inherited by the next connection
  reusing that name.

### A language server

The engine now speaks LSP, so an editor can drive it. `pysqlsuggestions-lsp` is
a second distribution in `lsp/` rather than a module in `src/`: a server needs
pygls and a driver, and the library's promise is that importing it pulls in
neither. Two tests hold that line — the versions must agree, and `src/` may not
name the server package.

The library itself is unchanged. Nothing was added to it, renamed in it, or
removed from it.

- **Completion at a caret, over stdio.** The connection profile arrives in
  `initializationOptions`; without one the server completes from the statement
  alone, which is the library's documented degraded mode rather than an error.

- **A completion request never fails.** An unreachable database, a rejected
  password or a dialect with no driver all fall back to that same mode. The
  failure is recorded rather than retried, because retrying means a blocking
  connection attempt for every character typed.

- **The database is not contacted until the first completion.** Opening a
  document opens no socket, so a backend that is down costs a completion rather
  than a hung editor.

- **The engine's ranking survives the trip.** Items carry `sortText`, since a
  client re-sorts by its own fuzzy score otherwise, and `filterText` set to the
  term the engine matched — the column name, so `usern` still finds
  `u.username`. Items carry a `textEdit` with an explicit range and never an
  `insertText`: re-deriving a word boundary is what drops a qualifier.

- **`plan_insertion`'s second edit reaches the editor.** A column accepted
  before any FROM exists writes the clause it needs as an `additionalTextEdit`,
  and a suggestion carrying template blanks — a statement shape, `Kind.SNIPPET`
  — becomes a snippet placeholder. A join proposal carries none: it inserts a
  finished clause, alias and condition included.

- **Statements are cut at semicolon tokens, not characters.** Scope comes from
  the whole statement, and a semicolon inside a literal, a comment or a quoted
  identifier is not a boundary. The dialect's own lexer decides.

- **A `pg8000` extra.** Pure Python, so the wheels an editor extension bundles
  are platform-independent. psycopg2 remains the documented choice for library
  users; this only governs what a bundle carries. ClickHouse is consequently a
  dialect the library serves and the server does not, its driver not being pure
  Python — the dialect still resolves, so keywords and quoting are right, and
  only the catalog is absent.

## 0.2.1

The library is unchanged — `src/` is byte-identical to 0.2.0. This release exists
to publish the demo, which is what a `v*` tag does.

### Demo

- **The boot shows how far along it is.** A cold visit spent 42 of its 44
  seconds on one unmoving `loading Python…`, 40 of them the wasm transferring.
  That is indistinguishable from a hang, and it is the first thing this project
  shows anyone. The runtime is now read through a streaming counter before
  `loadPyodide`, which then finds it in cache rather than fetching it twice, and
  the page draws a bar against a total the build injects.

  A percentage rather than megabytes: a `fetch` stream yields decoded bytes
  while the wire moves compressed ones — 8.25 MiB against 2.73 MiB for the wasm
  — so no byte counter on that page can honestly report how much has arrived.

- **`starting Python…` is a new phase.** Compiling the wasm and starting the
  interpreter take about two seconds during which the old message sat unchanged,
  past the point where the download had plainly finished. That was most of why
  the boot read as stuck.

- The bar disappears at 100% rather than sitting full, since a stalled full bar
  reads as the very hang this removes. If the stream is unsupported or a fetch
  refuses, the page boots exactly as it did before, without a bar.

## 0.2.0

Joins. `JOIN ⌶` answers with the whole clause and `ON ⌶` with the whole
condition, both read from the foreign keys the database already declares.

Nothing was removed or renamed. The additions are new fields with defaults and a
new `Kind` member — worth knowing if you exhaustively match on `Kind`, since
`kind` is what consumers serialise into an editor payload.

### Positions that now answer differently

- **`JOIN ⌶` offers whole clauses.** `FROM booking b JOIN ⌶` proposes
  `flight f ON b.flight_id = f.id` — relation, alias and condition in one
  accept — ahead of the relation names it used to list alone. Each proposal is
  annotated with the constraint it came from.
- **`ON ⌶` offers the whole condition.** `JOIN auth_user u ON ⌶` proposes
  `r.author_id = u.id` rather than leaving the comparison to be typed. The
  columns stay underneath, for a condition the constraints do not describe.
- **`ON r.⌶` ranks that relation's foreign key columns up**, annotated. A
  qualifier has committed the left side, so a whole condition is no longer
  expressible there.

Proposals fire from **both ends** of a constraint, because a constraint is
directed and a join is not: a query starting at `auth_user` — which holds no
foreign key columns and is referenced by seven tables in the test fixture — is
offered the relations that reference *it*. Many-to-one ranks above one-to-many,
being both more often wanted and unable to multiply the result set. Two
constraints to the same relation stay two proposals; choosing between them is
the caller's.

**Postgres only, and deliberately.** ClickHouse and Trino declare no
constraints, so both positions there behave exactly as before. The obvious
fallback — matching `<singular>_id` against `<table>.id` — is rejected rather
than unbuilt: it is right often enough to be inviting and wrong often enough to
matter, and a wrong join condition is valid SQL that silently returns the wrong
rows. No parser catches that, and neither does the person reading the result.

### Added

- `ForeignKey` — one declared relationship, with column tuples on both sides, so
  a composite key needs no special case and renders as an `AND` chain.
- `SupportsForeignKeys` — the capability behind the two positions above. Absent,
  they answer as they did before. A backend that keeps no constraints should not
  implement it rather than guess.
- `Kind.JOIN` — a candidate that is a whole clause or condition rather than a
  name, so a front end can render it distinctly.
- `Suggestion.note` — why a suggestion outranks its neighbours, as
  `fk: auth_user.id`. Distinct from `detail`, which says what the thing is.
- `Candidate.match_text` — what matching runs against when that is neither the
  text nor the label. A join proposal is hunted for by the relation name and
  inserts a whole clause; without a field of its own the two collided and the
  list showed a bare name.
- `MemoryCatalog(foreign_keys=...)` — declare relationships in a snapshot, which
  is what makes the two positions testable without a database.
- `pysqlsuggestions.testing.DialectConformance` — the shared corpus every
  dialect must pass, specified for 0.1 and not built until now. It reads a
  dialect's declarations for mistakes that can only ever do nothing (a
  lowercase clause name, a `follows` naming a clause that is absent), then puts
  it the propositions every caller assumes: an alias reaches its columns, a
  dotted path narrows one level per segment, a quoted name is the same name,
  both sides of a join are in scope. The SQL is spelled from what each dialect
  says about its own namespace and quoting, so a three-level dialect is asked
  about three levels.

  Shipped rather than kept in `tests/`, so anyone publishing a dialect can hold
  it to the same standard.

- `pysqlsuggestions.dialects.registry` — `available()` and `named()`, which
  read the `pysqlsuggestions.dialects` entry-point group. The group has been
  advertised in `pyproject.toml` since 0.1.0 and nothing read it, so a
  third-party dialect could register correctly and never be found.

### Demo

- **The published page reaches nothing.** Pyodide is carried in the site rather
  than fetched from a CDN, pinned by digest, and the build refuses to assemble a
  page whose files name any absolute URL. That costs 11.7 MiB against a demo
  payload of 135 kB and buys a page that works on an air-gapped laptop and
  cannot be broken by somebody else's outage — which is the claim the demo
  exists to make. It had already failed the other way: a load where `micropip`
  could not be fetched left the page booted with a dead editor and nothing a
  visitor could act on.
- `micropip` is gone with it. It was loaded only to install one pure-Python
  wheel with no dependencies, which `unpackArchive` does in three lines.
- The demo schema declares its foreign keys, including two from one relation to
  the same target and two that cross a schema boundary.

## 0.1.1

Every change is a fix. Nothing was removed and nothing renamed; the additions
are new fields with defaults.

### Suggestions that were wrong

Each of these produced SQL the server rejects, and each is now checked against a
real Postgres at every caret in a corpus of statements — see *Testing* below.

- **A relation that is already written no longer offers another.** The blank
  line under a finished query answered with every relation in the schema, and
  with the catalog list on a three-level dialect. A comma or a JOIN has to come
  between two relations.
- **`AS` is spent once it has been used.** `FROM flight_raw AS fr ` offered `AS`
  again, at the top of the list. The same in a select list: `SELECT id AS x `.
- **An alias is offered for the relation it would attach to**, rather than for
  the last one still lacking a name — `FROM a JOIN b AS y ` proposed a name for
  `a`, which would have landed after `y`.
- **A clause name stopped between its two words takes only the rest of itself.**
  `GROUP ` offered every relation in the schema; `ORDER `, `INSERT `, `DELETE `
  and `LEFT ` were the same. Derived from the clause model, so a dialect adding
  `ARRAY JOIN` or `DISTINCT ON` is covered without further declaration.
- **Clauses that shape a result set are offered only where there is one.**
  A finished `UPDATE ... WHERE id = 2` offered `GROUP BY`, `HAVING`, `WINDOW`,
  `ORDER BY`, `LIMIT`, `OFFSET`, `FETCH` and the three set operators. A query
  nested inside an INSERT or a CTE still gets them.
- **`LATERAL` and Trino's `UNNEST` are offered where a reference begins**, not
  after one: `JOIN t AS u LATERAL` parses as nothing.
- **`LIMIT ` takes a number** and no longer fills that position with `OFFSET`.
  `LIMIT` and `FETCH` are two spellings of one limit, so writing either settles
  both.
- **`DISTINCT` is offered before the select list rather than after an item**,
  and behind a typed prefix. It was offered at `SELECT * ` and `SELECT x AS n `,
  where it is a syntax error, and `SELECT dis` found nothing at all.
- **`UPDATE` and `INSERT INTO` offer what actually follows them** — `SET` and
  the rows respectively. `FROM`, `WHERE`, `RETURNING` and `ON CONFLICT` come
  after those, not after the table being named.
- **An operator position offers operators.** `UPDATE t SET total ` answered with
  the reserved word list — `AS`, `BY`, `DO`, `IN`, `IS`, `ON` — where only `=`
  belongs.
- **A star takes no alias** (`SELECT * AS x` is a syntax error), while
  `count(*) ` may still be aliased.
- **A cast offers its own keyword.** `SELECT cast(total ` offered `FROM` and
  `GROUP BY`; only `AS` can follow the value.
- **An enum column that also has statistics listed every value twice**, once
  named by the type and once measured by the planner.
- **A word the clause model can suggest is a word the analyser recognises.**
  Twelve were missing, including `UPDATE`, `INSERT`, `DELETE` and `SET`, so
  writing one looked like finishing an operand: `UPDATE ` offered the clauses
  that follow a relation instead of the relation.

### Insertion

- **Accepting a namespace keeps the completion list open.** Choosing a schema,
  database or catalog left the caret past the dot with nothing offered, so the
  next level had to be triggered by hand.
- **Finishing a statement template leaves the caret past the end of it.** The
  blank filled last is the select list, in the middle of the statement, so a
  completed template stranded the caret inside a finished clause.

### Added

- `Insertion.expects_more` — whether the caret was left where completion should
  carry straight on. Not inferable from the caret and the edits, which is how a
  front end got it backwards for every namespace whose dot it had to write.
- `Clause.before_the_item`, `Clause.opens_an_item`, `Clause.aliases_with` — what
  stands between a clause and its first item, what may only begin an item, and
  the word that gives a relation its alias.
- A dialect folds its own clause vocabulary into `keywords` at construction, so
  the set the analyser consults cannot fall behind the words the model offers.

### Demo

- The browser build dropped the outstanding template blanks crossing into
  Pyodide, so choosing a table in the template left the caret where it was and
  closed the list. The request now crosses whole, as the server receives it.
- A Trino catalog is labelled `catalog`, a ClickHouse database `database`. The
  engine has one kind for every level of a dotted path because they behave
  identically; the word for it belongs to the dialect.

### Testing

Two harnesses, both of which found defects listed above and neither of which
existed before:

- `tests/integration/test_acceptance.py` accepts every suggestion at every caret
  across thirteen statements and asks Postgres to parse the result, telling a
  misplaced token from an unfinished statement by the error position. 1173 of
  1173 now land valid SQL, from 1144 of 1286.
- `tests/test_writable.py` walks realistic statements offline and asks whether
  anything offered continues them. 55 of 55 carets, from 49.

## 0.1.0

First release. Context-aware, schema-aware SQL completion as a library: lex,
analyse, derive a request, resolve it against a catalog, rank. Postgres deep;
ClickHouse and Trino ship as dialect data. No runtime dependencies.
