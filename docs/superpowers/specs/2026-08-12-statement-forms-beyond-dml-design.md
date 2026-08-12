# Statement forms beyond DML — design

Date: 2026-08-12
Status: **proposed**. Nothing built yet.

Closes the first half of gap 2 of `docs/gaps.md`: the statement forms this
engine does not recognise, and what it says at them instead of nothing.

---

## 1. Context

### What the engine does at these positions today

Measured against a two-relation `MemoryCatalog` under Postgres:

| caret | offered | why |
| --- | --- | --- |
| `DROP TABLE ⌶` | `SELECT`, `WITH`, `INSERT INTO`, `UPDATE`, … | no clause matched, and no clause means the empty-editor position |
| `TRUNCATE ⌶`, `ALTER TABLE ⌶`, `GRANT ⌶`, `CALL ⌶`, `VACUUM ⌶` | the same | the same |
| `CREATE TABLE t (id ⌶` | the same | the same |
| `EXPLAIN ⌶` | `SELECT`, `WITH`, `INSERT INTO`, … | the same — and here it is *right* |
| `EXPLAIN SELECT * FROM t WHERE ⌶` | columns | `EXPLAIN` is skipped as unknown and the inner statement analyses normally |

Two things stand out.

**The first is a wrong answer, not a missing one.** Accepting at `DROP TABLE ⌶`
writes `DROP TABLE SELECT`. `gaps.md` describes this as completing "as if it
were an expression"; it is worse than that — the position offers the words a
*statement* may begin with, inside a statement that has already begun.

**`EXPLAIN` already works, by accident.** `EXPLAIN`, `EXPLAIN ANALYZE` and
`EXPLAIN (FORMAT JSON)` all analyse their inner statement correctly today,
because an unrecognised leading word is simply skipped. Nothing pins that, and
§3 would break it.

### Prior art in this codebase to follow

- **A missing answer costs a keystroke; a wrong one costs correctness.** The
  argument is made in `ports.py` about inferred joins and in `engine/joins.py`
  about foreign keys, and it is the reason the bound-parameter work chose
  silence over a guess.
- **`_half_written_clauses`** already derives `GROUP ` → `BY` from the clause
  model, grouping multi-word phrases by head. A `DROP TABLE` clause gets
  `DROP ` → `TABLE` with no second edit.
- **`_clause_kinds` returns `(Kind.KEYWORD,)` after a relation** only when the
  clause has continuations — see the `Kind.TABLE in kinds` branch. A clause with
  an empty `followed_by` keeps offering relations after one is written.
- **`Clause.statements`** exists so `RETURNING` is refused after a SELECT's
  WHERE. A DDL statement form gets the same treatment for free.

### Decisions taken during brainstorming

1. **Refuse, then model.** Two changes that compose: a statement whose form the
   engine does not know suggests nothing, and the forms whose answer is "a
   relation" are modelled. The first fixes `GRANT`, `CALL`, `VACUUM`, `COMMENT`,
   `SET` and `BEGIN` without modelling any of them.
2. **"Completed" is the load-bearing word.** A statement has begun when a
   *completed* token precedes the caret. `SELEC⌶` has a token before the caret
   and the caret is inside it, so that is still the statement-start position and
   still completes to `SELECT`.
3. **`DROP VIEW` and `DROP INDEX` are out.** See §5.
4. **`CREATE TABLE` is out**, with `ALTER TABLE`'s vocabulary stopping at four
   words. This is the line `gaps.md` draws between DDL completion and DDL
   authoring, and this slice stays on the near side of it.

### Rejected approaches

- **Putting `EXPLAIN` in `statement_start`.** It would be offered in an empty
  editor, which is nice, and it would break `EXPLAIN SELECT … ⌶`:
  `statement_form` returns the first start it finds that is not `WITH`, so an
  EXPLAIN'd query would report its form as `EXPLAIN`, and every clause declaring
  `statements={'SELECT'}` — `GROUP BY`, `ORDER BY`, `LIMIT` — would be refused.
  `EXPLAIN` wraps a statement rather than being one, exactly as `WITH` does, and
  generalising that distinction is more machinery than being offered in an empty
  editor is worth.
- **Refusing on "no token before the caret" rather than "no completed token".**
  One word shorter and it breaks every half-typed statement keyword.
- **Leaving the empty-editor answer and adding DDL clauses only.** The wrong
  answer would survive at every form not modelled, which is most of them, and
  the list of forms nobody will model is unbounded.

---

## 2. Scope

### In

- The refusal: `clause is None` plus a begun statement yields no kinds.
- Four clauses in ANSI: `DROP TABLE`, `TRUNCATE`, `ALTER TABLE`, `EXPLAIN`.
- The first three added to `statement_start`, so an empty editor offers them.
- Tests pinning `EXPLAIN`'s behaviour, which is currently accidental.

### Out, deliberately

- **`DROP VIEW`, `DROP INDEX`, `DROP SCHEMA`.** §5.
- **`CREATE TABLE`,** and `ALTER TABLE` past `ADD`/`DROP`/`RENAME`/`ALTER`.
- **`GRANT`, `CALL`, `VACUUM`, `COMMENT`, `SET`, `BEGIN`.** They become silent,
  which is the whole point; modelling them is a separate decision each time.

### Non-goals

- Validating DDL. The engine does not know whether the relation being dropped
  exists in a droppable state, and a completion engine has no business finding
  out.

---

## 3. The refusal

One predicate in `analyse.py`:

```python
def statement_has_begun(tokens: Sequence[Token], lo: int, hi: int, caret: int) -> bool:
    """
    Whether a completed token precedes the caret in this statement.

    The empty-editor answer — the words a statement may begin with — is right
    only where a statement has not begun. After `DROP TABLE ` it proposes
    `SELECT`, and accepting that writes `DROP TABLE SELECT`.

    Completed is the load-bearing word. `SELEC<caret>` has a token before the
    caret, but the caret is inside it: the word is still being typed, so the
    position is still the one that offers `SELECT`.
    """
```

and a small restructure in `derive_request`: the kinds expression, currently
inline in the `Request(...)` call, is lifted to a local so it can be blanked.

```python
    kinds = _continued_kinds(continues, only, _expansion_first(star) + _values_first(...) + _kinds_for(...))
    if clause is None and not continues and statement_has_begun(tokens, lo, hi, caret):
        kinds = ()
```

`not continues` is what keeps `DROP ⌶` answering `TABLE`: a half-written clause
names its own continuations, and those are the answer whatever the clause model
says about the statement.

Only `kinds` needs blanking. `resolve` returns early on empty kinds, so the
`statement_start` list in `_keywords` — the other place the empty-editor answer
is produced — is never reached, and needs no change of its own.

### What changes, verified

Measured across every position where `clause is None`:

| position | before | after |
| --- | --- | --- |
| `⌶`, `  ⌶`, `-- note\n⌶`, `/* c */ ⌶` | statement starts | unchanged |
| `SELEC⌶` | `SELECT` | unchanged |
| `SELECT id FROM t; ⌶` and `; SEL⌶` | statement starts | unchanged |
| `DROP TABLE ⌶`, `TRUNCATE ⌶`, `GRANT ⌶` | statement starts | nothing, then §4 for the modelled ones |
| `WITH a AS (⌶`, `SELECT * FROM (⌶`, `INSERT INTO t (⌶`, `SELECT (⌶`, `VALUES (⌶` | — | unchanged; their clause is not `None`, so the rule never reaches them |

The last row is the one worth checking rather than assuming, and it was.

---

## 4. The clauses

Four entries in `dialects/ansi.py`. Each `followed_by` is required rather than
decorative: `_clause_kinds` returns `(Kind.KEYWORD,)` after a relation only when
the clause has continuations, so an empty list leaves `DROP TABLE users ⌶`
offering a second relation, which cannot follow without a comma.

| clause | `suggests` | `followed_by` |
| --- | --- | --- |
| `DROP TABLE` | `RELATION_REFERENCE` | `CASCADE`, `RESTRICT` |
| `TRUNCATE` | `RELATION_REFERENCE` | `CASCADE`, `RESTRICT` |
| `ALTER TABLE` | `RELATION_REFERENCE` | `ADD`, `DROP`, `RENAME`, `ALTER` |
| `EXPLAIN` | `(Kind.SNIPPET, Kind.KEYWORD)` | `_EXPLAINABLE` |

`DROP TABLE`, `TRUNCATE` and `ALTER TABLE` join `statement_start`;
`EXPLAIN` does not, for the reason in §1.

**`EXPLAIN`'s continuations are not `statement_start`.** Postgres explains a
query, not a `DROP` — `EXPLAIN DROP TABLE users` is a syntax error. So the DML
starts get a name of their own and the two lists are built from it:

```python
_EXPLAINABLE = ('SELECT', 'WITH', 'INSERT INTO', 'UPDATE', 'DELETE FROM')
STATEMENT_START = (*_EXPLAINABLE, 'DROP TABLE', 'TRUNCATE', 'ALTER TABLE')
```

Written this way round, adding a DDL form to `statement_start` later cannot
silently start offering it after `EXPLAIN`.

`DROP ⌶` → `TABLE` needs no entry of its own: `_half_written_clauses` derives it
from the clause name, the same way `GROUP ⌶` → `BY` is derived.

`TRUNCATE TABLE users` — the ANSI spelling, where Postgres allows the bare form
— still works. `TABLE` is a reserved word, so `after_operand` reads it as a
keyword rather than a completed operand, and the position after it still wants a
relation.

---

## 5. Why `DROP VIEW` is not here

It needs relation-kind filtering to be correct: `DROP VIEW some_table` is a
statement the server refuses, and offering it costs the most valuable row in the
list — the same argument `_of_comparable_type` makes about a bigint on the wrong
side of a comparison.

Filtering needs a *set* of kinds per clause, not one kind, because `DROP TABLE`
must accept `partitioned table` and `foreign table` alongside `table`, and those
strings are normalised per dialect by the row mappers. That is dialect-specific
machinery in service of two clauses, so it waits for a slice that wants it.

Until then `DROP VIEW ⌶` falls under §3 and says nothing, which is the honest
answer for a form the engine does not model.

---

## 6. Testing

A new `tests/test_statement_forms.py`:

- Every row of §3's table, since the refusal's value is entirely in which
  positions it does *not* touch.
- Each modelled clause offers relations, and offers keywords rather than a
  second relation once one is written.
- `DROP ⌶` offers `TABLE`.
- `EXPLAIN ⌶` offers the statement starts; `EXPLAIN SELECT * FROM t WHERE ⌶`
  offers columns; `EXPLAIN ANALYZE …` and `EXPLAIN (FORMAT JSON) …` too. These
  pass today and would regress under §3 alone, which is exactly why they are
  written down.
- An unmodelled form — `GRANT ⌶`, `VACUUM ⌶` — offers nothing.

`tests/corpus/cases.py` gains rows for `DROP TABLE ⌶` and `GRANT ⌶`.

Conformance gains nothing. These are dialect vocabulary rather than a
capability, and `DialectConformance.structure` already requires that every
`statement_start` phrase names a declared clause — which the three new entries
satisfy, and which would have caught them if they did not.

Integration: Postgres plans `DROP TABLE <accepted>` inside a savepoint that is
rolled back, reusing the `misplaced` harness in
`tests/integration/test_acceptance.py`. The acceptance sweep also runs over the
corpus at every caret; a DDL statement added there would be accepted and parsed
by the server for free.

---

## 7. Documentation

- `docs/gaps.md`: gap 2 is narrowed rather than closed — it loses its `DROP` and
  `EXPLAIN` paragraphs and keeps `CREATE TABLE`, so the numbering does not move.
  The closed-gap section records that the entry understated the problem: the
  position offered the words a statement may begin with, not "an expression".
- `CHANGELOG.md`: the refusal first, because it changes behaviour for every
  unmodelled form, then the four clauses.

## 8. Open questions carried forward

1. **Relation-kind filtering**, which gates `DROP VIEW`, `DROP INDEX` and
   `DROP SCHEMA`. Wants a set of kinds per clause and a per-dialect vocabulary
   for them.
2. **`CREATE TABLE`.** The type position is already answerable —
   `dialect.types` ships for cast positions — and what is missing is a clause
   model for a parenthesised definition list.
3. **`EXPLAIN` and `WITH` are both wrappers.** `statement_form` special-cases
   `WITH` by name. A second wrapper makes that a category rather than an
   exception, and whoever adds `CREATE TABLE AS` or `CREATE VIEW AS` will meet
   it a third time.
