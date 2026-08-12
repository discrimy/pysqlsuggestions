# `WITH` Answers Its Positions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each of `WITH`'s five caret positions answers with what belongs there,
including the two whose right answer is nothing.

**Architecture:** One new `Clause` field, `opens_a_group`, names the words that
may begin a clause's parenthesised body, read by one rule in `_continues`. The
other four positions need no new mechanism — `expecting` and `item_words`
already tell them apart, and `WITH` simply has to declare `followed_by`,
`aliases_with` and `before_the_item` like any other clause.

**Tech Stack:** Python 3.10+, no runtime dependencies. `uv run pytest`,
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.

## Global Constraints

- **Python 3.10 floor.** No `match`, no `X | Y` in `isinstance`, no starred
  expression directly inside a subscript.
- **Zero runtime dependencies.** Standard library only in `src/`.
- **Line length 120, single quotes.** `ruff format` decides.
- **Docstrings required** (`ruff` rule set `D`) on every public module, class,
  function and method, including new dataclass fields, which this codebase
  documents individually.
- **`engine/` may not import `ports` or `resolve`** — `tests/test_purity.py`
  enforces it. Nothing here needs to.
- **A dialect is data composed with `dataclasses.replace`,** never a subclass,
  and a dialect's grammar belongs in the dialect rather than in the engine.
- **ANSI is the conservative baseline.** A form only one shipped backend
  accepts belongs to that backend.
- **Every task ends green.** `uv run pytest`, `ruff check`,
  `ruff format --check` and `mypy` all clean before the commit.
- **Two positions must keep answering nothing.** `WITH ⌶` and
  `WITH a AS (…), ⌶` are a name the author invents. Silence there is correct and
  is the regression half of this slice.

---

## File Structure

| file | change |
|---|---|
| `src/pysqlsuggestions/dialects/base.py` | `Clause.opens_a_group`; `__post_init__` folds it |
| `src/pysqlsuggestions/engine/request.py` | one rule in `_continues` |
| `src/pysqlsuggestions/dialects/ansi.py` | `WITH` declares four things; `recursive` reserved |
| `src/pysqlsuggestions/dialects/postgres.py` | `WITH` extended with the data-modifying forms |
| `src/pysqlsuggestions/testing/__init__.py` | one conformance case |
| `tests/test_cte_positions.py` | new — one test per position |
| `tests/test_statement_forms.py` | one line and one paragraph updated |
| `tests/corpus/cases.py`, `tests/test_conformance.py`, `CHANGELOG.md` | as described |

**Two facts verified while writing this plan**, both of which the tasks depend
on:

1. At `WITH a AS (SELECT ⌶` the governing clause is already reported as
   `SELECT`, not `WITH`. That is why Task 1's rule needs no "is the group still
   empty" guard — it cannot fire once anything is typed.
2. At `WITH a AS (…) ⌶` the statement form is reported as `WITH`, and `VALUES`
   declares `statements={'INSERT INTO'}`. So `VALUES` in `followed_by` would be
   filtered out and never appear. It is deliberately absent there.

---

## Task 1: a clause can say what begins its group

**Files:**
- Modify: `src/pysqlsuggestions/dialects/base.py` (`Clause`, `Dialect.__post_init__`)
- Modify: `src/pysqlsuggestions/engine/request.py` (`_continues`)
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (the `WITH` clause)
- Modify: `tests/test_statement_forms.py`
- Test: `tests/test_cte_positions.py` (new)

**Interfaces:**
- Produces: `Clause.opens_a_group: tuple[str, ...] = ()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cte_positions.py`:

```python
"""
The five carets a `WITH` clause has, and what belongs at each.

Two of them answer nothing and are right to: a CTE name is the author's to
invent, and no engine can guess it. The other three had no answer either, which
is what this fixes — `WITH` declared no `suggests`, no `followed_by`, and
nothing declared it `follows`, so its continuations were empty and every
position fell through.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {('public', 'auth_user'): [('id', 'bigint'), ('email', 'varchar')]}


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, MemoryCatalog(SNAPSHOT))]


def test_a_cte_body_offers_the_statements_it_may_contain() -> None:
    """
    Every one of these plans on Postgres — the data-modifying CTEs and the
    nested `WITH` included — so this is the position's whole answer rather than
    a selection from it.
    """
    found = offered('WITH a AS (')
    assert 'SELECT' in found
    assert 'VALUES' in found
    assert 'WITH' in found


def test_the_body_offers_nothing_else() -> None:
    """
    A whole statement belongs there and nothing smaller. Offering a column or a
    relation would propose `WITH a AS (auth_user`, which parses as nothing.
    """
    assert 'auth_user' not in offered('WITH a AS (')
    assert 'id' not in offered('WITH a AS (')


def test_a_typed_body_belongs_to_the_statement_in_it() -> None:
    """
    The rule cannot fire twice: once a word is typed the governing clause is
    that statement's, not `WITH`'s, so the body's word list is unreachable and
    needs no guard against being offered again.
    """
    assert 'auth_user' in offered('WITH a AS (SELECT * FROM ')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cte_positions.py -v`
Expected: the first FAILS — `WITH a AS (` offers nothing. The second and third
pass and must keep passing; the second is the guard that the new rule does not
open the position to columns and relations.

- [ ] **Step 3: Add the field**

In `src/pysqlsuggestions/dialects/base.py`, add to `Clause` after
`before_the_item`:

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

- [ ] **Step 4: Fold it into the keyword set**

In `Dialect.__post_init__`, add it to the phrases gathered:

```python
        spoken = {
            word.upper()
            for clause in self.clauses.clauses
            for phrase in (clause.name, *clause.followed_by, *clause.after_operand, *clause.opens_a_group)
            for word in phrase.split()
        }
```

Without this a dialect declaring a word here would leave it unrecognised by the
analyser — the exact failure the existing comment on that method describes.

- [ ] **Step 5: Read it in `_continues`**

In `src/pysqlsuggestions/engine/request.py`, in `_continues`, add directly after
the `before_the_item` block:

```python
    # The words a clause's parenthesised group may begin with — a CTE body.
    # No guard against the group already having content is needed: once a word
    # is typed there the governing clause is that statement's, so this cannot
    # fire twice.
    if opening is not None and opening.opens_a_group and depth_at(tokens, caret) > 0:
        return opening.opens_a_group, True
```

`depth_at` is already imported in this module.

- [ ] **Step 6: Declare it on ANSI's `WITH`**

In `src/pysqlsuggestions/dialects/ansi.py`, replace the `WITH` clause entry:

```python
        # A CTE body takes a whole statement. `VALUES` is here and deliberately
        # not in `followed_by`: a VALUES body is the ordinary way to write a
        # literal table, and after the list the clause model filters it out
        # anyway, since VALUES declares itself part of INSERT INTO.
        Clause(name='WITH', suggests=(), opens_a_group=('SELECT', 'VALUES', 'WITH')),
```

- [ ] **Step 7: Update the test that pinned the silence**

`tests/test_statement_forms.py::test_a_parenthesised_position_is_not_reached_by_the_rule`
asserts `offered('WITH a AS (') == []` and explains it as a separate gap. The
gap is closed; the test's own subject — that a parenthesised position is not
silenced by the unmodelled-statement refusal — is unchanged and worth keeping.
Replace its docstring's second paragraph and that one assertion:

```python
    `SELECT * FROM (` opens a derived table and offers relations. `WITH a AS (`
    opens a CTE body and offers the statements a body may contain. Both are
    parenthesised positions with a governing clause, which is the point here:
    neither is reached by the refusal.
    """
    assert offered('SELECT * FROM (') == ['users', 'orders', 'public']
    assert 'SELECT' in offered('WITH a AS (')
```

- [ ] **Step 8: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A src tests
git commit -m "feat: a clause can say what its parenthesised group begins with"
```

---

## Task 2: the other four positions

**Files:**
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (the `WITH` clause, `RESERVED`)
- Test: `tests/test_cte_positions.py`

**Interfaces:**
- Consumes: `opens_a_group` from Task 1, unchanged by this task.

**No new mechanism.** `expecting == 'operand'` already returns the clause's
empty `suggests`, which is why two of these positions are correct today and must
stay so. `item_words` already separates `WITH a ⌶` from `WITH a AS (…) ⌶`, which
is what `aliases_with` and `_unspent_alias` are built on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cte_positions.py`:

```python
def test_a_name_is_the_authors_to_invent() -> None:
    """
    Both positions where a CTE name goes answer nothing, and both did before
    this change. An engine cannot guess a name, and offering keywords where one
    belongs would be worse than silence.
    """
    assert offered('WITH ') == []
    assert offered('WITH a AS (SELECT 1), ') == []


def test_after_the_name_comes_as() -> None:
    """The only word that can follow a CTE name, and it had no answer before."""
    assert offered('WITH a ') == ['AS']


def test_after_the_body_comes_the_statement_it_feeds() -> None:
    """
    `AS` is spent by now — it is in the item's words — so `_unspent_alias` drops
    it, which is the whole of what separates this position from the one above.
    """
    found = offered('WITH a AS (SELECT 1) ')
    assert 'SELECT' in found
    assert 'AS' not in found


def test_recursive_is_offered_behind_a_prefix() -> None:
    """
    Like `DISTINCT` after `SELECT`: it stands between the clause and its first
    item, it is rare, and a CTE name is what usually follows `WITH` — so it
    surfaces once something is typed rather than above every caret.
    """
    assert 'RECURSIVE' in offered('WITH rec')


def test_recursive_is_not_read_as_a_cte_name() -> None:
    """
    Without the word reserved, the analyser reads it as a name already written
    and offers `AS` — where another name belongs. All three shipped backends
    accept `WITH RECURSIVE`, and only Trino reserved the word.
    """
    assert offered('WITH RECURSIVE ') == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cte_positions.py -v`
Expected: `after_the_name_comes_as`, `after_the_body_comes_the_statement_it_feeds`,
`recursive_is_offered_behind_a_prefix` and `recursive_is_not_read_as_a_cte_name`
FAIL. `test_a_name_is_the_authors_to_invent` passes and must keep passing — it
is the regression guard for this task.

- [ ] **Step 3: Declare the other three things**

In `src/pysqlsuggestions/dialects/ansi.py`, replace the `WITH` clause entry
written in Task 1 with the full one:

```python
        # A CTE body takes a whole statement. `VALUES` is in `opens_a_group` and
        # deliberately not in `followed_by`: a VALUES body is the ordinary way
        # to write a literal table, and after the list the clause model filters
        # it out anyway, since VALUES declares itself part of INSERT INTO.
        #
        # `aliases_with` is what separates `WITH a ` from `WITH a AS (…) `: the
        # second has AS among its item words, so `_unspent_alias` drops it. The
        # same machinery `FROM t AS x` already uses.
        Clause(
            name='WITH',
            suggests=(),
            opens_a_group=('SELECT', 'VALUES', 'WITH'),
            followed_by=('AS', 'SELECT'),
            aliases_with='AS',
            before_the_item=('RECURSIVE',),
        ),
```

- [ ] **Step 4: Reserve the word**

In the same file, add the word `recursive` to the `RESERVED` string. The set is
built by `.split()`, so placement within the string is free — put it beside
`references` on the line that already reads `... primary references right ...`,
which keeps the run alphabetical.

Trino's own `RESERVED` also lists `recursive`; a frozenset union makes that
harmless, and removing it there would be an unrelated change.

- [ ] **Step 5: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. If `tests/test_dialect_lexing.py` or the golden corpus
fails, reserving `recursive` changed how a word lexes somewhere else — read the
failure before adjusting anything, because the reserved set also drives quoting.

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git commit -m "feat: the other four carets a WITH clause has"
```

---

## Task 3: Postgres takes the data-modifying forms

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py`
- Test: `tests/test_cte_positions.py`

**Interfaces:**
- Consumes: ANSI's `WITH` clause from Task 2.

**Measured, not assumed.** All four of `INSERT INTO`, `UPDATE`, `DELETE FROM`
and a nested `WITH` plan inside a Postgres CTE body, and all of `INSERT INTO`,
`UPDATE` and `DELETE FROM` plan after the list. ClickHouse answers
`Syntax error: failed at position 12` for `WITH x AS (INSERT INTO t VALUES (1))`,
which is why the extension lives here rather than in ANSI.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cte_positions.py`:

```python
def test_postgres_takes_a_data_modifying_cte() -> None:
    """
    `WITH a AS (INSERT INTO … RETURNING id) SELECT * FROM a` plans, and so do
    the UPDATE and DELETE forms. A Postgres extension: ClickHouse refuses the
    same statement with a syntax error.
    """
    found = offered('WITH a AS (')
    assert 'INSERT INTO' in found
    assert 'UPDATE' in found
    assert 'DELETE FROM' in found


def test_postgres_takes_one_after_the_list_too() -> None:
    """`WITH a AS (SELECT 1) INSERT INTO … SELECT x FROM a` plans."""
    assert 'INSERT INTO' in offered('WITH a AS (SELECT 1) ')


def test_clickhouse_keeps_the_conservative_body() -> None:
    """
    Inherited rather than declared, and the refusal is why: a dialect that
    cannot run the statement should not offer the word that starts it.
    """
    sql = 'WITH a AS ('
    found = [s.text for s in complete(sql, len(sql), CLICKHOUSE, MemoryCatalog(SNAPSHOT))]
    assert 'SELECT' in found
    assert 'INSERT INTO' not in found
```

and extend the file's imports:

```python
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
```

- [ ] **Step 2: Run the tests to verify two fail**

Run: `uv run pytest tests/test_cte_positions.py -v`
Expected: the two Postgres tests FAIL; `test_clickhouse_keeps_the_conservative_body`
passes and must keep passing — it is what says the extension stayed out of the
baseline.

- [ ] **Step 3: Extend the clause**

In `src/pysqlsuggestions/dialects/postgres.py`, add to the `ANSI.clauses.extend(`
call, after the `ALTER SEQUENCE` entry:

```python
        # Data-modifying CTEs, which are Postgres's own: all three forms plan
        # inside a body and after the list, and ClickHouse refuses the first
        # with a syntax error. `extend` replaces a clause of the same name
        # rather than merging into it, so ANSI's declarations are restated.
        Clause(
            name='WITH',
            suggests=(),
            opens_a_group=('SELECT', 'VALUES', 'WITH', 'INSERT INTO', 'UPDATE', 'DELETE FROM'),
            followed_by=('AS', 'SELECT', 'INSERT INTO', 'UPDATE', 'DELETE FROM'),
            aliases_with='AS',
            before_the_item=('RECURSIVE',),
        ),
```

- [ ] **Step 4: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: Postgres CTE bodies take the statements that modify data"
```

---

## Task 4: the corpus asks every dialect

**Files:**
- Modify: `src/pysqlsuggestions/testing/__init__.py`
- Modify: `tests/corpus/cases.py`
- Test: `tests/test_conformance.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conformance.py`:

```python
def test_the_corpus_asks_a_dialect_what_its_groups_begin_with() -> None:
    """
    A clause declaring `opens_a_group` and never answering inside one is silent
    rather than wrong, which is the kind of mistake `structure` cannot see and
    only a behavioural case can.
    """
    for dialect in SHIPPED:
        assert [case for case in DialectConformance.cases(dialect) if 'group' in case.name]


def test_a_dialect_declaring_no_group_words_gets_no_case() -> None:
    """
    The corpus asks a dialect only what it claims to do — the bargain
    `parameter()` makes for `?` and `relation_search` makes for Trino.
    """
    bare = replace(ANSI, clauses=ClauseModel(clauses=(Clause(name='SELECT', suggests=(Kind.COLUMN,)),)))
    assert not [case for case in DialectConformance.cases(bare) if 'group' in case.name]
```

Add to the `CASES` tuple in `tests/corpus/cases.py`:

```python
    GoldenRequest(
        sql='WITH a AS (⌶',
        kinds=('keyword',),
        clause='WITH',
        note='a CTE body takes a whole statement, and only the words that start one',
    ),
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_conformance.py tests/test_golden_requests.py -v`
Expected: `test_the_corpus_asks_a_dialect_what_its_groups_begin_with` FAILS — no
case mentions a group. The golden case passes already, having been built by
Tasks 1–3; it is there so the request shape is pinned alongside the others.

- [ ] **Step 3: Add the case**

In `src/pysqlsuggestions/testing/__init__.py`, in `cases`, add after the
`literal argument` block:

```python
        grouped = next((c for c in dialect.clauses.clauses if c.opens_a_group), None)
        if grouped is not None:
            cases.append(
                Case(
                    name='a clause that opens a group says what may begin one',
                    sql=f'{grouped.name} a AS (',
                    expect=(grouped.opens_a_group[0],),
                ),
            )
```

Found by what the clause declares rather than by the name `WITH`, so a dialect
spelling its CTE clause differently is still covered. `expect` names the first
word the dialect itself lists, so the case asserts the dialect's own claim
rather than one this corpus invented.

- [ ] **Step 4: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. If a shipped dialect fails the new case, Task 1's rule does
not reach it — fix it there rather than weakening the case.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "test: the corpus asks what a clause's group begins with"
```

---

## Task 5: the changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write the entry**

In `CHANGELOG.md`, directly under `## Unreleased`:

```markdown
### `WITH` answers where it never did

`WITH a AS (⌶` offers the statements a CTE body may contain — `SELECT`,
`VALUES`, a nested `WITH`, and on Postgres the data-modifying forms, all
verified against the server. `WITH a AS (…) ⌶` offers the statement the CTE
feeds. `WITH a ⌶` offers `AS`, and `WITH rec⌶` offers `RECURSIVE`.

Every one of those positions answered nothing before: the clause declared no
`suggests`, no `followed_by`, and nothing declared it `follows`, so its
continuations were empty and each caret fell through.

`WITH ⌶` and `WITH a AS (…), ⌶` still answer nothing, which is right — a CTE
name is the author's to invent.

`Clause` gains `opens_a_group`, the words that may begin a clause's
parenthesised body. A dialect needs it to describe a CTE: what belongs inside
the group and what belongs after it are different lists, and a nested `WITH` is
the case that proves it.

ClickHouse keeps the conservative body list, because it refuses a data-modifying
CTE outright.
```

- [ ] **Step 2: Verify**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass — `tests/test_build_pages.py` renders the changelog, so a
malformed heading shows up here.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: five positions that had no answer"
```

---

## Self-review notes

**Spec coverage.** §1's table → Tasks 1 and 2, one test per row. §3 the field →
Task 1 Steps 3–4. §4 the rule → Task 1 Step 5. §5 the other declarations → Task
2. §6 dialect differences → Tasks 2 and 3, including the `VALUES` asymmetry,
which Task 1 Step 6's comment records at the declaration. §7 unit → Tasks 1–3;
golden corpus and conformance → Task 4; "no integration test" → nothing
implements it, which is the point. §8 → Task 5, and `docs/gaps.md` correctly
gets no change.

**Ordering.**
- Task 1 before Task 2: Task 2's full clause declaration includes
  `opens_a_group`, so the field must exist.
- Task 2 before Task 3: Postgres restates ANSI's declarations, and restating one
  that does not exist yet would not compile.
- Task 4 after Task 3: the conformance case asserts behaviour all three build.

**Two things a task is expected to disturb.**
- Task 1 Step 7 — `test_a_parenthesised_position_is_not_reached_by_the_rule`
  asserts the silence this slice removes. Its subject is unrelated and it stays;
  one assertion and one paragraph change.
- Task 2 Step 4 — reserving `recursive` changes a word's lexing everywhere, and
  the reserved set also drives quoting. Step 5 names what to read if something
  unexpected fails.

**One thing deliberately left undone**, recorded in the spec and repeated here
so it is not mistaken for an oversight: `WITH a AS (…) VALUES (1)` plans and is
not offered, because `VALUES` declares `statements={'INSERT INTO'}` and the
statement form at that caret is `WITH`. Reaching it means widening
`INSERT INTO`'s model for a caret almost nobody types.
