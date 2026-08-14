# The shared cases run everywhere — design

Thirty-eight of the conformance suite's cases describe behaviour ClickHouse and
Trino share with Postgres, and nothing runs them there. This runs them.

Fourth in the SELECT-grammar sequence. The first built the suite, the second
closed the gaps expressible in dialect data, the third silenced the parens that
define names — and the second and third are why this one is needed.

---

## 1. Context

### What was shipped unproven

This sequence put four productions into `ansi.py` and one predicate into
`engine/`. All five reach ClickHouse and Trino, and the suite that justified
them covers only Postgres:

| shipped | reaches |
| --- | --- |
| the `FETCH { FIRST \| NEXT } … { ONLY \| WITH TIES }` tail | all three |
| `OFFSET n [ ROW \| ROWS ]` | all three |
| `RIGHT JOIN`, `FULL JOIN` in `_JOINS` | all three |
| `WINDOW` losing its kind, gaining `opens_a_group` | all three |
| `opens_a_name_list` in `engine/analyse.py` | all three |

`tests/test_dialect_clauses.py` was extended alongside them, and it asserts that
a `Clause` record holds a field. That is not the same as a caret answering: it
would pass unchanged if `derive_request` stopped consulting the clause at all.

### What the other two dialects actually answer

Measured on 2026-08-13, running every passing Postgres case against
`CLICKHOUSE` and `TRINO` with the suite's own fixture:

| | cases |
| --- | --- |
| hold on all three | 35 |
| hold on Postgres and Trino | 3 |
| Postgres only | 18 |

The eighteen are Postgres's own — `LATERAL`, `ROWS FROM`, the `FOR UPDATE`
family, the `GROUP BY` grouping words, `DISTINCT ON`, `LIMIT ALL`,
`SEARCH`/`CYCLE`, `ORDER BY … USING`. The three divide where the dialects do:
Trino declares `TABLESAMPLE` and ClickHouse does not, so `FROM t ⌶`,
`TABLESAMPLE ⌶` and `REPEATABLE (⌶` hold on one and not the other.

Nothing in that table is a surprise, which is the point — it is a baseline
worth freezing before it stops being true.

### Decisions taken during brainstorming

1. **The shared cases run on every dialect they name**, rather than a second
   synopsis per backend. §2.
2. **Declared, not derived.** §2.
3. **Only a passing case may name more than one dialect.** §4.

### Rejected approaches

- **A `clickhouse.txt` synopsis and its own case list.** The right answer
  eventually, and it does not close *this* debt: what shipped unproven is the
  shared baseline, and a ClickHouse-specific suite would cover `PREWHERE`,
  `ARRAY JOIN` and `SAMPLE` while leaving `FETCH` on ClickHouse as untested as
  it is now. Roughly the size of the original Postgres suite, and worth its own
  decision once this is in.
- **Deriving the marking by running everything everywhere.** It writes itself
  and it cannot fail: a dialect that stops answering would be recorded as a
  dialect that never did. The marking has to be a claim to be worth testing.
- **A separate case list per dialect, sharing the record.** Three files
  restating the same `sql` and `offers`, and three chances to edit one and
  forget the others. The field is smaller than the duplication it avoids.

---

## 2. The field

`GrammarCase` gains one:

```python
    dialects: tuple[str, ...] = ('postgres',)
    """
    Which backends this case must hold on. Postgres alone by default.

    Declared rather than derived. Running every case against every dialect and
    recording what passes would absorb a regression as though it were a
    decision — the value of naming them is that a case marked shared and newly
    failing is a backend losing behaviour nothing else covers.

    Postgres is the default because the synopsis is Postgres's. A case naming
    another dialect is claiming the production is not Postgres's alone, which is
    a claim about SQL rather than about this repository, so it is made
    explicitly and one case at a time.
    """
```

The default keeps all 68 existing cases meaning exactly what they meant.

### The marking

From the measurement in §1: 35 cases take
`dialects=('postgres', 'clickhouse', 'trino')`, three take
`('postgres', 'trino')`, and the remaining 30 — 18 Postgres-specific and 12
pending — keep the default.

---

## 3. The runner

`test_grammar_position` parametrizes over `(case, dialect)` pairs rather than
cases. 68 cases become 106 runs. The dialect is looked up in the same
`DIALECTS` mapping `tests/test_golden_requests.py` already uses, so a typo in a
name fails rather than silently skipping — the trap that mapping exists to
avoid.

The fixture does not change. `MemoryCatalog` is dialect-neutral, and the
measurement above ran against it on all three backends without adjustment:
`public.users` and `public.orders` resolve identically, because a two-level
namespace is what all three dialects declare.

The test id gains the dialect, so a failure names the backend it failed on
rather than leaving the reader to infer it from a repeated case.

---

## 4. Two rules the data enforces

**Only a passing case may name more than one dialect.** A pending case marked
shared would `xfail` three times for one reason, and the burn-down would count
one gap as three. A data test asserts `not case.pending or len(case.dialects) == 1`.

**Every named dialect must exist.** `tests/test_corpus.py` already makes this
assertion about its own corpus, for the reason that a misspelt name silently
skips the case rather than failing it. The same test, over the same set.

---

## 5. `WITH x AS (⌶` is really two cases

The measurement put it in the Postgres-only bucket, correctly: it asserts
`SELECT`, `VALUES`, `INSERT INTO`, `UPDATE` and `DELETE FROM`, and the last
three are Postgres's data-modifying CTEs, which ClickHouse refuses outright.

But `SELECT` and `VALUES` hold on all three. Splitting the case moves that half
into the covered set:

```python
    GrammarCase(
        sql='WITH x AS (⌶',
        cite=_WITH_QUERY,
        offers=('SELECT', 'VALUES'),
        dialects=('postgres', 'clickhouse', 'trino'),
        note='the two body forms every backend has',
    ),
    GrammarCase(
        sql='WITH x AS (⌶',
        cite=_WITH_QUERY,
        offers=('INSERT INTO', 'UPDATE', 'DELETE FROM'),
        note="data-modifying CTEs, which are Postgres's; ClickHouse refuses the first outright",
    ),
```

Two cases at one caret, which the record already permits — several cases share
a `cite` today, and the parametrize id is built from both `cite` and `sql`.
Recorded here because it is the only case the measurement showed to be *mixed*
rather than shared or specific, and splitting it is what the measurement is for.

---

## 6. The burn-down

`tests/conftest.py` gains a second line under the grammar one:

```
grammar burn-down: 57/69 SELECT positions answered, 9 of the 12 gaps refused
  also holding: 36/36 on clickhouse, 39/39 on trino
```

The split in §5 adds a case that passes, so the numerator and the total both
rise by one and the twelve gaps are unchanged. The shared counts rise with it —
35 and 38 become 36 and 39, since the half that was mixed is now marked.

The denominators are the counts of cases naming each dialect, so both read
`n/n` while the suite is green — the number that moves is the numerator, and it
only moves down. A ratio rather than a bare count because the denominator is
the claim: it says how much of the baseline is asserted there at all.

The Postgres totals rise by one from the split in §5.

---

## 7. Testing

The suite is its own test. Beyond it:

- the two data rules in §4, one test each;
- `tests/test_dialect_clauses.py` keeps its assertions, which are about clause
  *records* and remain the right shape for what they check.

No integration tests and no containers. This asserts what the engine offers,
which is decided before any catalog is consulted; the containers settled which
productions belong in `ansi.py` and that question is closed.

---

## 8. Documentation

- `CHANGELOG.md`: under the existing `### Nothing changes at a caret` heading,
  since nothing does.
- `docs/gaps.md`: no entry. The suite is the list.

---

## 9. Open questions carried forward

- **A synopsis per dialect.** ClickHouse has six clauses with no conformance
  case at all — `PREWHERE`, `ARRAY JOIN`, `SAMPLE`, `LIMIT BY`, `SETTINGS`,
  `FINAL` — and Trino has three. This design covers the baseline they share
  with Postgres and says nothing about those.
- **`ansi` as a fourth dialect.** The mapping has it and no case names it.
  Every shared case should hold there too, since ANSI is what the other three
  compose from — worth measuring, and left out here because a case that names
  four dialects and fails on the baseline is a different investigation.
