# CREATE TABLE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `CREATE TABLE t (id ⌶` the types it should have had, and in doing so unblock the `TABLE` statement form that has been waiting on it.

**Architecture:** `CREATE TABLE` becomes a clause first, because `clause_at` ranks matches by (end offset, word count) and the two-word name is what stops a bare `TABLE` capturing the definition list. The list itself is answered by a new position rule in `engine/analyse.py` — counting an item's words at the list's own depth — rather than by clause continuations, which leak into the parens. `TABLE` then reaches the ANSI baseline and each backend takes away what it does not have.

**Tech Stack:** Python 3.11+, uv workspace, pytest, ruff, mypy strict. No runtime dependencies — `src/pysqlsuggestions/` imports nothing outside the standard library, ever.

## Global Constraints

- **Zero runtime dependencies.** `import pysqlsuggestions` must pull in no driver. `tests/test_purity.py` fails the build otherwise.
- **`engine/` may not import `ports` or `resolve`.** Purity flows one direction only.
- **Dialects are data.** A dialect is a frozen `Dialect` composed with `dataclasses.replace`, never a subclass. `ClauseModel.extend` **replaces** a same-named clause rather than merging into it.
- **`Dialect.__post_init__` folds the clause model's vocabulary into `keywords`.** A word the model can suggest but `keywords` omits reads as an identifier to the analyser. Never bypass this.
- **Missing capability → fewer suggestions, never an error.**
- Ruff with `D` enabled and mypy `strict` over `src`, `tests` and `lsp`: every function needs a docstring and full annotations. **Single quotes, 120 columns.**
- Docstrings and comments record *why* a shape was chosen and which alternative was rejected. A change that adds behaviour without saying what it refused is out of keeping.
- Commits are `feat:`/`fix:`/`test:`/`docs:`/`refactor:`/`chore:` with a lowercase prose summary and a body explaining the decision.
- The gate is `./scripts/check.sh` — ruff format --check, ruff check, mypy strict, pytest. Run `uv run pytest -m 'not integration'` for the fast loop.

## File Structure

| File | Responsibility in this change |
| --- | --- |
| `src/pysqlsuggestions/dialects/base.py` | the new `Clause.defines_columns` field, and folding its words into `keywords` |
| `src/pysqlsuggestions/dialects/ansi.py` | the `CREATE TABLE` and `TABLE` clauses, `_COLUMN_CONSTRAINTS`, `_QUERY` widening, `STATEMENT_START` |
| `src/pysqlsuggestions/dialects/postgres.py` | `ONLY` on `TABLE`, the wider constraint list, and deleting the comment that refused `TABLE` |
| `src/pysqlsuggestions/dialects/trino.py` | `CREATE TABLE` narrowed to the one constraint Trino takes |
| `src/pysqlsuggestions/dialects/clickhouse.py` | `TABLE` removed — the backend rejects it outright |
| `src/pysqlsuggestions/engine/analyse.py` | `defines_a_column`, and `TABLE` in `_RELATION_CLAUSES` |
| `src/pysqlsuggestions/engine/request.py` | wiring the three definition-list positions to kinds |
| `src/pysqlsuggestions/testing/__init__.py` | one conformance case for the new field |
| `tests/test_create_table.py` | **new** — every definition-list caret |
| `tests/test_statement_forms.py` | the `TABLE` form |
| `tests/grammar/cases.py` | three cases stop being pending |

---

### Task 1: `CREATE TABLE` as a clause

The clause that makes `TABLE` modellable. Nothing here answers a definition list yet — Task 2 does that — but `CREATE ⌶` starts answering `TABLE`, and the parens must be *verified silent* so Task 2 has a clean baseline.

**Files:**
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (the `CLAUSES` tuple, and `STATEMENT_START`)
- Test: `tests/test_create_table.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: a `Clause` named `'CREATE TABLE'` in `ANSI.clauses`, and `'CREATE TABLE'` in `ANSI.statement_start`. Task 2 adds `defines_columns` to that same clause; Task 3 adds a `'TABLE'` clause beside it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_create_table.py`:

```python
"""
The parenthesised definition list, and the clause that opens it.

`CREATE TABLE t (id ⌶` had nothing to say, which is gap 1 in `docs/gaps.md`.
Every caret here was silent before this suite, so nothing in it can regress from
a right answer to a wrong one — only from silence to an answer.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {
    ('public', 'users'): [('id', 'bigint'), ('email', 'text')],
    ('public', 'orders'): [('id', 'bigint')],
}


def catalog() -> MemoryCatalog:
    """Two relations, so a case can assert that neither is offered."""
    return MemoryCatalog(SNAPSHOT)


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def clause_at_end(sql: str) -> str | None:
    """The clause the engine believes governs the end of `sql`."""
    return derive_request(sql, len(sql), POSTGRES).clause


def test_create_offers_the_word_that_finishes_it() -> None:
    """Derived from the clause name by `_half_written_clauses`, like `GROUP ⌶`."""
    assert offered('CREATE ') == ['TABLE']


def test_drop_and_alter_still_offer_their_own_words() -> None:
    """
    A new head must not claim theirs.

    `_half_written_clauses` skips a head that is already a phrase, so a clause
    named `CREATE` alone would have made `('CREATE',)` a phrase and silenced
    this. Two words is what keeps all three heads answering.
    """
    assert 'TABLE' in offered('DROP ')
    assert 'TABLE' in offered('ALTER ')


def test_the_longer_clause_wins_over_a_bare_table() -> None:
    """
    `clause_at` ranks by (end offset, word count), so two words beat one.

    This is the whole reason `TABLE` is modellable at all: without it the
    definition list would be governed by the bare form and offer relations.
    """
    assert clause_at_end('CREATE TABLE t (id ') == 'CREATE TABLE'


def test_the_relation_being_created_is_not_suggested() -> None:
    """
    The name is the author's to invent, so `Kind.TABLE` here is a wrong answer.

    `WINDOW` carries the same empty `suggests` for the same reason.
    """
    assert offered('CREATE TABLE ') == []


def test_if_not_exists_is_reached_by_typing() -> None:
    """
    `before_the_item`, which `request.py` gates behind a non-empty prefix.

    The caret after `CREATE TABLE ` is where a name is being typed, and a
    keyword ranked above it would be in the way. Behind a prefix it costs
    nothing.
    """
    assert offered('CREATE TABLE if') == ['IF NOT EXISTS']


def test_the_definition_list_is_not_offered_the_clause_continuations() -> None:
    """
    A clause's `followed_by` reaches inside its parens, where it cannot parse.

    Measured before the clause was written: `followed_by=('AS',)` put `AS` at
    this caret, and `CREATE TABLE t (id AS` parses as nothing. The clause
    declares none, which is why this is silent rather than wrong.
    """
    assert offered('CREATE TABLE t (id ') == []


def test_create_table_is_offered_where_a_statement_may_begin() -> None:
    """An empty editor is exactly where it is a useful suggestion."""
    assert 'CREATE TABLE' in offered('')


def test_explain_does_not_offer_it() -> None:
    """`EXPLAIN CREATE TABLE t (id int)` is a syntax error."""
    assert 'CREATE TABLE' not in offered('EXPLAIN ')
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `uv run pytest tests/test_create_table.py -q`

Expected: `test_create_offers_the_word_that_finishes_it`, `test_the_longer_clause_wins_over_a_bare_table`, `test_if_not_exists_is_reached_by_typing` and `test_create_table_is_offered_where_a_statement_may_begin` FAIL. The other four pass vacuously — they assert silence, and everything is silent today. That is the point of writing them now: they are the regression guard for Task 2.

- [ ] **Step 3: Add the clause**

In `src/pysqlsuggestions/dialects/ansi.py`, inside the `CLAUSES` tuple, immediately after the `ALTER TABLE` clause and before `CALL`:

```python
        # Modelled before `TABLE` and that order is the whole point: `clause_at`
        # ranks matches by (end offset, word count), so this two-word name beats
        # the bare form ending at the same token — the same rule that answers
        # `DELETE FROM ⌶` with DELETE FROM rather than with the FROM inside it.
        # Without this clause, modelling `TABLE` made `CREATE TABLE t (id ⌶`
        # offer relations, in a definition list where a relation cannot go.
        #
        # `suggests=()` because the relation is being *invented*. Kind.TABLE
        # would offer every relation in the catalog at the one caret where
        # naming an existing one is the single thing that cannot work; WINDOW
        # carries the same empty tuple for the same reason.
        #
        # No `followed_by`, and that is measured rather than forgotten. A
        # clause's continuations reach the caret wherever the clause governs,
        # and parentheses do not change which clause governs — so
        # `followed_by=('AS',)` put `AS` *inside* the definition list, where
        # `CREATE TABLE t (id AS` parses as nothing. What it costs is
        # `CREATE TABLE t AS SELECT …`: `after_as` reads the caret past AS as an
        # alias being invented, so offering the word would lead somewhere that
        # answers nothing. A missing answer, chosen over a wrong one.
        Clause(
            name='CREATE TABLE',
            suggests=(),
            before_the_item=('IF NOT EXISTS',),
        ),
```

Then extend `STATEMENT_START`:

```python
STATEMENT_START = (*EXPLAINABLE, 'DROP TABLE', 'DROP VIEW', 'TRUNCATE', 'ALTER TABLE', 'CALL', 'CREATE TABLE')
```

Leave `EXPLAINABLE` alone — `EXPLAIN CREATE TABLE` is a syntax error.

- [ ] **Step 4: Run the tests and the fast suite**

Run: `uv run pytest tests/test_create_table.py -q && uv run pytest -m 'not integration' -q`

Expected: all eight new tests PASS, and the rest of the suite is unchanged. If anything else fails, stop — a new statement form has reached a position it should not have, and the failure names it.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/dialects/ansi.py tests/test_create_table.py
git commit -m "feat: CREATE TABLE is a clause, so TABLE can become one"
```

Body should record that the two-word name is what lets `clause_at` prefer it over a bare `TABLE`, and that the absent `followed_by` is measured — one leaked `AS` into the definition list.

---

### Task 2: The definition list answers

The mechanism. A new `Clause` field, a new position rule, and the per-dialect word lists.

**Files:**
- Modify: `src/pysqlsuggestions/dialects/base.py` (the `Clause` dataclass, and `Dialect.__post_init__`)
- Modify: `src/pysqlsuggestions/engine/analyse.py` (new `defines_a_column`, and the `Literal` import)
- Modify: `src/pysqlsuggestions/engine/request.py` (the import list, and an early return in `derive_request`)
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (`_COLUMN_CONSTRAINTS`, and `defines_columns` on the clause)
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (the wider list)
- Modify: `src/pysqlsuggestions/dialects/trino.py` (the narrower list)
- Modify: `src/pysqlsuggestions/testing/__init__.py` (one conformance case)
- Test: `tests/test_create_table.py`

**Interfaces:**
- Consumes: the `'CREATE TABLE'` clause from Task 1.
- Produces:
  - `Clause.defines_columns: tuple[str, ...] = ()`
  - `analyse.defines_a_column(tokens: Sequence[Token], lo: int, hi: int, caret: int, clause: str | None, clauses: ClauseModel) -> Literal['name', 'type', 'constraint'] | None`
  - `ansi._COLUMN_CONSTRAINTS: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_create_table.py`:

```python
def test_a_column_name_is_the_authors_to_invent() -> None:
    """
    The first word of each item names something that does not exist yet.

    The same silence `opens_a_name_list` gives a column alias list, reached by a
    different rule: an alias list renames existing columns and never takes a
    type, so the two are separate lists rather than one.
    """
    assert offered('CREATE TABLE t (') == []
    assert offered('CREATE TABLE t (id integer, ') == []


def test_a_type_belongs_after_the_name() -> None:
    """
    `Kind.TYPE`, answered from `dialect.types` — the list `CAST(x AS ⌶)` reads.

    `docs/gaps.md` predicted exactly this: "the candidates already exist".
    """
    found = offered('CREATE TABLE t (id ')
    assert 'text' in found
    assert 'integer' in found


def test_the_type_position_offers_no_relation() -> None:
    """A definition list is not a FROM list, however alike the parens look."""
    found = offered('CREATE TABLE t (id ')
    assert 'users' not in found
    assert 'orders' not in found


def test_a_multi_word_type_is_offered_whole() -> None:
    """
    Which is what pays for the trade the count makes — see the test below.

    Offered from the one caret where a type begins, so accepting it never
    reaches the caret that cannot finish it.
    """
    assert 'double precision' in offered('CREATE TABLE t (id ')


def test_a_hand_typed_half_of_a_two_word_type_reaches_constraints() -> None:
    """
    The known cost of counting words rather than parsing the item.

    `double ` is two words in, so it reads as a constraint position and
    `precision` is not offered. Deliberate: the alternative offers the type list
    at every caret past the name, which puts a second type after a complete one
    and writes `id integer text`. A missing answer for a hand-typist beats a
    wrong answer for everyone.
    """
    assert 'precision' not in offered('CREATE TABLE t (id double ')


def test_constraints_follow_a_type() -> None:
    """The clause's own `defines_columns`, carried on `continues`."""
    found = offered('CREATE TABLE t (id integer ')
    assert 'NOT NULL' in found
    assert 'PRIMARY KEY' in found
    assert 'REFERENCES' in found


def test_a_constraint_may_follow_a_constraint() -> None:
    """`id integer NOT NULL DEFAULT 0` is one item with two of them."""
    assert 'DEFAULT' in offered('CREATE TABLE t (id integer NOT NULL ')


def test_a_type_is_not_offered_where_a_constraint_belongs() -> None:
    """`CREATE TABLE t (id integer text` parses as nothing."""
    assert 'text' not in offered('CREATE TABLE t (id integer ')


def test_a_nested_paren_is_not_the_definition_list() -> None:
    """
    Every construct that nests sits one level deeper, and the depth test
    excludes all of them without naming any: a type's own parameters, a column
    CHECK, a foreign key's column list.
    """
    assert offered('CREATE TABLE t (id numeric(10, ') == []
    assert offered('CREATE TABLE t (id integer CHECK (id > ') == []
    assert offered('CREATE TABLE t (id integer REFERENCES users (') == []


def test_a_qualified_name_still_opens_a_definition_list() -> None:
    """`CREATE TABLE public.t (…)` is the same list, written with a schema."""
    assert 'text' in offered('CREATE TABLE public.t (id ')


def test_a_half_typed_column_name_is_still_a_name() -> None:
    """
    The word under the caret is being typed, not finished.

    Counting it would make the first character of every column name look like a
    completed name and answer with types.
    """
    assert offered('CREATE TABLE t (i') == []


def test_a_half_typed_type_is_narrowed_by_its_prefix() -> None:
    """The word under the caret is skipped by the count and used by the ranker."""
    assert 'integer' in offered('CREATE TABLE t (id int')


def test_a_definition_list_outside_the_clause_is_untouched() -> None:
    """
    `INSERT INTO orders (⌶` is a column list of an existing relation, and the
    rule must not reach it — its clause declares no `defines_columns`.
    """
    assert 'id' in offered('INSERT INTO orders (')
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_create_table.py -q`

Expected: the four that assert silence and `test_a_definition_list_outside_the_clause_is_untouched` PASS; the eight asserting a type or a constraint FAIL. Note that `test_the_definition_list_is_not_offered_the_clause_continuations` from Task 1 must still pass at every step after this — it is the guard that the new rule did not reintroduce the leak.

- [ ] **Step 3: Add the field to `Clause`**

In `src/pysqlsuggestions/dialects/base.py`, in the `Clause` dataclass, directly after `opens_a_group`:

```python
    defines_columns: tuple[str, ...] = ()
    """
    Words that may follow a column's type in this clause's parenthesised list.

    A non-empty tuple is also what marks the clause as opening one, the way
    `opens_a_group` marks a clause as opening a body. A separate flag beside the
    list would let a dialect declare a definition list with no constraint words
    and get silence at every caret past a type — a state worth making
    unspellable.

    Not `opens_a_group`, which names what may *begin* a group. A definition list
    has no opening word; it has an alternation. The names in it are the author's
    to invent and this engine has nothing to invent them from, so only the
    second half of each item can be answered at all.
    """
```

Then fold its words into `keywords`, in `Dialect.__post_init__`:

```python
        spoken = {
            word.upper()
            for clause in self.clauses.clauses
            for phrase in (
                clause.name,
                *clause.followed_by,
                *clause.after_operand,
                *clause.opens_a_group,
                *clause.defines_columns,
            )
            for word in phrase.split()
        }
```

That fold is load-bearing: `KEY` is in no dialect's `RESERVED`, so without it the second half of `PRIMARY KEY` would read as an identifier to the analyser.

- [ ] **Step 4: Add the position rule**

In `src/pysqlsuggestions/engine/analyse.py`, add `Literal` to the typing import:

```python
from typing import Literal
```

Then add the function directly after `opens_a_name_list` and its helper `_plain_word`:

```python
def defines_a_column(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    clause: str | None,
    clauses: ClauseModel,
) -> Literal['name', 'type', 'constraint'] | None:
    """
    Where in a parenthesised column definition the caret sits, or None if outside one.

    `CREATE TABLE t (id integer NOT NULL, email text)` is an alternation rather
    than a list of one thing: each item invents a name, then names a type, then
    takes any number of constraints. Only the last two can be answered — a name
    being invented has nothing behind it in any catalog.

    Which of the three it is comes from counting the item's plain words since
    the last comma, at the list's own depth. Counting rather than parsing is
    what makes every nested construct fall out for free: `numeric(10, 2)`,
    `CHECK (x > 0)`, `REFERENCES users (id)` and `PRIMARY KEY (a, b)` all sit
    one level deeper, and none of them is named here.

    A two-word type — `double precision` — reads as a constraint position,
    because a count cannot see that the type is unfinished. Deliberate: the
    caret before it offers `double precision` whole, so only somebody typing the
    first word by hand reaches the bad caret. The alternative, offering types at
    every caret past the name, writes `id integer text`.
    """
    governing = clauses.get(clause) if clause else None
    if governing is None or not governing.defines_columns:
        return None

    opening = _definition_paren(tokens, lo, hi, caret, governing.name, clauses)
    if opening < 0 or depth_at(tokens, caret) != tokens[opening].depth + 1:
        return None

    # The word under the caret is being typed rather than finished, so it is not
    # part of the count: without this the first character of a column name looks
    # like a completed name and the position answers with types.
    last = _index_before(tokens, caret)
    if last >= 0 and tokens[last].type is TokenType.IDENT and tokens[last].end >= caret:
        last -= 1

    depth = tokens[opening].depth + 1
    words = 0
    for index in range(opening + 1, last + 1):
        token = tokens[index]
        if token.type in _SKIP or token.depth != depth:
            continue
        if token.type is TokenType.PUNCT and token.text == ',':
            words = 0
        elif token.type is TokenType.IDENT:
            words += 1
    if words == 0:
        return 'name'
    return 'type' if words == 1 else 'constraint'


def _definition_paren(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    name: str,
    clauses: ClauseModel,
) -> int:
    """
    Index of the first `(` after the clause called `name`, or -1.

    The clause's own list is the first group it opens, and finding it by
    position is what lets the caller tell `CREATE TABLE t (id ⌶` from
    `CREATE TABLE t (id numeric(⌶`. Both are governed by the same clause and
    only their depth relative to *this* paren separates them.
    """
    after = -1
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.IDENT or token.quoted or token.end >= caret:
            continue
        matched = _clause_starting_at(tokens, index, hi, clauses)
        if matched is not None and matched[0] == name:
            after = matched[1]
    if after < 0:
        return -1
    for index in range(after, hi):
        token = tokens[index]
        if token.start >= caret:
            break
        if token.type is TokenType.PUNCT and token.text == '(':
            return index
    return -1
```

- [ ] **Step 5: Wire it into the request**

In `src/pysqlsuggestions/engine/request.py`, add `defines_a_column` to the `engine.analyse` import list (alphabetical — it goes between `depth_at` and `in_literal`), and add `Clause` to the `dialects.base` import:

```python
from pysqlsuggestions.dialects.base import Clause, Dialect
```

Then, in `derive_request`, directly after the `opens_a_name_list` block and before `continues, only = _continues(...)`:

```python
    defines = defines_a_column(tokens, lo, hi, caret, clause, dialect.clauses)
    if defines is not None:
        return _defining_a_column(defines, dialect.clauses.get(clause) if clause else None, clause, scope, prefix, span)
```

And add the helper beside `_inside_a_literal`:

```python
def _defining_a_column(
    where: Literal['name', 'type', 'constraint'],
    governing: Clause | None,
    clause: str | None,
    scope: Scope | None,
    prefix: str,
    span: tuple[int, int],
) -> Request:
    """
    What a caret inside a parenthesised column definition admits.

    Three positions and three answers. A name being invented has none. A type
    comes from the dialect's own list, which `CAST(x AS ⌶)` already reads. The
    constraints ride on `continues` rather than on the clause's continuations,
    because that is what the field means — words finishing the construct under
    the caret, where a clause's own list would be talking about the statement.

    An early return like `in_placeholder` above, rather than a narrowing of what
    follows: both halves of the answer have to go quiet in the name position,
    the kinds as much as the keywords.
    """
    if where == 'type':
        return Request(
            kinds=(Kind.TYPE,),
            prefix=prefix,
            replace_span=span,
            clause=clause,
            scope=scope,
            expecting='type',
        )
    if where == 'constraint' and governing is not None:
        return Request(
            kinds=(Kind.KEYWORD,),
            prefix=prefix,
            replace_span=span,
            clause=clause,
            scope=scope,
            continues=governing.defines_columns,
        )
    return Request(kinds=(), prefix=prefix, replace_span=span, clause=clause, scope=scope)
```

- [ ] **Step 6: Declare the words, per dialect**

In `src/pysqlsuggestions/dialects/ansi.py`, above `CLAUSES`:

```python
_COLUMN_CONSTRAINTS = ('NOT NULL', 'NULL', 'DEFAULT', 'PRIMARY KEY')
"""
What may follow a column's type, in the baseline.

The four that at least two of the three shipped backends accept, verified
against the containers rather than read off the standard. UNIQUE, REFERENCES and
CHECK are Postgres's alone here — ClickHouse and Trino both refuse all three —
so they are declared there, the way DROP SEQUENCE is.
"""
```

Add the field to the clause added in Task 1:

```python
        Clause(
            name='CREATE TABLE',
            suggests=(),
            before_the_item=('IF NOT EXISTS',),
            defines_columns=_COLUMN_CONSTRAINTS,
        ),
```

In `src/pysqlsuggestions/dialects/postgres.py`, inside `ANSI.clauses.extend(...)`:

```python
        # Three more constraints than the baseline, each verified against the
        # server: ClickHouse and Trino refuse all three, so they cannot go in
        # ANSI without offering words their parsers reject.
        replace(
            _ansi('CREATE TABLE'),
            defines_columns=(*_ansi('CREATE TABLE').defines_columns, 'UNIQUE', 'REFERENCES', 'CHECK'),
        ),
```

In `src/pysqlsuggestions/dialects/trino.py`, inside `ANSI.clauses.extend(...)`:

```python
        # Trino takes `NOT NULL` in a column definition and nothing else — NULL,
        # DEFAULT and PRIMARY KEY are all `mismatched input … Expecting: ')', ','`.
        # Restated rather than refined through a helper, which is safe only
        # because this clause deliberately carries no `followed_by`: the trap
        # `postgres.py`'s `_ansi` exists to close is a hand-copied continuation
        # list falling behind the canonical clause order, and there is none here.
        Clause(
            name='CREATE TABLE',
            suggests=(),
            before_the_item=('IF NOT EXISTS',),
            defines_columns=('NOT NULL',),
        ),
```

ClickHouse inherits ANSI's four unchanged — all four are verified there, including `PRIMARY KEY`.

- [ ] **Step 7: Add the conformance case**

In `src/pysqlsuggestions/testing/__init__.py`, in `DialectConformance.cases`, beside the `opens_a_group` case:

```python
        # Found by what the clause declares rather than by the name
        # `CREATE TABLE`, so a dialect spelling its DDL differently is still
        # covered. A new field that changes what a caret admits belongs here:
        # the corpus ships in the wheel for third-party dialects, which have no
        # other test at all.
        defines = next((c for c in dialect.clauses.clauses if c.defines_columns), None)
        if defines is not None and dialect.types:
            cases.append(
                Case(
                    name='a definition list answers its type position with a type',
                    sql=f'{defines.name} t (id ',
                    expect=(dialect.types[0],),
                    forbid=('users',),
                ),
            )
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_create_table.py tests/test_conformance.py -q && uv run pytest -m 'not integration' -q`

Expected: every test in `tests/test_create_table.py` passes, including the four silence assertions from Task 1. Full suite green.

- [ ] **Step 9: Run the gate**

Run: `./scripts/check.sh`

Expected: green. `mypy strict` is the one most likely to complain here — `defines_a_column`'s return type is a `Literal` union and `_defining_a_column` must exhaust it.

- [ ] **Step 10: Commit**

```bash
git add src/pysqlsuggestions tests/test_create_table.py
git commit -m "feat: a definition list answers with types, then constraints"
```

Body should say why the rule counts words at a depth (every nested construct falls out for free), what the count costs (`double ⌶` reaches constraints rather than `precision`), and why that trade was taken.

---

### Task 3: The `TABLE` statement form

`TABLE t` is `SELECT * FROM t`. Three dialect facts here were measured against the containers, not argued.

**Files:**
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (`_QUERY`, the `TABLE` clause, `STATEMENT_START`)
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (`ONLY`, and deleting the comment that refused the form)
- Modify: `src/pysqlsuggestions/dialects/clickhouse.py` (remove the clause and the statement start)
- Modify: `src/pysqlsuggestions/engine/analyse.py` (`_RELATION_CLAUSES`)
- Test: `tests/test_statement_forms.py`

**Interfaces:**
- Consumes: the `'CREATE TABLE'` clause from Task 1, which is what keeps this clause from capturing the definition list.
- Produces: a `'TABLE'` clause in `ANSI.clauses` and in `ANSI.statement_start`, absent from `CLICKHOUSE`.

- [ ] **Step 1: Write the failing tests**

Add the ClickHouse import at the top of `tests/test_statement_forms.py`, above the Postgres one:

```python
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
```

Then append:

```python
def test_the_table_form_offers_relations() -> None:
    """
    `TABLE users` is `SELECT * FROM users`, and it was silent.

    Blocked until now on `CREATE TABLE`: a bare `TABLE` clause captured the
    definition list, because nothing longer was there to win the match.
    """
    found = offered('TABLE ')
    assert 'users' in found
    assert 'orders' in found


def test_the_table_form_takes_the_query_tail() -> None:
    """
    A result set to shape, so ORDER BY and LIMIT belong after it.

    Both verified against Postgres, and Trino's parser enumerated the whole set
    when it refused something else.
    """
    found = offered('TABLE users ')
    assert 'ORDER BY' in found
    assert 'LIMIT' in found
    assert 'UNION' in found


def test_the_table_form_puts_its_relation_in_scope() -> None:
    """
    Without this the tail offers the columns of every relation in the catalog.

    A wrong answer created by modelling the form, which is why
    `_RELATION_CLAUSES` had to grow with it rather than after it.

    Asserted on the scope rather than on the offered text, because how a column
    is *written* there — bare or qualified — is a separate decision that
    `tests/test_ambiguous_relations.py` already pins.
    """
    scope = derive_request('TABLE users ORDER BY ', len('TABLE users ORDER BY '), POSTGRES).scope
    assert scope is not None
    assert [relation.label for relation in scope.relations] == ['users']
    assert not any(text.startswith('orders') for text in offered('TABLE users ORDER BY '))


def test_clickhouse_has_no_table_form_at_all() -> None:
    """
    It answers `TABLE report_executions` with a syntax error at position 1.

    The `CALL` case exactly: inheriting a clause from the baseline would offer a
    word whose statement the server rejects outright, so both the clause and the
    statement start have to go.
    """
    assert CLICKHOUSE.clauses.get('TABLE') is None
    assert 'TABLE' not in CLICKHOUSE.statement_start
    assert CLICKHOUSE.clauses.get('CREATE TABLE') is not None


def test_a_second_relation_does_not_follow_the_first() -> None:
    """`TABLE users orders` parses as nothing."""
    assert 'orders' not in offered('TABLE users ')


def test_postgres_offers_only_before_the_relation() -> None:
    """
    `ONLY` is Postgres's: Trino answers `TABLE ONLY t` with mismatched input.

    Behind a prefix, like every `before_the_item` word.
    """
    assert offered('TABLE on') == ['ONLY']
    assert 'users' in offered('TABLE ONLY ')


def test_truncate_table_still_offers_relations() -> None:
    """
    `TRUNCATE` is one word, so the new clause matches the `TABLE` after it and
    wins on end offset. The answer is the same either way — both positions want
    a relation — and this pins that it stayed the same.
    """
    assert 'users' in offered('TRUNCATE TABLE ')


def test_drop_table_still_beats_the_bare_form() -> None:
    """
    Two words to one at the same end offset, which is the tiebreak `clause_at`
    applies. Without it `DROP TABLE ⌶` would lose its kind narrowing.
    """
    assert clause_at_end('DROP TABLE ') == 'DROP TABLE'
    assert clause_at_end('ALTER TABLE ') == 'ALTER TABLE'
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_statement_forms.py -q`

Expected: four FAIL — the ones asserting the form answers something
(`offers_relations`, `takes_the_query_tail`, `puts_its_relation_in_scope`,
`postgres_offers_only`). Four PASS: the two guarding `TRUNCATE TABLE` and
`DROP TABLE`, the ClickHouse case (which is already true and must stay true),
and `a_second_relation_does_not_follow_the_first`, which passes vacuously while
the position is silent and becomes load-bearing at Step 4.

- [ ] **Step 3: Widen `_QUERY`**

In `src/pysqlsuggestions/dialects/ansi.py`:

```python
_QUERY = frozenset({'SELECT', 'TABLE'})
"""
The statement forms that have a result set to shape.

GROUP BY, ORDER BY, LIMIT and the rest belong to a query and to nothing else. An
UPDATE or a DELETE has no result to group or order, and every one of these
offered after a finished one wrote SQL the server refuses.

`TABLE t` is `SELECT * FROM t`, so it is one of them — `TABLE t ORDER BY id` and
`TABLE t LIMIT 1` both run. Naming it here only *permits*: what is offered still
comes from the clause's own `followed_by` and from clauses declaring `follows`,
neither of which reaches GROUP BY from TABLE.
"""
```

- [ ] **Step 4: Add the clause and the statement start**

In `src/pysqlsuggestions/dialects/ansi.py`, after the `CREATE TABLE` clause:

```python
        # `TABLE t` is `SELECT * FROM t`. Modellable only now that CREATE TABLE
        # exists: `clause_at` prefers the longer name at the same end offset, so
        # the definition list is governed by that clause and not by this one.
        # DROP TABLE and ALTER TABLE are protected by the same tiebreak.
        #
        # `followed_by` is Trino's own list, taken from the parser: refusing
        # `TABLE t ONLY` it reported "Expecting: '.', 'EXCEPT', 'FETCH',
        # 'INTERSECT', 'LIMIT', 'OFFSET', 'ORDER', 'UNION', <EOF>" — which is
        # `_onwards('UNION')` exactly.
        #
        # `ONLY` is not here. Postgres takes `TABLE ONLY t` and Trino does not,
        # so it is declared in postgres.py.
        Clause(name='TABLE', suggests=RELATION_REFERENCE, followed_by=_onwards('UNION')),
```

and:

```python
STATEMENT_START = (
    *EXPLAINABLE,
    'DROP TABLE',
    'DROP VIEW',
    'TRUNCATE',
    'ALTER TABLE',
    'CALL',
    'CREATE TABLE',
    'TABLE',
)
```

`TRUNCATE` must stay ahead of `TABLE` in this tuple: `statement_form` reads it in order, and `TRUNCATE TABLE users` matches both.

- [ ] **Step 5: Put the relation in scope**

In `src/pysqlsuggestions/engine/analyse.py`:

```python
_RELATION_CLAUSES = frozenset({'FROM', 'JOIN', 'UPDATE', 'DELETE FROM', 'INSERT INTO', 'TABLE'})
"""
Clauses whose items are relations, so a scope is built from them.

`TABLE` is here for the same reason as `FROM`, and adding it is not optional:
without it `TABLE users ORDER BY ⌶` offers the columns of every relation the
catalog holds, which is a wrong answer that modelling the form would have
created.
"""
```

- [ ] **Step 6: Give Postgres `ONLY`, and delete the comment that refused the form**

In `src/pysqlsuggestions/dialects/postgres.py`, delete the comment block that runs from

```
        # `TABLE t` is `SELECT * FROM t` and is deliberately *not* modelled.
```

down to and including

```
        # own.
```

— the two paragraphs refusing the form and naming its precondition. Stop there: the paragraph beginning "These three exist to make a caret stop answering" belongs to `TABLESAMPLE`, `SEARCH` and `CYCLE`, which are still exactly three, so it stays untouched.

In its place put:

```python
        # `ONLY` is Postgres's alone. Trino runs `TABLE t` and answers
        # `TABLE ONLY t` with mismatched input, so the word cannot go in the
        # baseline — and ClickHouse has no `TABLE` form at all.
        #
        # This clause was refused for three releases, and the comment that
        # refused it named the fix: a statement form is found by the first word
        # that starts one, and TABLE is a word inside CREATE TABLE, so modelling
        # it alone made `CREATE TABLE t (id ⌶` offer relations. With the longer
        # clause modelled the tiebreak in `clause_at` settles it, exactly as
        # predicted.
        replace(_ansi('TABLE'), before_the_item=('ONLY',)),
```

Keep the paragraph that follows it — the one beginning "These three exist to make a caret stop answering" — since it belongs to `TABLESAMPLE`, `SEARCH` and `CYCLE`, and reword its opening from "These three" to name them, since `TABLE` is no longer one of the group it introduced.

- [ ] **Step 7: Take it away from ClickHouse**

In `src/pysqlsuggestions/dialects/clickhouse.py`:

```python
    # ClickHouse has neither CALL nor the `TABLE t` query form. Its parser
    # answers each with a syntax error whose message lists what it does accept —
    # `TABLE report_executions` fails at position 1 — and none of them is this.
    # Both the clause and the statement start have to go in each case: the
    # conformance corpus reports a statement start whose clause is missing, so
    # doing only one of the two fails the suite.
    statement_start=tuple(phrase for phrase in ANSI.statement_start if phrase not in {'CALL', 'TABLE'}),
    clauses=ANSI.clauses.without('CALL', 'TABLE').extend(
```

- [ ] **Step 8: Run the tests and the gate**

Run: `uv run pytest tests/test_statement_forms.py -q && ./scripts/check.sh`

Expected: all seven new tests pass; the full gate green. Watch `tests/test_relation_kinds.py` and `tests/test_conformance.py` in particular — the first pins `DROP TABLE`'s narrowing, and the second checks that every `statement_start` phrase names a declared clause on all three dialects.

- [ ] **Step 9: Commit**

```bash
git add src/pysqlsuggestions tests/test_statement_forms.py
git commit -m "feat: TABLE is a statement form, three releases after it was refused"
```

Body should record the three measurements: ClickHouse rejects the form, Trino rejects `ONLY`, and Trino's parser supplied the continuation list.

---

### Task 4: The grammar cases, and the record

The suite that has been counting this gap stops counting it.

**Files:**
- Modify: `tests/grammar/cases.py` (three cases, and one constant's docstring)
- Modify: `docs/gaps.md` (entry 1 moves)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: nothing further.

- [ ] **Step 1: Measure before marking**

Before editing anything, check which dialects each of the three cases really holds on. `TABLE ONLY` is expected to be Postgres's, and `TABLE ⌶` to hold on Trino too — but the suite's discipline is that a marking is measured first.

Run:

```bash
uv run python -c "
from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from tests.test_grammar_select import SNAPSHOT, FUNCTIONS
for name, d in (('postgres', POSTGRES), ('clickhouse', CLICKHOUSE), ('trino', TRINO)):
    for sql in ('TABLE ', 'TABLE on', 'TABLE ONLY '):
        got = [s.text for s in complete(sql, len(sql), d, MemoryCatalog(SNAPSHOT, functions=FUNCTIONS))]
        print(f'{name:<11}{sql!r:<14} {got[:5]}')
"
```

Record what it prints; the markings below assume `TABLE ⌶` holds on Postgres and Trino and the other two on Postgres alone. **If the measurement disagrees, follow the measurement** and adjust the `dialects=` values — that is the whole reason this step is first.

- [ ] **Step 2: Un-pend the three cases**

In `tests/grammar/cases.py`, replace the three cases under `# --- the TABLE form ---` with:

```python
    GrammarCase(
        sql='TABLE ⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('users',),
        dialects=_POSTGRES_AND_TRINO,
        note='waited on CREATE TABLE being modelled, so the longer clause name wins the match',
    ),
    GrammarCase(
        sql='TABLE on⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('ONLY',),
        note='ONLY is Postgres\'s: Trino answers `TABLE ONLY t` with mismatched input',
    ),
    GrammarCase(
        sql='TABLE ONLY ⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('users',),
        note='ONLY is Postgres\'s: Trino answers `TABLE ONLY t` with mismatched input',
    ),
```

The `pending=True` and `refused=` lines go with them — the production is no longer refused, and a `refused` note that no longer applies is worse than none.

- [ ] **Step 3: Widen the shared constant's docstring**

`_POSTGRES_AND_TRINO` now covers two facts, not one. Replace its docstring:

```python
_POSTGRES_AND_TRINO = ('postgres', 'trino')
"""
Two things ClickHouse does not have, and the cases that divide on them.

Trino declares `TABLESAMPLE` and ClickHouse does not — three cases. And
ClickHouse answers `TABLE users` with a syntax error at position 1, where
Postgres and Trino both run it — one more.

They are the reason `dialects` is a tuple rather than a boolean: `shared` would
have had to mean "all of them", and these are not.
"""
```

- [ ] **Step 4: Run the suite and read the burn-down**

Run: `uv run pytest tests/test_grammar_select.py -q`

Expected: no xfail-strict failures, and the burn-down reads:

```
grammar burn-down: 60/69 SELECT positions answered, 6 of the 9 gaps refused
```

If it reads anything else, the arithmetic in this plan is wrong and the printed number is right — reconcile before continuing.

- [ ] **Step 5: Move the gaps entry**

In `docs/gaps.md`, delete section `## 1. CREATE TABLE`, renumber `## 2. History ranking` to `## 1.`, and add to **Closed since this list was written**:

```markdown
- **CREATE TABLE.** `CREATE TABLE t (id ⌶` offers types, then the constraints
  that may follow one, and `TABLE t` is a statement form at last.

  This entry called it "the clause model and nothing else". It was not: a
  definition list has no opening word, so `opens_a_group` could not carry it and
  the alternation of name-then-type needed a position rule in `engine/`. What
  the entry did get right is that the candidates already existed —
  `dialect.types` answers the type position with no new plumbing at all.

  The entry also predicted why `TABLE` was blocked and what would unblock it,
  and both held: `clause_at` ranks by (end offset, word count), so modelling
  `CREATE TABLE` first is what stops the bare form capturing the definition
  list. DROP TABLE and ALTER TABLE were already relying on that same tiebreak.

  Which words each backend takes was measured, not read off the standard.
  ClickHouse rejects `TABLE t` outright and Trino rejects `ONLY`; of the column
  constraints only `NOT NULL` is common to all three, and Trino takes nothing
  else. The advice about being deliberate stands and is why `CREATE VIEW`,
  `CREATE INDEX` and `CREATE TABLE … AS SELECT` are still not here.
```

- [ ] **Step 6: Write the changelog**

In `CHANGELOG.md`, under the Unreleased section's heading for what changes at a caret:

```markdown
- `CREATE TABLE t (id ⌶` offers types, and the caret past one offers the
  constraints that may follow it — `NOT NULL`, `DEFAULT`, `PRIMARY KEY` and, on
  Postgres, `UNIQUE`, `REFERENCES` and `CHECK`. A column name being invented
  still answers nothing, as does every nested paren: a type's parameters, a
  `CHECK`, a foreign key's column list.
- `CREATE ⌶` answers `TABLE`, and `CREATE TABLE if⌶` answers `IF NOT EXISTS`.
- `TABLE users ⌶` is a statement form: the relation, then the query tail it
  shares with a SELECT. `TABLE ONLY ⌶` on Postgres, which is the only backend
  that takes the word. ClickHouse has no such form and is not offered one.
- `CREATE TABLE t AS SELECT …` is still not offered. The clause carries no
  continuations at all, because a clause's own reach into its parentheses put
  `AS` where `CREATE TABLE t (id AS` parses as nothing.
```

- [ ] **Step 7: Run the whole gate**

Run: `./scripts/check.sh`

Expected: green, with `60/69` in the burn-down.

- [ ] **Step 8: Commit**

```bash
git add tests/grammar/cases.py docs/gaps.md CHANGELOG.md
git commit -m "test: the three TABLE cases stop waiting"
```

Body should note that the burn-down moved 57/69 → 60/69 and that the `refused` notes naming this work were deleted rather than reworded.

---

## Verification

After Task 4, the whole gate:

```bash
./scripts/check.sh
```

Expected: green. Burn-down:

```
corpus burn-down: 34/34 golden requests passing
report_service suite: 158/158 passing, 0 known gaps
grammar burn-down: 60/69 SELECT positions answered, 6 of the 9 gaps refused
  also holding: 36 on clickhouse, 40 on trino
```

The `also holding` line rises on Trino by one — `TABLE ⌶` — and is unchanged on ClickHouse.

Integration tests are not part of this. What the engine offers is decided before a catalog is consulted, and the containers already settled which words belong to which dialect.
