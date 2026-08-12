# Procedures and Sequences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `CALL ⌶` offers procedures and `nextval('⌶` offers sequences, while
`SELECT ⌶` and `FROM ⌶` keep offering exactly what they offer today.

**Architecture:** The catalog reports a subtype on two records it already
returns — `Function.kind` and `Table.kind` — and `resolve` filters by it in one
place. Two new `Kind` members carry the result to a front end. A caret inside a
string literal that is the first argument of a declared call is a new position,
answered by dialect data rather than by hard-coded function names.

**Tech Stack:** Python 3.10+, no runtime dependencies. `uv run pytest`,
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.

## Global Constraints

- **Python 3.10 floor.** No `match`, no `X | Y` in `isinstance`, no starred
  expression directly inside a subscript.
- **Zero runtime dependencies.** Standard library only in `src/`.
- **Line length 120, single quotes.** `ruff format` decides; do not hand-wrap.
- **Docstrings are required** (`ruff` rule set `D` is on) on every public module,
  class, function and method. Write what the thing is *for* and why it is the
  way it is — every existing docstring in this codebase does.
- **`engine/` may not import `ports` or `resolve`.** `tests/test_purity.py`
  enforces it.
- **A dialect is data composed with `dataclasses.replace`,** never a subclass.
- **Every task ends green.** `uv run pytest`, `ruff check`, `ruff format --check`
  and `mypy` all clean before the commit.
- **Guiding principle:** a missing answer costs a keystroke; a wrong one costs
  correctness. When in doubt, offer nothing.
- Backends for the integration tasks: `docker compose -f docker/docker-compose.yml up --wait`.

---

## File Structure

**Modified — library**

| file | change |
|---|---|
| `src/pysqlsuggestions/types.py` | `Kind.PROCEDURE`, `Kind.SEQUENCE`; `Function.kind`; `Function.result` nullable; `Request.writes_a_literal` |
| `src/pysqlsuggestions/dialects/base.py` | `LiteralArgument`; `Dialect.literal_arguments`; `ClauseModel.without` |
| `src/pysqlsuggestions/dialects/ansi.py` | `CALL` clause; `CALL` in `STATEMENT_START` |
| `src/pysqlsuggestions/dialects/clickhouse.py` | subtract `CALL` from clauses and statement starts; `Function.kind` in the row mapper |
| `src/pysqlsuggestions/dialects/trino.py` | `Function.kind` from `SHOW FUNCTIONS` row 3 |
| `src/pysqlsuggestions/dialects/postgres.py` | `prokind` `'p'`; `relkind` `'S'`; `DROP SEQUENCE`/`ALTER SEQUENCE`; own `statement_start`; `literal_arguments` |
| `src/pysqlsuggestions/engine/analyse.py` | `_string_index_under`, `_call_opening`, `literal_argument_call` |
| `src/pysqlsuggestions/engine/request.py` | literal-argument branch; `_qualified_kinds` narrowing; argument-list rule |
| `src/pysqlsuggestions/resolve.py` | kind filters; procedure and sequence candidates; detail rendering |
| `src/pysqlsuggestions/testing/__init__.py` | fixture sequence and procedure; three cases; one structure check |
| `lsp/pysqlsuggestions_lsp/convert.py` | `ITEM_KINDS` entries |

**Modified — fixtures, corpus, docs**

`docker/postgres/01-schema.sql`, `tests/corpus/cases.py`,
`tests/integration/test_acceptance.py`, `tests/integration/test_backends.py`,
`tests/test_apply.py`, `tests/test_conformance.py`, `docs/gaps.md`,
`CHANGELOG.md`.

**Created**

`tests/test_procedures.py`, `tests/test_sequences.py`.

---

## Task 1: `Function` gains a kind and may report no result

**Files:**
- Modify: `src/pysqlsuggestions/types.py` (the `Function` dataclass)
- Modify: `src/pysqlsuggestions/resolve.py` (`_function_candidate`)
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (functions row mapper)
- Modify: `src/pysqlsuggestions/dialects/clickhouse.py` (functions row mapper)
- Modify: `src/pysqlsuggestions/dialects/trino.py` (functions row mapper)
- Modify: `tests/test_apply.py:91`
- Test: `tests/test_procedures.py` (new)

**Interfaces:**
- Produces: `Function(schema, name, args, result, kind='function')` where
  `result: str | None` and `kind` is one of `'function'`, `'aggregate'`,
  `'window'`, `'procedure'`.

**Background you need.** `Function` is a frozen slots dataclass. Every
construction in the codebase and in tests uses keyword arguments, so a new field
with a default at the end is safe. Postgres's `pg_get_function_result` returns
**NULL** for a procedure and the current mapper does `str(row[3])`, which would
render the string `'None'` into a user-visible detail column. ClickHouse has no
return-type information at all and currently puts the word `aggregate` or
`function` into `result` — a kind in the return-type slot, because there was
nowhere else for it. Trino's `SHOW FUNCTIONS` returns a real kind at `row[3]`
(`scalar`) that nothing reads.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_procedures.py`:

```python
"""
Callables the catalog can tell apart.

A procedure is not a function you may call in an expression — Postgres answers
`archive_old_reports(date) is a procedure` and refuses to plan — so the record
has to carry which it is, and the engine has to read it.
"""

from __future__ import annotations

from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.resolve import _function_candidate
from pysqlsuggestions.types import Function


def test_a_function_is_a_function_unless_it_says_otherwise() -> None:
    """The default has to be the safe reading: an unfiltered backend keeps working."""
    assert Function(schema=None, name='now', args='', result='timestamptz').kind == 'function'


def test_postgres_reports_which_kind_of_callable_it_found() -> None:
    """prokind is the whole distinction, and the mapper is the only place it is visible."""
    query = POSTGRES.catalog_queries.functions
    assert query is not None
    assert query.row(('pg_catalog', 'count', '"any"', 'bigint', 'a')).kind == 'aggregate'
    assert query.row(('pg_catalog', 'now', '', 'timestamptz', 'f')).kind == 'function'
    assert query.row(('pg_catalog', 'rank', '', 'bigint', 'w')).kind == 'window'
    assert query.row(('public', 'archive', 'IN cutoff date', None, 'p')).kind == 'procedure'


def test_a_procedure_reports_no_result_rather_than_the_word_none() -> None:
    """pg_get_function_result is NULL for a procedure, and str(None) is 'None'."""
    query = POSTGRES.catalog_queries.functions
    assert query is not None
    assert query.row(('public', 'archive', 'IN cutoff date', None, 'p')).result is None


def test_clickhouse_stops_putting_a_kind_in_the_return_type() -> None:
    """`count() -> aggregate` claimed a return type of `aggregate`. It has none to report."""
    query = CLICKHOUSE.catalog_queries.functions
    assert query is not None
    counted = query.row(('count', 1))
    assert counted.kind == 'aggregate'
    assert counted.result is None
    assert query.row(('abs', 0)).kind == 'function'


def test_trino_reads_the_kind_column_it_was_already_fetching() -> None:
    """SHOW FUNCTIONS returns (name, result, args, kind, deterministic, description)."""
    query = TRINO.catalog_queries.functions
    assert query is not None
    scalar = query.row(('abs', 'bigint', 'bigint', 'scalar', True, 'Absolute value'))
    assert scalar.kind == 'function'
    assert scalar.result == 'bigint'
    assert query.row(('sum', 'bigint', 'bigint', 'aggregate', True, '')).kind == 'aggregate'


def test_the_detail_drops_the_arrow_when_there_is_no_result() -> None:
    """`count() -> ` reads as a broken signature; `count()` reads as an unknown one."""
    unknown = _function_candidate(Function(schema=None, name='count', args=None, result=None, kind='aggregate'))
    assert unknown.detail == 'count()  aggregate'
    known = _function_candidate(Function(schema='pg_catalog', name='now', args='', result='timestamptz'))
    assert known.detail == 'now() -> timestamptz'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_procedures.py -v`
Expected: FAIL — `Function` has no attribute `kind`, and the Postgres mapper
takes four columns rather than five.

- [ ] **Step 3: Add the fields**

In `src/pysqlsuggestions/types.py`, replace the `result` line of `Function` and
append `kind`:

```python
    result: str | None
    """
    The type it returns, or None where there is nothing to report.

    None means two different true things and neither is a lie: a backend that
    keeps no signatures (ClickHouse), and a callable that returns nothing at all
    (a procedure, where `pg_get_function_result` is NULL). Both render the same
    way — without an arrow — because both mean "no return type to show".
    """
    kind: str = 'function'
    """
    Which sort of callable: function, aggregate, window, procedure.

    Defaulted so that every existing construction keeps working and a backend
    that cannot distinguish says the safe thing. `procedure` is the one value
    that changes behaviour: a procedure cannot appear in an expression —
    Postgres answers `… is a procedure. HINT: To call a procedure, use CALL.` —
    so the expression positions filter it out and `CALL` filters everything
    else out.
    """
```

- [ ] **Step 4: Render the detail**

In `src/pysqlsuggestions/resolve.py`, replace `_function_candidate` entirely:

```python
def _function_candidate(function: Function, kind: Kind = Kind.FUNCTION) -> Candidate:
    """
    One callable, with as much of its signature as the backend reported.

    The arrow is dropped rather than left dangling when there is no result to
    put after it: `count() -> ` reads as a broken signature where `count()`
    reads as an unknown one, and ClickHouse reports no signatures at all.

    A kind other than `function` is named, because that is the part a reader
    cannot infer from the name — `count` being an aggregate and `rank` a window
    function is what decides whether either belongs where the caret is.
    """
    signature = f'{function.name}({function.args or ""})'
    if function.result:
        signature = f'{signature} -> {function.result}'
    if function.kind != 'function':
        signature = f'{signature}  {function.kind}'
    return Candidate(
        text=function.name,
        kind=kind,
        detail=signature,
        type=function.result,
        takes_arguments=function.takes_arguments,
    )
```

- [ ] **Step 5: Teach the three row mappers**

`src/pysqlsuggestions/dialects/postgres.py` — add `p.prokind` to the select list
and map it. Replace the `SELECT` line and the `row=` lambda of the `functions`
query:

```python
            SELECT n.nspname, p.proname,
                   pg_get_function_arguments(p.oid), pg_get_function_result(p.oid), p.prokind
```

```python
        row=lambda row: Function(
            schema=str(row[0]),
            name=str(row[1]),
            args=str(row[2]),
            # NULL for a procedure, which returns nothing. `str(None)` would put
            # the word `None` in a detail column a user reads.
            result=str(row[3]) if row[3] is not None else None,
            kind=_PROKIND.get(str(row[4]), 'function'),
        ),
```

and add, next to `_RELKIND` at the top of the module:

```python
_PROKIND = {'f': 'function', 'a': 'aggregate', 'w': 'window', 'p': 'procedure'}
```

`src/pysqlsuggestions/dialects/clickhouse.py` — replace the `functions` row
mapper:

```python
        # args is None, not '': system.functions carries no signatures, and an
        # empty string would claim these take no arguments, which would put the
        # caret after `count()` instead of inside it. `result` is None for the
        # same reason — there is no return type to report, and the word
        # `aggregate` used to sit in that field for want of anywhere else.
        row=lambda row: Function(
            schema=None,
            name=str(row[0]),
            args=None,
            result=None,
            kind='aggregate' if row[1] else 'function',
        ),
```

`src/pysqlsuggestions/dialects/trino.py` — replace the `functions` row mapper,
keeping the existing comment above it:

```python
        row=lambda row: Function(
            schema=None,
            name=str(row[0]),
            args=str(row[2]),
            result=str(row[1]),
            # Column 3 is the kind, which was fetched and ignored. Trino spells
            # a plain function `scalar`; the other two spellings match ours.
            kind='function' if str(row[3]) == 'scalar' else str(row[3]),
        ),
```

- [ ] **Step 6: Fix the one test fixture that used `result` as a kind**

`tests/test_apply.py:91` currently reads:

```python
    cat = MemoryCatalog(SNAPSHOT, functions=(Function(schema=None, name='nowhere', args=None, result='function'),))
```

Change it to:

```python
    cat = MemoryCatalog(SNAPSHOT, functions=(Function(schema=None, name='nowhere', args=None, result=None),))
```

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add -A src tests
git commit -m "feat: a callable says which sort it is, and may report no result"
```

---

## Task 2: procedures reach the catalog and stay out of expressions

**Files:**
- Modify: `src/pysqlsuggestions/types.py` (`Kind`)
- Modify: `lsp/pysqlsuggestions_lsp/convert.py` (`ITEM_KINDS`)
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (`prokind IN`)
- Modify: `src/pysqlsuggestions/resolve.py` (`_unqualified`, function branch)
- Test: `tests/test_procedures.py`

**Interfaces:**
- Consumes: `Function.kind` from Task 1.
- Produces: `Kind.PROCEDURE`. Expression positions never emit it.

**Why the LSP mapping is in this task and not a later one.**
`tests/lsp/test_convert.py::test_every_kind_the_engine_can_emit_has_an_item_kind`
iterates `Kind` and fails when one falls back to `CompletionItemKind.Text`. That
guard exists to force exactly this, so the mapping lands with the enum member
rather than in a tidy-up task that would leave the suite red in between.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_procedures.py`:

```python
PROCEDURES = (
    Function(schema='public', name='archive_old_reports', args='IN cutoff date', result=None, kind='procedure'),
    Function(schema='pg_catalog', name='count', args='"any"', result='bigint', kind='aggregate'),
)


def catalog() -> MemoryCatalog:
    """A snapshot with one procedure and one aggregate, so both directions can be asserted."""
    return MemoryCatalog({('public', 'auth_user'): [('id', 'bigint'), ('email', 'varchar')]}, functions=PROCEDURES)


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def test_an_expression_position_does_not_offer_a_procedure() -> None:
    """
    Server-verified: `SELECT archive_old_reports(current_date)` is refused with
    `… is a procedure. HINT: To call a procedure, use CALL.`

    So this is not a missing answer being added — it is a wrong one being kept
    out while the catalog starts reporting procedures at all.
    """
    found = offered('SELECT ')
    assert 'count' in found
    assert 'archive_old_reports' not in found


def test_the_postgres_query_now_asks_for_procedures() -> None:
    """The filter downstream is what makes widening this safe, so the two go together."""
    query = POSTGRES.catalog_queries.functions
    assert query is not None
    assert "'p'" in query.sql
```

and extend that file's imports:

```python
from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_procedures.py -v`
Expected: FAIL — `'archive_old_reports' not in found` fails, because nothing
filters yet.

- [ ] **Step 3: Add the kind**

In `src/pysqlsuggestions/types.py`, after `Kind.FUNCTION`:

```python
    PROCEDURE = 'procedure'
    """
    A callable that a statement invokes rather than evaluates.

    Distinct from FUNCTION because the two are not interchangeable in either
    direction: `SELECT my_procedure()` is refused outright, and `CALL now()` is
    too. A front end that colours by kind should say which one it found.
    """
```

- [ ] **Step 4: Map it for LSP**

In `lsp/pysqlsuggestions_lsp/convert.py`, add to `ITEM_KINDS` after the
`Kind.FUNCTION` line:

```python
    Kind.PROCEDURE: CompletionItemKind.Method,
```

and extend the mapping's docstring:

```python
"""
Every Kind the engine can emit. A test fails if one is added and not mapped.

LSP has no procedure and no sequence, so those two are mapped for visual
distinctness rather than for a natural fit: every closer name is taken by
something the new kind would be confused with — `Class` is a table, `Function`
is a function, `Value` is a literal.
"""
```

- [ ] **Step 5: Widen the Postgres query and narrow resolve**

In `src/pysqlsuggestions/dialects/postgres.py`, in the `functions` query:

```python
              AND p.prokind IN ('f', 'a', 'w', 'p')
```

In `src/pysqlsuggestions/resolve.py`, in `_unqualified`, replace the function
branch:

```python
    if Kind.FUNCTION in request.kinds:
        # Procedures are excluded rather than merely unranked. A procedure in an
        # expression is not a poor suggestion, it is one the server refuses:
        # `SELECT archive_old_reports(…)` answers `… is a procedure`.
        candidates += [_function_candidate(f) for f in reader.functions() if f.kind != 'procedure']
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass, including the LSP guard.

- [ ] **Step 7: Commit**

```bash
git add -A src lsp tests
git commit -m "feat: the catalog reports procedures, and expressions refuse them"
```

---

## Task 3: `CALL` is a clause, and ClickHouse says it has none

**Files:**
- Modify: `src/pysqlsuggestions/dialects/base.py` (`ClauseModel.without`)
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (`CALL` clause, `STATEMENT_START`)
- Modify: `src/pysqlsuggestions/dialects/clickhouse.py` (subtract it)
- Modify: `src/pysqlsuggestions/engine/request.py` (argument-list rule)
- Modify: `src/pysqlsuggestions/resolve.py` (`Kind.PROCEDURE` branch)
- Test: `tests/test_procedures.py`

**Interfaces:**
- Consumes: `Kind.PROCEDURE` from Task 2.
- Produces: `ClauseModel.without(*names) -> ClauseModel`; a `CALL` clause
  suggesting `(Kind.PROCEDURE, Kind.SCHEMA)`; `_NOT_A_RELATION` in `request.py`.

**Background.** `Dialect.__post_init__` folds every clause name and every
`statement_start` phrase into `keywords`, so a clause added here is recognised
as a keyword automatically. `DialectConformance.structure` already reports a
`statement_start` phrase that no clause declares — which means removing the
clause from ClickHouse *forces* removing the statement start too, and the corpus
will say so if you do only one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_procedures.py`:

```python
def test_call_offers_procedures_and_not_functions() -> None:
    """The mirror of the expression case: `CALL now()` is refused just as firmly."""
    found = offered('CALL ')
    assert 'archive_old_reports' in found
    assert 'count' not in found


def test_a_procedure_arrives_ready_for_its_arguments() -> None:
    """`CALL archive_old_reports(` with the caret inside, which is what a call needs next."""
    [found] = [s for s in complete('CALL ', 5, POSTGRES, catalog()) if s.text == 'archive_old_reports']
    assert found.takes_arguments is True


def test_the_argument_list_of_a_call_offers_nothing() -> None:
    """
    `CALL proc(⌶` has no FROM, so no column is in scope, and a procedure cannot
    nest inside a procedure. Everything the clause would otherwise suggest is a
    wrong answer here.
    """
    assert offered('CALL archive_old_reports(') == []


def test_clickhouse_does_not_offer_a_statement_it_cannot_parse() -> None:
    """
    Server-verified: ClickHouse answers `Syntax error … Expected one of: Query,
    …` for `CALL foo()`, and its list of accepted forms has no CALL in it.

    Both halves have to go — the clause and the statement start — because the
    conformance corpus reports a statement start whose clause is missing.
    """
    assert 'CALL' not in CLICKHOUSE.statement_start
    assert CLICKHOUSE.clauses.get('CALL') is None
    assert 'CALL' in POSTGRES.statement_start
    assert 'CALL' in TRINO.statement_start
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_procedures.py -v`
Expected: FAIL — `CALL ` offers nothing, because no clause matches it.

- [ ] **Step 3: Give `ClauseModel` a subtraction**

In `src/pysqlsuggestions/dialects/base.py`, directly after `extend`:

```python
    def without(self, *names: str) -> ClauseModel:
        """
        A new model with `names` removed. The receiver is untouched.

        The counterpart to `extend`, and needed for the same reason: a dialect
        composed from ANSI inherits clauses the standard has and its backend does
        not. ClickHouse has no `CALL` — its parser lists every form it accepts
        and CALL is not among them — and inheriting one would offer a word whose
        statement the server rejects outright.
        """
        dropped = set(names)
        return ClauseModel(clauses=tuple(clause for clause in self.clauses if clause.name not in dropped))
```

- [ ] **Step 4: Declare the clause**

In `src/pysqlsuggestions/dialects/ansi.py`, add to the `CLAUSES` tuple, directly
after the `ALTER TABLE` entry:

```python
        # No `followed_by`: a call ends the statement, and an empty continuation
        # list is how a clause says so — the same rule that stops RETURNING and
        # FETCH proposing a successor.
        Clause(name='CALL', suggests=(Kind.PROCEDURE, Kind.SCHEMA)),
```

and extend `STATEMENT_START`:

```python
STATEMENT_START = (*EXPLAINABLE, 'DROP TABLE', 'TRUNCATE', 'ALTER TABLE', 'CALL')
```

- [ ] **Step 5: Subtract it from ClickHouse**

In `src/pysqlsuggestions/dialects/clickhouse.py`, change the `clauses=` argument
of the `replace(ANSI, ...)` call from `ANSI.clauses.extend(` to
`ANSI.clauses.without('CALL').extend(`, and add a `statement_start` argument
directly after `namespace=`:

```python
    # ClickHouse has no CALL. Its parser answers `CALL foo()` with a syntax
    # error whose message lists every form it does accept, and none of them is
    # this one. Both the clause and the statement start have to go: the
    # conformance corpus reports a statement start whose clause is missing, so
    # doing only one of the two fails the suite.
    statement_start=tuple(phrase for phrase in ANSI.statement_start if phrase != 'CALL'),
```

- [ ] **Step 6: Keep the argument list quiet**

In `src/pysqlsuggestions/engine/request.py`, add a module constant below
`_NAMESPACE_KINDS`:

```python
_NOT_A_RELATION = frozenset({Kind.PROCEDURE})
"""
Kinds naming something the namespace rules do not describe.

A schema holds relations, so `_qualified_kinds` answers one segment with tables
and columns. A clause suggesting one of these is asking for something else, and
two positions have to say so: past a dot, and inside the clause's own argument
list.
"""
```

and in `_clause_kinds`, insert immediately after `found = dialect.clauses.get(clause)`
and its `None` guard:

```python
    if inside_a_group and _NOT_A_RELATION & set(found.suggests):
        # `CALL proc(⌶` is an argument list. There is no FROM, so no column is
        # in scope, and a procedure cannot nest inside one.
        return ()
```

- [ ] **Step 7: Answer the kind in resolve**

In `src/pysqlsuggestions/resolve.py`, in `_unqualified`, directly after the
`Kind.FUNCTION` branch:

```python
    if Kind.PROCEDURE in request.kinds:
        candidates += [
            _function_candidate(f, Kind.PROCEDURE) for f in reader.functions() if f.kind == 'procedure'
        ]
```

- [ ] **Step 8: Retire the assertion that CALL is silent**

`tests/test_statement_forms.py::test_an_unmodelled_form_offers_nothing`
currently asserts `offered('CALL ') == []`. That was true when `CALL` was an
unmodelled form and is false now — the position answers with procedures and
schemas. Delete that one line and extend the docstring:

```python
def test_an_unmodelled_form_offers_nothing() -> None:
    """
    A form the engine does not know is a position it has nothing true to say
    about. It used to say `SELECT`.

    `CALL` was on this list and has been modelled since; what it answers is
    asserted in `tests/test_procedures.py`. The rule is unchanged — these are
    the forms still unmodelled, and the list is expected to shrink.
    """
    assert offered('GRANT ') == []
    assert offered('VACUUM ') == []
    assert offered('CREATE TABLE t (id ') == []
```

- [ ] **Step 9: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. If the conformance suite reports
`statement may start with 'CALL', which no clause here declares` for ClickHouse,
Step 5's two halves have got out of step.

- [ ] **Step 10: Commit**

```bash
git add -A src tests
git commit -m "feat: CALL names a procedure, and ClickHouse says it has no CALL"
```

---

## Task 4: `CALL billing.⌶` names a procedure, not a column

**Files:**
- Modify: `src/pysqlsuggestions/engine/request.py` (`_kinds_for`, `_qualified_kinds`)
- Modify: `src/pysqlsuggestions/resolve.py` (`_qualified`)
- Test: `tests/test_procedures.py`

**Interfaces:**
- Consumes: `_NOT_A_RELATION` from Task 3.
- Produces: `_qualified_kinds(qualifier, scope, dialect, clause)` — note the
  fourth parameter.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_procedures.py`:

```python
def test_a_schema_qualifier_after_call_still_means_procedures() -> None:
    """
    `billing.` normally reads as a schema, whose usual contents are relations —
    so the namespace rule answers with tables and columns. Neither can be
    called, which makes it a wrong answer rather than a thin one.
    """
    found = offered('CALL public.')
    assert 'archive_old_reports' in found
    assert 'auth_user' not in found
    assert 'id' not in found
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_procedures.py::test_a_schema_qualifier_after_call_still_means_procedures -v`
Expected: FAIL — `auth_user` is offered, because the qualifier reads as a schema.

- [ ] **Step 3: Thread the clause through**

In `src/pysqlsuggestions/engine/request.py`, change the last line of `_kinds_for`:

```python
    return _qualified_kinds(qualifier, scope, dialect, clause)
```

and change `_qualified_kinds`'s signature and add the new rule directly after
the `_names_a_relation` check:

```python
def _qualified_kinds(
    qualifier: tuple[str, ...],
    scope: Scope | None,
    dialect: Dialect,
    clause: str | None = None,
) -> tuple[Kind, ...]:
```

```python
    found = dialect.clauses.get(clause) if clause else None
    named = tuple(kind for kind in (found.suggests if found else ()) if kind in _NOT_A_RELATION)
    if named and len(qualifier) < len(dialect.namespace.levels):
        # A clause naming something other than a relation keeps naming it past a
        # dot. `CALL billing.` is a procedure in `billing`, never a column of it.
        return named
```

Leave the docstring's existing paragraphs and add one:

```python
    A clause that names something other than a relation overrides the namespace
    reading entirely, because the namespace reading describes what a schema
    usually holds and this clause is asking for something else in it.
```

- [ ] **Step 4: Fetch it in resolve**

In `src/pysqlsuggestions/resolve.py`, in `_qualified`, directly after the
`if scope is not None:` block closes and before the
`if Kind.COLUMN in request.kinds and len(request.qualifier) >= …` line:

```python
    if Kind.PROCEDURE in request.kinds:
        # One namespace level up from a procedure is a schema, and that is the
        # only reading — a procedure is not a member of a relation.
        return [
            _function_candidate(f, Kind.PROCEDURE)
            for f in reader.functions(request.qualifier[-1])
            if f.kind == 'procedure'
        ]
```

- [ ] **Step 5: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git commit -m "feat: a dot after CALL still means a procedure"
```

---

## Task 5: sequences reach the catalog and stay out of `FROM`

**Files:**
- Modify: `src/pysqlsuggestions/types.py` (`Kind`)
- Modify: `lsp/pysqlsuggestions_lsp/convert.py` (`ITEM_KINDS`)
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (`_RELKIND`, two `relkind IN` lists)
- Modify: `src/pysqlsuggestions/resolve.py` (`_unqualified`, `_qualified` table branches)
- Test: `tests/test_sequences.py` (new)

**Interfaces:**
- Produces: `Kind.SEQUENCE`; `Table.kind == 'sequence'` from relkind `S`.

**The load-bearing assertion here is the one that says nothing changed.** Every
other line in this task exists so that `FROM ⌶` keeps answering exactly what it
answered before, with nineteen sequences now in the catalog.

**Why the filter is negative.** `Table.kind` is the storage engine name on
ClickHouse — `mergetree`, `log`, `distributed` — not a relational category. A
positive whitelist of queryable kinds would empty ClickHouse's `FROM` clause
entirely. "Not a sequence" is the only rule that holds for a backend this
package has never heard of.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sequences.py`:

```python
"""
A sequence is a relation you may not put in a FROM list.

Not because the server refuses it — `SELECT * FROM auth_user_id_seq` returns
`last_value | log_cnt | is_called` quite happily — but because a schema created
by Django has one sequence per table, and doubling the commonest caret in the
language with names nobody is reaching for is a cost paid on every keystroke.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {
    ('public', 'auth_user'): [('id', 'bigint'), ('email', 'varchar')],
    ('public', 'auth_user_id_seq'): [('last_value', 'bigint')],
    ('billing', 'MonthlyTotals_id_seq'): [('last_value', 'bigint')],
}
KINDS = {('public', 'auth_user_id_seq'): 'sequence', ('billing', 'MonthlyTotals_id_seq'): 'sequence'}


def catalog() -> MemoryCatalog:
    """A snapshot holding one table and two sequences, one of them off the search path."""
    return MemoryCatalog(SNAPSHOT, table_kinds=KINDS, search_path=('public',))


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def test_a_relation_position_never_offers_a_sequence() -> None:
    """
    The assertion the whole filter exists to pass. A Django schema has one
    sequence per table, so without this the commonest caret in the language
    doubles in length with names nobody is reaching for.
    """
    found = offered('SELECT * FROM ')
    assert 'auth_user' in found
    assert 'auth_user_id_seq' not in found


def test_a_prefix_search_does_not_reach_one_either() -> None:
    """The search path is not what hides a sequence, so reaching past it must not reveal one."""
    assert 'MonthlyTotals_id_seq' not in offered('SELECT * FROM Month')


def test_a_schema_qualifier_does_not_list_sequences() -> None:
    """`billing.` lists what you can query in `billing`, which is not everything in it."""
    assert 'MonthlyTotals_id_seq' not in offered('SELECT * FROM billing.')


def test_the_postgres_queries_now_fetch_sequences() -> None:
    """Both paths, because a sequence outside the search path has to be reachable by prefix."""
    tables = POSTGRES.catalog_queries.tables
    search = POSTGRES.catalog_queries.relation_search
    assert tables is not None
    assert search is not None
    assert "'S'" in tables.sql
    assert "'S'" in search.sql
    assert tables.row(('public', 'auth_user_id_seq', 'S', 1)).kind == 'sequence'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sequences.py -v`
Expected: FAIL — `auth_user_id_seq` is offered in the `FROM` position, and
`'S'` is in neither query.

- [ ] **Step 3: Add the kind**

In `src/pysqlsuggestions/types.py`, after `Kind.TABLE` and its `CTE` neighbour:

```python
    SEQUENCE = 'sequence'
    """
    A generator of numbers, which lives in the relation namespace and is not one.

    Selectable — `SELECT * FROM a_seq` returns its state — and never what
    anybody means by `FROM ⌶`, since a schema has one per serial column. Named
    where it is wanted instead: `nextval('⌶`, `DROP SEQUENCE ⌶`.
    """
```

- [ ] **Step 4: Map it for LSP**

In `lsp/pysqlsuggestions_lsp/convert.py`, add to `ITEM_KINDS`:

```python
    Kind.SEQUENCE: CompletionItemKind.Reference,
```

- [ ] **Step 5: Fetch sequences from Postgres**

In `src/pysqlsuggestions/dialects/postgres.py`, add to `_RELKIND`:

```python
    'S': 'sequence',
```

and add `'S'` to the `relkind IN` list in **both** the `tables` query and the
`relation_search` query:

```python
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
```

(In `tables` the line reads `WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')`; in
`relation_search` it is the same text. Both change.)

Add a comment above the `tables` query's `WHERE`:

```python
            -- 'S' is a sequence. It is fetched here rather than by a query of
            -- its own because it is a relation in every sense pg_class knows;
            -- `resolve` is what keeps it out of a FROM list.
```

- [ ] **Step 6: Filter in resolve**

In `src/pysqlsuggestions/resolve.py`, add a module constant next to
`_MAX_VALUES`:

```python
_SEQUENCE = 'sequence'
"""
The one relation kind that is not a relation to query.

Tested for negatively — "not a sequence" rather than "one of these kinds" —
because `Table.kind` is the storage engine name on ClickHouse (`mergetree`,
`log`) and the relation type on Postgres. No whitelist of ours could enumerate
the engines a ClickHouse installation has, and one that tried would empty its
FROM clause.
"""
```

In `_unqualified`, replace the `Kind.TABLE` branch's first three statements:

```python
    if Kind.TABLE in request.kinds:
        listed = [table for table in reader.tables(None) if table.kind != _SEQUENCE]
        candidates += [_table_candidate(table) for table in listed]
        here = {(table.schema, table.name) for table in listed}
        candidates += [
            _table_candidate(table, qualify=table.schema)
            for table in reader.search_relations(request.prefix, limit)
            if table.kind != _SEQUENCE and (table.schema, table.name) not in here
        ]
```

In `_qualified`, change the `Kind.TABLE` branch:

```python
    if Kind.TABLE in request.kinds:
        candidates += [
            _table_candidate(table)
            for table in reader.tables(request.qualifier[-1])
            if table.kind != _SEQUENCE
        ]
```

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass, including the LSP guard.

- [ ] **Step 8: Commit**

```bash
git add -A src lsp tests
git commit -m "feat: sequences reach the catalog and nothing offers them a FROM"
```

---

## Task 6: `DROP SEQUENCE` and `ALTER SEQUENCE`

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (clauses, `statement_start`)
- Modify: `src/pysqlsuggestions/resolve.py` (`_table_candidate`, `Kind.SEQUENCE` branches)
- Test: `tests/test_sequences.py`

**Interfaces:**
- Consumes: `Kind.SEQUENCE`, `_SEQUENCE` from Task 5.
- Produces: `_table_candidate(table, qualify=None, kind=Kind.TABLE)` — note the
  third parameter.

**The trap to avoid.** Last slice, `ALTER TABLE`'s continuations were written as
bare words and `('DROP',)` became a phrase in its own right —
`_half_written_clauses` skips a head that is already a phrase, so `DROP ⌶`
stopped answering `TABLE`. Here the two new clause *names* share the heads
`DROP` and `ALTER`, and neither head is a phrase, so both are answered. That is
what Step 1's test pins.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sequences.py`:

```python
def test_dropping_a_sequence_offers_sequences_and_nothing_else() -> None:
    """A relation would not parse there: `DROP SEQUENCE auth_user` is refused."""
    found = offered('DROP SEQUENCE ')
    assert 'auth_user_id_seq' in found
    assert 'auth_user' not in found


def test_altering_one_offers_the_same_names() -> None:
    """Same position, same answer. The two clauses differ only in what may follow."""
    assert 'auth_user_id_seq' in offered('ALTER SEQUENCE ')


def test_a_shared_head_answers_with_both_of_its_phrases() -> None:
    """
    `DROP` begins two clause names now, and neither `DROP` nor `ALTER` is a
    clause in its own right — so both continuations are offered. This is the
    case that broke last slice, when a bare `DROP` among ALTER TABLE's
    continuations made ('DROP',) a phrase and `DROP ⌶` stopped answering TABLE.
    """
    assert set(offered('DROP ')) >= {'TABLE', 'SEQUENCE'}
    assert set(offered('ALTER ')) >= {'TABLE', 'SEQUENCE'}


def test_a_schema_qualifier_names_a_sequence_in_it() -> None:
    """`billing.` after this clause lists what the clause is for, not what a schema holds."""
    found = offered('DROP SEQUENCE billing.')
    assert 'MonthlyTotals_id_seq' in found
    assert 'auth_user' not in found


def test_both_clauses_can_start_a_statement() -> None:
    """
    Not optional: the conformance corpus reports a statement start whose clause
    is missing, and the converse — a clause never reachable — is what would make
    these dead on arrival.
    """
    assert 'DROP SEQUENCE' in POSTGRES.statement_start
    assert 'ALTER SEQUENCE' in POSTGRES.statement_start
    assert 'DROP SEQUENCE' not in TRINO.statement_start
```

and add to that file's imports:

```python
from pysqlsuggestions.dialects.trino import TRINO
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sequences.py -v`
Expected: FAIL — `DROP SEQUENCE ` offers nothing.

- [ ] **Step 3: Declare the clauses**

In `src/pysqlsuggestions/dialects/postgres.py`, add to the `ANSI.clauses.extend(`
call, after the `EXPLAIN` entry:

```python
        # Postgres's alone. Trino's parser lists what DROP accepts — CATALOG,
        # FUNCTION, MATERIALIZED, ROLE, SCHEMA, TABLE, VIEW — and SEQUENCE is
        # not among them; ClickHouse has no sequences at all. A form only one
        # shipped backend implements belongs to that one rather than to the
        # baseline they share.
        #
        # Two-word continuations, for the reason ALTER TABLE's are: a bare
        # `RENAME` would make ('RENAME',) a phrase in its own right, and
        # `_half_written_clauses` skips a head that is already a phrase.
        Clause(
            name='DROP SEQUENCE',
            suggests=(Kind.SEQUENCE, Kind.SCHEMA),
            followed_by=('CASCADE', 'RESTRICT'),
        ),
        Clause(
            name='ALTER SEQUENCE',
            suggests=(Kind.SEQUENCE, Kind.SCHEMA),
            followed_by=('RENAME TO', 'OWNED BY'),
        ),
```

and add a `statement_start` argument to the `replace(ANSI, ...)` call, directly
after `namespace=`:

```python
    statement_start=(*ANSI.statement_start, 'DROP SEQUENCE', 'ALTER SEQUENCE'),
```

- [ ] **Step 4: Let a sequence qualifier keep meaning a sequence**

Task 3 defined `_NOT_A_RELATION` in `src/pysqlsuggestions/engine/request.py` as
`frozenset({Kind.PROCEDURE})`, because `Kind.SEQUENCE` did not exist yet. It
does now, and `DROP SEQUENCE billing.⌶` needs the same narrowing for the same
reason — the namespace rule would answer with the tables and columns `billing`
holds, none of which can be dropped as a sequence:

```python
_NOT_A_RELATION = frozenset({Kind.PROCEDURE, Kind.SEQUENCE})
```

- [ ] **Step 5: Build the candidates**

In `src/pysqlsuggestions/resolve.py`, change `_table_candidate`'s signature and
its `kind=` argument:

```python
def _table_candidate(table: Table, qualify: str | None = None, kind: Kind = Kind.TABLE) -> Candidate:
```

```python
        kind=kind,
```

Add a helper below `_expansion`:

```python
def _sequences(request: Request, reader: _Reader, limit: int) -> list[Candidate]:
    """
    Sequences by name, from the default namespace and from a prefix search.

    The same two sources a relation comes from, and for the same reason: a
    sequence outside the search path has to be written qualified, and slice 2
    already built the half that finds one.
    """
    listed = [table for table in reader.tables(None) if table.kind == _SEQUENCE]
    here = {(table.schema, table.name) for table in listed}
    found = [(table, None) for table in listed]
    found += [
        (table, table.schema)
        for table in reader.search_relations(request.prefix, limit)
        if table.kind == _SEQUENCE and (table.schema, table.name) not in here
    ]
    return [_table_candidate(table, qualify=qualify, kind=Kind.SEQUENCE) for table, qualify in found]
```

In `_unqualified`, directly after the `Kind.TABLE` branch:

```python
    if Kind.SEQUENCE in request.kinds:
        candidates += _sequences(request, reader, limit)
```

In `_qualified`, directly after the `Kind.PROCEDURE` branch added in Task 4:

```python
    if Kind.SEQUENCE in request.kinds:
        return [
            _table_candidate(table, kind=Kind.SEQUENCE)
            for table in reader.tables(request.qualifier[-1])
            if table.kind == _SEQUENCE
        ]
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. If conformance reports
`statement may start with 'DROP SEQUENCE', which no clause here declares`, Step 3
added the statement start without the clause.

- [ ] **Step 7: Commit**

```bash
git add -A src tests
git commit -m "feat: two statements that name a sequence"
```

---

## Task 7: a literal that is a call's first argument

**Files:**
- Modify: `src/pysqlsuggestions/dialects/base.py` (`LiteralArgument`, `Dialect.literal_arguments`)
- Modify: `src/pysqlsuggestions/engine/analyse.py` (three helpers)
- Modify: `src/pysqlsuggestions/types.py` (`Request.writes_a_literal`)
- Modify: `src/pysqlsuggestions/engine/request.py` (`_inside_a_literal`)
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (`literal_arguments`)
- Test: `tests/test_sequences.py`

**Interfaces:**
- Produces: `LiteralArgument(function: str, suggests: tuple[Kind, ...])`;
  `Dialect.literal_arguments: tuple[LiteralArgument, ...]`;
  `analyse.literal_argument_call(tokens, caret) -> str | None`;
  `Request.writes_a_literal: bool`.

**Background on token depths.** An opening `(` carries the *outer* depth, so in
`nextval('x')` the `nextval` and `(` tokens are at depth 0 and the string is at
depth 1. `_enclosing_call` already relies on this; the new `_call_opening`
factors out the paren-finding half so the comma scan can reuse it rather than
keeping a second copy of the rule.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sequences.py`:

```python
def request(sql: str) -> Request:
    """The request at the end of `sql`."""
    return derive_request(sql, len(sql), POSTGRES)


def test_a_literal_naming_a_sequence_is_a_position() -> None:
    """`nextval` takes a regclass, and the dialect is where that fact lives."""
    found = request("SELECT nextval('")
    assert found.kinds == (Kind.SEQUENCE,)
    assert found.writes_a_literal is True


def test_what_is_typed_inside_the_literal_is_the_prefix() -> None:
    """The quote is not part of what the user is hunting for."""
    assert request("SELECT nextval('aut").prefix == 'aut'


def test_the_span_covers_the_whole_literal() -> None:
    """The answer replaces the literal rather than nesting a second one inside it."""
    sql = "SELECT nextval('aut"
    assert request(sql).replace_span == (15, len(sql))


def test_a_later_argument_is_not_the_position() -> None:
    """
    `setval('seq', 1)` names its sequence first. What the first argument names
    says nothing about the second, so a caret past a comma keeps its silence.
    """
    assert request("SELECT setval('s', '") .kinds == ()


def test_an_undeclared_function_is_not_the_position() -> None:
    """A literal inside `lower('…')` is a string, and offering a relation there is nonsense."""
    assert request("SELECT lower('") .kinds == ()


def test_a_dialect_declaring_none_has_no_such_position() -> None:
    """ANSI has no nextval, so the same SQL is an ordinary literal there."""
    assert derive_request("SELECT nextval('", 16, ANSI).kinds == ()


def test_a_comparison_literal_still_offers_values() -> None:
    """The older reading of a caret inside a literal is untouched by the new one."""
    found = derive_request("SELECT * FROM auth_user WHERE email = 'a", 40, POSTGRES)
    assert found.kinds == (Kind.VALUE,)
```

and add to that file's imports:

```python
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.engine.request import derive_request
from pysqlsuggestions.types import Kind, Request
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sequences.py -v`
Expected: FAIL — `Request` has no attribute `writes_a_literal`.

- [ ] **Step 3: Add the dialect record**

In `src/pysqlsuggestions/dialects/base.py`, after the `Placeholder` block and
before `Syntax`:

```python
@dataclass(frozen=True, slots=True)
class LiteralArgument:
    """
    A call whose first argument is a name written inside a string literal.

    `nextval('users_id_seq')` names a relation in a place the grammar calls a
    string, so nothing about the syntax marks it — only the identity of the
    function does, which makes it dialect data.

    Deliberately not inferred from the declared argument type. `nextval` takes a
    `regclass`, and reading that would offer every relation in the database
    where only a sequence is valid: a wrong answer replacing a missing one.
    """

    function: str
    """The bare function name, matched case-insensitively."""
    suggests: tuple[Kind, ...]
    """What the first argument names. Most relevant first."""
```

and add to `Dialect`, after `types`:

```python
    literal_arguments: tuple[LiteralArgument, ...] = ()
    """
    Calls whose first argument is a name in a literal. Empty for most dialects.

    Empty means a caret inside a string admits nothing but the values a compared
    column holds, which is what every position did before this existed.
    """
```

- [ ] **Step 4: Find the position in `analyse`**

In `src/pysqlsuggestions/engine/analyse.py`, replace `string_under` with two
functions, and replace `_enclosing_call` with two:

```python
def _string_index_under(tokens: Sequence[Token], caret: int) -> int:
    """Index of the string literal the caret is inside, or -1."""
    for index, token in enumerate(tokens):
        if token.type is TokenType.STRING and _inside(token, caret):
            return index
    return -1


def string_under(tokens: Sequence[Token], caret: int) -> Token | None:
    """
    The string literal the caret is inside, if it is inside one.

    Separate from `in_literal`, which also answers for comments: a half-typed
    literal is a position with an answer — the values that column holds, or the
    sequence a `nextval` names — where a comment is a position with none.
    """
    index = _string_index_under(tokens, caret)
    return tokens[index] if index >= 0 else None
```

```python
def _call_opening(tokens: Sequence[Token], index: int) -> int:
    """
    Index of the `(` whose argument list encloses `index`, or -1.

    An opening paren carries the depth *outside* it, so the one being looked for
    sits one level below the token it encloses.
    """
    wanted = tokens[index].depth - 1
    for candidate in range(index - 1, -1, -1):
        token = tokens[candidate]
        if token.type is TokenType.PUNCT and token.text == '(' and token.depth == wanted:
            return candidate
    return -1


def _enclosing_call(tokens: Sequence[Token], index: int) -> str | None:
    """The uppercased name of the function whose argument list encloses `index`, if any."""
    opening = _call_opening(tokens, index)
    if opening < 0:
        return None
    before = _skip_back(tokens, opening - 1)
    if before < 0 or tokens[before].type is not TokenType.IDENT:
        return None
    return tokens[before].value.upper()


def literal_argument_call(tokens: Sequence[Token], caret: int) -> str | None:
    """
    The uppercased name of the call whose *first* argument the caret's literal is.

    None when the caret is not inside a string, when that string is not directly
    inside an argument list, or when a comma at the same depth puts it past the
    first argument. `nextval('<caret>` answers NEXTVAL; `setval('s', '<caret>`
    answers nothing, because what a call's first argument names says nothing
    about its later ones.
    """
    index = _string_index_under(tokens, caret)
    if index < 0:
        return None
    name = _enclosing_call(tokens, index)
    if name is None:
        return None
    opening = _call_opening(tokens, index)
    depth = tokens[index].depth
    if any(
        token.type is TokenType.PUNCT and token.text == ',' and token.depth == depth
        for token in tokens[opening + 1 : index]
    ):
        return None
    return name
```

- [ ] **Step 5: Carry the fact on the request**

In `src/pysqlsuggestions/types.py`, add to `Request` after `star_qualifier`:

```python
    writes_a_literal: bool = False
    """
    Whether the span replaces a string literal, so an answer needs quoting into one.

    `nextval('<caret>` and `DROP SEQUENCE <caret>` both want sequences and write
    them differently — one inside quotes, one bare — and the kind cannot say
    which, because it is the same kind. Only the position knows.
    """
```

- [ ] **Step 6: Read it in `request.py`**

In `src/pysqlsuggestions/engine/request.py`, add `literal_argument_call` to the
`analyse` import list (keeping it alphabetical: it sorts after `in_placeholder`
and before `inside_a_cast_awaiting_as`), pass the dialect to the call site:

```python
    if in_literal(tokens, caret):
        return _inside_a_literal(tokens, caret, clause, scope, comparand, dialect)
```

and replace `_inside_a_literal` entirely:

```python
def _inside_a_literal(
    tokens: Sequence[Token],
    caret: int,
    clause: str | None,
    scope: Scope | None,
    comparand: tuple[str, ...],
    dialect: Dialect,
) -> Request:
    """
    What a caret inside a literal or a comment admits.

    Nothing, except in the two places a literal is being written as something
    other than free text. A declared call names an object in its first argument
    — `nextval('<caret>` is asking which sequences exist — and a comparison's
    right-hand side is asking which values that column holds. Going silent the
    moment the opening quote is typed makes either feature look broken.

    The declared call is read first. It is the narrower fact: it depends on the
    identity of the enclosing function rather than on what the position usually
    admits, and a call written inside a comparison is still a call.

    The span covers the literal in both cases, so the answer replaces it rather
    than nesting inside it.
    """
    written = string_under(tokens, caret)
    if written is None:
        return Request(kinds=(), prefix='', replace_span=(caret, caret), clause=clause, scope=scope)
    quote = written.text[0]
    typed = written.text[1 : caret - written.start]
    prefix = typed.replace(quote * 2, quote)
    span = (written.start, written.end if written.terminated else caret)

    named = _literal_argument_kinds(tokens, caret, dialect)
    if named:
        return Request(
            kinds=named,
            prefix=prefix,
            replace_span=span,
            clause=clause,
            scope=scope,
            writes_a_literal=True,
        )
    if not comparand:
        return Request(kinds=(), prefix='', replace_span=(caret, caret), clause=clause, scope=scope)
    return Request(
        kinds=(Kind.VALUE,),
        prefix=prefix,
        replace_span=span,
        clause=clause,
        scope=scope,
        comparand=comparand,
        writes_a_literal=True,
    )


def _literal_argument_kinds(tokens: Sequence[Token], caret: int, dialect: Dialect) -> tuple[Kind, ...]:
    """
    What the dialect says this call's first argument names, if it says anything.

    Matched case-insensitively, because `NEXTVAL('x')` and `nextval('x')` are the
    same call and a dialect should not have to spell both.
    """
    called = literal_argument_call(tokens, caret)
    if called is None:
        return ()
    for declared in dialect.literal_arguments:
        if declared.function.upper() == called:
            return declared.suggests
    return ()
```

- [ ] **Step 7: Declare them for Postgres**

In `src/pysqlsuggestions/dialects/postgres.py`, add `LiteralArgument` to the
`dialects.base` import, and add to the `replace(ANSI, ...)` call after `types=`:

```python
    # The three calls that name a sequence in a string. Their argument is a
    # `regclass`, which the server will accept for any relation — so the fact
    # that only a sequence is *valid* here is knowledge about these functions
    # rather than about their signature, which is why it is written down.
    literal_arguments=(
        LiteralArgument(function='nextval', suggests=(Kind.SEQUENCE,)),
        LiteralArgument(function='currval', suggests=(Kind.SEQUENCE,)),
        LiteralArgument(function='setval', suggests=(Kind.SEQUENCE,)),
    ),
```

- [ ] **Step 8: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. `ruff format` will remove the space before `.kinds` in the
two tests written as `request("…") .kinds`; let it.

- [ ] **Step 9: Commit**

```bash
git add -A src tests
git commit -m "feat: a literal that is a call's first argument is a position"
```

---

## Task 8: the sequence written into the literal

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py` (`_sequences`)
- Test: `tests/test_sequences.py`

**Interfaces:**
- Consumes: `Request.writes_a_literal` from Task 7, `_sequences` from Task 6.

**The fact this rests on, server-verified:**
`nextval('billing."MonthlyTotals_id_seq"')` runs, and
`nextval('billing.MonthlyTotals_id_seq')` is refused with
`relation "billing.monthlytotals_id_seq" does not exist`. The identifier keeps
its quotes *inside* the string, because the server parses that string as a
`regclass` rather than as text.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sequences.py`:

```python
def test_a_sequence_inside_a_literal_is_quoted_into_one() -> None:
    """The whole literal is replaced, so the answer supplies its own quotes."""
    assert "'auth_user_id_seq'" in offered("SELECT nextval('")


def test_a_name_needing_identifier_quotes_keeps_them_inside_the_string() -> None:
    """
    Server-verified: `nextval('billing."MonthlyTotals_id_seq"')` runs, and the
    unquoted spelling is refused with `relation … does not exist`. The string is
    parsed as a regclass, not as text, so the quoting rules are the identifier's.
    """
    assert '\'billing."MonthlyTotals_id_seq"\'' in offered("SELECT nextval('Month")


def test_the_bare_name_is_what_matching_and_the_list_show() -> None:
    """Typing `aut` must find it, and a popup should show a name rather than a quoted string."""
    [found] = [
        s for s in complete("SELECT nextval('aut", 19, POSTGRES, catalog()) if s.kind is Kind.SEQUENCE
    ]
    assert found.label == 'auth_user_id_seq'
    assert found.text == "'auth_user_id_seq'"


def test_the_same_kind_is_written_bare_where_the_position_is_bare() -> None:
    """One kind, two renderings. `DROP SEQUENCE` takes an identifier, not a string."""
    assert 'auth_user_id_seq' in offered('DROP SEQUENCE ')
    assert "'auth_user_id_seq'" not in offered('DROP SEQUENCE ')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sequences.py -v`
Expected: FAIL — the literal position offers `auth_user_id_seq` unquoted.

- [ ] **Step 3: Render both ways**

In `src/pysqlsuggestions/resolve.py`, replace the last line of `_sequences` and
extend its docstring:

```python
def _sequences(request: Request, reader: _Reader, dialect: Dialect, limit: int) -> list[Candidate]:
    """
    Sequences by name, from the default namespace and from a prefix search.

    The same two sources a relation comes from, and for the same reason: a
    sequence outside the search path has to be written qualified, and slice 2
    already built the half that finds one.

    Written bare or into a string literal, because the two positions that want a
    sequence spell it differently. `DROP SEQUENCE <caret>` takes an identifier.
    `nextval('<caret>` takes a string the server parses as a `regclass`, which
    means the identifier keeps its own quotes inside it —
    `nextval('billing."MonthlyTotals_id_seq"')` runs where the unquoted spelling
    is refused. The kind cannot tell the two apart; only the request can.
    """
    listed = [table for table in reader.tables(None) if table.kind == _SEQUENCE]
    here = {(table.schema, table.name) for table in listed}
    found = [(table, None) for table in listed]
    found += [
        (table, table.schema)
        for table in reader.search_relations(request.prefix, limit)
        if table.kind == _SEQUENCE and (table.schema, table.name) not in here
    ]
    if not request.writes_a_literal:
        return [_table_candidate(table, qualify=qualify, kind=Kind.SEQUENCE) for table, qualify in found]
    return [_sequence_literal(table, qualify, dialect) for table, qualify in found]


def _sequence_literal(table: Table, qualify: str | None, dialect: Dialect) -> Candidate:
    """
    One sequence, spelled as the string literal that names it.

    `literal=True` carries the text through insertion untouched, which makes the
    quoting this function's job — both kinds of it. The identifier is quoted by
    the dialect's rules because the server reads the string as a `regclass`, and
    then the whole thing is quoted as a string, doubling any interior quote.

    `label` and `match_text` carry the bare name: typing `mon` should find it by
    the word-prefix tier rather than the substring one, and a popup should show a
    name rather than a quoted string.
    """
    parts = (qualify, table.name) if qualify else (table.name,)
    written = '.'.join(quote_if_needed(part, dialect) for part in parts)
    return Candidate(
        text="'" + written.replace("'", "''") + "'",
        kind=Kind.SEQUENCE,
        detail=f'{table.schema}.{table.name} (sequence)',
        label=table.name,
        match_text=table.name,
        literal=True,
        position=1 if qualify else 0,
    )
```

and update the one call site in `_unqualified`:

```python
    if Kind.SEQUENCE in request.kinds:
        candidates += _sequences(request, reader, dialect, limit)
```

- [ ] **Step 4: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "feat: a sequence in a regclass literal keeps its identifier quotes"
```

---

## Task 9: the shipped corpus holds every dialect to it

**Files:**
- Modify: `src/pysqlsuggestions/testing/__init__.py`
- Test: `tests/test_conformance.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: three cases and one structure check in `DialectConformance`.

**Why this task exists.** `tests/test_sequences.py` proves Postgres does the
right thing. The corpus is what proves it for a dialect nobody here has written
— including the one a third party ships through the entry-point group. The case
worth having most is the negative one: a relation position never offers a
sequence, asserted for every dialect, because the shared fixture always has one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conformance.py`:

```python
def test_the_corpus_asks_every_dialect_to_keep_sequences_out_of_a_relation_position() -> None:
    """
    The fixture always holds a sequence, so the proposition applies to a dialect
    that has none — which is the point. A third-party dialect fetching relkind
    'S' without filtering finds out here rather than from its users.
    """
    for dialect in SHIPPED:
        assert [case for case in DialectConformance.cases(dialect) if 'sequence' in case.name]


def test_a_dialect_that_offers_sequences_for_a_relation_fails_the_corpus() -> None:
    """
    Broken on purpose, like every case in the second half of this file. A clause
    suggesting SEQUENCE where a relation belongs is the exact mistake the filter
    prevents, and it is silent — the list is merely longer.
    """
    broken = replace(
        POSTGRES,
        clauses=POSTGRES.clauses.extend(Clause(name='FROM', follows=frozenset({'SELECT'}), suggests=(Kind.SEQUENCE,))),
    )
    assert DialectConformance.check(broken)


def test_a_literal_argument_that_can_never_match_is_reported() -> None:
    """
    `_enclosing_call` returns a single uppercased word, so a name with a dot, a
    space or parentheses in it can never equal one — and an empty `suggests` can
    never produce a candidate. Both are silent, which is what `structure` is for.
    """
    broken = replace(ANSI, literal_arguments=(LiteralArgument(function='pg_catalog.nextval', suggests=(Kind.SEQUENCE,)),))
    assert any('single word' in problem for problem in DialectConformance.structure(broken))
    empty = replace(ANSI, literal_arguments=(LiteralArgument(function='nextval', suggests=()),))
    assert any('suggests nothing' in problem for problem in DialectConformance.structure(empty))


def test_a_dialect_declaring_no_literal_arguments_gets_no_case() -> None:
    """
    The corpus asks a dialect only what it claims to do — the same bargain
    `parameter()` makes for `?` and `relation_search` makes for Trino.
    """
    assert not [case for case in DialectConformance.cases(TRINO) if 'literal' in case.name]
    assert [case for case in DialectConformance.cases(POSTGRES) if 'literal' in case.name]
```

and add `LiteralArgument` to that file's `dialects.base` import.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_conformance.py -v`
Expected: FAIL — no case mentions a sequence, and `structure` reports nothing
about literal arguments.

- [ ] **Step 3: Put a sequence and a procedure in the fixture**

In `src/pysqlsuggestions/testing/__init__.py`, add module constants after
`ORDERS`:

```python
SEQUENCE = 'orders_id_seq'
"""
A sequence in the fixture, so every dialect is asked to keep one out of a
relation position — including the dialects that have no sequences at all. A
proposition that only applied to backends with the feature could not catch the
dialect that grows it next.
"""
PROCEDURE = 'recalculate_totals'
```

and add `Function` to the imports:

```python
from pysqlsuggestions.types import Function
```

Replace the body of `catalog`:

```python
    @staticmethod
    def catalog(dialect: Dialect) -> MemoryCatalog:
        """
        A fixture shaped to this dialect's namespace depth.

        `OTHER` sits outside the search path on purpose: a relation the bare
        position cannot see is the only way a case can tell a dialect that
        searches from one that merely lists.

        A sequence and a procedure are always present, whether or not the
        dialect has either. Both exist to be *excluded* from the ordinary
        positions, and a fixture that only held them for backends with the
        feature could not make that proposition at all.
        """
        snapshot = {
            (SCHEMA, 'users'): list(USERS),
            (SCHEMA, 'orders'): list(ORDERS),
            (OTHER, 'archived_orders'): list(ORDERS),
            (SCHEMA, SEQUENCE): [('last_value', 'bigint')],
        }
        kinds = {(SCHEMA, SEQUENCE): 'sequence'}
        # `order_count` rather than `total`: the fixture already has a column
        # called `total`, and a forbid clause that could be satisfied by the
        # wrong thing proves nothing.
        functions = (
            Function(schema=SCHEMA, name=PROCEDURE, args='', result=None, kind='procedure'),
            Function(schema=SCHEMA, name='order_count', args='', result='integer'),
        )
        if len(dialect.namespace.levels) >= 3:  # noqa: PLR2004
            return MemoryCatalog(
                snapshot,
                table_kinds=kinds,
                functions=functions,
                catalogs={CATALOG: [SCHEMA, OTHER]},
            )
        return MemoryCatalog(snapshot, table_kinds=kinds, functions=functions, search_path=(SCHEMA,))
```

- [ ] **Step 4: Add the three cases**

In `cases`, insert into the `cases = [...]` literal, after the
`'a relation position offers relations'` entry:

```python
            Case(
                name='a relation position never offers a sequence',
                sql='SELECT * FROM ',
                forbid=(SEQUENCE,),
            ),
```

and after the `relation_search` block, add two more conditional blocks:

```python
        declared = next(iter(dialect.literal_arguments), None)
        if declared is not None:
            cases.append(
                Case(
                    name='a literal argument offers what the dialect says it names',
                    sql=f"SELECT {declared.function}('",
                    expect=(SEQUENCE,),
                    forbid=('users',),
                ),
            )
        calls = next((c.name for c in dialect.clauses.clauses if Kind.PROCEDURE in c.suggests), None)
        if calls is not None:
            cases.append(
                Case(
                    name='a procedure position offers procedures and not functions',
                    sql=f'{calls} ',
                    expect=(PROCEDURE,),
                    forbid=('order_count',),
                ),
            )
```

and add `Kind` to the module's imports:

```python
from pysqlsuggestions.types import Function, Kind
```

Note: the case is found by *what the clause suggests* rather than by the name
`CALL`, so a dialect spelling its call statement differently is still covered.

- [ ] **Step 5: Let `check` see a name written into a literal**

The literal-argument case cannot pass without this. `check` compares against
`s.text`, and a sequence inside `nextval('…')` arrives as `'orders_id_seq'` —
quotes included — so `'orders_id_seq' in plain` is false and the case fails for
a reason that has nothing to do with the behaviour under test.

In `check`, replace the `plain` line:

```python
            # A name written into a string literal — a sequence inside
            # `nextval('…')` — is still that name, and every proposition here is
            # about the name. The quotes belong to the position, not the answer.
            plain = {text.rsplit('.', 1)[-1].strip('\'"') for text in found}
```

- [ ] **Step 6: Add the structure check**

In `structure`, before the `return`:

```python
        for declared in dialect.literal_arguments:
            if len(declared.function.split()) != 1 or not declared.function.isidentifier():
                problems.append(
                    f'literal argument {declared.function!r} is not a single word, '
                    f'so it can never equal the name of an enclosing call',
                )
            if not declared.suggests:
                problems.append(f'literal argument {declared.function!r} suggests nothing, so it can never answer')
```

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. If a shipped dialect now fails
`a relation position never offers a sequence`, Task 5's filter has a hole —
fix it there rather than weakening the case.

- [ ] **Step 8: Commit**

```bash
git add -A src tests
git commit -m "test: the corpus holds every dialect to what a sequence is not"
```

---

## Task 10: the golden corpus, the seed, and the live servers

**Files:**
- Modify: `tests/corpus/cases.py`
- Modify: `tests/integration/test_acceptance.py`
- Modify: `docker/postgres/01-schema.sql`
- Modify: `tests/integration/test_backends.py`

**Interfaces:**
- Consumes: everything from Tasks 1–9.

**The seed change needs a rebuild.** `docker-entrypoint-initdb.d` scripts run
only on an empty data directory, so after editing the SQL you must
`docker compose -f docker/docker-compose.yml down -v` and bring it back up with
`--wait`. Nothing else in the suite will tell you that you forgot.

**Why nothing joins the acceptance `CORPUS`.** That harness parses by prefixing
`EXPLAIN`, and `EXPLAIN CALL probe_proc()` is `syntax error at or near "CALL"`.
Separately, its `carets()` generator stops at each end of each space and at the
end of the statement, so a caret inside a string literal is unreachable to it —
an entry for `nextval` would sweep offsets 6, 7 and the end and never touch the
position under test. The literal case gets a direct test instead.

- [ ] **Step 1: Write the failing tests**

Add to the `CASES` tuple in `tests/corpus/cases.py`, at the end:

```python
    GoldenRequest(
        sql='CALL ⌶',
        kinds=('procedure', 'schema'),
        clause='CALL',
        note='a statement that invokes rather than evaluates',
    ),
    GoldenRequest(
        sql="SELECT nextval('⌶",
        kinds=('sequence',),
        clause='SELECT',
        note='a name inside a literal, because the server reads that literal as a regclass',
    ),
```

Add to `tests/integration/test_backends.py`, in the PostgreSQL section:

```python
def test_postgres_finds_the_seeded_procedure(postgres_catalog: DbapiCatalog) -> None:
    """
    Stock Postgres 16 ships no procedures at all — pg_proc holds only 'f', 'a'
    and 'w' — so the seed is the only place this assertion can come from.
    """
    found = suggest('CALL ⌶', POSTGRES, postgres_catalog)
    assert 'recalculate_totals' in found


def test_postgres_keeps_the_procedure_out_of_an_expression(postgres_catalog: DbapiCatalog) -> None:
    """`SELECT recalculate_totals()` is refused by the server: `… is a procedure`."""
    found = suggest('SELECT ⌶', POSTGRES, postgres_catalog)
    assert 'count' in found
    assert 'recalculate_totals' not in found


def test_postgres_offers_no_sequence_where_a_relation_belongs(postgres_catalog: DbapiCatalog) -> None:
    """The seed's bigserial columns create one sequence per table; none of them belongs here."""
    found = suggest('SELECT * FROM ⌶', POSTGRES, postgres_catalog)
    assert 'reports_report' in found
    assert not [name for name in found if name.endswith('_id_seq')]


def test_postgres_writes_a_sequence_literal_the_server_accepts(postgres_catalog: DbapiCatalog) -> None:
    """
    The fact the whole literal half rests on: the string is parsed as a
    regclass, so a mixed-case name keeps its identifier quotes inside it.
    `nextval('billing.MonthlyTotals_id_seq')` is refused with
    `relation "billing.monthlytotals_id_seq" does not exist`.
    """
    psycopg2 = pytest.importorskip('psycopg2')
    # A *terminated* literal in a *closed* call, because the applied statement
    # has to be one the server can parse. `SELECT nextval('Month` would splice
    # correctly and still be missing its closing paren, and EXPLAIN would fail
    # for a reason that has nothing to do with the suggestion.
    sql = "SELECT nextval('Month')"
    caret = sql.index('Month') + len('Month')
    [found] = [s for s in complete(sql, caret, POSTGRES, postgres_catalog) if 'MonthlyTotals' in s.text]
    written = apply_suggestion(sql, found, dialect=POSTGRES)[0]
    assert written == 'SELECT nextval(\'billing."MonthlyTotals_id_seq"\')'
    with psycopg2.connect(POSTGRES_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(f'EXPLAIN {written}')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_golden_requests.py tests/integration/test_backends.py -v`
Expected: the golden cases pass already (Tasks 3 and 7 built them); the four
integration tests FAIL — no procedure is seeded, and the suite may skip if the
backends are down.

- [ ] **Step 3: Seed the procedures**

At the end of `docker/postgres/01-schema.sql`:

```sql
-- --------------------------------------------------------------------------- --
-- procedures
-- --------------------------------------------------------------------------- --
--
-- Stock PostgreSQL ships none: pg_proc holds 'f', 'a' and 'w' and no 'p' at
-- all. Without these, the completion tests for `CALL ` would be asserting
-- against an empty list and would pass however broken the feature was.
--
-- One per schema, so the schema-qualified position has something to find too.

CREATE PROCEDURE recalculate_totals(since date DEFAULT NULL)
LANGUAGE SQL AS $$ SELECT 1 $$;

CREATE PROCEDURE billing.close_period(period_name varchar)
LANGUAGE SQL AS $$ SELECT 1 $$;
```

- [ ] **Step 4: Rebuild the backend and run the integration suite**

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up --wait
uv run pytest tests/integration -v
```
Expected: PASS, including the four new tests. If they skip, the backends are not up.

- [ ] **Step 5: Record why the sweep cannot take either**

In `tests/integration/test_acceptance.py`, add `Kind.PROCEDURE` to `UNJUDGEABLE`
and extend both docstrings:

```python
UNJUDGEABLE = frozenset({Kind.FUNCTION, Kind.SNIPPET, Kind.PROCEDURE})
"""
Kinds whose insertion is deliberately unfinished.

A function arrives as `count()` with the caret between the parentheses, a
procedure as `proc()` the same way, and a template as a shape with blanks in it.
All are illegal SQL on purpose, and Postgres reports them the same way it
reports a genuinely misplaced token — so this harness cannot judge them and says
so rather than guessing.
"""
```

and in `misplaced`'s docstring, replace the second paragraph:

```python
    Only queries can be checked this way. The parse happens by prefixing
    `EXPLAIN`, and `EXPLAIN DROP TABLE t` is itself a syntax error — as are
    `EXPLAIN CALL p()` and `EXPLAIN EXPLAIN SELECT 1`. So `CORPUS` holds DML and
    nothing else, and the other statement forms are covered by
    `tests/test_statement_forms.py`, `tests/test_procedures.py` and
    `tests/test_sequences.py` instead.

    A caret inside a string literal is out of reach for a second reason:
    `carets()` stops at each end of each space and at the end of the statement,
    and a literal has neither. An entry for `nextval('…')` would be swept at
    three positions, none of them the one under test, and would pass while
    proving nothing — which is why the sequence-literal case is a direct test in
    `test_backends.py` instead.
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A tests docker
git commit -m "test: two servers find a procedure and refuse it an expression"
```

---

## Task 11: the documentation says what changed and what did not

**Files:**
- Modify: `docs/gaps.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Move gap 1 to the closed list**

In `docs/gaps.md`, delete the `## 1. Procedures and sequences` section,
renumber `## 2. CREATE TABLE` to `## 1.` and `## 3. History ranking` to `## 2.`,
fix the cross-reference inside the history-ranking section (it refers to itself
as "gap 3"), and add to the top of `## Closed since this list was written`:

```markdown
- **Procedures and sequences.** `CALL ⌶` offers procedures, `nextval('⌶` offers
  sequences, and `DROP SEQUENCE ⌶` and `ALTER SEQUENCE ⌶` offer them too. Both
  halves are one filter over two records — the catalog reports a subtype, and
  the position admits only some subtypes — which is why they were built
  together rather than in sequence.

  This entry called the sequence half "the cheaper" one and it was not. What it
  did not say is that `prokind IN ('f', 'a', 'w')` was already load-bearing: a
  procedure in an expression is refused by the server outright, so widening that
  filter without a matching one downstream would have traded a missing answer
  for a wrong one. Nor that stock Postgres ships **no procedures at all**, which
  makes `CALL ⌶` invisible until somebody writes one — the seed grew two so the
  integration tests assert against something.

  The identifier keeps its quotes inside the string:
  `nextval('billing."MonthlyTotals_id_seq"')` runs and the bare spelling is
  refused, because the server reads that literal as a `regclass` rather than as
  text.

  ClickHouse now says what it *lacks* for the first time — it has no `CALL`, and
  inheriting one from ANSI would have offered a word its parser rejects.
```

and add to the `## Already named elsewhere` list:

```markdown
- **Relation-kind filtering finer than one notch.** `DROP VIEW ⌶` and
  `DROP INDEX ⌶` still answer nothing. `Table.kind` already carries `view`,
  `materialized view` and `foreign table`, so the catalog half is done; what is
  undecided is the shape — a `Kind` per relation type, or a list of kinds on
  `Clause`. Two kinds is what there were users for, and choosing between those
  shapes with one hypothetical consumer is how a field gets designed wrong.
```

- [ ] **Step 2: Write the changelog entry**

In `CHANGELOG.md`, add a section under `## Unreleased`, above
`### Statements that are not queries`:

```markdown
### Procedures and sequences

`CALL ⌶` offers procedures. `SELECT ⌶` does not — a procedure in an expression
is refused by the server, so this is a wrong answer kept out rather than a
missing one added. `CALL billing.⌶` still means a procedure, where the namespace
rule would have answered with columns and tables.

`nextval('⌶`, `currval('⌶` and `setval('⌶` offer sequences, written into the
literal with their identifier quotes intact —
`nextval('billing."MonthlyTotals_id_seq"')`, because the server parses that
string as a `regclass` and refuses the bare spelling. Which functions name a
sequence is dialect data, so a dialect can declare its own.

`DROP SEQUENCE ⌶` and `ALTER SEQUENCE ⌶` offer sequences, and `DROP ⌶` now
answers `TABLE` and `SEQUENCE`.

**`SELECT ⌶` and `FROM ⌶` are unchanged**, which is the point of most of the
work: sequences reach the catalog now, and a schema has one per serial column.

`Function` carries a `kind` — function, aggregate, window or procedure — and a
`result` that may be `None`. ClickHouse used to report `count() -> aggregate`,
a kind in the return-type field for want of anywhere else; it now reports
`count()  aggregate` and no return type, which is the truth about what
`system.functions` knows. Postgres marks its aggregates and window functions
for the first time.

ClickHouse no longer offers `CALL`, which its parser rejects.
```

Then correct the entry above it. `### Statements that are not queries` lists
`CALL` among the forms that "answer with nothing", which is no longer true —
edit that sentence to drop `CALL` from the list and add a line saying where it
went:

```markdown
**Every other unrecognised form now answers with nothing.** `GRANT`, `VACUUM`,
`COMMENT`, `SET`, `BEGIN` and anything a third-party dialect has not modelled
are silent where they used to propose `SELECT`. (`CALL` was on this list and is
modelled now — see *Procedures and sequences* above.) A half-typed keyword is
not an unrecognised form: `SELEC⌶` still completes to `SELECT`, and so do an
empty editor, the position after a `;`, and the position after a comment.
```

and in the `DROP VIEW` paragraph of that same entry, replace the last sentence —
"filtering by relation kind needs a set of kinds per clause … That waits for a
change that wants it" — since the filter itself now exists:

```markdown
`DROP VIEW` and `DROP INDEX` are among the silent ones. Offering them relations
would mean offering tables for `DROP VIEW`, which the server refuses. Filtering
by relation kind exists now — it is what keeps sequences out of `FROM` — but
only one notch coarse: `Kind.TABLE` means "not a sequence". Telling a view from
a table needs either a kind per relation type or a list of kinds per clause, and
that choice waits for a second consumer.
```

- [ ] **Step 3: Verify the documents are consistent**

Run:
```bash
grep -n '^## ' docs/gaps.md
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```
Expected: gaps.md shows `## 1. CREATE TABLE`, `## 2. History ranking` and no
stale cross-reference to a gap number that no longer exists; the suite is green.

- [ ] **Step 4: Commit**

```bash
git add -A docs CHANGELOG.md
git commit -m "docs: a gap closed, and the two things the entry did not say"
```

---

## Self-review notes

**Spec coverage.** §3 → Task 1. §4 → Tasks 2, 5. §5 → Tasks 3, 4, 6. §6 →
Tasks 7, 8. §7 → Task 11's `gaps.md` entry. §8 unit → Tasks 2–8; conformance →
Task 9; golden corpus and integration → Task 10; LSP → Tasks 2 and 5. §9 →
Task 11.

**One spec item deliberately not implemented as written.** §8's "the acceptance
`CORPUS` gains the `nextval` case only" was corrected in the spec itself during
planning, twice: `EXPLAIN CALL` is a syntax error, and the sweep's caret
generator cannot reach inside a literal at all. Task 10 adds nothing to `CORPUS`
and records both reasons where the next person will meet them.

**Five defects the self-review caught, fixed above rather than left to
execution.**

1. `_NOT_A_RELATION` was defined in Task 3 as `{Kind.PROCEDURE}` because
   `Kind.SEQUENCE` did not exist yet, which would have left
   `DROP SEQUENCE billing.⌶` answering with tables. Task 6 Step 4 widens it.
2. `tests/test_statement_forms.py` asserts `offered('CALL ') == []`. That test
   goes red the moment Task 3 lands, so Task 3 retires the line itself.
3. `DialectConformance.check` compares against `s.text`, and a sequence written
   into a literal arrives as `'orders_id_seq'`. The literal-argument case could
   not have passed. Task 9 Step 5 strips the quotes.
4. The fixture function was named `total`, which is also an `orders` column — a
   `forbid` clause that the wrong thing could satisfy. Renamed `order_count`.
5. The integration test applied a suggestion into `SELECT nextval('Month`, whose
   call has no closing paren, so `EXPLAIN` would have failed for an unrelated
   reason. It now uses a terminated literal in a closed call and an explicit
   caret.

**Ordering constraints that matter.**
- Task 1 before Task 2: the filter reads `Function.kind`.
- Task 2 before Task 3: `Kind.PROCEDURE` must exist before a clause suggests it.
- Task 5 before Tasks 6–8: all three read `Table.kind == 'sequence'`.
- Task 7 before Task 8: `_sequences` reads `Request.writes_a_literal`.
- Task 9 after Task 8: the corpus asserts behaviour every earlier task builds.
- Tasks 2 and 5 each carry their own `ITEM_KINDS` entry, because the LSP guard
  fails the moment a `Kind` is added without one.
