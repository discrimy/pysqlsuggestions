# pysqlsuggestions Request Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `derive_request(sql, caret, dialect) -> Request` — a pure, dependency-free function that turns partially-typed SQL into a description of what should be suggested, correct for ANSI, PostgreSQL, ClickHouse and Trino.

**Architecture:** Three pure stages over a token stream. A tolerant lexer driven entirely by a `Syntax` record produces `Token`s carrying precomputed paren depth and case-folded values; four analysis functions (`statement_at`, `qualifier_and_prefix`, `clause_at`, `scope_of`) read that stream; `derive_request` combines them into a frozen `Request`. No I/O anywhere, no third-party imports, and a structural guard asserting the engine package never imports the I/O layer.

**Tech Stack:** Python 3.10+, stdlib only at runtime. uv for dependency management, pytest, ruff, mypy strict.

## Global Constraints

- Runtime dependencies: **zero**. `[project] dependencies = []` and it stays that way.
- `requires-python = ">=3.10"`. report_service pins `>=3.11,<3.12`, which sits inside that. Do not use 3.11+ syntax (`StrEnum`, `Self`, `except*`).
- Every module starts with `from __future__ import annotations`.
- ruff: `line-length = 120`, single quotes, `D` docstring rules enabled. mypy `strict = true`.
- Nothing under `src/pysqlsuggestions/engine/` may import `pysqlsuggestions.ports` or `pysqlsuggestions.resolve`. Enforced by a test.
- **No `dialect.name` comparisons outside `src/pysqlsuggestions/dialects/`.** If the engine needs to know something about a dialect, it becomes a field on a record.
- All public value types are `@dataclass(frozen=True, slots=True)`.
- Docstrings on every public module, class and function (ruff `D` will fail the build otherwise). `D100`, `D104`, `D105`, `D107`, `D203`, `D205`, `D212`, `D4` are ignored, matching report_service.
- Commit after every task. Conventional-commit prefixes (`feat:`, `test:`, `chore:`, `docs:`).

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Package metadata, zero deps, ruff/mypy/pytest config |
| `src/pysqlsuggestions/types.py` | Public value types: `Kind`, `Column`, `Table`, `Function`, `Projection`, `Relation`, `Scope`, `Request`, `Candidate`, `Suggestion` |
| `src/pysqlsuggestions/dialects/base.py` | Dialect record types: `Syntax`, `Namespace`, `Clause`, `ClauseModel`, `Query`, `CatalogQueries`, `Dialect` |
| `src/pysqlsuggestions/dialects/ansi.py` | The `ANSI` instance — baseline syntax, namespace, clause model, reserved words |
| `src/pysqlsuggestions/dialects/postgres.py` | `POSTGRES`, composed from `ANSI` with `replace()` |
| `src/pysqlsuggestions/dialects/clickhouse.py` | `CLICKHOUSE`, likewise |
| `src/pysqlsuggestions/dialects/trino.py` | `TRINO`, likewise |
| `src/pysqlsuggestions/engine/lex.py` | `TokenType`, `Token`, `lex()` — the only module that reads raw source text |
| `src/pysqlsuggestions/engine/analyse.py` | `statement_at`, `qualifier_and_prefix`, `in_literal`, `clause_at`, `scope_of` |
| `src/pysqlsuggestions/engine/request.py` | `derive_request()` — kind narrowing and assembly |
| `tests/corpus/cases.py` | `GoldenRequest` record and the translated corpus, caret marked with `⌶` |
| `tests/conftest.py` | Burn-down reporter for the xfail corpus |

Deliberately **not** in this plan: `ports.py`, `resolve.py`, `catalogs/`, `engine/local.py`, `engine/rank.py`, `api.py`, `testing/conformance.py`. Those are plan 2.

---

### Task 1: Project skeleton, tooling and purity guards

**Files:**
- Create: `pyproject.toml`, `README.md`, `.pre-commit-config.yaml`, `scripts/check.sh`, `.github/workflows/ci.yml`
- Create: `src/pysqlsuggestions/__init__.py`, `src/pysqlsuggestions/py.typed`
- Create: `src/pysqlsuggestions/engine/__init__.py`, `src/pysqlsuggestions/dialects/__init__.py`
- Test: `tests/test_purity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an installable package importable as `pysqlsuggestions`; the two purity guards every later task must keep green.

- [ ] **Step 1: Write the failing purity tests**

Create `tests/test_purity.py`:

```python
"""Structural guards: the core must stay dependency-free and the engine must stay pure."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / 'src' / 'pysqlsuggestions' / 'engine'
FORBIDDEN_FOR_ENGINE = {'pysqlsuggestions.ports', 'pysqlsuggestions.resolve'}
DRIVERS = {'psycopg2', 'psycopg', 'trino', 'clickhouse_connect', 'clickhouse_driver', 'sqlalchemy', 'sqlglot'}


def test_import_pulls_in_no_drivers() -> None:
    """Importing the package must not import any database driver."""
    code = 'import sys, pysqlsuggestions; print(" ".join(sorted(sys.modules)))'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    assert not (DRIVERS & loaded), f'drivers leaked into import: {sorted(DRIVERS & loaded)}'


def _imported_modules(path: Path) -> set[str]:
    """Fully-qualified module names imported by `path`, resolving relative imports."""
    package_parts = path.relative_to(ENGINE.parents[1]).with_suffix('').parts
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                names.add(node.module or '')
            else:
                base = package_parts[: len(package_parts) - node.level]
                names.add('.'.join((*base, node.module)) if node.module else '.'.join(base))
    return names


def test_engine_never_imports_the_io_layer() -> None:
    """Nothing under engine/ may import ports or resolve — purity is structural, not aspirational."""
    offenders = {
        str(path.relative_to(ENGINE.parent)): sorted(FORBIDDEN_FOR_ENGINE & _imported_modules(path))
        for path in sorted(ENGINE.rglob('*.py'))
        if FORBIDDEN_FOR_ENGINE & _imported_modules(path)
    }
    assert not offenders, f'engine imported the I/O layer: {offenders}'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_purity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pysqlsuggestions'` (nothing exists yet).

- [ ] **Step 3: Create the package skeleton**

`src/pysqlsuggestions/__init__.py`:

```python
"""Context-aware, schema-aware SQL completion as an embeddable library."""

from __future__ import annotations

__version__ = '0.1.0.dev0'

__all__ = ['__version__']
```

`src/pysqlsuggestions/engine/__init__.py`:

```python
"""Pure stages: lex, analyse, request. Nothing here performs I/O."""

from __future__ import annotations
```

`src/pysqlsuggestions/dialects/__init__.py`:

```python
"""Dialects are data composed with dataclasses.replace, never subclassed."""

from __future__ import annotations
```

Create an empty `src/pysqlsuggestions/py.typed` (zero bytes).

- [ ] **Step 4: Write pyproject.toml**

```toml
[project]
name = 'pysqlsuggestions'
version = '0.1.0.dev0'
description = 'Context-aware, schema-aware SQL completion as a library'
readme = 'README.md'
requires-python = '>=3.10'
dependencies = []
license = { text = 'MIT' }
classifiers = [
    'Development Status :: 3 - Alpha',
    'Intended Audience :: Developers',
    'License :: OSI Approved :: MIT License',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 3.10',
    'Programming Language :: Python :: 3.11',
    'Programming Language :: Python :: 3.12',
    'Topic :: Database',
    'Topic :: Software Development :: Libraries :: Python Modules',
    'Typing :: Typed',
]

[build-system]
requires = ['hatchling']
build-backend = 'hatchling.build'

[tool.hatch.build.targets.wheel]
packages = ['src/pysqlsuggestions']

[dependency-groups]
dev = [
    'pytest>=8.4',
    'pytest-cov>=7.0',
    'ruff>=0.14',
    'mypy>=1.7',
    'pre-commit>=3.5',
]

[tool.ruff]
line-length = 120
src = ['src', 'tests']

[tool.ruff.lint]
select = ['A', 'B', 'BLE', 'C4', 'COM', 'D', 'E', 'F', 'G', 'I', 'PT', 'Q', 'RET', 'RSE', 'SIM', 'T20', 'UP', 'W']
ignore = ['A003', 'COM812', 'D100', 'D104', 'D105', 'D106', 'D107', 'D203', 'D205', 'D212', 'D4', 'PT009', 'RET504']

[tool.ruff.lint.flake8-quotes]
inline-quotes = 'single'

[tool.ruff.lint.flake8-pytest-style]
fixture-parentheses = false

[tool.ruff.format]
quote-style = 'single'

[tool.mypy]
strict = true
files = ['src', 'tests']

[tool.pytest.ini_options]
testpaths = ['tests']
```

- [ ] **Step 5: Write README.md**

```markdown
# pysqlsuggestions

Context-aware, schema-aware SQL completion for Python. A library, not a CLI and
not a language server — importable into a FastAPI service, a notebook kernel or
an internal reporting tool without dragging a process boundary along.

Zero runtime dependencies. Three backends: PostgreSQL, ClickHouse, Trino.

Status: in development. See `docs/superpowers/specs/` for the design.
```

- [ ] **Step 6: Write scripts/check.sh and CI**

`scripts/check.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest -q
```

Make it executable: `chmod +x scripts/check.sh`.

`.github/workflows/ci.yml`:

```yaml
name: ci

on: [push, pull_request]

jobs:
  check:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv python install ${{ matrix.python-version }}
      - run: uv sync
      - run: ./scripts/check.sh

  no-extras:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'
      - run: pip install .
      - run: python -c "import pysqlsuggestions; print(pysqlsuggestions.__version__)"
```

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.3
    hooks:
      - id: ruff-format
      - id: ruff
        args: [--fix]
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv sync && uv run pytest tests/test_purity.py -v`
Expected: PASS, 2 tests. (`engine/` contains only `__init__.py`, so the AST scan trivially finds no offenders — it becomes load-bearing from Task 4 onward.)

Run: `./scripts/check.sh`
Expected: all four commands exit 0.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: project skeleton, tooling and purity guards"
```

---

### Task 2: Core value types

**Files:**
- Create: `src/pysqlsuggestions/types.py`
- Test: `tests/test_types.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Kind`, `Column`, `Table`, `Function`, `Projection`, `Relation`, `Scope`, `Request`, `Candidate`, `Suggestion`. Every later task imports from here. Exact signatures are in Step 3 — treat them as fixed.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_types.py`:

```python
"""The value types, with emphasis on the two decisions that are easy to regress."""

from __future__ import annotations

import dataclasses

import pytest

from pysqlsuggestions.types import Kind, Projection, Relation, Request, Scope


def test_kind_values_are_json_ready_strings() -> None:
    """Consumers serialise `kind` straight into JSON; auto() integers would be meaningless."""
    assert [k.value for k in Kind] == ['column', 'table', 'schema', 'function', 'alias', 'keyword']


def test_request_defaults() -> None:
    """Only kinds, prefix and replace_span are required."""
    request = Request(kinds=(Kind.COLUMN,), prefix='na', replace_span=(11, 13))
    assert request.qualifier == ()
    assert request.clause is None
    assert request.scope is None


def test_request_is_immutable() -> None:
    """Request is a value; nothing downstream may mutate it."""
    request = Request(kinds=(), prefix='', replace_span=(0, 0))
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.prefix = 'x'  # type: ignore[misc]


def test_relation_has_three_projection_states() -> None:
    """A catalog object, a self-described relation, and one needing star expansion."""
    users = Relation(alias='u', path=('users',), source='table')
    assert users.projection is None

    named = Relation(alias='r', path=('recent',), source='cte', projection=Projection(columns=('id', 'total')))
    assert named.projection is not None
    assert named.projection.stars == ()

    starred = Relation(alias='a', path=('a',), source='cte', projection=Projection(stars=(users,)))
    assert starred.projection is not None
    assert starred.projection.columns == ()
    assert starred.projection.stars == (users,)


def test_scope_nests() -> None:
    """Subqueries see their parent's relations."""
    outer = Scope(relations=(Relation(alias='o', path=('orders',), source='table'),))
    inner = Scope(relations=(), parent=outer)
    assert inner.parent is outer
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pysqlsuggestions.types'`.

- [ ] **Step 3: Write types.py**

```python
"""Public value types. Everything here is a frozen dataclass or an enum."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Mapping


class Kind(Enum):
    """What a suggestion is.

    Values are explicit strings rather than auto() because consumers serialise
    them straight into JSON payloads for an editor.
    """

    COLUMN = 'column'
    TABLE = 'table'
    SCHEMA = 'schema'
    FUNCTION = 'function'
    ALIAS = 'alias'
    KEYWORD = 'keyword'


@dataclass(frozen=True, slots=True)
class Column:
    """A column as the catalog reports it."""

    schema: str
    table: str
    name: str
    type: str
    position: int = 0
    """attnum / ordinal_position. Declaration order outranks alphabetical when ranking."""


@dataclass(frozen=True, slots=True)
class Table:
    """A relation as the catalog reports it."""

    schema: str
    name: str
    kind: str = 'table'
    """Normalised by the dialect row mappers: table, view, materialized view, foreign table..."""


@dataclass(frozen=True, slots=True)
class Function:
    """A function, aggregate or window function as the catalog reports it."""

    schema: str | None
    name: str
    args: str
    result: str


@dataclass(frozen=True, slots=True)
class Projection:
    """The output columns of a relation the statement defines itself.

    `stars` holds relations that a bare `*` or `t.*` referred to; they cannot be
    expanded without the catalog, so resolve finishes the job. A projection with
    empty `stars` needs no catalog call at all.
    """

    columns: tuple[str, ...] = ()
    stars: tuple[Relation, ...] = ()


@dataclass(frozen=True, slots=True)
class Relation:
    """A relation referenced by the statement.

    `projection is None` means the relation lives in the catalog. Otherwise the
    statement described it: a CTE, a derived table, or a VALUES list.
    """

    alias: str | None
    path: tuple[str, ...]
    source: Literal['table', 'cte', 'subquery']
    projection: Projection | None = None

    @property
    def label(self) -> str:
        """The name this relation answers to: its alias, else the last path segment."""
        return self.alias or (self.path[-1] if self.path else '')


@dataclass(frozen=True, slots=True)
class Scope:
    """The relations visible at one point in a statement."""

    relations: tuple[Relation, ...] = ()
    ctes: Mapping[str, Relation] = field(default_factory=dict)
    parent: Scope | None = None

    def visible(self) -> tuple[Relation, ...]:
        """This scope's relations plus every enclosing scope's, innermost first."""
        return self.relations + (self.parent.visible() if self.parent else ())


@dataclass(frozen=True, slots=True)
class Request:
    """What the engine decided should be suggested, before anything is fetched.

    This is the seam: everything upstream is pure text analysis, everything
    downstream is catalog access and ranking.
    """

    kinds: tuple[Kind, ...]
    """Most relevant first; rank consumes this order."""
    prefix: str
    """Already typed, unquoted and case-folded."""
    replace_span: tuple[int, int]
    """(start of prefix, caret). What the editor overwrites."""
    qualifier: tuple[str, ...] = ()
    """Segments left of the last dot, unquoted and case-folded."""
    clause: str | None = None
    """Nearest clause keyword, uppercased."""
    scope: Scope | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """A pre-ranking suggestion. No score, no span."""

    text: str
    kind: Kind
    detail: str | None = None
    position: int = 0
    origin: str = 'catalog'
    """catalog | local | keyword. Ranking treats locally derived candidates differently."""


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A ranked suggestion, ready for an editor."""

    text: str
    kind: Kind
    replace_span: tuple[int, int]
    score: float
    detail: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_types.py -v`
Expected: PASS, 5 tests.

Run: `uv run mypy`
Expected: `Success: no issues found`.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/types.py tests/test_types.py
git commit -m "feat: core value types with three-state Projection"
```

---

### Task 3: Dialect record types and the ANSI baseline

**Files:**
- Create: `src/pysqlsuggestions/dialects/base.py`, `src/pysqlsuggestions/dialects/ansi.py`
- Test: `tests/test_dialect_records.py`

**Interfaces:**
- Consumes: `pysqlsuggestions.types.Kind`.
- Produces: `Syntax`, `Namespace`, `Clause`, `ClauseModel`, `Query`, `CatalogQueries`, `Dialect` from `dialects.base`; the `ANSI` instance from `dialects.ansi`. `ClauseModel.extend(*clauses) -> ClauseModel` and `ClauseModel.get(name) -> Clause | None` are the two methods later tasks call.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dialect_records.py`:

```python
"""Dialects are composed data. These tests pin the composition mechanics."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Clause, ClauseModel, Namespace, Syntax
from pysqlsuggestions.types import Kind


def test_extend_appends_without_mutating() -> None:
    """A dialect adding a clause must not disturb the model it extended."""
    base = ClauseModel(clauses=(Clause(name='WHERE', suggests=(Kind.COLUMN,)),))
    extended = base.extend(Clause(name='PREWHERE', suggests=(Kind.COLUMN,)))
    assert [c.name for c in base.clauses] == ['WHERE']
    assert [c.name for c in extended.clauses] == ['WHERE', 'PREWHERE']


def test_get_finds_by_name() -> None:
    """Lookup is by exact uppercased name."""
    model = ClauseModel(clauses=(Clause(name='GROUP BY', suggests=(Kind.COLUMN,)),))
    found = model.get('GROUP BY')
    assert found is not None
    assert found.suggests == (Kind.COLUMN,)
    assert model.get('ORDER BY') is None


def test_names_are_sorted_longest_first() -> None:
    """clause_at matches greedily, so multi-word names must be tried before their prefixes."""
    model = ClauseModel(clauses=(Clause(name='BY'), Clause(name='GROUP BY'), Clause(name='ORDER BY')))
    assert model.names()[0] in {'GROUP BY', 'ORDER BY'}
    assert model.names()[-1] == 'BY'


def test_replace_composes_a_variant() -> None:
    """The documented way to build a dialect: replace fields, never subclass."""
    variant = replace(
        ANSI,
        name='clickhouse',
        syntax=replace(ANSI.syntax, identifier_quotes=('"', '`'), unquoted_case='preserve'),
        namespace=Namespace(levels=('database', 'table')),
    )
    assert variant.name == 'clickhouse'
    assert variant.syntax.identifier_quotes == ('"', '`')
    assert ANSI.syntax.identifier_quotes == ('"',)
    assert ANSI.namespace.levels == ('schema', 'table')


def test_ansi_defaults() -> None:
    """The fallback dialect must be conservative: no dollar quoting, no :: cast."""
    assert ANSI.name == 'ansi'
    assert ANSI.syntax == Syntax()
    assert ANSI.syntax.dollar_quoting is False
    assert ANSI.syntax.cast_operator is None
    assert 'select' in ANSI.reserved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dialect_records.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pysqlsuggestions.dialects.base'`.

- [ ] **Step 3: Write dialects/base.py**

```python
"""The record types a dialect is made of.

A dialect is data you compose with dataclasses.replace, not a class you
subclass — ClickHouse and Trino each share different subsets with ANSI, a shape
no MRO expresses. Instances live in the sibling modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from pysqlsuggestions.types import Kind


@dataclass(frozen=True, slots=True)
class Syntax:
    """Everything the lexer needs. No other stage reads this record."""

    identifier_quotes: tuple[str, ...] = ('"',)
    line_comments: tuple[str, ...] = ('--',)
    nested_block_comments: bool = False
    """Postgres nests /* /* */ */. ClickHouse and Trino stop at the first close."""
    string_escape_backslash: bool = False
    r"""ClickHouse honours \' inside literals; Postgres with standard_conforming_strings does not."""
    unquoted_case: Literal['lower', 'upper', 'preserve'] = 'lower'
    dollar_quoting: bool = False
    cast_operator: str | None = None


@dataclass(frozen=True, slots=True)
class Namespace:
    """How many levels a dotted path has, and what each level means."""

    levels: tuple[str, ...] = ('schema', 'table')

    def level_of(self, segments: int) -> str | None:
        """What a qualifier of `segments` parts names, or None if it is too deep."""
        return self.levels[segments - 1] if 0 < segments <= len(self.levels) else None


@dataclass(frozen=True, slots=True)
class Clause:
    """One clause keyword and what it implies."""

    name: str
    """Uppercased. May contain single spaces: 'GROUP BY', 'ARRAY JOIN'."""
    follows: frozenset[str] = frozenset()
    """Clauses this one may appear after. Empty means unconstrained."""
    suggests: tuple[Kind, ...] = ()
    """Most relevant first."""


@dataclass(frozen=True, slots=True)
class ClauseModel:
    """The clause vocabulary of a dialect."""

    clauses: tuple[Clause, ...] = ()

    def extend(self, *clauses: Clause) -> ClauseModel:
        """A new model with `clauses` appended. The receiver is untouched."""
        return ClauseModel(clauses=self.clauses + clauses)

    def get(self, name: str) -> Clause | None:
        """The clause called `name`, or None. Linear scan over a few dozen entries."""
        for clause in self.clauses:
            if clause.name == name:
                return clause
        return None

    def names(self) -> tuple[str, ...]:
        """Clause names ordered longest first, so greedy matching tries 'GROUP BY' before 'BY'."""
        return tuple(sorted((c.name for c in self.clauses), key=lambda n: (-len(n.split()), -len(n), n)))


@dataclass(frozen=True, slots=True)
class Query:
    """Introspection SQL as data.

    Placeholders are neutral $1, $2 markers; the DB-API catalog rewrites them
    for whatever paramstyle the driver reports.
    """

    sql: str
    row: Callable[[tuple[object, ...]], object]


@dataclass(frozen=True, slots=True)
class CatalogQueries:
    """The introspection queries a dialect provides. Populated in a later plan."""

    schemas: Query | None = None
    tables: Query | None = None
    columns: Query | None = None
    functions: Query | None = None


@dataclass(frozen=True, slots=True)
class Dialect:
    """A backend, as data."""

    name: str
    syntax: Syntax = field(default_factory=Syntax)
    namespace: Namespace = field(default_factory=Namespace)
    clauses: ClauseModel = field(default_factory=ClauseModel)
    keywords: frozenset[str] = frozenset()
    """Offered as completions. Ideally introspected; the static set is the offline fallback."""
    reserved: frozenset[str] = frozenset()
    """Lowercased. Drives quoting decisions, which must be made before any connection exists."""
    catalog_queries: CatalogQueries = field(default_factory=CatalogQueries)
```

- [ ] **Step 4: Write dialects/ansi.py**

```python
"""The ANSI baseline. An unknown backend degrades to this rather than failing."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import ClauseModel, Dialect, Namespace, Syntax

RESERVED = frozenset(
    {
        'all', 'and', 'any', 'array', 'as', 'asc', 'between', 'by', 'case', 'cast', 'check', 'column',
        'constraint', 'create', 'cross', 'current_date', 'current_time', 'current_timestamp', 'default',
        'desc', 'distinct', 'do', 'else', 'end', 'except', 'exists', 'false', 'for', 'foreign', 'from',
        'full', 'grant', 'group', 'having', 'in', 'inner', 'intersect', 'into', 'is', 'join', 'left',
        'like', 'limit', 'natural', 'not', 'null', 'offset', 'on', 'only', 'or', 'order', 'outer',
        'primary', 'references', 'right', 'select', 'some', 'table', 'then', 'to', 'true', 'union',
        'unique', 'user', 'using', 'values', 'when', 'where', 'window', 'with',
    },
)

ANSI = Dialect(
    name='ansi',
    syntax=Syntax(),
    namespace=Namespace(levels=('schema', 'table')),
    clauses=ClauseModel(),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
)
```

The clause model is filled in Task 9; an empty `ClauseModel()` is correct until then, not a placeholder — nothing reads it yet.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_dialect_records.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add src/pysqlsuggestions/dialects tests/test_dialect_records.py
git commit -m "feat: dialect record types and the ANSI baseline"
```

---

### Task 4: Lexer — identifiers, numbers, operators, depth

**Files:**
- Create: `src/pysqlsuggestions/engine/lex.py`
- Test: `tests/test_lex_core.py`

**Interfaces:**
- Consumes: `dialects.base.Syntax`.
- Produces: `TokenType`, `Token`, and `lex(src: str, syntax: Syntax) -> tuple[Token, ...]`. Every analysis function in Tasks 8–14 consumes `tuple[Token, ...]`.

Strings, comments and dollar-quoting arrive in Task 5. This task establishes the scanner loop and `depth`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lex_core.py`:

```python
"""Lexer: identifiers, numbers, operators, punctuation, whitespace, paren depth."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Syntax
from pysqlsuggestions.engine.lex import Token, TokenType, lex


def significant(src: str, syntax: Syntax | None = None) -> list[Token]:
    """Tokens with whitespace dropped — the shape every analysis function works with."""
    return [t for t in lex(src, syntax or Syntax()) if t.type is not TokenType.WHITESPACE]


def test_spans_cover_the_source_exactly() -> None:
    """Every character belongs to exactly one token, in order. replace_span depends on this."""
    src = 'SELECT a, b FROM t'
    tokens = lex(src, Syntax())
    assert ''.join(t.text for t in tokens) == src
    assert [t.start for t in tokens] == [0] + [t.end for t in tokens][:-1]


def test_identifiers_and_punctuation() -> None:
    """A dotted reference is three tokens, not one."""
    tokens = significant('users.id')
    assert [(t.type, t.value) for t in tokens] == [
        (TokenType.IDENT, 'users'),
        (TokenType.PUNCT, '.'),
        (TokenType.IDENT, 'id'),
    ]


def test_unquoted_identifiers_fold_to_lower_by_default() -> None:
    """value is folded; text keeps the source slice so offsets stay exact."""
    token = significant('SELECT')[0]
    assert token.value == 'select'
    assert token.text == 'SELECT'


def test_quoted_identifiers_preserve_case_and_strip_quotes() -> None:
    """A quoted identifier is one token whose value is the unquoted content."""
    token = significant('"Mixed Case"')[0]
    assert token.type is TokenType.IDENT
    assert token.value == 'Mixed Case'
    assert token.quoted is True
    assert token.text == '"Mixed Case"'


def test_doubled_quote_is_an_escape() -> None:
    """'\"a\"\"b\"' is one identifier containing a quote character."""
    token = significant('"a""b"')[0]
    assert token.value == 'a"b'


def test_numbers() -> None:
    """Integers, decimals and exponents are single NUMBER tokens."""
    assert [t.value for t in significant('1 1.5 1e10 2.5E-3')] == ['1', '1.5', '1e10', '2.5E-3']


def test_multi_character_operators_win() -> None:
    """<= is one token, not < followed by =."""
    assert [t.text for t in significant('a <= b <> c || d')] == ['a', '<=', 'b', '<>', 'c', '||', 'd']


def test_cast_operator_is_lexed_when_the_dialect_has_one() -> None:
    """:: is an operator for Postgres-like dialects and two unknowns for strict ANSI."""
    with_cast = significant('a::int', Syntax(cast_operator='::'))
    assert [t.text for t in with_cast] == ['a', '::', 'int']
    assert with_cast[1].type is TokenType.OPERATOR

    without = significant('a::int', Syntax(cast_operator=None))
    assert [t.text for t in without] == ['a', ':', ':', 'int']


def test_depth_is_precomputed() -> None:
    """Parens carry the outer depth; their contents carry the inner one."""
    tokens = significant('SELECT (a + b) FROM t')
    assert [(t.text, t.depth) for t in tokens] == [
        ('SELECT', 0), ('(', 0), ('a', 1), ('+', 1), ('b', 1), (')', 0), ('FROM', 0), ('t', 0),
    ]


def test_unbalanced_close_paren_does_not_go_negative() -> None:
    """Completion runs on broken input by definition; depth must stay sane."""
    assert [t.depth for t in significant('a) b')] == [0, 0, 0]


def test_empty_source() -> None:
    """Lexing nothing yields nothing."""
    assert lex('', Syntax()) == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lex_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pysqlsuggestions.engine.lex'`.

- [ ] **Step 3: Write engine/lex.py**

```python
"""A tolerant, dialect-driven tokenizer.

This is the only module that reads raw source text. It never raises: an
unterminated string, quote or comment yields a token running to end of input
with `terminated` false, because completion works on invalid input by
definition.

It deliberately does not classify keywords. Every word is an IDENT; analyse
consults the dialect's vocabulary. That keeps this module dependent on dialect
*syntax* only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pysqlsuggestions.dialects.base import Syntax

_OPERATOR_CHARS = frozenset('+-*/%=<>|&^~!@#?:')
_MULTI_CHAR_OPERATORS = ('<=', '>=', '<>', '!=', '||', '->>', '->', '#>>', '#>')
_PUNCTUATION = frozenset('.,();[]')


class TokenType(Enum):
    """The token categories analyse distinguishes."""

    IDENT = 'ident'
    NUMBER = 'number'
    STRING = 'string'
    COMMENT = 'comment'
    OPERATOR = 'operator'
    PUNCT = 'punct'
    WHITESPACE = 'whitespace'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class Token:
    """One lexical unit, located in the source."""

    type: TokenType
    start: int
    end: int
    text: str
    """The raw source slice. sum(len(text)) equals len(src)."""
    value: str = ''
    """For IDENT: unquoted and case-folded. For others: the raw text."""
    quoted: bool = False
    terminated: bool = True
    """False when the token ran to end of input looking for its closing delimiter."""
    depth: int = 0
    """Paren nesting. An open paren carries the outer depth, its contents the inner."""

    def covers(self, caret: int) -> bool:
        """Whether `caret` sits inside this token (exclusive of the very start)."""
        return self.start < caret <= self.end


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == '_'


def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch in '_$'


def _fold(value: str, syntax: Syntax) -> str:
    if syntax.unquoted_case == 'lower':
        return value.lower()
    if syntax.unquoted_case == 'upper':
        return value.upper()
    return value


def _scan_quoted_ident(src: str, pos: int, quote: str) -> tuple[int, str, bool]:
    """Scan from the opening quote. Returns (end, unquoted value, terminated)."""
    i, out = pos + 1, []
    while i < len(src):
        if src[i] == quote:
            if i + 1 < len(src) and src[i + 1] == quote:
                out.append(quote)
                i += 2
                continue
            return i + 1, ''.join(out), True
        out.append(src[i])
        i += 1
    return len(src), ''.join(out), False


def _scan_number(src: str, pos: int) -> int:
    i = pos
    while i < len(src) and (src[i].isdigit() or src[i] == '.'):
        i += 1
    if i < len(src) and src[i] in 'eE':
        j = i + 1
        if j < len(src) and src[j] in '+-':
            j += 1
        if j < len(src) and src[j].isdigit():
            i = j
            while i < len(src) and src[i].isdigit():
                i += 1
    return i


def _match_operator(src: str, pos: int, syntax: Syntax) -> str | None:
    """The longest operator starting at `pos`, or None."""
    candidates = _MULTI_CHAR_OPERATORS
    if syntax.cast_operator:
        candidates = (syntax.cast_operator, *candidates)
    for op in sorted(candidates, key=len, reverse=True):
        if src.startswith(op, pos):
            return op
    return src[pos] if src[pos] in _OPERATOR_CHARS else None


def lex(src: str, syntax: Syntax) -> tuple[Token, ...]:
    """Tokenize `src`. Total, never raises, and preserves every offset."""
    tokens: list[Token] = []
    pos, depth, length = 0, 0, len(src)

    while pos < length:
        ch = src[pos]

        if ch.isspace():
            end = pos
            while end < length and src[end].isspace():
                end += 1
            tokens.append(Token(TokenType.WHITESPACE, pos, end, src[pos:end], src[pos:end], depth=depth))
            pos = end
            continue

        if ch in syntax.identifier_quotes:
            end, value, terminated = _scan_quoted_ident(src, pos, ch)
            token = Token(TokenType.IDENT, pos, end, src[pos:end], value, quoted=True, terminated=terminated,
                          depth=depth)
            tokens.append(token)
            pos = end
            continue

        if _is_ident_start(ch):
            end = pos
            while end < length and _is_ident_char(src[end]):
                end += 1
            raw = src[pos:end]
            tokens.append(Token(TokenType.IDENT, pos, end, raw, _fold(raw, syntax), depth=depth))
            pos = end
            continue

        if ch.isdigit():
            end = _scan_number(src, pos)
            tokens.append(Token(TokenType.NUMBER, pos, end, src[pos:end], src[pos:end], depth=depth))
            pos = end
            continue

        if ch in _PUNCTUATION:
            if ch == '(':
                tokens.append(Token(TokenType.PUNCT, pos, pos + 1, ch, ch, depth=depth))
                depth += 1
            elif ch == ')':
                depth = max(0, depth - 1)
                tokens.append(Token(TokenType.PUNCT, pos, pos + 1, ch, ch, depth=depth))
            else:
                tokens.append(Token(TokenType.PUNCT, pos, pos + 1, ch, ch, depth=depth))
            pos += 1
            continue

        operator = _match_operator(src, pos, syntax)
        if operator is not None:
            end = pos + len(operator)
            tokens.append(Token(TokenType.OPERATOR, pos, end, operator, operator, depth=depth))
            pos = end
            continue

        tokens.append(Token(TokenType.UNKNOWN, pos, pos + 1, ch, ch, depth=depth))
        pos += 1

    return tuple(tokens)
```

Note on `_match_operator`: `:` is in `_OPERATOR_CHARS`, so with `cast_operator=None` the string `a::int` yields two single-character `:` operator tokens — matching `test_cast_operator_is_lexed_when_the_dialect_has_one`, which expects `[':', ':']` as separate tokens.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lex_core.py -v`
Expected: PASS, 11 tests.

Run: `uv run pytest tests/test_purity.py -v`
Expected: PASS — the AST guard is now load-bearing, since `engine/lex.py` exists and imports only `dialects.base`.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/engine/lex.py tests/test_lex_core.py
git commit -m "feat: tolerant lexer core with precomputed paren depth"
```

---

### Task 5: Lexer — strings, comments, dollar quoting, tolerance

**Files:**
- Modify: `src/pysqlsuggestions/engine/lex.py`
- Test: `tests/test_lex_literals.py`

**Interfaces:**
- Consumes: `Token`, `TokenType`, `lex` from Task 4.
- Produces: no new names. `lex()` gains STRING and COMMENT tokens and the `terminated=False` behaviour that Task 14 relies on to suppress suggestions inside a literal.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lex_literals.py`:

```python
"""Lexer: string literals, comments, dollar quoting, and tolerance of unterminated input."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Syntax
from pysqlsuggestions.engine.lex import TokenType, lex

PG = Syntax(dollar_quoting=True, nested_block_comments=True, cast_operator='::')
CH = Syntax(line_comments=('--', '#'), string_escape_backslash=True)


def only(src: str, syntax: Syntax) -> list[tuple[TokenType, str, bool]]:
    """(type, text, terminated) for every non-whitespace token."""
    return [(t.type, t.text, t.terminated) for t in lex(src, syntax) if t.type is not TokenType.WHITESPACE]


def test_string_literal_is_one_token() -> None:
    """Contents never leak into parsing — LIKE '%smith%' must not produce operators."""
    assert only("LIKE '%smith%'", Syntax()) == [
        (TokenType.IDENT, 'LIKE', True),
        (TokenType.STRING, "'%smith%'", True),
    ]


def test_doubled_quote_inside_a_string() -> None:
    """'it''s' is a single literal."""
    assert only("'it''s'", Syntax()) == [(TokenType.STRING, "'it''s'", True)]


def test_unterminated_string_runs_to_end_of_input() -> None:
    """The caret sitting inside an open literal is the case that must not crash."""
    tokens = only("WHERE name = 'ab", Syntax())
    assert tokens[-1] == (TokenType.STRING, "'ab", False)


def test_backslash_escape_only_when_the_dialect_says_so() -> None:
    r"""ClickHouse honours \'; Postgres with standard_conforming_strings does not."""
    assert only(r"'a\'b'", CH) == [(TokenType.STRING, r"'a\'b'", True)]
    postgres = only(r"'a\'b'", PG)
    assert postgres[0] == (TokenType.STRING, r"'a\'", True)


def test_line_comment() -> None:
    """A line comment ends at the newline, which stays whitespace."""
    assert only('-- hi\nSELECT', Syntax()) == [
        (TokenType.COMMENT, '-- hi', True),
        (TokenType.IDENT, 'SELECT', True),
    ]


def test_clickhouse_hash_comment() -> None:
    """ClickHouse adds # as a line comment marker; other dialects treat it as an operator."""
    assert only('# hi\nSELECT', CH)[0][0] is TokenType.COMMENT
    assert only('# hi\nSELECT', Syntax())[0][0] is TokenType.OPERATOR


def test_block_comment() -> None:
    """/* */ spans newlines."""
    assert only('/* a\nb */ SELECT', Syntax())[0] == (TokenType.COMMENT, '/* a\nb */', True)


def test_nested_block_comments_only_where_supported() -> None:
    """Postgres nests; ANSI stops at the first close."""
    src = '/* a /* b */ c */ SELECT'
    assert only(src, PG)[0] == (TokenType.COMMENT, '/* a /* b */ c */', True)
    assert only(src, Syntax())[0] == (TokenType.COMMENT, '/* a /* b */', True)


def test_unterminated_block_comment() -> None:
    """Runs to end of input rather than raising."""
    assert only('/* open', Syntax()) == [(TokenType.COMMENT, '/* open', False)]


def test_dollar_quoting() -> None:
    """$$ and $tag$ bodies are opaque string tokens where the dialect allows them."""
    assert only('$$ any ' + "'" + ' text $$', PG)[0][0] is TokenType.STRING
    assert only('$fn$ body $fn$', PG) == [(TokenType.STRING, '$fn$ body $fn$', True)]
    assert only('$fn$ body', PG) == [(TokenType.STRING, '$fn$ body', False)]


def test_dollar_quoting_off_by_default() -> None:
    """Without the flag, $ is not a literal delimiter."""
    assert only('$$ x $$', Syntax())[0][0] is not TokenType.STRING


def test_unterminated_quoted_identifier() -> None:
    """Same tolerance as strings."""
    assert only('SELECT "unclosed', Syntax())[-1] == (TokenType.IDENT, '"unclosed', False)


def test_depth_ignores_parens_inside_literals() -> None:
    """A paren in a string must not shift depth for the rest of the statement."""
    tokens = [t for t in lex("SELECT '(' , a FROM t", Syntax()) if t.type is not TokenType.WHITESPACE]
    assert all(t.depth == 0 for t in tokens)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lex_literals.py -v`
Expected: FAIL — string and comment characters currently emit OPERATOR/UNKNOWN tokens.

- [ ] **Step 3: Extend engine/lex.py**

Add these scanners above `lex()`:

```python
def _scan_string(src: str, pos: int, syntax: Syntax) -> tuple[int, bool]:
    """Scan a single-quoted literal from its opening quote. Returns (end, terminated)."""
    i = pos + 1
    while i < len(src):
        ch = src[i]
        if ch == '\\' and syntax.string_escape_backslash:
            i += 2
            continue
        if ch == "'":
            if i + 1 < len(src) and src[i + 1] == "'":
                i += 2
                continue
            return i + 1, True
        i += 1
    return len(src), False


def _scan_line_comment(src: str, pos: int) -> int:
    end = src.find('\n', pos)
    return len(src) if end == -1 else end


def _scan_block_comment(src: str, pos: int, syntax: Syntax) -> tuple[int, bool]:
    """Scan from '/*'. Returns (end, terminated), honouring nesting when supported."""
    i, level = pos + 2, 1
    while i < len(src):
        if syntax.nested_block_comments and src.startswith('/*', i):
            level += 1
            i += 2
            continue
        if src.startswith('*/', i):
            level -= 1
            i += 2
            if level == 0:
                return i, True
            continue
        i += 1
    return len(src), False


def _scan_dollar_quote(src: str, pos: int) -> tuple[int, bool] | None:
    """Scan a $tag$...$tag$ literal. Returns None when `pos` does not open one."""
    close = src.find('$', pos + 1)
    if close == -1:
        return None
    tag = src[pos : close + 1]
    if not all(_is_ident_char(c) for c in tag[1:-1]):
        return None
    end = src.find(tag, close + 1)
    return (len(src), False) if end == -1 else (end + len(tag), True)
```

Then insert these branches inside the `while pos < length` loop of `lex()`, immediately after the whitespace branch and **before** the identifier-quote branch:

```python
        comment_marker = next((m for m in syntax.line_comments if src.startswith(m, pos)), None)
        if comment_marker is not None:
            end = _scan_line_comment(src, pos)
            tokens.append(Token(TokenType.COMMENT, pos, end, src[pos:end], src[pos:end], depth=depth))
            pos = end
            continue

        if src.startswith('/*', pos):
            end, terminated = _scan_block_comment(src, pos, syntax)
            tokens.append(Token(TokenType.COMMENT, pos, end, src[pos:end], src[pos:end],
                                terminated=terminated, depth=depth))
            pos = end
            continue

        if ch == "'":
            end, terminated = _scan_string(src, pos, syntax)
            tokens.append(Token(TokenType.STRING, pos, end, src[pos:end], src[pos:end],
                                terminated=terminated, depth=depth))
            pos = end
            continue

        if ch == '$' and syntax.dollar_quoting:
            scanned = _scan_dollar_quote(src, pos)
            if scanned is not None:
                end, terminated = scanned
                tokens.append(Token(TokenType.STRING, pos, end, src[pos:end], src[pos:end],
                                    terminated=terminated, depth=depth))
                pos = end
                continue
```

The line-comment check must precede the operator branch, since `--` and `#` are otherwise operator characters. Placing it before the identifier-quote branch is harmless and keeps all delimiter handling together.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lex_literals.py tests/test_lex_core.py -v`
Expected: PASS, 24 tests total. Both files must pass — Task 4's span-coverage test is the guard that these new branches still account for every character.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/engine/lex.py tests/test_lex_literals.py
git commit -m "feat: lex strings, comments and dollar quoting with unterminated tolerance"
```

---

### Task 6: Dialect syntax records for Postgres, ClickHouse and Trino

**Files:**
- Create: `src/pysqlsuggestions/dialects/postgres.py`, `src/pysqlsuggestions/dialects/clickhouse.py`, `src/pysqlsuggestions/dialects/trino.py`
- Test: `tests/test_dialect_lexing.py`

**Interfaces:**
- Consumes: `ANSI`, `Dialect`, `Syntax`, `Namespace`, `lex`.
- Produces: `POSTGRES`, `CLICKHOUSE`, `TRINO` module-level instances. Later tasks import them as `from pysqlsuggestions.dialects.postgres import POSTGRES`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dialect_lexing.py`:

```python
"""Lexical divergence between the four dialects, which is where most dialect variance lives."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.lex import TokenType, lex

ALL = [ANSI, POSTGRES, CLICKHOUSE, TRINO]


def significant(src: str, dialect: Dialect) -> list[tuple[TokenType, str]]:
    """(type, value) for every non-whitespace token."""
    return [(t.type, t.value) for t in lex(src, dialect.syntax) if t.type is not TokenType.WHITESPACE]


@pytest.mark.parametrize('dialect', ALL, ids=lambda d: d.name)
def test_every_dialect_has_reserved_words(dialect: Dialect) -> None:
    """Reserved words ship offline because quoting decisions precede any connection."""
    assert 'select' in dialect.reserved
    assert all(word.islower() for word in dialect.reserved)


def test_namespace_depth_differs() -> None:
    """One tuple drives three different answers to `analytics.<caret>`."""
    assert POSTGRES.namespace.levels == ('schema', 'table')
    assert CLICKHOUSE.namespace.levels == ('database', 'table')
    assert TRINO.namespace.levels == ('catalog', 'schema', 'table')


def test_postgres_folds_to_lower_clickhouse_preserves() -> None:
    """Case folding is the divergence users notice first."""
    assert significant('SELECT Foo', POSTGRES)[1] == (TokenType.IDENT, 'foo')
    assert significant('SELECT Foo', CLICKHOUSE)[1] == (TokenType.IDENT, 'Foo')
    assert significant('SELECT Foo', TRINO)[1] == (TokenType.IDENT, 'foo')


def test_clickhouse_accepts_backtick_identifiers() -> None:
    """ClickHouse quotes with either " or `; the others only know about "."""
    assert significant('`My Col`', CLICKHOUSE) == [(TokenType.IDENT, 'My Col')]
    assert significant('`My Col`', POSTGRES)[0][0] is not TokenType.IDENT


def test_clickhouse_hash_comments() -> None:
    """ClickHouse alone treats # as a line comment."""
    assert significant('# note\nSELECT', CLICKHOUSE) == [(TokenType.COMMENT, '# note'), (TokenType.IDENT, 'SELECT')]


def test_postgres_dollar_quoting_and_nested_comments() -> None:
    """Both are Postgres-only among these four."""
    assert significant('$fn$ x $fn$', POSTGRES) == [(TokenType.STRING, '$fn$ x $fn$')]
    assert significant('$fn$ x $fn$', TRINO)[0][0] is not TokenType.STRING
    assert significant('/* a /* b */ c */', POSTGRES) == [(TokenType.COMMENT, '/* a /* b */ c */')]
    assert significant('/* a /* b */ c */', TRINO) == [(TokenType.COMMENT, '/* a /* b */')]


def test_ansi_has_no_cast_operator() -> None:
    """The conservative fallback does not assume ::; the three real backends do."""
    assert ANSI.syntax.cast_operator is None
    assert POSTGRES.syntax.cast_operator == '::'
    assert CLICKHOUSE.syntax.cast_operator == '::'
    assert TRINO.syntax.cast_operator == '::'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dialect_lexing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pysqlsuggestions.dialects.postgres'`.

- [ ] **Step 3: Write the three dialect modules**

`src/pysqlsuggestions/dialects/postgres.py`:

```python
"""PostgreSQL. Composed from ANSI; nothing here subclasses anything."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import Namespace, Syntax

RESERVED = ANSI_RESERVED | frozenset(
    {
        'analyse', 'analyze', 'asymmetric', 'both', 'collate', 'current_role', 'current_user', 'deferrable',
        'do', 'freeze', 'ilike', 'initially', 'isnull', 'lateral', 'leading', 'localtime', 'localtimestamp',
        'notnull', 'placing', 'returning', 'session_user', 'similar', 'symmetric', 'trailing', 'variadic',
        'verbose', 'when',
    },
)

POSTGRES = replace(
    ANSI,
    name='postgres',
    syntax=Syntax(
        identifier_quotes=('"',),
        line_comments=('--',),
        nested_block_comments=True,
        string_escape_backslash=False,
        unquoted_case='lower',
        dollar_quoting=True,
        cast_operator='::',
    ),
    namespace=Namespace(levels=('schema', 'table')),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
)
```

`src/pysqlsuggestions/dialects/clickhouse.py`:

```python
"""ClickHouse."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import Namespace, Syntax

RESERVED = ANSI_RESERVED | frozenset(
    {
        'anti', 'any', 'array', 'asof', 'cluster', 'final', 'format', 'global', 'prewhere', 'sample',
        'semi', 'settings', 'ttl',
    },
)

CLICKHOUSE = replace(
    ANSI,
    name='clickhouse',
    syntax=Syntax(
        identifier_quotes=('"', '`'),
        line_comments=('--', '#'),
        nested_block_comments=False,
        string_escape_backslash=True,
        unquoted_case='preserve',
        dollar_quoting=False,
        cast_operator='::',
    ),
    namespace=Namespace(levels=('database', 'table')),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
)
```

`src/pysqlsuggestions/dialects/trino.py`:

```python
"""Trino."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import Namespace, Syntax

RESERVED = ANSI_RESERVED | frozenset(
    {
        'alter', 'catalogs', 'current_catalog', 'current_path', 'current_role', 'current_schema',
        'current_user', 'deallocate', 'describe', 'execute', 'extract', 'localtime', 'localtimestamp',
        'normalize', 'prepare', 'recursive', 'rollup', 'schemas', 'skip', 'unnest',
    },
)

TRINO = replace(
    ANSI,
    name='trino',
    syntax=Syntax(
        identifier_quotes=('"',),
        line_comments=('--',),
        nested_block_comments=False,
        string_escape_backslash=False,
        unquoted_case='lower',
        dollar_quoting=False,
        cast_operator='::',
    ),
    namespace=Namespace(levels=('catalog', 'schema', 'table')),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
)
```

`keywords` is the offline fallback set here; a later plan replaces it with introspected data from `pg_proc`, `system.functions` and `SHOW FUNCTIONS` per plan.md §4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dialect_lexing.py -v`
Expected: PASS, 10 tests (the parametrised one counts four times).

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/dialects tests/test_dialect_lexing.py
git commit -m "feat: postgres, clickhouse and trino syntax records"
```

---

### Task 7: Golden-request corpus harness with xfail burn-down

**Files:**
- Create: `tests/corpus/__init__.py`, `tests/corpus/cases.py`
- Create: `tests/conftest.py`
- Test: `tests/test_corpus.py`

**Interfaces:**
- Consumes: nothing from `src/` yet — the corpus is data.
- Produces: `GoldenRequest`, `CASES`, and `split_caret(sql) -> tuple[str, int]` from `tests.corpus.cases`. Task 14's test imports these and asserts `derive_request` matches. Every case starts `pending=True` and is flipped as stages land.

- [ ] **Step 1: Write the corpus record and an initial batch**

Create `tests/corpus/__init__.py` (empty file with a docstring):

```python
"""The translated acceptance corpus. Data only — no assertions live here."""
```

Create `tests/corpus/cases.py`:

```python
"""Golden requests, translated from pgcli's test_sqlcompletion and report_service's test_autocomplete.

Each case marks the caret inline with ⌶, which reads far better than an integer
offset and cannot drift out of sync with the SQL when a case is edited.

`pending=True` means the case is expected to fail: it is an xfail(strict=True)
until the stage that satisfies it lands. The burn-down count is reported by
tests/conftest.py on every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CARET = '⌶'


@dataclass(frozen=True)
class GoldenRequest:
    """One (sql, caret) input and the Request it must produce."""

    sql: str
    """Caret marked with ⌶. The marker is stripped before lexing."""
    kinds: tuple[str, ...]
    """Kind values, in order. Compared against [k.value for k in request.kinds]."""
    prefix: str = ''
    qualifier: tuple[str, ...] = ()
    clause: str | None = None
    relations: tuple[str, ...] = ()
    """Rendered as 'alias:dotted.path' per relation, in scope order. '' alias when unaliased."""
    dialect: str = 'postgres'
    pending: bool = True
    note: str = ''


def split_caret(sql: str) -> tuple[str, int]:
    """Strip the ⌶ marker and return (sql without marker, caret offset)."""
    caret = sql.index(CARET)
    return sql[:caret] + sql[caret + len(CARET) :], caret


def render_relations(relations: tuple[tuple[str | None, tuple[str, ...]], ...]) -> tuple[str, ...]:
    """Render (alias, path) pairs the way `GoldenRequest.relations` spells them."""
    return tuple(f'{alias or ""}:{".".join(path)}' for alias, path in relations)


CASES: tuple[GoldenRequest, ...] = (
    # --- prefix and qualifier ------------------------------------------------
    GoldenRequest(
        sql='SELECT ⌶',
        kinds=('column', 'function', 'keyword'),
        clause='SELECT',
        note='empty prefix, no relations yet',
    ),
    GoldenRequest(
        sql='SELECT id, na⌶ FROM users u',
        kinds=('column', 'function', 'keyword'),
        prefix='na',
        clause='SELECT',
        relations=('u:users',),
        note='plan.md §3.3 worked trace: scope comes from the whole statement',
    ),
    GoldenRequest(
        sql='SELECT * FROM users u WHERE u.⌶',
        kinds=('column',),
        qualifier=('u',),
        clause='WHERE',
        relations=('u:users',),
        note='the qualifier collapses the answer to columns only',
    ),
    GoldenRequest(
        sql='SELECT * FROM orders o JOIN users u ON o.user_id = u.⌶',
        kinds=('column',),
        qualifier=('u',),
        clause='ON',
        relations=('o:orders', 'u:users'),
    ),
    GoldenRequest(
        sql='SELECT * FROM users u WHERE u.em⌶',
        kinds=('column',),
        prefix='em',
        qualifier=('u',),
        clause='WHERE',
        relations=('u:users',),
    ),
    GoldenRequest(
        sql='SELECT * FROM "Mixed Case" m WHERE m.⌶',
        kinds=('column',),
        qualifier=('m',),
        clause='WHERE',
        relations=('m:Mixed Case',),
        note='quoted identifiers keep their case',
    ),
    # --- namespace depth -----------------------------------------------------
    GoldenRequest(
        sql='SELECT * FROM analytics.⌶',
        kinds=('table',),
        qualifier=('analytics',),
        clause='FROM',
        dialect='postgres',
        note='segment 1 reads as a schema',
    ),
    GoldenRequest(
        sql='SELECT * FROM analytics.⌶',
        kinds=('table',),
        qualifier=('analytics',),
        clause='FROM',
        dialect='clickhouse',
        note='segment 1 reads as a database, same answer shape',
    ),
    GoldenRequest(
        sql='SELECT * FROM analytics.⌶',
        kinds=('schema',),
        qualifier=('analytics',),
        clause='FROM',
        dialect='trino',
        note='segment 1 reads as a catalog, so segment 2 is a schema',
    ),
    GoldenRequest(
        sql='SELECT public.users.⌶ FROM public.users',
        kinds=('column',),
        qualifier=('public', 'users'),
        clause='SELECT',
        relations=(':public.users',),
        dialect='postgres',
        note='schema.table.column is legal, so a two-segment qualifier is ambiguous',
    ),
    # --- clause detection ----------------------------------------------------
    GoldenRequest(sql='SELECT * FROM ⌶', kinds=('table', 'schema'), clause='FROM'),
    GoldenRequest(sql='SELECT * FROM t JOIN ⌶', kinds=('table', 'schema'), clause='JOIN', relations=(':t',)),
    GoldenRequest(
        sql='SELECT * FROM t GROUP BY ⌶',
        kinds=('column', 'function'),
        clause='GROUP BY',
        relations=(':t',),
    ),
    GoldenRequest(
        sql='SELECT * FROM t ORDER BY ⌶',
        kinds=('column', 'function'),
        clause='ORDER BY',
        relations=(':t',),
    ),
    GoldenRequest(
        sql='SELECT a, (SELECT b FROM t2), ⌶ FROM t1',
        kinds=('column', 'function', 'keyword'),
        clause='SELECT',
        relations=(':t1',),
        note='a subquery that closed before the caret must not capture the clause',
    ),
    GoldenRequest(
        sql='SELECT * FROM t WHERE (a AND ⌶)',
        kinds=('column', 'function'),
        clause='WHERE',
        relations=(':t',),
        note='a non-subquery paren group falls back to the enclosing clause',
    ),
    GoldenRequest(
        sql='SELECT * FROM t1; SELECT * FROM t2 WHERE ⌶',
        kinds=('column', 'function'),
        clause='WHERE',
        relations=(':t2',),
        note='statement isolation: t1 is not in scope',
    ),
    # --- CTEs and subqueries -------------------------------------------------
    GoldenRequest(
        sql='WITH recent AS (SELECT id, total FROM orders) SELECT r.⌶ FROM recent r',
        kinds=('column',),
        qualifier=('r',),
        clause='SELECT',
        relations=('r:recent',),
        note='plan.md §3.3: no catalog call at all',
    ),
    GoldenRequest(
        sql='WITH a AS (SELECT * FROM users) SELECT a.⌶ FROM a',
        kinds=('column',),
        qualifier=('a',),
        clause='SELECT',
        relations=(':a',),
        note='the star case the three-state Projection exists for',
    ),
    GoldenRequest(
        sql='SELECT * FROM (SELECT id FROM orders) d WHERE d.⌶',
        kinds=('column',),
        qualifier=('d',),
        clause='WHERE',
        relations=('d:',),
        note='derived table; path is empty, projection is self-described',
    ),
    GoldenRequest(
        sql='SELECT * FROM users u WHERE id IN (SELECT user_id FROM orders o WHERE o.⌶)',
        kinds=('column',),
        qualifier=('o',),
        clause='WHERE',
        relations=('o:orders', 'u:users'),
        note='inner scope first, outer scope still visible',
    ),
    # --- literals and comments suppress everything ---------------------------
    GoldenRequest(
        sql="SELECT * FROM t WHERE name = 'ab⌶",
        kinds=(),
        clause='WHERE',
        relations=(':t',),
        note='inside an unterminated literal: offer nothing',
    ),
    GoldenRequest(
        sql="SELECT * FROM t WHERE name LIKE '%smith%⌶'",
        kinds=(),
        clause='WHERE',
        relations=(':t',),
        note='inside a terminated literal: still nothing',
    ),
    GoldenRequest(
        sql='SELECT * FROM t -- note ⌶',
        kinds=(),
        clause='FROM',
        relations=(':t',),
        note='inside a comment: nothing',
    ),
)
```

- [ ] **Step 2: Write the burn-down reporter**

Create `tests/conftest.py`:

```python
"""Reports how much of the acceptance corpus is still pending, on every run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.corpus.cases import CASES

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter


def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    """Print the corpus burn-down so progress is a number rather than a feeling."""
    pending = sum(1 for case in CASES if case.pending)
    total = len(CASES)
    terminalreporter.write_line(f'corpus burn-down: {total - pending}/{total} golden requests passing')
```

- [ ] **Step 3: Write the failing corpus sanity test**

Create `tests/test_corpus.py`:

```python
"""The corpus is data; these tests keep the data itself honest."""

from __future__ import annotations

import pytest

from tests.corpus.cases import CARET, CASES, split_caret


def test_corpus_is_not_empty() -> None:
    """A silently empty corpus would make the burn-down meaningless."""
    assert len(CASES) >= 20


@pytest.mark.parametrize('case', CASES, ids=lambda c: c.sql)
def test_every_case_marks_exactly_one_caret(case: object) -> None:
    """Two markers or none would produce a nonsense offset."""
    assert case.sql.count(CARET) == 1  # type: ignore[attr-defined]


def test_split_caret_strips_the_marker() -> None:
    """The marker must never reach the lexer."""
    sql, caret = split_caret('SELECT a⌶ FROM t')
    assert sql == 'SELECT a FROM t'
    assert caret == 8
    assert CARET not in sql


def test_every_case_names_a_known_dialect() -> None:
    """A typo in a dialect name would silently skip the case."""
    assert {case.dialect for case in CASES} <= {'ansi', 'postgres', 'clickhouse', 'trino'}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: PASS. The `pytest_terminal_summary` line reads `corpus burn-down: 0/24 golden requests passing`.

Note this task's tests pass immediately — the corpus is data with no implementation behind it yet. The burn-down number starting at zero is the point.

- [ ] **Step 5: Commit**

```bash
git add tests/corpus tests/conftest.py tests/test_corpus.py
git commit -m "test: golden-request corpus harness with burn-down reporting"
```

---

### Task 8: Analyse — statement isolation, qualifier and prefix

**Files:**
- Create: `src/pysqlsuggestions/engine/analyse.py`
- Test: `tests/test_analyse_prefix.py`

**Interfaces:**
- Consumes: `Token`, `TokenType` from `engine.lex`.
- Produces:
  - `statement_at(tokens: Sequence[Token], caret: int) -> tuple[int, int]` — index range into `tokens`.
  - `in_literal(tokens: Sequence[Token], caret: int) -> bool`.
  - `qualifier_and_prefix(tokens, caret) -> tuple[tuple[str, ...], str, tuple[int, int]]` returning `(qualifier, prefix, replace_span)`.
  - `depth_at(tokens, caret) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analyse_prefix.py`:

```python
"""Statement isolation and the word under the caret."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import depth_at, in_literal, qualifier_and_prefix, statement_at
from pysqlsuggestions.engine.lex import lex
from tests.corpus.cases import split_caret


def at(marked: str) -> tuple[tuple[str, ...], str, tuple[int, int]]:
    """Run qualifier_and_prefix on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return qualifier_and_prefix(lex(sql, POSTGRES.syntax), caret)


def test_bare_prefix() -> None:
    """A half-typed word with no dot."""
    assert at('SELECT na⌶') == ((), 'na', (7, 9))


def test_no_prefix_after_whitespace() -> None:
    """The caret after a space starts a fresh word."""
    assert at('SELECT ⌶') == ((), '', (7, 7))


def test_qualified_empty_prefix() -> None:
    """Immediately after a dot: qualifier known, nothing typed, nothing to replace."""
    assert at('SELECT u.⌶') == (('u',), '', (9, 9))


def test_qualified_with_prefix() -> None:
    """replace_span covers only the part after the dot — the qualifier keeps its place."""
    assert at('SELECT u.em⌶') == (('u',), 'em', (9, 11))


def test_two_segment_qualifier() -> None:
    """schema.table.column is legal in Postgres."""
    assert at('SELECT public.users.i⌶') == (('public', 'users'), 'i', (20, 21))


def test_whitespace_around_the_dot() -> None:
    """`u . id` is legal SQL and must not break qualifier detection."""
    assert at('SELECT u . em⌶') == (('u',), 'em', (11, 13))


def test_quoted_qualifier_and_prefix_keep_their_case() -> None:
    """Quoted identifiers are not folded."""
    assert at('SELECT "My Table".Col⌶') == (('My Table',), 'col', (18, 21))


def test_prefix_is_folded_for_the_dialect() -> None:
    """Postgres folds unquoted words to lower; the corpus compares folded values."""
    assert at('SELECT NA⌶')[1] == 'na'


def test_caret_in_the_middle_of_a_word_replaces_only_what_precedes_it() -> None:
    """replace_span ends at the caret, matching the existing editor behaviour."""
    assert at('SELECT nam⌶e FROM t') == ((), 'nam', (7, 10))


def test_no_prefix_after_an_operator() -> None:
    """`=` is not the start of an identifier."""
    assert at('WHERE a =⌶') == ((), '', (9, 9))


@pytest.mark.parametrize(
    ('marked', 'expected'),
    [
        ("SELECT 'ab⌶", True),
        ("SELECT 'ab'⌶", False),
        ('SELECT a -- note ⌶', True),
        ('SELECT /* x ⌶ */ a', True),
        ('SELECT a⌶', False),
    ],
)
def test_in_literal(marked: str, expected: bool) -> None:
    """A caret inside a string or comment suppresses every suggestion."""
    sql, caret = split_caret(marked)
    assert in_literal(lex(sql, POSTGRES.syntax), caret) is expected


def test_statement_isolation() -> None:
    """Only the statement containing the caret is analysed."""
    sql, caret = split_caret('SELECT * FROM t1; SELECT * FROM t2 WHERE ⌶')
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    texts = [t.text for t in tokens[lo:hi] if not t.text.isspace()]
    assert 't1' not in texts
    assert 't2' in texts


def test_semicolon_inside_parens_does_not_split() -> None:
    """Only depth-0 semicolons end a statement."""
    sql, caret = split_caret("SELECT f('a;b') , ⌶ FROM t")
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    assert [t.text for t in tokens[lo:hi] if t.text == 'FROM'] == ['FROM']


def test_depth_at() -> None:
    """The caret's paren depth drives clause matching."""
    sql, caret = split_caret('SELECT * FROM (SELECT ⌶)')
    assert depth_at(lex(sql, POSTGRES.syntax), caret) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_analyse_prefix.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pysqlsuggestions.engine.analyse'`.

- [ ] **Step 3: Write engine/analyse.py**

```python
"""Pure analysis over a token stream.

Every function here takes tokens and a caret offset and returns a plain value.
Nothing performs I/O, and nothing knows what a catalog is.
"""

from __future__ import annotations

from typing import Sequence

from pysqlsuggestions.engine.lex import Token, TokenType

_SKIP = (TokenType.WHITESPACE, TokenType.COMMENT)


def _index_before(tokens: Sequence[Token], caret: int) -> int:
    """Index of the last token starting strictly before `caret`, or -1."""
    last = -1
    for index, token in enumerate(tokens):
        if token.start < caret:
            last = index
        else:
            break
    return last


def depth_at(tokens: Sequence[Token], caret: int) -> int:
    """The paren depth the caret sits at."""
    index = _index_before(tokens, caret)
    if index < 0:
        return 0
    token = tokens[index]
    if token.type is TokenType.PUNCT and token.text == '(' and token.end <= caret:
        return token.depth + 1
    return token.depth


def in_literal(tokens: Sequence[Token], caret: int) -> bool:
    """Whether the caret sits inside a string literal or a comment."""
    for token in tokens:
        if token.type in (TokenType.STRING, TokenType.COMMENT) and token.covers(caret):
            return True
    return False


def statement_at(tokens: Sequence[Token], caret: int) -> tuple[int, int]:
    """The index range [lo, hi) of the statement containing `caret`.

    Statements are separated by semicolons at depth 0; a semicolon inside a
    string or inside parens does not split.
    """
    lo = 0
    for index, token in enumerate(tokens):
        if token.type is TokenType.PUNCT and token.text == ';' and token.depth == 0:
            if token.start >= caret:
                return lo, index
            lo = index + 1
    return lo, len(tokens)


def qualifier_and_prefix(
    tokens: Sequence[Token],
    caret: int,
) -> tuple[tuple[str, ...], str, tuple[int, int]]:
    """The dotted path and half-typed word immediately left of the caret.

    Returns (qualifier segments, prefix, replace_span). The span always ends at
    the caret, so choosing a suggestion replaces what was typed and nothing more.
    """
    index = _index_before(tokens, caret)
    prefix, span, cursor = '', (caret, caret), index

    if index >= 0 and tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        token = tokens[index]
        typed = token.text[: caret - token.start]
        prefix = _value_of(typed, token)
        span = (token.start, caret)
        cursor = index - 1
    elif index >= 0 and tokens[index].type in _SKIP:
        cursor = index

    segments: list[str] = []
    cursor = _skip_back(tokens, cursor)
    while cursor >= 0 and tokens[cursor].type is TokenType.PUNCT and tokens[cursor].text == '.':
        cursor = _skip_back(tokens, cursor - 1)
        if cursor < 0 or tokens[cursor].type is not TokenType.IDENT:
            break
        segments.append(tokens[cursor].value)
        cursor = _skip_back(tokens, cursor - 1)

    return tuple(reversed(segments)), prefix, span


def _skip_back(tokens: Sequence[Token], index: int) -> int:
    """The nearest index at or before `index` that is not whitespace or a comment."""
    while index >= 0 and tokens[index].type in _SKIP:
        index -= 1
    return index


def _value_of(typed: str, token: Token) -> str:
    """Fold a partially typed identifier the same way the lexer folded the whole one."""
    if token.quoted:
        return typed.lstrip('"`')
    folded = token.value
    return folded[: len(typed)] if len(folded) == len(token.text) else typed.lower()
```

Note on `_value_of`: for an unquoted identifier the folded value has the same length as the source text, so slicing it to the typed length gives the correctly folded prefix — this is what makes `SELECT NA⌶` produce `na` under Postgres and `NA` under ClickHouse without the analyser knowing which dialect it is looking at.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_analyse_prefix.py -v`
Expected: PASS, 18 tests (the parametrised `in_literal` counts five times).

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/engine/analyse.py tests/test_analyse_prefix.py
git commit -m "feat: statement isolation, literal detection, qualifier and prefix"
```

---

### Task 9: The ANSI clause model and `clause_at`

**Files:**
- Modify: `src/pysqlsuggestions/dialects/ansi.py`, `src/pysqlsuggestions/engine/analyse.py`
- Test: `tests/test_analyse_clause.py`

**Interfaces:**
- Consumes: `ClauseModel`, `Clause`, `Kind`, tokens.
- Produces: `clause_at(tokens, lo, hi, caret, clauses: ClauseModel) -> str | None` in `engine.analyse`, and a populated `ANSI.clauses`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analyse_clause.py`:

```python
"""Clause detection: scan back to the nearest clause keyword at the caret's depth."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import clause_at, statement_at
from pysqlsuggestions.engine.lex import lex
from tests.corpus.cases import split_caret


def clause(marked: str) -> str | None:
    """Run clause_at on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    return clause_at(tokens, lo, hi, caret, POSTGRES.clauses)


@pytest.mark.parametrize(
    ('marked', 'expected'),
    [
        ('SELECT ⌶', 'SELECT'),
        ('SELECT a FROM ⌶', 'FROM'),
        ('SELECT a FROM t WHERE ⌶', 'WHERE'),
        ('SELECT a FROM t JOIN u ON ⌶', 'ON'),
        ('SELECT a FROM t GROUP BY ⌶', 'GROUP BY'),
        ('SELECT a FROM t ORDER BY ⌶', 'ORDER BY'),
        ('SELECT a FROM t GROUP BY a HAVING ⌶', 'HAVING'),
        ('INSERT INTO ⌶', 'INSERT INTO'),
        ('UPDATE t SET ⌶', 'SET'),
        ('DELETE FROM ⌶', 'DELETE FROM'),
        ('WITH x AS (SELECT ⌶', 'SELECT'),
        ('⌶', None),
    ],
)
def test_clause_detection(marked: str, expected: str | None) -> None:
    """The nearest clause keyword wins, and multi-word names beat their prefixes."""
    assert clause(marked) == expected


def test_multi_word_clause_beats_its_last_word() -> None:
    """`GROUP BY` must not be read as the single word `BY`."""
    assert clause('SELECT a FROM t GROUP BY ⌶') == 'GROUP BY'


def test_a_closed_subquery_does_not_capture_the_clause() -> None:
    """`SELECT a, (SELECT b FROM t2), ⌶` is still in the outer SELECT."""
    assert clause('SELECT a, (SELECT b FROM t2), ⌶ FROM t1') == 'SELECT'


def test_inside_an_open_subquery_the_inner_clause_wins() -> None:
    """Depth equality is what separates the two."""
    assert clause('SELECT * FROM (SELECT b FROM t2 WHERE ⌶)') == 'WHERE'


def test_non_subquery_parens_fall_back_to_the_enclosing_clause() -> None:
    """`WHERE (a AND ⌶)` has no clause keyword at depth 1, so WHERE is the answer."""
    assert clause('SELECT * FROM t WHERE (a AND ⌶)') == 'WHERE'


def test_function_call_parens_fall_back_too() -> None:
    """`SELECT sum(⌶` is still the SELECT clause."""
    assert clause('SELECT sum(⌶') == 'SELECT'


def test_clause_ignores_the_word_being_typed() -> None:
    """A half-typed `fro` is not the FROM clause."""
    assert clause('SELECT a fro⌶') == 'SELECT'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_analyse_clause.py -v`
Expected: FAIL with `ImportError: cannot import name 'clause_at'`.

- [ ] **Step 3: Populate the ANSI clause model**

Replace the `ANSI = Dialect(...)` assignment in `src/pysqlsuggestions/dialects/ansi.py`, adding the imports `Clause` and `Kind`, and inserting this above it:

```python
COLUMN_EXPRESSION = (Kind.COLUMN, Kind.FUNCTION)
RELATION_REFERENCE = (Kind.TABLE, Kind.SCHEMA)

CLAUSES = ClauseModel(
    clauses=(
        Clause(name='WITH', suggests=()),
        Clause(name='SELECT', suggests=(Kind.COLUMN, Kind.FUNCTION, Kind.KEYWORD)),
        Clause(name='FROM', follows=frozenset({'SELECT'}), suggests=RELATION_REFERENCE),
        Clause(name='DELETE FROM', suggests=RELATION_REFERENCE),
        Clause(name='INSERT INTO', suggests=RELATION_REFERENCE),
        Clause(name='UPDATE', suggests=RELATION_REFERENCE),
        Clause(name='JOIN', follows=frozenset({'FROM', 'JOIN'}), suggests=RELATION_REFERENCE),
        Clause(name='ON', follows=frozenset({'JOIN'}), suggests=COLUMN_EXPRESSION),
        Clause(name='USING', follows=frozenset({'JOIN'}), suggests=(Kind.COLUMN,)),
        Clause(name='WHERE', suggests=COLUMN_EXPRESSION),
        Clause(name='GROUP BY', follows=frozenset({'FROM', 'WHERE'}), suggests=COLUMN_EXPRESSION),
        Clause(name='HAVING', follows=frozenset({'GROUP BY'}), suggests=COLUMN_EXPRESSION),
        Clause(name='WINDOW', suggests=COLUMN_EXPRESSION),
        Clause(name='ORDER BY', suggests=COLUMN_EXPRESSION),
        Clause(name='PARTITION BY', suggests=COLUMN_EXPRESSION),
        Clause(name='LIMIT', suggests=(Kind.KEYWORD,)),
        Clause(name='OFFSET', suggests=(Kind.KEYWORD,)),
        Clause(name='FETCH', suggests=(Kind.KEYWORD,)),
        Clause(name='SET', follows=frozenset({'UPDATE'}), suggests=(Kind.COLUMN,)),
        Clause(name='VALUES', suggests=COLUMN_EXPRESSION),
        Clause(name='RETURNING', suggests=COLUMN_EXPRESSION),
        Clause(name='UNION', suggests=(Kind.KEYWORD,)),
        Clause(name='INTERSECT', suggests=(Kind.KEYWORD,)),
        Clause(name='EXCEPT', suggests=(Kind.KEYWORD,)),
    ),
)
```

and set `clauses=CLAUSES` in the `ANSI` instance.

Multi-word names are matched as consecutive `IDENT` tokens, so `JOIN` covers `LEFT JOIN`, `INNER JOIN` and `CROSS JOIN` without separate entries — the qualifier words before `JOIN` are simply not clause names, and the scan finds `JOIN` itself.

- [ ] **Step 4: Add clause_at to engine/analyse.py**

Add the import `from pysqlsuggestions.dialects.base import ClauseModel` and this function:

```python
def clause_at(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    clauses: ClauseModel,
) -> str | None:
    """The nearest clause keyword governing the caret.

    Scans back over tokens at the caret's own depth. A subquery that closed
    before the caret sits at a deeper level and is skipped, so
    `SELECT a, (SELECT b FROM t2), <caret>` is still the outer SELECT.

    When the caret's depth holds no clause keyword — `WHERE (a AND <caret>)`,
    `SELECT sum(<caret>` — the search widens to the enclosing depth.
    """
    words = clauses.names()
    if not words:
        return None
    depth = depth_at(tokens, caret)
    while depth >= 0:
        found = _scan_for_clause(tokens, lo, hi, caret, words, depth)
        if found is not None:
            return found
        depth -= 1
    return None


def _scan_for_clause(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    words: tuple[str, ...],
    depth: int,
) -> str | None:
    """The clause name ending nearest to the left of `caret` at exactly `depth`.

    Ranked by (end offset, word count), so `DELETE FROM <caret>` answers
    'DELETE FROM' rather than the bare 'FROM' that ends at the same token.
    """
    best: tuple[int, int, str] | None = None
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.IDENT or token.depth != depth or token.end >= caret:
            continue
        for name in words:
            parts = name.split()
            run = _ident_run(tokens, index, hi, len(parts))
            if run is None or [t.value.upper() for t in run] != parts or run[-1].end >= caret:
                continue
            candidate = (run[-1].end, len(parts), name)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
            break
    return best[2] if best is not None else None


def _ident_run(tokens: Sequence[Token], start: int, hi: int, count: int) -> list[Token] | None:
    """`count` consecutive IDENT tokens beginning at `start`, ignoring whitespace and comments."""
    run: list[Token] = []
    index = start
    while index < hi and len(run) < count:
        token = tokens[index]
        if token.type in _SKIP:
            index += 1
            continue
        if token.type is not TokenType.IDENT:
            return None
        run.append(token)
        index += 1
    return run if len(run) == count else None
```

`words` is ordered longest-first by `ClauseModel.names()`, so `GROUP BY` is tried before `BY` at the same start index.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_analyse_clause.py -v`
Expected: PASS, 18 tests (the parametrised one counts twelve times).

Run: `uv run pytest -q`
Expected: everything green; burn-down still `0/24`.

- [ ] **Step 6: Commit**

```bash
git add src/pysqlsuggestions tests/test_analyse_clause.py
git commit -m "feat: ANSI clause model and depth-aware clause detection"
```

---

### Task 10: Dialect clause extensions

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py`, `clickhouse.py`, `trino.py`
- Test: `tests/test_dialect_clauses.py`

**Interfaces:**
- Consumes: `ANSI.clauses`, `ClauseModel.extend`.
- Produces: no new names — `POSTGRES.clauses`, `CLICKHOUSE.clauses` and `TRINO.clauses` gain their dialect-specific entries.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dialect_clauses.py`:

```python
"""Dialect clause vocabulary. Adding a clause must cost one line, not a parser change."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.analyse import clause_at, statement_at
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Kind
from tests.corpus.cases import split_caret


def clause(marked: str, dialect: Dialect) -> str | None:
    """Run clause_at on ⌶-marked SQL for one dialect."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, dialect.syntax)
    lo, hi = statement_at(tokens, caret)
    return clause_at(tokens, lo, hi, caret, dialect.clauses)


def test_clickhouse_prewhere() -> None:
    """PREWHERE is the canonical example from plan.md §4."""
    assert clause('SELECT * FROM t PREWHERE ⌶', CLICKHOUSE) == 'PREWHERE'
    assert clause('SELECT * FROM t PREWHERE ⌶', POSTGRES) == 'FROM'


@pytest.mark.parametrize('name', ['PREWHERE', 'FINAL', 'ARRAY JOIN', 'SETTINGS', 'SAMPLE', 'LIMIT BY'])
def test_clickhouse_clause_vocabulary(name: str) -> None:
    """Each ClickHouse-only clause is present exactly once."""
    assert CLICKHOUSE.clauses.get(name) is not None
    assert ANSI.clauses.get(name) is None


@pytest.mark.parametrize('name', ['UNNEST', 'MATCH_RECOGNIZE', 'TABLESAMPLE'])
def test_trino_clause_vocabulary(name: str) -> None:
    """Each Trino-only clause is present exactly once."""
    assert TRINO.clauses.get(name) is not None
    assert ANSI.clauses.get(name) is None


@pytest.mark.parametrize('name', ['LATERAL', 'ON CONFLICT', 'DISTINCT ON'])
def test_postgres_clause_vocabulary(name: str) -> None:
    """Each Postgres-only clause is present exactly once."""
    assert POSTGRES.clauses.get(name) is not None
    assert ANSI.clauses.get(name) is None


def test_extending_did_not_disturb_ansi() -> None:
    """ANSI is shared by all three; extend() must never mutate it."""
    assert len(ANSI.clauses.clauses) == 24


def test_array_join_suggests_columns() -> None:
    """ARRAY JOIN takes an array-valued column, not a table."""
    array_join = CLICKHOUSE.clauses.get('ARRAY JOIN')
    assert array_join is not None
    assert array_join.suggests == (Kind.COLUMN, Kind.FUNCTION)


def test_settings_suggests_keywords_only() -> None:
    """SETTINGS takes setting names, which are neither columns nor tables."""
    settings = CLICKHOUSE.clauses.get('SETTINGS')
    assert settings is not None
    assert settings.suggests == (Kind.KEYWORD,)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dialect_clauses.py -v`
Expected: FAIL — `CLICKHOUSE.clauses.get('PREWHERE')` returns `None`.

- [ ] **Step 3: Extend each dialect's clause model**

In `clickhouse.py`, add the imports `Clause` and `Kind`, and set `clauses=` in the `replace()` call:

```python
    clauses=ANSI.clauses.extend(
        Clause(name='PREWHERE', follows=frozenset({'FROM', 'SAMPLE', 'FINAL'}),
               suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(name='FINAL', follows=frozenset({'FROM'}), suggests=()),
        Clause(name='SAMPLE', follows=frozenset({'FROM', 'FINAL'}), suggests=(Kind.KEYWORD,)),
        Clause(name='ARRAY JOIN', follows=frozenset({'FROM', 'PREWHERE'}),
               suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(name='LIMIT BY', follows=frozenset({'ORDER BY', 'LIMIT'}),
               suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(name='SETTINGS', suggests=(Kind.KEYWORD,)),
    ),
```

In `trino.py`:

```python
    clauses=ANSI.clauses.extend(
        Clause(name='UNNEST', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(name='MATCH_RECOGNIZE', follows=frozenset({'FROM'}), suggests=(Kind.COLUMN,)),
        Clause(name='TABLESAMPLE', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.KEYWORD,)),
    ),
```

In `postgres.py`:

```python
    clauses=ANSI.clauses.extend(
        Clause(name='LATERAL', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.TABLE, Kind.FUNCTION)),
        Clause(name='DISTINCT ON', follows=frozenset({'SELECT'}), suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(name='ON CONFLICT', follows=frozenset({'INSERT INTO', 'VALUES'}), suggests=(Kind.COLUMN,)),
    ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dialect_clauses.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/dialects tests/test_dialect_clauses.py
git commit -m "feat: clickhouse, trino and postgres clause vocabularies"
```

---

### Task 11: Scope — FROM and JOIN relations

**Files:**
- Modify: `src/pysqlsuggestions/engine/analyse.py`
- Test: `tests/test_analyse_scope_tables.py`

**Interfaces:**
- Consumes: tokens, `ClauseModel`, `Relation`, `Scope`.
- Produces: `scope_of(tokens, lo, hi, caret, dialect) -> Scope` in `engine.analyse`. This task handles plain table references only; Tasks 12 and 13 extend the same function for CTEs and subqueries.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analyse_scope_tables.py`:

```python
"""Scope: the relations a statement puts in view. Tables and aliases only in this task."""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import scope_of, statement_at
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Scope
from tests.corpus.cases import split_caret


def scope(marked: str) -> Scope:
    """Run scope_of on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    return scope_of(tokens, lo, hi, caret, POSTGRES)


def rendered(marked: str) -> list[str]:
    """Relations as 'alias:dotted.path', the corpus spelling."""
    return [f'{r.alias or ""}:{".".join(r.path)}' for r in scope(marked).visible()]


def test_single_table_no_alias() -> None:
    """The simplest case."""
    assert rendered('SELECT ⌶ FROM users') == [':users']


def test_alias() -> None:
    """An alias is what the qualifier will match against."""
    assert rendered('SELECT ⌶ FROM users u') == ['u:users']


def test_explicit_as_alias() -> None:
    """AS is optional and must be skipped, not read as the alias."""
    assert rendered('SELECT ⌶ FROM users AS u') == ['u:users']


def test_qualified_table() -> None:
    """A schema-qualified reference keeps both segments in path."""
    assert rendered('SELECT ⌶ FROM public.users u') == ['u:public.users']


def test_multiple_relations_in_declaration_order() -> None:
    """Order matters for ranking later."""
    assert rendered('SELECT ⌶ FROM orders o, users u') == ['o:orders', 'u:users']


def test_join() -> None:
    """JOIN contributes relations exactly like FROM."""
    assert rendered('SELECT ⌶ FROM orders o JOIN users u ON o.user_id = u.id') == ['o:orders', 'u:users']


def test_left_outer_join() -> None:
    """Join qualifier words are not relations and not aliases."""
    assert rendered('SELECT ⌶ FROM orders o LEFT OUTER JOIN users u ON o.user_id = u.id') == [
        'o:orders',
        'u:users',
    ]


def test_scope_is_built_from_the_whole_statement() -> None:
    """The FROM clause sits to the right of the caret and must still be seen."""
    assert rendered('SELECT na⌶ FROM users u') == ['u:users']


def test_the_half_typed_word_is_not_a_relation() -> None:
    """`FROM us⌶` must not register a relation called `us`."""
    assert rendered('SELECT * FROM us⌶') == []


def test_keywords_are_not_read_as_aliases() -> None:
    """`FROM users WHERE` must not alias users to `where`."""
    assert rendered('SELECT * FROM users WHERE ⌶') == [':users']


def test_quoted_relation_keeps_its_case() -> None:
    """Quoted identifiers are preserved verbatim in path."""
    assert rendered('SELECT ⌶ FROM "Mixed Case" m') == ['m:Mixed Case']


def test_update_and_delete_targets() -> None:
    """Relations come from UPDATE and DELETE FROM as well as SELECT ... FROM."""
    assert rendered('UPDATE users u SET name = ⌶') == ['u:users']
    assert rendered('DELETE FROM users u WHERE ⌶') == ['u:users']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_analyse_scope_tables.py -v`
Expected: FAIL with `ImportError: cannot import name 'scope_of'`.

- [ ] **Step 3: Add scope_of to engine/analyse.py**

Add imports `from pysqlsuggestions.dialects.base import Dialect` and `from pysqlsuggestions.types import Relation, Scope`, then:

```python
_RELATION_CLAUSES = frozenset({'FROM', 'JOIN', 'UPDATE', 'DELETE FROM', 'INSERT INTO'})
_JOIN_QUALIFIERS = frozenset({'LEFT', 'RIGHT', 'FULL', 'INNER', 'OUTER', 'CROSS', 'NATURAL', 'LATERAL',
                              'ANTI', 'SEMI', 'ASOF', 'GLOBAL', 'ANY', 'ALL'})


def scope_of(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> Scope:
    """The relations visible at `caret`, built from the whole statement.

    Reading only the text left of the caret cannot work: in `SELECT na<caret>
    FROM users u` the relation that answers the question sits to the right.
    """
    relations = _relations_in(tokens, lo, hi, caret, dialect)
    return Scope(relations=tuple(relations))


def _relations_in(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> list[Relation]:
    """Every table reference introduced between `lo` and `hi`."""
    relations: list[Relation] = []
    index = lo
    while index < hi:
        token = tokens[index]
        if token.type in _SKIP:
            index += 1
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect)
        if matched is None or matched[0] not in _RELATION_CLAUSES:
            index += 1
            continue
        index = matched[1]
        index = _read_relation_list(tokens, index, hi, caret, dialect, relations)
    return relations


def _clause_starting_at(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    dialect: Dialect,
) -> tuple[str, int] | None:
    """(clause name, index just past it) when a clause name starts at `index`."""
    for name in dialect.clauses.names():
        parts = name.split()
        run = _ident_run(tokens, index, hi, len(parts))
        if run is not None and [t.value.upper() for t in run] == parts:
            return name, _index_of(tokens, run[-1]) + 1
    return None


def _index_of(tokens: Sequence[Token], token: Token) -> int:
    """The position of `token` in `tokens`, located by its start offset."""
    for index, candidate in enumerate(tokens):
        if candidate.start == token.start:
            return index
    raise ValueError('token not in stream')


def _read_relation_list(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    out: list[Relation],
) -> int:
    """Read comma-separated relation references until the next clause keyword."""
    while index < hi:
        index = _skip_forward(tokens, index, hi)
        if index >= hi:
            break
        if _clause_starting_at(tokens, index, hi, dialect) is not None:
            break
        token = tokens[index]
        if token.type is TokenType.PUNCT and token.text == ',':
            index += 1
            continue
        if token.type is TokenType.IDENT and token.value.upper() in _JOIN_QUALIFIERS:
            index += 1
            continue
        if token.type is not TokenType.IDENT:
            break
        path, index = _read_dotted_path(tokens, index, hi)
        if _covers_caret(tokens, path, caret):
            continue
        alias, index = _read_alias(tokens, index, hi, dialect)
        out.append(Relation(alias=alias, path=tuple(t.value for t in path), source='table'))
    return index


def _skip_forward(tokens: Sequence[Token], index: int, hi: int) -> int:
    """The next index at or after `index` that is not whitespace or a comment."""
    while index < hi and tokens[index].type in _SKIP:
        index += 1
    return index


def _read_dotted_path(tokens: Sequence[Token], index: int, hi: int) -> tuple[list[Token], int]:
    """Read `ident (. ident)*` starting at `index`."""
    path = [tokens[index]]
    index += 1
    while True:
        probe = _skip_forward(tokens, index, hi)
        if probe >= hi or tokens[probe].type is not TokenType.PUNCT or tokens[probe].text != '.':
            return path, index
        after = _skip_forward(tokens, probe + 1, hi)
        if after >= hi or tokens[after].type is not TokenType.IDENT:
            return path, index
        path.append(tokens[after])
        index = after + 1


def _read_alias(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    dialect: Dialect,
) -> tuple[str | None, int]:
    """Read an optional alias, with or without AS."""
    probe = _skip_forward(tokens, index, hi)
    if probe >= hi or tokens[probe].type is not TokenType.IDENT:
        return None, index
    if tokens[probe].value.upper() == 'AS':
        probe = _skip_forward(tokens, probe + 1, hi)
        if probe >= hi or tokens[probe].type is not TokenType.IDENT:
            return None, index
        return tokens[probe].value, probe + 1
    word = tokens[probe].value.upper()
    if word in dialect.reserved_upper or _clause_starting_at(tokens, probe, hi, dialect) is not None:
        return None, index
    return tokens[probe].value, probe + 1


def _covers_caret(tokens: Sequence[Token], path: Sequence[Token], caret: int) -> bool:
    """Whether the caret sits inside this reference — a half-typed name is not a relation."""
    return any(token.covers(caret) for token in path)
```

- [ ] **Step 4: Add `reserved_upper` to Dialect**

`_read_alias` needs the reserved set uppercased. Add this property to `Dialect` in `dialects/base.py`:

```python
    @property
    def reserved_upper(self) -> frozenset[str]:
        """`reserved`, uppercased. Alias detection compares against folded-then-uppercased words."""
        return frozenset(word.upper() for word in self.reserved)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_analyse_scope_tables.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 6: Commit**

```bash
git add src/pysqlsuggestions tests/test_analyse_scope_tables.py
git commit -m "feat: scope from FROM and JOIN relations with alias detection"
```

---

### Task 12: Scope — CTEs and projections

**Files:**
- Modify: `src/pysqlsuggestions/engine/analyse.py`
- Test: `tests/test_analyse_scope_ctes.py`

**Interfaces:**
- Consumes: `scope_of` from Task 11, `Projection`, `Relation`.
- Produces: `scope_of` now populates `Scope.ctes` and gives CTE relations a `Projection`. Adds `select_outputs(tokens, lo, hi) -> Projection` to `engine.analyse`, which Task 15 (plan 2, §6.1 features) also uses.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analyse_scope_ctes.py`:

```python
"""CTEs: the case users spend their time in, and the one no system catalog can answer."""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import scope_of, select_outputs, statement_at
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Scope
from tests.corpus.cases import split_caret


def scope(marked: str) -> Scope:
    """Run scope_of on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    return scope_of(tokens, lo, hi, caret, POSTGRES)


def outputs(sql: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(explicit column names, rendered star sources) for a select body."""
    tokens = lex(sql, POSTGRES.syntax)
    projection = select_outputs(tokens, 0, len(tokens), POSTGRES)
    return projection.columns, tuple(f'{r.alias or ""}:{".".join(r.path)}' for r in projection.stars)


def test_named_outputs() -> None:
    """A plain select list is fully self-described."""
    assert outputs('SELECT id, total FROM orders') == (('id', 'total'), ())


def test_aliased_outputs_use_the_alias() -> None:
    """The output name is what the outer query can reference."""
    assert outputs('SELECT sum(total) AS revenue, id FROM orders') == (('revenue', 'id'), ())


def test_expression_without_an_alias_has_no_output_name() -> None:
    """`sum(total)` with no AS contributes nothing referenceable."""
    assert outputs('SELECT sum(total), id FROM orders') == (('id',), ())


def test_bare_star_records_its_source() -> None:
    """The star cannot be expanded without the catalog, so its source is recorded."""
    assert outputs('SELECT * FROM users') == ((), (':users',))


def test_qualified_star_records_only_that_relation() -> None:
    """`o.*` pulls from orders alone."""
    assert outputs('SELECT o.* FROM orders o JOIN users u ON o.user_id = u.id') == ((), ('o:orders',))


def test_mixed_star_and_names() -> None:
    """Both halves are kept."""
    assert outputs('SELECT id, u.* FROM users u') == (('id',), ('u:users',))


def test_cte_is_registered_with_its_projection() -> None:
    """plan.md §3.3: no catalog call at all."""
    result = scope('WITH recent AS (SELECT id, total FROM orders) SELECT r.⌶ FROM recent r')
    relation = next(r for r in result.visible() if r.label == 'r')
    assert relation.source == 'cte'
    assert relation.projection is not None
    assert relation.projection.columns == ('id', 'total')
    assert relation.projection.stars == ()


def test_cte_selecting_a_star_keeps_the_star_unresolved() -> None:
    """The three-state Projection exists for exactly this."""
    result = scope('WITH a AS (SELECT * FROM users) SELECT a.⌶ FROM a')
    relation = next(r for r in result.visible() if r.label == 'a')
    assert relation.projection is not None
    assert relation.projection.columns == ()
    assert [r.path for r in relation.projection.stars] == [('users',)]


def test_declared_column_list_wins() -> None:
    """`WITH a(x, y) AS (...)` names the outputs regardless of the body."""
    result = scope('WITH a(x, y) AS (SELECT id, total FROM orders) SELECT a.⌶ FROM a')
    relation = next(r for r in result.visible() if r.label == 'a')
    assert relation.projection is not None
    assert relation.projection.columns == ('x', 'y')


def test_multiple_ctes() -> None:
    """Comma-separated CTEs are all registered."""
    result = scope('WITH a AS (SELECT id FROM t1), b AS (SELECT n FROM t2) SELECT ⌶ FROM a, b')
    assert sorted(result.ctes) == ['a', 'b']


def test_cte_is_not_a_catalog_table() -> None:
    """A relation resolving to a CTE must carry source='cte', so resolve skips the catalog."""
    result = scope('WITH recent AS (SELECT id FROM orders) SELECT ⌶ FROM recent')
    relation = next(r for r in result.visible() if r.label == 'recent')
    assert relation.source == 'cte'


def test_a_table_sharing_a_name_with_no_cte_stays_a_table() -> None:
    """Only names declared in WITH become CTEs."""
    result = scope('SELECT ⌶ FROM recent')
    relation = next(r for r in result.visible() if r.label == 'recent')
    assert relation.source == 'table'
    assert relation.projection is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_analyse_scope_ctes.py -v`
Expected: FAIL with `ImportError: cannot import name 'select_outputs'`.

- [ ] **Step 3: Add select_outputs and CTE handling to engine/analyse.py**

Add `from pysqlsuggestions.types import Projection` to the imports, then:

```python
def select_outputs(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
) -> Projection:
    """The output columns of the select body spanning [lo, hi).

    Explicit names and aliases go into `columns`. A bare `*` or a qualified
    `t.*` cannot be expanded here — the catalog holds that answer — so the
    relation it refers to is recorded in `stars` for resolve to finish.
    """
    body_relations = _relations_in(tokens, lo, hi, -1, dialect)
    start = _after_clause(tokens, lo, hi, 'SELECT', dialect)
    if start is None:
        return Projection()
    end = _next_clause_at_depth(tokens, start, hi, dialect, tokens[start].depth, {'FROM'})
    columns: list[str] = []
    stars: list[Relation] = []
    for item_lo, item_hi in _split_items(tokens, start, end):
        name, star_for = _output_of(tokens, item_lo, item_hi, body_relations, dialect)
        if star_for is not None:
            stars.extend(star_for)
        elif name is not None:
            columns.append(name)
    return Projection(columns=tuple(columns), stars=tuple(stars))


def _after_clause(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    name: str,
    dialect: Dialect,
) -> int | None:
    """Index just past the first occurrence of clause `name`."""
    for index in range(lo, hi):
        if tokens[index].type is not TokenType.IDENT:
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect)
        if matched is not None and matched[0] == name:
            return matched[1]
    return None


def _next_clause_at_depth(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
    depth: int,
    names: set[str],
) -> int:
    """Index of the next clause in `names` at `depth`, or `hi`."""
    for index in range(lo, hi):
        if tokens[index].type is not TokenType.IDENT or tokens[index].depth != depth:
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect)
        if matched is not None and matched[0] in names:
            return index
    return hi


def _split_items(tokens: Sequence[Token], lo: int, hi: int) -> list[tuple[int, int]]:
    """Split [lo, hi) on commas at the shallowest depth present."""
    if lo >= hi:
        return []
    base = min(t.depth for t in tokens[lo:hi] if t.type not in _SKIP)
    items, start = [], lo
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is TokenType.PUNCT and token.text == ',' and token.depth == base:
            items.append((start, index))
            start = index + 1
    items.append((start, hi))
    return [(a, b) for a, b in items if a < b]


def _output_of(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    relations: Sequence[Relation],
    dialect: Dialect,
) -> tuple[str | None, list[Relation] | None]:
    """(output name, star sources). Exactly one of the two is not None."""
    significant = [t for t in tokens[lo:hi] if t.type not in _SKIP]
    if not significant:
        return None, None

    if significant[-1].type is TokenType.OPERATOR and significant[-1].text == '*':
        if len(significant) >= 3 and significant[-2].text == '.':
            label = significant[-3].value
            return None, [r for r in relations if r.label == label]
        return None, list(relations)

    if len(significant) >= 2 and significant[-2].value.upper() == 'AS':
        return significant[-1].value, None

    if len(significant) >= 2 and significant[-1].type is TokenType.IDENT:
        word = significant[-1].value.upper()
        if word not in dialect.reserved_upper and significant[-2].type is not TokenType.PUNCT:
            return significant[-1].value, None

    if len(significant) == 1 and significant[0].type is TokenType.IDENT:
        return significant[0].value, None
    if significant[-1].type is TokenType.IDENT and significant[-2].text == '.':
        return significant[-1].value, None
    return None, None
```

Then rewrite `scope_of` to register CTEs and rebind relations that name one:

```python
def scope_of(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> Scope:
    """The relations visible at `caret`, built from the whole statement."""
    ctes = _read_ctes(tokens, lo, hi, dialect)
    relations = [_bind(relation, ctes) for relation in _relations_in(tokens, lo, hi, caret, dialect)]
    return Scope(relations=tuple(relations), ctes=ctes)


def _bind(relation: Relation, ctes: dict[str, Relation]) -> Relation:
    """Rebind a reference to a declared CTE, so resolve never asks the catalog for it."""
    if len(relation.path) != 1:
        return relation
    declared = ctes.get(relation.path[0])
    if declared is None:
        return relation
    return Relation(
        alias=relation.alias,
        path=relation.path,
        source='cte',
        projection=declared.projection,
    )


def _read_ctes(tokens: Sequence[Token], lo: int, hi: int, dialect: Dialect) -> dict[str, Relation]:
    """Every relation declared in a leading WITH clause, keyed by name."""
    start = _after_clause(tokens, lo, hi, 'WITH', dialect)
    if start is None:
        return {}
    ctes: dict[str, Relation] = {}
    index = _skip_forward(tokens, start, hi)
    if index < hi and tokens[index].type is TokenType.IDENT and tokens[index].value.upper() == 'RECURSIVE':
        index = _skip_forward(tokens, index + 1, hi)
    while index < hi:
        index = _skip_forward(tokens, index, hi)
        if index >= hi or tokens[index].type is not TokenType.IDENT:
            break
        name = tokens[index].value
        index += 1
        declared, index = _read_declared_columns(tokens, index, hi)
        index = _skip_forward(tokens, index, hi)
        if index >= hi or tokens[index].value.upper() != 'AS':
            break
        index = _skip_forward(tokens, index + 1, hi)
        if index >= hi or tokens[index].text != '(':
            break
        body_lo = index + 1
        body_hi = _matching_paren(tokens, index, hi)
        projection = (
            Projection(columns=declared)
            if declared
            else select_outputs(tokens, body_lo, body_hi, dialect)
        )
        ctes[name] = Relation(alias=None, path=(name,), source='cte', projection=projection)
        index = _skip_forward(tokens, body_hi + 1, hi)
        if index < hi and tokens[index].text == ',':
            index += 1
            continue
        break
    return ctes


def _read_declared_columns(tokens: Sequence[Token], index: int, hi: int) -> tuple[tuple[str, ...], int]:
    """Read an optional `(x, y)` column list following a CTE name."""
    probe = _skip_forward(tokens, index, hi)
    if probe >= hi or tokens[probe].text != '(':
        return (), index
    close = _matching_paren(tokens, probe, hi)
    names = tuple(t.value for t in tokens[probe + 1 : close] if t.type is TokenType.IDENT)
    return names, close + 1


def _matching_paren(tokens: Sequence[Token], index: int, hi: int) -> int:
    """Index of the `)` closing the `(` at `index`, or `hi`."""
    depth = tokens[index].depth
    for probe in range(index + 1, hi):
        token = tokens[probe]
        if token.type is TokenType.PUNCT and token.text == ')' and token.depth == depth:
            return probe
    return hi
```

Note `_relations_in` is called with `caret=-1` inside `select_outputs`, so no reference is ever discarded as half-typed when reading a CTE body.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_analyse_scope_ctes.py tests/test_analyse_scope_tables.py -v`
Expected: PASS, 24 tests. Task 11's tests must still pass — `scope_of` changed shape and they are the guard.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/engine/analyse.py tests/test_analyse_scope_ctes.py
git commit -m "feat: CTE scope with three-state projections"
```

---

### Task 13: Scope — subqueries and parent scopes

**Files:**
- Modify: `src/pysqlsuggestions/engine/analyse.py`
- Test: `tests/test_analyse_scope_subqueries.py`

**Interfaces:**
- Consumes: `scope_of` from Task 12.
- Produces: `scope_of` returns the innermost `Scope` containing the caret, with `parent` linking outward, and registers derived tables as `source='subquery'` relations.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analyse_scope_subqueries.py`:

```python
"""Nested scopes: a subquery sees its own relations first and its parent's as well."""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import scope_of, statement_at
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Scope
from tests.corpus.cases import split_caret


def scope(marked: str) -> Scope:
    """Run scope_of on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    return scope_of(tokens, lo, hi, caret, POSTGRES)


def rendered(marked: str) -> list[str]:
    """Visible relations as 'alias:dotted.path', innermost first."""
    return [f'{r.alias or ""}:{".".join(r.path)}' for r in scope(marked).visible()]


def test_derived_table_is_registered() -> None:
    """`(SELECT ...) d` is a relation the statement described itself."""
    result = scope('SELECT * FROM (SELECT id FROM orders) d WHERE d.⌶')
    relation = next(r for r in result.visible() if r.label == 'd')
    assert relation.source == 'subquery'
    assert relation.projection is not None
    assert relation.projection.columns == ('id',)


def test_derived_table_selecting_a_star() -> None:
    """Same three-state projection as a CTE."""
    result = scope('SELECT * FROM (SELECT * FROM orders) d WHERE d.⌶')
    relation = next(r for r in result.visible() if r.label == 'd')
    assert relation.projection is not None
    assert [r.path for r in relation.projection.stars] == [('orders',)]


def test_caret_inside_a_subquery_sees_the_inner_relation_first() -> None:
    """Inner scope first, outer scope still visible."""
    assert rendered('SELECT * FROM users u WHERE id IN (SELECT user_id FROM orders o WHERE o.⌶)') == [
        'o:orders',
        'u:users',
    ]


def test_parent_link_is_set() -> None:
    """Correlated subqueries reference the outer query, so the link must exist."""
    result = scope('SELECT * FROM users u WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = ⌶)')
    assert result.parent is not None
    assert [r.label for r in result.relations] == ['o']
    assert [r.label for r in result.parent.relations] == ['u']


def test_caret_outside_a_subquery_does_not_see_its_relations() -> None:
    """A subquery's FROM is private to it."""
    assert rendered('SELECT ⌶ FROM users u WHERE id IN (SELECT user_id FROM orders o)') == ['u:users']


def test_two_levels_of_nesting() -> None:
    """Scopes chain all the way out."""
    sql = 'SELECT * FROM a x WHERE id IN (SELECT id FROM b y WHERE id IN (SELECT id FROM c z WHERE z.⌶))'
    assert rendered(sql) == ['z:c', 'y:b', 'x:a']


def test_derived_table_body_relations_are_not_visible_outside() -> None:
    """`orders` inside the derived table must not leak into the outer scope."""
    assert rendered('SELECT ⌶ FROM (SELECT id FROM orders) d') == ['d:']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_analyse_scope_subqueries.py -v`
Expected: FAIL — `scope_of` currently returns one flat scope with no `parent` and treats `(SELECT ...)` contents as outer relations.

- [ ] **Step 3: Make scope_of recursive**

Replace `scope_of` in `engine/analyse.py`:

```python
def scope_of(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> Scope:
    """The innermost scope containing `caret`, chained to its parents.

    Built from the whole statement: in `SELECT na<caret> FROM users u` the
    relation that answers the question sits to the right of the caret.
    """
    ctes = _read_ctes(tokens, lo, hi, dialect)
    return _scope_level(tokens, lo, hi, caret, dialect, ctes, parent=None)


def _scope_level(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    ctes: dict[str, Relation],
    parent: Scope | None,
) -> Scope:
    """One query level, recursing into whichever subquery holds the caret."""
    relations = [_bind(r, ctes) for r in _relations_in(tokens, lo, hi, caret, dialect)]
    for derived_lo, derived_hi, alias in _derived_tables(tokens, lo, hi, dialect):
        projection = select_outputs(tokens, derived_lo, derived_hi, dialect)
        relations.append(Relation(alias=alias, path=(), source='subquery', projection=projection))

    here = Scope(relations=tuple(relations), ctes=ctes, parent=parent)

    for inner_lo, inner_hi in _subquery_bodies(tokens, lo, hi):
        if tokens[inner_lo].start <= caret <= tokens[inner_hi - 1].end:
            return _scope_level(tokens, inner_lo, inner_hi, caret, dialect, ctes, parent=here)
    return here


def _subquery_bodies(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
) -> list[tuple[int, int]]:
    """Index ranges of parenthesised bodies that begin with SELECT, one level down."""
    depth = min((t.depth for t in tokens[lo:hi] if t.type not in _SKIP), default=0)
    bodies = []
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.PUNCT or token.text != '(' or token.depth != depth:
            continue
        body_lo = _skip_forward(tokens, index + 1, hi)
        if body_lo >= hi or tokens[body_lo].type is not TokenType.IDENT:
            continue
        if tokens[body_lo].value.upper() not in {'SELECT', 'WITH', 'VALUES', 'TABLE'}:
            continue
        bodies.append((body_lo, _matching_paren(tokens, index, hi)))
    return bodies


def _derived_tables(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
) -> list[tuple[int, int, str | None]]:
    """Subquery bodies in a FROM position, with the alias that names them."""
    out = []
    for body_lo, body_hi in _subquery_bodies(tokens, lo, hi):
        opener = body_lo - 1
        while opener > lo and tokens[opener].text != '(':
            opener -= 1
        before = _skip_back(tokens, opener - 1)
        if before < lo or tokens[before].type is not TokenType.IDENT:
            continue
        if tokens[before].value.upper() not in _RELATION_CLAUSES | {'JOIN'}:
            continue
        alias, _ = _read_alias(tokens, body_hi + 1, hi, dialect)
        out.append((body_lo, body_hi, alias))
    return out
```

`_relations_in` must also stop descending into subqueries. Add this guard at the top of its loop, immediately after the `_SKIP` check:

```python
        if token.depth > tokens[lo].depth:
            index += 1
            continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_analyse_scope_subqueries.py tests/test_analyse_scope_ctes.py tests/test_analyse_scope_tables.py -v`
Expected: PASS, 31 tests. All three scope test files must pass together.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/engine/analyse.py tests/test_analyse_scope_subqueries.py
git commit -m "feat: nested scopes for subqueries and derived tables"
```

---

### Task 14: `derive_request` and the corpus burn-down

**Files:**
- Create: `src/pysqlsuggestions/engine/request.py`
- Modify: `src/pysqlsuggestions/__init__.py`, `tests/corpus/cases.py`
- Test: `tests/test_request.py`, `tests/test_golden_requests.py`

**Interfaces:**
- Consumes: everything from Tasks 4–13.
- Produces: `derive_request(sql: str, caret: int, dialect: Dialect) -> Request`, re-exported as `pysqlsuggestions.derive_request`. Plan 2's `complete()` calls exactly this.

- [ ] **Step 1: Write the failing unit tests**

Create `tests/test_request.py`:

```python
"""Request derivation: kind narrowing, the part that decides answer quality."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.request import derive_request
from pysqlsuggestions.types import Kind, Request
from tests.corpus.cases import split_caret


def request(marked: str, dialect: Dialect = POSTGRES) -> Request:
    """Run derive_request on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return derive_request(sql, caret, dialect)


def test_alias_qualifier_narrows_to_columns() -> None:
    """plan.md §10's worked example. No keywords, no functions, no tables."""
    result = request('SELECT * FROM users u WHERE u.⌶')
    assert result.kinds == (Kind.COLUMN,)
    assert result.qualifier == ('u',)


def test_unqualified_select_offers_columns_functions_and_keywords() -> None:
    """Narrowing only happens when there is something to narrow on."""
    assert request('SELECT ⌶ FROM t').kinds == (Kind.COLUMN, Kind.FUNCTION, Kind.KEYWORD)


def test_from_clause_offers_tables_and_schemas() -> None:
    """A relation position never suggests columns."""
    assert request('SELECT * FROM ⌶').kinds == (Kind.TABLE, Kind.SCHEMA)


def test_namespace_qualifier_postgres() -> None:
    """One segment names a schema, so the answer is tables."""
    assert request('SELECT * FROM analytics.⌶').kinds == (Kind.TABLE,)


def test_namespace_qualifier_trino() -> None:
    """Trino's first segment is a catalog, so the answer is schemas."""
    assert request('SELECT * FROM analytics.⌶', TRINO).kinds == (Kind.SCHEMA,)


def test_namespace_qualifier_clickhouse() -> None:
    """ClickHouse's first segment is a database, so the answer is tables."""
    assert request('SELECT * FROM analytics.⌶', CLICKHOUSE).kinds == (Kind.TABLE,)


def test_qualifier_deeper_than_the_namespace_reads_as_a_column() -> None:
    """Postgres allows schema.table.column, so two segments leave only columns."""
    assert request('SELECT public.users.⌶ FROM public.users').kinds == (Kind.COLUMN,)


def test_trino_two_segment_qualifier_reaches_tables() -> None:
    """Three namespace levels mean catalog.schema. still has a table level to offer."""
    assert request('SELECT * FROM prod.analytics.⌶', TRINO).kinds == (Kind.TABLE,)


def test_alias_beats_a_schema_of_the_same_name() -> None:
    """Resolution order is alias first, then namespace."""
    result = request('SELECT * FROM orders public WHERE public.⌶')
    assert result.kinds == (Kind.COLUMN,)


def test_caret_in_a_literal_offers_nothing() -> None:
    """Suggesting identifiers inside a string is worse than suggesting nothing."""
    result = request("SELECT * FROM t WHERE name = 'ab⌶")
    assert result.kinds == ()
    assert result.prefix == ''


def test_caret_in_a_comment_offers_nothing() -> None:
    """Same rule."""
    assert request('SELECT * FROM t -- note ⌶').kinds == ()


def test_replace_span_covers_only_the_typed_prefix() -> None:
    """The qualifier keeps its place when a suggestion is accepted."""
    result = request('SELECT * FROM users u WHERE u.em⌶')
    assert result.replace_span == (30, 32)
    assert result.prefix == 'em'


def test_scope_is_attached() -> None:
    """Resolve needs the scope; it must never arrive as None for a real statement."""
    result = request('SELECT na⌶ FROM users u')
    assert result.scope is not None
    assert [r.label for r in result.scope.visible()] == ['u']


def test_empty_input() -> None:
    """An empty document is not an error."""
    result = derive_request('', 0, POSTGRES)
    assert result.kinds == (Kind.KEYWORD,)
    assert result.clause is None
```

- [ ] **Step 2: Write the failing corpus test**

Create `tests/test_golden_requests.py`:

```python
"""The translated acceptance corpus, run against derive_request."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.request import derive_request
from tests.corpus.cases import CASES, GoldenRequest, split_caret

DIALECTS = {'ansi': ANSI, 'postgres': POSTGRES, 'clickhouse': CLICKHOUSE, 'trino': TRINO}


def _params() -> list[object]:
    """Each case, marked xfail(strict=True) while it is still pending."""
    return [
        pytest.param(case, marks=pytest.mark.xfail(strict=True, reason=case.note or 'pending')) if case.pending
        else pytest.param(case)
        for case in CASES
    ]


@pytest.mark.parametrize('case', _params(), ids=[f'{c.dialect}: {c.sql}' for c in CASES])
def test_golden_request(case: GoldenRequest) -> None:
    """derive_request must reproduce the recorded Request exactly."""
    sql, caret = split_caret(case.sql)
    result = derive_request(sql, caret, DIALECTS[case.dialect])

    assert tuple(kind.value for kind in result.kinds) == case.kinds
    assert result.prefix == case.prefix
    assert result.qualifier == case.qualifier
    assert result.clause == case.clause

    relations = result.scope.visible() if result.scope else ()
    assert tuple(f'{r.alias or ""}:{".".join(r.path)}' for r in relations) == case.relations
```

- [ ] **Step 3: Run both to verify they fail**

Run: `uv run pytest tests/test_request.py tests/test_golden_requests.py -v`
Expected: `test_request.py` FAILs with `ModuleNotFoundError: No module named 'pysqlsuggestions.engine.request'`; `test_golden_requests.py` collects but every case xfails.

- [ ] **Step 4: Write engine/request.py**

```python
"""Stage three: turn the analysis into a Request.

This is the seam. Everything above is text; everything below is catalog access
and ranking. Kind narrowing happens here, and it is the main quality lever in a
completion engine — mediocre ones suggest everything all the time.
"""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.engine.analyse import (
    clause_at,
    in_literal,
    qualifier_and_prefix,
    scope_of,
    statement_at,
)
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Kind, Request, Scope

_NAMESPACE_KINDS = {'schema': Kind.SCHEMA, 'database': Kind.SCHEMA, 'catalog': Kind.SCHEMA, 'table': Kind.TABLE}


def derive_request(sql: str, caret: int, dialect: Dialect) -> Request:
    """What should be suggested at `caret`, decided without touching a catalog."""
    tokens = lex(sql, dialect.syntax)
    lo, hi = statement_at(tokens, caret)
    clause = clause_at(tokens, lo, hi, caret, dialect.clauses)

    if in_literal(tokens, caret):
        scope = scope_of(tokens, lo, hi, caret, dialect) if tokens else None
        return Request(kinds=(), prefix='', replace_span=(caret, caret), clause=clause, scope=scope)

    qualifier, prefix, span = qualifier_and_prefix(tokens, caret)
    scope = scope_of(tokens, lo, hi, caret, dialect) if tokens else None
    kinds = _kinds_for(clause, qualifier, scope, dialect)
    return Request(
        kinds=kinds,
        prefix=prefix,
        replace_span=span,
        qualifier=qualifier,
        clause=clause,
        scope=scope,
    )


def _kinds_for(
    clause: str | None,
    qualifier: tuple[str, ...],
    scope: Scope | None,
    dialect: Dialect,
) -> tuple[Kind, ...]:
    """What the caret position admits, narrowed by any qualifier."""
    if not qualifier:
        return _clause_kinds(clause, dialect)
    return _qualified_kinds(qualifier, scope, dialect)


def _clause_kinds(clause: str | None, dialect: Dialect) -> tuple[Kind, ...]:
    """The kinds the governing clause admits."""
    if clause is None:
        return (Kind.KEYWORD,)
    found = dialect.clauses.get(clause)
    return found.suggests if found is not None else (Kind.KEYWORD,)


def _qualified_kinds(
    qualifier: tuple[str, ...],
    scope: Scope | None,
    dialect: Dialect,
) -> tuple[Kind, ...]:
    """Resolution order is alias first, then namespace.

    A qualifier naming something in scope collapses the answer to columns
    outright — no keywords, no functions, no tables. Only when it matches no
    relation is it read as a schema, database or catalog name, and how deep the
    qualifier reaches decides what the next segment can be. A qualifier deeper
    than the namespace has nowhere left to go but a column.

    The union plan.md §3.3 calls for in the ambiguous Postgres
    `schema.table.column` case is a resolution concern, not a kind one: both
    readings yield COLUMN, and which relation to fetch is resolve's problem.
    """
    if scope is not None and _names_a_relation(qualifier[0], scope):
        return (Kind.COLUMN,)

    level = dialect.namespace.level_of(len(qualifier) + 1)
    if level is None:
        return (Kind.COLUMN,)

    kind = _NAMESPACE_KINDS.get(level)
    return (kind,) if kind is not None else ()


def _names_a_relation(segment: str, scope: Scope) -> bool:
    """Whether `segment` is an alias or relation name anywhere in the scope chain."""
    return any(relation.label == segment for relation in scope.visible()) or segment in scope.ctes
```

- [ ] **Step 5: Re-export from the package root**

Replace `src/pysqlsuggestions/__init__.py`:

```python
"""Context-aware, schema-aware SQL completion as an embeddable library."""

from __future__ import annotations

from pysqlsuggestions.engine.request import derive_request
from pysqlsuggestions.types import (
    Candidate,
    Column,
    Function,
    Kind,
    Projection,
    Relation,
    Request,
    Scope,
    Suggestion,
    Table,
)

__version__ = '0.1.0.dev0'

__all__ = [
    'Candidate',
    'Column',
    'Function',
    'Kind',
    'Projection',
    'Relation',
    'Request',
    'Scope',
    'Suggestion',
    'Table',
    '__version__',
    'derive_request',
]
```

- [ ] **Step 6: Run the unit tests**

Run: `uv run pytest tests/test_request.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 7: Burn the corpus down**

Run: `uv run pytest tests/test_golden_requests.py -v`

Every case still marked `pending=True` that now passes will report `XPASS(strict)` and **fail the run**. That is the mechanism working. For each such case, set `pending=False` in `tests/corpus/cases.py` and re-run.

Repeat until the run is green. Cases that remain genuinely failing stay `pending=True` — do not weaken an assertion to make one pass. If a case cannot be satisfied, leave it pending and note why in its `note` field; it becomes input to plan 2.

Expected end state: `uv run pytest -q` is green and the summary line reads `corpus burn-down: N/24 golden requests passing` with N ≥ 20. The four literal/comment cases and the `derived table` case are the likeliest stragglers.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: derive_request with alias-first qualifier resolution"
```

---

### Task 15: Public API documentation and release notes for the stage

**Files:**
- Modify: `README.md`
- Create: `docs/request-pipeline.md`

**Interfaces:**
- Consumes: `derive_request`.
- Produces: nothing importable. This task exists so the milestone is handed over legibly.

- [ ] **Step 1: Write the doctest-style usage test**

Add to `tests/test_request.py`:

```python
def test_readme_example_is_accurate() -> None:
    """The example in README.md must actually work, verbatim."""
    sql = 'SELECT id, na FROM users u'
    result = derive_request(sql, 13, POSTGRES)
    assert result.prefix == 'na'
    assert result.clause == 'SELECT'
    assert result.replace_span == (11, 13)
    assert [r.label for r in (result.scope.visible() if result.scope else ())] == ['u']
```

- [ ] **Step 2: Run it to verify it fails or passes**

Run: `uv run pytest tests/test_request.py::test_readme_example_is_accurate -v`
Expected: PASS if Task 14 is complete. If it fails, the offsets in the assertion are wrong — fix the assertion against real output, then write the README to match.

- [ ] **Step 3: Rewrite README.md**

```markdown
# pysqlsuggestions

Context-aware, schema-aware SQL completion for Python. A library, not a CLI and
not a language server — importable into a FastAPI service, a notebook kernel or
an internal reporting tool without dragging a process boundary along.

Zero runtime dependencies. PostgreSQL, ClickHouse and Trino, plus an `ansi`
fallback so an unknown backend degrades instead of failing.

## Status

The request pipeline is implemented: given SQL and a caret, the library decides
what should be suggested. Fetching and ranking those suggestions is next.

## Usage

```python
from pysqlsuggestions import derive_request
from pysqlsuggestions.dialects.postgres import POSTGRES

request = derive_request('SELECT id, na FROM users u', 13, POSTGRES)

request.prefix        # 'na'
request.clause        # 'SELECT'
request.replace_span  # (11, 13) — what the editor overwrites
request.kinds         # (Kind.COLUMN, Kind.FUNCTION, Kind.KEYWORD)
request.scope         # relations in view, built from the whole statement
```

The scope comes from the entire statement, not the text left of the caret — the
`FROM` clause that answers the question above sits to the right of it.

## Design

See `docs/request-pipeline.md` for how the stages fit together, and
`docs/superpowers/specs/` for the full design.
```

- [ ] **Step 4: Write docs/request-pipeline.md**

```markdown
# The request pipeline

Three pure stages turn text into a `Request`. None of them performs I/O, so all
of them are testable with no database and no mocks.

## Lex

`engine/lex.py` scans the source once, driven entirely by the dialect's `Syntax`
record. It never raises: an unterminated string, quoted identifier or comment
produces a token running to end of input with `terminated=False`, because
completion works on invalid input by definition.

Two properties matter downstream. Each token carries its paren `depth`,
precomputed during the scan, which turns every "nearest keyword at depth 0"
question into a filter. And the lexer does not classify keywords — every word is
an `IDENT`, and analyse consults the dialect's vocabulary. That keeps the lexer
dependent on dialect *syntax* only, never dialect *vocabulary*.

## Analyse

`engine/analyse.py` is four pure functions over the token stream:

- `statement_at` isolates the statement containing the caret.
- `qualifier_and_prefix` reads the dotted path and half-typed word to its left.
- `clause_at` scans back to the nearest clause keyword at the caret's depth, so a
  subquery that closed before the caret does not capture it.
- `scope_of` walks the whole statement, recursing into CTE bodies and returning
  the innermost scope with `parent` links outward.

## Request

`engine/request.py` narrows. A qualifier matching a relation in scope collapses
the answer to columns; otherwise it is read as a namespace level, and one tuple
(`Namespace.levels`) gives three different answers to `analytics.` across the
three backends. Where both readings are legal — Postgres permits
`schema.table.column` — the union is emitted rather than a guess.

Resolution order is **alias first, then namespace**.
```

- [ ] **Step 5: Run the full suite**

Run: `./scripts/check.sh`
Expected: ruff format, ruff check, mypy and pytest all exit 0.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/request-pipeline.md tests/test_request.py
git commit -m "docs: request pipeline usage and design notes"
```

---

## What plan 2 picks up

`engine/local.py` (§6.1 free features, using `select_outputs` from Task 12),
`engine/rank.py`, `ports.py`, `catalogs/memory.py`, `resolve.py`, `api.complete()`,
`testing/conformance.py`, the resolution half of the translated corpus, and the
differential test against `pg_autocomplete.py`.

Any corpus case still `pending=True` at the end of Task 14 is an input to that
plan, not a failure of this one.
