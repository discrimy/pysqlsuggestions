# Statement forms beyond DML — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `DROP TABLE ⌶` proposing `SELECT`, offer relations at the DDL positions that want one, and make every statement form the engine does not model say nothing.

**Architecture:** Two changes that compose. Three DDL clauses and an `EXPLAIN` clause join the ANSI model, which is data — no new machinery. Then one predicate splits `clause is None` into "no statement has started" (keep the empty-editor answer) and "this form is unknown" (answer nothing).

**Tech Stack:** Python 3.10+, no runtime dependencies. pytest, ruff, mypy strict. `uv` runs everything. Docker for the integration suite.

Implements `docs/superpowers/specs/2026-08-12-statement-forms-beyond-dml-design.md`.

**Task order is a dependency order, not the spec's reading order.** The refusal (Task 3) would silence `EXPLAIN ⌶`, which works today by accident, so `EXPLAIN` is modelled first. Every task ends with a green suite and behaviour no worse than the commit before it.

## Global Constraints

- **Python 3.10 floor.** No `*` unpacking directly inside a subscript, no `match`.
- **Zero runtime dependencies** under `src/pysqlsuggestions/`.
- **`engine/` stays pure.** No module under `src/pysqlsuggestions/engine/` may import `pysqlsuggestions.ports` or `pysqlsuggestions.resolve`; `tests/test_purity.py` enforces it.
- **Line length 120. Single quotes.** `ruff format` with `quote-style = 'single'`.
- **Every public function, class and module needs a docstring** — ruff's `D` rules are on. House style: a one-line summary, then, where the decision was not obvious, a paragraph naming the failure the code prevents.
- **mypy strict** over `src`, `tests` and `lsp`.
- **A clause name is uppercase** and may contain single spaces. `DialectConformance.structure` fails a dialect whose clause name is not uppercase, and whose `statement_start` phrase names no declared clause.
- **Run tests with** `uv run pytest`; lint `uv run ruff check .` and `uv run ruff format --check .`; types `uv run mypy`.
- **Integration tests need docker:** `docker compose -f docker/docker-compose.yml up -d --wait`.
- **Commit after every task.** Message style `type: lowercase phrase`, saying what changed for a reader.

---

## File Structure

**Modified:**

- `src/pysqlsuggestions/dialects/ansi.py` — `EXPLAINABLE`, the four clauses, `STATEMENT_START`.
- `src/pysqlsuggestions/dialects/postgres.py` — `EXPLAIN`'s `before_the_item`.
- `src/pysqlsuggestions/engine/analyse.py` — `statement_has_begun`.
- `src/pysqlsuggestions/engine/request.py` — the blanking in `derive_request`.
- `tests/corpus/cases.py`, `tests/integration/test_acceptance.py`.
- `docs/gaps.md`, `CHANGELOG.md`.

**Created:**

- `tests/test_statement_forms.py` — the refusal's boundary cases and the four clauses.

---

### Task 1: `EXPLAIN` becomes a clause it was only pretending to be

**Files:**
- Modify: `src/pysqlsuggestions/dialects/ansi.py`
- Modify: `src/pysqlsuggestions/dialects/postgres.py`
- Create: `tests/test_statement_forms.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ansi.EXPLAINABLE: tuple[str, ...]`; a clause named `EXPLAIN` in `ANSI.clauses`.

`EXPLAIN`, `EXPLAIN ANALYZE` and `EXPLAIN (FORMAT JSON)` all behave correctly today because an unrecognised leading word is skipped. Task 3 removes that accident, so this pins the behaviour first and makes it deliberate.

- [ ] **Step 1: Write the failing test**

Create `tests/test_statement_forms.py`:

```python
"""
Statement forms this engine does not have a clause model for.

`DROP TABLE ⌶` offered `SELECT`, because no clause matched and no clause meant
the empty-editor position — the words a statement may *begin* with, inside a
statement that had already begun. Accepting one wrote `DROP TABLE SELECT`.

Two rules answer it. The forms whose answer is a relation are modelled, and a
form the engine does not recognise says nothing at all.
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
    """Two relations, which is all any of these positions needs."""
    return MemoryCatalog(SNAPSHOT)


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def clause_at_end(sql: str) -> str | None:
    """The clause the engine believes governs the end of `sql`."""
    return derive_request(sql, len(sql), POSTGRES).clause


def test_explain_is_a_clause_rather_than_an_unrecognised_word() -> None:
    """It behaved correctly by being skipped, which is not the same as being understood."""
    assert clause_at_end('EXPLAIN ') == 'EXPLAIN'


def test_explain_offers_the_statements_it_can_explain() -> None:
    """A query, not a DROP: `EXPLAIN DROP TABLE users` is a syntax error."""
    found = offered('EXPLAIN ')
    assert 'SELECT' in found
    assert 'WITH' in found
    assert 'INSERT INTO' in found


def test_explain_still_analyses_the_statement_inside_it() -> None:
    """The inner statement is the one being completed, and it always was."""
    assert offered('EXPLAIN SELECT * FROM users u WHERE u.') == ['id', 'email']


def test_explain_analyze_still_analyses_the_statement_inside_it() -> None:
    """A modifier between EXPLAIN and its statement must not break the scope."""
    assert offered('EXPLAIN ANALYZE SELECT * FROM users u WHERE u.') == ['id', 'email']


def test_explain_with_options_still_analyses_the_statement_inside_it() -> None:
    """`EXPLAIN (FORMAT JSON) …` puts a parenthesised group in the way."""
    assert offered('EXPLAIN (FORMAT JSON) SELECT * FROM users u WHERE u.') == ['id', 'email']


def test_postgres_offers_its_own_explain_modifiers() -> None:
    """
    ANALYZE stands between EXPLAIN and its statement, which is what
    `before_the_item` means — the same field that keeps DISTINCT out of the
    middle of a select list. Offered behind a prefix only, like DISTINCT.
    """
    assert 'ANALYZE' in offered('EXPLAIN ana')
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_statement_forms.py -v`
Expected: `test_explain_is_a_clause_rather_than_an_unrecognised_word` FAILS (`assert None == 'EXPLAIN'`) and `test_postgres_offers_its_own_explain_modifiers` FAILS. The other four PASS — they are pinning behaviour that already holds, which is the point of writing them now.

- [ ] **Step 3: Name the explainable statements in `ansi.py`**

Replace the `STATEMENT_START` line:

```python
EXPLAINABLE = ('SELECT', 'WITH', 'INSERT INTO', 'UPDATE', 'DELETE FROM')
"""
The statement forms a query planner will accept.

Named separately from `STATEMENT_START` because `EXPLAIN` takes these and not
the DDL forms below — `EXPLAIN DROP TABLE users` is a syntax error. Written this
way round, adding a statement form later cannot silently start offering it after
`EXPLAIN`.
"""

STATEMENT_START = EXPLAINABLE
```

`STATEMENT_START` gains its DDL entries in Task 2; keeping it equal to
`EXPLAINABLE` here is what makes this task a no-op for every other position.

- [ ] **Step 4: Add the clause**

In the `CLAUSES = ClauseModel(clauses=(...))` tuple, after the `EXCEPT` entry:

```python
        # A wrapper rather than a statement: it takes one and reports on it.
        # Deliberately absent from `statement_start` — `statement_form` returns
        # the first start that is not WITH, so an EXPLAIN'd query would report
        # its form as EXPLAIN and lose every clause declaring `statements`
        # {'SELECT'}: GROUP BY, ORDER BY, LIMIT.
        Clause(name='EXPLAIN', suggests=(Kind.SNIPPET, Kind.KEYWORD), followed_by=EXPLAINABLE),
```

- [ ] **Step 5: Give Postgres its modifiers**

In `dialects/postgres.py`, inside `ANSI.clauses.extend(...)`:

```python
        # ANALYZE and VERBOSE stand between EXPLAIN and its statement, which is
        # what `before_the_item` means. `followed_by` would offer them after the
        # statement, where they cannot go.
        Clause(
            name='EXPLAIN',
            suggests=(Kind.SNIPPET, Kind.KEYWORD),
            followed_by=EXPLAINABLE,
            before_the_item=('ANALYZE', 'VERBOSE'),
        ),
```

`ClauseModel.extend` replaces an entry of the same name rather than appending,
so this supersedes ANSI's, and `followed_by` has to be restated. Add
`EXPLAINABLE` to the existing
`from pysqlsuggestions.dialects.ansi import ANSI, COLUMN_EXPRESSION` line — the
constant is public for exactly this reason, as `COLUMN_EXPRESSION` already is.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_statement_forms.py -v`
Expected: all six PASS.

- [ ] **Step 7: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all green. Nothing else should move — `EXPLAIN` was invisible to the clause model before and is now a clause nothing else references.

- [ ] **Step 8: Commit**

```bash
git add src/pysqlsuggestions/dialects/ tests/test_statement_forms.py
git commit -m "feat: EXPLAIN is a clause it was only pretending to be"
```

---

### Task 2: The three forms that want a relation

**Files:**
- Modify: `src/pysqlsuggestions/dialects/ansi.py`
- Test: `tests/test_statement_forms.py`

**Interfaces:**
- Consumes: `EXPLAINABLE` and `STATEMENT_START` from Task 1.
- Produces: clauses named `DROP TABLE`, `TRUNCATE`, `ALTER TABLE`; `STATEMENT_START` extended with all three.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statement_forms.py`:

```python
def test_drop_table_offers_relations() -> None:
    """It offered `SELECT`, and accepting wrote `DROP TABLE SELECT`."""
    found = offered('DROP TABLE ')
    assert 'users' in found
    assert 'orders' in found
    assert 'SELECT' not in found


def test_truncate_offers_relations() -> None:
    """Postgres allows the bare form; the ANSI `TRUNCATE TABLE` spelling also works."""
    assert 'users' in offered('TRUNCATE ')
    assert 'users' in offered('TRUNCATE TABLE ')


def test_alter_table_offers_relations() -> None:
    """The relation comes first whatever the alteration turns out to be."""
    assert 'users' in offered('ALTER TABLE ')


def test_drop_offers_the_word_that_finishes_it() -> None:
    """
    Derived from the clause name by `_half_written_clauses`, the same way
    `GROUP ⌶` offers `BY`. No entry of its own.
    """
    assert offered('DROP ') == ['TABLE']


def test_a_written_relation_is_not_followed_by_another() -> None:
    """
    `DROP TABLE users orders` parses as nothing. The clause's `followed_by` is
    what makes the position after a relation answer with keywords instead — a
    clause with an empty one keeps offering relations.
    """
    found = offered('DROP TABLE users ')
    assert 'CASCADE' in found
    assert 'orders' not in found


def test_the_ddl_forms_are_offered_where_a_statement_may_begin() -> None:
    """An empty editor is exactly where `DROP TABLE` is a useful suggestion."""
    found = offered('')
    assert 'DROP TABLE' in found
    assert 'TRUNCATE' in found
    assert 'ALTER TABLE' in found


def test_explain_does_not_offer_ddl() -> None:
    """`EXPLAIN DROP TABLE users` is a syntax error, confirmed against the server."""
    assert 'DROP TABLE' not in offered('EXPLAIN ')


def test_a_ddl_statement_is_not_offered_query_clauses() -> None:
    """
    `Clause.statements` already refuses RETURNING after a SELECT's WHERE, and it
    does the same here: a DROP has no result set to group or order.
    """
    found = offered('DROP TABLE users ')
    assert 'GROUP BY' not in found
    assert 'ORDER BY' not in found
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_statement_forms.py -v`
Expected: the eight new tests FAIL. `test_drop_table_offers_relations` shows `SELECT` among the suggestions, which is the bug this slice exists for.

- [ ] **Step 3: Add the clauses**

In `ansi.py`, in the `CLAUSES` tuple, directly after the `EXPLAIN` entry:

```python
        # DDL that names one relation. Each `followed_by` is load-bearing rather
        # than decorative: `_clause_kinds` answers a written relation with
        # keywords only when the clause has continuations, so an empty list
        # leaves `DROP TABLE users ` offering a second relation, which cannot
        # follow without a comma.
        Clause(name='DROP TABLE', suggests=RELATION_REFERENCE, followed_by=('CASCADE', 'RESTRICT')),
        Clause(name='TRUNCATE', suggests=RELATION_REFERENCE, followed_by=('CASCADE', 'RESTRICT')),
        # Stops at four words on purpose. Completing `ADD CONSTRAINT … FOREIGN
        # KEY …` is DDL authoring, which is a different size of thing.
        Clause(name='ALTER TABLE', suggests=RELATION_REFERENCE, followed_by=('ADD', 'DROP', 'RENAME', 'ALTER')),
```

- [ ] **Step 4: Extend `STATEMENT_START`**

```python
STATEMENT_START = (*EXPLAINABLE, 'DROP TABLE', 'TRUNCATE', 'ALTER TABLE')
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_statement_forms.py -v`
Expected: all PASS.

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`

Expected: all green. Two suites are worth reading rather than skimming if they move:

- `tests/test_conformance.py` — `structure` requires every `statement_start` phrase to name a declared clause. All three do.
- `tests/integration/test_acceptance.py` — it accepts every suggestion at every caret across a corpus and parses the result. New words in the empty-editor list mean new statements to parse; a failure there is a real one.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/dialects/ansi.py tests/test_statement_forms.py
git commit -m "feat: three statements that name a relation, and now offer one"
```

---

### Task 3: A form the engine does not know says nothing

**Files:**
- Modify: `src/pysqlsuggestions/engine/analyse.py`
- Modify: `src/pysqlsuggestions/engine/request.py`
- Test: `tests/test_statement_forms.py`

**Interfaces:**
- Consumes: the clauses from Tasks 1–2, which is what keeps `EXPLAIN ⌶` and `DROP TABLE ⌶` alive through this change.
- Produces: `analyse.statement_has_begun(tokens: Sequence[Token], lo: int, hi: int, caret: int) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statement_forms.py`:

```python
def test_an_unmodelled_form_offers_nothing() -> None:
    """
    A form the engine does not know is a position it has nothing true to say
    about. It used to say `SELECT`.
    """
    assert offered('GRANT ') == []
    assert offered('VACUUM ') == []
    assert offered('CALL ') == []
    assert offered('CREATE TABLE t (id ') == []


def test_an_empty_editor_still_offers_the_statement_starts() -> None:
    """The empty-editor answer is right exactly where a statement has not begun."""
    assert 'SELECT' in offered('')
    assert 'SELECT' in offered('   ')


def test_a_half_typed_statement_keyword_still_completes() -> None:
    """
    `SELEC` has a token before the caret and is still the statement-start
    position: the caret is *inside* that token, so the word is still being
    typed. This is the whole reason the rule says `completed`.
    """
    assert 'SELECT' in offered('SELEC')


def test_the_position_after_a_semicolon_is_a_fresh_statement() -> None:
    """A statement that ended does not make the next one already begun."""
    assert 'SELECT' in offered('SELECT id FROM users; ')
    assert 'SELECT' in offered('SELECT id FROM users; SEL')


def test_a_comment_does_not_begin_a_statement() -> None:
    """Nor does whitespace. Neither is a token anything can be written after."""
    assert 'SELECT' in offered('-- a note\n')
    assert 'SELECT' in offered('/* a note */ ')


def test_a_parenthesised_query_still_opens() -> None:
    """
    These have a clause, so the rule never reaches them — worth pinning, because
    silencing any of them would be a far worse regression than the bug fixed.
    """
    assert 'SELECT' in offered('WITH a AS (')
    assert 'SELECT' in offered('SELECT * FROM (')


def test_the_modelled_forms_survive_the_refusal() -> None:
    """Both work only because they were modelled first; this is what says so."""
    assert 'SELECT' in offered('EXPLAIN ')
    assert 'users' in offered('DROP TABLE ')
    assert offered('DROP ') == ['TABLE']
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_statement_forms.py -k 'unmodelled' -v`
Expected: FAIL — `GRANT ⌶` offers the statement starts. The other new tests PASS already; they exist to catch this change going too far, which is the risk that matters.

- [ ] **Step 3: Add the predicate**

In `analyse.py`, directly below `statement_at` so the two read together:

```python
def statement_has_begun(tokens: Sequence[Token], lo: int, hi: int, caret: int) -> bool:
    """
    Whether a completed token precedes the caret in this statement.

    The empty-editor answer — the words a statement may begin with — is right
    only where a statement has not begun. After `DROP TABLE ` it proposed
    `SELECT`, and accepting that wrote `DROP TABLE SELECT`: a wrong answer where
    the engine simply did not recognise the form.

    Completed is the load-bearing word. `SELEC<caret>` has a token before the
    caret, but the caret is inside it — the word is still being typed, and the
    position is still the one that offers `SELECT`.
    """
    return any(token.type not in _SKIP and token.end < caret for token in tokens[lo:hi])
```

- [ ] **Step 4: Blank the kinds in `derive_request`**

In `engine/request.py`, add `statement_has_begun` to the `analyse` import block
(alphabetically it sits after `star_span`). Then lift the kinds expression out of
the `Request(...)` call so it can be blanked:

```python
    kinds = _continued_kinds(
        continues,
        only,
        _expansion_first(star)
        + _values_first(comparand, expecting, qualifier)
        + _kinds_for(clause, qualifier, scope, dialect, expecting, depth_at(tokens, caret) > 0),
    )
    if clause is None and not continues and statement_has_begun(tokens, lo, hi, caret):
        # No clause matched and yet the statement has begun: this is a form the
        # engine does not model. `not continues` is what keeps `DROP ` answering
        # `TABLE` — a half-written clause names its own continuations, and those
        # are the answer whatever the clause model says about the statement.
        kinds = ()

    return Request(
        kinds=kinds,
        ...
    )
```

Leave every other argument of the `Request(...)` call exactly as it is.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_statement_forms.py -v`
Expected: all PASS.

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all green. If `tests/test_golden_requests.py` fails, read the case before touching it: a golden row asserting `('snippet', 'keyword')` at a position where a statement has begun was recording this bug.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/engine/ tests/test_statement_forms.py
git commit -m "fix: a statement form the engine does not know says nothing"
```

---

### Task 4: The corpus and a real server

**Files:**
- Modify: `tests/corpus/cases.py`
- Modify: `tests/integration/test_acceptance.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: nothing.

- [ ] **Step 1: Bring the backends up**

Run: `docker compose -f docker/docker-compose.yml up -d --wait`

- [ ] **Step 2: Add the golden rows**

Append to `CASES` in `tests/corpus/cases.py`:

```python
    GoldenRequest(
        sql='DROP TABLE ⌶',
        kinds=('table', 'schema'),
        clause='DROP TABLE',
        note='a statement form that names a relation, which it now knows it does',
    ),
    GoldenRequest(
        sql='GRANT ⌶',
        kinds=(),
        note='a form the engine does not model: nothing, where it used to offer SELECT',
    ),
    GoldenRequest(
        sql='EXPLAIN ⌶',
        kinds=('snippet', 'keyword'),
        clause='EXPLAIN',
        note='a wrapper: what it takes is a statement, so the statement starts belong here',
    ),
```

`GoldenRequest` carries sql, kinds, prefix, qualifier, clause, relations, dialect,
pending and note — no `statement` field, which is why these rows assert the clause
and the kinds and nothing else.

- [ ] **Step 3: Run the corpus**

Run: `uv run pytest tests/test_golden_requests.py -v`
Expected: PASS, and the burn-down line at the end of the run reports three more cases.

- [ ] **Step 4: Add a DDL statement to the acceptance corpus**

In `tests/integration/test_acceptance.py`, append to `CORPUS`:

```python
    'DROP TABLE reports_runlog CASCADE',
    'TRUNCATE reports_runlog',
    'EXPLAIN SELECT id FROM orders',
```

The existing `test_no_suggestion_writes_a_statement_postgres_refuses` sweeps
every caret of every corpus entry, accepts each suggestion, and parses the
result with `EXPLAIN` inside a savepoint that is rolled back. Nothing is
executed, so naming a real relation in a `DROP` is safe — and `misplaced()`
only reports syntax errors, so a semantic complaint about dropping a table with
dependents is ignored by design.

- [ ] **Step 5: Run the acceptance sweep**

Run: `uv run pytest tests/integration/test_acceptance.py -v`
Expected: PASS. A failure here names the caret and the words that broke it; read
that output rather than deleting the corpus entry.

- [ ] **Step 6: Run the whole integration suite**

Run: `uv run pytest -m integration -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/corpus/cases.py tests/integration/test_acceptance.py
git commit -m "test: the corpus records what a DDL caret answers, and a server parses it"
```

---

### Task 5: Documentation

**Files:**
- Modify: `docs/gaps.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: everything.
- Produces: nothing.

- [ ] **Step 1: Narrow gap 2 in `docs/gaps.md`**

Gap 2 is *narrowed, not closed* — `CREATE TABLE` remains — so the numbering does
not move. Rewrite the section so it describes only what is left:

```markdown
## 2. CREATE TABLE

`CREATE TABLE t (id ⌶` has nothing to say. `DROP`, `TRUNCATE`, `ALTER TABLE` and
`EXPLAIN` are modelled now, and every other unrecognised form answers with
nothing rather than with the words a statement may begin with.

What is missing is a clause model for a parenthesised definition list: where a
type belongs rather than a name, and the words that follow one. The candidates
already exist — `dialect.types` ships for cast positions — so this is the clause
model and nothing else.

Worth being deliberate about how far it goes. DDL completion shades into DDL
authoring, and an engine that knows `ALTER TABLE … ADD CONSTRAINT` well enough
to be useful is a different size of thing than one that knows `SELECT`.
```

Then check the two cross-references elsewhere in the file that name gap
numbers — one in gap 1 ("a dependency of gap 2"), one under *Not gaps* ("the
answer is gap 3") — and confirm both still say something true. They should: no
numbering moved.

- [ ] **Step 2: Record the correction**

Add to the *Closed since this list was written* section:

```markdown
- **DROP, TRUNCATE, ALTER TABLE and EXPLAIN.** They name a relation, or a
  statement, and now offer one. Every form still unmodelled — `GRANT`, `CALL`,
  `VACUUM`, `COMMENT` — answers with nothing.

  This entry said an unrecognised statement "completes as if it were an
  expression". It was worse than that: the position offered the words a
  statement may *begin* with, so `DROP TABLE ⌶` proposed `SELECT` and accepting
  wrote `DROP TABLE SELECT`. `EXPLAIN` was the opposite case — already correct,
  and only because nothing recognised it, which is why it is a clause now.
```

- [ ] **Step 3: Add the CHANGELOG entry**

Under `## Unreleased`, above the cross-schema entry. Adapt to the file's voice, keep every fact:

```markdown
### Statements that are not queries

`DROP TABLE ⌶` used to offer `SELECT`, `WITH` and `INSERT INTO` — the words a
statement may *begin* with, inside a statement that had already begun. Accepting
one wrote `DROP TABLE SELECT`.

`DROP TABLE`, `TRUNCATE` and `ALTER TABLE` now offer relations, and are offered
themselves where a statement may begin. `DROP ⌶` offers `TABLE`. `EXPLAIN` takes
the statements a planner accepts — not `DROP`, which is a syntax error.

**Every other unrecognised form now answers with nothing.** `GRANT`, `CALL`,
`VACUUM`, `COMMENT`, `SET`, `BEGIN` and anything a third-party dialect has not
modelled are silent where they used to propose `SELECT`. A half-typed keyword is
not an unrecognised form: `SELEC⌶` still completes to `SELECT`, and so does an
empty editor, the position after a `;`, and the position after a comment.

`DROP VIEW` and `DROP INDEX` are among the silent ones. Offering them relations
would mean offering tables for `DROP VIEW`, which the server refuses, and
filtering by relation kind needs a set of kinds per clause — `DROP TABLE` must
accept partitioned and foreign tables too. That waits for a change that wants it.
```

- [ ] **Step 4: Verify**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all green. Then re-read `docs/gaps.md` end to end and confirm no
sentence describes behaviour that now exists.

- [ ] **Step 5: Commit**

```bash
git add docs/gaps.md CHANGELOG.md
git commit -m "docs: half a gap closed, and what the entry got wrong about it"
```

---

## Verification

After Task 5, from a clean tree:

```bash
docker compose -f docker/docker-compose.yml up -d --wait
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

All four must pass before the branch is offered for review.
