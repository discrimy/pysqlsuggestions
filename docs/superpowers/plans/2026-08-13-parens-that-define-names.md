# Parens That Define Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Silence the four carets where a parenthesis opens a list of names being defined, and model `ROWS FROM` so the fifth answers with a function.

**Architecture:** One predicate in `engine/analyse.py` reads the token left of the caret's opening paren and decides from `Clause.aliases_with` and `Clause.opens_a_group` alone — no SQL vocabulary in the engine. `derive_request` calls it just after `qualifier_and_prefix` and returns early with `kinds=()`, the shape `in_placeholder` already uses. `ROWS FROM` is a Postgres clause, not part of the rule.

**Tech Stack:** Python 3.12, pytest, `uv`. No new dependencies.

## Global Constraints

- **Design doc:** `docs/superpowers/specs/2026-08-13-parens-that-define-names-design.md`. Read §2 and §4 before Task 1.
- **No SQL words in `engine/`.** The predicate reads `Clause.aliases_with`; it must not match the literals `AS`, `ROWS` or `FROM`. That is why `ROWS FROM` is Task 3 and not part of the rule.
- **`./scripts/check.sh` must pass at the end of every task**: `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`.
- **Style:** single quotes, 120 columns, ruff `D` enabled — every function needs a docstring. mypy `strict` over `src`, `tests`, `lsp`.
- **Prose register:** the docstring says *why* the shape was chosen and what was rejected. `engine/analyse.py` sets the register — see `at_the_clause_start` and `_half_written_clauses`.
- **The negatives carry the weight.** Fifteen positions must keep answering. Never relax one to make a positive pass.
- **Removing a `pending=True` is how a case is claimed.** `xfail(strict=True)` fails the build on an unexpected pass.
- **Measured baseline:** the predicate was prototyped on 2026-08-13 against all twenty positions in §4 of the spec and agreed with every one. The wiring was not; Task 2 measures it.
- **Commits:** `feat:`/`fix:`/`test:`/`docs:`, lowercase prose summary, body explaining the decision. End every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## File Structure

| Path | Change |
| --- | --- |
| `src/pysqlsuggestions/engine/analyse.py` | `opens_a_name_list`, beside the other caret predicates |
| `src/pysqlsuggestions/engine/request.py` | one early return in `derive_request` |
| `src/pysqlsuggestions/dialects/postgres.py` | the `ROWS FROM` clause |
| `tests/test_analyse_prefix.py` | the predicate's twenty cases |
| `tests/grammar/cases.py` | five `pending` flags removed, one `refused` deleted |
| `CHANGELOG.md` | five carets under the existing heading |

---

### Task 1: The predicate

Pure function, no caller yet. Nothing changes at any caret in this task.

**Files:**
- Modify: `src/pysqlsuggestions/engine/analyse.py`
- Test: `tests/test_analyse_prefix.py`

**Interfaces:**
- Consumes: `_group_start(tokens, caret, depth) -> int`, `_skip_back(tokens, index) -> int`, `depth_at(tokens, caret) -> int`, all already in `analyse.py`; `ClauseModel` from `dialects.base`, already imported there.
- Produces: `opens_a_name_list(tokens: Sequence[Token], caret: int, clause: str | None, clauses: ClauseModel) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analyse_prefix.py`. `lex`, `POSTGRES` and `split_caret` are already imported there; add `opens_a_name_list` and `clause_at`/`statement_at` to the `analyse` import.

```python
def _name_list(marked: str) -> bool:
    """Run opens_a_name_list on ⌶-marked SQL, for the postgres dialect."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    clause = clause_at(tokens, lo, hi, caret, POSTGRES.clauses)
    return opens_a_name_list(tokens, caret, clause, POSTGRES.clauses)


@pytest.mark.parametrize(
    'marked',
    [
        'WITH x (⌶',
        'SELECT * FROM users AS u (⌶',
        'SELECT * FROM generate_series(1, 2) AS t (⌶',
        'SELECT * FROM generate_series(1, 2) AS (⌶',
    ],
)
def test_a_paren_that_opens_a_list_of_names(marked: str) -> None:
    """
    Four shapes where the author is inventing names and the catalog has nothing to say.

    Every one of them offered relations or the CTE body words before this
    existed, which is SQL the server refuses rather than a suggestion missing.
    """
    assert _name_list(marked)


@pytest.mark.parametrize(
    'marked',
    [
        # A group the clause itself declares — the alias word introduces it.
        'WITH x AS (⌶',
        'SELECT * FROM users WINDOW w AS (⌶',
        # Calls. `FROM f(` has an identifier left of the paren too, which is why
        # the rule is keyed on the clause and not on that shape alone.
        'SELECT * FROM generate_series(⌶',
        'SELECT count(⌶',
        'SELECT * FROM users TABLESAMPLE BERNOULLI (⌶',
        # Positions that answer well today and a broader rule would silence.
        'INSERT INTO users (⌶',
        'SELECT * FROM users u JOIN orders o USING (⌶',
        'SELECT * FROM users WHERE id IN (⌶',
        'SELECT * FROM users GROUP BY ROLLUP (⌶',
        'SELECT DISTINCT ON (⌶',
        # Ordinary grouping and subqueries.
        'SELECT * FROM (⌶',
        'SELECT * FROM users WHERE (⌶',
        'SELECT * FROM users GROUP BY (⌶',
        'SELECT (⌶',
        'SELECT * FROM users WHERE id = (⌶',
    ],
)
def test_every_other_paren_still_answers(marked: str) -> None:
    """
    The fifteen negatives, which carry more weight than the four positives.

    Four of these answer usefully today — INSERT's column list, a function's
    arguments, USING's join columns, IN's values — so a rule wide enough to
    catch the positives by shape alone would cost more than it gives.
    """
    assert not _name_list(marked)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_analyse_prefix.py -k "name_list or other_paren" -v`
Expected: collection ERROR — `opens_a_name_list` does not exist yet. That is the failure; do not proceed until you see it.

- [ ] **Step 3: Write the predicate**

Add to `src/pysqlsuggestions/engine/analyse.py`, immediately after `at_the_clause_start` so the caret predicates stay together:

```python
def opens_a_name_list(
    tokens: Sequence[Token],
    caret: int,
    clause: str | None,
    clauses: ClauseModel,
) -> bool:
    """
    Whether the paren the caret sits in opens a list of names being defined.

    Four shapes, all of them positions where the author is inventing names and
    a catalog therefore has nothing to say:

        WITH x (a, b) AS (...)      a CTE's column list
        FROM t AS u (a, b)          a relation's column aliases
        FROM f(1) AS t (a int)      a function's column definitions
        FROM f(1) AS (a int)        the same, unnamed

    Depth and the governing clause do not separate them from anything: the
    caret's clause is `WITH` in the first and `FROM` in the rest, exactly as it
    is for the bodies and calls that must go on answering. What separates them
    is the token that introduced the paren.

    A clause declaring `opens_a_group` has already said what its group holds, so
    the only question is whether this paren *is* that group — it is when the
    alias word introduces it, and `WITH x (` is the list that precedes one. A
    clause with no group answers the same question the other way round: a paren
    the alias word introduced, or that a name the alias word introduced
    introduced, is names being defined.

    Read from `Clause.aliases_with` rather than matched against `AS`, so no SQL
    vocabulary enters this module and a dialect aliasing with another word gets
    the same behaviour. `ROWS FROM(` is deliberately not here for that reason:
    it is Postgres spelling and is a clause of its own.
    """
    depth = depth_at(tokens, caret)
    if depth <= 0:
        return False
    start = _group_start(tokens, caret, depth)
    if start <= 0:
        return False

    # `start - 1` is the paren itself, so the word that introduced it is before that.
    at = _skip_back(tokens, start - 2)
    introducer = _plain_word(tokens, at)
    if introducer is None:
        return False

    governing = clauses.get(clause) if clause else None
    if governing is None or not governing.aliases_with:
        return False
    alias = governing.aliases_with.upper()

    if governing.opens_a_group:
        return introducer != alias
    # Either the alias word introduced the paren, or it introduced the name that did.
    return introducer == alias or _plain_word(tokens, _skip_back(tokens, at - 1)) == alias


def _plain_word(tokens: Sequence[Token], index: int) -> str | None:
    """The uppercased value at `index`, or None where it is not an unquoted word."""
    if index < 0 or index >= len(tokens):
        return None
    token = tokens[index]
    if token.type is not TokenType.IDENT or token.quoted:
        return None
    return token.value.upper()
```

If `ClauseModel` is not already imported in `analyse.py`, add it to the existing
`from pysqlsuggestions.dialects.base import ...` line. `engine/` may import
`dialects` — only `ports` and `resolve` are forbidden, which `tests/test_purity.py` enforces.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_analyse_prefix.py -k "name_list or other_paren" -v`
Expected: PASS, 19 of them — 4 positives and 15 negatives.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -m 'not integration' -q`
Expected: unchanged — 1279 passed, 17 xfailed. Nothing calls the predicate yet, so a difference here means something else moved.

- [ ] **Step 6: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/engine/analyse.py tests/test_analyse_prefix.py
git commit -F - <<'EOF'
feat: a predicate for the paren that opens a list of names

`WITH x (`, `FROM t AS u (`, `FROM f(1) AS t (` and `FROM f(1) AS (` are
positions where the author is inventing names, and each offered relations
or the CTE body words instead — SQL the server refuses rather than a
suggestion missing.

Depth and the governing clause separate them from nothing: the clause is
WITH in the first and FROM in the rest, exactly as it is for the bodies
and calls that must go on answering. The token that introduced the paren
is what separates them, and it is read through `Clause.aliases_with` so
that no SQL vocabulary enters engine/ and a dialect aliasing with another
word behaves the same.

Nothing calls it yet. The fifteen negatives are the point of this commit:
four of them answer usefully today — INSERT's column list, a function's
arguments, USING's join columns, IN's values — and a rule wide enough to
catch the positives by shape alone would have silenced them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: Wire it into `derive_request`

**Files:**
- Modify: `src/pysqlsuggestions/engine/request.py`
- Modify: `tests/grammar/cases.py`

**Interfaces:**
- Consumes: `opens_a_name_list(tokens, caret, clause, clauses) -> bool` from Task 1.
- Produces: no new names.

- [ ] **Step 1: Add the early return**

In `derive_request`, immediately after the line

```python
    qualifier, prefix, span = qualifier_and_prefix(tokens, caret)
```

insert:

```python
    if opens_a_name_list(tokens, caret, clause, dialect.clauses):
        # A list of names being defined. Both halves of the answer have to go
        # quiet, not just the keywords — the fault this fixes was `users` being
        # offered, which is a kind rather than a word — so this returns rather
        # than narrowing what follows, the way `in_placeholder` above does.
        #
        # `prefix` and `span` are kept: the author may be part-way through a
        # name, and an editor still needs the range a completion would replace
        # even when there is nothing to put in it.
        return Request(kinds=(), prefix=prefix, replace_span=span, clause=clause, scope=scope)
```

Add `opens_a_name_list` to the `from pysqlsuggestions.engine.analyse import (...)` list at the top of the file.

- [ ] **Step 2: Run the conformance suite**

Run: `uv run pytest tests/test_grammar_select.py -q -rX 2>&1 | tail -6`
Expected: four XPASS-driven failures — `WITH x (⌶`, `FROM users AS u (⌶`,
`generate_series(1, 2) AS t (⌶` and `generate_series(1, 2) AS (⌶` are `pending`
and now pass, and `xfail(strict=True)` makes that a failure. That is the signal
the wiring worked; Step 3 claims them.

- [ ] **Step 3: Claim the four cases**

In `tests/grammar/cases.py`, delete the `pending=True` line from those four
cases. Keep every `refused` string: the field labels the production, not the
case, and a CTE column list is still a position this engine will never suggest
into — what changed is that it no longer suggests the wrong thing.

Replace the stale `note` on each, which describes a fault that no longer exists.
For `WITH x (⌶`:

```python
        note='the paren is a name list rather than the CTE body, told apart by the alias word',
```

For the other three:

```python
        note='a list of names being defined; silent by the rule in engine/analyse.py',
```

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -m 'not integration' -q 2>&1 | tail -5`
Expected: PASS, 13 xfailed. This is a change in `engine/`, so `tests/queries/`
and `tests/corpus/` are the real guard — both exercise parenthesised constructs
heavily. A failure there means the predicate is wider than Task 1's negatives
proved, and the fix is another negative rather than a narrower assertion.

- [ ] **Step 5: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/engine/request.py tests/grammar/cases.py
git commit -F - <<'EOF'
fix: a paren that defines names offers nothing

Four carets offered relations or the CTE body words where the author is
inventing names: `WITH x (`, `FROM t AS u (`, `FROM f(1) AS t (` and
`FROM f(1) AS (`.

An early return rather than a narrowing, and for the reason the
placeholder branch above it returns: the fault was `users` being offered,
which is a kind and not a word, so suppressing the keyword list alone
would have left it. `prefix` and `span` survive the return — the author
may be part-way through a name, and an editor needs the replace range
even when there is nothing to put in it.

The four cases keep their `refused` reasons. That field labels the
production, and a CTE column list is still one this engine will never
suggest into; what changed is that it no longer suggests the wrong thing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: `ROWS FROM`, the changelog, and the count

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py`
- Modify: `tests/grammar/cases.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `Kind` from `types.py`, already imported in `postgres.py`.
- Produces: no new names.

- [ ] **Step 1: Declare the clause**

In `postgres.py`, inside `ANSI.clauses.extend(...)`, beside the `LATERAL` clause:

```python
        # `ROWS FROM( f(), g() )` takes a list of function calls. Two words, so
        # `_half_written_clauses` answers `ROWS ⌶` with `FROM`; `opens_an_item`
        # because it begins a FROM item rather than following a finished one,
        # which is what LATERAL declares for the same reason.
        #
        # A clause rather than a case in `opens_a_name_list`: that rule reads
        # `Clause.aliases_with` so no SQL word enters engine/, and `ROWS FROM`
        # is Postgres spelling. Modelling it also answers the caret instead of
        # merely silencing it — the grammar puts a function there, and the
        # catalog has those.
        Clause(
            name='ROWS FROM',
            follows=frozenset({'FROM', 'JOIN'}),
            opens_an_item=True,
            suggests=(Kind.FUNCTION,),
        ),
```

- [ ] **Step 2: Turn the case from a refusal into an expectation**

In `tests/grammar/cases.py`, replace the `ROWS FROM(⌶` case entirely:

```python
    GrammarCase(
        sql='SELECT * FROM ROWS FROM(⌶',
        cite=_ROWS_FROM,
        offers=('now',),
        refuses=('users', 'orders', 'public'),
        note='a function, which the grammar puts here and a catalog has; it was offering relations',
    ),
```

`refused` is deleted rather than reworded — the production is modelled now, so
the field would be false. `offers=('now',)` needs the fixture to provide a
function; if `SNAPSHOT` in `tests/test_grammar_select.py` has none, add one to
the `MemoryCatalog` construction in `catalog()`:

```python
def catalog() -> MemoryCatalog:
    """A fresh catalog per case, so no case can be affected by another's caching."""
    return MemoryCatalog(SNAPSHOT, functions=[Function(schema='public', name='now', args='', result='timestamptz')])
```

`Function` imports from `pysqlsuggestions.types`. Check `MemoryCatalog.__init__`
for the exact keyword — it is `functions: Iterable[Function] = ()`.

- [ ] **Step 3: Run the conformance suite**

Run: `uv run pytest tests/test_grammar_select.py -q 2>&1 | tail -4`
Expected: PASS, 12 xfailed, and the burn-down line reading
`56/68 SELECT positions answered`. If `ROWS FROM(⌶` still fails on `offers`, the
fixture has no function — Step 2's `catalog()` change is what supplies it.

- [ ] **Step 4: Verify the twelve that remain are the expected ones**

Run: `uv run pytest tests/test_grammar_select.py -q -rx 2>&1 | grep XFAIL | wc -l`
Expected: 12. They are the four withdrawn deliberately (`UNION`, `INTERSECT`,
`EXCEPT`, `USING (id) ⌶`), the three `TABLE` cases blocked on `CREATE TABLE`,
`WITH ORDINALITY`, `FOR UPDATE OF ⌶`, `ORDER BY id USING ⌶`, `WITH x AS ⌶` and
`SELECT * FROM ⌶`. Anything else remaining is a step that did not finish.

- [ ] **Step 5: Write the changelog entry**

`CHANGELOG.md` already has a `### Wrong answers that are now right` heading under
`## Unreleased`. Append to that section, before the `### Positions that had no
answer` heading:

```markdown
A parenthesis that opens a list of names being defined offered relations or the
CTE body words. `WITH x (⌶` proposed `SELECT` and `VALUES` inside a column list,
`FROM t AS u (⌶` and `FROM f(1) AS t (⌶` proposed table names where a column is
being named, and `FROM ROWS FROM(⌶` read the construct as an ordinary `FROM`.

The first four are quiet now. They are told apart from the bodies and calls that
must go on answering — a CTE body, a function's arguments, `INSERT`'s column
list, `IN`'s values — by the word that introduced the paren, read from the
dialect's own `aliases_with` rather than matched against `AS`. `ROWS FROM(⌶`
offers a function, which is what the grammar puts there.
```

Then update the count in the `### Nothing changes at a caret` section: `Fifty-one
of sixty-eight positions are answered` becomes `Fifty-six of sixty-eight
positions are answered`, and `The seventeen it still records` becomes `The twelve
it still records`. Re-read the sentence that follows and correct its breakdown to
match Step 4's list — it currently says "five inside a paren", which is now none.

- [ ] **Step 6: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/dialects/postgres.py tests/grammar/cases.py tests/test_grammar_select.py CHANGELOG.md
git commit -F - <<'EOF'
feat: ROWS FROM takes a function, and says so

The fifth paren that answered wrongly, and the one the name-list rule
deliberately does not cover: `ROWS FROM` is Postgres spelling, and that
rule reads `Clause.aliases_with` precisely so no SQL word enters engine/.

Modelling it beats silencing it. The grammar puts a function call there
and a catalog has those, so the caret answers rather than merely stopping
being wrong — which is why the case loses its `refused` reason instead of
keeping it. The previous design called this exotica the position should
stay silent for; it costs one clause.

Fifty-six of sixty-eight positions answered. The twelve remaining are
four withdrawn deliberately, three waiting on CREATE TABLE, and five
needing a capability that does not exist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

## Notes for the implementer

- **Task 1 changes no behaviour.** The predicate has no caller until Task 2, and Step 5 of Task 1 exists to prove it. If the suite moves there, something other than this plan did it.
- **A negative that fails is a finding, not an obstacle.** The fifteen in Task 1 were prototyped and agreed; if one fails after wiring, the predicate is wider than it looked and the answer is to narrow it, never to drop the negative.
- **Do not put `AS` in `analyse.py`.** The whole reason `ROWS FROM` is a separate task is to keep SQL vocabulary out of the engine. A literal `'AS'` there would pass every test in this plan and be wrong.
- **Twelve cases are meant to remain pending**, listed in Task 3 Step 4.
