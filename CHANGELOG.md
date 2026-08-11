# Changelog

Grouped by what changes for someone using the library rather than by commit.
The engine's whole job is what it offers at a caret, so that is what this
records: the positions where it now answers differently.

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
