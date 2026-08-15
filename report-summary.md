# QA sweep — orchestrator's verdicts

Eight QA agents were dispatched against the library, each on an independent domain, under a standing
instruction to minimise every issue to a runnable repro and to check it against `docs/gaps.md` and the
existing tests before reporting. **Every finding below was then re-run independently by the
orchestrator**; nothing here rests on an agent's word, and several agent claims were corrected in the
process.

Baseline: `uv run pytest -m 'not integration'` → **1700 passed, 1 skipped, 10 xfailed**, re-run at the
end and still green. The repo source was never modified; agents wrote only to scratch directories.

**Status: complete. All 8 agents reported — 37 verified, 2 unsure, 0 false.**

The eighth was a high-volume differential fuzzer: **≈1,566,800 distinct (sql, caret, dialect) triples**
plus an `apply_suggestion` and a re-entrant `complete()` per suggestion. Five of its seven harnesses —
caret sweeps, mutation, grammar generation, four-dialect differential, output invariants — found
**nothing at all**, and its invariant checker was validated against planted failures of each class, so
that zero means what it says. Every crash it found came from resource-limit probes. Its 727,184
differential comparisons produced **zero** raise-splits between dialects and 1,061 count-splits, all
three buckets by design (Postgres `$n` placeholders, per-dialect `:name`/`{}`/`?` spellings, and
ClickHouse dropping `TABLE`).

That is the single most reassuring result in this report: the engine's *answers* are solid, and almost
every defect found is at a boundary — insertion, caching, quoting, the LSP layer — or is unbounded
resource use.

---

## Fixed so far — branch `fix/qa-sweep-quick-wins`

**27 findings closed and one substantially improved**, across five commits, each fix carrying a
regression test written *before* it and watched to fail. Gate green throughout:
`./scripts/check.sh` → ruff format, ruff check, mypy strict, **1734 passed** (from 1700).

| Commit | Closes |
| --- | --- |
| `9e123c1` from a caret to the catalog | 1, 2, 3, 4, 5, 6, 10, 13, 17*, 37 |
| `4b38c3e` a CTE belongs to the query that declares it | 24, 25, 27 |
| `8b5f21c` the server's half of a position | 30, 31, 32, 33, 35, 36 |
| `4fce7b7` a literal's opening is not always one character | 9, 16, 21, 22, 23 |
| `0dce915` four things that answered for something nobody asked | 8, 12, 15, 18 |

### Still open, and why

| # | Finding | Why it is still here |
| --- | --- | --- |
| 7 | Postgres leaves ~13k BMP code points unquoted | Real, and the fix is a narrower character class — but it changes what every non-ASCII name inserts as, so it wants its own change and its own round-trip corpus. |
| 11 | Capability detection differs on 3.10 vs 3.12 | Left deliberately. Matching 3.12 means `inspect.getattr_static`, which stops seeing `classmethod` capabilities that work today — a live regression traded for a proxy-catalog edge case. |
| 14 | A plugin named `postgres` shadows the built-in | Design call: should built-ins be privileged, or is last-wins intended? |
| 19 | `without()` is a silent no-op on an unknown name | Design call. Making it raise matches `postgres._ansi`'s stated reasoning but would break any third-party dialect dropping a clause it does not have. |
| 20 | Markers rewritten inside strings and comments | Latent; no shipped query trips it. Needs a real scanner in `render`, not a regex. |
| 26 | `ORDER BY` after a set operation | Needs a decision on what the position should offer — result columns only, I would argue. |
| 28 | The `INSERT` target is in scope for the source `SELECT` | Needs the target visible to the column list and `RETURNING` while invisible after the `SELECT`. |
| 29, 34 | Cubic nesting cost; per-keystroke LSP cost | Both are constants and caching, not correctness. |
| U1, U2 | The two design calls | Unchanged. |

| # | Fix | Test |
| --- | --- | --- |
| 1 | `select_list_end` skips trailing trivia instead of stopping at the last token | `test_the_relation_clears_a_trailing_line_comment` |
| 2 | Same fix: stopping before the branch-boundary whitespace keeps the separator | `test_the_relation_is_separated_from_a_following_set_operator` |
| 3 | `select_list_end` clamps to the caret's own paren group | `test_the_relation_stays_inside_the_subquery_that_needs_it` |
| 4 | `convert._split_edits` searches from the back, so the caret's edit wins a tie | `test_the_edit_at_the_caret_is_the_primary_one_at_an_empty_prefix` |
| 5 | `_key` carries `None` instead of folding it to `''`, and `tables` gained a sentinel | `test_an_empty_qualifier_does_not_evict_the_relation_list`, `test_an_empty_namespace_does_not_empty_the_relation_list` |
| 6 | `before_the_item` folded into `keywords` in `Dialect.__post_init__` | `test_before_the_item_is_folded_into_the_keywords` |
| 10 | `_MAX_STAR_DEPTH` bounds the `_columns_of` ↔ `_from_projection` walk | `test_a_long_cte_chain_resolves_without_exhausting_the_stack` |
| 13 | `render` rejects `$0` for every paramstyle | `test_a_zero_marker_is_rejected_rather_than_binding_the_last_value` |
| 17 | *Partial.* Memoised `ClauseModel.__hash__`; `bisect` in `_unclosed_call_depth` | `test_unclosed_nesting_does_not_cost_its_square` |
| 37 | `_MAX_NESTING` threaded through `select_outputs` ↔ `_read_ctes` | `test_nested_ctes_do_not_exhaust_the_stack` |

**Findings 1–3 were one fix.** `select_list_end` now clamps to the caret's parenthesised group and
walks back over trailing trivia rather than stopping at the last token — with the result never placed
before the caret, since at `SELECT ⌶` the select list ends exactly there and an earlier offset would
put the clause in front of the column and break `Insertion.edits`' latest-first ordering. Skipping the
whitespace also fixed the set-operator fusion for free: the space before `UNION` survives the
insertion, so no explicit separator was needed. Controls re-verified — separate statements and
`ORDER BY` still place the clause correctly.

**Finding 4 was not an API bug.** Equal spans at `SELECT ⌶` are *correct*: the select list really does
end at the caret. The defect was `_split_edits` matching from the front of a tuple that is ordered
latest-first, so it now searches from the back. The API-side contract that tuple order carries the
ordering when offsets tie is pinned by its own test.

**Finding 5 needed two fixes, not one.** The sentinel separated `tables` from `columns`, and the test
for it passed — but re-running the original repro showed it *still broken* in the other order. `_key`
also folded `None` into `''` via `schema or ''`, so `tables(None)` ("wherever the search path reaches")
collided with `tables('')` ("a schema actually named that", which has nothing in it). Both halves are
now fixed and both orders verified. Recorded because the lesson generalises: the passing test proved
less than it appeared to, and only re-running the untouched repro caught it.

**Two corrections to this report, found by doing the work:**

1. **Finding 17's stated root cause was wrong.** `_unclosed_call_depth`'s linear scan is real and is
   now a bisection, but it was worth only ~33% on the bare-paren shape and nothing on the nested one.
   Profiling the *actual* shape found **118 million `hash()` calls, 24 of 44 seconds**: `_by_first_word`
   is `@cache`d on `ClauseModel`, so every one of ~1M lookups re-hashed the whole clause model.
   `ClauseModel` now memoises its hash at construction, which is sound because the record is frozen
   and both `extend` and `without` return new ones. Nested subqueries at depth 160: **35.3s → 3.0s**,
   and the growth ratio fell from a clean 4× per doubling to 2.6×.
2. **17 is improved, not closed.** A run of *bare* unclosed parens is still cleanly quadratic — 3.9×
   per doubling, ~4s at 8,000 — because `clause_at` widens outward once per paren depth and calls the
   O(n) `_group_start` and `_scan_for_clause` each time. That is a separate mechanism from the hash,
   and a far less realistic input than nested subqueries, so it is left open deliberately. The
   regression test names what it does and does not cover.
3. **One test is still missing.** The `select_outputs` → `select_outputs` cycle (nested derived
   tables) has no affordable regression test: the shape recurses only about one frame per level, so
   reaching the stack limit needs depth ~1000, and even after the hash fix that is tens of seconds. It
   is protected by the same `remaining` guard the nested-CTE test exercises, but not directly pinned.

Verdicts: **verified** (reproduced independently, and not blessed by an existing test or by
`docs/gaps.md`), **false** (does not reproduce, or is refused by design), **unsure** (reproduces, but
whether it is a defect is a design call the maintainer owns).

---

## Summary

| # | Finding | Severity | Verdict |
|---|---|---|---|
| 1 | `_relation_edit` splices ` FROM` into a trailing line comment | corrupts query | verified |
| 2 | ` FROM x` fuses onto a following set operator (`auth_userUNION`) | corrupts query | verified |
| 3 | ` FROM x` spliced into the wrong query level (CTE / subquery) | corrupts query | verified |
| 4 | Equal edit spans at `SELECT ⌶`; LSP then picks the FROM as primary | corrupts query | verified |
| 5 | Cache key collision: `tables(s)` vs `columns(s, '')` | crash + silent data loss | verified |
| 6 | `before_the_item` bypasses the `__post_init__` keyword fold | invariant → invalid SQL | verified |
| 7 | Postgres leaves ~13k BMP code points unquoted; engine cannot re-read its own output | corrupts query | verified |
| 8 | `SELECT NULL` harvested as an output column named `"null"` | invalid SQL | verified |
| 9 | `Clause(name='')` crashes `complete()` **and** `DialectConformance.check` | crash | verified |
| 10 | `RecursionError` on ~495 chained CTEs, with no catalog at all | crash | verified |
| 11 | Capability detection differs on 3.10 vs 3.12 for a proxying catalog | cross-version crash | verified |
| 12 | `render` binds values no marker asks for (`numeric`/`named`/`pyformat`) | latent wrong binding | verified |
| 13 | `$0` silently binds the *last* value | silent wrong binding | verified |
| 14 | Entry point named `postgres` silently shadows the built-in | invariant | verified |
| 15 | `available()` hands out its cached mutable dict | invariant | verified |
| 16 | `DialectConformance` passes a dialect whose catalog SQL will always raise | blind spot | verified |
| 17 | `derive_request` is quadratic on nested `FROM (…)` | perf | verified |
| 18 | Negative `limit` slices from the end instead of clamping | edge case | verified |
| 19 | `without()` is a silent no-op on a name that is not there | edge case | verified |
| 20 | Markers rewritten inside strings, comments and dollar-quoted bodies | latent | verified |
| 21 | `E'…'` / `$$…$$` literals derive a prefix containing the quote | wrong output | verified |
| 22 | Typing past an embedded apostrophe kills the value suggestion | wrong output | verified |
| 23 | Zero-length placeholder opener: `lex` never terminates, and so does conformance | hang | verified |
| 24 | A CTE inside a subquery leaks out — and shadows a real table of the same name | invariant | verified |
| 25 | A `WITH` nested in a CTE body is invisible from inside that body | invariant | verified |
| 26 | `ORDER BY` after a set operation is scoped to the last branch | wrong output | verified |
| 27 | Caret at the end of a *terminated* line comment offers suggestions | wrong output | verified |
| 28 | The `INSERT` target relation is in scope for the source `SELECT` | wrong output | verified |
| 29 | Nested derived tables cost ~cubic; 1 KB query blows the documented budget | perf | verified |
| 30 | LSP: completion for a URI with no open document raises `-32603` | crash | verified |
| 31 | LSP: edit ranges are code points, but the server advertises UTF-16 | corrupts document | verified |
| 32 | LSP: a lone CR is a line break to the client but not to `line_starts` | corrupts document | verified |
| 33 | LSP: no connect timeout on the live path — 21 s freeze, lock held throughout | hang | verified |
| 34 | LSP: every keystroke re-lexes the whole document; nothing cached | perf | verified |
| 35 | LSP: `check()` can answer "it failed" with an empty detail | edge case | verified |
| 36 | LSP: the degraded notification hands the editor a raw Python dict | edge case | verified |
| 37 | `RecursionError` in `analyse` (`select_outputs` ↔ `_read_ctes`), escaping the LSP | crash | verified |
| U1 | Function caret lost to a pending template blank | edge case | unsure |
| U2 | One unclosed paren makes every later `;` non-splitting, merging statements | wrong output | unsure |

Findings 1–4 share one root cause. 12, 13, 20 share another. Fixing two functions —
`analyse.select_list_end` / `api._relation_edit`, and `catalogs.dbapi.render` — closes seven.

---

## Verified — query corruption

### 1–4. The `select_list_end` / `_relation_edit` family

**One root cause, four symptoms.** `api._relation_edit` (`api.py:182-195`) generates a whole
` FROM <relation>` clause and asks `analyse.select_list_end` (`analyse.py:1379`) where to put it.
That function computes an offset without regard to the caret's paren depth, to trailing comments, or
to branch-boundary whitespace — and `_relation_edit` never passes its generated text through
`_separated`, the guard that exists to stop exactly this class of token fusion.

Shared setup:

```python
from pysqlsuggestions import apply_suggestion, complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

CAT = MemoryCatalog({('public', 'auth_user'): [('id', 'bigint'), ('name', 'varchar')]})

def accept(sql, caret):
    s = next(x for x in complete(sql, caret, POSTGRES, CAT) if x.relation)
    return apply_suggestion(sql, s, dialect=POSTGRES)
```

**1 — trailing line comment swallows the clause.** `accept('SELECT na -- note', 9)` gives

```
'SELECT auth_user.name -- note FROM public.auth_user'
```

The FROM lands *inside* the comment, so the statement has none: Postgres answers
`ERROR: missing FROM-clause entry for table "auth_user"`. `select_list_end` skips comments in its
clause scan (`_SKIP`) but falls back to `tokens[hi - 1].end`, and `hi - 1` **is** the comment token.
Specific to a comment ending the buffer — a block comment and a comment followed by a newline both
land correctly. Also hits `'SELECT na --'` and `'SELECT id, na -- x'`.

**2 — fusion onto a set operator.** `accept('SELECT na UNION SELECT 1', 9)` gives

```
'SELECT auth_user.name  FROM public.auth_userUNION SELECT 1'
```

Re-lexing yields a single token `auth_userUNION`: the relation name is wrong *and* the UNION is
destroyed. Reproduced for `UNION`, `UNION ALL`, `INTERSECT`, `EXCEPT`, and with a newline separator.
**Found independently by two agents** working different domains (insertion, and scope analysis via
`select_list_end`), which is the strongest corroboration in this report.
Two defects compound — no trailing separator (the `1AND` hazard, on the one path that skips
`_separated`), and an offset one token late because a set operator ends the *branch* via `_branch_at`,
so control falls through to the whitespace token.

**3 — wrong query level.** The scan walks straight out of a subquery:

```
'WITH x AS (SELECT na) SELECT * FROM x'  →  'WITH x AS (SELECT auth_user.name) SELECT * FROM public.auth_user FROM x'
'SELECT 1 WHERE EXISTS (SELECT na)'      →  'SELECT 1 WHERE EXISTS (SELECT auth_user.name) FROM public.auth_user'
'SELECT * FROM (SELECT na) t'            →  'SELECT * FROM (SELECT auth_user.name) t FROM public.auth_user'
```

All three are syntax errors, and in every case the subquery that needed the relation still has none.
Control confirms separate *statements* are handled correctly (`'SELECT na;'` keeps its FROM in its own
statement) — it is specifically nesting that fails.

**4 — equal spans, and the LSP picks the wrong primary.**

```python
s = next(x for x in complete('SELECT ', 7, POSTGRES, CAT) if x.relation)
plan_insertion('SELECT ', s, dialect=POSTGRES).edits
# (Edit(span=(7, 7), text=' FROM public.auth_user'), Edit(span=(7, 7), text='auth_user.id'))
```

`Insertion.edits` documents itself as ordered "latest in the text first" so that "an earlier edit
cannot move a later one that has already been made". At `SELECT ⌶` — the commonest trigger position
there is — both edits sit at the *same* offset, so position gives no ordering and correctness rests on
tuple order alone; the other order yields `'SELECT  FROM public.auth_userauth_user.id'`.

Downstream this is live. `convert._split_edits` picks the primary with
`next(edit for edit in edits if edit.span[0] == span[0])`, which now matches the FROM edit:

```
text_edit           : ' FROM public.auth_user'
additionalTextEdits : ['auth_user.id']
```

Inverted, with two LSP edits at one range (undefined in the spec). Sharpest detail: the test
`tests/lsp/test_convert.py::test_the_edit_at_the_caret_is_the_primary_one` has the docstring
**"The FROM clause must not end up in text_edit"** — precisely what happens. It uses `'SELECT ema'`
(span `(7, 10)`), where the spans differ, so the equal-span case is untested.

*Not covered by:* `test_the_relation_goes_before_whatever_follows_the_select_list` (ORDER BY only);
both existing separator tests cover the `here` edit only; `tests/test_insertion.py`'s two relation-edit
tests are both top-level SELECTs.

### 7. Postgres leaves ~13,000 BMP code points unquoted, and the lexer then splits the name

`rank._plain_identifier` builds its pattern from `_NON_ASCII = '-￿'` — the entire BMP above
ASCII, not letters — while `Syntax.unquoted_non_ascii`'s own docstring says "Whether a non-ASCII
**letter** may go unquoted". `lex._is_ident_char` is far narrower, so quoter and lexer disagree.

```python
NAME = 'total\xa0due'                      # U+00A0 NO-BREAK SPACE
quote_if_needed(NAME, POSTGRES)            # 'total\xa0due'  — not quoted
```

Splicing it and re-lexing gives `['select', 'total', 'due', 'from', 't']` — one column became two
identifiers. End to end: accepting the suggestion writes
`'SELECT invoices.total\xa0due FROM invoices'`, and the engine re-reads its own output as
`prefix='due', kinds=['keyword']`, offering nothing. Hand-quoted, the same text reads correctly as
`prefix='total\xa0due', kinds=['column']`.

I measured the exposure myself: **15,037** mid-name code points are left unquoted and fail the
round trip, of which 2,048 are surrogates — so **12,989** reachable ones, matching the agent's count
exactly. 19 of those are Python whitespace (U+00A0, U+2000–200A, U+3000, …) where the name silently
*splits*; the rest (ZWSP, BOM, RTL override, en dash, `«`) lex as `IDENT + UNKNOWN + IDENT`.
Only Postgres is affected — ANSI, ClickHouse and Trino set `unquoted_non_ascii=False` and quote all of
it. Astral-plane names are over-quoted, which is harmless.

Postgres's own scanner accepts most of these bare, so this shows as an internal contradiction rather
than a server error — but the practical damage is real: the engine cannot re-read a statement it just
wrote, so every later completion in that statement is wrong.

### 8. `SELECT NULL` becomes an output column named `"null"`

`analyse._output_of` takes any single-token `IDENT` select item as an output name. Its two neighbouring
branches (`_is_an_implicit_alias`, `_is_a_call`) guard with `dialect.reserved_upper`; this one does
not. `rank._render` then quotes the name *because* it is reserved, and the `local` origin bonus ranks
it first.

```
'SELECT NULL FROM t GROUP BY '  →  top suggestion '"null"' (score 140.0, above every real column)
applied                         →  'SELECT NULL FROM t GROUP BY "null"'
```

The CTE case is worse — `c."null"` is the **only** suggestion offered:

```
'WITH c AS (SELECT NULL FROM t) SELECT  FROM c'  →  'WITH c AS (…) SELECT c."null" FROM c'
```

Postgres answers `ERROR: column c.null does not exist`; the real output name is `?column?`. Reproduced
for `NULL`, `TRUE`, `FALSE`, `CURRENT_USER`, `LOCALTIME`, `SESSION_USER`. `_output_of`'s own docstring
describes this case — *"an expression Postgres calls `?column?`, and nothing useful can be suggested
for it"* — so the intended behaviour is already written down. Control: `SELECT id AND id` correctly
offers nothing.

---

## Verified — crashes

### 5. Cache key collision between `tables` and `columns`

`_Reader._key` builds `(identity, dialect, *parts)`. Every reader sharing the 4-tuple shape got a
`\x00` sentinel — `'\x00schemas'`, `'\x00fk'`, `'\x00values:<col>'` — **except `tables`**:

```python
def tables(self, schema):          return self._read(self._key(schema or '', ''),    …)   # resolve.py:228
def columns(self, schema, table):  return self._read(self._key(schema or '', table), …)   # resolve.py:232
```

So `tables(None)` and `columns(None, '')` are the same key, and a quoted empty identifier reaches
`columns(_, '')` from ordinary carets. I confirmed five: `SELECT "".`, `SELECT * FROM "".`,
`… WHERE "".`, `… GROUP BY "".`, `… ORDER BY "".`

Both halves reproduce:

```
# relation list cached first, then read back as columns
AttributeError: 'Table' object has no attribute 'table'

# the other order — silent, and worse
cache after 'SELECT "".'  →  {('alice','postgres','',''): ()}
'SELECT * FROM ⌶' warm    →  ['public']
'SELECT * FROM ⌶' cold    →  ['users', 'orders', 'public']      # relations lost: {users, orders}
```

After one `SELECT "".` completion the relation list is silently empty for as long as that cache lives.
`lsp/` holds one long-lived cache per session, so both halves are live there; the crash half trips
`degrade()`, which sets `_catalog = None` and disables schema completion for the rest of the session.

Existing cache tests (`test_cache_is_keyed_by_role`, `test_one_cache_two_roles_do_not_leak`, …) all
assert the *identity* dimension — which holds, and I re-confirmed it. None asserts that two different
reads get different keys. Fix is a sentinel of `tables`' own.

### 9. An empty clause name crashes `complete()` and the conformance harness

Reached through documented composition only — `ClauseModel.extend` + `dataclasses.replace`, no
`object.__setattr__`:

```python
D = replace(ANSI, clauses=ANSI.clauses.extend(Clause(name='')))
DialectConformance.structure(D)      # → NO PROBLEMS REPORTED
complete('SELECT ', 7, D, CAT)       # → IndexError: list index out of range
DialectConformance.check(D)          # → IndexError: list index out of range
```

`analyse.py:1513` does `grouped.setdefault(name.split()[0], …)`, assuming every clause name has a
word. `Clause(name='   ')` does the same (`'   '.split()` is empty and `'   '.upper()` slips past the
uppercase check). Two contract breaches at once: `structure()` exists to report "a declaration that
contradicts the engine's own conventions" and reports nothing, and `check()`'s docstring promises
"a list rather than an exception so a caller sees every failure at once" — it raises. This is the
harness third-party dialect authors are told to run.

### 10. `RecursionError` on a long CTE chain, with no catalog at all

`resolve._columns_of` → `_from_projection` → `_columns_of` walks projection stars with no depth cap.
`analyse.py` caps its own descent at `_MAX_NESTING = 64` in a docstring naming this exact failure —
*"a RecursionError arrives at the editor as a crash rather than as a slightly-less-precise list."*
CTEs are siblings, not nested scopes, so that cap never applies to a chain.

```
depth 100: ok      depth 400: ok      depth 495: RecursionError      depth 600: RecursionError
```

`derive_request` survives; only `resolve` blows up. `tests/test_scale.py::test_absurd_nesting_does_not_exhaust_the_stack`
covers the pure stage with a different shape that never builds a projection chain.
Caveat: ~495 chained CTEs is generated-SQL territory, not hand-written.

### 37. A second unbounded recursion, in `analyse` — and it escapes the LSP

Distinct from finding 10, which is in `resolve`. This one is in the pure stage, needs no catalog, and
hits **all four dialects** identically:

```python
sql = 'WITH a AS(' * 495 + 'SELECT 1'    # 4958 chars
complete(sql, len(sql), ANSI)            # → RecursionError
```

494 is fine, 495 raises. I extracted the repeating frames to confirm the cycle:

```
['analyse.py:_read_ctes', 'analyse.py:select_outputs', 'analyse.py:_after_clause',
 'analyse.py:_clause_starting_at', '<string>:__hash__']
```

A second, independent cycle is reached through nested derived tables
(`'SELECT * FROM ' + '(SELECT * FROM ' * 1000 + 't'`), where `select_outputs` self-recurses.

**Root cause, confirmed in the source.** `_MAX_NESTING = 64` exists for exactly this — its docstring
says *"a RecursionError arrives at the editor as a crash rather than as a slightly-less-precise list"*
— but `grep` shows it is referenced only at `analyse.py:1072`, `1094` and `1111`, all inside
`_scope_level`'s `remaining` parameter. `select_outputs` and `_read_ctes` recurse with no bound at all.

It escapes the language server, which I verified directly:

```python
Session().suggest('WITH a AS(' * 495 + 'SELECT 1', 4958)    # → RecursionError
```

against a docstring reading *"Items for a caret at `offset` in `text`. Never raises."* With a catalog
attached it is worse: `_from_catalog` catches `Exception` (which `RecursionError` is), logs, and calls
`degrade()` — then falls through to the **unguarded** `complete()` at `server.py:181` and raises
anyway. One pathological document both spuriously degrades the session *and* fails the request.

`tests/test_scale.py::test_absurd_nesting_does_not_exhaust_the_stack` asserts depth 1500 is survivable,
but only for the `'SELECT ' + '(SELECT ' * n` shape, which happens to miss both cycles.
`tests/queries/test_subqueries.py::test_deeply_nested_derived_tables` and
`test_ctes.py::test_nested_cte_inside_a_cte_body` assert the recursion *should* happen — so the
recursion is wanted and only the bound is missing.

### 11. Capability detection is opposite on 3.10 and 3.12

CPython 3.12 changed `_ProtocolMeta.__instancecheck__` to use `inspect.getattr_static`. For a catalog
with a `__getattr__` proxy (a lazy wrapper — or any `MagicMock` in a downstream test suite), I ran the
identical code on both supported interpreters:

```
3.12.11  {Catalog: True, SupportsColumnSearch: False, …}   complete → []            # degrades cleanly
3.10.11  {Catalog: True, SupportsColumnSearch: True,  …}   complete → TypeError     # 'object' object is not iterable
```

`requires-python = '>=3.10'` and CI covers 3.10/3.11/3.12, so the same adapter passes on one supported
interpreter and violates "missing capability → never an error" on another.

### 23. A zero-length placeholder opener makes `lex` non-terminating — and hangs the conformance harness

`lex.py:225` computes `start = pos + len(placeholder.opens)`. With `opens=''`, `start == pos`, so the
scanner appends a zero-width PARAM token and never advances.

```python
SYNTAX = Syntax(placeholders=(Placeholder(opens='', body='none'),))
DialectConformance.structure(replace(POSTGRES, name='broken', syntax=SYNTAX))   # → []  (no problems)
lex('a', SYNTAX)                                                                # → never returns
```

I ran this under a 15-second timeout on a **one-character input**: exit code 124. `lex`'s own docstring
says "**Total**, never raises" — non-termination is worse than raising, since it takes the editor with
it. `DialectConformance.check` hangs too, because it runs `complete` over its case corpus: the shipped
self-check tool never returns rather than reporting the bad declaration. It already catches the sibling
case (`body == 'any'` with no `closes`, `testing/__init__.py:375-380`), so this belongs in the same
list.

Reachable only from a malformed third-party dialect — but third-party dialects are an advertised
extension point, and this is exactly the failure `DialectConformance` ships to prevent. Compare
finding 9: same harness, same class of blind spot.

---

## Verified — scope

`docs/gaps.md` states that the comparison against DBeaver "did not find… any gap in the analysis half.
Statement-wide scope, subqueries, CTEs, set operations and per-branch clause state are all handled
here." Findings 24–28 are all in that half. They are correctness bugs in a strong subsystem, not
missing features — the agent's 40,000-case fuzz plus every-caret sweeps over ~110 queries × 4 dialects
produced **zero** crashes, out-of-bounds spans or `RecursionError`s, so the tolerance story is intact.

### 24. A CTE declared inside a subquery leaks into the enclosing scope

`_read_ctes` (`analyse.py:1871`) calls `_after_clause(…, 'WITH', …)` with **no `depth`** — the one
caller that omits it, against a docstring saying depth "restricts the search to one query level". So
the statement-level CTE table is built from the first `WITH` at *any* depth.

```
'SELECT * FROM (WITH iq AS (SELECT 1 AS aa) SELECT * FROM iq) d, ⌶'
  scope.ctes → {'iq': Relation(source='cte', projection=('aa',))}
  offers     → [('iq', 'cte'), ('users', 'table'), …]
```

`iq` is local to the derived table; Postgres answers `relation "iq" does not exist`. The other
direction is worse — an inner CTE **rebinds a real table of the same name**:

```
'SELECT * FROM users WHERE id IN (WITH users AS (SELECT 1 AS zz) SELECT * FROM users) AND ⌶'
  outer relations → [('users', 'cte')]        # the real table, reclassified
  offers          → ['users.zz']              # id / name / email all lost
```

The outer `FROM users` is the genuine catalog table, and every one of its real columns disappears in
favour of a phantom. `tests/queries/test_subqueries.py::test_cte_in_subquery_in_where` asserts such a
CTE is readable when the caret is *inside* that subquery — correct, and what the depth-free lookup
buys — but nothing asserts it should be invisible outside.

### 25. A `WITH` nested inside a CTE body is invisible from inside that body

The mirror image of 24. `_scope_level` passes the pre-computed statement-level CTE set into a CTE
body, and that pass stopped at the outer `WITH`, so the inner body's own `WITH` is never read.

```
'WITH oq AS (WITH iq AS (SELECT 1 AS aa) SELECT ⌶ FROM iq) SELECT * FROM oq'
  relations → [('iq', 'table')]     # 'table', not 'cte' — treated as a catalog relation
  offers    → []
  outward view 'WITH oq AS (…) SELECT ⌶ FROM oq' → ['oq.aa']    # already correct
```

`select_outputs`'s own docstring states the requirement — *"`WITH outer_q AS (WITH inner_q AS (...)
SELECT * FROM inner_q)` has to reach inner_q"* — and the engine reaches it from outside but not from
inside. `tests/queries/test_ctes.py::test_nested_cte_inside_a_cte_body` asserts the outward direction
only.

### 26. `ORDER BY` after a set operation is scoped to the last branch

`_branch_at` cuts on set operators unconditionally, so a trailing `ORDER BY` — which belongs to the
whole set operation — inherits the final branch's `FROM`.

```
'SELECT name AS nm FROM users UNION SELECT total FROM orders ORDER BY ⌶'
  → ['total', 'orders.id', 'orders.user_id']
'SELECT id FROM users UNION SELECT id FROM orders ORDER BY orders.⌶'
  → ['id', 'user_id', 'total']
control, no UNION: 'SELECT name AS nm FROM users ORDER BY ⌶'
  → ['nm', 'users.id', 'users.name', 'users.email']
```

The legal answer is `nm`, the result column named by the **first** branch — reachable normally, and
unreachable after a UNION. Everything offered instead is refused by the server: *"ORDER BY on a
UNION/INTERSECT/EXCEPT result must be on one of the result columns"*, and the qualified form
`orders.id` can never parse there at all. `tests/queries/test_set_operations.py` covers select-list and
`WHERE` per branch (all correct) but has no `ORDER BY` case.

### 27. A caret at the end of a *terminated* line comment offers suggestions

`_inside` treats `caret == token.end` as outside. For a string that is right — `end` is past the
closing quote. For a line comment `end` is the index **of** the newline, so `caret == end` is still
inside the comment text, which is exactly where a typist's caret sits.

```
'SELECT * FROM users -- note⌶\nSELECT 1'   kinds=(KEYWORD,)  → ['u', 'use', 'AS', 'JOIN', 'LEFT JOIN']
'SELECT * FROM users -- note⌶'             kinds=()          → []          # control, correct
```

Accepting `JOIN` splices it into the comment. `docs/request-pipeline.md` states the `terminated` rule
and says "a line comment reaching end of input is therefore unterminated, which is what makes
`-- note ⌶` suppress suggestions" — it never considers the terminated case, and both the test and the
corpus case use only the EOF form. The insertion agent independently noticed this symptom and declined
to file it as out of its scope; it is the same bug, now diagnosed.

### 28. The `INSERT` target relation is in scope for the source `SELECT`

`_RELATION_CLAUSES` includes `'INSERT INTO'`, so the target joins the source query's scope:

```
'INSERT INTO users (id) SELECT ⌶ FROM orders'
  scope  → ['users', 'orders']
  offers → ['orders.id', 'users.id', 'users.name', 'orders.user_id', 'orders.total', 'users.email']
```

`INSERT INTO users (id) SELECT name FROM orders` gets `ERROR: column "name" does not exist`.

**This is not a one-line fix, and the agent was right to flag the tension.** I confirmed the two
positions that need the target visible still work today and would regress if it were simply removed
from `_RELATION_CLAUSES`:

```
'INSERT INTO users (⌶'                                     → ['users.id', 'users.name', 'users.email']
'INSERT INTO users (id) SELECT id FROM orders RETURNING ⌶' → ['orders.id', 'users.id', …]
```

The target must be visible to the column list and to `RETURNING`, and invisible from the `SELECT`
onward.

### 29. Nested derived tables cost roughly cubic

Measured independently, and steeper on my machine than the agent reported:

```
depth  16   len   286    0.078s
depth  32   len   542    0.429s   ×5.5
depth  64   len  1054    3.352s   ×7.8
```

`_scope_level` recurses to `_MAX_NESTING = 64` and each level calls `select_outputs` over its whole
range, which recurses again through `_derived_tables` and `_read_ctes`. `tests/test_scale.py` sets
`_BUDGET_SECONDS = 1.5` and asserts "four times the query should not be sixteen times the work" — here
a **1 KB** query at exactly `_MAX_NESTING` exceeds that budget by 2.2×, at the depth the cap's own
comment anticipates ("Nobody writes sixty-four, but a code generator does"). The cap bounds recursion
depth but not cost. Width is fine (200 CTEs at 0.16s, linear), so this is specific to depth. Related
to finding 17, but distinct: that one is quadratic on *unclosed* shapes, this is cubic on closed ones.

---

## Verified — literals and prefixes

### 21. `E'…'` and `$$…$$` literals derive a prefix that still contains the quote

`request.py:189` does `quote = written.text[0]`, assuming a STRING token starts with its quote
character. The lexer correctly includes the `E` prefix in `Token.text`, so `text[0]` is `E`:

```
"x = 'an"    → text="'an"     text[0]="'"     ✓
"x = E'an"   → text="E'an"    text[0]='E'     ✗
'x = $$clic' → text='$$clic'  text[0]='$'     ✗
```

The prefix is therefore sliced one character too early, and the damage differs by shape:

```
"… name = 'an"   prefix='an'    → ["'ann'", "'bann'"]     # correct
"… name = E'an"  prefix="'an"   → ["'ann'"]               # 'bann' silently dropped
"… type = 'clic" prefix='clic'  → ["'clickhouse'"]        # correct
"… type = $$clic" prefix='$clic' → []                     # nothing at all
```

The E-string case only half-survives by accident — the contaminated prefix `'an` still matches
`'ann'` on ranking's substring tier, but not `'bann'`. Dollar quoting has no such rescue and goes
silent. Nothing in `docs/gaps.md`, the specs, or the `_inside_a_literal` docstring refuses this;
the existing E-string tests assert token shape only, never the prefix.

### 22. Typing one more character of the value the engine just offered makes it vanish

`request.py:191` un-doubles the prefix (`typed.replace(quote * 2, quote)`), but the VALUE candidate it
is matched against is the *doubled*, SQL-quoted form built in `resolve.py:876`, with no `match_text`.
So `rank._match_strength` compares `o'b` against `'o''brien'`:

```
"… name = 'o"      prefix='o'     → ["'o''brien'"]
"… name = 'o''"    prefix="o'"    → ["'o''brien'"]        # survives by substring luck
"… name = 'o''b"   prefix="o'b"   → []                    # gone
```

The engine offers `'o''brien'`, and typing the next character of its own suggestion drops it. The two
sides should be compared in one representation. `test_a_half_typed_literal_still_completes` states the
intent — *"typing the opening quote is the natural next keystroke, and going silent there makes the
feature look broken"* — but covers the plain case only.

---

## Verified — dialects, registry and generated SQL

### 6. `before_the_item` bypasses the `__post_init__` keyword fold

`Dialect.__post_init__` folds `clause.name`, `followed_by`, `after_operand`, `opens_a_group` and
`defines_columns` into `keywords` — but **not** `before_the_item`, which `request.py` returns as a
suggestion list all the same. CLAUDE.md states the rule: *"A word the model can suggest but `keywords`
omits reads as an identifier to the analyser, so never bypass this."*

My own sweep over all four dialects:

```
ansi        {'IF': ['CREATE TABLE']}
postgres    {'CUBE': ['GROUP BY'], 'GROUPING': ['GROUP BY'], 'IF': ['CREATE TABLE'],
             'ROLLUP': ['GROUP BY'], 'SETS': ['GROUP BY']}
clickhouse  {'IF': ['CREATE TABLE']}
trino       {'IF': ['CREATE TABLE']}
```

Postgres offers `ROLLUP` at `GROUP BY rol⌶` and then cannot read it back:

```
'… GROUP BY ROLLUP ⌶'  postgres  →  ['HAVING', 'WINDOW', 'UNION', 'INTERSECT', 'EXCEPT']
'… GROUP BY ROLLUP ⌶'  + folded  →  ['id', 'count', 'events.name']          # correct
'… GROUP BY ROLLUP ⌶'  trino     →  ['id', 'count', 'events.name']          # correct
```

`ROLLUP` reads as GROUP BY's *item*, so the caret after it offers clause continuations; accepting one
writes `GROUP BY ROLLUP HAVING …`, which Postgres refuses. The tell is that Trino — which never offers
the word but has `rollup` in `RESERVED` — analyses it correctly, while Postgres, the only dialect that
offers it, does not. The `IF` gap is inert today (CREATE TABLE has `suggests=()`); the four GROUP BY
words are not. `tests/grammar/cases.py` asserts `GROUP BY rol⌶` and `GROUP BY ROLLUP (⌶` but never
`GROUP BY ROLLUP ⌶` without the paren.

### 12, 13, 20. `catalogs/dbapi.py:render`

**12 — binds values no marker asks for.** `qmark`/`format` build their parameter tuple from markers
that actually occur; `numeric` returns `tuple(values)` and `named`/`pyformat` enumerate `values`,
ignoring the SQL:

```
qmark     ('SHOW FUNCTIONS', ())          format    ('SHOW FUNCTIONS', ())
numeric   ('SHOW FUNCTIONS', ('public',)) named     ('SHOW FUNCTIONS', {'p1': 'public'})
pyformat  ('SHOW FUNCTIONS', {'p1': 'public'})
```

A positional driver rejects this outright (sqlite3: *"uses 0, and there are 1 supplied"*). Latent —
no shipped adapter uses `numeric`, and dict-style drivers tolerate extra keys.

**13 — `$0` binds the last value.** `positional()` computes `int(...) - 1`, so `$0` → index `-1`:

```
render('SELECT $0, $1', ('first', 'second'), 'format')  →  ('SELECT %s, %s', ('second', 'first'))
render('SELECT $3',     ('a',),              'format')  →  IndexError                    # loud
```

`$0` is the one index that fails *silently* and produces valid SQL bound to the wrong value — and
`Template.snippet` in the same package uses `$0` with a different meaning, making it a plausible
mistake for a dialect author.

**20 — markers rewritten inside strings, comments and dollar-quoted bodies.** The rewrite is a
context-free regex:

```
"SELECT '$1 is text', x WHERE s = $1"   →  ("SELECT '%s is text', x WHERE s = %s", ('a', 'a'))
'SELECT 1 -- costs $1\nWHERE s = $1'    →  ('SELECT 1 -- costs %s\nWHERE s = %s',  ('a', 'a'))
'SELECT $$ body $1 $$ WHERE s = $1'     →  ('SELECT $$ body %s $$ WHERE s = %s',   ('a', 'a'))
```

Latent — no shipped query trips it — but Postgres is both the dialect with `dollar_quoting=True` and
the one whose introspection SQL is likeliest to grow a `$$` body, and nothing states the restriction.
`$10` beside `$1` is handled correctly, and a bare `$` is left alone.

### 14. A plugin named `postgres` silently replaces the built-in

`registry.available()` does `found[entry.name] = loaded` with no collision check, and the four
built-ins are registered through **the same entry-point group** (`pyproject.toml:40-44`), so they are
not privileged — the winner is whichever distribution `importlib.metadata` enumerates last. The agent
demonstrated this with a fake distribution; I confirmed the mechanism in the source and the shared
group. `lsp/connections.py` resolves the dialect this way and hands it to `DbapiCatalog`, so a
shadowing dialect's introspection SQL is what reaches the user's real database — while the paramstyle
still comes from the hard-coded `DRIVERS` table. There is no way to ask for the shipped one, and
nothing warns.

Registry behaviour was otherwise good: unimportable modules, missing attributes and non-`Dialect`
objects are all skipped without disturbing the rest.

### 15. `available()` hands out its cached mutable dict

```
same object returned twice: True
after a caller mutates it → named('postgres') == 'not a dialect at all'
```

`@cache` on a zero-arg function returns the same `dict` to every caller; one mutation poisons the
registry process-wide and defeats the `isinstance(loaded, Dialect)` guard.

### 16. `DialectConformance` passes a dialect whose catalog SQL always raises

```python
broken = replace(POSTGRES, catalog_queries=replace(POSTGRES.catalog_queries,
    columns=Query(sql='SELECT 1 WHERE s = $1 AND t = $2 AND x = $3', row=lambda r: r)))
DialectConformance.check(broken)   # → PASSES (empty list)
```

`DbapiCatalog` fixes each query's arity (`columns` gets 2 values), so "the highest `$N` exceeds what
this query will ever be given" is a static contradiction of exactly the kind `structure()` reports
elsewhere — but it never inspects `catalog_queries` at all. Conformance ships in the wheel so a
third-party dialect has one test; a `$N` typo is the mistake it will not catch. Its stated limits
("not a test against a server", "a dialect wrong but self-consistent passes") do not cover this: a
marker count needs neither a server nor consistency.

---

## Verified — smaller

### 17. Unclosed nesting is super-quadratic — a 2.4 KB half-typed query costs 33 seconds

Three agents hit this independently. The severe shape is **unclosed** nested derived tables:

```
'SELECT * FROM ' + '(SELECT * FROM ' * depth + 't'
  depth  20   len   315     0.096s
  depth  40   len   615     0.693s   ×7.2
  depth  80   len  1215     5.307s   ×7.7
  depth 160   len  2415    32.636s   ×6.1
```

Closed, the same nesting is trivial — 0.140s at depth 80 against 5.3s unclosed, **38× cheaper**. Bare
unclosed parens are cleanly quadratic on top of that (`'('*n`: 0.097s → 1.5s → 5.9s for n = 1k → 4k →
8k, ×3.9 per doubling; the fuzzer measured **962s at n = 100,000**), while balanced parens stay linear
(0.32s at n = 50,000). `lex` is linear throughout, so all of it is in analyse.

**Root cause, confirmed in the source.** `_unclosed_call_depth` (`analyse.py:1427`) ends:

```python
starts = tuple(index for index in open_groups if not _opens_a_query(tokens, index, hi, dialect))
if not starts:
    return lambda _: 0
return lambda index: sum(1 for start in starts if start < index)
```

`_relations_in` calls that lookup once per token, so it is O(unclosed × tokens). `starts` is built by
pushing onto a stack, so it is **already ascending** — a `bisect` would make it O(log n). The
zero-dangling-paren fast path is fine and the docstring is right about it; only the dangling path is
quadratic. The remaining ~40% is `clause_at`'s `while depth >= 0` loop calling the O(n) `_group_start`
and `_scan_for_clause` once per depth.

`tests/test_scale.py::test_the_cost_grows_with_the_query_not_with_its_square` states the invariant in
its name and asserts `cost(1000) / cost(250) < 8`; this shape blows past it. `analyse.py:1493` records
that a previous quadratic was removed precisely because *"a query long enough to matter is exactly the
one worth completing"*. **A half-typed query with unclosed parens is not an edge case — it is the state
the editor is in on most keystrokes**, which is what lifts this above a curiosity. See also finding 29
for the closed-but-deep cubic shape.

### 18. Negative `limit` slices from the end

`rank.py:147` is `ordered[:limit]`, so a negative limit drops the *last* N instead of clamping:

```
limit=None → 6 items    limit=0 → []    limit=-1 → 5 items    limit=-3 → 3 items    limit=-100 → []
```

`limit=0` is already correct, so only the negative branch leaks Python slice semantics. `complete()`
also forwards `limit * 5` to `resolve`, so the two layers disagree.

### 19. `without()` is a silent no-op on a name that is not there

```
ANSI clauses: 32  →  .without('FORM')  →  32          (typo, no signal)
postgres._ansi('FORM')  →  raises KeyError            (the deliberate contrast)
```

`clickhouse.py` does `ANSI.clauses.without('CALL', 'TABLE')`; a typo there leaves ClickHouse offering
a statement its parser rejects. `postgres.py`'s `_ansi()` makes the opposite choice for a stated
reason — *"a name that is not in ANSI is a typo, and a silently absent clause would drop the
refinement without a word"* — and the same argument applies here. Separately, the fold is union-only,
so `extend` then `without` leaves the word behind claiming keyword status.

---

## Verified — the language server

`server.py`'s module docstring: *"One rule governs every handler: a completion request never fails."*
Findings 30 and 33 break it. 31 and 32 corrupt the user's document when the edit is applied.

Worth stating first, because it bounds the rest: **`Session.suggest` itself never raised** across 432
statement × dialect combinations at every caret offset — astral characters, NUL, BOM, lone CR, 200
nested parens, unterminated literals, unknown dialect strings — with zero out-of-range ranges. Twenty
overlapping completions, twenty completions racing sixty `didChange`s, and a mid-flight
`$/cancelRequest` were all answered cleanly. The failures below are in the layer *above* `suggest`.

### 30. Completion for a URI with no open document raises

```python
workspace.get_text_document('file:///d%3A/tmp/never-opened.sql')   # per its docstring, "create[s] one pointing at disk"
document.source                                                     # → FileNotFoundError
```

I reproduced the `FileNotFoundError` directly; the agent confirmed it reaches the editor over stdio as
JSON-RPC `-32603` with a traceback. `server.py:248-250` has no guard between `get_text_document` and
the two calls that read `.source`.

Three reachable routes, all confirmed end to end: `didClose` then a completion already in flight (a
genuine race — `didClose` runs on the event loop while `completion` is `@server.thread()`'d onto the
pool); two spellings of one Windows path (`file:///d%3A/…` vs `file:///D%3A/…`); and a closed
`untitled:` document.

**The `untitled:` variant is worse than a crash.** `untitled:Untitled-1` resolves to the *relative*
path `Untitled-1`, read from the server's CWD — so when that path happens to exist, there is no error
at all, just a completion list built from a different file's contents:

```
resolved path: Untitled-1     source read: 'WITH from_the_disk AS (SELECT 1) SELECT * FROM fro'
items: ['from_the_disk']
```

Nothing in `tests/lsp/` mentions a missing document, a URI, or `get_text_document`.

### 31. Edit ranges are code-point offsets; the server advertises UTF-16

The conversion is asymmetric. Inbound, `offset_at_position` runs pygls's `PositionCodec` and decodes
UTF-16 units correctly — which is why the caret lands right. Outbound, `documents.to_position` returns
`offset - starts[line]`, a raw code-point index, straight into `Position.character`. Nothing in `lsp/`
ever calls `position_to_client_units`.

```
'WITH recent AS (SELECT 1) SELECT 🙂 FROM rec'    code points 43, utf-16 units 44
range=(40, 43)  new_text='recent'
client applies → 'WITH recent AS (SELECT 1) SELECT 🙂 FROMrecentc'
should be      → 'WITH recent AS (SELECT 1) SELECT 🙂 FROM recent'
```

Off by one UTF-16 unit per astral character before the caret, and it accumulates (three emoji give
`FRrecentrec`). A BMP accent is unaffected — I confirmed `é` round-trips correctly, so this is
specifically astral characters: emoji, `𝐀`, CJK extension blocks. The server's own `initialize` result
carries `positionEncoding: utf-16`, and LSP 3.17 makes UTF-16 the default regardless. Every existing
range test uses pure-ASCII documents; grepping `lsp/`, `tests/lsp/`, `docs/` for
`utf-16|code unit|astral|surrogate` returns nothing.

### 32. A lone CR splits lines for the client and for pygls, but not for `line_starts`

`documents.line_starts` counts only `'\n'`; the inbound half of the same request uses
`TextDocument.lines`, i.e. `str.splitlines(True)`, which splits on `\r`. One request, two line models.

```
'WITH recent AS (SELECT 1)\rSELECT * FROM rec'
inbound offset=43 (correct)   outbound range: line=0, chars=(40, 43)
                              …but line 0 is only 25 characters long
client applies → 'WITH recent AS (SELECT 1)recent\rSELECT * FROM rec'
should be      → 'WITH recent AS (SELECT 1)\rSELECT * FROM recent'
```

The suggestion is right and the caret decodes right; only the range is wrong, so the client inserts at
a clamped position and leaves the user's `rec` behind. I confirmed `\n` and `\r\n` both produce a
correct `line=1` range, so this is specific to a lone CR. The mirror case is the same root: `splitlines`
also splits on `\f`, `\v`, `\x85`, U+2028 and U+2029, which editors do *not* treat as line breaks, so
the caret itself lands wrong — that half originates in pygls but reaches the user through this handler,
and a U+2028 inside a string literal is enough.

### 33. No connect timeout on the live path

```
connections._connect  passes timeout: False
check._timed_connect  passes timeout: True   (CONNECT_TIMEOUT = 5)
```

Confirmed by reading both functions. They are the same argument-assembly code sixty lines apart, and
only the check path is bounded — with a comment explaining why: *"The driver gives up before the caller
has to kill the process."* So "Test connection" gives up in 5 s while the live server hangs 21 s on
Windows (~130 s on Linux) against the same unroutable host. `Session._lock` covers `catalog()`, so
every caret arriving during the wait blocks too — the agent measured eight concurrent completions all
paying the full duration. It happens once per server lifetime (`_tried` prevents retry), but that once
is the first completion after opening a file with a database behind a VPN that is down.

### 34. Every keystroke re-lexes the whole document

```
2.6 MB   didOpen + first completion 2.8s   second completion 3.0s
10.5 MB  didOpen + first completion 11.3s  second completion 10.4s
```

Nothing is cached between requests, so the second keystroke costs what the first did. `TRIGGERS`
includes `' '`, so every space fires one, and pygls's pool has 32 workers — a few seconds of typing can
queue minutes of CPU. Growth is *linear*, which matches `test_scale.py`'s stated intent ("the shape of
the cost, not a benchmark figure"), so the complexity is a decision; the constant and the absence of
any per-document cache are the finding. Within the conversion, `to_item` calls `plan_insertion` per
suggestion, re-lexing the statement once per item (~44 ms/item at 1 MB).

### 35, 36. The error paths lose the message

```
check() with a messageless driver error → {'ok': False, 'detail': ''}
degrade() with the same error           → 'DatabaseError'

degraded notification → "{'S': 'FATAL', 'C': '28P01', 'M': 'password authentication failed for user \"report\"'}"
check() on the same error → 'password authentication failed for user "report"'
```

Each module has the fix the other is missing. `check.describe` ends with a bare
`' '.join(str(error).split())` and no class-name fallback, so an error with no message reduces to `''`
— against a docstring saying *"A verdict is the product; an exception would leave the caller with
nothing to show, which is the state this feature exists to end."* Not synthetic: any HTTP answer with a
non-200 status and an empty body (a proxy's bodiless 503, a bodiless 401) produces exactly this.
Conversely `Session.degrade` never calls `describe`, so it emits pg8000's raw dict into a notification
whose docstring says it carries *"why"* for display — the exact shape
`test_check.py::test_a_server_error_is_reduced_to_its_message` pins as *"unreadable"*.

---

## Unsure

### U1. A function taking arguments loses its inside-the-parens caret when blanks remain

```python
s = Suggestion(text='count', kind=Kind.FUNCTION, replace_span=(7, 7), score=1.0, takes_arguments=True)
plan_insertion('SELECT  FROM  AS ', s, pending=(13, 17)).caret   # 20  — past the '()'
plan_insertion('SELECT  FROM  AS ', s).caret                     # 13  — between the parens
```

Reproduces. `plan_insertion` computes the inside-the-parens caret, then `if moved and finished`
discards it. Why this is a design call: the comment at `api.py:132-135` states outright that these are
"two questions with different answers" and that a function taking arguments "finishes its blank and
still wants the list open", so `finished=True` is deliberate. Which answer should win when both a
pending blank and an empty argument list are outstanding is the maintainer's decision. Reachable only
when a front end cycles to a non-first blank.

### U2. One unclosed paren makes every later `;` non-splitting, so statements merge

The behaviour reproduces and is harmful; the mechanism is documented, which is why this is a design
call rather than a straight defect.

```
'SELECT count(*) FROM users;\nSELECT ⌶ FROM orders'   ; depth=0  → ['orders.total']
'SELECT count(* FROM users;\nSELECT ⌶ FROM orders'    ; depth=1  → ['orders.total', 'users.uname']
```

`lex` never resets `depth`, so a `;` after an unclosed `(` carries `depth == 1`, and
`analyse.statement_at` splits only on depth-0 semicolons — so the earlier statement's relations leak
into the later one's select list. In an LSP holding a whole multi-statement buffer, one missing
parenthesis anywhere above the caret poisons everything below it.

`docs/request-pipeline.md:29-30` states the rule outright: "splitting on depth-0 semicolons so a `;`
inside a string or parens does not divide." So the mechanism is intended. But I checked the stated
rationale and it does not hold up: a `;` cannot legally sit inside parentheses in any of the four
dialects, and the one construct that looks like a counterexample — a `;` in a dollar-quoted function
body — is a single STRING token, not a depth-1 punct. I verified this:

```
'CREATE FUNCTION f() … AS $$ BEGIN; RETURN 1; END; $$ …'
  → ; tokens at depth>0: []      (the whole body is one STRING token)
```

So the "or parens" half of the rule protects against nothing reachable, while `depth > 0` at a `;` is
always evidence of an unclosed paren — which makes "do not split" the strictly worse recovery. The
maintainer may still prefer the current rule for simplicity; that is the call. No test asserts the
current behaviour.

---

## Checked and dismissed — not findings

Recorded so they are not rediscovered.

- **No FK is ever inferred from column names.** With a catalog declaring zero FKs, `JOIN ⌶` and `ON ⌶`
  offer no `Kind.JOIN` and no `fk:` note. Matches `docs/gaps.md` gap 1 and `engine/joins.py`.
- **No path reads table data.** At `WHERE s = ⌶` the only calls are `columns` and `common_values`; a
  `RESTRICTED` column is not queried at all. The Postgres `values` query is `pg_enum` ∪ `pg_stats`.
- **Missing capability → fewer suggestions, never an error.** Verified per protocol at six carets
  each — every absence gave a shorter list and zero exceptions. (Finding 11 is the 3.10 exception.)
- **No cross-identity cache leak.** 15 identities including `''`, `'a:b'`, `'a\x00b'` — no key shared
  between two distinct identities. Keys are tuples, so separator injection is impossible. Finding 5 is
  a collision *within* one identity, not across.
- **Ranking is a genuine total order and deterministic.** Brute-forced antisymmetry and transitivity
  over a 60-candidate pool: 0 violations. Identical output under seven `PYTHONHASHSEED` values. The
  agent's initial "pairwise disagreement" was an artifact of its own harness and it retracted it.
- **Quoting is otherwise sound.** 216 hostile names × 4 dialects plus a full 0x00000–0x2FFFF sweep:
  the only failures are finding 7's Postgres set. `a"b`, `` a`b ``, backslashes, NUL, `;`, `--`,
  reserved words, digit-leading, emoji, `ß`, Turkish `İ` all round-trip on all four dialects.
- **Local candidates are not harvested from strings or comments.** `_SKIP` excludes comments and no
  `_output_of` branch accepts a `STRING` token. Finding 8 is a *keyword* leaking through, not a string.
  This also means `docs/gaps.md`'s refusal of DBeaver-style "word completion from the open document"
  is intact.
- **Keyword casing ignores strings and comments**, correctly.
- **`resolve` never mutates the caller's sequences**; bounded on a 100k-column catalog (0.31–0.68s).
- **Mid-word carets producing `orders.statetate`** — the documented replace-to-caret contract.
- **Garbage-in/garbage-out from an adapter that breaks its own declared types** (returning `None`, an
  `int`, `Column(name=None)`) propagates, and nothing promises otherwise. Correctly not a finding.
- **`Table.rows` unused, no hover docs, no bound-parameter names, async** — all named as open gaps in
  `docs/gaps.md`, so known, not findings.
- **The lexer core is genuinely solid.** ~240,000 inputs across four dialects (fragment
  cross-products, random concatenation, character soup with NUL, lone surrogates, astral chars, ZWSP,
  NBSP, RTL marks, combining marks): never raised, never produced a zero-width, inverted,
  out-of-bounds, non-tiling or negative-depth token, and linear on every pathological shape tried.
  Literal termination, nested/unnested comments, dollar-quote tag matching, `;` inertness inside
  strings/comments/identifiers/dollar-quotes (a 15-spelling differential harness found 0 differences),
  and `depth` clamping at a stray `)` were all specifically attacked and were correct. Findings 21–23
  are in `request.py` and in a malformed dialect declaration, not in the scanner.
- **`Syntax.unquoted_extra` / `unquoted_non_ascii` are ignored by the lexer** (`_is_ident_char`
  hardcodes `'_$'`); they drive quoting only. Consistent with "tolerant", so not filed — though it is
  the same quoter/lexer split that finding 7 turns into a live bug.
- **CR-only line endings** make a line comment swallow the rest of the buffer (`_scan_line_comment`
  searches for `'\n'` only). Filed only as the LSP half (finding 32), where it corrupts a document.
- **No credential leak found.** `Profile.__repr__` omits the password (`repr=False`); neither bundled
  HTTP reader puts credentials in a URL (ClickHouse uses `X-ClickHouse-Key`, Trino an `Authorization`
  header), so `TransportError` text carries no secret. Six configurations with a password set all
  reported `no password`. Residual hazard recorded but **not** filed: both `degrade` and `describe`
  pass driver exception text through verbatim, so a driver that echoes its DSN would leak into the
  notification and the log. None of the three bundled drivers does.
- **The version-agreement check is complete — I was wrong to suspect otherwise.** I primed the LSP
  agent with the hypothesis that the fourth version location might be unchecked. It verified that
  `tests/test_purity.py::test_the_extension_version_matches_the_library` does read
  `editors/vscode/package.json`. No gap; the hypothesis was mine and it was wrong.
- **Malformed LSP config, document sync abuse, concurrency and cancellation are all clean.** 15 shapes
  of bad `initializationOptions` (including `null`, `[]`, a bare DSN string) all answered; `didOpen`
  twice, out-of-bounds ranges, `end` before `start`, disagreeing `rangeLength`, versions going
  backwards and `didClose` twice never crashed the server. A negative line/character makes the request
  vanish without a response, but that is upstream in pygls and violates the LSP spec.
- **The completion handler reads the document twice** (`offset_at_position` then `.source`), so a
  `didChange` between them mixes one revision's offset with another's text. Not filed: the agent could
  not hit it in 4000 attempts with real threads, the offset is clamped, and the module docstring
  explicitly accepts a stale answer ("a completion whose answer arrives late is one the next keystroke
  has already replaced").
- **Argument-surface abuse is handled gracefully.** `caret` of `-1000`, `10**12`; `limit` of `0`,
  `-1000`, `10**12`; `catalog=None`, `cache=None`, empty/huge/NUL `identity` — no exceptions, and every
  `replace_span` stayed in `[0, len(sql)]`. Wrong-*typed* arguments (`dialect=None`, `sql=1`) raise
  plain `AttributeError`s; in a `mypy --strict` library shipping `py.typed`, a runtime guard would not
  earn its keep, so these are recorded as observed rather than filed.
- **A hand-fabricated `Suggestion` span** (`replace_span=(-3, 2)`) yields a caret of `-2`. The library
  never emits such a span and the span is documented as travelling with the suggestion, so a caller
  inventing one is out of contract.

---

## Coverage

What the sweep actually executed, so the negatives carry weight:

| Harness | Scope | Volume | Result |
| --- | --- | --- | --- |
| Caret sweep | 951 corpus queries × 4 dialects × every caret, each suggestion applied and re-completed | 144,464 triples | clean |
| Mutational | 30,000 rounds: truncation, char edits, unbalanced quotes/parens/comments, astral + combining + zero-width + NBSP + RTL + NUL + lone surrogates | 124,930 | clean |
| Grammar fuzz | 4,000 generated statements (CTEs, joins, subqueries, set ops, windows, CASE, casts, DML) at every caret | 212,015 | clean |
| Differential | identical input through all four dialects | 727,184 | 0 raise-splits; 1,061 count-splits, all by design |
| Output invariants | limit respected, determinism, no duplicate `(text, kind)`, score non-increasing | 356,736 | clean |
| Resource limits | 18 scaling families to 11 MB, 1M-char tokens, depth 100,000 | ~500 timed | findings 17, 29, 37 |
| Lexer sweep | fragment cross-products, character soup, span tiling | ~240,000 | clean |
| Scope sweep | token salad × 4 dialects, carets −3…len+3, plus ~110 queries × every caret | ~40,000 | clean |
| LSP | 432 statement×dialect at every caret, concurrency, cancellation, malformed sync and config | — | findings 30–36 |

Roughly **1.9 million** `complete()` calls in total. Every crash in the campaign came from
resource-limit probes; the engine's answers themselves never faulted.
