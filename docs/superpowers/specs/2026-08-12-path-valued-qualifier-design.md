# A qualifier that is a path — design

The last wrong answer this engine is known to give.

Two relations with the same name in different schemas can both be in scope, and
every column reference the engine writes for them is `invoices.amount` — which
the server refuses as ambiguous. The same root cause silently drops one of two
matching columns before any `FROM` exists, and makes star expansion emit the
same column twice.

---

## 1. Context

### What the servers say

Every claim was run against `docker/docker-compose.yml`.

- **Two same-named relations from different schemas can both be in scope.**
  `EXPLAIN SELECT 1 FROM public.invoices, billing.invoices` plans; Postgres
  aliases the second internally as `invoices_1`. This was assumed impossible
  when the defect was first reported and it is not.
- **A bare reference is then refused.**
  `SELECT invoices.amount FROM public.invoices, billing.invoices` →
  `ERROR: table reference "invoices" is ambiguous`.
- **A path-valued reference resolves it.** Both
  `SELECT public.invoices.amount FROM …` and `SELECT billing.invoices.amount FROM …`
  plan, each picking the relation named.
- **A bare reference against a qualified `FROM` is fine when there is no
  collision.** `SELECT invoices.amount FROM billing.invoices` plans — which is
  why nothing may change unless a collision actually exists.
- **Trino takes a reference at either depth.** Both
  `postgresql.public.reports_report.name` and `public.reports_report.name`
  resolve against `FROM postgresql.public.reports_report`.

### What the engine does today

```
FROM public.invoices, billing.invoices
  SELECT amou⌶  → 'invoices.amount'            → server refuses: ambiguous
  SELECT *⌶     → 'invoices.amount, invoices.id, invoices.amount, invoices.period'
                                               → ambiguous, and `amount` twice
  SELECT amou⌶  (no FROM) → one entry; the other schema unreachable, and its
                            detail reads `invoices.amount :: numeric`, so even
                            the survivor does not say which schema it came from
```

### Why it happens

`Candidate.qualifier` is `str | None` — one segment. `resolve` fills it with
`relation.label`, which is `invoices` for both relations, and `rank._render`
writes `f'{qualifier}.{text}'`. Two candidates then carry identical text, and
rank's dedupe keys on `(kind, text)`, so one is dropped.

### Blast radius

Small, and worth stating because it is what makes this a single slice.
`Candidate.qualifier` is **set in three places**, all in `resolve.py`
(`_from_projection`, `_column_candidate`, `_table_candidate`), and **read in
one**, `rank._render`. `joins.py` builds its conditions from aliases it
generates itself, which are unique by construction — line 49 excludes labels
already taken — so join proposals are unaffected. `api.py` mentions the word
only in a docstring. No test constructs a `Candidate.qualifier`; the
`qualifier=` occurrences in `tests/corpus/cases.py` are `Request.qualifier`,
which is already a tuple.

### Decisions taken during brainstorming

1. **Full declared path, on collision only.** Not the shortest disambiguating
   prefix: what counts as short enough depends on the search path, which the
   engine models only partially, so that rule could be right against the fixture
   and wrong against a server.
2. **One entry per schema at the offer stage**, rather than keeping the dedupe
   and enriching the detail. Chosen for completeness over list length, against
   the recommendation in §5 — recorded because the trade-off is real.

### Rejected approaches

- **Proposing an alias on collision.** Probably what a person would write, and a
  different feature: it rewrites the `FROM` clause, while the caret that suffers
  the ambiguity is usually nowhere near it.
- **A dotted string in the existing `str` field.** `quote_if_needed` would treat
  `public.invoices` as one name and emit `"public.invoices"`. This is the reason
  the field's type has to change rather than its contents.
- **Changing rank's dedupe key.** Unnecessary once the texts differ, and a
  weaker fix: it would leave two entries rendering identically in the list.

---

## 2. Scope

### In

- `Candidate.qualifier: tuple[str, ...]`, and segment-wise quoting in `rank`.
- The collision rule in scope, at the offer stage, and in star expansion.
- Search-path ranking for the offer stage's new entries.

### Out, deliberately

- **Alias proposals.** See above.
- **`joins.py`.** Its aliases are unique by construction.
- **`Suggestion`.** The qualifier is baked into `text` before it reaches one, so
  the public output type does not change at all.
- **Ambiguity between a column of a relation and a column of another with the
  same name.** `SELECT amount FROM a, b` where both have `amount` is a *column*
  ambiguity, not a relation one. The engine already answers it by qualifying
  every column with its relation, and that is unchanged here.

---

## 3. `Candidate.qualifier` becomes a path

```python
    qualifier: tuple[str, ...] = ()
```

replacing `str | None = None`. Empty means unqualified, where `None` did before.

`rank._render` quotes each segment rather than the whole:

```python
    if candidate.qualifier:
        prefix = '.'.join(quote_if_needed(part, dialect) for part in candidate.qualifier)
        return f'{prefix}.{text}', ()
```

That is the whole mechanical change. Everything else is deciding what to put in
it.

---

## 4. Where a collision is decided

Two places, because there are two kinds of collision. Neither needs anything
from `engine/`, so `tests/test_purity.py` stays satisfied without thought.

### In scope

Labels appearing more than once among the statement's **catalog** relations —
those with `projection is None`. A relation whose label collides qualifies with
its full declared path; every other relation keeps its label.

This can only ever fire on two unaliased same-named relations. An aliased one
answers to its alias (`FROM public.invoices a, billing.invoices b` gives `a` and
`b`), and a CTE or derived table has a name unique within the statement. That
narrowness is the point: it is exactly the state the server calls ambiguous.

### At the offer stage

`(table, column)` pairs appearing under more than one schema in the
`loose_columns` result. Deliberately keyed on the pair rather than on the table
name alone: `public.invoices.amount` and `billing.invoices.period` share a table
name and can never render identically, so neither is lengthened.

Each such candidate carries its own `relation`, which is what already writes the
`FROM` clause — so the two entries write `FROM public.invoices` and
`FROM billing.invoices` respectively.

---

## 5. Star expansion, and the duplicate it emits

`_expansion` builds its qualifier from `relation.label` directly rather than
through `Candidate.qualifier`, so the rule has to be applied there too. It is
the same rule and the same helper.

It fixes a second defect for free. Today
`SELECT *` over those two relations expands to

```
invoices.amount, invoices.id, invoices.amount, invoices.period
```

— `amount` twice, because each relation contributes its own and the two render
identically. Under the path rule they become `public.invoices.amount` and
`billing.invoices.amount`, which are different columns and read as such.

---

## 6. The offer stage multiplies

Chosen deliberately, and the risk is recorded here rather than discovered later.

`SELECT amou⌶` will offer one entry per schema holding a matching column. In a
schema-per-tenant database — where every table exists in every schema — that is
one entry per tenant where today there is one entry in total. It is bounded by
`search_columns`' server-side limit rather than unbounded, but that bound is
500, not 5.

The mitigation is ranking, not truncation: a column whose schema is in the
default namespace sorts first. That costs one `reader.tables(None)` at a
position that makes no such call today. It is cached like every other catalog
read, so the cost is one query per session rather than one per keystroke.

No cap is added. This codebase logs a truncation rather than hiding one, and a
cap here would be a number somebody picked; if the demo shows the list is
unusable, capping is a later change made with evidence.

---

## 7. Testing

### Unit

One file, `tests/test_ambiguous_relations.py`, because all of it is one
proposition seen from three positions:

- A bare reference where two same-named relations are in scope carries the full
  path; where only one relation is in scope it does not.
- An aliased pair does **not** trigger the rule — this is the test that keeps
  the rule narrow, and it would fail if the collision were keyed on the relation
  name rather than the label.
- Star expansion qualifies both sides and no longer repeats `amount`.
- The offer stage returns one entry per schema, each with its own `relation`,
  and the default-namespace one first.
- A single-schema fixture produces byte-identical output to today. This is the
  regression test the whole design is shaped around.

### Conformance

One case: **a column reference is never ambiguous.** The shared fixture grows a
second schema holding a relation of the same name as one already there, and the
case asserts that completing a column with both in scope does not produce a bare
reference. Every dialect should satisfy it, including one nobody here wrote.

### Integration

Complete, apply, and have the server **plan** the result — the shape
`test_postgres_reaches_a_relation_off_the_search_path` already uses. It is the
only thing that can show `public.invoices.amount` runs where `invoices.amount`
is refused, and the refusal is the whole reason for the slice. The seed grows a
`public.invoices` alongside the existing `billing.invoices`.

### Not in the acceptance sweep

Its `CORPUS` statements are checked at carets derived from spaces, which reaches
this fine — but the sweep only asks whether a *suggestion* is misplaced, and the
defect here is a suggestion that is well-placed and ambiguous. The server-plan
integration test is what judges it.

---

## 8. Documentation

- `CHANGELOG.md`: the behaviour change, and the note that `Candidate.qualifier`
  changed type — visible to anyone who built a `Candidate` by hand, which the
  ports make possible.
- `CHANGELOG.md`, again, further up. The relations-outside-the-search-path entry
  carries a **"One known limitation"** paragraph that says these two "still
  collapse to a single suggestion" and that telling them apart "needs a
  qualifier that can hold a path rather than a name, which is not in this
  change". That paragraph is now describing something fixed, so it is rewritten
  to say what it was and where it went rather than deleted — the same treatment
  every closed entry in `docs/gaps.md` gets.

  It also **understated the defect**, and the correction belongs with it: the
  collapse was the visible half, and the invisible half was that the surviving
  suggestion is itself refused by the server once both relations are in scope.

- `docs/gaps.md`: **no change.** The limitation was never recorded there —
  checked, not assumed, while writing this spec, which had claimed otherwise.

---

## 9. Open questions carried forward

- **Whether the offer stage needs a cap.** §6. Deliberately deferred until the
  demo says so.
- **Alias proposals on collision**, which would make the reference short again.
