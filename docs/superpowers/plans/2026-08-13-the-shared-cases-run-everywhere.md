# The Shared Cases Run Everywhere Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the thirty-eight conformance cases whose behaviour ClickHouse and Trino share with Postgres against those dialects, so the baseline this sequence put into `ansi.py` and `engine/` is asserted where it landed.

**Architecture:** `GrammarCase` gains a `dialects` field defaulting to `('postgres',)`, so every existing case keeps its meaning. The runner parametrizes over `(case, dialect)` pairs. The marking is the measurement in the spec written down — declared, never derived.

**Tech Stack:** Python 3.12, pytest, `uv`. No new dependencies, no containers.

## Global Constraints

- **Design doc:** `docs/superpowers/specs/2026-08-13-the-shared-cases-run-everywhere-design.md`. Read §2 and §4 before Task 1.
- **Nothing under `src/` changes.** This is test data and a test runner. A task that edits `src/pysqlsuggestions/` has gone wrong.
- **`./scripts/check.sh` must pass at the end of every task**: `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`.
- **Style:** single quotes, 120 columns, ruff `D` enabled — every function needs a docstring. mypy `strict` covers `tests/`.
- **Declared, never derived.** Do not write code that computes which dialects a case holds on. The marking is a claim; a computed one cannot fail.
- **Never mark a case shared to make something pass.** If a case fails on a dialect it names, either the dialect lost behaviour or the claim was wrong — both need a person, not an edit to the list.
- **Measured baseline:** the thirty-eight cases in Task 2 were measured against `CLICKHOUSE` and `TRINO` on 2026-08-13 with the suite's own fixture. Every one passed.
- **Commits:** `test:`/`docs:`, lowercase prose summary, body explaining the decision. End every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## File Structure

| Path | Change |
| --- | --- |
| `tests/grammar/cases.py` | the `dialects` field, two marking constants, 38 cases marked, one case split |
| `tests/test_grammar_select.py` | runner parametrizes over `(case, dialect)`; two data rules |
| `tests/conftest.py` | a second burn-down line |
| `CHANGELOG.md` | an entry under the existing heading |

---

### Task 1: The field and the runner

The machinery, with every case still defaulting to Postgres. Nothing is asserted
anywhere new, and Step 4 proves it.

**Files:**
- Modify: `tests/grammar/cases.py`
- Modify: `tests/test_grammar_select.py`

**Interfaces:**
- Consumes: `GrammarCase`, `CASES` from `tests/grammar/cases.py`; `ANSI`, `POSTGRES`, `CLICKHOUSE`, `TRINO` from `pysqlsuggestions.dialects.*`.
- Produces: `GrammarCase.dialects: tuple[str, ...]`, default `('postgres',)`; `DIALECTS: dict[str, Dialect]` in the runner.

- [ ] **Step 1: Add the field**

In `tests/grammar/cases.py`, add to `GrammarCase` after `refuses` and before `pending`:

```python
    dialects: tuple[str, ...] = ('postgres',)
    """
    Which backends this case must hold on. Postgres alone by default.

    Declared rather than derived. Running every case against every dialect and
    recording what passes would absorb a regression as though it were a
    decision — the value of naming them is that a case marked shared and newly
    failing is a backend losing behaviour nothing else covers.

    Postgres is the default because the synopsis is Postgres's. A case naming
    another dialect claims the production is not Postgres's alone, which is a
    claim about SQL rather than about this repository, so it is made explicitly
    and one case at a time.
    """
```

- [ ] **Step 2: Parametrize the runner over pairs**

In `tests/test_grammar_select.py`, replace `_params` and the `test_grammar_position` decorator. The existing versions read:

```python
def _params() -> list[object]:
    """Each case, marked xfail(strict=True) while it is still pending."""
    return [
        pytest.param(case, marks=pytest.mark.xfail(strict=True, reason=case.note or 'pending'))
        if case.pending
        else pytest.param(case)
        for case in CASES
    ]


@pytest.mark.parametrize('case', _params(), ids=[f'{c.cite[:40]} :: {c.sql}' for c in CASES])
def test_grammar_position(case: GrammarCase) -> None:
```

Replace both with:

```python
DIALECTS = {'ansi': ANSI, 'postgres': POSTGRES, 'clickhouse': CLICKHOUSE, 'trino': TRINO}
"""
Every dialect a case may name, by the name it names it with.

The same mapping `tests/test_golden_requests.py` keeps, and for the same reason:
a misspelt dialect looked up here raises, where a bare string compared against
the shipped dialects would silently match nothing and skip the case.
"""


def _pairs() -> list[tuple[GrammarCase, str]]:
    """Every (case, dialect) the suite runs, one per dialect a case names."""
    return [(case, name) for case in CASES for name in case.dialects]


def _params() -> list[object]:
    """Each pair, marked xfail(strict=True) while the case is still pending."""
    return [
        pytest.param(case, name, marks=pytest.mark.xfail(strict=True, reason=case.note or 'pending'))
        if case.pending
        else pytest.param(case, name)
        for case, name in _pairs()
    ]


@pytest.mark.parametrize(
    ('case', 'dialect'),
    _params(),
    ids=[f'{name}: {case.cite[:32]} :: {case.sql}' for case, name in _pairs()],
)
def test_grammar_position(case: GrammarCase, dialect: str) -> None:
```

and change the body's first two lines from

```python
    sql, caret = split_caret(case.sql)
    found = offered(sql, caret)
```

to

```python
    sql, caret = split_caret(case.sql)
    found = offered(sql, caret, DIALECTS[dialect])
```

Then give `offered` its dialect, replacing the existing definition:

```python
def offered(sql: str, caret: int, dialect: Dialect = POSTGRES) -> list[str]:
    """The suggestion texts at `caret` in `sql`, for `dialect`."""
    return [suggestion.text for suggestion in complete(sql, caret, dialect, catalog())]
```

Add the imports the new names need, beside the existing `POSTGRES` one:

```python
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.trino import TRINO
```

- [ ] **Step 3: Add the two data rules**

Append to the data-tests section of `tests/test_grammar_select.py`, beside
`test_every_case_asserts_something`:

```python
@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_every_named_dialect_exists(case: GrammarCase) -> None:
    """A misspelt name would skip the dialect rather than fail it, which is the worse failure."""
    assert set(case.dialects) <= set(DIALECTS)


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_only_a_passing_case_names_more_than_one_dialect(case: GrammarCase) -> None:
    """
    A pending case marked shared would xfail once per dialect for one reason.

    The burn-down would then count a single gap two or three times, and the
    reason printed beside each would be the same sentence. A case earns its
    other dialects by passing on Postgres first.
    """
    assert not case.pending or len(case.dialects) == 1
```

- [ ] **Step 4: Run the suite and confirm nothing moved**

Run: `uv run pytest tests/test_grammar_select.py -q 2>&1 | tail -4`
Expected: PASS with 12 xfailed, and the burn-down still reading
`56/68 SELECT positions answered`. Every case still names only Postgres, so the
pair count equals the case count and the run is the same one as before.

- [ ] **Step 5: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add tests/grammar/cases.py tests/test_grammar_select.py
git commit -F - <<'EOF'
test: a grammar case says which dialects it must hold on

`GrammarCase` gains `dialects`, defaulting to Postgres alone, and the
runner parametrizes over (case, dialect) pairs rather than cases.

Nothing is marked yet, so the pair count equals the case count and this
run is the same run as before — which is the point of separating the
machinery from the claim it will carry.

Two data rules arrive with the field. A named dialect must exist, because
a misspelt one would skip rather than fail; and only a passing case may
name more than one, because a pending case marked shared would xfail once
per dialect for a single reason and the burn-down would count that gap
two or three times.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: The marking

The measurement written down. This is where the claim is made.

**Files:**
- Modify: `tests/grammar/cases.py`

**Interfaces:**
- Consumes: `GrammarCase.dialects` from Task 1.
- Produces: `_EVERY_DIALECT` and `_POSTGRES_AND_TRINO` constants.

- [ ] **Step 1: Add the two constants**

In `tests/grammar/cases.py`, below `UNCITED`:

```python
_EVERY_DIALECT = ('postgres', 'clickhouse', 'trino')
"""
A production all three shipped backends have, at a caret all three answer alike.

Measured before it was claimed — every case carrying this passed against
`CLICKHOUSE` and `TRINO` when it was added. That is the baseline, and a case
here going red means a dialect lost behaviour no other test covers.
"""

_POSTGRES_AND_TRINO = ('postgres', 'trino')
"""
Trino declares `TABLESAMPLE` and ClickHouse does not, which is the whole of it.

Three cases divide here, and they are the reason `dialects` is a tuple rather
than a boolean: `shared` would have had to mean "all of them" and these are not.
"""
```

- [ ] **Step 2: Mark the thirty-five**

Add `dialects=_EVERY_DIALECT,` to each of these cases, after its `refuses` line
where it has one and after `offers` otherwise. Match on the `sql=` value:

```
'⌶'
'WITH rec⌶'
'WITH x ⌶'
'WITH x (⌶'
'SELECT ⌶'
'SELECT dis⌶'
'SELECT id, ⌶'
'SELECT DISTINCT ON (⌶'
'SELECT id ⌶'
'SELECT * FROM ONLY ⌶'
'SELECT * FROM users AS u (⌶'
'WITH x AS (SELECT 1) SELECT * FROM x ⌶'
'SELECT * FROM generate_series(1, 2) AS t (⌶'
'SELECT * FROM generate_series(1, 2) AS (⌶'
'SELECT * FROM users u ⌶'
'SELECT * FROM users u JOIN orders o ⌶'
'SELECT * FROM users u JOIN orders o ON ⌶'
'SELECT * FROM users u NATURAL ⌶'
'SELECT * FROM users u CROSS ⌶'
'SELECT * FROM users WHERE ⌶'
'SELECT * FROM users GROUP BY ⌶'
'SELECT * FROM users GROUP BY (⌶'
'SELECT * FROM users GROUP BY ROLLUP (⌶'
'SELECT * FROM users GROUP BY CUBE (⌶'
'SELECT * FROM users GROUP BY GROUPING SETS (⌶'
'SELECT * FROM users GROUP BY id HAVING ⌶'
'SELECT * FROM users WINDOW ⌶'
'SELECT * FROM users WINDOW w AS (⌶'
'SELECT * FROM users ORDER BY ⌶'
'SELECT * FROM users ORDER BY id ASC ⌶'
'SELECT * FROM users LIMIT ⌶'
'SELECT * FROM users OFFSET 10 ⌶'
'SELECT * FROM users FETCH ⌶'
'SELECT * FROM users FETCH FIRST 10 ⌶'
'SELECT * FROM users FETCH FIRST 10 ROWS ⌶'
```

- [ ] **Step 3: Mark the three**

Add `dialects=_POSTGRES_AND_TRINO,` to these, and extend each note to say why —
the reason is the same for all three and belongs beside each rather than in one
of them:

```
'SELECT * FROM users ⌶'
'SELECT * FROM users TABLESAMPLE ⌶'
'SELECT * FROM users TABLESAMPLE BERNOULLI (10) REPEATABLE (⌶'
```

For the first, whose note is currently about `AS` and `TABLESAMPLE`:

```python
        note='ClickHouse has no TABLESAMPLE clause, so this caret offers less there and the case does not name it',
```

For the other two, append the same clause to the existing note text.

- [ ] **Step 4: Split `WITH x AS (⌶`**

Replace the single case with two. The current one asserts all five body forms;
the first two hold everywhere and the last three are Postgres's:

```python
    GrammarCase(
        sql='WITH x AS (⌶',
        cite=_WITH_QUERY,
        offers=('SELECT', 'VALUES'),
        dialects=_EVERY_DIALECT,
        note='the two body forms every backend has',
    ),
    GrammarCase(
        sql='WITH x AS (⌶',
        cite=_WITH_QUERY,
        offers=('INSERT INTO', 'UPDATE', 'DELETE FROM'),
        note="data-modifying CTEs, which are Postgres's; ClickHouse refuses the first outright",
    ),
```

Two cases at one caret is already permitted — several cases share a `cite`, and
the parametrize id is built from the dialect, the cite and the sql, so the two
are distinguishable in output.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py -q 2>&1 | tail -4`
Expected: PASS, 12 xfailed, and the burn-down reading
`57/69 SELECT positions answered`. The pair count is now 144 — 69 Postgres runs,
36 ClickHouse and 39 Trino, the last being the 36 shared plus the three
`TABLESAMPLE` cases.

**A failure here is a finding, not an obstacle.** Every marking in Steps 2 and 3
was measured. If one fails, either the measurement was stale or a dialect has
changed since — read the failure before touching the list, and never remove a
dialect from a case to make the run green.

- [ ] **Step 6: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add tests/grammar/cases.py
git commit -F - <<'EOF'
test: mark the thirty-eight cases the other dialects share

Measured, not judged: of the fifty-six passing cases, thirty-five hold on
ClickHouse and Trino unchanged, three hold on Trino alone because it
declares TABLESAMPLE and ClickHouse does not, and eighteen are Postgres's
own — LATERAL, ROWS FROM, the FOR UPDATE family, the grouping words,
DISTINCT ON, LIMIT ALL, SEARCH and CYCLE, ORDER BY USING.

This is what was shipped unproven. The FETCH tail, OFFSET's noise words,
RIGHT and FULL JOIN, WINDOW losing its kind and the name-list predicate
all reach ClickHouse and Trino, and until now the only thing asserting
them there checked that a Clause record held a field.

`WITH x AS (⌶` splits, being the one case the measurement showed to be
mixed: SELECT and VALUES hold everywhere, the data-modifying CTEs are
Postgres's and ClickHouse refuses the first outright.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: The burn-down and the changelog

**Files:**
- Modify: `tests/conftest.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `CASES as GRAMMAR_CASES`, already imported in `conftest.py`.
- Produces: nothing.

- [ ] **Step 1: Add the second line**

In `tests/conftest.py`, after the existing `grammar burn-down` line:

```python
    shared = sorted({name for case in GRAMMAR_CASES for name in case.dialects} - {'postgres'})
    if shared:
        counts = ', '.join(f'{sum(1 for c in GRAMMAR_CASES if name in c.dialects)} on {name}' for name in shared)
        terminalreporter.write_line(f'  also holding: {counts}')
```

A count rather than a ratio, because every case naming a dialect passes on it —
the number is how much of the baseline is asserted there at all, and a case that
stopped holding would fail the run rather than move this figure.

- [ ] **Step 2: Run the whole suite and read the summary**

Run: `uv run pytest -m 'not integration' -q 2>&1 | tail -6`
Expected: PASS, and among the summary lines:

```
grammar burn-down: 57/69 SELECT positions answered, 9 of the 12 gaps refused
  also holding: 36 on clickhouse, 39 on trino
```

- [ ] **Step 3: Write the changelog entry**

`CHANGELOG.md` has a `### Nothing changes at a caret` heading under
`## Unreleased`. Append to that section:

```markdown
The conformance suite runs on more than Postgres. Thirty-eight of its cases
describe behaviour ClickHouse and Trino share, and a `dialects` field on each
case says so — the `FETCH` tail, `OFFSET`'s noise words, `RIGHT` and `FULL
JOIN`, `WINDOW`'s definition body, and the rule that a parenthesis naming
columns answers nothing.

All five of those were added to the shared baseline in this release and reached
those two backends with nothing asserting them there. The marking is measured
rather than assumed: every case naming a dialect was run against it first.

Three cases name Trino and not ClickHouse, which declares no `TABLESAMPLE`, and
eighteen name Postgres alone because the productions are Postgres's — `LATERAL`,
`ROWS FROM`, the `FOR UPDATE` family, the grouping words, `DISTINCT ON`,
`LIMIT ALL`, `SEARCH` and `CYCLE`, `ORDER BY … USING`.
```

- [ ] **Step 4: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add tests/conftest.py CHANGELOG.md
git commit -F - <<'EOF'
test: report how much of the baseline each dialect asserts

A count rather than a ratio. Every case naming a dialect passes on it, so
a ratio would read n/n forever; the number worth printing is how much of
the shared baseline is asserted there at all, and a case that stopped
holding fails the run rather than moving this line.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

## Notes for the implementer

- **Task 1 changes no behaviour and Step 4 proves it.** Every case still names Postgres alone, so the run is identical to the one before. If the burn-down moves there, something other than this plan did it.
- **Never remove a dialect from a case to make the suite green.** The marking is the claim being tested. A case failing on a dialect it names means that dialect lost behaviour nothing else covers, and that is the finding this whole plan exists to surface.
- **The three `TABLESAMPLE` cases are the only asymmetry**, and it is Trino's clause rather than a Postgres one. If a fourth case ever needs `_POSTGRES_AND_TRINO`, check whether it is really the same reason before reusing the constant.
- **Twelve cases remain pending**, unchanged by this plan — the marking only touches cases that pass.
