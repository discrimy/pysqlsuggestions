# Changelog

Grouped by what changes for someone using the library rather than by commit.
The engine's whole job is what it offers at a caret, so that is what this
records: the positions where it now answers differently.

## Unreleased

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
  and a join proposal's template blanks become snippet placeholders.

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
