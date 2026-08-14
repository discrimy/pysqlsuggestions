# Closing the SELECT grammar gaps — design

The conformance suite reports 22 of 59 positions answered. This closes every gap
it records except the six that sit inside parentheses, and it does so in dialect
data: no file under `engine/` changes.

Sequel to `2026-08-13-select-grammar-conformance-design.md`, which built the
suite and deliberately repaired nothing.

---

## 1. Context

### Six of the recorded gaps are the suite's fault

`engine/request.py:315` gates `before_the_item` behind a non-empty prefix:

```python
if prefix and opening is not None and opening.before_the_item and at_the_clause_start(...):
    return opening.before_the_item, False
```

The reason is written beside it — at `SELECT ⌶` a column is nearly always what
belongs there, and a rarely-wanted keyword above every column costs more than it
can return; behind a prefix it costs nothing. Measured, it works:

| caret | offered |
| --- | --- |
| `SELECT dis⌶` | `DISTINCT` |
| `WITH rec⌶` | `RECURSIVE` |

So `WITH ⌶ → RECURSIVE`, `SELECT ⌶ → DISTINCT`, `GROUP BY ⌶ → ROLLUP` and
`LIMIT ⌶ → ALL` are cases demanding behaviour this codebase refused on purpose.
Acting on them would undo a decision, not close a gap. `LIMIT` is the sharpest:
its docstring already records that giving it a kind made `LIMIT ⌶` offer
`OFFSET`, "which belongs after the number rather than instead of it", so the
naive fix reintroduces a fixed bug.

**Four positions get rewritten to use a prefix.** They are not engine work.

### Trino already models TABLESAMPLE

`tests/test_dialect_clauses.py` asserts `TRINO.clauses.get('TABLESAMPLE')` is not
None while ANSI's is. The declaration is three fields:

```python
Clause(name='TABLESAMPLE', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.KEYWORD,))
```

That is enough to stop `FROM t TABLESAMPLE ⌶` being read as still inside `FROM`,
which is exactly what the Postgres case needs. Three of the nine positions the
suite marked "refused, needs silence" — `TABLESAMPLE`, `SEARCH`, `CYCLE` — are
therefore clause data. Only the six inside parentheses need the analyser.

### Decisions taken during brainstorming

1. **Dialect data only.** Nothing under `engine/` changes. §2.
2. **Mechanism follows the position, not the word.** §3.
3. **Postgres first; ANSI only on evidence from all three backends.** §4.

### Rejected approaches

- **Giving `FETCH` a flat `followed_by` and stopping there.** It passes all three
  FETCH cases, because `offers` is a subset assertion — and offers `ONLY` at
  `FETCH ⌶`, where it cannot go. Trading a missing answer for a wrong one is the
  trade this repository refuses everywhere else.
- **Teaching `ClauseModel` about positions within a clause.** A real parser for
  `FETCH FIRST 10 ROWS ONLY` and nothing else. `EXCLUSIVE` already expresses
  ordered choices and costs one tuple. §3.
- **Putting every standard production in `ansi.py`.** The conformance suite
  proves Postgres only, so an ANSI change ships untested for two backends, and
  each inherited word is a wrong answer wherever the backend rejects it. §4.
- **A spec per group.** Considered, since the four groups merge separately. One
  spec covers them because they share a single rule, a single test suite and a
  single risk profile; the *plan* splits them into tasks that merge on their own.

---

## 2. What this does not do

Every change is a `Clause` field, an `EXCLUSIVE` entry, a `statement_start`
entry, or a case rewrite — with one exception, found by prototyping and added
here after the design was first approved.

### The one engine change: `at_the_clause_start` is broken

`engine/analyse.py:313` compares the run of words before the caret to the clause
name by equality, and `_words_before` walks back through consecutive identifiers
without stopping at the clause boundary:

| caret | `_words_before` | equals the name? |
| --- | --- | --- |
| `SELECT dis⌶` | `('SELECT',)` | yes |
| `GROUP BY rol⌶` | `('USERS', 'GROUP', 'BY')` | no |
| `LIMIT al⌶` | `('USERS', 'LIMIT')` | no |

So `before_the_item` is dead for every clause that does not begin its statement,
and `DISTINCT` works only because `SELECT` happens to lead. The function's own
docstring says it reports "whether nothing has been written in `clause` yet",
which at `GROUP BY ⌶` is true and it answers false.

The fix is to ask whether the run *ends with* the name rather than equals it.
Three lines, its own task and its own commit so it can be reverted alone, and
the guards hold: `SELECT id, dis⌶` and `GROUP BY id, rol⌶` both stay silent,
because a comma breaks the run before either can match.

Taken deliberately rather than deferred with the rest of the analyser work. It
is a defect in a function used by one caller, not the paren-context feature —
and without it `GROUP BY ROLLUP` and `LIMIT ALL` cannot be expressed at all.

### `FOR UPDATE OF` stays pending

`OF` takes a `from_reference`, and once a relation is aliased Postgres accepts
only the alias. `Kind.TABLE` offers catalog relations, so it answers
`FROM users u FOR UPDATE OF ⌶` with `users` — which the server refuses.
`Kind.ALIAS` is no help either: it *generates* a name for the relation just
written rather than listing the ones in scope.

Nothing in the `Kind` vocabulary means "a relation this statement already has",
so `OF` is declared with `suggests=()`. A silent caret rather than a confident
wrong one, which is the trade this repository makes everywhere else. The
capability is worth its own design.

Six positions stay pending and refused, all of them inside parentheses:
`WITH x (⌶`, `FROM users AS u (⌶`, `REPEATABLE (⌶`, `AS t (⌶`, `AS (⌶`,
`ROWS FROM(⌶`. Each reads as an ordinary relation position because the analyser
does not know the construct that opened the paren, and teaching it that is work
in `engine/analyse.py` with a different risk profile — a regression there shows
up across the whole suite rather than in these cases. It gets its own decision
once this has landed.

---

## 3. Mechanism follows the position

The rule, and the only thing an implementer has to internalise:

> **Does anything else belong at this caret?**
> If yes — a column, a relation, a row count — the words go in
> `before_the_item`, and the conformance case uses a prefix.
> If no, they go in `followed_by` and appear at an empty caret.

| position | competing candidates | mechanism |
| --- | --- | --- |
| `SELECT ⌶`, `GROUP BY ⌶` | columns | `before_the_item` |
| `LIMIT ⌶` | a row count | `before_the_item` |
| `FETCH ⌶`, `OFFSET 10 ⌶`, `UNION ⌶` | none | `followed_by` |
| `FOR UPDATE ⌶` | none | `followed_by` |
| `FOR ⌶` | none | a half-written clause name |

`FOR ⌶` is the third mechanism and not a fourth rule: the `FOR` family are
two-word clauses, so the caret after `FOR` is a partially typed clause name and
`_half_written_clauses` completes it — the same path that answers `DROP ⌶` with
`DROP TABLE`. It is listed here only because an implementer looking for
`followed_by` on a clause called `FOR` will not find one, there being no such
clause.

### FETCH, and why `EXCLUSIVE` is the right machine

`FETCH { FIRST | NEXT } [ count ] { ROW | ROWS } { ONLY | WITH TIES }` names four
carets and three choices, in order. A flat `followed_by` offers all six words
everywhere. `EXCLUSIVE` already exists for precisely this shape — it holds
"choices made once per list item, each sequence written in the order SQL takes
it", and making a later choice settles the earlier ones:

```python
(frozenset({'FIRST', 'NEXT'}), frozenset({'ROW', 'ROWS'}), frozenset({'ONLY', 'WITH TIES'})),
```

With that entry and a flat `followed_by`, `FETCH ⌶` offers all six and
`FETCH FIRST 10 ROWS ⌶` offers `ONLY` and `WITH TIES` alone. The clause model
learns nothing about positions; the same tuple that stops `ORDER BY id ASC ⌶`
offering `DESC` does the work.

---

## 4. Placement, and what counts as evidence

Every production lands in `postgres.py` unless all three backends are shown to
accept it. Four are promoted to `ansi.py`, and each must be *demonstrated*
against the containers in `docker/docker-compose.yml` — postgres:16, clickhouse
24.8, trino 468 — not argued from the standard:

| promoted to `ansi.py` | why it is safe |
| --- | --- |
| `FETCH { FIRST \| NEXT } … { ONLY \| WITH TIES }` | standard row limiting |
| `RIGHT JOIN`, `FULL JOIN`, the `OUTER` spellings | all three have outer joins |
| `UNION \| INTERSECT \| EXCEPT … DISTINCT` | all three spell the default |
| `OFFSET start [ ROW \| ROWS ]` | standard noise words |

A promotion whose statement any backend rejects moves to `postgres.py` and the
rejection is recorded where the clause is declared. Evidence is a statement the
server accepts, run against the container; the integration tests already skip
rather than fail when a backend is unreachable, and that convention holds here.

Everything else is Postgres's: `LIMIT ALL`, `ORDER BY … USING`, the `FOR` family,
`TABLE`, and the three silencing clauses.

### The ANSI half needs its own guard

The conformance suite proves Postgres. An `ansi.py` change reaches ClickHouse and
Trino with no case covering them, which is the whole reason the default is
Postgres-first. Each promotion therefore also gets an assertion in
`tests/test_dialect_clauses.py`, beside the existing `test_clickhouse_prewhere`
and the two vocabulary tests. One line per promotion, and without it the
promotion does not land.

---

## 5. The changes

**`ansi.py`** — the four promotions above, plus `WINDOW`, which is not a
promotion but a correction:

```python
Clause(name='WINDOW', statements=_QUERY, suggests=(), opens_a_group=('PARTITION BY', 'ORDER BY'),
       aliases_with='AS', followed_by=_onwards('UNION')),
```

`suggests=COLUMN_EXPRESSION` is a wrong answer, not a missing one: a window name
is being defined at `WINDOW ⌶`, and a column there writes a statement the server
refuses. All three backends have named windows, so the correction is shared.

**`postgres.py`** — `LIMIT ALL` and the five `GROUP BY` grouping words as
`before_the_item`; `ORDER BY … USING`; `FOR UPDATE`, `FOR NO KEY UPDATE`,
`FOR SHARE`, `FOR KEY SHARE` with `OF`, `NOWAIT` and `SKIP LOCKED`; `TABLE`
added to `statement_start` with a clause naming `RELATION_REFERENCE`; and
`TABLESAMPLE`, `SEARCH` and `CYCLE` declared solely so those carets stop
answering, each carrying a comment saying that is why it exists.

Two-word clause names throughout for the `FOR` family, for the reason
`ALTER TABLE` and `DROP SEQUENCE` already record: a bare `FOR` among the
continuations would make `('FOR',)` a phrase in its own right, and
`_half_written_clauses` skips a head that is already a phrase.

---

## 6. Testing

The conformance suite is the acceptance test; no new test module. Four groups,
each merging on its own, each ending with the burn-down as its criterion:

1. the four prefix rewrites, plus `SELECT al⌶` and the `GROUP BY` words as new
   cases — a prefix-gated position needs one case per word the grammar names
   there, so the denominator grows;
2. the result-shaping tail;
3. the join vocabulary;
4. the Postgres forms and the three silencing clauses.

**The target is seven pending cases** — the six paren-context refusals, plus
`FOR UPDATE OF ⌶`, which §2 records as needing a capability that does not
exist. The
denominator is whatever the rewrites make it, so the goal is stated as the
remainder rather than as a ratio — the plan measures both, as the previous plan
did, and no flag is written down that has not been run.

Every case flipping from pending to passing is one `pending=True` deleted.
`xfail(strict=True)` means a case fixed by accident fails the build until its
flag is removed, so the suite cannot silently over-report.

---

## 7. Documentation

- `CHANGELOG.md`: grouped by what changes at a caret, and a great deal does.
  `WINDOW ⌶` and the `FOR` family lead, both being wrong answers rather than
  missing ones.
- `docs/gaps.md`: no new entry. The suite is the list for this territory, which
  the intro now says.
- The six remaining refusals keep their `refused` reasons in
  `tests/grammar/cases.py`, which is where a reader looks for them.

---

## 8. Open questions carried forward

- **The six paren-context positions.** `engine/analyse.py` has to know what
  opened a paren before those carets can go quiet. Its own decision, deliberately
  not taken here.
- **ClickHouse's `GROUP BY … WITH ROLLUP`.** ClickHouse spells the grouping sets
  differently from Postgres and Trino, which is why the grouping words are
  Postgres's here. A ClickHouse-local declaration is a later, separate change.
- **Whether `TABLESAMPLE` deserves more than silence.** Trino's declaration
  offers `Kind.KEYWORD` and no keywords, so both dialects now have a clause that
  exists to stop a wrong answer rather than to give a right one. Naming the two
  standard methods is a small change that neither backend's docs make risky, and
  it is left out only because sampling methods are installation-extensible.
