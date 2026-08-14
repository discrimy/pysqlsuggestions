# Closing the SELECT Grammar Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take the SELECT conformance suite from 22 answered positions to every position but eleven, in dialect data plus one three-line engine fix.

**Architecture:** Each production picks its mechanism by whether anything competes at that caret — `followed_by` where nothing does, `before_the_item` (prefix-gated) where a column or a number does. Four standard productions are promoted to `ansi.py` only after all three backends are shown to accept them; everything else lands in `postgres.py`. The conformance suite is the acceptance test.

**Tech Stack:** Python 3.12, pytest, `uv`, docker compose (postgres:16, clickhouse 24.8, trino 468). No new dependencies.

## Global Constraints

- **Design doc:** `docs/superpowers/specs/2026-08-13-closing-the-select-grammar-gaps-design.md`. Read §2 and §3 before Task 1.
- **One engine change only:** `at_the_clause_start` in `engine/analyse.py`, in Task 1, in its own commit. No other file under `engine/` changes.
- **`./scripts/check.sh` must pass at the end of every task**: `ruff format --check`, `ruff check`, `mypy --strict`, `pytest`.
- **Style:** single quotes, 120 columns, ruff `D` enabled — every function needs a docstring. mypy `strict` over `src`, `tests`, `lsp`.
- **Prose register:** every clause added says *why* in a comment, and what it refused. `dialects/base.py` and `dialects/postgres.py` set the register.
- **Removing a `pending=True` is how a case is claimed.** `xfail(strict=True)` fails the build on an unexpected pass, so a case fixed by accident cannot go unnoticed.
- **Never widen a `refuses` list to make a case pass.** If a position still answers wrongly, the clause is wrong.
- **Measured baseline:** every expectation below was prototyped against a composed dialect on 2026-08-13. Where the prototype disagreed with the spec, the plan records the measurement.
- **Commits:** `feat:`/`fix:`/`test:`/`docs:`, lowercase prose summary, body explaining the decision. End every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## File Structure

| Path | Change |
| --- | --- |
| `src/pysqlsuggestions/engine/analyse.py` | `at_the_clause_start` compares by suffix, not equality (Task 1 only) |
| `src/pysqlsuggestions/dialects/base.py` | one `EXCLUSIVE` sequence for the FETCH tail |
| `src/pysqlsuggestions/dialects/ansi.py` | join vocabulary, set-operator `DISTINCT`, `OFFSET`/`FETCH`, `WINDOW` |
| `src/pysqlsuggestions/dialects/postgres.py` | `LIMIT ALL`, grouping words, `ORDER BY … USING`, the `FOR` family, `TABLE`, `FROM`, `LATERAL`, three silencing clauses |
| `tests/grammar/cases.py` | four positions rewritten to use a prefix; `pending` removed as each is claimed |
| `tests/test_analyse_prefix.py` | regression tests for the engine fix |
| `tests/test_dialect_clauses.py` | one assertion per ANSI promotion |
| `CHANGELOG.md` | an Unreleased entry |

---

### Task 1: Fix `at_the_clause_start`

**Files:**
- Modify: `src/pysqlsuggestions/engine/analyse.py:304-313`
- Test: `tests/test_analyse_prefix.py`

**Interfaces:**
- Consumes: `_words_before(tokens, caret) -> tuple[str, ...]`, already in `analyse.py`.
- Produces: `at_the_clause_start(tokens: Sequence[Token], caret: int, clause: str) -> bool`, same signature, corrected behaviour.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analyse_prefix.py`. Match the import style already at the top of that file; if `lex`, `statement_at` or `split_caret` are not imported there, add them.

```python
def _at_start(marked: str, clause: str) -> bool:
    """Run at_the_clause_start on ⌶-marked SQL, for the postgres dialect."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    return at_the_clause_start(tokens, caret, clause)


def test_a_clause_that_does_not_begin_the_statement_still_has_a_start() -> None:
    """
    `_words_before` walks back through consecutive identifiers without stopping.

    So the run before `GROUP BY rol` was ('USERS', 'GROUP', 'BY'), which equals
    no clause name, and `before_the_item` was dead for every clause but the
    leading one. DISTINCT worked only because SELECT comes first.
    """
    assert _at_start('SELECT * FROM users GROUP BY rol⌶', 'GROUP BY')
    assert _at_start('SELECT * FROM users LIMIT al⌶', 'LIMIT')


def test_a_written_item_still_ends_the_clause_start() -> None:
    """The guard the equality check was providing, which the suffix check must keep."""
    assert not _at_start('SELECT id, dis⌶', 'SELECT')
    assert not _at_start('SELECT * FROM users GROUP BY id, rol⌶', 'GROUP BY')


def test_the_leading_clause_is_unchanged() -> None:
    """`SELECT dis` was the one case that worked, and it must go on working."""
    assert _at_start('SELECT dis⌶', 'SELECT')
    assert not _at_start('SELECT * FROM users ⌶', 'FROM')
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_analyse_prefix.py -k clause_start -v`
Expected: FAIL — `test_a_clause_that_does_not_begin_the_statement_still_has_a_start` asserts False. The other two already pass; they are the guards.

- [ ] **Step 3: Apply the fix**

Replace the body of `at_the_clause_start`, keeping the docstring and extending it:

```python
def at_the_clause_start(tokens: Sequence[Token], caret: int, clause: str) -> bool:
    """
    Whether nothing has been written in `clause` yet.

    True at `SELECT ⌶`, false at `SELECT id, ⌶` and `SELECT * ⌶` — a comma and a
    star are not words, so the run of words before the caret is empty rather
    than the clause's own name. What stands between a clause and its first item
    belongs here and only here.

    A suffix rather than an equality, because `_words_before` walks back through
    consecutive identifiers and does not stop at a clause boundary: the run
    before `GROUP BY rol` is ('USERS', 'GROUP', 'BY'), and comparing that whole
    run to the name reported false wherever a relation preceded the clause.
    `before_the_item` was therefore dead for every clause but a leading one, and
    `DISTINCT` worked by the accident of SELECT coming first. The guards are
    unaffected: a comma or a star breaks the run, so `SELECT id, ⌶` still has
    nothing to match.
    """
    words = _words_before(tokens, caret)
    name = tuple(clause.upper().split())
    return len(words) >= len(name) and words[-len(name) :] == name
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_analyse_prefix.py -k clause_start -v`
Expected: PASS, all three.

- [ ] **Step 5: Run the whole suite, because this is the widest-blast-radius change in the plan**

Run: `uv run pytest -m 'not integration' -q`
Expected: PASS with 37 xfailed and no new failures. If a golden request or a queries test breaks, stop — the suffix comparison has caught a position that was relying on the equality, and that needs understanding before anything else lands.

- [ ] **Step 6: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/engine/analyse.py tests/test_analyse_prefix.py
git commit -F - <<'EOF'
fix: before_the_item reaches a clause that is not the first

`at_the_clause_start` compared the whole run of words before the caret to
the clause name, and `_words_before` walks back through consecutive
identifiers without stopping at a clause boundary. The run before
`GROUP BY rol` is ('USERS', 'GROUP', 'BY'), so the comparison failed and
`before_the_item` did nothing.

It was dead for every clause that does not begin its statement. DISTINCT
worked, and only because SELECT comes first — which is why nothing
noticed. The function's own docstring says it reports whether anything
has been written in the clause yet, and at `GROUP BY ⌶` that is true and
it answered false.

A suffix comparison instead. The guards are untouched: a comma or a star
breaks the run, so `SELECT id, dis⌶` and `GROUP BY id, rol⌶` stay silent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: Rewrite the four prefix-gated cases

Suite-only. No file under `src/` changes.

**Files:**
- Modify: `tests/grammar/cases.py`

**Interfaces:**
- Consumes: `GrammarCase` and the `_WITH_QUERY` constant, both already in the file.
- Produces: a longer `CASES` tuple. Later tasks delete `pending=True` from entries here.

- [ ] **Step 1: Replace the `WITH ⌶` case**

Find the case with `sql='WITH ⌶'` and replace it wholesale:

```python
    GrammarCase(
        sql='WITH rec⌶',
        cite='[ WITH [ RECURSIVE ] with_query [, ...] ]',
        offers=('RECURSIVE',),
        note='prefix-gated: request.py withholds before_the_item at an empty caret, on purpose',
    ),
```

- [ ] **Step 2: Replace the `SELECT ⌶` case with three**

Find the case with `sql='SELECT ⌶'` and replace it with:

```python
    GrammarCase(
        sql='SELECT ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('users.id',),
        refuses=('ALL', 'DISTINCT'),
        note='a column is what belongs here; the modifiers are prefix-gated and must not crowd it',
    ),
    GrammarCase(
        sql='SELECT dis⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('DISTINCT',),
    ),
    GrammarCase(
        sql='SELECT al⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('ALL',),
        pending=True,
        note='ALL is absent from SELECT.before_the_item; DISTINCT is there and answers',
    ),
```

- [ ] **Step 3: Replace the `GROUP BY ⌶` case with six**

Find the case with `sql='SELECT * FROM users GROUP BY ⌶'` and replace it with:

```python
    GrammarCase(
        sql='SELECT * FROM users GROUP BY ⌶',
        cite='[ GROUP BY [ ALL | DISTINCT ] grouping_element [, ...] ]',
        offers=('users.id',),
        refuses=('ROLLUP', 'CUBE'),
        note='columns belong here; the grouping words are prefix-gated',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY al⌶',
        cite='[ GROUP BY [ ALL | DISTINCT ] grouping_element [, ...] ]',
        offers=('ALL',),
        pending=True,
        note='before_the_item never fired here at all until at_the_clause_start was fixed',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY dis⌶',
        cite='[ GROUP BY [ ALL | DISTINCT ] grouping_element [, ...] ]',
        offers=('DISTINCT',),
        pending=True,
        note='as ALL',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY rol⌶',
        cite='ROLLUP ( { expression | ( expression [, ...] ) } [, ...] )',
        offers=('ROLLUP',),
        pending=True,
        note='as ALL',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY cu⌶',
        cite='CUBE ( { expression | ( expression [, ...] ) } [, ...] )',
        offers=('CUBE',),
        pending=True,
        note='as ALL',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY grouping⌶',
        cite='GROUPING SETS ( grouping_element [, ...] )',
        offers=('GROUPING SETS',),
        pending=True,
        note='as ALL',
    ),
```

- [ ] **Step 4: Replace the `LIMIT ⌶` case with two**

Find the case with `sql='SELECT * FROM users LIMIT ⌶'` and replace it with:

```python
    GrammarCase(
        sql='SELECT * FROM users LIMIT ⌶',
        cite='[ LIMIT { count | ALL } ]',
        refuses=('OFFSET', 'FETCH', 'ALL'),
        note=(
            'a row count belongs here and nothing can suggest one. LIMIT deliberately has no kind: '
            'its docstring records that giving it one made this caret offer OFFSET, which goes after '
            'the number rather than instead of it'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users LIMIT al⌶',
        cite='[ LIMIT { count | ALL } ]',
        offers=('ALL',),
        pending=True,
        note='LIMIT ALL is the spelling that takes a word; before_the_item is where it goes',
    ),
```

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py -q 2>&1 | tail -4`
Expected: PASS. The case count rises from 59 to 67. `WITH rec⌶`, `SELECT ⌶`, `SELECT dis⌶`, `GROUP BY ⌶` and `LIMIT ⌶` are all green immediately — the first two because the engine already answers, the last three because they now assert what the engine correctly withholds.

- [ ] **Step 6: Confirm no citation was lost**

Run: `uv run pytest tests/test_grammar_select.py -k synopsis -q`
Expected: PASS. Splitting a case must not orphan a synopsis line; `ROLLUP`, `CUBE` and `GROUPING SETS` now carry their own citations, which the old single `GROUP BY ⌶` case did not.

- [ ] **Step 7: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add tests/grammar/cases.py
git commit -F - <<'EOF'
test: the prefix-gated positions were the suite's mistake, not the engine's

`before_the_item` is withheld at an empty caret by a decision with its
reasoning written beside it in request.py: at `SELECT ⌶` a column is
nearly always what belongs there, and a rarely-wanted keyword above every
column costs more than it can return. Behind a prefix it costs nothing.

So `WITH ⌶ → RECURSIVE` and `LIMIT ⌶ → ALL` demanded that a decision be
undone. `WITH rec⌶` and `SELECT dis⌶` answer today and are green on
arrival; the empty-caret cases now assert the silence instead, which is
the behaviour worth pinning.

Nine cases where there were four, because a prefix-gated position needs
one per word the grammar names there.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: Prove the four ANSI promotions against all three backends

No code. Evidence, recorded in the commit message so a later reader can see what was run.

**Files:**
- None. This task produces a commit with an empty tree change only if the evidence changes a decision; otherwise it records findings in Task 4's and Task 5's clause comments.

**Interfaces:**
- Consumes: `docker/docker-compose.yml`.
- Produces: a verdict per production, used by Tasks 4 and 5.

- [ ] **Step 1: Start the backends**

Run: `docker compose -f docker/docker-compose.yml up -d --wait`
Expected: three healthy services. If docker is unavailable, stop and report — Tasks 4 and 5 cannot place a clause in `ansi.py` without this, and the fallback is to put all four in `postgres.py` instead, which is a decision for the user rather than the implementer.

- [ ] **Step 2: Run each candidate statement on each backend**

Postgres:

```bash
docker compose -f docker/docker-compose.yml exec -T postgres psql -U postgres -d pysqlsuggestions -c \
  "SELECT 1 AS n FETCH FIRST 1 ROWS ONLY" -c \
  "SELECT 1 AS n OFFSET 0 ROWS" -c \
  "SELECT 1 AS n UNION DISTINCT SELECT 2" -c \
  "SELECT a.n FROM (SELECT 1 n) a FULL OUTER JOIN (SELECT 1 n) b ON a.n = b.n"
```

ClickHouse:

```bash
docker compose -f docker/docker-compose.yml exec -T clickhouse clickhouse-client --multiquery -q \
  "SELECT 1 AS n FETCH FIRST 1 ROWS ONLY;
   SELECT 1 AS n OFFSET 0 ROWS;
   SELECT 1 AS n UNION DISTINCT SELECT 2;
   SELECT a.n FROM (SELECT 1 n) a FULL OUTER JOIN (SELECT 1 n) b ON a.n = b.n;"
```

Trino:

```bash
docker compose -f docker/docker-compose.yml exec -T trino trino --execute \
  "SELECT 1 AS n FETCH FIRST 1 ROWS ONLY;
   SELECT 1 AS n OFFSET 0 ROWS;
   SELECT 1 AS n UNION DISTINCT SELECT 2;
   SELECT a.n FROM (SELECT 1 n) a FULL OUTER JOIN (SELECT 1 n) b ON a.n = b.n;"
```

- [ ] **Step 3: Record the verdicts**

Write the four results into a scratch note for Tasks 4 and 5. A production every backend accepts goes in `ansi.py`. **A production any backend rejects goes in `postgres.py` instead, and the rejection is quoted in a comment beside the clause** — that comment is the whole value of this task, in the register `dialects/postgres.py` already uses for `DROP SEQUENCE`.

- [ ] **Step 4: Stop the backends**

Run: `docker compose -f docker/docker-compose.yml down -v`

---

### Task 4: The result-shaping tail

**Files:**
- Modify: `src/pysqlsuggestions/dialects/base.py` (`EXCLUSIVE`)
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (`FETCH`, `OFFSET`, the three set operators)
- Modify: `tests/test_dialect_clauses.py`
- Modify: `tests/grammar/cases.py` (remove `pending`)

**Interfaces:**
- Consumes: `EXCLUSIVE: tuple[tuple[frozenset[str], ...], ...]` from `dialects/base.py`, read by `resolve.py`.
- Produces: no new names.

- [ ] **Step 1: Add the FETCH sequence to `EXCLUSIVE`**

In `src/pysqlsuggestions/dialects/base.py`, append to the `EXCLUSIVE` tuple:

```python
    # The FETCH tail names four carets and three choices, in the order SQL takes
    # them. A flat `followed_by` on the clause offers all six words everywhere —
    # including `ONLY` at `FETCH ⌶`, where it cannot go. This is the same machine
    # that stops `ORDER BY id ASC ⌶` offering `DESC`, and it means the clause
    # model needs no notion of position within a clause to get all four right.
    (frozenset({'FIRST', 'NEXT'}), frozenset({'ROW', 'ROWS'}), frozenset({'ONLY', 'WITH TIES'})),
```

- [ ] **Step 2: Give FETCH and OFFSET their continuations, and the set operators DISTINCT**

In `src/pysqlsuggestions/dialects/ansi.py`, replace the `FETCH`, `OFFSET`, `UNION`, `INTERSECT` and `EXCEPT` clauses:

```python
        Clause(name='OFFSET', statements=_QUERY, suggests=(Kind.KEYWORD,), followed_by=('ROW', 'ROWS', *_onwards('FETCH'))),
        # Every word of the tail, with EXCLUSIVE doing the ordering. Listing them
        # per position would need a clause per word — `FETCH FIRST`, then a
        # clause for the count — and the count is not a word at all.
        Clause(
            name='FETCH',
            statements=_QUERY,
            suggests=(Kind.KEYWORD,),
            followed_by=('FIRST', 'NEXT', 'ROW', 'ROWS', 'ONLY', 'WITH TIES'),
        ),
        # `DISTINCT` is the default and worth offering: it is the word that says
        # the duplicate-removal was meant, where `ALL` says it was not.
        Clause(name='UNION', statements=_QUERY, suggests=(Kind.KEYWORD,), followed_by=('ALL', 'DISTINCT', 'SELECT')),
        Clause(name='INTERSECT', statements=_QUERY, suggests=(Kind.KEYWORD,), followed_by=('ALL', 'DISTINCT', 'SELECT')),
        Clause(name='EXCEPT', statements=_QUERY, suggests=(Kind.KEYWORD,), followed_by=('ALL', 'DISTINCT', 'SELECT')),
```

Keep the existing comment above `LIMIT`/`OFFSET`/`FETCH` about row counts — it still explains why `LIMIT` has no kind, which is now the only clause of the three without one.

- [ ] **Step 3: Guard the promotions for the other two dialects**

Append to `tests/test_dialect_clauses.py`:

```python
@pytest.mark.parametrize('dialect', [ANSI, POSTGRES, CLICKHOUSE, TRINO])
def test_the_fetch_tail_reaches_every_dialect(dialect: Dialect) -> None:
    """
    Promoted to ANSI, so ClickHouse and Trino inherit it and no grammar case covers them.

    All three backends accept `FETCH FIRST n ROWS ONLY`, which Task 3 of the
    plan verified against the containers rather than against the standard.
    """
    fetch = dialect.clauses.get('FETCH')
    assert fetch is not None
    assert 'WITH TIES' in fetch.followed_by


@pytest.mark.parametrize('name', ['UNION', 'INTERSECT', 'EXCEPT'])
def test_a_set_operator_offers_both_spellings(name: str) -> None:
    """ALL and DISTINCT are the two halves of one choice, and only ALL was offered."""
    clause = ANSI.clauses.get(name)
    assert clause is not None
    assert {'ALL', 'DISTINCT'} <= set(clause.followed_by)
```

- [ ] **Step 4: Claim the cases**

In `tests/grammar/cases.py`, delete the `pending=True` line from these six cases, and delete the now-stale `note` on each (the note describes a fault that no longer exists):

`SELECT * FROM users UNION ⌶`, `SELECT * FROM users INTERSECT ⌶`, `SELECT * FROM users EXCEPT ⌶`, `SELECT * FROM users OFFSET 10 ⌶`, `SELECT * FROM users FETCH ⌶`, `SELECT * FROM users FETCH FIRST 10 ⌶`, `SELECT * FROM users FETCH FIRST 10 ROWS ⌶`.

That is seven cases, not six — `OFFSET 10 ⌶` is claimed here too.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py tests/test_dialect_clauses.py -q 2>&1 | tail -4`
Expected: PASS, seven fewer xfails. An xpass here means a case you did not claim was also fixed — find it and claim it rather than leaving it.

- [ ] **Step 6: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/dialects/base.py src/pysqlsuggestions/dialects/ansi.py tests/test_dialect_clauses.py tests/grammar/cases.py
git commit -F - <<'EOF'
feat: the row-limiting tail answers at every caret it has

`FETCH ⌶` reported kinds=['keyword'] and offered no keyword, which is
worse than reporting nothing: a client shows an empty list rather than
falling through to whatever it would otherwise do.

The whole tail is one `followed_by` and one EXCLUSIVE sequence. Listing
the words per position would need a clause per word, and the count in the
middle is not a word at all — whereas EXCLUSIVE already expresses ordered
choices where a later one settles the earlier, which is exactly the shape
of FIRST/NEXT then ROW/ROWS then ONLY/WITH TIES.

DISTINCT joins ALL on the three set operators. Both are promoted to ANSI
against evidence from all three containers rather than from the standard,
and each carries a test in test_dialect_clauses.py, since the grammar
suite proves Postgres only.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 5: The join vocabulary

**Files:**
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (`_JOINS`, `USING`)
- Modify: `tests/test_dialect_clauses.py`
- Modify: `tests/grammar/cases.py`

**Interfaces:**
- Consumes: `_JOINS: tuple[str, ...]` in `ansi.py`, read by `_AFTER_RELATION`, `ON.followed_by` and `USING.followed_by`.
- Produces: no new names.

- [ ] **Step 1: Widen `_JOINS`**

In `src/pysqlsuggestions/dialects/ansi.py`, replace the `_JOINS` definition:

```python
_JOINS = ('JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN', 'CROSS JOIN')
"""
A join may follow another join's ON, so these are added back where the order alone would not.

Ordered by how often each is what you meant. The `OUTER` spellings are
deliberately absent: `LEFT JOIN` and `LEFT OUTER JOIN` mean the same thing, the
shorter is what people write, and offering both doubles the list to say one
thing twice. `NATURAL` is absent for the opposite reason — it changes the
meaning, and a join whose columns are chosen by name is the inference this
library refuses everywhere else.
"""
```

- [ ] **Step 2: Declare the three new joins as clauses**

`_JOINS` names them, but a word only becomes a clause by being one. Beside the existing `JOIN` clause, add:

```python
        # Each spelling is its own clause because `clause_at` matches names, and
        # a caret after `LEFT JOIN orders ` has to find a clause that says a
        # relation was named. They share JOIN's declarations exactly.
        *(
            Clause(
                name=name,
                follows=frozenset({'FROM', 'JOIN'}),
                repeats=True,
                suggests=RELATION_REFERENCE,
                followed_by=('AS', 'ON', 'USING'),
                aliases_with='AS',
            )
            for name in ('LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN', 'CROSS JOIN')
        ),
```

If `LEFT JOIN`, `INNER JOIN` or `CROSS JOIN` already exist as clauses in the file, replace those entries rather than adding a second — `ClauseModel.extend` replaces by name, but a literal tuple does not.

- [ ] **Step 3: Give `USING` its alias**

Replace the `USING` clause in the same file:

```python
        Clause(
            name='USING',
            follows=frozenset({'JOIN'}),
            repeats=True,
            suggests=(Kind.COLUMN,),
            # PG 14's join_using_alias. `aliases_with` rather than a bare entry
            # in `followed_by`, so a second AS is not offered once one is spent.
            aliases_with='AS',
            followed_by=('AS', *_JOINS, *_onwards('WHERE')),
        ),
```

- [ ] **Step 4: Guard the promotion**

Append to `tests/test_dialect_clauses.py`:

```python
@pytest.mark.parametrize('name', ['LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN', 'CROSS JOIN'])
def test_every_join_spelling_is_a_clause_in_every_dialect(name: str) -> None:
    """
    Promoted to ANSI, so the two dialects the grammar suite does not cover inherit them.

    A name in `_JOINS` that is not also a clause is offered and then not
    recognised once written, which is the worse half of both worlds.
    """
    for dialect in (ANSI, POSTGRES, CLICKHOUSE, TRINO):
        assert dialect.clauses.get(name) is not None, f'{dialect.name} lacks {name}'
```

- [ ] **Step 5: Claim the cases**

Delete `pending=True` and the stale note from `SELECT * FROM users u ⌶` and `SELECT * FROM users u JOIN orders o USING (id) ⌶`.

The `users u ⌶` case asserts `RIGHT JOIN` and `FULL JOIN` and does not assert `NATURAL`; leave it that way. `from_item NATURAL join_type from_item` keeps its own case, which is green already and stays green.

- [ ] **Step 6: Run the suite**

Run: `uv run pytest tests/test_grammar_select.py tests/test_dialect_clauses.py tests/queries -q 2>&1 | tail -4`
Expected: PASS. `tests/queries` is included because it is the ported production suite and joins are most of what it exercises — a widened `_JOINS` changes what is offered after every relation in it.

- [ ] **Step 7: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/dialects/ansi.py tests/test_dialect_clauses.py tests/grammar/cases.py
git commit -F - <<'EOF'
feat: the join vocabulary has a right and a full side

`_JOINS` held four spellings and the grammar has more. RIGHT JOIN and
FULL JOIN were missing from every position that offers a join, which is
every caret after a relation.

The OUTER spellings stay out: `LEFT OUTER JOIN` means what `LEFT JOIN`
means, and offering both says one thing twice in a list whose whole value
is being short. NATURAL stays out for the opposite reason — it picks join
columns by name, which is the inference engine/joins.py refuses at
length.

USING gains the PG 14 join_using_alias through `aliases_with`, so a
second AS is not offered once one is spent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 6: The Postgres modifiers, and the WINDOW correction

**Files:**
- Modify: `src/pysqlsuggestions/dialects/ansi.py` (`WINDOW`)
- Modify: `src/pysqlsuggestions/dialects/postgres.py` (`SELECT`, `GROUP BY`, `LIMIT`, `ORDER BY`)
- Modify: `tests/grammar/cases.py`

**Interfaces:**
- Consumes: `COLUMN_EXPRESSION` and `_QUERY` from `ansi.py`; `ClauseModel.extend`, which replaces a clause of the same name.
- Produces: no new names.

- [ ] **Step 1: Correct WINDOW in `ansi.py`**

A window name is being defined at `WINDOW ⌶`, so a column there is a wrong answer. Replace the clause:

```python
        # `suggests=()` because what belongs here is a name being invented, and
        # the engine has nothing to invent it from. It used to offer columns,
        # which is not a missing answer but one that writes a statement the
        # server refuses. `opens_a_group` carries the definition's own words,
        # the way WITH's body words are carried.
        Clause(
            name='WINDOW',
            statements=_QUERY,
            suggests=(),
            aliases_with='AS',
            opens_a_group=('PARTITION BY', 'ORDER BY'),
            followed_by=_onwards('UNION'),
        ),
```

- [ ] **Step 2: Add the Postgres modifiers**

In `src/pysqlsuggestions/dialects/postgres.py`, inside the `ANSI.clauses.extend(...)` call, add:

```python
        # `before_the_item` and not `followed_by` for all three: a column
        # belongs at `SELECT ⌶` and `GROUP BY ⌶`, and a row count at `LIMIT ⌶`,
        # so these are prefix-gated by request.py and reached by typing. Putting
        # them in `followed_by` would rank a rarely-wanted word above every
        # column in the schema.
        #
        # `extend` replaces a clause of the same name, so ANSI's declarations
        # are restated in full here.
        Clause(
            name='SELECT',
            suggests=COLUMN_EXPRESSION,
            followed_by=('AS', *ANSI_ONWARDS_FROM),
            before_the_item=('DISTINCT', 'ALL'),
            aliases_with='AS',
        ),
        Clause(
            name='GROUP BY',
            follows=frozenset({'FROM', 'WHERE'}),
            statements=frozenset({'SELECT'}),
            suggests=COLUMN_EXPRESSION,
            # ClickHouse spells its grouping sets `GROUP BY … WITH ROLLUP`, so
            # this list is Postgres's rather than the baseline's. Trino agrees
            # with Postgres and could have them too; that is a change with its
            # own evidence to gather.
            before_the_item=('ALL', 'DISTINCT', 'ROLLUP', 'CUBE', 'GROUPING SETS'),
            followed_by=ANSI_ONWARDS_HAVING,
        ),
        # No kind, deliberately: the comment in ansi.py records that giving
        # LIMIT one made `LIMIT ⌶` offer OFFSET, which goes after the number
        # rather than instead of it. `ALL` reaches the caret by prefix instead.
        Clause(
            name='LIMIT',
            statements=frozenset({'SELECT'}),
            before_the_item=('ALL',),
            followed_by=ANSI_ONWARDS_OFFSET,
        ),
        Clause(
            name='ORDER BY',
            statements=frozenset({'SELECT'}),
            suggests=COLUMN_EXPRESSION,
            # `USING operator` is Postgres's alone — an explicit ordering
            # operator, where the standard has only ASC and DESC.
            followed_by=('ASC', 'DESC', 'USING', 'NULLS FIRST', 'NULLS LAST', *ANSI_ONWARDS_LIMIT),
        ),
```

`ANSI_ONWARDS_FROM`, `ANSI_ONWARDS_HAVING`, `ANSI_ONWARDS_OFFSET` and `ANSI_ONWARDS_LIMIT` do not exist. `_onwards` is private to `ansi.py`, and `postgres.py` must not reach into it. Export the four tuples it needs by adding to `ansi.py`, beside `EXPLAINABLE`:

```python
ONWARDS_FROM = _onwards('FROM')
ONWARDS_HAVING = _onwards('HAVING')
ONWARDS_OFFSET = _onwards('OFFSET')
ONWARDS_LIMIT = _onwards('LIMIT')
"""
The canonical clause order, sliced at the points a composing dialect restates.

`ClauseModel.extend` replaces a clause rather than merging into it, so a dialect
refining one has to repeat its whole declaration — including the slice of
`_ORDER` it continues into. Exported so that repetition is a name rather than a
second hand-written list, which is the shape `_ORDER`'s own docstring warns
about.
"""
```

Import them in `postgres.py` as `ONWARDS_FROM as ANSI_ONWARDS_FROM` and so on, or import them plainly and drop the `ANSI_` prefix in the clause bodies above — either is fine, but be consistent.

- [ ] **Step 3: Claim the cases**

Delete `pending=True` and the stale note from: `SELECT al⌶`, `GROUP BY al⌶`, `GROUP BY dis⌶`, `GROUP BY rol⌶`, `GROUP BY cu⌶`, `GROUP BY grouping⌶`, `LIMIT al⌶`, `SELECT * FROM users ORDER BY id ⌶`, `SELECT * FROM users WINDOW ⌶`, `SELECT * FROM users WINDOW w AS (⌶`.

Leave `SELECT * FROM users ORDER BY id USING ⌶` pending. It asserts `('<', '>')`, and operators reach a caret through `Clause.operators`, which is a predicate-clause mechanism — `ORDER BY` is not one, and making it one would offer `=` after every ordering column. Change its note to say so:

```python
        note='USING takes an operator, and operators reach a caret only through Clause.operators, which marks a predicate clause; ORDER BY is not one',
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -m 'not integration' -q 2>&1 | tail -4`
Expected: PASS. Ten fewer xfails. The whole suite, not just the grammar file: `WINDOW` losing its kind changes a shared clause, and `tests/queries` exercises windows.

- [ ] **Step 5: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/dialects/ansi.py src/pysqlsuggestions/dialects/postgres.py tests/grammar/cases.py
git commit -F - <<'EOF'
feat: the grouping words, LIMIT ALL, ORDER BY USING, and a window name

`WINDOW ⌶` offered users.id where a window name is being defined. Not a
missing suggestion but one that writes a statement the server refuses, so
the clause now suggests nothing and carries the definition's own words in
`opens_a_group`, the way WITH's body words are carried.

The grouping words and LIMIT ALL are `before_the_item`, so they are
reached by typing rather than ranked above every column. That mechanism
did nothing for either clause until at_the_clause_start was fixed, which
is why this lands after that commit and not before.

ROLLUP, CUBE and GROUPING SETS are Postgres's here rather than the
baseline's: ClickHouse spells them `GROUP BY … WITH ROLLUP`.

`_onwards` slices are exported from ansi.py, because `extend` replaces a
clause whole and a dialect restating one was otherwise hand-copying the
clause order that _ORDER's docstring exists to stop being hand-copied.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 7: The locking clause and the TABLE form

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py`
- Modify: `tests/grammar/cases.py`

**Interfaces:**
- Consumes: `RELATION_REFERENCE` from `ansi.py`, `Kind` from `types.py`.
- Produces: no new names.

- [ ] **Step 1: Add the locking family**

In `postgres.py`, inside `ANSI.clauses.extend(...)`:

```python
        # Four two-word clause names rather than one `FOR` clause with
        # continuations, for the reason DROP SEQUENCE and ALTER TABLE record: a
        # bare `FOR` would make ('FOR',) a phrase in its own right, and
        # `_half_written_clauses` skips a head that is already a phrase — so
        # `FOR ⌶` would stop answering `UPDATE`.
        #
        # Until these existed, `FOR` was not a clause at all, so the caret after
        # it was still read as inside FROM: `SELECT * FROM users FOR ⌶` offered
        # `users`, and accepting wrote `FROM users FOR users`.
        *(
            Clause(
                name=name,
                follows=frozenset({'FROM', 'JOIN', 'WHERE', 'GROUP BY', 'HAVING', 'ORDER BY', 'LIMIT', 'OFFSET'}),
                statements=frozenset({'SELECT'}),
                suggests=(Kind.KEYWORD,),
                followed_by=('OF', 'NOWAIT', 'SKIP LOCKED'),
            )
            for name in ('FOR UPDATE', 'FOR NO KEY UPDATE', 'FOR SHARE', 'FOR KEY SHARE')
        ),
        # `OF` names a relation the statement already has, and nothing in `Kind`
        # means that. `Kind.TABLE` would offer every catalog relation, and once
        # the relation is aliased the server takes only the alias — so
        # `FROM users u FOR UPDATE OF users` is refused, and offering `users`
        # there would be a confident wrong answer where silence is available.
        # `Kind.ALIAS` invents a name for the relation just written rather than
        # listing the ones in scope, so it does not serve either.
        Clause(
            name='OF',
            follows=frozenset({'FOR UPDATE', 'FOR NO KEY UPDATE', 'FOR SHARE', 'FOR KEY SHARE'}),
            statements=frozenset({'SELECT'}),
            suggests=(),
            followed_by=('NOWAIT', 'SKIP LOCKED'),
        ),
```

- [ ] **Step 2: Add the TABLE form**

Add the clause, and add `'TABLE'` to `statement_start`:

```python
        # `TABLE t` is `SELECT * FROM t`, and it is a whole statement rather
        # than a clause of one. `ONLY` is `before_the_item` for the reason
        # SELECT's DISTINCT is: a relation is what belongs at `TABLE ⌶`.
        Clause(
            name='TABLE',
            suggests=RELATION_REFERENCE,
            before_the_item=('ONLY',),
        ),
```

```python
    statement_start=(
        *ANSI.statement_start,
        'DROP SEQUENCE',
        'ALTER SEQUENCE',
        'DROP MATERIALIZED VIEW',
        'DROP INDEX',
        'TABLE',
    ),
```

- [ ] **Step 3: Claim the cases**

Delete `pending=True` and the stale note from `SELECT * FROM users FOR ⌶`, `SELECT * FROM users FOR UPDATE ⌶`, `SELECT * FROM users u FOR UPDATE OF u ⌶`, `TABLE ⌶` and `TABLE ONLY ⌶`.

`TABLE ⌶` asserts `('users', 'ONLY')` and `ONLY` is prefix-gated, so change that case to assert `('users',)` and add a second:

```python
    GrammarCase(
        sql='TABLE on⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('ONLY',),
    ),
```

Leave `SELECT * FROM users u FOR UPDATE OF ⌶` pending, and replace its note:

```python
        pending=True,
        refused='OF names a relation already in scope, and no Kind means that; Kind.TABLE would answer an aliased relation with its bare name, which the server refuses',
        note='silent by choice — see the OF clause in postgres.py',
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -m 'not integration' -q 2>&1 | tail -4`
Expected: PASS, five fewer xfails than after Task 6.

- [ ] **Step 5: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/dialects/postgres.py tests/grammar/cases.py
git commit -F - <<'EOF'
feat: row locking, and the statement that is a bare TABLE

`SELECT * FROM users FOR ⌶` offered `users`. FOR was not a clause, so the
analyser still believed the caret was inside FROM, and accepting the
first suggestion wrote `FROM users FOR users`. It was the worst answer
the conformance suite found.

Four two-word clause names rather than one FOR with continuations, for
the reason DROP SEQUENCE records: a bare FOR would make ('FOR',) a phrase
and `_half_written_clauses` skips a head that is already one.

OF suggests nothing on purpose. It names a relation the statement already
has, no Kind means that, and Kind.TABLE would answer an aliased relation
with its bare name — which the server refuses. A silent caret beats a
confident wrong one; the missing capability is worth its own design.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 8: Three clauses that exist to stop a caret answering

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py`
- Modify: `tests/grammar/cases.py`

**Interfaces:**
- Consumes: `Kind` from `types.py`.
- Produces: no new names.

- [ ] **Step 1: Declare them**

In `postgres.py`, inside `ANSI.clauses.extend(...)`:

```python
        # These three exist to make a caret stop answering, not to make it
        # answer. Until a word is a clause, the analyser reads the caret after
        # it as still inside the clause before — so `FROM t TABLESAMPLE ⌶`
        # offered JOIN and WHERE, and `… CYCLE ⌶` offered the CTE body words.
        # Declaring the clause is the whole fix; `trino.py` already does exactly
        # this for its own TABLESAMPLE.
        #
        # No sampling methods are named. Postgres ships BERNOULLI and SYSTEM and
        # an extension may add more, so a static list here would go quietly
        # wrong on any installation that has one.
        Clause(name='TABLESAMPLE', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.KEYWORD,)),
        # SEARCH and CYCLE follow a recursive CTE's body. BREADTH and DEPTH are
        # the only two words SEARCH takes, so it can answer as well as stop a
        # wrong answer; CYCLE takes a column of the CTE, which is a scope this
        # position cannot see, so it says nothing.
        Clause(
            name='SEARCH',
            follows=frozenset({'WITH'}),
            suggests=(Kind.KEYWORD,),
            followed_by=('BREADTH', 'DEPTH'),
        ),
        Clause(name='CYCLE', follows=frozenset({'WITH'}), suggests=()),
```

- [ ] **Step 1b: Offer TABLESAMPLE where it can go, and give LATERAL a body**

Declaring `TABLESAMPLE` with `follows={'FROM', 'JOIN'}` silences the caret after
it but does **not** get the word offered after a relation — measured, `FROM
users ⌶` still lacks it. `continuations` derives a clause from `follows` only
when the clause is not already in `followed_by`, and `FROM`'s list is explicit.
So replace `FROM` in `postgres.py`, and extend the existing `LATERAL` clause:

```python
        # TABLESAMPLE is derived from `follows` for most clauses, but FROM's
        # `followed_by` is an explicit list and derivation only adds what that
        # list omits — so the word has to be named here to reach the caret.
        # `extend` replaces, so ANSI's declarations are restated.
        Clause(
            name='FROM',
            follows=frozenset({'SELECT'}),
            suggests=RELATION_REFERENCE,
            followed_by=('AS', 'TABLESAMPLE', *ANSI_AFTER_RELATION),
            aliases_with='AS',
        ),
        # `( select )` is a whole statement, the way a CTE body is, so LATERAL
        # needs `opens_a_group` for `FROM LATERAL (⌶` to answer. Without it the
        # paren was read as an ordinary FROM position and offered relations.
        Clause(
            name='LATERAL',
            follows=frozenset({'FROM', 'JOIN'}),
            opens_an_item=True,
            suggests=(Kind.TABLE, Kind.FUNCTION),
            opens_a_group=('SELECT',),
        ),
```

`ANSI_AFTER_RELATION` does not exist. `_AFTER_RELATION` is private to `ansi.py`;
export it beside the `ONWARDS_*` tuples Task 6 added, as `AFTER_RELATION`, with
a docstring saying it exists so a composing dialect restates the list by name
rather than by hand.

Do **not** add `'WITH ORDINALITY'` to this list. `followed_by` is per clause and
not per item kind, so it would be offered after `FROM users ⌶` too, where the
server refuses it — a wrong answer traded for a missing one, which is the trade
this plan refuses everywhere else. `generate_series(1, 2) ⌶` stays pending for
exactly that reason.

- [ ] **Step 2: Claim the cases**

Delete `pending=True` from `SELECT * FROM users TABLESAMPLE ⌶`, `WITH RECURSIVE x AS (SELECT 1) SEARCH ⌶`, `WITH RECURSIVE x AS (SELECT 1) CYCLE ⌶`, `SELECT * FROM users ⌶` and `SELECT * FROM LATERAL (⌶`.

**Keep their `refused` strings.** `refused` labels the production, not the case — the design is explicit that the two are independent, and these three productions are still ones the engine will not model. What changed is that they no longer answer wrongly.

`TABLESAMPLE ⌶` asserts `offers=('BERNOULLI', 'SYSTEM')` and no method is named, so drop that assertion and keep the refusal:

```python
    GrammarCase(
        sql='SELECT * FROM users TABLESAMPLE ⌶',
        cite='[ TABLESAMPLE sampling_method ( argument [, ...] ) [ REPEATABLE ( seed ) ] ]',
        refuses=('JOIN', 'WHERE', 'users'),
        refused='sampling methods are extensible per installation; a list here could not be kept true',
        note='the clause exists to stop this caret answering as though it were still inside FROM',
    ),
```

- [ ] **Step 3: Run the suite**

Run: `uv run pytest -m 'not integration' -q 2>&1 | tail -4`
Expected: PASS, five fewer xfails. Eleven should remain.

- [ ] **Step 4: Verify the remaining seven are the expected ones**

Run: `uv run pytest tests/test_grammar_select.py -q -rx 2>&1 | grep XFAIL`
Expected: exactly eleven, and every one of them on this list. Anything else remaining is a task that did not finish.

| still pending | why |
| --- | --- |
| `WITH x (⌶`, `FROM users AS u (⌶`, `REPEATABLE (⌶`, `AS t (⌶`, `AS (⌶`, `ROWS FROM(⌶` | inside a paren whose opening construct the analyser does not track |
| `FOR UPDATE OF ⌶` | no `Kind` means "a relation this statement already has" |
| `ORDER BY id USING ⌶` | operators reach a caret only through `Clause.operators`, which marks a predicate clause |
| `SELECT * FROM ⌶` (`ONLY`, `LATERAL`) | `resolve.py:598` filters `opens_an_item` clauses out of this position deliberately |
| `WITH x AS ⌶` (`MATERIALIZED`) | the alias is spent at that caret and nothing renders there |
| `SELECT * FROM generate_series(1, 2) ⌶` (`WITH ORDINALITY`) | `followed_by` is per clause, not per item kind, so offering it here would also offer it after `FROM users ⌶`, where it is invalid |

- [ ] **Step 5: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add src/pysqlsuggestions/dialects/postgres.py tests/grammar/cases.py
git commit -F - <<'EOF'
feat: TABLESAMPLE, SEARCH and CYCLE stop answering as though inside FROM

A word that is not a clause leaves the caret after it governed by the
clause before, so `FROM t TABLESAMPLE ⌶` offered JOIN, WHERE and every
relation, and `… CYCLE ⌶` offered SELECT and INSERT INTO. Declaring the
clause is the whole fix, and trino.py already does exactly this for its
own TABLESAMPLE.

No sampling method is named. Postgres ships BERNOULLI and SYSTEM and an
extension may add more, so a static list would go quietly wrong on any
installation that has one — the position stays silent instead.

All three keep their `refused` reasons. That field labels the production
and not the case, and these are still productions this engine will not
model; what changed is that they no longer answer wrongly.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 9: The changelog

**Files:**
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: the final burn-down figures from Task 8.
- Produces: nothing.

- [ ] **Step 1: Read the final burn-down**

Run: `uv run pytest -m 'not integration' -q 2>&1 | grep 'grammar burn-down'`
Use the printed figures verbatim in the entry below rather than the ones this plan predicts.

- [ ] **Step 2: Write the entry**

`CHANGELOG.md` is grouped by what changes at a caret. Add under `## Unreleased`, above the `### Nothing changes at a caret` heading the conformance suite added — and note that heading is now wrong for the release as a whole, so it becomes a subsection of its own about the suite:

```markdown
### Wrong answers that are now right

`SELECT * FROM users FOR ⌶` offered `users`, and accepting wrote
`SELECT * FROM users FOR users`. `FOR` was not a clause, so the caret after it
was still read as inside `FROM`. The four locking forms are clauses now, and the
caret offers `UPDATE`, `NO KEY UPDATE`, `SHARE` and `KEY SHARE`.

`WINDOW ⌶` offered a column where a window name is being defined. It suggests
nothing now, and `WINDOW w AS (⌶` offers `PARTITION BY` and `ORDER BY`.

`FROM t TABLESAMPLE ⌶` offered `JOIN` and `WHERE`; `WITH … CYCLE ⌶` offered
`SELECT` and `INSERT INTO`. Both are clauses now and both are quiet.

### Positions that had no answer

The `FETCH { FIRST | NEXT } … { ONLY | WITH TIES }` tail, at all four of its
carets. `OFFSET n ⌶` takes `ROW` and `ROWS`. `UNION`, `INTERSECT` and `EXCEPT`
offer `DISTINCT` beside `ALL`. `ORDER BY id ⌶` offers `USING`. `RIGHT JOIN` and
`FULL JOIN` join the join list. `USING (id) ⌶` takes the PG 14 join alias.
`TABLE t` is a statement the engine knows.

Behind a prefix, where the engine puts words that would otherwise crowd out a
column: `SELECT al⌶` → `ALL`; `GROUP BY rol⌶` → `ROLLUP`, with `CUBE`,
`GROUPING SETS`, `ALL` and `DISTINCT`; `LIMIT al⌶` → `ALL`; `TABLE on⌶` →
`ONLY`.

### A bug that had hidden all of those

`before_the_item` — the mechanism that puts a word behind a prefix — did nothing
for any clause that was not the first in its statement. `at_the_clause_start`
compared the whole run of words before the caret to the clause name, and that
run does not stop at a clause boundary, so `GROUP BY rol` compared
`('USERS', 'GROUP', 'BY')` and failed. `DISTINCT` worked, and only because
`SELECT` comes first, which is why nothing had noticed.

A dialect that declared `before_the_item` on any other clause was silently
getting nothing.
```

- [ ] **Step 3: Run the gate and commit**

Run: `./scripts/check.sh`

```bash
git add CHANGELOG.md
git commit -F - <<'EOF'
docs: what changed at a caret, which this time is a great deal

Grouped as the file is — by position rather than by commit — and led by
the wrong answers, since those cost more than the missing ones.

The before_the_item bug gets its own section. Every prefix-gated position
in the release depended on it, and a dialect declaring the field on any
clause but a leading one was silently getting nothing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

## Notes for the implementer

- **The order of Tasks 1 and 6 is load-bearing.** `GROUP BY ROLLUP` and `LIMIT ALL` are expressible only after `at_the_clause_start` is fixed. If Task 6 is attempted first, both look like dialect-data failures and are not.
- **A clause name must also be a clause.** Adding a word to `_JOINS` or to `followed_by` makes it *offered*; adding a `Clause` makes it *recognised once written*. Task 5 does both deliberately, and either alone is a half-fix.
- **`ClauseModel.extend` replaces by name and a literal tuple does not.** In `ansi.py` you are editing a literal tuple, so a second `Clause(name='FETCH', ...)` shadows nothing and the first one wins every lookup. Replace entries there; add them in `postgres.py`.
- **Do not name sampling methods to make a case green.** The case asserts a refusal, not an offer, and the design records why the list is not shipped.
- **Eleven cases are meant to remain pending**, and Task 8 Step 4 tabulates them with a reason each. A run ending with fewer means a case was claimed that should not have been — most likely by widening a `refuses` list or asserting a word the engine offers in the wrong place.
