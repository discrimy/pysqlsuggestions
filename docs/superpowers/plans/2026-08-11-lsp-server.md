# pysqlsuggestions LSP server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A language server that answers `textDocument/completion` with `pysqlsuggestions` results, installable as a wheel and usable by any LSP client.

**Architecture:** A new distribution `pysqlsuggestions-lsp` in `lsp/`, beside `demo/` and outside `src/`, so the library keeps its zero-dependency claim. Five modules: two pure (statement splitting, item conversion), one that opens connections, one that speaks LSP, one entry point. Depends on the library one way only.

**Tech Stack:** Python 3.10+, pygls (LSP framework), lsprotocol (LSP types), pg8000 and trino (pure-Python drivers), pytest, mypy strict, ruff.

This is plan 1 of 2 for `docs/superpowers/specs/2026-08-11-vscode-extension-design.md`. Plan 2 covers the VS Code extension, the managed venv and VSIX packaging. This plan produces working, testable software on its own: a server any LSP client can drive.

## Global Constraints

- **Python floor is 3.10.** Matches the library's `requires-python = '>=3.10'`. No `match` statements guarded on 3.11, no `Self`, no PEP 695 generics.
- **`src/pysqlsuggestions/` must never import `pysqlsuggestions_lsp`.** The dependency runs one way. Task 1 adds a test for it.
- **`import pysqlsuggestions` must still import no driver.** `test_import_pulls_in_no_drivers` already enforces this and must keep passing.
- **Single quotes, 120 columns.** `ruff format` with `quote-style = 'single'`; the repo's existing style.
- **mypy strict passes.** `[tool.mypy] strict = true`; every new module is fully annotated, including test functions (`-> None`).
- **Docstrings on every public module, class and function.** Ruff's `D` rules are on, with `D100`/`D104`/`D105`/`D106`/`D107` ignored.
- **`lsp/pyproject.toml` version equals root `pyproject.toml` version.** Verbatim string equality. Task 1 adds a test.
- **Verification command is `./scripts/check.sh`** — ruff format, ruff check, mypy, pytest. `uv run pytest -m 'not integration'` skips the docker backends.

---

## File Structure

**Created:**

| path | responsibility |
| --- | --- |
| `lsp/pyproject.toml` | the `pysqlsuggestions-lsp` distribution: deps, version, build backend |
| `lsp/pysqlsuggestions_lsp/__init__.py` | `__version__` only |
| `lsp/pysqlsuggestions_lsp/documents.py` | statement boundaries and offset→line/character. Pure. |
| `lsp/pysqlsuggestions_lsp/convert.py` | `Suggestion` + `plan_insertion` → `CompletionItem`. Pure. |
| `lsp/pysqlsuggestions_lsp/connections.py` | `Profile` → dialect → driver → `DbapiCatalog` |
| `lsp/pysqlsuggestions_lsp/server.py` | pygls server and handlers |
| `lsp/pysqlsuggestions_lsp/__main__.py` | stdio entry point |
| `tests/lsp/test_documents.py` | statement splitting |
| `tests/lsp/test_convert.py` | item conversion, ordering, filtering |
| `tests/lsp/test_connections.py` | dialect and driver resolution, against fakes |
| `tests/lsp/test_server.py` | handlers through pygls' test client |
| `tests/lsp/__init__.py` | package marker, matching `tests/queries/` |
| `tests/integration/test_lsp_backends.py` | end to end against docker Postgres |

**Modified:**

| path | change |
| --- | --- |
| `pyproject.toml` | uv workspace member, `pg8000` extra, dev deps, mypy/ruff paths |
| `tests/test_purity.py` | one-way dependency guard, version lockstep guard |

`documents.py` and `convert.py` hold every behaviour that can be silently wrong, and neither needs a server or a database to test. That is the point of the split.

---

### Task 1: The distribution, the workspace, and the structural guards

Nothing works yet; this makes `pysqlsuggestions_lsp` importable, wires it into the existing checks, and adds the two guards the spec asks for. The guards come first because they are the cheapest thing to add now and the most annoying thing to retrofit.

**Files:**
- Create: `lsp/pyproject.toml`, `lsp/pysqlsuggestions_lsp/__init__.py`, `tests/lsp/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_purity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the importable package `pysqlsuggestions_lsp` with `__version__: str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_purity.py`:

```python
LSP = ROOT / 'lsp' / 'pysqlsuggestions_lsp'


def test_lsp_version_matches_the_library() -> None:
    """
    The server and the library are released together, so their versions agree.

    The extension bundles wheels built from this tree. A server wheel claiming a
    version the library wheel does not is a bug report nobody can reproduce,
    because the two numbers in it describe different code.
    """
    root = re.search(r"^version = '([^']+)'", (ROOT / 'pyproject.toml').read_text(), re.M)
    server = re.search(r"^version = '([^']+)'", (ROOT / 'lsp' / 'pyproject.toml').read_text(), re.M)
    assert root is not None and server is not None
    assert root.group(1) == server.group(1)


def test_the_library_does_not_import_the_server() -> None:
    """
    The dependency runs one way: the server imports the library, never the reverse.

    `lsp/` may import drivers and pygls, which is exactly why the library must
    not reach into it. An import added in the wrong direction would drag both
    into `import pysqlsuggestions` and break the zero-dependency claim from a
    file that looks unrelated to it.
    """
    for path in (ROOT / 'src' / 'pysqlsuggestions').rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        assert 'pysqlsuggestions_lsp' not in source, f'{path} names the server package'
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/test_purity.py -v
```

Expected: `test_lsp_version_matches_the_library` FAILS — `lsp/pyproject.toml` does not exist, so `read_text` raises `FileNotFoundError`. `test_the_library_does_not_import_the_server` PASSES already, which is correct: it is a regression guard, and a guard that passes on day one is a guard doing its job.

- [ ] **Step 3: Create the distribution**

`lsp/pyproject.toml`:

```toml
[project]
name = 'pysqlsuggestions-lsp'
version = '0.2.1'
description = 'A language server over pysqlsuggestions'
requires-python = '>=3.10'
license = { file = '../LICENSE' }
dependencies = [
    'pysqlsuggestions==0.2.1',
    'pygls>=1.3',
]

# Named after the driver, not the backend, following the library's own extras.
# The extension bundles these two because both are pure Python and so one VSIX
# serves every platform; psycopg2 stays the documented choice for library users.
[project.optional-dependencies]
pg8000 = ['pg8000>=1.30']
trino = ['trino>=0.328']

[project.scripts]
pysqlsuggestions-lsp = 'pysqlsuggestions_lsp.__main__:main'

[build-system]
requires = ['hatchling']
build-backend = 'hatchling.build'

[tool.hatch.build.targets.wheel]
packages = ['pysqlsuggestions_lsp']
```

`lsp/pysqlsuggestions_lsp/__init__.py`:

```python
"""A language server over pysqlsuggestions. The library stays free of it."""

from __future__ import annotations

__version__ = '0.2.1'
```

`tests/lsp/__init__.py`: empty file.

- [ ] **Step 4: Wire it into the root project**

In `pyproject.toml`, add a `pg8000` extra beside the existing ones, keeping the comment's promise that extras are named after the driver:

```toml
[project.optional-dependencies]
psycopg2 = ['psycopg2-binary>=2.9']
pg8000 = ['pg8000>=1.30']
clickhouse-driver = ['clickhouse-driver>=0.2.9']
trino = ['trino>=0.328']
demo = ['fastapi>=0.110', 'uvicorn>=0.29']
```

Add the workspace and the path dependency so `uv sync` installs the server editable:

```toml
[tool.uv.workspace]
members = ['lsp']

[tool.uv.sources]
pysqlsuggestions-lsp = { workspace = true }
```

Add to `[dependency-groups] dev`:

```toml
    'pysqlsuggestions-lsp',
    'pg8000>=1.30',
    'pytest-lsp>=0.4',
```

Extend the tool paths:

```toml
[tool.ruff]
src = ['src', 'tests', 'lsp']

[tool.ruff.lint.isort]
known-first-party = ['pysqlsuggestions', 'pysqlsuggestions_lsp', 'scripts', 'tests']

[tool.mypy]
strict = true
files = ['src', 'tests', 'lsp']
```

- [ ] **Step 5: Sync and run the full check**

```bash
uv sync
uv run pytest tests/test_purity.py -v
```

Expected: both new tests PASS.

```bash
./scripts/check.sh
```

Expected: everything passes. If ruff complains about `lsp/` formatting, run `uv run ruff format .` and re-run.

- [ ] **Step 6: Commit**

```bash
git add lsp/ tests/lsp/__init__.py tests/test_purity.py pyproject.toml uv.lock
git commit -m "feat: a package for the server, and guards on which way it depends"
```

---

### Task 2: Statement boundaries

`derive_request` builds scope from the whole statement, so a document of many statements must be cut down to the one holding the caret. Cutting on the `;` character is wrong — it appears inside literals, comments and quoted identifiers — and which delimiters those are is a property of the dialect. `engine.lex` already knows, and `;` is in its `_PUNCTUATION` set, so semicolons arrive as `PUNCT` tokens.

**Files:**
- Create: `lsp/pysqlsuggestions_lsp/documents.py`
- Test: `tests/lsp/test_documents.py`

**Interfaces:**
- Consumes: `pysqlsuggestions.engine.lex.lex`, `TokenType`; `pysqlsuggestions.dialects.base.Syntax`.
- Produces:
  - `statement_at(text: str, offset: int, syntax: Syntax) -> tuple[str, int]` — the statement containing `offset`, and its start offset in `text`. The caret within the statement is `offset - start`.
  - `line_starts(text: str) -> list[int]` — offset of each line's first character.
  - `to_position(starts: Sequence[int], offset: int) -> tuple[int, int]` — zero-based `(line, character)`.

- [ ] **Step 1: Write the failing tests**

`tests/lsp/test_documents.py`:

```python
"""Statement boundaries, and the offset arithmetic every span depends on."""

from __future__ import annotations

import unittest

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions_lsp.documents import line_starts, statement_at, to_position

SYNTAX = POSTGRES.syntax


class TestStatementAt(unittest.TestCase):
    def test_a_lone_statement_is_returned_whole(self) -> None:
        text = 'SELECT id FROM users'
        self.assertEqual(statement_at(text, 9, SYNTAX), (text, 0))

    def test_the_caret_picks_the_second_of_two(self) -> None:
        text = 'SELECT 1;SELECT 2'
        self.assertEqual(statement_at(text, 15, SYNTAX), ('SELECT 2', 9))

    def test_the_terminator_is_not_part_of_the_statement(self) -> None:
        text = 'SELECT 1;SELECT 2'
        self.assertEqual(statement_at(text, 3, SYNTAX), ('SELECT 1', 0))

    def test_a_caret_just_past_a_terminator_belongs_to_what_follows(self) -> None:
        text = 'SELECT 1;SELECT 2'
        self.assertEqual(statement_at(text, 9, SYNTAX), ('SELECT 2', 9))

    def test_whitespace_after_a_terminator_is_kept(self) -> None:
        """Trimming it would shift every offset the engine hands back."""
        text = 'SELECT 1;\nSELECT 2'
        self.assertEqual(statement_at(text, 17, SYNTAX), ('\nSELECT 2', 9))

    def test_a_semicolon_in_a_string_does_not_end_a_statement(self) -> None:
        text = "SELECT ';' AS s FROM users"
        self.assertEqual(statement_at(text, 20, SYNTAX), (text, 0))

    def test_a_semicolon_in_a_comment_does_not_end_a_statement(self) -> None:
        text = 'SELECT 1 -- ; not a boundary\nFROM users'
        self.assertEqual(statement_at(text, 33, SYNTAX), (text, 0))

    def test_a_semicolon_in_a_quoted_identifier_does_not_end_a_statement(self) -> None:
        text = 'SELECT "odd;name" FROM users'
        self.assertEqual(statement_at(text, 22, SYNTAX), (text, 0))

    def test_a_trailing_terminator_leaves_an_empty_statement_after_it(self) -> None:
        text = 'SELECT 1;'
        self.assertEqual(statement_at(text, 9, SYNTAX), ('', 9))

    def test_an_empty_document_is_an_empty_statement(self) -> None:
        self.assertEqual(statement_at('', 0, SYNTAX), ('', 0))

    def test_the_caret_within_the_statement_is_recoverable(self) -> None:
        """The whole reason the base offset is returned at all."""
        text = 'SELECT 1;SELECT na FROM users'
        statement, base = statement_at(text, 18, SYNTAX)
        self.assertEqual(statement[: 18 - base], 'SELECT na')


class TestPositions(unittest.TestCase):
    def test_line_starts_marks_every_line(self) -> None:
        self.assertEqual(line_starts('a\nbb\n\nc'), [0, 2, 5, 6])

    def test_an_offset_on_the_first_line(self) -> None:
        self.assertEqual(to_position(line_starts('SELECT 1'), 7), (0, 7))

    def test_an_offset_on_a_later_line(self) -> None:
        self.assertEqual(to_position(line_starts('SELECT 1\nFROM t'), 11), (1, 2))

    def test_an_offset_at_end_of_input(self) -> None:
        text = 'SELECT 1\n'
        self.assertEqual(to_position(line_starts(text), len(text)), (1, 0))
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/lsp/test_documents.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'pysqlsuggestions_lsp.documents'`.

- [ ] **Step 3: Write the implementation**

`lsp/pysqlsuggestions_lsp/documents.py`:

```python
"""
Where one statement ends and the next begins, and where an offset sits.

`derive_request` builds scope from the whole statement — the FROM answering a
caret in the SELECT list is to the *right* of it — so a document of several
statements must be cut to the one holding the caret. Handing over the whole
document would put every relation in every statement into scope for all of them.

Splitting on the `;` character is wrong: it occurs inside string literals,
comments and quoted identifiers, and which delimiters those are is a property of
the dialect. So this splits on semicolon *tokens*. That is the one place this
package reaches into the engine's internals rather than its API, and it is worth
it: a hand-rolled splitter would be a second, untested, dialect-unaware lexer
whose disagreements with the real one surface as scope silently missing from a
completion list.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

from pysqlsuggestions.dialects.base import Syntax
from pysqlsuggestions.engine.lex import TokenType, lex


def statement_at(text: str, offset: int, syntax: Syntax) -> tuple[str, int]:
    """
    The statement containing `offset`, and where it starts in `text`.

    The caret within the returned statement is `offset - start`. The terminating
    semicolon belongs to neither side, and leading whitespace is kept: trimming
    it would shift every span the engine hands back by an amount the caller
    would have to remember to undo.
    """
    start = 0
    for token in lex(text, syntax):
        if token.type is not TokenType.PUNCT or token.text != ';':
            continue
        if offset <= token.start:
            return text[start : token.start], start
        start = token.end
    return text[start:], start


def line_starts(text: str) -> list[int]:
    """The offset of each line's first character, `[0]` for text with no newline."""
    starts = [0]
    for index, character in enumerate(text):
        if character == '\n':
            starts.append(index + 1)
    return starts


def to_position(starts: Sequence[int], offset: int) -> tuple[int, int]:
    """
    Zero-based `(line, character)` for `offset`, given `line_starts(text)`.

    LSP speaks line and character; every span in this library is an offset. The
    line starts are computed once per request and shared, because a document of
    any size would otherwise be rescanned for each of forty suggestions.
    """
    line = bisect_right(starts, offset) - 1
    return line, offset - starts[line]
```

- [ ] **Step 4: Run to verify they pass**

```bash
uv run pytest tests/lsp/test_documents.py -v
```

Expected: all PASS.

If `test_a_semicolon_in_a_comment_does_not_end_a_statement` fails, check whether `lex` emits the comment as one `COMMENT` token — it should, per the module docstring's guarantee that tokens tile the source. Do not work around it in `documents.py`; a comment token that leaks a semicolon is a lexer bug worth reporting rather than papering over.

- [ ] **Step 5: Commit**

```bash
git add lsp/pysqlsuggestions_lsp/documents.py tests/lsp/test_documents.py
git commit -m "feat: the statement under the caret, cut on tokens rather than characters"
```

---

### Task 3: Suggestions into completion items

Where the engine's work is preserved or thrown away. VS Code re-sorts and re-filters by its own fuzzy score unless told otherwise, and the engine's ranking is the product — many-to-one joins above one-to-many, values by frequency, exact matches above near ones.

One wrinkle found while reading `rank.py`: it matches against `candidate.match_text or candidate.label or candidate.text`, but only `label` survives onto `Suggestion`. So the term the engine matched on has to be reconstructed here rather than read off.

**Files:**
- Create: `lsp/pysqlsuggestions_lsp/convert.py`
- Test: `tests/lsp/test_convert.py`

**Interfaces:**
- Consumes: `documents.to_position`, `documents.line_starts`; `pysqlsuggestions.plan_insertion`; `pysqlsuggestions.types.{Suggestion, Kind, Insertion}`.
- Produces:
  - `to_item(statement: str, base: int, starts: Sequence[int], suggestion: Suggestion, index: int, dialect: Dialect) -> CompletionItem`
  - `match_term(suggestion: Suggestion) -> str`
  - `ITEM_KINDS: dict[Kind, CompletionItemKind]`

- [ ] **Step 1: Write the failing tests**

`tests/lsp/test_convert.py`:

```python
"""Turning a ranked suggestion into an item without losing the ranking."""

from __future__ import annotations

import unittest

from lsprotocol.types import CompletionItemKind, InsertTextFormat
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Kind, Suggestion
from pysqlsuggestions_lsp.convert import match_term, to_item
from pysqlsuggestions_lsp.documents import line_starts


def item(statement: str, suggestion: Suggestion, index: int = 0, base: int = 0):  # type: ignore[no-untyped-def]
    """Convert against a single-line document, which is what most cases need."""
    return to_item(statement, base, line_starts(statement), suggestion, index, POSTGRES)


class TestOrdering(unittest.TestCase):
    def test_sort_text_preserves_the_engine_order(self) -> None:
        """
        The engine ranked these; VS Code must not re-rank them.

        Zero-padded so string comparison and numeric order agree: '10' sorts
        before '9' otherwise, which silently reverses the tail of every list.
        """
        one = item('SELECT ', Suggestion(text='a', kind=Kind.COLUMN, replace_span=(7, 7), score=9.0), index=0)
        ten = item('SELECT ', Suggestion(text='b', kind=Kind.COLUMN, replace_span=(7, 7), score=1.0), index=10)
        assert one.sort_text is not None and ten.sort_text is not None
        self.assertLess(one.sort_text, ten.sort_text)
        self.assertEqual(len(one.sort_text), len(ten.sort_text))


class TestMatchTerm(unittest.TestCase):
    def test_a_qualified_column_is_found_by_its_column_name(self) -> None:
        """`usern` must still find `u.username`, which is the whole point."""
        suggestion = Suggestion(text='u.username', kind=Kind.COLUMN, replace_span=(0, 0), score=1.0)
        self.assertEqual(match_term(suggestion), 'username')

    def test_a_bare_column_is_itself(self) -> None:
        self.assertEqual(match_term(Suggestion(text='id', kind=Kind.COLUMN, replace_span=(0, 0), score=1.0)), 'id')

    def test_a_value_is_found_without_typing_the_quote(self) -> None:
        suggestion = Suggestion(text="'postgres'", kind=Kind.VALUE, replace_span=(0, 0), score=1.0)
        self.assertEqual(match_term(suggestion), 'postgres')

    def test_a_join_is_found_by_its_label(self) -> None:
        """The label leads with the relation name, which is what gets typed."""
        suggestion = Suggestion(
            text='flight f ON b.flight_id = f.id',
            kind=Kind.JOIN,
            replace_span=(0, 0),
            score=1.0,
            label='flight f ON b.flight_id = f.id',
        )
        self.assertEqual(match_term(suggestion), 'flight f ON b.flight_id = f.id')


class TestEdits(unittest.TestCase):
    def test_the_span_becomes_the_edit_range(self) -> None:
        """
        The range comes from replace_span, never from a word boundary.

        Re-deriving it is what drops the qualifier: `where u.crea` accepting
        `created_at` must give `where u.created_at`, not `where created_at`.
        """
        statement = 'SELECT crea FROM t'
        result = item(statement, Suggestion(text='created_at', kind=Kind.COLUMN, replace_span=(7, 11), score=1.0))
        assert result.text_edit is not None
        self.assertEqual(result.text_edit.range.start.character, 7)
        self.assertEqual(result.text_edit.range.end.character, 11)
        self.assertEqual(result.text_edit.new_text, 'created_at')

    def test_the_base_offset_shifts_the_range(self) -> None:
        """A statement after a `;` reports spans relative to itself."""
        document = 'SELECT 1;SELECT crea FROM t'
        statement, base = 'SELECT crea FROM t', 9
        result = to_item(
            statement,
            base,
            line_starts(document),
            Suggestion(text='created_at', kind=Kind.COLUMN, replace_span=(7, 11), score=1.0),
            0,
            POSTGRES,
        )
        assert result.text_edit is not None
        self.assertEqual(result.text_edit.range.start.character, 16)

    def test_a_column_before_a_from_carries_the_clause_as_an_extra_edit(self) -> None:
        """plan_insertion returns two edits; the second is not the primary one."""
        statement = 'SELECT ema'
        suggestion = Suggestion(
            text='auth_user.email',
            kind=Kind.COLUMN,
            replace_span=(7, 10),
            score=1.0,
            relation=('auth_user',),
        )
        result = item(statement, suggestion)
        assert result.additional_text_edits is not None
        self.assertEqual(len(result.additional_text_edits), 1)
        self.assertIn('FROM auth_user', result.additional_text_edits[0].new_text)

    def test_an_ordinary_suggestion_has_no_extra_edits(self) -> None:
        result = item('SELECT ', Suggestion(text='id', kind=Kind.COLUMN, replace_span=(7, 7), score=1.0))
        self.assertFalse(result.additional_text_edits)


class TestSnippets(unittest.TestCase):
    def test_stops_become_snippet_placeholders(self) -> None:
        statement = 'SELECT * FROM booking b JOIN '
        suggestion = Suggestion(
            text='flight f ON b.flight_id = f.id',
            kind=Kind.JOIN,
            replace_span=(29, 29),
            score=1.0,
            stops=(9,),
        )
        result = item(statement, suggestion)
        self.assertEqual(result.insert_text_format, InsertTextFormat.Snippet)
        assert result.text_edit is not None
        self.assertIn('$1', result.text_edit.new_text)

    def test_text_without_stops_is_inserted_literally(self) -> None:
        """A dollar or brace in a value must not be read as a placeholder."""
        suggestion = Suggestion(text="'$1 off'", kind=Kind.VALUE, replace_span=(0, 0), score=1.0)
        result = item("", suggestion)
        self.assertEqual(result.insert_text_format, InsertTextFormat.PlainText)
        assert result.text_edit is not None
        self.assertEqual(result.text_edit.new_text, "'$1 off'")


class TestPresentation(unittest.TestCase):
    def test_each_kind_maps_to_an_item_kind(self) -> None:
        for kind, expected in (
            (Kind.COLUMN, CompletionItemKind.Field),
            (Kind.TABLE, CompletionItemKind.Class),
            (Kind.SCHEMA, CompletionItemKind.Module),
            (Kind.FUNCTION, CompletionItemKind.Function),
            (Kind.KEYWORD, CompletionItemKind.Keyword),
            (Kind.VALUE, CompletionItemKind.Value),
        ):
            suggestion = Suggestion(text='x', kind=kind, replace_span=(0, 0), score=1.0)
            self.assertEqual(item('', suggestion).kind, expected)

    def test_the_note_reaches_the_user(self) -> None:
        """`fk: flight.id` is the teaching part of a ranked list."""
        suggestion = Suggestion(
            text='flight f ON b.flight_id = f.id',
            kind=Kind.JOIN,
            replace_span=(0, 0),
            score=1.0,
            note='fk: flight.id',
        )
        self.assertIn('fk: flight.id', item('', suggestion).detail or '')

    def test_the_label_is_shown_when_the_text_would_read_poorly(self) -> None:
        suggestion = Suggestion(text='count()', kind=Kind.FUNCTION, replace_span=(0, 0), score=1.0, label='count')
        self.assertEqual(item('', suggestion).label, 'count')
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/lsp/test_convert.py -v
```

Expected: collection error — no module `pysqlsuggestions_lsp.convert`.

- [ ] **Step 3: Write the implementation**

`lsp/pysqlsuggestions_lsp/convert.py`:

```python
"""
A ranked suggestion, as an editor's completion item.

Two things are easy to lose here and hard to notice missing.

The first is *order*. A client re-sorts and re-filters by its own fuzzy score
unless every item carries `sortText`, and the engine's ranking is the product:
many-to-one joins above one-to-many, values by frequency, exact matches above
near ones. The list still appears and still holds the right items — silently in
the wrong order.

The second is the *span*. `replace_span` travels with the suggestion precisely so
an editor does not re-derive a word boundary and drop a qualifier, so every item
carries a `textEdit` with an explicit range and never an `insertText`.
"""

from __future__ import annotations

from collections.abc import Sequence

from lsprotocol.types import (
    CompletionItem,
    CompletionItemKind,
    InsertTextFormat,
    Position,
    Range,
    TextEdit,
)
from pysqlsuggestions import plan_insertion
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.types import Edit, Kind, Suggestion

from pysqlsuggestions_lsp.documents import to_position

ITEM_KINDS: dict[Kind, CompletionItemKind] = {
    Kind.COLUMN: CompletionItemKind.Field,
    Kind.TABLE: CompletionItemKind.Class,
    Kind.CTE: CompletionItemKind.Class,
    Kind.SCHEMA: CompletionItemKind.Module,
    Kind.FUNCTION: CompletionItemKind.Function,
    Kind.ALIAS: CompletionItemKind.Variable,
    Kind.KEYWORD: CompletionItemKind.Keyword,
    Kind.OPERATOR: CompletionItemKind.Operator,
    Kind.TYPE: CompletionItemKind.TypeParameter,
    Kind.SNIPPET: CompletionItemKind.Snippet,
    Kind.VALUE: CompletionItemKind.Value,
    Kind.JOIN: CompletionItemKind.Snippet,
}

_SNIPPET_SPECIALS = str.maketrans({'\\': r'\\', '$': r'\$', '}': r'\}'})


def match_term(suggestion: Suggestion) -> str:
    """
    What the user types to find this.

    `rank` matches against `match_text or label or text`, but only `label`
    reaches a `Suggestion`, so the term is reconstructed rather than read. Two
    cases matter: a qualified column is hunted for by its column name — `usern`
    must find `u.username` — and a value is hunted for without its quotes.
    """
    if suggestion.label is not None:
        return suggestion.label
    if suggestion.kind is Kind.VALUE:
        return suggestion.text.strip('\'"')
    return suggestion.text.rsplit('.', 1)[-1]


def _range(base: int, starts: Sequence[int], span: tuple[int, int]) -> Range:
    """A span within the statement, as a range within the document."""
    start = to_position(starts, base + span[0])
    end = to_position(starts, base + span[1])
    return Range(start=Position(line=start[0], character=start[1]), end=Position(line=end[0], character=end[1]))


def _snippet(text: str, stops: Sequence[int]) -> str:
    """
    `text` with a placeholder at each stop, in visiting order.

    Stops are offsets *within* the text, so they are applied last-first: an
    earlier insertion would otherwise move every offset after it. Literal
    dollars and braces are escaped, since a value like `'$1 off'` is not a
    template and must not become one.
    """
    escaped: list[str] = []
    cut = 0
    for index, stop in enumerate(stops, start=1):
        escaped.append(text[cut:stop].translate(_SNIPPET_SPECIALS))
        escaped.append(f'${index}')
        cut = stop
    escaped.append(text[cut:].translate(_SNIPPET_SPECIALS))
    return ''.join(escaped)


def _detail(suggestion: Suggestion) -> str | None:
    """
    What the thing is, and why it outranks its neighbours.

    `detail` says what it is; `note` says why it won — `fk: flight.id`. They are
    separate on the Suggestion and a client has one field, so they are joined
    rather than one being dropped.
    """
    parts = [part for part in (suggestion.detail, suggestion.note) if part]
    return '  '.join(parts) if parts else None


def to_item(
    statement: str,
    base: int,
    starts: Sequence[int],
    suggestion: Suggestion,
    index: int,
    dialect: Dialect,
) -> CompletionItem:
    """
    One suggestion, as an item.

    `index` is its place in the engine's ranking and becomes `sortText`,
    zero-padded so that string order and numeric order agree — unpadded, '10'
    sorts before '9' and the tail of every list quietly reverses.
    """
    plan = plan_insertion(statement, suggestion, dialect=dialect)
    primary, extra = _split_edits(plan.edits, suggestion.replace_span)
    text = _snippet(primary.text, suggestion.stops) if suggestion.stops else primary.text
    return CompletionItem(
        label=suggestion.label or suggestion.text,
        kind=ITEM_KINDS.get(suggestion.kind, CompletionItemKind.Text),
        detail=_detail(suggestion),
        sort_text=f'{index:04d}',
        filter_text=match_term(suggestion),
        text_edit=TextEdit(range=_range(base, starts, primary.span), new_text=text),
        additional_text_edits=[
            TextEdit(range=_range(base, starts, edit.span), new_text=edit.text) for edit in extra
        ],
        insert_text_format=InsertTextFormat.Snippet if suggestion.stops else InsertTextFormat.PlainText,
    )


def _split_edits(edits: Sequence[Edit], span: tuple[int, int]) -> tuple[Edit, list[Edit]]:
    """
    The edit at the caret, and the others.

    A column chosen before any FROM exists produces two, and only the one at the
    suggestion's own span may be the item's `textEdit` — a client applies that
    one at the caret and the rest wherever they say. `plan_insertion` orders them
    latest-first, so the caret's edit is not reliably either end of the tuple.
    """
    primary = next((edit for edit in edits if edit.span[0] == span[0]), edits[0])
    return primary, [edit for edit in edits if edit is not primary]
```

- [ ] **Step 4: Run to verify they pass**

```bash
uv run pytest tests/lsp/test_convert.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run the whole check**

```bash
./scripts/check.sh
```

Expected: passes. `to_item` takes six parameters, which ruff does not object to but is worth noticing — if a seventh appears, the request context wants to be a small frozen dataclass rather than a longer signature.

- [ ] **Step 6: Commit**

```bash
git add lsp/pysqlsuggestions_lsp/convert.py tests/lsp/test_convert.py
git commit -m "feat: suggestions become items, and keep the order they were ranked in"
```

---

### Task 4: Profiles, dialects and drivers

Turning a connection profile into a `DbapiCatalog`. The dialect comes from the entry-point registry, so a third-party dialect works here with no change. The driver is chosen by dialect name, and connecting is deferred to the first query — `DbapiCatalog` calls `open_cursor` per query, so a warm cache means an editor session touches the database not at all.

**Files:**
- Create: `lsp/pysqlsuggestions_lsp/connections.py`
- Test: `tests/lsp/test_connections.py`

**Interfaces:**
- Consumes: `pysqlsuggestions.dialects.registry.named`, `pysqlsuggestions.catalogs.dbapi.DbapiCatalog`.
- Produces:
  - `Profile` — frozen dataclass: `dialect: str`, `host: str`, `port: int | None`, `database: str | None`, `user: str | None`, `password: str | None`.
  - `Profile.from_options(options: object) -> Profile | None`
  - `DRIVERS: dict[str, tuple[str, str]]` — dialect name → (module, paramstyle).
  - `open_catalog(profile: Profile, connect: Connect | None = None) -> DbapiCatalog | None` — None when the dialect or driver is unknown.
  - `Connect = Callable[[Profile], Any]`

- [ ] **Step 1: Write the failing tests**

`tests/lsp/test_connections.py`:

```python
"""Profile to catalog, without a database in sight."""

from __future__ import annotations

import unittest
from typing import Any

from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions_lsp.connections import DRIVERS, Profile, open_catalog

PROFILE = Profile(dialect='postgres', host='localhost', port=5432, database='app', user='ana', password='secret')


class FakeCursor:
    """Answers every query with nothing, which is a valid catalog answer."""

    def execute(self, operation: str, parameters: Any = None) -> None:
        self.last = (operation, parameters)

    def fetchall(self) -> list[Any]:
        return []


class FakeConnection:
    def __init__(self) -> None:
        self.cursors = 0

    def cursor(self) -> FakeCursor:
        self.cursors += 1
        return FakeCursor()


class TestProfile(unittest.TestCase):
    def test_options_become_a_profile(self) -> None:
        options = {'dialect': 'postgres', 'host': 'db', 'port': 5432, 'database': 'app', 'user': 'ana'}
        profile = Profile.from_options(options)
        assert profile is not None
        self.assertEqual((profile.dialect, profile.host, profile.port), ('postgres', 'db', 5432))

    def test_options_without_a_dialect_are_no_profile(self) -> None:
        """No dialect means no catalog, which is the documented degraded mode."""
        self.assertIsNone(Profile.from_options({'host': 'db'}))

    def test_no_options_at_all_are_no_profile(self) -> None:
        self.assertIsNone(Profile.from_options(None))

    def test_a_profile_does_not_print_its_password(self) -> None:
        """repr reaches logs and crash reports."""
        self.assertNotIn('secret', repr(PROFILE))


class TestOpenCatalog(unittest.TestCase):
    def test_a_known_dialect_gives_a_catalog(self) -> None:
        catalog = open_catalog(PROFILE, connect=lambda profile: FakeConnection())
        self.assertIsInstance(catalog, DbapiCatalog)

    def test_an_unknown_dialect_gives_nothing(self) -> None:
        profile = Profile(dialect='oracle', host='db', port=None, database=None, user=None, password=None)
        self.assertIsNone(open_catalog(profile, connect=lambda p: FakeConnection()))

    def test_connecting_is_deferred_until_a_query(self) -> None:
        """
        Opening a document must not open a socket.

        A database behind a VPN that happens to be down would otherwise hang the
        editor on file open rather than on the first completion.
        """
        opened: list[Profile] = []

        def connect(profile: Profile) -> FakeConnection:
            opened.append(profile)
            return FakeConnection()

        catalog = open_catalog(PROFILE, connect=connect)
        assert catalog is not None
        self.assertEqual(opened, [])
        catalog.tables()
        self.assertEqual(opened, [PROFILE])

    def test_the_connection_is_reused_across_queries(self) -> None:
        connections: list[FakeConnection] = []

        def connect(profile: Profile) -> FakeConnection:
            connections.append(FakeConnection())
            return connections[-1]

        catalog = open_catalog(PROFILE, connect=connect)
        assert catalog is not None
        catalog.tables()
        catalog.schemas()
        self.assertEqual(len(connections), 1)

    def test_every_declared_driver_names_a_paramstyle_dbapi_accepts(self) -> None:
        """render() raises on anything else, from inside a catalog read."""
        for module, paramstyle in DRIVERS.values():
            self.assertIn(paramstyle, ('qmark', 'format', 'numeric', 'named', 'pyformat'), module)
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/lsp/test_connections.py -v
```

Expected: collection error — no module `pysqlsuggestions_lsp.connections`.

- [ ] **Step 3: Write the implementation**

`lsp/pysqlsuggestions_lsp/connections.py`:

```python
"""
A connection profile, as a catalog.

The dialect comes from the entry-point registry rather than a hard-coded map, so
a third-party dialect works here without this file knowing it exists. The driver
does not, because a driver is a module to import and a paramstyle to declare.

Both bundled drivers are pure Python. That is not an accident: it is what lets
one VSIX serve every platform instead of one per platform and architecture.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from pysqlsuggestions.catalogs.dbapi import Cursor, DbapiCatalog
from pysqlsuggestions.dialects.registry import named

Connect = Callable[['Profile'], Any]

DRIVERS: dict[str, tuple[str, str]] = {
    'postgres': ('pg8000.dbapi', 'format'),
    'trino': ('trino.dbapi', 'qmark'),
}
"""Dialect name to (driver module, paramstyle). Pure-Python drivers only."""


@dataclass(frozen=True, slots=True)
class Profile:
    """Where to connect, and as whom."""

    dialect: str
    host: str
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    """Kept out of `repr` — this object reaches logs and crash reports."""

    @classmethod
    def from_options(cls, options: object) -> Profile | None:
        """
        A profile from a client's `initializationOptions`, or None.

        None rather than a raise: no profile is the documented degraded mode,
        where completion answers from the statement alone. A client that sent
        nothing gets a working server, not a failed one.
        """
        if not isinstance(options, dict):
            return None
        dialect = options.get('dialect')
        host = options.get('host')
        if not isinstance(dialect, str) or not isinstance(host, str):
            return None
        port = options.get('port')
        return cls(
            dialect=dialect,
            host=host,
            port=port if isinstance(port, int) else None,
            database=_text(options.get('database')),
            user=_text(options.get('user')),
            password=_text(options.get('password')),
        )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _connect(profile: Profile) -> Any:
    """Open a connection with the driver the dialect names."""
    module, _ = DRIVERS[profile.dialect]
    driver = import_module(module)
    arguments: dict[str, Any] = {'host': profile.host}
    for name, value in (
        ('port', profile.port),
        ('database', profile.database),
        ('user', profile.user),
        ('password', profile.password),
    ):
        if value is not None:
            arguments[name] = value
    return driver.connect(**arguments)


def open_catalog(profile: Profile, connect: Connect | None = None) -> DbapiCatalog | None:
    """
    A catalog for `profile`, or None when nothing here can serve it.

    Nothing is connected yet. `DbapiCatalog` calls `open_cursor` per query, so
    the socket opens on the first catalog read and a warm cache means an editor
    session touches the database not at all. Opening a document must never open
    a connection: a database behind a VPN that happens to be down would hang the
    editor on file open rather than on a completion the user asked for.
    """
    dialect = named(profile.dialect)
    if dialect is None or profile.dialect not in DRIVERS:
        return None
    _, paramstyle = DRIVERS[profile.dialect]
    opener = connect or _connect
    held: list[Any] = []

    def open_cursor() -> Cursor:
        if not held:
            held.append(opener(profile))
        cursor: Cursor = held[0].cursor()
        return cursor

    return DbapiCatalog(open_cursor, dialect, paramstyle=paramstyle)
```

- [ ] **Step 4: Run to verify they pass**

```bash
uv run pytest tests/lsp/test_connections.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lsp/pysqlsuggestions_lsp/connections.py tests/lsp/test_connections.py
git commit -m "feat: a profile becomes a catalog, and connects no sooner than it must"
```

---

### Task 5: The server

Wiring the three modules to LSP. The governing rule from the spec: **a completion request never fails.** Every failure degrades to catalog-free completion, which is a genuinely useful mode — keywords, CTE columns, select-list names, aliases — rather than an error arriving mid-keystroke.

**Files:**
- Create: `lsp/pysqlsuggestions_lsp/server.py`, `lsp/pysqlsuggestions_lsp/__main__.py`
- Test: `tests/lsp/test_server.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `create_server(connect: Connect | None = None) -> LanguageServer`, `main() -> None`.

- [ ] **Step 1: Write the failing test**

`tests/lsp/test_server.py`:

```python
"""The handlers, driven directly rather than over a pipe."""

from __future__ import annotations

import unittest
from typing import Any

from lsprotocol.types import (
    CompletionParams,
    DidOpenTextDocumentParams,
    Position,
    TextDocumentIdentifier,
    TextDocumentItem,
)
from pysqlsuggestions_lsp.server import completion, create_server

URI = 'file:///query.sql'


def opened(server: Any, text: str) -> None:
    """Put a document into the server's workspace."""
    item = TextDocumentItem(uri=URI, language_id='sql', version=1, text=text)
    server.workspace.put_text_document(item)
    del DidOpenTextDocumentParams


def ask(server: Any, line: int, character: int) -> list[Any]:
    params = CompletionParams(
        text_document=TextDocumentIdentifier(uri=URI),
        position=Position(line=line, character=character),
    )
    result = completion(server, params)
    return list(result.items)


class TestCompletionWithoutACatalog(unittest.TestCase):
    """No profile was sent, so only what the statement itself describes is offered."""

    def setUp(self) -> None:
        self.server = create_server()

    def test_a_cte_name_is_offered_from_the_statement_alone(self) -> None:
        opened(self.server, 'WITH recent AS (SELECT 1) SELECT * FROM rec')
        labels = [item.label for item in ask(self.server, 0, 43)]
        self.assertIn('recent', labels)

    def test_items_arrive_in_the_engines_order(self) -> None:
        opened(self.server, 'WITH recent AS (SELECT 1) SELECT * FROM rec')
        items = ask(self.server, 0, 43)
        self.assertEqual([item.sort_text for item in items], sorted(item.sort_text for item in items))

    def test_the_caret_in_the_second_statement_does_not_see_the_first(self) -> None:
        """
        Scope is per statement.

        Handing the engine the whole document would put `alpha` in scope for a
        caret in a statement that never mentions it.
        """
        opened(self.server, 'SELECT * FROM alpha;\nSELECT * FROM b')
        labels = [item.label for item in ask(self.server, 1, 15)]
        self.assertNotIn('alpha', labels)

    def test_an_empty_document_answers_without_raising(self) -> None:
        opened(self.server, '')
        self.assertIsInstance(ask(self.server, 0, 0), list)


class TestDegradation(unittest.TestCase):
    def test_a_catalog_that_raises_still_answers(self) -> None:
        """
        A completion request never fails.

        An unreachable database degrades to what the statement describes. The
        alternative is an error popup arriving on a keystroke.
        """

        def connect(profile: Any) -> Any:
            message = 'connection refused'
            raise OSError(message)

        server = create_server(connect=connect)
        server.profile = __import__(
            'pysqlsuggestions_lsp.connections', fromlist=['Profile']
        ).Profile(dialect='postgres', host='nowhere')
        server.catalog = None
        opened(server, 'WITH recent AS (SELECT 1) SELECT * FROM rec')
        labels = [item.label for item in ask(server, 0, 43)]
        self.assertIn('recent', labels)
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/lsp/test_server.py -v
```

Expected: collection error — no module `pysqlsuggestions_lsp.server`.

- [ ] **Step 3: Write the implementation**

`lsp/pysqlsuggestions_lsp/server.py`:

```python
"""
The server.

One rule governs every handler: a completion request never fails. The library
degrades by design — `resolve.py` implements it so no adapter has to — so an
unreachable database, a rejected password or an unknown dialect all fall back to
what the statement itself describes: keywords, CTE columns, select-list names,
aliases. That is a useful answer. An error popup on a keystroke is not.

One connection per process. The profile arrives in `initializationOptions` and
changing it restarts the server, which is cheap, discards a warm cache only on a
deliberate and rare action, and removes every bug where a server holds state
from a connection it no longer has.
"""

from __future__ import annotations

import logging
from typing import Any

from lsprotocol.types import (
    TEXT_DOCUMENT_COMPLETION,
    CompletionList,
    CompletionOptions,
    CompletionParams,
    InitializeParams,
)
from pygls.server import LanguageServer
from pysqlsuggestions import DEFAULT_LIMIT, complete
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.registry import named

from pysqlsuggestions_lsp import __version__
from pysqlsuggestions_lsp.connections import Connect, Profile, open_catalog
from pysqlsuggestions_lsp.convert import to_item
from pysqlsuggestions_lsp.documents import line_starts, statement_at

log = logging.getLogger(__name__)

TRIGGERS = ['.', ' ', ',', '(']
"""A dot continues a reference; the rest open a position where something is wanted."""


class SqlServer(LanguageServer):
    """A language server holding one connection's worth of state."""

    def __init__(self, connect: Connect | None = None) -> None:
        super().__init__(name='pysqlsuggestions', version=__version__)
        self.connect = connect
        self.profile: Profile | None = None
        self.catalog: Any = None
        self.cache: dict[Any, Any] = {}

    @property
    def dialect(self) -> Dialect:
        """
        The configured dialect, or ANSI.

        ANSI is not a failure state: an unknown backend degrades rather than
        breaking, and the shipped fallback exists for exactly this.
        """
        if self.profile is None:
            return ANSI
        return named(self.profile.dialect) or ANSI

    def catalog_for_request(self) -> Any:
        """
        The catalog, built on first use, or None once building it has failed.

        Failure is recorded rather than retried per keystroke: a database that
        is down stays down for the length of a coffee, and one log line beats
        forty a minute.
        """
        if self.catalog is not None or self.profile is None:
            return self.catalog
        try:
            self.catalog = open_catalog(self.profile, connect=self.connect)
        except Exception:  # noqa: BLE001
            log.exception('could not open a catalog; completing from the statement alone')
            self.profile = None
        return self.catalog


def create_server(connect: Connect | None = None) -> SqlServer:
    """A server with its handlers registered. `connect` is for tests."""
    server = SqlServer(connect=connect)
    server.feature(TEXT_DOCUMENT_COMPLETION, CompletionOptions(trigger_characters=TRIGGERS))(completion)
    server.lsp.fm.add_builtin_feature('initialize', initialize)
    return server


def initialize(server: SqlServer, params: InitializeParams) -> None:
    """Record the profile. Nothing is connected here."""
    server.profile = Profile.from_options(params.initialization_options)
    if server.profile is None:
        log.info('no connection profile; completing from the statement alone')


def completion(server: SqlServer, params: CompletionParams) -> CompletionList:
    """
    Suggestions for the caret.

    Never raises: every failure below degrades to a catalog-free answer, and a
    catalog-free answer is still keywords, CTE columns and aliases.
    """
    document = server.workspace.get_text_document(params.text_document.uri)
    text = document.source
    offset = document.offset_at_position(params.position)
    dialect = server.dialect
    statement, base = statement_at(text, offset, dialect.syntax)
    starts = line_starts(text)
    try:
        suggestions = complete(
            statement,
            offset - base,
            dialect,
            server.catalog_for_request(),
            cache=server.cache,
            identity=server.profile.user if server.profile else None,
            limit=DEFAULT_LIMIT,
        )
    except Exception:  # noqa: BLE001
        log.exception('the catalog failed; completing from the statement alone')
        server.catalog = None
        server.profile = None
        suggestions = complete(statement, offset - base, dialect)
    items = [to_item(statement, base, starts, s, index, dialect) for index, s in enumerate(suggestions)]
    return CompletionList(is_incomplete=False, items=items)
```

`lsp/pysqlsuggestions_lsp/__main__.py`:

```python
"""`python -m pysqlsuggestions_lsp` — the server on stdio."""

from __future__ import annotations

from pysqlsuggestions_lsp.server import create_server


def main() -> None:
    """Serve on stdin and stdout until the client goes away."""
    create_server().start_io()


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run to verify they pass**

```bash
uv run pytest tests/lsp/test_server.py -v
```

Expected: all PASS.

pygls' registration API has moved between major versions. If `add_builtin_feature` or `server.feature(...)` as used above does not exist in the installed pygls, check its version with `uv run python -c "import pygls; print(pygls.__version__)"` and consult its docs — register `initialize` by whatever mechanism that version documents, and keep the handler bodies as written. The behaviour under test is the handlers, not the registration.

- [ ] **Step 5: Drive it over a real pipe, by hand**

```bash
uv run python -m pysqlsuggestions_lsp < /dev/null
```

Expected: exits without a traceback. This confirms the entry point imports and starts; the handlers are covered by the tests above.

- [ ] **Step 6: Run the whole check and commit**

```bash
./scripts/check.sh
git add lsp/pysqlsuggestions_lsp/server.py lsp/pysqlsuggestions_lsp/__main__.py tests/lsp/test_server.py
git commit -m "feat: a server that answers, and answers something when the database will not"
```

---

### Task 6: End to end against a real database

Everything so far runs without a database. This is the test that would catch a `pg8000` paramstyle mismatch, a dialect query that fails against a real server, or a join proposal that never arrives — and it is the first time the pg8000 path runs at all.

**Files:**
- Create: `tests/integration/test_lsp_backends.py`

**Interfaces:**
- Consumes: `create_server`, `Profile`; the existing docker Postgres and `tests/integration/conftest.py`.
- Produces: nothing.

- [ ] **Step 1: Read how the existing integration tests connect**

```bash
sed -n '1,60p' tests/integration/conftest.py
sed -n '1,50p' tests/integration/test_backends.py
```

Note the marker, the fixture names, and the Postgres DSN or connection arguments in use. The test below must reach the same server the existing suite does. Adjust host, port, user, password and database in `PROFILE` to match what you find rather than trusting the values written here.

- [ ] **Step 2: Write the failing test**

`tests/integration/test_lsp_backends.py`:

```python
"""The server against a real Postgres, over the pg8000 driver.

Everything else in `tests/lsp/` runs against fakes, so this is the only place a
paramstyle mismatch, a dialect query the server rejects, or a driver that reports
rows differently would be caught.
"""

from __future__ import annotations

import unittest

import pytest
from lsprotocol.types import CompletionParams, Position, TextDocumentIdentifier, TextDocumentItem
from pysqlsuggestions_lsp.connections import Profile
from pysqlsuggestions_lsp.server import completion, create_server

URI = 'file:///query.sql'

# Match tests/integration/conftest.py; see step 1.
PROFILE = Profile(
    dialect='postgres',
    host='localhost',
    port=55432,
    database='reports',
    user='pysqlsuggestions',
    password='pysqlsuggestions',
)


@pytest.mark.integration
class TestAgainstPostgres(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server()
        self.server.profile = PROFILE

    def _items(self, text: str, line: int, character: int) -> list[str]:
        item = TextDocumentItem(uri=URI, language_id='sql', version=1, text=text)
        self.server.workspace.put_text_document(item)
        params = CompletionParams(
            text_document=TextDocumentIdentifier(uri=URI),
            position=Position(line=line, character=character),
        )
        return [item.label for item in completion(self.server, params).items]

    def test_columns_come_from_the_database(self) -> None:
        text = 'SELECT * FROM auth_user u WHERE u.'
        self.assertTrue(self._items(text, 0, len(text)))

    def test_a_qualifier_collapses_the_answer_to_columns(self) -> None:
        """No keywords, no functions, no tables — the README's own example."""
        text = 'SELECT * FROM auth_user u WHERE u.'
        self.assertNotIn('SELECT', self._items(text, 0, len(text)))

    def test_the_pg8000_path_reads_the_catalog_at_all(self) -> None:
        """
        A paramstyle mismatch surfaces here and nowhere else.

        `render()` doubles literal `%` for format-style drivers, and the
        introspection SQL is full of them. Getting this wrong raises an opaque
        IndexError from inside the driver.
        """
        catalog = self.server.catalog_for_request()
        assert catalog is not None
        self.assertTrue(catalog.tables())
```

- [ ] **Step 3: Start the backends and run it**

```bash
docker compose -f docker/docker-compose.yml up -d --wait
uv run pytest tests/integration/test_lsp_backends.py -v -m integration
```

Expected: FAIL first if the profile values are wrong — fix them from step 1 rather than guessing. Then PASS.

If `test_the_pg8000_path_reads_the_catalog_at_all` raises `IndexError` from inside pg8000, the paramstyle in `DRIVERS` is wrong. pg8000's DB-API module reports its own `paramstyle`; check it with `uv run python -c "import pg8000.dbapi; print(pg8000.dbapi.paramstyle)"` and set `DRIVERS['postgres']` to match.

- [ ] **Step 4: Confirm the unit suite still runs without docker**

```bash
uv run pytest -m 'not integration' -q
```

Expected: PASS, and the new integration test is deselected.

- [ ] **Step 5: Run the whole check and commit**

```bash
./scripts/check.sh
git add tests/integration/test_lsp_backends.py
git commit -m "test: the server against a real Postgres, over a driver nothing else exercises"
```

---

### Task 7: Say it exists

A README nobody updated is how a feature stays undiscovered. The repo's own README carries every other capability; this belongs there too.

**Files:**
- Create: `lsp/README.md`
- Modify: `README.md`, `CHANGELOG.md`

- [ ] **Step 1: Write `lsp/README.md`**

```markdown
# pysqlsuggestions-lsp

A language server over `pysqlsuggestions`. The library stays free of it: this is
an adapter beside `demo/`, not a layer inside `src/`.

```bash
uv run python -m pysqlsuggestions_lsp
```

Speaks LSP on stdio. The connection profile arrives in `initializationOptions`:

```json
{ "dialect": "postgres", "host": "localhost", "port": 5432, "database": "app", "user": "ana", "password": "…" }
```

Without one it completes from the statement alone — keywords, CTE columns,
select-list names, aliases — which is a useful degraded mode rather than an
error. The database is not contacted until the first completion request, and a
failure to reach it degrades to that same mode rather than failing the request.

One connection per process. Changing the profile means restarting the server.

Drivers are pure Python by design — `pg8000` for Postgres, `trino` for Trino —
so the wheels are platform-independent. ClickHouse is not yet served here; its
driver is not pure Python.
```

- [ ] **Step 2: Add a section to the root `README.md`**

Place it after the "Browser demo" section and before "Design":

```markdown
## In an editor

The engine speaks LSP, so any client can drive it:

```bash
uv run python -m pysqlsuggestions_lsp
```

The connection profile arrives in `initializationOptions`, the database is not
contacted until the first completion request, and an unreachable one degrades to
completing from the statement alone rather than failing the request. See
`lsp/README.md`.
```

- [ ] **Step 3: Add a CHANGELOG entry**

Follow the existing format at the top of `CHANGELOG.md` — read the most recent entry first and match its structure and voice.

- [ ] **Step 4: Verify the README's command actually works**

```bash
uv run python -m pysqlsuggestions_lsp < /dev/null
```

Expected: exits cleanly. A README command that does not run is worse than no README.

- [ ] **Step 5: Commit**

```bash
git add README.md lsp/README.md CHANGELOG.md
git commit -m "docs: the engine speaks LSP, and the README says so"
```

---

## Self-Review

**Spec coverage.** Walking §5 of the spec: the `lsp/` package and its five modules are Tasks 1–5; statement splitting via `engine.lex` is Task 2; one connection per process is Task 4 and the `initialize` handler in Task 5; lazy connection is Task 4, tested explicitly; `sortText`/`filterText` and the Kind map are Task 3. §7's error table is Task 5's degradation path plus Task 4's `from_options` returning None — the rows about `python3`, venv creation and client restart belong to the extension and are plan 2. §8's structural guards are Task 1; the pure-module tests are Tasks 2–4; the docker end-to-end and pg8000 coverage are Task 6. §4's pg8000 decision appears as the extra in Task 1 and the `DRIVERS` map in Task 4; wheel pinning is plan 2, since nothing is bundled yet.

**Gap found and closed:** the spec's §5 note that `convert.py` depends on "pysqlsuggestions types" understates it — it also needs `lsprotocol`. Task 3's interface block says so rather than leaving an implementer to discover it.

**Second gap found and closed:** `rank` matches on `match_text or label or text` but only `label` reaches a `Suggestion`, so `filterText` cannot simply read the term the engine matched. Task 3 reconstructs it in `match_term` and tests all four cases. If join filtering proves wrong in practice, the real fix is carrying `match_text` onto `Suggestion` — a library change, deliberately not made here.

**Placeholder scan:** no TBD, no "add error handling", no "similar to Task N". Every code step carries the code. Task 6 step 1 asks the implementer to read the existing conftest rather than hard-coding connection values — that is a real instruction with a stated reason, not a placeholder.

**Type consistency:** `statement_at` returns `(str, int)` and is consumed that way in Task 5. `to_position` takes `(starts, offset)` and returns `(int, int)` in both Task 2 and Task 3. `open_catalog(profile, connect=...)` matches its use in Task 5's `catalog_for_request`. `Profile.from_options` returns `Profile | None` and every caller handles None. `to_item`'s six parameters are identical in Tasks 3, 5 and 6.

**Known soft spot, stated rather than hidden:** Task 5 registers the `initialize` handler through a pygls API that differs across pygls majors. The step says so and tells the implementer to consult the installed version's docs, because inventing a version-specific incantation here would be a plan asserting something it cannot check.
