# `WITH` answers its positions — design

Second of three slices clearing the recorded debts. The third — relation-kind
filtering finer than "not a sequence" — is independent and gets its own spec.

`WITH` declares `suggests=()`, no `followed_by`, and nothing declares it
`follows`. So `ClauseModel.continuations('WITH')` is empty, `_clause_kinds`
returns early, and every one of the clause's five caret positions answers with
nothing — including the two where exactly one thing can legally come next.

---

## 1. Context

### What each position does today, and what it should do

Measured against the engine, not inferred. `expecting` and `item_words` are the
two signals the model already carries at each caret:

| caret | `expecting` | `item_words` ∋ `AS` | today | should be |
|---|---|---|---|---|
| `WITH ⌶` | operand | — | nothing | nothing — a name to invent |
| `WITH rec⌶` | operand | — | nothing | `RECURSIVE` |
| `WITH a ⌶` | connective | no | nothing | `AS` |
| `WITH a AS (⌶` | operand, depth 1 | — | nothing | what a CTE body may begin with |
| `WITH a AS (…) ⌶` | connective | **yes** | nothing | what may follow the CTE list |
| `WITH a AS (…), ⌶` | operand | — | nothing | nothing — a name to invent |

Two of the six are already correct by accident: `expecting == 'operand'` returns
the clause's `suggests`, which is empty. They must stay correct.

### What the servers accept

Every row run against `docker/docker-compose.yml`.

**Postgres** — all of these plan:

- `WITH a AS (SELECT 1 AS x) SELECT * FROM a`
- `WITH a AS (VALUES (1)) SELECT * FROM a`
- `WITH a AS (INSERT INTO … RETURNING id) SELECT * FROM a`
- `WITH a AS (UPDATE … RETURNING id) SELECT * FROM a`
- `WITH a AS (DELETE FROM … RETURNING id) SELECT * FROM a`
- `WITH a AS (WITH b AS (…) SELECT …) SELECT * FROM a` — a **nested** `WITH`
- `WITH RECURSIVE a AS (…) SELECT * FROM a`
- `WITH a AS MATERIALIZED (…) SELECT * FROM a`
- After the list: `SELECT`, `INSERT INTO`, `UPDATE`, `DELETE FROM`, and
  `WITH a AS (SELECT 1) VALUES (1)`

**ClickHouse** — `WITH x AS (SELECT 1) SELECT * FROM x` and
`WITH RECURSIVE …` work; `WITH x AS (INSERT INTO t VALUES (1)) …` is
`Syntax error: failed at position 12`. It also has a scalar form,
`WITH 1 AS x SELECT x`, where the expression comes first and the name second —
the reverse of the standard.

**Trino** — `SELECT` and `VALUES` in a body, and `WITH RECURSIVE x(n) AS (…)`.

So the body list and the follows list are **not the same**: a nested `WITH` is
legal inside a body and not after one.

### Decisions taken during brainstorming

1. **One new `Clause` field** for the body position, rather than reusing
   `followed_by`. See §3.
2. **ANSI stays conservative** — `SELECT` and `VALUES` only. The
   data-modifying CTEs are a Postgres extension and ClickHouse rejects them.

### Rejected approaches

- **Reusing `followed_by` for both positions.** It would offer `AS` inside the
  body and offer the body's words after a written name, and the two lists
  genuinely differ on nested `WITH`.
- **A `WITH`-specific branch in `_clause_kinds`**, in the shape of the existing
  `INSERT INTO` + `inside_a_group` check. It would work, and it would put a
  dialect's grammar in the engine — where a third-party dialect could not
  reach it.
- **Modelling ClickHouse's scalar `WITH 1 AS x`.** A different feature: the
  expression precedes the name, so the caret after `WITH ` means something else
  there. Silence is right for both readings, and is what both get.

---

## 2. Scope

### In

- `Clause.opens_a_group`, folded into `Dialect.keywords` like its neighbours.
- One rule in `_continues` reading it.
- `WITH` declaring `followed_by`, `aliases_with`, `before_the_item` and
  `opens_a_group` in ANSI; Postgres extending the two lists.
- `recursive` in ANSI's reserved set.

### Out, deliberately

- **ClickHouse's scalar `WITH`.** See above.
- **`MATERIALIZED` / `NOT MATERIALIZED`.** Postgres-only, and it sits between
  `AS` and the `(` — a seventh position, and the rarest of them.
- **A CTE's own column list**, `WITH a (x, y) AS (…)`. Legal in all three, and
  the names in it are the author's to invent, so the position is silent either
  way.
- **`INSERT INTO`'s column list**, which is the same idea as `opens_a_group`
  and is currently a hard-coded clause-name check in `request.py`. Converting
  it is a tidy-up with no behaviour change, and belongs to whoever next has a
  reason to touch that branch.

---

## 3. The new field

```python
    opens_a_group: tuple[str, ...] = ()
    """
    Words that may begin this clause's parenthesised group.

    `WITH a AS (<caret>` is inside the clause and is not the clause's own
    position: what belongs there is a whole statement, and what belongs after
    the group is a different list — a nested `WITH` is legal in a CTE body and
    not after one. `followed_by` cannot serve both without offering `AS` inside
    the body and the body's words after a written name.
    """
```

It is a `Clause` field rather than an engine rule so a third-party dialect can
declare it. `Dialect.__post_init__` folds it into `keywords` alongside
`followed_by` and `after_operand`, so a dialect adding one cannot leave its own
words unrecognised — the failure that rule exists to prevent.

**A second consumer is already visible.** `CREATE TABLE t (⌶` — gap 1 — is the
same shape, and `INSERT INTO`'s column list is the same idea written as a
clause-name check in `request.py`. The field is being added for one caller and
is not speculative.

---

## 4. Where it plugs in

`_continues`, the existing seam for "words that finish the construct under the
caret, and they are the whole answer". One condition: the governing clause
declares `opens_a_group` and the caret is at depth.

Nothing guards against the body already having content, because nothing needs
to: once a word is typed there, `clause_at` reports *that* clause instead —
`WITH a AS (SELECT ⌶` already reports `SELECT`, measured. The rule can only fire
where the group is still empty.

Returning through `continues` rather than through `_clause_kinds` means
`local_candidates` emits them, which is where every other "these words are the
whole answer" case is already served. No change to `_keywords` or
`_clause_kinds`.

---

## 5. The other four positions

`WITH` gains three ordinary declarations and one reserved word:

- **`followed_by`** — what may come after the CTE list. `AS` is in it, which is
  what serves `WITH a ⌶`.
- **`aliases_with='AS'`** — so `_unspent_alias` drops `AS` once it is in
  `item_words`. That is the whole of what separates `WITH a ⌶` from
  `WITH a AS (…) ⌶`, and it is machinery that already exists for `FROM t AS x`.
- **`before_the_item=('RECURSIVE',)`** — precisely what that field means, and
  the same treatment `DISTINCT` gets after `SELECT`. It surfaces behind a
  prefix only, which is right: `RECURSIVE` is rare and a CTE name is what
  usually follows `WITH`.
- **`recursive` in ANSI's `RESERVED`.** Without it the analyser reads the word
  as a CTE name, `WITH RECURSIVE ⌶` looks exactly like `WITH a ⌶`, and `AS` is
  offered where a name belongs. Trino already reserves it; ANSI and Postgres do
  not.

---

## 6. Dialect differences

| | body (`opens_a_group`) | after the list (`followed_by`) |
|---|---|---|
| ANSI | `SELECT`, `VALUES`, `WITH` | `AS`, `SELECT` |
| Postgres | + `INSERT INTO`, `UPDATE`, `DELETE FROM` | + `INSERT INTO`, `UPDATE`, `DELETE FROM` |
| ClickHouse, Trino | inherited | inherited |

**`VALUES` is in the body list and not the follows list**, and that asymmetry is
not a judgement — it is what the clause model does. `VALUES` declares
`statements={'INSERT INTO'}`, and at `WITH a AS (…) ⌶` the statement form is
reported as `WITH`, so `ClauseModel.continuations` filters it out. Both facts
verified rather than reasoned about.

`WITH a AS (…) VALUES (1)` does plan, so this is a missing answer. Making it
appear would mean widening `VALUES.statements` to include `WITH` — a change to
`INSERT INTO`'s model to reach a caret almost nobody types. Left alone, and
recorded here so the next reader does not think it was overlooked.

The body list does not go through that filter: the rule in §4 returns
`opens_a_group` directly, which is why `VALUES` survives there — and it matters
there, since a `VALUES` body is the ordinary way to write a literal table.

ClickHouse inherits the conservative list because it refuses `INSERT` in a body,
and that refusal is the reason the extension lives in Postgres rather than in
the baseline.

---

## 7. Testing

### Unit

One file, `tests/test_cte_positions.py`, with a test per row of §1's table —
including the two that must keep answering nothing, which are the regression
half of this slice. Plus the dialect split: Postgres offers `INSERT INTO` in a
body and ClickHouse does not.

### Golden corpus

`WITH a AS (⌶` joins `tests/corpus/cases.py`, asserting `kinds=('keyword',)`
and `clause='WITH'`. It is a request-level assertion and touches no server.

### Conformance

One case, built from what the dialect declares: a dialect with `opens_a_group`
on any clause must answer inside that clause's group. A dialect declaring none
is skipped rather than failed — the bargain `parameter()` and `relation_search`
already make.

### No integration test, deliberately

The server-plan tests in the last two slices earned their place by settling
something in doubt — whether a sequence literal keeps its identifier quotes,
whether a qualified reference resolves. Nothing here is in doubt: accepting
`SELECT` at `WITH a AS (⌶` produces `WITH a AS (SELECT`, which is not a
statement, so a test would have to hand-assemble the rest — and would then be
testing that hand-assembly rather than the suggestion.

What a server could settle is *which words belong in a body*, and that is
already settled: §1 lists eight `EXPLAIN` results run against Postgres and the
ClickHouse refusal, and those measurements are what §6's table is built from. A
test would re-run them; the spec records them.

---

## 8. Documentation

- `CHANGELOG.md`: an entry under Unreleased.
- `docs/gaps.md`: **no change.** The CTE-body silence was never listed there —
  checked while writing this, having first cited a test by the wrong name. It
  lives in `tests/test_statement_forms.py`'s
  `test_a_parenthesised_position_is_not_reached_by_the_rule`, whose docstring
  says the position "has never had an answer. A separate gap, named here so the
  next reader does not mistake it for this one."

  That test's own subject is different — it pins that a parenthesised position
  is not silenced by the unmodelled-statement refusal — so the `WITH a AS (`
  line is updated to the new answer and the paragraph naming the gap comes out,
  while the test itself and its `SELECT * FROM (` assertion stay as they are.

---

## 9. Open questions carried forward

- **`INSERT INTO`'s column list** as an `opens_a_group` user, replacing the
  clause-name check in `request.py`.
- **Relation kinds finer than "not a sequence"** — the third debt.
