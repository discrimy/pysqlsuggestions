# Placeholders and star expansion — design

Date: 2026-08-12
Status: **proposed**. Nothing built yet.

Closes gaps 5 and 1 of `docs/gaps.md`, which are the two entries on that list
living entirely in the pure half of the engine. Neither needs a catalog
capability, a new port, or an adapter change, and both are testable with no
connection at all.

---

## 1. Context

### What the engine does at these positions today

Measured, not assumed, against a `MemoryCatalog` holding
`users(id, user_id, usage_count)` under Postgres:

| caret | offered today | should be |
| --- | --- | --- |
| `WHERE u.id = :us⌶` | `u.user_id`, `u.usage_count` | nothing |
| `WHERE u.id = ? ⌶` | `u.id`, `u.user_id`, `u.usage_count` | `AND`, `OR`, `GROUP BY`, … |
| `WHERE u.name = ${re⌶` | every column matching `re` | nothing |
| `WHERE u.id = $1 ⌶` | `AND`, `OR`, … | the same, but on purpose |
| `SELECT *⌶ FROM users u` | `WHERE`, `GROUP BY`, `HAVING`, … | the column list, then those |

The fourth row is the interesting one: `$1` lexes as an UNKNOWN `$` followed by
a NUMBER, and `after_operand` treats a number as the end of an operand, so the
right answer arrives for the wrong reason. `?` lexes as an OPERATOR, which opens
an operand rather than closing one, which is why the second row is wrong.

Gap 5 is the only entry in `gaps.md` that describes an *active wrong answer*
rather than a missing one. Accepting `u.user_id` at `= :us⌶` writes
`WHERE u.id = u.user_id` — valid SQL, silently not the query the author meant.

### Prior art in this codebase to follow

- **A dialect is data composed with `dataclasses.replace`**, not a class you
  subclass. `dialects/base.py` says so in its module docstring. Anything a
  caller might need to vary belongs in a record, and varying it needs no new
  argument on `complete`.
- **`Syntax` is read by the lexer and by nothing else.** Its own docstring
  makes that claim, and the placeholder spelling belongs there for the same
  reason `identifier_quotes` does.
- **`Candidate` carries optional fields for candidates that are special.**
  `literal`, `snippet`, `match_text`, `qualifier`, `relation` and `note` all
  exist because one kind of candidate needed something the others do not.
- **`joins.py` builds a Candidate whose text is a whole rendered clause**, doing
  its own quoting through `quote_if_needed` while staying pure. A star expansion
  is the same shape of thing.
- **`replace_span` travels with the suggestion.** `lsp/convert.py` says so in
  its module docstring; `plan_insertion` and `demo/payload.py` both read it off
  the `Suggestion` rather than the `Request`.
- **`tests/test_purity.py`** forbids `engine/` importing `ports` or `resolve`.
  Everything in §3 and §4.2–4.4 is pure; only §4.5 touches the catalog.

### Decisions taken during brainstorming

1. **Two gaps, one spec.** Both are position analysis plus a small type change,
   both are offline-testable, and neither introduces a protocol. Splitting them
   would duplicate the testing and documentation sections and buy nothing.
2. **Silence only for placeholders.** No caller-supplied bound parameter names,
   no `Kind.PARAMETER`, no new argument on `complete` or `derive_request`. The
   complaint in `gaps.md` is that a wrong answer is given; stopping it is the
   deliverable. Offering bound names is a separate feature with its own API
   surface, and every front end in this repo would then have to decide whether
   to pass it.
3. **A bare `*` expands qualified once more than one relation is in scope**, and
   bare when there is exactly one. It is the only rule that always emits SQL the
   server accepts: `users` and `orders` both have `id`, and an unqualified
   expansion of the two is `ERROR: column reference "id" is ambiguous`.
4. **`${var}` ships as a constant wired into no dialect.** It is a templating
   convention — dbt, Metabase, Jinja over any backend — not a backend's syntax.
   Putting it in ANSI would state something false about the standard. A caller
   who wants it composes it in, which is already how this codebase is extended.
5. **No cap on expansion width.** A three-hundred-column relation expands to a
   three-hundred-column line. That is what DBeaver does and what somebody who
   put the caret on a star and asked for its columns asked for. A cap would have
   to pick a number and would silently truncate a list the author then has to
   finish by hand.

### Rejected approaches

- **A regex per placeholder spelling.** `engine/lex.py` is a hand scanner that
  imports `unicodedata` and nothing else; `re` appears only in `rank.py`. A
  record says *what the shapes are*, a pattern says *how to find them*, and a
  third-party dialect gets the first wrong less often than the second.
- **Reading the star as a prefix.** Making `qualifier_and_prefix` return
  `prefix='*'` is tempting because it produces the right span for free, but the
  prefix then goes through `_match_strength`, which matches `*` against nothing
  — so `SELECT *⌶` would lose `FROM` along with everything else.
- **Putting the star's span in `Request.replace_span`.** The same caret also
  offers `FROM`, and `FROM` inserts *at* the caret. One span for the position
  means accepting `FROM` deletes the star.
- **`snippet=` for the expansion text.** It is how `joins.py` carries a rendered
  clause, but `expand_snippet` strips `$1`-shaped runs and Postgres allows `$`
  inside an identifier — a column called `a$1` would be eaten. `literal=True`
  exists for exactly this and is checked one branch later in `_render`.
- **Qualifying only the colliding names.** Shortest correct output, but it
  produces `u.id, name, email, o.id, user_id, total` — a list whose rule is
  invisible to the person accepting it.

---

## 2. Scope

### In

- A `PARAM` token, spelled per dialect, with defaults for all four.
- Silence at a caret inside one, and operand-completion behaviour outside one.
- `Kind.EXPANSION`, and a candidate for it at a caret on a select-list star.
- A per-candidate span, which the expansion is the first thing to need.

### Out, deliberately

- **Bound parameter names.** §1 decision 2.
- **Type inference through a placeholder.** `WHERE created_at > :since⌶` cannot
  narrow anything, because nothing in the text says what `:since` holds. The
  comparand machinery is untouched.
- **Expansion anywhere but a select-list star.** `count(*)` is excluded by
  construction (§4.2). `INSERT INTO t (⌶)` is a column list, not a star, and is
  a different feature.

### Non-goals

- Validating that a placeholder is one the caller will actually bind. The engine
  does not execute queries and does not know the binding.
- Rewriting `*` in place as the schema changes. The expansion is a one-time
  edit; once accepted it is ordinary text.

---

## 3. Placeholders

### 3.1 The record

The four spellings differ structurally, not merely in text, so a tuple of
strings cannot express them. A new record in `dialects/base.py`:

```python
@dataclass(frozen=True, slots=True)
class Placeholder:
    """One way this dialect spells a bound parameter."""

    opens: str
    """The literal text that begins one: ':', '$', '?', '${'."""
    body: Literal['name', 'digits', 'none', 'any'] = 'name'
    """What may follow. 'any' runs to `closes` and requires it."""
    closes: str = ''
    """The delimiter that ends it, for the braced forms. Empty when the body ends itself."""
```

and one field on `Syntax`:

```python
placeholders: tuple[Placeholder, ...] = ()
```

The four spellings `gaps.md` names become
`Placeholder(':')`, `Placeholder('$', 'digits')`, `Placeholder('?', 'none')` and
`Placeholder('${', 'any', '}')`.

### 3.2 Lexing

`TokenType.PARAM` joins the enum. The scan goes in the main loop **after**
comments and string literals — a placeholder inside a literal is text — and
**before** the dollar-quoting branch. That ordering is what makes `$1` reliable:
`_scan_dollar_quote` reads `$1$2` as a tag it never finds again and swallows the
rest of the statement as an unterminated string. Trying placeholders first
costs dollar quoting nothing, because `$$body$$` and `$tag$body$tag$` both fail
the `digits` body at their second character.

Candidates are tried longest `opens` first, so `${` beats `$`.

`terminated` follows the rule the rest of the lexer uses — false when the token
ran to end of input looking for a delimiter — with one addition, because two of
these bodies have no delimiter to look for:

| body | terminated |
| --- | --- |
| `none` | always: `?` is complete the moment it is written |
| `any` | only when `closes` was found |
| `name`, `digits` | never: another character could always extend the name |

That table is what decides the behaviour in §3.4, and it is the reason `= ?⌶`
answers with connectives while `= :us⌶` answers with nothing.

`::` needs no special case. A `:name` placeholder requires an identifier start
after the colon, and `:` is not one, so `a::int` falls through to
`_match_operator`, which already prefers the dialect's `cast_operator`. The same
reasoning covers `arr[1:3]`, where the digit fails the `name` body.

### 3.3 Per-dialect defaults

Chosen so that no default breaks syntax the backend really has:

| dialect | placeholders | why |
| --- | --- | --- |
| ANSI | `?`, `:name` | the standard's dynamic parameter marker, plus the embedded-SQL host variable form |
| Postgres | `$1`, `:name` | the native server-side form, plus the spelling every tool over it uses |
| Trino | `?` | native prepared statements; Trino has no `?` operator |
| ClickHouse | `{name:Type}` | its own parameter syntax, as `Placeholder('{', 'any', '}')` |

Postgres deliberately does **not** get `?`: it is the JSONB existence operator,
and `data ? 'key'` is a real predicate that must keep lexing as one.

`${var}` ships as a named constant beside the record —
`TEMPLATE_PLACEHOLDER = Placeholder('${', 'any', '}')` — exported and wired into
nothing, per §1 decision 4. A reporting tool composes it in:

```python
syntax = replace(POSTGRES.syntax, placeholders=(*POSTGRES.syntax.placeholders, TEMPLATE_PLACEHOLDER))
DIALECT = replace(POSTGRES, syntax=syntax)
```

### 3.4 Analyse

Three changes, all small.

**A caret inside one suggests nothing.** `derive_request` gains a guard above
the existing literal check:

```python
if in_placeholder(tokens, caret):
    return Request(kinds=(), prefix='', replace_span=(caret, caret), clause=clause, scope=scope)
```

Above rather than folded into `in_literal`, because `_inside_a_literal` carries
the one case where a literal *does* answer — the values a compared column holds
— and a placeholder has no equivalent. Keeping them apart is what stops a future
edit to one silently changing the other.

**A placeholder ends an operand.** `after_operand` gains `TokenType.PARAM`
alongside `NUMBER` and `STRING`. This is the whole of the `= ? ⌶` fix:
`predicate_complete` is already armed by the `=`, so `expecting` becomes
`connective` and the clause's `followed_by` list answers.

**One small cleanup.** `in_literal` and `string_under` each spell the
inside-a-token rule inline, and `in_placeholder` would be a third copy. Factor
it to a module-private `_inside(token, caret)` and have all three call it. This
is not refactoring for its own sake: the rule is subtle — a caret at the closing
delimiter of a terminated token is outside it — and three copies is where it
starts to drift.

Nothing else moves. `comparand_at` reads identifiers and finds none in a
placeholder, so `WHERE :p = ⌶` proposes no values, which is right — the engine
cannot know what `:p` holds. `_output_of` returns no name for `SELECT :p`, which
matches what Postgres calls it: `?column?`.

---

## 4. Star expansion

### 4.1 The kind

`Kind.EXPANSION`. Not `COLUMN` — a front end colouring by kind should not claim
that a comma-separated list of six names is a column — and not `SNIPPET`, which
means a statement shape with blanks to fill.

### 4.2 Detection

A new predicate in `analyse.py`. Three conditions, all necessary:

1. `caret == token.end` for an `OPERATOR` token whose text is `*`. A star is one
   character, so this is the only caret that can be said to be *on* it;
   `SELECT ⌶*` is before it and `SELECT * ⌶` is past it.
2. `_star_is_an_item` accepts it, so `SELECT a * ⌶` and `WHERE 5 * ⌶` are
   multiplication and are left alone.
3. `_enclosing_call` returns None. `_star_is_an_item` accepts `count(*)` because
   a `(` precedes it, and offering to expand there would write
   `count(id, name, email)`.

Deliberately not conditioned on the clause being `SELECT`. Postgres `RETURNING *`
is the same construct and the same answer, and the clause model already carries
`RETURNING`; a rule naming SELECT would have to be extended for it and for every
dialect that grows another projection clause.

### 4.3 Two fields on the Request

Mirroring `comparand` and `comparand_type`, which have the same shape — analysis
says what it can see, and the stage below does the rest:

```python
star: tuple[int, int] | None = None
"""
The span of a `*` the caret sits on, qualifier included.

`u.*` is replaced whole rather than in part: each expanded column carries its
own `u.`, so leaving the written one in place would mean emitting the first
column bare and the rest qualified.
"""
star_of: tuple[Relation, ...] = ()
"""
The relations that star stands for. Empty when it stands for none.

`t.*` names one, a bare `*` names every relation of its own query level.
"""
```

`star_of` is computed the way `_output_of` already computes `Projection.stars`:
filter the scope's relations by label when the star is qualified, take them all
when it is not. `Projection.stars` cannot be reused directly — it holds every
star in the select list, and two of them (`SELECT a.*, b.*`) are
indistinguishable there.

An empty `star_of` — `SELECT *⌶` with no `FROM` yet — produces no candidate, so
that caret keeps answering `FROM` and nothing pretends to know more.

### 4.4 A per-candidate span

`Candidate` gains:

```python
span: tuple[int, int] | None = None
"""
What to replace, when that is not what the rest of the position replaces.

A star expansion overwrites the star; the `FROM` offered at the same caret is
inserted beside it. One span for the whole position cannot serve both, and
`Request.replace_span` is the position's.
"""
```

and `rank` reads `candidate.span if candidate.span is not None else
request.replace_span` when building the `Suggestion` — spelled against `None`
rather than truthiness, because a span is a tuple and the falsy-looking `(0, 0)`
is a real one. That is the entire downstream cost: `plan_insertion`,
`lsp/convert.py` and `demo/payload.py` all already read the span off the
suggestion, precisely as `convert.py`'s docstring says they must.

### 4.5 Building the text

In `resolve.py`, reached when `Kind.EXPANSION` is among the request's kinds. For
each relation in `star_of`, call the existing `_columns_of` — which needs no
catalog at all for a CTE or a derived table, since its projection is already
carried — then render:

- Each name through `quote_if_needed`, as `joins.py` does.
- Qualified with the relation's `label` when `len(star_of) > 1`, bare when it is
  one, per §1 decision 3. A qualified star is one relation by construction, so
  `u.*` expands to `u.id, u.name, u.email` through the same rule.
- Joined with `', '`.

One `Candidate`, with `literal=True` so `_render` inserts it verbatim (§1,
rejected approaches) and `span=request.star`. Its `text` is the joined list;
`label` is `expand *`, because a list a hundred characters wide is not a thing
to show in a completion popup; `detail` is `3 columns of users`, or `7 columns
of users, orders` when the star stands for more than one relation.

The label does not distinguish `*` from `u.*`. It could only do so by carrying
the star's spelling down as a third field, and `star_of` cannot stand in for it:
a qualified star and a bare star over a single-relation FROM both name exactly
one relation. The detail names that relation, which is the part a reader of the
list cannot already see.

A relation the catalog cannot answer for contributes nothing. If that empties
the whole list, no candidate is emitted — an expansion to zero columns is worse
than no expansion.

### 4.6 Insertion

Nothing to add. `plan_insertion` falls to its default path: the kind is neither
`FUNCTION` nor `SCHEMA`, there are no `stops` and no `relation`, so it splices
the text over the span and leaves the caret past it. `_separated` adds no
leading space because the span covers something rather than being empty.

---

## 5. Ranking

`request.kinds` at a star becomes `(Kind.EXPANSION, Kind.KEYWORD)`. It is
prepended in `derive_request`, beside the existing `_values_first` call and for
the same reason: both are positions where one kind leads because of something
the clause model cannot see. The composition becomes

```python
_expansion_first(star) + _values_first(...) + _kinds_for(...)
```

The expansion goes first, so `_kind_bonus` puts it above `FROM`. Putting the caret on the
star is the gesture that asks for it; `FROM` remains one row down, and stays
first at `SELECT * ⌶` with a space, which is the caret from which people
actually reach for it.

The prefix at that position is empty, so `_match_strength` returns
`_EXACT_PREFIX` for everything and the kind bonus decides the order. The
candidate carries `match_text='*'` so that the rule stays true if a prefix ever
can appear there.

No new bonus constant. `_LOCAL_BONUS` and `_JOIN_BONUS` exist because those
candidates compete *within* a kind against fetched ones; an expansion is alone
in its kind.

---

## 6. Testing

TDD throughout: each behaviour below is a failing test before it is code.

### 6.1 Placeholders, offline

- `tests/test_lex_core.py`: each body shape tokenizes, including unterminated
  `${re`; `$$body$$` and `$tag$b$tag$` still lex as strings under Postgres;
  `a::int`, `arr[1:3]` and `data ? 'key'` still lex as they do today.
- `tests/test_dialect_lexing.py`: each dialect's own spellings, and the negative
  — `?` is an operator under Postgres and a placeholder under Trino.
- A new `tests/test_placeholders.py` for the request-level propositions: the
  five rows of §1's table, plus `= :us ⌶` and `= ${region}⌶` answering with
  connectives, and `WHERE :p = ⌶` proposing no values.
- A composed-dialect test that `TEMPLATE_PLACEHOLDER` works when wired in and
  that `${re⌶` still offers columns when it is not — the default has to be
  demonstrably the default.

### 6.2 Star expansion, offline

A new `tests/test_star_expansion.py`:

- One relation expands bare; two expand qualified; `u.*` expands qualified to
  one relation's columns.
- The span covers `*` for a bare star and `u.*` for a qualified one, asserted
  through `apply_suggestion` on the resulting text rather than on the span
  alone.
- `count(*⌶)`, `SELECT a * ⌶` and `SELECT * ⌶` (with a space) offer no
  expansion.
- `SELECT *⌶` with no FROM offers `FROM` and no expansion.
- A CTE's star expands with a `_NullCatalog`, which is the offline claim the
  projection machinery exists to make.
- `FROM` is still offered at `SELECT *⌶`, one row below the expansion.

### 6.3 Existing suites

`tests/test_golden_requests.py` gains rows for both features.
`tests/test_purity.py` is unchanged and must stay passing — everything in §3 and
§4.2–4.4 lives under `engine/`.

### 6.4 Conformance

`DialectConformance.structure` gains one check: a placeholder whose `body` is
`'any'` and whose `closes` is empty can never terminate, which is a declaration
that does nothing — exactly the class of silent mistake that method exists to
catch.

`DialectConformance.cases` gains one case, built from the dialect's own first
placeholder so it spells itself per backend: a caret inside one offers nothing.
No star case — the corpus asserts texts that must appear, and the expansion's
text is the fixture's whole column list, which the existing cases already cover
from the other side.

### 6.5 Against the container

`tests/test_conformance.py` and the integration suite run against docker. The
one thing only a server can settle is that an expansion of a real relation is
SQL that server accepts, so the integration test expands a star against Postgres
and executes the result. This project's rule is that docker being available
leaves no excuse for unverified SQL.

---

## 7. The front ends

- `lsp/convert.py`: `Kind.EXPANSION` maps to `CompletionItemKind.Snippet`,
  beside `Kind.JOIN`, which is the closest thing LSP has to "this writes several
  things at once". The `.get(..., Text)` fallback means an unmapped kind
  degrades rather than crashing, but relying on that would show a star expansion
  as plain text.
- `demo/static/index.html` styles by `k-<kind>`, so `.k-expansion` joins the
  existing rules. `site/` is generated from `demo/` by `scripts/build_pages.py`
  and needs no separate edit.
- The demo's schema already has enough columns for an expansion to read as one;
  no fixture change.

---

## 8. Documentation

- `docs/gaps.md` loses sections 1 and 5 and renumbers. The "what the comparison
  did not find" paragraph stands.
- `README.md`'s status paragraph names star expansion among what works.
- `CHANGELOG.md` gains both, with the `?`-under-Postgres exclusion stated
  outright — somebody will otherwise report it as a bug.

---

## 9. Open questions carried forward

1. **Bound parameter names.** Deferred by §1 decision 2, not rejected. When it
   lands it wants a `Kind.PARAMETER` and an argument on both entry points, and
   the interesting question is whether the LSP server can discover the binding
   from anything but its own configuration.
2. **A placeholder's type.** `WHERE created_at > :since` could narrow the right
   side if the caller declared what `:since` holds. That is the same shape as
   the bound-names feature and should be judged with it.
3. **Star expansion in `INSERT INTO t (⌶)`.** The column list is the same list
   from a different direction. It is not a star, so it is not this feature, but
   whoever builds it should read §4.5.
4. **Expansion width.** §1 decision 5 caps nothing. If a real schema makes that
   unpleasant, the answer is probably a front-end fold rather than a truncation
   in the engine, since the engine cannot know what the editor can display.
