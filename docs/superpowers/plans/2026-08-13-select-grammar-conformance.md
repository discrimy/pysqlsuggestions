# SELECT Grammar Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Postgres conformance suite that measures the engine against the official `SELECT` synopsis, printing a burn-down and changing no shipped behaviour.

**Architecture:** The synopsis is stored verbatim in `tests/grammar/select.txt`. `tests/grammar/cases.py` holds one `GrammarCase` per caret the grammar names, each citing the synopsis line it comes from. `tests/test_grammar_select.py` runs them against `complete()` with a shared `MemoryCatalog`, marking unsatisfied cases `xfail(strict=True)`, and asserts that every line of the synopsis is cited by at least one case.

**Tech Stack:** Python 3.12, pytest, `uv`. No new dependencies — `pysqlsuggestions` has none and must keep none.

## Global Constraints

- **Design doc:** `docs/superpowers/specs/2026-08-13-select-grammar-conformance-design.md`. Read it before Task 1.
- **Nothing under `src/` changes.** This plan is measurement only. A task that edits `src/pysqlsuggestions/` has gone wrong.
- **`./scripts/check.sh` must pass at the end of every task**: `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`. Every case either passes or is a strict xfail, so the gate stays green.
- **Style:** single quotes, 120 columns, ruff `D` enabled — every module, class and function needs a docstring. mypy `strict` covers `tests/`, so annotate fully.
- **Prose register:** docstrings and comments say *why* a shape was chosen and what was rejected. See `src/pysqlsuggestions/dialects/base.py` for the register to match.
- **Caret marker** is `⌶` (U+2336), and `CARET`/`split_caret` are imported from `tests/corpus/cases.py` — never redefined.
- **Commits:** `test:` for the suite, `docs:` for documentation, lowercase prose summary, body explaining the decision. End every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- **Measured baseline:** 22 of 59 cases pass today. Every `pending=True` in this plan was measured against the engine, not guessed. If a case marked pending unexpectedly passes, `xfail(strict=True)` turns that into a failure — investigate rather than flipping the flag.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `tests/grammar/__init__.py` | package marker, one-line docstring |
| `tests/grammar/select.txt` | the PG 18 synopsis, verbatim, under a provenance header |
| `tests/grammar/cases.py` | the `GrammarCase` record, `UNCITED`, and `CASES` |
| `tests/test_grammar_select.py` | the runner, the fixture, and the data-honesty tests |
| `tests/conftest.py` | modified: a third burn-down line |
| `CHANGELOG.md` | modified: an Unreleased entry |
| `docs/gaps.md` | modified: one sentence pointing at the suite |

---

### Task 1: The record, the synopsis file, and the runner

Seeds the suite with the `with_query` group so the machinery is exercised end to end. The coverage test arrives in Task 6, once every line has a citation.

**Files:**
- Create: `tests/grammar/__init__.py`
- Create: `tests/grammar/select.txt`
- Create: `tests/grammar/cases.py`
- Create: `tests/test_grammar_select.py`

**Interfaces:**
- Consumes: `CARET`, `split_caret` from `tests/corpus/cases.py`; `complete` from `pysqlsuggestions.api`; `MemoryCatalog` from `pysqlsuggestions.catalogs.memory`; `POSTGRES` from `pysqlsuggestions.dialects.postgres`.
- Produces: `GrammarCase` (frozen dataclass, fields `sql: str`, `cite: str`, `offers: tuple[str, ...]`, `refuses: tuple[str, ...]`, `pending: bool`, `refused: str`, `note: str`); `CASES: tuple[GrammarCase, ...]`; `UNCITED: frozenset[str]`; `SYNOPSIS: str`; and in the runner `catalog() -> MemoryCatalog`, `offered(sql: str, caret: int) -> list[str]`, `_collapse(text: str) -> str`.

- [ ] **Step 1: Create the package marker**

`tests/grammar/__init__.py`:

```python
"""The official SELECT grammar, as test data."""
```

- [ ] **Step 2: Create `tests/grammar/select.txt`**

Verbatim from https://www.postgresql.org/docs/current/sql-select.html (PostgreSQL 18). Reproduce exactly, including indentation:

```
# PostgreSQL SELECT synopsis, reproduced verbatim.
# Source: https://www.postgresql.org/docs/current/sql-select.html
# Server version: 18. Fetched: 2026-08-13.
# Edit this file only to re-sync with a later server. tests/test_grammar_select.py
# asserts that every grammar line below is cited by at least one case, so a
# re-sync that adds a line will fail until somebody writes the case for it.

[ WITH [ RECURSIVE ] with_query [, ...] ]
SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]
    [ { * | expression [ [ AS ] output_name ] } [, ...] ]
    [ FROM from_item [, ...] ]
    [ WHERE condition ]
    [ GROUP BY [ ALL | DISTINCT ] grouping_element [, ...] ]
    [ HAVING condition ]
    [ WINDOW window_name AS ( window_definition ) [, ...] ]
    [ { UNION | INTERSECT | EXCEPT } [ ALL | DISTINCT ] select ]
    [ ORDER BY expression [ ASC | DESC | USING operator ] [ NULLS { FIRST | LAST } ] [, ...] ]
    [ LIMIT { count | ALL } ]
    [ OFFSET start [ ROW | ROWS ] ]
    [ FETCH { FIRST | NEXT } [ count ] { ROW | ROWS } { ONLY | WITH TIES } ]
    [ FOR { UPDATE | NO KEY UPDATE | SHARE | KEY SHARE } [ OF from_reference [, ...] ] [ NOWAIT | SKIP LOCKED ] [...] ]

where from_item can be one of:

    [ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]
                [ TABLESAMPLE sampling_method ( argument [, ...] ) [ REPEATABLE ( seed ) ] ]
    [ LATERAL ] ( select ) [ [ AS ] alias [ ( column_alias [, ...] ) ] ]
    with_query_name [ [ AS ] alias [ ( column_alias [, ...] ) ] ]
    [ LATERAL ] function_name ( [ argument [, ...] ] )
                [ WITH ORDINALITY ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]
    [ LATERAL ] function_name ( [ argument [, ...] ] ) [ AS ] alias ( column_definition [, ...] )
    [ LATERAL ] function_name ( [ argument [, ...] ] ) AS ( column_definition [, ...] )
    [ LATERAL ] ROWS FROM( function_name ( [ argument [, ...] ] ) [ AS ( column_definition [, ...] ) ] [, ...] )
                [ WITH ORDINALITY ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]
    from_item join_type from_item { ON join_condition | USING ( join_column [, ...] ) [ AS join_using_alias ] }
    from_item NATURAL join_type from_item
    from_item CROSS JOIN from_item

and grouping_element can be one of:

    ( )
    expression
    ( expression [, ...] )
    ROLLUP ( { expression | ( expression [, ...] ) } [, ...] )
    CUBE ( { expression | ( expression [, ...] ) } [, ...] )
    GROUPING SETS ( grouping_element [, ...] )

and with_query is:

    with_query_name [ ( column_name [, ...] ) ] AS [ [ NOT ] MATERIALIZED ] ( select | values | insert | update | delete | merge )
        [ SEARCH { BREADTH | DEPTH } FIRST BY column_name [, ...] SET search_seq_col_name ]
        [ CYCLE column_name [, ...] SET cycle_mark_col_name [ TO cycle_mark_value DEFAULT cycle_mark_default ] USING cycle_path_col_name ]

TABLE [ ONLY ] table_name [ * ]
```

- [ ] **Step 3: Create `tests/grammar/cases.py` with the record and the first group**

```python
"""
The official SELECT grammar, as cases.

`tests/corpus/cases.py` burns down against expectations somebody observed —
pgcli's tests and a production suite. A corpus can only hold positions somebody
thought to write down. This file burns down against a *specified* set: every
caret the PostgreSQL SELECT synopsis names, whether or not anyone has met it.

The synopsis itself is `select.txt`, verbatim, and `test_grammar_select.py`
asserts that every line of it is cited here. That is what stops this file
drifting from the document it claims to track.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SYNOPSIS = (Path(__file__).parent / 'select.txt').read_text(encoding='utf-8')
"""The grammar as printed, read once at import."""


@dataclass(frozen=True)
class GrammarCase:
    """One caret the synopsis names, and what the engine must say there."""

    sql: str
    """Caret marked with ⌶, the convention tests/corpus/cases.py established."""
    cite: str
    """
    The synopsis line this position comes from, verbatim.

    Both a citation and a coverage token: the runner checks each cite against
    `select.txt`, and checks every line of `select.txt` against the cites.
    """
    offers: tuple[str, ...] = ()
    """
    Suggestion texts that must all appear. A subset assertion, not an equality.

    Ranking is `engine/rank.py`'s subject and `tests/test_complete.py` pins it
    already. A conformance case that also asserted order would go red on
    changes that have nothing to do with the grammar.
    """
    refuses: tuple[str, ...] = ()
    """
    Suggestion texts that must not appear at all.

    Where wrong answers die, and the reason this file exists rather than a
    second golden-request corpus: `WINDOW ⌶` offering a column is not a missing
    answer, it is an answer that writes SQL the server refuses.
    """
    pending: bool = False
    """True for a case the engine cannot satisfy today: an xfail(strict=True)."""
    refused: str = ''
    """
    Why this production is a deliberate non-goal. Empty for the rest.

    Independent of `pending`, and the two combine. `TABLESAMPLE ⌶` is refused
    *and* pending: we are not going to model sampling methods, and the engine
    must still stop offering `JOIN` there. Two commitments, both true, so
    `refused` does not excuse a case from the burn-down — it records that the
    fix is silence rather than grammar.
    """
    note: str = ''
    """
    Anything a later reader needs. Used above all for accidental greens.

    `FROM ONLY ⌶` passes because `ONLY` is skipped as an unrecognised token and
    the FROM clause carries the position, not because anything models it. That
    case will go red the day the production is modelled properly, and the note
    is the only warning.
    """


UNCITED = frozenset(
    {
        '( )',
        'expression',
    },
)
"""
Synopsis lines deliberately left uncited, so the coverage test can pass.

Both are `grouping_element` alternatives that are ordinary expression positions
with nothing specific to assert — an empty grouping set offers what any
expression offers. `( expression [, ...] )` *is* cited, by the `GROUP BY (⌶`
case, which covers all three in practice. Listed rather than pattern-matched:
a set of two strings is auditable, and a rule that skipped "short lines" would
silently swallow a real production later.
"""

CASES: tuple[GrammarCase, ...] = (
    # --- with_query -------------------------------------------------------
    GrammarCase(
        sql='⌶',
        cite='[ WITH [ RECURSIVE ] with_query [, ...] ]',
        offers=('WITH', 'SELECT'),
        note='the empty editor, where a statement may begin',
    ),
    GrammarCase(
        sql='WITH ⌶',
        cite='[ WITH [ RECURSIVE ] with_query [, ...] ]',
        offers=('RECURSIVE',),
        pending=True,
        note='offers nothing; RECURSIVE is in before_the_item and never reaches this caret',
    ),
    GrammarCase(
        sql='WITH x ⌶',
        cite='with_query_name [ ( column_name [, ...] ) ] AS [ [ NOT ] MATERIALIZED ] ( select | values | insert | update | delete | merge )',
        offers=('AS',),
    ),
    GrammarCase(
        sql='WITH x (⌶',
        cite='with_query_name [ ( column_name [, ...] ) ] AS [ [ NOT ] MATERIALIZED ] ( select | values | insert | update | delete | merge )',
        refuses=('SELECT', 'VALUES', 'users'),
        pending=True,
        refused='a CTE column list names columns being defined, so there is nothing to suggest; the fix is silence',
        note='offers the CTE body words — SELECT, VALUES, WITH — inside the column list',
    ),
    GrammarCase(
        sql='WITH x AS ⌶',
        cite='with_query_name [ ( column_name [, ...] ) ] AS [ [ NOT ] MATERIALIZED ] ( select | values | insert | update | delete | merge )',
        offers=('MATERIALIZED', 'NOT MATERIALIZED'),
        pending=True,
        note='offers nothing at all here',
    ),
    GrammarCase(
        sql='WITH x AS (⌶',
        cite='with_query_name [ ( column_name [, ...] ) ] AS [ [ NOT ] MATERIALIZED ] ( select | values | insert | update | delete | merge )',
        offers=('SELECT', 'VALUES', 'INSERT INTO', 'UPDATE', 'DELETE FROM'),
        note='MERGE is in the grammar and in no dialect here; not asserted',
    ),
    GrammarCase(
        sql='WITH RECURSIVE x AS (SELECT 1) SEARCH ⌶',
        cite='[ SEARCH { BREADTH | DEPTH } FIRST BY column_name [, ...] SET search_seq_col_name ]',
        offers=('BREADTH', 'DEPTH'),
        refuses=('SELECT', 'INSERT INTO'),
        pending=True,
        refused='recursive search ordering is a production this engine does not intend to model',
        note='reads SEARCH as still inside WITH and offers the CTE body words',
    ),
    GrammarCase(
        sql='WITH RECURSIVE x AS (SELECT 1) CYCLE ⌶',
        cite='[ CYCLE column_name [, ...] SET cycle_mark_col_name [ TO cycle_mark_value DEFAULT cycle_mark_default ] USING cycle_path_col_name ]',
        refuses=('SELECT', 'INSERT INTO'),
        pending=True,
        refused='cycle detection is a production this engine does not intend to model',
        note='same fault as SEARCH: the CTE body words leak past the closing paren',
    ),
)
```

- [ ] **Step 4: Create `tests/test_grammar_select.py`**

```python
"""
The SELECT conformance suite: every caret the official synopsis names.

Runs against `complete`, not `derive_request`, because the question here is
which *words* a position offers — `tests/test_golden_requests.py` already pins
the Request shape. Same caret convention, a different assertion.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from tests.corpus.cases import CARET, split_caret
from tests.grammar.cases import CASES, SYNOPSIS, UNCITED, GrammarCase

SNAPSHOT = {
    ('public', 'users'): [('id', 'bigint'), ('email', 'text')],
    ('public', 'orders'): [('id', 'bigint'), ('user_id', 'bigint'), ('total', 'numeric')],
}
"""
Two relations, five columns, one plausible join.

The shape `tests/test_statement_forms.py` already uses. Small enough that a
`refuses` list can name every column by hand, which is what makes an exclusion
assertion trustworthy.
"""


def catalog() -> MemoryCatalog:
    """A fresh catalog per case, so no case can be affected by another's caching."""
    return MemoryCatalog(SNAPSHOT)


def offered(sql: str, caret: int) -> list[str]:
    """The suggestion texts at `caret` in `sql`."""
    return [suggestion.text for suggestion in complete(sql, caret, POSTGRES, catalog())]


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
    """Every word the synopsis puts at this caret is offered, and none it forbids."""
    sql, caret = split_caret(case.sql)
    found = offered(sql, caret)

    missing = [word for word in case.offers if word not in found]
    assert not missing, f'not offered: {missing}; got {found}'

    wrong = [word for word in case.refuses if word in found]
    assert not wrong, f'wrongly offered: {wrong}; got {found}'


# --- the data itself ------------------------------------------------------


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_every_case_marks_exactly_one_caret(case: GrammarCase) -> None:
    """Two markers or none would produce a nonsense offset."""
    assert case.sql.count(CARET) == 1


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_every_case_asserts_something(case: GrammarCase) -> None:
    """A case with neither an offer nor a refusal passes vacuously and measures nothing."""
    assert case.offers or case.refuses


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_every_cite_is_a_line_of_the_synopsis(case: GrammarCase) -> None:
    """A citation invented at the keyboard would make the coverage test meaningless."""
    assert _collapse(case.cite) in {_collapse(line) for line in SYNOPSIS.splitlines()}


def _collapse(text: str) -> str:
    """Runs of whitespace to one space, ends trimmed. The synopsis is indented for print."""
    return ' '.join(text.split())
```

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py -q`
Expected: PASS — 3 of the 8 cases green, 5 xfailed, and every data test passing.

- [ ] **Step 6: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS. If ruff reformats `cases.py`, accept its output and re-run.

- [ ] **Step 7: Commit**

```bash
git add tests/grammar tests/test_grammar_select.py
git commit -F - <<'EOF'
test: the SELECT synopsis as a conformance suite

The corpus burns down against expectations somebody observed. This burns
down against a specified set: every caret the official grammar names,
whether or not anyone has met it.

Each case cites the synopsis line it comes from, and the file it cites is
stored verbatim, so the suite can be checked against the document it
claims to track rather than against memory of it.

`refused` labels the production and `pending` labels the case, and the
two combine deliberately. `WITH x (⌶` is both: a CTE column list is a
position we will never suggest into, and offering SELECT there is still
wrong today.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: The select list and `from_item`

**Files:**
- Modify: `tests/grammar/cases.py` (append to `CASES`)

**Interfaces:**
- Consumes: `GrammarCase` from Task 1.
- Produces: nothing new; extends `CASES`.

- [ ] **Step 1: Append the select-list and `from_item` cases**

Insert before the closing `)` of `CASES`:

```python
    # --- the select list --------------------------------------------------
    GrammarCase(
        sql='SELECT ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('ALL', 'DISTINCT'),
        pending=True,
        note='offers columns only; `before_the_item` carries DISTINCT and omits ALL',
    ),
    GrammarCase(
        sql='SELECT id, ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        refuses=('DISTINCT', 'ALL'),
        note='both are legal only directly after SELECT; this is the position they must not reach',
    ),
    GrammarCase(
        sql='SELECT DISTINCT ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('ON',),
    ),
    GrammarCase(
        sql='SELECT DISTINCT ON (⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('users.id',),
    ),
    GrammarCase(
        sql='SELECT id ⌶',
        cite='[ { * | expression [ [ AS ] output_name ] } [, ...] ]',
        offers=('AS', 'FROM'),
    ),
    # --- from_item --------------------------------------------------------
    GrammarCase(
        sql='SELECT * FROM ⌶',
        cite='[ FROM from_item [, ...] ]',
        offers=('users', 'ONLY', 'LATERAL'),
        pending=True,
        note='offers relations; neither ONLY nor LATERAL is offered where an item begins',
    ),
    GrammarCase(
        sql='SELECT * FROM ONLY ⌶',
        cite='[ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('users',),
        note='an accidental green: ONLY is skipped as an unrecognised token and FROM carries the position',
    ),
    GrammarCase(
        sql='SELECT * FROM users ⌶',
        cite='[ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('AS', 'TABLESAMPLE'),
        pending=True,
        note='AS is offered, TABLESAMPLE is not',
    ),
    GrammarCase(
        sql='SELECT * FROM users AS u (⌶',
        cite='[ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='a column alias list names columns being defined; there is nothing to suggest and silence is the answer',
        note='offers relation names inside the alias list',
    ),
    GrammarCase(
        sql='SELECT * FROM users TABLESAMPLE ⌶',
        cite='[ TABLESAMPLE sampling_method ( argument [, ...] ) [ REPEATABLE ( seed ) ] ]',
        offers=('BERNOULLI', 'SYSTEM'),
        refuses=('JOIN', 'WHERE'),
        pending=True,
        refused='sampling methods are extensible per installation; the engine will not carry a list it cannot keep true',
        note='offers the clauses that follow a relation, so accepting writes TABLESAMPLE JOIN',
    ),
    GrammarCase(
        sql='SELECT * FROM users TABLESAMPLE BERNOULLI (10) REPEATABLE (⌶',
        cite='[ TABLESAMPLE sampling_method ( argument [, ...] ) [ REPEATABLE ( seed ) ] ]',
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='a repeat seed is a number; nothing in a catalog answers it',
        note='offers relation names where a seed belongs',
    ),
    GrammarCase(
        sql='SELECT * FROM LATERAL (⌶',
        cite='[ LATERAL ] ( select ) [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('SELECT',),
        refuses=('users', 'orders'),
        pending=True,
        note='a parenthesised LATERAL takes a whole subquery; the position offers relations instead',
    ),
    GrammarCase(
        sql='WITH x AS (SELECT 1) SELECT * FROM x ⌶',
        cite='with_query_name [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('AS',),
    ),
    GrammarCase(
        sql='SELECT * FROM LATERAL ⌶',
        cite='[ LATERAL ] function_name ( [ argument [, ...] ] )',
        offers=('users',),
        note='LATERAL is modelled in postgres.py, so this green is real',
    ),
    GrammarCase(
        sql='SELECT * FROM generate_series(1, 2) ⌶',
        cite='[ WITH ORDINALITY ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('WITH ORDINALITY', 'AS'),
        pending=True,
        note='offers neither; a function in a FROM list takes an alias and an ordinality marker',
    ),
    GrammarCase(
        sql='SELECT * FROM generate_series(1, 2) AS t (⌶',
        cite='[ LATERAL ] function_name ( [ argument [, ...] ] ) [ AS ] alias ( column_definition [, ...] )',
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='a column definition list is DDL inside a query; naming types here is the DDL authoring this engine stops short of',
        note='offers relation names where a column name and type belong',
    ),
    GrammarCase(
        sql='SELECT * FROM generate_series(1, 2) AS (⌶',
        cite='[ LATERAL ] function_name ( [ argument [, ...] ] ) AS ( column_definition [, ...] )',
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='the anonymous spelling of the definition list above, refused for the same reason',
        note='offers relation names',
    ),
    GrammarCase(
        sql='SELECT * FROM ROWS FROM(⌶',
        cite='[ LATERAL ] ROWS FROM( function_name ( [ argument [, ...] ] ) [ AS ( column_definition [, ...] ) ] [, ...] )',
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='a multi-function FROM item is exotica; the position must stay silent rather than answer as an ordinary FROM',
        note='reads ROWS FROM( as an ordinary FROM and offers relations',
    ),
```

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py -q`
Expected: PASS. 26 cases total — 10 green, 16 xfailed.

- [ ] **Step 3: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/grammar/cases.py
git commit -F - <<'EOF'
test: the select list and every from_item form

Eleven of the seventeen new cases are red, and six of those are wrong
answers rather than missing ones: the alias list, the column definition
lists, ROWS FROM( and REPEATABLE( all offer relation names in positions
where a relation cannot go.

`FROM ONLY ⌶` is green and carries a note saying why that means less than
it looks — ONLY is skipped as an unrecognised token and the FROM clause
carries the position. It will go red the day ONLY is modelled.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: Joins, and the clauses that shape a result

**Files:**
- Modify: `tests/grammar/cases.py` (append to `CASES`)

**Interfaces:**
- Consumes: `GrammarCase` from Task 1.
- Produces: nothing new; extends `CASES`.

- [ ] **Step 1: Append the join, WHERE, GROUP BY, HAVING and WINDOW cases**

```python
    # --- joins ------------------------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users u ⌶',
        cite='from_item join_type from_item { ON join_condition | USING ( join_column [, ...] ) [ AS join_using_alias ] }',
        offers=('JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'FULL JOIN', 'CROSS JOIN'),
        pending=True,
        note='RIGHT JOIN and FULL JOIN are absent from _JOINS in ansi.py; only four spellings exist',
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o ⌶',
        cite='from_item join_type from_item { ON join_condition | USING ( join_column [, ...] ) [ AS join_using_alias ] }',
        offers=('ON', 'USING'),
        refuses=('AS',),
        note='AS is correctly withheld: the alias is spent, and a second one parses as nothing',
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o ON ⌶',
        cite='from_item join_type from_item { ON join_condition | USING ( join_column [, ...] ) [ AS join_using_alias ] }',
        offers=('u.id', 'o.user_id'),
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o USING (id) ⌶',
        cite='from_item join_type from_item { ON join_condition | USING ( join_column [, ...] ) [ AS join_using_alias ] }',
        offers=('AS',),
        pending=True,
        note='the join_using_alias, new in PG 14; USING goes straight to the next clause',
    ),
    GrammarCase(
        sql='SELECT * FROM users u NATURAL ⌶',
        cite='from_item NATURAL join_type from_item',
        offers=('JOIN', 'LEFT JOIN'),
        note='an accidental green: NATURAL is skipped and FROM offers its joins anyway',
    ),
    GrammarCase(
        sql='SELECT * FROM users u CROSS ⌶',
        cite='from_item CROSS JOIN from_item',
        offers=('JOIN',),
    ),
    # --- the clauses that shape a result ----------------------------------
    GrammarCase(
        sql='SELECT * FROM users WHERE ⌶',
        cite='[ WHERE condition ]',
        offers=('users.id',),
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY ⌶',
        cite='[ GROUP BY [ ALL | DISTINCT ] grouping_element [, ...] ]',
        offers=('ALL', 'DISTINCT', 'ROLLUP', 'CUBE', 'GROUPING SETS'),
        pending=True,
        note='offers columns only; none of the five grouping words reaches this caret',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY (⌶',
        cite='( expression [, ...] )',
        offers=('users.id',),
        note='covers the bare `expression` and `( )` alternatives too; see UNCITED',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY ROLLUP (⌶',
        cite='ROLLUP ( { expression | ( expression [, ...] ) } [, ...] )',
        offers=('users.id',),
        note='an accidental green: ROLLUP is skipped and GROUP BY carries the position',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY CUBE (⌶',
        cite='CUBE ( { expression | ( expression [, ...] ) } [, ...] )',
        offers=('users.id',),
        note='accidental, as ROLLUP is',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY GROUPING SETS (⌶',
        cite='GROUPING SETS ( grouping_element [, ...] )',
        offers=('users.id',),
        note='accidental, as ROLLUP is',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY id HAVING ⌶',
        cite='[ HAVING condition ]',
        offers=('users.id',),
    ),
    GrammarCase(
        sql='SELECT * FROM users WINDOW ⌶',
        cite='[ WINDOW window_name AS ( window_definition ) [, ...] ]',
        refuses=('users.id', 'users.email'),
        pending=True,
        note='a window name is being defined here; offering a column writes SQL the server refuses',
    ),
    GrammarCase(
        sql='SELECT * FROM users WINDOW w AS (⌶',
        cite='[ WINDOW window_name AS ( window_definition ) [, ...] ]',
        offers=('PARTITION BY', 'ORDER BY'),
        pending=True,
        note='offers columns; PARTITION BY exists as a clause and is not reachable from here',
    ),
```

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py -q`
Expected: PASS. 41 cases total — 20 green, 21 xfailed.

- [ ] **Step 3: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/grammar/cases.py
git commit -F - <<'EOF'
test: joins, grouping elements, and the window clause

WINDOW ⌶ is the wrong answer worth having found. The grammar puts a name
being defined there and the engine offers users.id, which is not a
missing suggestion but one that writes a statement the server rejects.

The join vocabulary is short by two spellings the grammar has — RIGHT and
FULL — and USING (id) ⌶ has no join_using_alias. NATURAL, ROLLUP, CUBE
and GROUPING SETS are all green by accident, each skipped as an
unrecognised word while the enclosing clause answers, and each says so.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 4: Set operations, ordering, and the row-count clauses

**Files:**
- Modify: `tests/grammar/cases.py` (append to `CASES`)

**Interfaces:**
- Consumes: `GrammarCase` from Task 1.
- Produces: nothing new; extends `CASES`.

- [ ] **Step 1: Append the cases**

```python
    # --- set operations ---------------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users UNION ⌶',
        cite='[ { UNION | INTERSECT | EXCEPT } [ ALL | DISTINCT ] select ]',
        offers=('ALL', 'DISTINCT', 'SELECT'),
        pending=True,
        note='ALL and SELECT are offered, DISTINCT is not',
    ),
    GrammarCase(
        sql='SELECT * FROM users INTERSECT ⌶',
        cite='[ { UNION | INTERSECT | EXCEPT } [ ALL | DISTINCT ] select ]',
        offers=('ALL', 'DISTINCT'),
        pending=True,
        note='same omission as UNION',
    ),
    GrammarCase(
        sql='SELECT * FROM users EXCEPT ⌶',
        cite='[ { UNION | INTERSECT | EXCEPT } [ ALL | DISTINCT ] select ]',
        offers=('ALL', 'DISTINCT'),
        pending=True,
        note='same omission as UNION',
    ),
    # --- ordering ---------------------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users ORDER BY ⌶',
        cite='[ ORDER BY expression [ ASC | DESC | USING operator ] [ NULLS { FIRST | LAST } ] [, ...] ]',
        offers=('users.id',),
    ),
    GrammarCase(
        sql='SELECT * FROM users ORDER BY id ⌶',
        cite='[ ORDER BY expression [ ASC | DESC | USING operator ] [ NULLS { FIRST | LAST } ] [, ...] ]',
        offers=('ASC', 'DESC', 'NULLS FIRST', 'NULLS LAST', 'USING'),
        pending=True,
        note='everything but USING; an explicit ordering operator has no entry',
    ),
    GrammarCase(
        sql='SELECT * FROM users ORDER BY id USING ⌶',
        cite='[ ORDER BY expression [ ASC | DESC | USING operator ] [ NULLS { FIRST | LAST } ] [, ...] ]',
        offers=('<', '>'),
        refuses=('users.id',),
        pending=True,
        note='offers columns where an operator belongs',
    ),
    GrammarCase(
        sql='SELECT * FROM users ORDER BY id ASC ⌶',
        cite='[ ORDER BY expression [ ASC | DESC | USING operator ] [ NULLS { FIRST | LAST } ] [, ...] ]',
        offers=('NULLS FIRST', 'NULLS LAST'),
        refuses=('ASC', 'DESC'),
        note='EXCLUSIVE in dialects/base.py settles the direction once, which is what this pins',
    ),
    # --- the row-count clauses --------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users LIMIT ⌶',
        cite='[ LIMIT { count | ALL } ]',
        offers=('ALL',),
        pending=True,
        note='offers nothing; LIMIT ALL is the spelling that takes a word rather than a number',
    ),
    GrammarCase(
        sql='SELECT * FROM users OFFSET 10 ⌶',
        cite='[ OFFSET start [ ROW | ROWS ] ]',
        offers=('ROW', 'ROWS', 'FETCH'),
        pending=True,
        note='FETCH is offered, the noise words are not',
    ),
    GrammarCase(
        sql='SELECT * FROM users FETCH ⌶',
        cite='[ FETCH { FIRST | NEXT } [ count ] { ROW | ROWS } { ONLY | WITH TIES } ]',
        offers=('FIRST', 'NEXT'),
        pending=True,
        note='claims kinds=[keyword] and offers no keyword: the clause has no followed_by',
    ),
    GrammarCase(
        sql='SELECT * FROM users FETCH FIRST 10 ⌶',
        cite='[ FETCH { FIRST | NEXT } [ count ] { ROW | ROWS } { ONLY | WITH TIES } ]',
        offers=('ROW', 'ROWS'),
        pending=True,
        note='silent',
    ),
    GrammarCase(
        sql='SELECT * FROM users FETCH FIRST 10 ROWS ⌶',
        cite='[ FETCH { FIRST | NEXT } [ count ] { ROW | ROWS } { ONLY | WITH TIES } ]',
        offers=('ONLY', 'WITH TIES'),
        pending=True,
        note='silent, and this is the one place WITH TIES can go',
    ),
```

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py -q`
Expected: PASS. 53 cases total — 22 green, 31 xfailed.

- [ ] **Step 3: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/grammar/cases.py
git commit -F - <<'EOF'
test: set operations, ordering, and the row-count clauses

The whole FETCH tail is silent — FIRST, NEXT, ROW, ROWS, ONLY and WITH
TIES reach no caret — while the clause still reports kinds=['keyword'].
A position that claims a kind and offers nothing is worse than one that
claims nothing, because a client shows an empty list rather than falling
through.

DISTINCT is missing from all three set operators, LIMIT ALL from LIMIT,
the noise words from OFFSET, and USING from ORDER BY. ORDER BY id ASC ⌶
is green and pins EXCLUSIVE: a written direction settles the choice.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 5: The locking clause and the `TABLE` form

**Files:**
- Modify: `tests/grammar/cases.py` (append to `CASES`, closing the tuple)

**Interfaces:**
- Consumes: `GrammarCase` from Task 1.
- Produces: the complete `CASES` tuple — 59 entries.

- [ ] **Step 1: Append the final cases**

```python
    # --- the locking clause -----------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users FOR ⌶',
        cite='[ FOR { UPDATE | NO KEY UPDATE | SHARE | KEY SHARE } [ OF from_reference [, ...] ] [ NOWAIT | SKIP LOCKED ] [...] ]',
        offers=('UPDATE', 'NO KEY UPDATE', 'SHARE', 'KEY SHARE'),
        refuses=('users', 'orders', 'public'),
        pending=True,
        note='the sharpest wrong answer in the suite: FOR is not a clause, so the caret is still read as inside FROM and accepting writes `FROM users FOR users`',
    ),
    GrammarCase(
        sql='SELECT * FROM users FOR UPDATE ⌶',
        cite='[ FOR { UPDATE | NO KEY UPDATE | SHARE | KEY SHARE } [ OF from_reference [, ...] ] [ NOWAIT | SKIP LOCKED ] [...] ]',
        offers=('OF', 'NOWAIT', 'SKIP LOCKED'),
        refuses=('users', 'orders'),
        pending=True,
        note='offers relations, having read UPDATE as the start of an UPDATE statement',
    ),
    GrammarCase(
        sql='SELECT * FROM users u FOR UPDATE OF ⌶',
        cite='[ FOR { UPDATE | NO KEY UPDATE | SHARE | KEY SHARE } [ OF from_reference [, ...] ] [ NOWAIT | SKIP LOCKED ] [...] ]',
        offers=('u',),
        pending=True,
        note='OF takes a from_reference, so the alias in scope is the answer; the engine offers `o` instead',
    ),
    GrammarCase(
        sql='SELECT * FROM users u FOR UPDATE OF u ⌶',
        cite='[ FOR { UPDATE | NO KEY UPDATE | SHARE | KEY SHARE } [ OF from_reference [, ...] ] [ NOWAIT | SKIP LOCKED ] [...] ]',
        offers=('NOWAIT', 'SKIP LOCKED'),
        pending=True,
        note='silent',
    ),
    # --- the TABLE form ---------------------------------------------------
    GrammarCase(
        sql='TABLE ⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('users', 'ONLY'),
        pending=True,
        note='silent: TABLE is not in statement_start, and an unrecognised form correctly says nothing',
    ),
    GrammarCase(
        sql='TABLE ONLY ⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('users',),
        pending=True,
        note='silent for the same reason',
    ),
)
```

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py -q`
Expected: PASS. 59 cases total — 22 green, 37 xfailed.

- [ ] **Step 3: Verify the measured baseline**

Run: `uv run pytest tests/test_grammar_select.py -q 2>&1 | tail -3`
Expected: the summary reports 37 xfailed. If it reports an xpass, a case marked pending is now satisfied — investigate before changing the flag, because `strict=True` means an xpass is a real failure and something else has changed.

- [ ] **Step 4: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/grammar/cases.py
git commit -F - <<'EOF'
test: the locking clause and the bare TABLE form

FOR ⌶ closes the case list on the worst answer in it. FOR is not a
clause, so the analyser still believes the caret is inside FROM: the
first suggestion is `users`, and accepting writes
`SELECT * FROM users FOR users`. FOR UPDATE ⌶ then reads UPDATE as the
start of an UPDATE statement and offers relations again.

TABLE is the opposite failure and the better one — not in
statement_start, unrecognised, and therefore silent. A missing answer,
which is the shape this engine prefers when it cannot be right.

Fifty-nine cases; twenty-two green.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 6: Coverage, the burn-down, and the documentation

The coverage test lands last because it can only pass once every synopsis line has a citation.

**Files:**
- Modify: `tests/test_grammar_select.py` (add the coverage test)
- Modify: `tests/conftest.py`
- Modify: `CHANGELOG.md`
- Modify: `docs/gaps.md`

**Interfaces:**
- Consumes: `CASES`, `SYNOPSIS`, `UNCITED` from `tests/grammar/cases.py`; `_collapse` from Task 1's runner.
- Produces: nothing consumed downstream.

- [ ] **Step 1: Write the failing coverage test**

Append to `tests/test_grammar_select.py`:

```python
def _grammar_lines() -> list[str]:
    """
    The productions in `select.txt`, without the provenance header or the prose.

    Lines ending in a colon are the document's own connective tissue — "where
    from_item can be one of:" — and name no position.
    """
    lines = []
    for raw in SYNOPSIS.splitlines():
        line = _collapse(raw)
        if not line or line.startswith('#') or line.endswith(':'):
            continue
        lines.append(line)
    return lines


def test_every_synopsis_line_is_cited() -> None:
    """
    The suite tracks a document, and this is what keeps that claim true.

    Re-sync `select.txt` with a later server and any production nobody wrote a
    case for is named here, rather than silently going unmeasured.
    """
    cited = {_collapse(case.cite) for case in CASES}
    uncovered = [line for line in _grammar_lines() if line not in cited and line not in UNCITED]
    assert not uncovered, f'synopsis lines with no case: {uncovered}'


def test_uncited_lines_are_really_in_the_synopsis() -> None:
    """An UNCITED entry that matches nothing is an exemption for a line that no longer exists."""
    lines = set(_grammar_lines())
    assert UNCITED <= lines
```

- [ ] **Step 2: Run it to see where it stands**

Run: `uv run pytest tests/test_grammar_select.py::test_every_synopsis_line_is_cited -q`
Expected: PASS if Tasks 1–5 cited every line. If it FAILS, the assertion message lists the uncovered lines — add a case for each, following the shape used in Tasks 2–5, rather than adding it to `UNCITED`. `UNCITED` is only for the two `grouping_element` alternatives named in Task 1.

- [ ] **Step 3: Add the burn-down line to `tests/conftest.py`**

Add the import beside the existing one:

```python
from tests.grammar.cases import CASES as GRAMMAR_CASES
```

and append to the body of `pytest_terminal_summary`, after the existing lines:

```python
    answered = sum(1 for case in GRAMMAR_CASES if not case.pending)
    refused = sum(1 for case in GRAMMAR_CASES if case.pending and case.refused)
    gaps = len(GRAMMAR_CASES) - answered
    terminalreporter.write_line(
        f'grammar burn-down: {answered}/{len(GRAMMAR_CASES)} SELECT positions answered, '
        f'{refused} of the {gaps} gaps refused',
    )
```

The denominator is every case, so the figure means what it says. `refused` counts the gaps whose fix is to make the position silent rather than to model the grammar — a reader needs both numbers to know what the work is.

- [ ] **Step 4: Run the whole suite and read the summary**

Run: `uv run pytest -m 'not integration' -q 2>&1 | tail -6`
Expected: PASS, and among the summary lines:

```
grammar burn-down: 22/59 SELECT positions answered, 9 of the 37 gaps refused
```

- [ ] **Step 5: Add the CHANGELOG entry**

Under `## [Unreleased]` in `CHANGELOG.md`, following the file's grouping — by what changes at a caret:

```markdown
### Nothing changes at a caret

- A conformance suite for the official PostgreSQL `SELECT` grammar, in
  `tests/grammar/`. The synopsis is stored verbatim and every case cites the
  line it comes from, so the suite can be checked against the document rather
  than against memory of it; a test asserts that no line goes uncited.

  Twenty-two of fifty-nine positions are answered. The gaps it records are
  mostly missing — the whole `FETCH … {ONLY | WITH TIES}` tail is silent,
  `LIMIT ALL`, `OFFSET … ROWS`, `RIGHT JOIN` and `FULL JOIN`, `GROUP BY` with
  `ROLLUP`, `CUBE` or `GROUPING SETS`, `ORDER BY … USING`, `DISTINCT` after a
  set operator, and the bare `TABLE` form.

  Fourteen are wrong rather than missing, which is the more expensive kind.
  `SELECT * FROM users FOR ⌶` offers `users`, because `FOR` is not a clause and
  the caret is still read as inside `FROM`; accepting writes
  `SELECT * FROM users FOR users`. `WINDOW ⌶` offers a column where a name is
  being defined. `TABLESAMPLE ⌶`, `ROWS FROM(⌶` and the column-definition lists
  all answer as though an ordinary relation position.
```

- [ ] **Step 6: Point `docs/gaps.md` at the suite**

In the introduction, after the paragraph ending "Ordered by value per unit of work, not by size.", add:

```markdown
For `SELECT` specifically there is now a second, finer list that maintains
itself: `tests/grammar/` measures every position the official PostgreSQL
synopsis names and prints a burn-down on each run. This document stays the
place for decisions with reasons; that suite is the place for coverage, and
neither restates the other.
```

- [ ] **Step 7: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/test_grammar_select.py tests/conftest.py CHANGELOG.md docs/gaps.md
git commit -F - <<'EOF'
test: assert the suite covers the synopsis, and print the burn-down

A case list that cites a document proves nothing on its own — the check
that matters runs the other way, from every line of select.txt back to
the cases. Re-sync with a later server and any production nobody covered
is named by the failure rather than going quietly unmeasured.

Two grouping_element alternatives are exempt and listed by hand, because
an empty grouping set offers what any expression offers and there is
nothing specific to assert. A rule that skipped short lines would have
swallowed a real production later.

The burn-down keeps every case in the denominator and reports refused
gaps as a second number, so `still wrong` and `will be fixed by silence`
stay separable: 22/59 answered, 9 of the 37 gaps refused.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

## Notes for the implementer

- **A case that unexpectedly passes is a signal, not a nuisance.** `xfail(strict=True)` fails on an xpass deliberately. Every pending flag here was measured on 2026-08-13; if one xpasses, something in `src/` changed and the case should be re-measured before the flag is touched.
- **Do not fix the engine while writing the suite.** `FOR ⌶` in particular is a cheap fix and explicitly out of scope — the design records it as the first candidate for the plan that follows this one.
- **`offers` is a subset assertion.** Never assert the full returned list or its order; `tests/test_complete.py` owns ranking.
- **Long `cite` strings will exceed 120 columns.** Ruff's line-length rule applies to code, and these are string literals it cannot split. Add `# noqa: E501` on those lines only if `ruff check` complains — do not restructure the record to avoid it, because a truncated citation would break the coverage test.
