# A Qualifier That Is a Path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A column reference is never ambiguous — when two same-named relations
are in scope, each column carries its relation's full path instead of a name the
server cannot resolve.

**Architecture:** `Candidate.qualifier` becomes a tuple of segments and `rank`
quotes them one by one. `resolve` decides, in two places, when a label collides:
among the statement's in-scope relations, and among the columns a prefix search
returned before any `FROM` exists. Nothing in `engine/` changes.

**Tech Stack:** Python 3.10+, no runtime dependencies. `uv run pytest`,
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.

## Global Constraints

- **Python 3.10 floor.** No `match`, no `X | Y` in `isinstance`, no starred
  expression directly inside a subscript.
- **Zero runtime dependencies.** Standard library only in `src/`.
- **Line length 120, single quotes.** `ruff format` decides; do not hand-wrap.
- **Docstrings required** (`ruff` rule set `D`) on every public module, class,
  function and method. Say what the thing is *for* and why it is the way it is.
- **`engine/` may not import `ports` or `resolve`** — `tests/test_purity.py`
  enforces it. Nothing in this plan needs to.
- **Every task ends green.** `uv run pytest`, `ruff check`,
  `ruff format --check` and `mypy` all clean before the commit.
- **Guiding principle:** a missing answer costs a keystroke; a wrong one costs
  correctness.
- **Nothing changes without a collision.** A single-schema database must produce
  byte-identical output. This is the constraint the whole design is shaped
  around, and Task 2 pins it.
- Backends: `docker compose -f docker/docker-compose.yml up --wait`.

---

## File Structure

**Modified — library**

| file | change |
|---|---|
| `src/pysqlsuggestions/types.py` | `Candidate.qualifier: tuple[str, ...]` |
| `src/pysqlsuggestions/engine/rank.py` | `_render` quotes each segment |
| `src/pysqlsuggestions/resolve.py` | five call sites converted; `_ambiguous_labels`, `_qualifier_for`, `_loose_columns`; `_expansion` |
| `src/pysqlsuggestions/testing/__init__.py` | `Case.expect_exact`; a second same-named relation; one case |

**Modified — fixtures, docs**

`docker/postgres/01-schema.sql`, `tests/integration/test_backends.py`,
`CHANGELOG.md`.

**Created**

`tests/test_ambiguous_relations.py` — every position, one proposition.

**The five `qualify=` call sites**, all in `resolve.py`, so no task has to
search for them:

| line | call |
|---|---|
| 329 | `_columns_of(relation, reader, seen, qualify=relation.label or None)` |
| 340 | `_column_candidate(c, qualify=c.table, relation=(c.schema, c.table))` |
| 352 | `_table_candidate(table, qualify=table.schema)` |
| 616 | `_table_candidate(table, qualify=qualify, kind=Kind.SEQUENCE)` |
| 806 | `_columns_of(star, reader, seen, label=label, qualify=qualify)` |

---

## Task 1: the qualifier becomes a tuple

**Files:**
- Modify: `src/pysqlsuggestions/types.py` (`Candidate.qualifier`)
- Modify: `src/pysqlsuggestions/engine/rank.py` (`_render`)
- Modify: `src/pysqlsuggestions/resolve.py` (four signatures, five call sites)
- Test: `tests/test_ambiguous_relations.py` (new)

**Interfaces:**
- Produces: `Candidate.qualifier: tuple[str, ...] = ()`. Empty means
  unqualified, where `None` did. `_columns_of`, `_from_projection`,
  `_column_candidate` and `_table_candidate` all take
  `qualify: tuple[str, ...] = ()`.

**This task changes no behaviour.** It is the type change alone, and the whole
existing suite is its regression test. The one new test proves the new type can
express something the old one could not.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambiguous_relations.py`:

```python
"""
Two relations with the same name, in different schemas, both in scope.

Postgres allows it — `SELECT 1 FROM public.invoices, billing.invoices` plans,
and the second is aliased internally as `invoices_1` — and then refuses every
bare reference to either: `table reference "invoices" is ambiguous`. So this is
the one position where the engine wrote SQL that does not run.
"""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.types import Candidate, Kind, Request


def test_a_qualifier_of_several_segments_renders_as_a_path() -> None:
    """
    Each segment is quoted on its own. A dotted string in a single-segment field
    would come back as `"public.invoices"` — one quoted name, and not a path,
    which is why the field's type had to change rather than its contents.
    """
    request = Request(kinds=(Kind.COLUMN,), prefix='', replace_span=(0, 0))
    candidate = Candidate(text='amount', kind=Kind.COLUMN, qualifier=('public', 'invoices'))
    [found] = rank([candidate], request, POSTGRES)
    assert found.text == 'public.invoices.amount'


def test_a_segment_that_needs_quoting_gets_it_alone() -> None:
    """A mixed-case relation in a lowercase-folding dialect: only that segment is quoted."""
    request = Request(kinds=(Kind.COLUMN,), prefix='', replace_span=(0, 0))
    candidate = Candidate(text='amount', kind=Kind.COLUMN, qualifier=('billing', 'MonthlyTotals'))
    [found] = rank([candidate], request, POSTGRES)
    assert found.text == 'billing."MonthlyTotals".amount'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambiguous_relations.py -v`
Expected: FAIL — `quote_if_needed` receives a tuple, or the rendered text is
`('public', 'invoices').amount`.

- [ ] **Step 3: Change the field**

In `src/pysqlsuggestions/types.py`, replace `Candidate.qualifier` and its
docstring:

```python
    qualifier: tuple[str, ...] = ()
    """
    Path to prefix on insertion, when a bare name would be ambiguous.

    Usually one segment — the relation's label. Two when that label names more
    than one relation in scope: `FROM public.invoices, billing.invoices` makes
    `invoices.amount` a reference the server refuses, and only
    `public.invoices.amount` says which one is meant.

    A tuple rather than a dotted string because each segment is quoted
    separately. `quote_if_needed('public.invoices')` would produce
    `"public.invoices"` — one name containing a dot, which resolves to nothing.

    Matching still runs against `text`, so typing `na` finds `r.name`: the
    qualifier is about what gets inserted, not what has to be typed to find it.
    """
```

- [ ] **Step 4: Render it**

In `src/pysqlsuggestions/engine/rank.py`, in `_render`, replace the qualifier
branch:

```python
    if candidate.qualifier:
        prefix = '.'.join(quote_if_needed(part, dialect) for part in candidate.qualifier)
        return f'{prefix}.{text}', ()
```

- [ ] **Step 5: Widen the four signatures**

In `src/pysqlsuggestions/resolve.py`, change `qualify: str | None = None` to
`qualify: tuple[str, ...] = ()` in all four of `_columns_of`,
`_from_projection`, `_column_candidate` and `_table_candidate`. The bodies need
no change: `_from_projection` and `_column_candidate` pass `qualifier=qualify`
straight through, and `_table_candidate`'s `position=1 if qualify else 0` reads
an empty tuple as falsy exactly as it read `None`.

- [ ] **Step 6: Convert the five call sites**

In `src/pysqlsuggestions/resolve.py`, in the order they appear:

```python
                candidates += _columns_of(relation, reader, seen, qualify=(relation.label,) if relation.label else ())
```

```python
                _column_candidate(c, qualify=(c.table,), relation=(c.schema, c.table))
```

```python
            _table_candidate(table, qualify=(table.schema,))
```

```python
        return [
            _table_candidate(table, qualify=(qualify,) if qualify else (), kind=Kind.SEQUENCE)
            for table, qualify in found
        ]
```

The fifth, in `_from_projection`, passes `qualify` through unchanged and needs
no edit — its type is now a tuple by the signature change in Step 5.

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. Any failure here is a call site missed in Step 6, not a
behaviour change — this task deliberately has none.

- [ ] **Step 8: Commit**

```bash
git add -A src tests
git commit -m "refactor: a candidate's qualifier is a path, not a name"
```

---

## Task 2: a collision in scope lengthens the reference

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py` (`_unqualified` COLUMN branch, two new helpers)
- Test: `tests/test_ambiguous_relations.py`

**Interfaces:**
- Consumes: `qualify: tuple[str, ...]` from Task 1.
- Produces: `_ambiguous_labels(relations: Sequence[Relation]) -> frozenset[str]`
  and `_qualifier_for(relation: Relation, ambiguous: frozenset[str]) -> tuple[str, ...]`.

**The rule is deliberately narrow.** It fires only on two *unaliased* relations
whose labels match. An aliased pair answers to `a` and `b`; a CTE or derived
table has a name unique within the statement. Step 1's aliased test is what
keeps it narrow, and it would fail if the collision were keyed on the relation's
declared name rather than on its label.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ambiguous_relations.py`:

```python
SNAPSHOT = {
    ('public', 'invoices'): [('amount', 'numeric'), ('id', 'bigint')],
    ('billing', 'invoices'): [('amount', 'numeric'), ('period', 'date')],
    ('public', 'auth_user'): [('email', 'varchar')],
}


def catalog() -> MemoryCatalog:
    """Two same-named relations in different schemas, and one that is unique."""
    return MemoryCatalog(SNAPSHOT, search_path=('public',))


def offered(sql: str, caret: int | None = None) -> list[str]:
    """Suggestion texts at `caret`, or at the end of `sql`."""
    at = len(sql) if caret is None else caret
    return [s.text for s in complete(sql, at, POSTGRES, catalog())]


RELATIONS = 'public.invoices, billing.invoices'
BOTH = f'FROM {RELATIONS}'


def test_two_same_named_relations_in_scope_get_their_whole_path() -> None:
    """
    Server-verified: `SELECT invoices.amount FROM public.invoices, billing.invoices`
    is refused with `table reference "invoices" is ambiguous`, and both
    `public.invoices.amount` and `billing.invoices.amount` plan.
    """
    found = offered(f'SELECT amou {BOTH}', caret=11)
    assert 'public.invoices.amount' in found
    assert 'billing.invoices.amount' in found
    assert 'invoices.amount' not in found


def test_a_relation_whose_label_is_unique_keeps_its_label() -> None:
    """The rule is per label, not per statement: one collision must not lengthen everything."""
    found = offered(f'SELECT ema FROM public.auth_user, {RELATIONS}', caret=10)
    assert 'auth_user.email' in found


def test_aliases_are_not_a_collision() -> None:
    """
    `FROM public.invoices a, billing.invoices b` answers to `a` and `b`, which
    the server resolves without help. Keyed on the label rather than the
    relation name, and this is the test that says so.
    """
    found = offered('SELECT amou FROM public.invoices a, billing.invoices b', caret=11)
    assert 'a.amount' in found
    assert 'b.amount' in found
    assert not [text for text in found if text.startswith('public.')]


def test_one_relation_is_untouched() -> None:
    """The constraint the whole design is shaped around: no collision, no change."""
    assert offered('SELECT amou FROM billing.invoices', caret=11) == ['invoices.amount']
```

and extend the file's imports:

```python
from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambiguous_relations.py -v`
Expected: the first test FAILS — one `invoices.amount` is offered and the other
is deduped away. The other three should already pass; they are the guards that
must *keep* passing.

- [ ] **Step 3: Add the two helpers**

In `src/pysqlsuggestions/resolve.py`, directly above `_unqualified`:

```python
def _ambiguous_labels(relations: Sequence[Relation]) -> frozenset[str]:
    """
    Labels naming more than one catalog relation here.

    Only catalog relations can collide. A CTE or derived table has a name unique
    within the statement, and an aliased relation answers to its alias — so this
    is empty for every query but the one that puts two same-named relations from
    different schemas in the same FROM. Postgres accepts that and then refuses
    every bare reference to either, which is the whole reason this exists.
    """
    counted: dict[str, int] = {}
    for relation in relations:
        if relation.projection is None and relation.label:
            counted[relation.label] = counted.get(relation.label, 0) + 1
    return frozenset(label for label, count in counted.items() if count > 1)


def _qualifier_for(relation: Relation, ambiguous: frozenset[str]) -> tuple[str, ...]:
    """
    What a reference to this relation must be prefixed with.

    Its label, which is what the author would write — or its whole declared
    path, when that label names something else too. The full path rather than
    the shortest disambiguating one: what counts as short enough depends on the
    search path, which this engine models only in part.
    """
    if relation.label in ambiguous:
        return relation.path
    return (relation.label,) if relation.label else ()
```

- [ ] **Step 4: Use them**

In `_unqualified`, replace the loop in the `if relations:` branch:

```python
            seen: set[tuple[str, ...]] = set()
            ambiguous = _ambiguous_labels(relations)
            for relation in relations:
                candidates += _columns_of(relation, reader, seen, qualify=_qualifier_for(relation, ambiguous))
```

- [ ] **Step 5: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git commit -m "fix: a reference to one of two same-named relations names its schema"
```

---

## Task 3: star expansion follows the same rule

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py` (`_expansion`)
- Test: `tests/test_ambiguous_relations.py`

**Interfaces:**
- Consumes: `_ambiguous_labels`, `_qualifier_for` from Task 2.

**Why it is separate from Task 2.** `_expansion` renders its own text with
`literal=True` and never touches `Candidate.qualifier`, so Task 2's change does
not reach it. It has a second defect Task 2 does not: because both relations
render as `invoices`, the expansion emits `invoices.amount` **twice**.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ambiguous_relations.py`:

```python
def test_a_star_over_two_same_named_relations_names_both() -> None:
    """
    Today this expands to `invoices.amount, invoices.id, invoices.amount,
    invoices.period` — every reference ambiguous, and `amount` written twice
    because the two relations render identically.
    """
    sql = f'SELECT * {BOTH}'
    [found] = [s for s in complete(sql, 8, POSTGRES, catalog()) if s.kind is Kind.EXPANSION]
    assert found.text == (
        'public.invoices.amount, public.invoices.id, billing.invoices.amount, billing.invoices.period'
    )


def test_a_star_over_one_relation_is_untouched() -> None:
    """No collision, no change — a one-relation star still expands bare."""
    sql = 'SELECT * FROM billing.invoices'
    [found] = [s for s in complete(sql, 8, POSTGRES, catalog()) if s.kind is Kind.EXPANSION]
    assert found.text == 'amount, period'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambiguous_relations.py -v`
Expected: the first FAILS with the doubled, unqualified list. The second passes
and must keep passing.

- [ ] **Step 3: Apply the rule**

In `src/pysqlsuggestions/resolve.py`, in `_expansion`, replace the loop that
builds `names`:

```python
    relations = request.star_of
    qualify = request.star_qualifier is not None or len(relations) > 1
    ambiguous = _ambiguous_labels(relations)
    seen: set[tuple[str, ...]] = set()
    names: list[str] = []
    for relation in relations:
        path = _qualifier_for(relation, ambiguous) if qualify else ()
        prefix = '.'.join(quote_if_needed(part, dialect) for part in path)
        for column in _columns_of(relation, reader, seen):
            rendered = quote_if_needed(column.text, dialect)
            names.append(f'{prefix}.{rendered}' if prefix else rendered)
```

and add a paragraph to the docstring, after the one about a star the author
qualified:

```python
    Two relations sharing a label are named in full, for the reason an ordinary
    reference is — and it fixes a second fault here. Rendering both as
    `invoices` made a star over them emit `invoices.amount` twice, so the list
    was not merely ambiguous but wrong about how many columns it had.
```

- [ ] **Step 4: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "fix: a star over two same-named relations stops repeating a column"
```

---

## Task 4: before any FROM, one entry per schema

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py` (`_unqualified` else-branch, one new helper, `_column_candidate`)
- Test: `tests/test_ambiguous_relations.py`
- Possibly modify: `tests/test_complete.py` (see Step 5)

**Interfaces:**
- Consumes: Task 1's tuple qualifier.
- Produces: `_loose_columns(request, reader, limit) -> list[Candidate]`, and
  `_column_candidate(..., position: int | None = None)`.

**This is the task you chose against my recommendation**, so the risk is stated
here too: in a schema-per-tenant database — every table in every schema — one
column name yields one entry per tenant. It is bounded by `search_columns`'
server-side limit, but that bound is 500. The mitigation is ranking, not
truncation, and no cap is added.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ambiguous_relations.py`:

```python
def test_the_offer_stage_reaches_every_schema() -> None:
    """
    Before this, one of these was silently dropped: both rendered
    `invoices.amount`, and ranking dedupes on the text to be inserted. The
    schema that lost was unreachable at this caret however much you typed.
    """
    found = offered('SELECT amou')
    assert found == ['public.invoices.amount', 'billing.invoices.amount']


def test_each_carries_the_relation_it_would_add_to_the_from() -> None:
    """Choosing a column here is choosing its table, and the two differ by schema."""
    got = {s.text: s.relation for s in complete('SELECT amou', 11, POSTGRES, catalog())}
    assert got['public.invoices.amount'] == ('public', 'invoices')
    assert got['billing.invoices.amount'] == ('billing', 'invoices')


def test_a_column_unique_to_one_schema_stays_short() -> None:
    """No collision, no change — `auth_user` exists once, so nothing is lengthened."""
    assert offered('SELECT ema') == ['auth_user.email']
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambiguous_relations.py -v`
Expected: the first two FAIL — one entry comes back, `invoices.amount`. The
third passes and must keep passing.

- [ ] **Step 3: Let a candidate override its position**

In `src/pysqlsuggestions/resolve.py`, change `_column_candidate`'s signature and
its `position=`:

```python
def _column_candidate(
    column: Column,
    label: str | None = None,
    qualify: tuple[str, ...] = (),
    relation: tuple[str, ...] = (),
    position: int | None = None,
) -> Candidate:
```

```python
        position=column.position if position is None else position,
```

- [ ] **Step 4: Build the loose columns**

In `src/pysqlsuggestions/resolve.py`, add above `_unqualified`:

```python
_OFF_SEARCH_PATH = _MAX_POSITION_PENALTY
"""
How far to demote a column whose schema is not in the default namespace.

Equal to the largest penalty `position` can express, so every in-path column
outranks every out-of-path one — and deliberately not larger, because the
penalty saturates there and a bigger number would say something the scoring
cannot hear. A table with more than fifty columns can tie, which is a fair
price for not inventing a second ranking signal.
"""


def _loose_columns(request: Request, reader: _Reader, limit: int) -> list[Candidate]:
    """
    Columns with no relation in scope — `SELECT <caret>` before any FROM.

    Each carries the relation it would need there, which insertion writes as a
    FROM clause. Two relations of the same name in different schemas therefore
    produce two entries, and they have to be told apart: rendering both as
    `invoices.amount` is what made ranking drop one of them, silently and
    whichever the user wanted.

    Lengthened only where they would collide — the same `(table, column)` pair
    under more than one schema. A shared table name is not enough:
    `public.invoices.amount` and `billing.invoices.period` can never render
    alike, so neither is touched.
    """
    columns = list(reader.loose_columns(request.prefix, limit))
    schemas: dict[tuple[str, str], set[str]] = {}
    for column in columns:
        schemas.setdefault((column.table, column.name), set()).add(column.schema)
    here = {(table.schema, table.name) for table in reader.tables(None)}
    return [
        _column_candidate(
            column,
            qualify=(column.schema, column.table) if len(schemas[column.table, column.name]) > 1 else (column.table,),
            relation=(column.schema, column.table),
            position=column.position + (0 if (column.schema, column.table) in here else _OFF_SEARCH_PATH),
        )
        for column in columns
    ]
```

- [ ] **Step 5: Call it**

In `_unqualified`, replace the `else:` branch's list comprehension with a call,
keeping the comment above it and adding to it:

```python
        else:
            # Nothing is in the FROM yet, so each column carries the relation it
            # would need there. Choosing one is choosing its table as well — and
            # the schema with it, because a searched column may live outside the
            # default namespace and `FROM invoices` would not resolve.
            #
            # The reference stays bare where it can: a qualified FROM entry
            # answers to its relation name, so `SELECT invoices.amount FROM
            # billing.invoices` is what this writes and what Postgres plans. It
            # lengthens only when two schemas would render the same reference.
            candidates += _loose_columns(request, reader, limit)
```

- [ ] **Step 6: Run everything, and expect one existing test to move**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`

`tests/test_complete.py::test_bare_select_uses_column_search_when_supported`
asserts on this position and may now see a different order, because an
out-of-path column is demoted where before nothing distinguished them. If it
fails on **order**, update the expectation and say why in the docstring. If it
fails on **which columns are present**, stop — that is a real regression and not
what this task intends.

- [ ] **Step 7: Commit**

```bash
git add -A src tests
git commit -m "fix: a column before any FROM is reachable in every schema that has it"
```

---

## Task 5: the corpus asks every dialect

**Files:**
- Modify: `src/pysqlsuggestions/testing/__init__.py`
- Test: `tests/test_conformance.py`

**Interfaces:**
- Produces: `Case.expect_exact: tuple[str, ...] = ()`.

**Why `Case` needs a new field.** `check` compares against
`text.rsplit('.', 1)[-1]` — the last segment — so `invoices.amount` and
`public.invoices.amount` both reduce to `amount` and no `expect`/`forbid` pair
can tell them apart. This proposition is about *how* a name is written, which is
the first one the corpus has had, so the field is genuinely new expressiveness
rather than a workaround.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conformance.py`:

```python
def test_the_corpus_asks_every_dialect_for_an_unambiguous_reference() -> None:
    """
    Two same-named relations in one FROM is a state every backend here allows
    and every backend here refuses a bare reference in. A dialect that got this
    wrong would write SQL that does not run.
    """
    for dialect in SHIPPED:
        assert [case for case in DialectConformance.cases(dialect) if 'ambiguous' in case.name]


def test_the_ambiguity_case_is_not_one_a_dialect_can_break() -> None:
    """
    Recorded because it is a real limit of this half of the file.

    Every other broken-dialect test below turns a declaration wrong and watches
    the corpus notice. This rule lives in `resolve`, not in any dialect, so no
    declaration can switch it off — which makes the case a regression guard
    shared with third-party dialects rather than a detector of their mistakes.
    It still earns its place: a dialect whose namespace depth is wrong writes
    the wrong path here, and that the corpus does catch.
    """
    for dialect in SHIPPED:
        assert not DialectConformance.check(dialect), dialect.name
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_conformance.py -v`
Expected: the first FAILS — no case mentions `ambiguous`. The second passes and
is the guard that the new case does not break the shipped dialects.

- [ ] **Step 3: Give `Case` an exact expectation**

In `src/pysqlsuggestions/testing/__init__.py`, add to `Case`:

```python
    expect_exact: tuple[str, ...] = ()
    """
    Texts that must appear *verbatim*, qualifier and all.

    `expect` and `forbid` compare against the last segment, which is right for
    almost everything — the proposition is usually about which thing is offered.
    This one is about how it is written: `invoices.amount` and
    `public.invoices.amount` name the same column and only one of them runs when
    two same-named relations are in scope.
    """
```

and in `check`, after the `present` line:

```python
            exact = [want for want in case.expect_exact if want not in found]
            if missing or present or exact:
                failures.append(
                    f'{dialect.name}: {case.name}\n'
                    f'    sql      {case.sql!r}\n'
                    f'    missing  {missing + exact}\n'
                    f'    unwanted {present}\n'
                    f'    offered  {found[:8]}',
                )
```

replacing the existing `if missing or present:` block entirely.

Also extend the filter at the end of `cases` so a case carrying only an exact
expectation is kept:

```python
        return [case for case in cases if case.expect or case.forbid or case.expect_exact]
```

- [ ] **Step 4: Put a same-named relation in the second schema**

In `catalog`, add to `snapshot`:

```python
            (OTHER, 'users'): list(USERS),
```

and extend that method's docstring with a paragraph:

```python
        `OTHER` also holds a relation named like one in `SCHEMA`. Two relations
        of the same name in one FROM is a state every backend here allows and
        every backend here then refuses a bare reference in, so the fixture has
        to be able to produce it.
```

- [ ] **Step 5: Add the case**

In `cases`, after the `'a relation position never offers a sequence'` entry:

```python
            Case(
                name='a reference to one of two same-named relations is not ambiguous',
                sql=f'SELECT * FROM {users}, {DialectConformance.reference(dialect, "users", schema=OTHER)} WHERE ',
                expect_exact=(f'{users}.id',),
            ),
```

and give `reference` the schema argument it needs:

```python
    @staticmethod
    def reference(dialect: Dialect, table: str, schema: str = SCHEMA) -> str:
        """A fully qualified relation reference, however many levels that takes."""
        parts = [schema, table]
        if len(dialect.namespace.levels) >= 3:  # noqa: PLR2004
            parts.insert(0, CATALOG)
        return '.'.join(parts)
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. A shipped dialect failing the new case means Task 2's rule
does not reach it — fix it there rather than weakening the case.

- [ ] **Step 7: Commit**

```bash
git add -A src tests
git commit -m "test: the corpus asks every dialect for a reference that resolves"
```

---

## Task 6: the server plans what the engine wrote

**Files:**
- Modify: `docker/postgres/01-schema.sql`
- Modify: `tests/integration/test_backends.py`

**The seed change needs a rebuild.** `docker-entrypoint-initdb.d` scripts run
only on an empty data directory:

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up --wait
```

**Why this is the only test that can judge it.** The acceptance sweep reports
SQLSTATE 42601 — syntax errors — and treats everything else as "semantic, and a
half-written query is full of those". An ambiguous reference is semantic, so the
sweep is structurally blind to this defect. Its silence is not evidence.

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_backends.py`, in the PostgreSQL section:

```python
def test_postgres_plans_a_reference_to_one_of_two_same_named_relations(
    postgres_catalog: DbapiCatalog,
) -> None:
    """
    The whole slice, end to end. `SELECT invoices.amount FROM public.invoices,
    billing.invoices` is refused with `table reference "invoices" is ambiguous`,
    and that is what the engine used to write.

    The acceptance sweep cannot catch this — it reports syntax errors only, and
    an ambiguous reference is semantic — so this is the guard.
    """
    psycopg2 = pytest.importorskip('psycopg2')
    sql = 'SELECT amou FROM public.invoices, billing.invoices'
    found = [s for s in complete(sql, 11, POSTGRES, postgres_catalog) if s.text.endswith('.amount')]
    assert {s.text for s in found} == {'public.invoices.amount', 'billing.invoices.amount'}
    with psycopg2.connect(POSTGRES_DSN) as connection, connection.cursor() as cursor:
        for suggestion in found:
            cursor.execute(f'EXPLAIN {apply_suggestion(sql, suggestion, dialect=POSTGRES)[0]}')
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/integration/test_backends.py -k same_named -v`
Expected: FAIL — `public.invoices` does not exist in the seed yet, so only one
relation is found.

- [ ] **Step 3: Seed the colliding relation**

In `docker/postgres/01-schema.sql`, after the `CREATE TABLE billing."MonthlyTotals"`
block (around line 207) and before the procedures section, add the following.
Placement is safe anywhere after `CREATE SCHEMA billing;` because this file sets
no `search_path` and qualifies every table explicitly — but keeping the two
`invoices` together is what makes the collision legible to a reader:

```sql
-- A relation named like one in `billing`, so a statement can put both in scope.
-- Postgres allows that — it aliases the second internally — and then refuses
-- every bare reference to either, which is the one case where this engine used
-- to write SQL that does not run.
CREATE TABLE public.invoices (
    id      bigserial PRIMARY KEY,
    amount  numeric(12, 2) NOT NULL DEFAULT 0,
    period  date
);
```

If `billing.invoices` is created inside a `CREATE SCHEMA billing;` block, put
this after that block closes — `public` is a different schema and the statement
must not land inside it.

- [ ] **Step 4: Rebuild and run the integration suite**

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up --wait
uv run pytest tests/integration -v
```

Expected: PASS. Watch two neighbours in particular —
`test_postgres_reaches_a_relation_off_the_search_path` completes `FROM invo`,
which now matches a relation in the search path as well; it filters for
`billing.invoices` by exact text, so it should hold. And
`test_postgres_offers_no_sequence_where_a_relation_belongs` now has one more
`_id_seq` in the database, which it asserts the absence of.

- [ ] **Step 5: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A tests docker
git commit -m "test: the server plans both references the engine now writes"
```

---

## Task 7: the changelog says what was wrong

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the entry**

In `CHANGELOG.md`, under `## Unreleased`, above `### Procedures and sequences`:

```markdown
### A column reference that resolves

Two relations with the same name in different schemas can both be in scope —
`FROM public.invoices, billing.invoices` is legal, and Postgres aliases the
second internally. Every column reference the engine wrote for them was
`invoices.amount`, which the server refuses: `table reference "invoices" is
ambiguous`. Each now carries its relation's whole path.

`SELECT *` over the two used to expand to `invoices.amount, invoices.id,
invoices.amount, invoices.period` — ambiguous, and naming `amount` twice.

Before any `FROM` exists, a column that several schemas have is now offered once
per schema instead of once in total. Previously the others were unreachable at
that caret however much you typed, because ranking dedupes on the text to be
inserted and all of them rendered alike. In a database with a schema per tenant
this makes that list longer; the schema on the search path sorts first.

**Nothing changes without a collision.** A single-schema database gets exactly
what it got before, and that is asserted rather than assumed.

`Candidate.qualifier` is now `tuple[str, ...]` rather than `str | None` — a path
is not a name, and a dotted string in the old field would have been quoted as
one name containing a dot. Only callers constructing a `Candidate` by hand are
affected; `Suggestion` is unchanged, since the qualifier is already part of its
`text`.
```

- [ ] **Step 2: Correct the paragraph that described this as a limitation**

Further down, the `### Relations and columns outside the search path` entry ends
with a **"One known limitation"** paragraph saying these "still collapse to a
single suggestion". Replace that paragraph with:

```markdown
**A limitation this entry recorded is now fixed.** It said two columns with the
same name, in same-named tables, in different schemas "still collapse to a
single suggestion", and that telling them apart "needs a qualifier that can hold
a path rather than a name". That qualifier exists — see *A column reference that
resolves* above.

It also understated the fault. The collapse was the visible half; the invisible
half was that the surviving suggestion is itself refused once both relations are
in scope, so the position was writing SQL that does not run rather than merely
offering one answer where two were due.
```

- [ ] **Step 3: Verify**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass — `tests/test_build_pages.py` renders the changelog, so a
malformed heading would show up here.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: the last wrong answer, and what its first report missed"
```

---

## Self-review notes

**Spec coverage.** §3 → Task 1. §4 in-scope → Task 2; offer stage → Task 4.
§5 → Task 3. §6 → Task 4 (the `_OFF_SEARCH_PATH` constant and its comment).
§7 unit → Tasks 1–4; conformance → Task 5; integration → Task 6; "not in the
acceptance sweep" → recorded in Task 6's preamble and in the test's docstring.
§8 → Task 7, both paragraphs.

**Ordering constraints.**
- Task 1 before everything: the tuple type is what the rest writes into.
- Task 2 before Task 3: `_expansion` uses Task 2's two helpers.
- Task 5 after Tasks 2 and 4: the corpus asserts behaviour they build.
- Task 6 after Task 2: the integration test needs the in-scope rule.

**Two places a task is expected to disturb its neighbours**, called out so
neither reads as a surprise:
- Task 4 Step 6 — `test_bare_select_uses_column_search_when_supported` may
  change order, with an explicit rule for telling an order change from a
  regression.
- Task 6 Step 4 — adding `public.invoices` changes what `FROM invo` offers and
  adds an `_id_seq` to the database, and names the two tests that touch both.

**One thing deliberately not done.** No cap on the offer-stage list. The spec
records the schema-per-tenant risk and the decision to meet it with ranking; a
cap would be a number picked without evidence, and this codebase logs a
truncation rather than hiding one.
