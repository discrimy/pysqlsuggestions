# The official SELECT grammar as a test oracle — design

A conformance suite for Postgres, written against the `SELECT` synopsis in the
PostgreSQL 18 documentation. It measures; it changes no shipped behaviour.

The corpus in `tests/corpus/` is a burn-down against *observed* expectations —
pgcli's tests and a production suite. This is a burn-down against a *specified*
one, which fails differently: a corpus can only contain positions somebody
thought to write down, and the synopsis contains every position the grammar has.

---

## 1. Context

### What the probes found

Run against `MemoryCatalog` with `users(id, email)` and
`orders(id, user_id, total)`. Two classes of fault, and the second was the
surprise.

**Positions that say nothing.** `FETCH ⌶` and `FETCH FIRST 10 ROWS ⌶` both
report `kinds=['keyword']` and offer no keyword at all — `Clause(name='FETCH')`
has a kind and no `followed_by`, so it claims a position it cannot fill.
`LIMIT ⌶` offers nothing where `ALL` is legal. `TABLE ⌶` offers nothing, which
is the correct treatment of an unrecognised form and still a missing answer.

**Positions that say something wrong.** These matter more, and this repository
already argues why — `docs/gaps.md` refuses inferred foreign keys on exactly
this ground.

| caret | offered today | the grammar's answer |
| --- | --- | --- |
| `SELECT * FROM users WINDOW ⌶` | `users.id`, `users.email` | a window name being defined |
| `SELECT * FROM users FOR ⌶` | `users`, `public`, `AS`, `JOIN`… | `UPDATE`, `NO KEY UPDATE`, `SHARE`, `KEY SHARE` |
| `FROM ROWS FROM(⌶` | `users`, `orders`, `public`, `AS`, `JOIN`… | a function |
| `FROM f(1) AS t (⌶` | `users`, `orders`, `public` | a new column name |
| `FROM users TABLESAMPLE ⌶` | `JOIN`, `LEFT JOIN`, `WHERE`… | a sampling method |
| `TABLESAMPLE BERNOULLI (10) REPEATABLE (⌶` | `users`, `orders`, `public` | a seed |
| `WITH RECURSIVE x AS (…) SEARCH ⌶` | `SELECT`, `INSERT INTO`, `UPDATE`… | `BREADTH`, `DEPTH` |
| `WITH x (⌶` | `SELECT`, `VALUES`, `WITH`… | a column name |
| `FROM users u ⌶` | `JOIN`, `LEFT JOIN`, `INNER JOIN`, `CROSS JOIN` | those plus `RIGHT`, `FULL`, `NATURAL`, the `OUTER` spellings |

`FOR ⌶` is the sharpest of them. `FOR` is not a clause, so the analyser still
believes the caret is inside `FROM`; accepting the first suggestion writes
`SELECT * FROM users FOR users`.

**Positions that pass by accident.** `SELECT ALL ⌶` offers columns,
`FROM ONLY ⌶` offers relations, and `FOR UPDATE OF ⌶` offers the aliases in
scope. All three are right, and none of them is right *because the model knows
the production* — the unmodelled word is skipped as an ordinary token and the
surrounding clause carries the position. `FOR UPDATE OF ⌶` reports its clause as
`UPDATE`, which is how you can tell.

Recorded because it means green has two meanings here, and a case that is green
for the second reason will go red the moment the production is modelled properly.

### The gap against the synopsis

Beyond the table above: `GROUP BY [ALL | DISTINCT]` and its `grouping_element`
alternatives (`ROLLUP`, `CUBE`, `GROUPING SETS`), `{UNION|…} … DISTINCT`,
`ORDER BY … USING operator`, `OFFSET start [ROW | ROWS]`, the whole
`FETCH … {ONLY | WITH TIES}` tail, `FOR … [NOWAIT | SKIP LOCKED]`, the
`[[AS] alias [(column_alias, …)]]` suffix every `from_item` takes,
`WITH ORDINALITY`, `USING (…) AS join_using_alias`, `[NOT] MATERIALIZED`, the
`CYCLE` clause, and the bare `TABLE [ONLY] table_name [*]` statement form.

### Decisions taken during brainstorming

1. **Measurement, not repair.** The suite lands red where the model is
   incomplete and nothing in `src/` changes. §2.
2. **Each case asserts both directions** — words that must be offered and words
   that must not. §3.
3. **`refused` labels the production, not the case.** §3.

### Rejected approaches

- **Demanding every legal word at every caret.** The synopsis says what is
  legal; `Clause.follows` says what is *typical*, and its docstring is explicit
  that the two differ — `HAVING` after a bare `FROM` is valid SQL nobody writes.
  A mechanical reading would demand the engine bury the useful answers, which is
  the failure this library exists to avoid. Cases are curated per position, and
  cite the line they come from.
- **Grammar-driven fuzzing.** Generating statements from the productions and
  asserting the lexer never raises tests an invariant `tests/test_lex_*.py`
  already covers directly, and it finds crashes rather than wrong answers. The
  wrong answers are the valuable half and a generator cannot recognise them.
- **Folding these into `tests/corpus/cases.py`.** That corpus records
  `Request` shape — kinds, prefix, qualifier, clause, relations — and runs
  against `derive_request` with no catalog. These cases are about the words a
  caret offers, which is `complete` and a catalog. Same caret convention, a
  different assertion, so a different file.
- **Silence as the definition of `refused`.** The rule the brainstorm settled on
  first, discarded once the probes ran: it would have described almost none of
  the exotica, since nearly every one of those positions answers wrongly rather
  than not at all.

---

## 2. What this does not do

No file under `src/` changes. Every case either passes or is
`xfail(strict=True)`, so `scripts/check.sh` stays green and the gate keeps its
meaning.

The pending list is a worklist for later plans, and each entry is one line of
the synopsis rather than a paragraph of prose. `docs/gaps.md` is not restructured
around it — a burn-down that prints on every run and a document explaining
refusals are different artefacts, and the document already says so about DBeaver.

---

## 3. The data

`tests/grammar/cases.py`:

```python
@dataclass(frozen=True)
class GrammarCase:
    """One caret the official synopsis names, and what the engine must say there."""

    sql: str
    """Caret marked with ⌶, the convention `tests/corpus/cases.py` established."""
    cite: str
    """The synopsis line this position comes from, verbatim."""
    offers: tuple[str, ...] = ()
    """Suggestion texts that must all appear. Order is not asserted."""
    refuses: tuple[str, ...] = ()
    """Suggestion texts that must not appear at all."""
    pending: bool = False
    """True for a case the model cannot satisfy today: xfail(strict=True)."""
    refused: str = ''
    """Why this production is a deliberate non-goal. Empty for the rest."""
    note: str = ''
```

`CARET` and `split_caret` are imported from `tests/corpus/cases.py` rather than
restated. One caret convention in the repository, and a second copy would be a
second thing to keep in step.

### The two flags are independent, and that is the point

`pending` describes the **case**: it does not pass today. `refused` describes the
**production**: the engine is not going to learn it.

They combine, and the combination is the honest description of `TABLESAMPLE ⌶`.
*We will not model sampling methods* and *the engine must not offer a table name
there* are different commitments, and both are true at once. A refused case that
is also pending says: this is work, and the fix is to make the position silent
rather than to implement the grammar.

So `refused` does not remove a case from the burn-down. The first draft of this
design had it do exactly that, on the theory that refused productions were
already quiet. They are not, and excluding them would have hidden six wrong
answers behind a word that sounded like a decision.

### `offers` is a subset assertion, `refuses` an exclusion

`offers` requires each word to appear somewhere in the returned texts; it does
not require the list to be exactly that. Ranking is `engine/rank.py`'s subject
and `tests/test_complete.py` already pins it — a conformance case that also
asserted order would fail on changes that have nothing to do with the grammar.

`refuses` requires absence. This is where wrong answers die, so a case with an
empty `refuses` and an empty `offers` is meaningless and the data tests reject
it.

---

## 4. The synopsis file

`tests/grammar/select.txt` holds the synopsis verbatim — the statement skeleton
and the `from_item`, `grouping_element`, `with_query` and `TABLE` definitions —
under a header giving the source URL, the server version and the date fetched.

Verbatim and in its own file rather than a Python constant, because a coverage
test reads it:

> every non-blank, non-header line of `select.txt`, with runs of whitespace
> collapsed, is a substring of at least one case's collapsed `cite`

That is what keeps the suite tied to the document it claims to track. Replace
the file when a later server changes the grammar and the test names the lines
nobody covered; without it the file is decoration and the case list drifts.

Whitespace is collapsed on both sides because the synopsis is indented for
print, and a case citing a wrapped continuation line should not have to
reproduce the column it was wrapped at.

`select.txt` is named for its statement so `insert.txt` and the rest can arrive
later without moving anything. Only SELECT is in scope here.

---

## 5. The runner

`tests/test_grammar_select.py`, holding both halves — the corpus splits its
runner from its data tests because two runners share one case list, and here
there is one.

```python
def _params() -> list[object]:
    """Each case, marked xfail(strict=True) while it is still pending."""


@pytest.mark.parametrize('case', _params(), ids=[c.sql for c in CASES])
def test_grammar_position(case: GrammarCase) -> None:
    """Every word the synopsis puts at this caret is offered, and none it forbids."""
```

One shared fixture: `users(id, email)` and `orders(id, user_id, total)`, the
shape `tests/test_statement_forms.py` already uses. Enough relations for a join,
enough columns for a qualifier, and small enough that a `refuses` list can name
every column by hand.

Data tests in the same module, following `tests/test_corpus.py`:

- exactly one caret per case;
- `offers` or `refuses` non-empty;
- `refused` non-empty whenever it is set, so the reason cannot be a bare `True`;
- every `cite` appears in `select.txt`, which catches a citation invented at the
  keyboard;
- the coverage direction from §4, every synopsis line cited.

---

## 6. The burn-down

`tests/conftest.py` gains a third line beside the two it prints:

```
grammar burn-down: 31/47 SELECT positions answered, 6 of the 16 gaps refused
```

The denominator is every case, so the count is what it claims to be. The second
number is how many of the failing cases carry a `refused` reason, which is what
separates "this many positions are wrong" from "this many get fixed by making
the position silent rather than by modelling the grammar". The figures shown are
illustrative; the plan writes the cases and the numbers follow from them.

---

## 7. Documentation

- `CHANGELOG.md`: an entry under Unreleased. Grouped by what changes at a caret,
  as the file is — and what changes at a caret here is nothing, so the entry
  says so and leads with the wrong answers the suite records.
- `docs/gaps.md`: no new gap entry. The suite *is* the list for this territory,
  and the two would fall out of step. One sentence in the intro pointing at it.

---

## 8. Open questions carried forward

- **`FOR ⌶` is a wrong answer with a cheap fix.** A locking clause in
  `postgres.py` would close it, and it is out of scope here by decision, not by
  difficulty. First candidate for the plan that follows.
- **The other dialects.** ClickHouse and Trino publish their own SELECT
  grammars, and the shape here takes a second `.txt` and a second case list. Not
  attempted until the Postgres one has been through a burn-down cycle and the
  record has earned its fields.
- **Accidental greens.** Three cases pass because a word is skipped rather than
  understood (§1). Nothing in the data model distinguishes them from real
  passes except the `note`, which is deliberate — inventing a fourth state for
  three cases costs more than the note does.
