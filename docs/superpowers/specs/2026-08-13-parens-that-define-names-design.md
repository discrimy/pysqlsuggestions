# A parenthesis that defines names answers nothing — design

Five carets in the conformance suite offer relations or CTE-body words where the
author is inventing names. All five are wrong answers rather than missing ones,
and four of them are one rule.

Third in the SELECT-grammar sequence, after
`2026-08-13-select-grammar-conformance-design.md` built the suite and
`2026-08-13-closing-the-select-grammar-gaps-design.md` closed everything
expressible in dialect data.

---

## 1. Context

### The five carets

Measured against `MemoryCatalog` with `users` and `orders`:

| caret | offered today | what the grammar puts there |
| --- | --- | --- |
| `WITH x (⌶` | `SELECT`, `VALUES`, `WITH`, `INSERT INTO` | a CTE column name |
| `FROM users AS u (⌶` | `users`, `orders`, `public` | a column alias |
| `FROM f(1) AS t (⌶` | `users`, `orders`, `public` | a column name and type |
| `FROM f(1) AS (⌶` | `users`, `orders`, `public` | a column name and type |
| `FROM ROWS FROM(⌶` | `users`, `orders`, `public`, `AS`, `JOIN` | a function call |

### Why the engine cannot tell

`engine/request.py:322` is the whole of the current test:

```python
if opening is not None and opening.opens_a_group and depth_at(tokens, caret) > 0:
    return opening.opens_a_group, True
```

Depth and the governing clause, and nothing about what opened the paren. So
`WITH x (` and `WITH x AS (` are indistinguishable, and a `FROM` whose clause
declares no group falls through to the relation suggestions that clause would
give anywhere else.

The knowledge is already in the file for a different purpose:
`_read_declared_columns` parses `WITH x (a, b) AS …` so that scope resolution
knows the CTE's columns. The parse exists; the suggestion path does not consult it.

### Decisions taken during brainstorming

1. **One predicate, keyed on the token left of the paren.** §2.
2. **Dialect data only — no SQL words in `engine/`.** §2.
3. **`ROWS FROM` is a clause, not part of the rule.** §3.

### Rejected approaches

- **A `Clause` field, `defines_names: bool`.** There is no clause at `WITH x (`
  or `AS u (` to hang it on. The construct is precisely the *absence* of one —
  a paren that no clause claimed — which is why the rule keys on the alias word
  the clause already declares.
- **Matching the literal words `AS`, `ROWS`, `FROM` in the predicate.** It
  passed 20 of 20 prototyped cases and puts SQL vocabulary in `engine/`, which
  `tests/test_purity.py` exists to keep out in the other direction and which
  would silently do nothing for a dialect spelling its alias word differently.
  Reading `Clause.aliases_with` costs nothing and is the same answer.
- **Suppressing every paren the analyser cannot explain.** Would silence
  `INSERT INTO users (⌶`, `generate_series(⌶`, `IN (⌶` and a dozen more. The
  fifteen negatives in §4 are the reason the rule is narrow.

---

## 2. The predicate

`engine/analyse.py` gains one function:

```python
def opens_a_name_list(tokens: Sequence[Token], caret: int, clause: str | None, clauses: ClauseModel) -> bool:
    """Whether the paren the caret sits in opens a list of names being defined."""
```

It finds the caret's group with the existing `_group_start`, takes the word
immediately left of the opening paren and the word before that, and decides:

**When the governing clause declares `opens_a_group`** — `WITH`, `WINDOW` — the
clause has already said what its group contains, so the only question is whether
this paren *is* that group. It is exactly when the clause's `aliases_with` word
introduces it:

    WITH x AS (⌶     the alias word introduces it   → the body
    WITH x (⌶        it does not                    → a name list

**When the governing clause declares no group** — `FROM`, `JOIN` — a paren
introduced by the alias word, or by a name that the alias word introduced, is a
list of names being defined:

    FROM f(1) AS (⌶      AS introduces it directly
    FROM f(1) AS t (⌶    AS introduced the name that introduces it
    FROM users AS u (⌶   the same shape, column aliases rather than definitions

Both branches read `Clause.aliases_with` rather than the letters `AS`. A dialect
that aliases with another word gets the same behaviour, and `engine/` learns no
SQL vocabulary — the direction of dependency `tests/test_purity.py` enforces.

### Wiring it in

The predicate is proven; the insertion point is not, and the plan measures it
rather than assuming.

What these cases assert is a *refusal* — no `users`, no `orders`, no `SELECT` —
so both halves of the answer have to go quiet: the keyword list and the kinds.
Returning early from the keyword helper suppresses only the first, and
`Kind.TABLE` is decided elsewhere in `request.py`. The requirement is therefore:

> at a caret where `opens_a_name_list` holds, `derive_request` yields no kinds
> and no keywords.

Whether that is one early return or two depends on where the kinds are settled,
which the plan establishes by reading `request.py` and measuring the five cases,
not by this document guessing.

### Why the token left of the paren is enough

`FROM f(⌶` has an identifier there too and is a function call, which is why the
first branch is keyed on the clause and not on the shape. Every other
parenthesis in SQL is introduced either by a keyword that is not the alias word,
by punctuation, or by a name in a clause that declares a group — and each of
those falls out of the two branches without a special case. §4 lists the fifteen
that were checked.

---

## 3. `ROWS FROM` is a clause

The fifth caret is not the rule's business. `[ LATERAL ] ROWS FROM( f(), g() )`
is Postgres spelling, and putting `ROWS` and `FROM` in the predicate is exactly
the vocabulary-in-`engine/` that §2 refuses.

It also deserves better than silence. The grammar puts a *function* there, which
a catalog can answer:

```python
Clause(name='ROWS FROM', follows=frozenset({'FROM', 'JOIN'}), opens_an_item=True, suggests=(Kind.FUNCTION,)),
```

So this case stops being a refusal and becomes an expectation: its `refused`
reason is deleted and `offers` names a function the fixture provides. Recorded
because the previous design called `ROWS FROM` "exotica the position must stay
silent for", and modelling it turned out to cost one clause.

---

## 4. Testing

The five conformance cases are the acceptance test. Beside them,
`tests/test_analyse_prefix.py` gains unit tests for the predicate — the five
positives and the fifteen negatives already prototyped:

`WITH x AS (⌶`, `generate_series(⌶`, `count(⌶`, `IN (⌶`, `FROM (⌶`, `WHERE (⌶`,
`GROUP BY (⌶`, `GROUP BY ROLLUP (⌶`, `DISTINCT ON (⌶`, `USING (⌶`,
`INSERT INTO users (⌶`, `WINDOW w AS (⌶`, `TABLESAMPLE BERNOULLI (⌶`,
`SELECT (⌶`, `WHERE id = (⌶`.

The negatives carry the weight. Four of them are positions that answer *well*
today and would be silenced by a broader rule: `INSERT INTO users (⌶` offers
columns, `generate_series(⌶` offers arguments, `USING (⌶` offers join columns,
`IN (⌶` offers values.

This is a change in `engine/`, so the guard is the whole suite rather than the
five cases — 1279 tests, of which `tests/queries/` and `tests/corpus/` exercise
parenthesised constructs heavily.

---

## 5. What this leaves

**Twelve pending cases**, none of them a wrong answer:

- **Four withdrawn deliberately** — `UNION DISTINCT`, the bare `TABLE` form, the
  PG 14 join alias, `WITH ORDINALITY`. Each was implemented and reverted, and
  each carries its reason.
- **Three `TABLE` cases**, blocked on `CREATE TABLE` being modelled so the
  longer form wins the match. That is gap 1 in `docs/gaps.md`.
- **Five needing a capability that does not exist** — a `Kind` meaning "a
  relation this statement already has" (`FOR UPDATE OF`), an operator outside a
  predicate clause (`ORDER BY … USING`), `MATERIALIZED`, and
  `FROM ⌶ → ONLY | LATERAL`, which `resolve.py:598` filters out on purpose.

---

## 6. Documentation

- `CHANGELOG.md`: the five carets, under the existing "Wrong answers that are
  now right" heading, which this release already has.
- `docs/gaps.md`: no new entry; the suite remains the list for this territory.
- The `refused` reason on `ROWS FROM(⌶` is deleted rather than reworded — the
  production is modelled now, so the field would be false.

---

## 7. Open questions carried forward

- **`ClickHouse` and `Trino` inherit the predicate**, since it lives in
  `engine/`. Both declare `aliases_with='AS'` on their relation clauses, so the
  behaviour should be identical; neither has conformance cases to prove it, and
  the unit tests are Postgres-only. A second synopsis is the answer, not more
  unit tests.
- **`WITH x (a, b) ⌶`** — after the column list closes, before `AS`. Not in the
  suite and not measured; worth a case whichever way it currently answers.
