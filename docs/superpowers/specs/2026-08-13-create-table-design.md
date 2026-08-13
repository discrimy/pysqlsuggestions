# CREATE TABLE — design

`CREATE TABLE t (id ⌶` has nothing to say. This gives it types, and in doing so
unblocks the `TABLE` statement form, which has been waiting on it.

Gap 1 in `docs/gaps.md`, and the fifth project in the SELECT-grammar sequence.
The first built the conformance suite, the second and third closed the gaps it
recorded, the fourth ran the shared cases on every dialect. This closes the one
entry that suite could not: three cases whose citation is a *statement* form
rather than a clause, and which the engine refused for a reason that named this
work.

---

## 1. Context

### What is silent today

Every caret this design touches answers with nothing. Measured against the
shipped `POSTGRES` on 2026-08-13:

```
'CREATE '                     -> []
'CREATE TABLE '               -> []
'CREATE TABLE t (id '         -> []
'TABLE '                      -> []
'TABLE ONLY '                 -> []
```

That matters more than it looks. This project is purely additive: there is no
wrong answer to withdraw, so no caret can regress from right to wrong, and
every assertion it adds is a position going from silence to an answer.

### Why `TABLE` was blocked

`tests/grammar/cases.py` carries three cases citing
`TABLE [ ONLY ] table_name [ * ]`, all pending, all with the same reason:

> a statement form is found by the first word that starts one, and TABLE is a
> word inside CREATE TABLE — modelling it made `CREATE TABLE t (id ⌶` offer
> relations, so it waits on CREATE TABLE being modelled first

`postgres.py` records the same thing at greater length. The prediction was that
modelling the longer form first would settle it, and that is now measured rather
than assumed: `clause_at` ranks candidates by `(end offset, word count)`, so
`CREATE TABLE` beats a bare `TABLE` ending at the same token — exactly the rule
that already makes `DELETE FROM ⌶` answer as `DELETE FROM` and not as `FROM`.

Prototyped by composing a dialect with `dataclasses.replace`, touching nothing
in `src/`: with both clauses declared, `CREATE TABLE t (id ⌶` reports its clause
as `CREATE TABLE`, and all three pending cases pass.

### Decisions taken during brainstorming

1. **The gap as `docs/gaps.md` defines it** — the clause model for a
   parenthesised definition list, "and nothing else". Not the sibling `CREATE`
   forms. §7.
2. **The definition list is a position rule, not a clause continuation.** §3.
3. **`TABLE` reaches the baseline; `ONLY` does not.** §4.

### Rejected approaches

- **Modelling `CREATE TABLE` only far enough to unblock `TABLE`.** Three
  grammar cases would go green and no caret would gain an answer — leaving
  `CREATE TABLE t (id ⌶`, the exact caret `docs/gaps.md` names as the symptom,
  still silent. The entry would have to stay open under a different heading.
- **`CREATE VIEW`, `CREATE INDEX`, `CREATE SCHEMA` alongside.** Roughly triples
  the surface, and each brings a tail with its own decision — a view's body, an
  index's method and operator classes. `docs/gaps.md` warns that DDL completion
  shades into DDL authoring, and this is where that warning applies.
- **Reusing `opens_a_name_list` for the definition list.** It already silences
  four parens that name columns, and `FROM f(1) AS t (a int)` is genuinely one
  of them — so extending it to report a *type* half looks free. It is not: the
  other three shapes there are column *alias* lists, which rename existing
  columns and never take a type. Telling them apart needs to know whether the
  aliased item was a function call. An alias list and a definition list are
  different lists, and conflating them would offer a type where only a name can
  go — a wrong answer traded for a missing one.

---

## 2. `CREATE TABLE`

In `ansi.py`, and in `statement_start`. All three backends have the statement.

```python
Clause(
    name='CREATE TABLE',
    suggests=(),
    before_the_item=('IF NOT EXISTS',),
    defines_columns=_COLUMN_CONSTRAINTS,
)
```

`suggests=()` because the relation is being *invented*. `Kind.TABLE` would offer
every relation in the catalog at the one caret where naming an existing one is
the single thing that cannot work. `WINDOW` carries the same empty tuple for the
same reason, and its docstring already records that offering columns there "is
not a missing answer but one that writes a statement the server refuses".

**No `followed_by`, deliberately.** Giving it one was measured and it leaks:
with `followed_by=('AS',)` the word reaches *inside* the definition paren, so
`CREATE TABLE t (id ⌶` offers `AS` — a wrong answer at a caret that is correctly
silent today. `_clause_kinds` answers a completed operand with the clause's
continuations wherever the clause governs, and the paren does not change which
clause governs.

That leaves `CREATE TABLE t AS SELECT …` unreachable, which is a real cost and
the right trade at this size: `after_as` reads the caret past `AS` as an alias
being invented, so offering the word would lead somewhere that answers nothing.
An empty `followed_by` also makes `CREATE TABLE t ⌶` silent, which is honest —
what follows is `(`, and this engine does not suggest punctuation. Carried
forward in §7.

`IF NOT EXISTS` sits in `before_the_item`, which `request.py` gates behind a
non-empty prefix. The gate is what makes the field safe here: the caret after
`CREATE TABLE ` is where a name is being typed, and a keyword ranked above it
would be in the way. Behind a prefix it costs nothing.

---

## 3. The definition list

### The new field

`Clause` gains one:

```python
    defines_columns: tuple[str, ...] = ()
    """
    Words that may follow a column's type in this clause's definition list.

    A non-empty tuple is also what marks the clause as opening one, the way
    `opens_a_group` marks a clause as opening a body. Two fields — a flag and a
    list — would let a dialect declare a definition list with no constraint
    words and get silence at every caret past the type, which is a state worth
    making unspellable.

    Different from `opens_a_group`, which says what may *begin* the group: a
    definition list has no opening word, it has an alternation. The names are
    the author's to invent and this engine has nothing to invent them from, so
    only the second half of each item can be answered at all.
    """
```

### The position rule

`defines_a_column(tokens, caret, clause, clauses)` in `engine/analyse.py`,
returning `'name' | 'type' | 'constraint' | None`. It counts the plain words of
the caret's own item — since the last comma, at the list's own depth:

| words so far | position | answered with |
| --- | --- | --- |
| none | a column name being invented | nothing |
| one | its type | `Kind.TYPE` |
| two or more | a constraint | `defines_columns` |
| — | a deeper paren | nothing |

Measured across the shapes that occur, and the depth test does more work than it
looks. Every construct that nests — `numeric(10, 2)`, `CHECK (x > 0)`,
`REFERENCES users (id)`, `PRIMARY KEY (a, b)` — lands one level below the list
and is excluded without naming any of them. That is the whole reason the rule
counts words at a depth rather than parsing an item.

`Kind.TYPE` needs nothing downstream: `resolve.py` already answers it from
`dialect.types`, which is how `CAST(x AS ⌶)` works. `docs/gaps.md` predicted
exactly this — "the candidates already exist".

### The multi-word type

A type may be two words — `double precision`, `character varying` — and the
count cannot see that. `CREATE TABLE t (id double ⌶` has two words in its item
and is answered as a constraint position, so `precision` is not offered.

Accepted rather than solved. The one-word caret before it offers
`double precision` **whole**, so the author who takes the completion never
reaches the bad caret; only one who typed `double` by hand does. The
alternative — offering the type list at every caret past the name — puts a
second type after a complete one, which writes `id integer text`. A missing
answer for a hand-typist beats a wrong answer for everyone.

### What is not offered

Table constraints — `PRIMARY KEY (a, b)`, `CONSTRAINT c CHECK (…)`,
`FOREIGN KEY` — begin an item, so they land in the `'name'` position and stay
silent. Deliberate: that caret is overwhelmingly a column name being invented,
and a handful of rare keywords ranked above silence there is the cost
`before_the_item`'s prefix gate exists to refuse. Carried forward in §7.

---

## 4. `TABLE`, and what the containers said

Verified against the three backends on 2026-08-13, not argued from the
standard:

| | `TABLE t` | `ONLY` | NOT NULL | NULL | DEFAULT | PRIMARY KEY | UNIQUE | REFERENCES | CHECK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Postgres | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| ClickHouse | **no** | — | yes | yes | yes | yes | no | no | no |
| Trino | yes | **no** | yes | no | no | no | no | no | no |

ClickHouse answers `TABLE report_executions` with `Syntax error: failed at
position 1 ('TABLE')`, so the clause reaches the baseline and ClickHouse drops
it with `.without('TABLE')` — the `CALL` precedent exactly, and for the same
reason: inheriting it would offer a word whose statement the server rejects
outright.

Trino refuses `ONLY`, listing what it expected instead. So `ONLY` is not on
ANSI's `TABLE`; `postgres.py` adds it through the `_ansi()` helper, the way
`DISTINCT` and the grouping words are already added to clauses ANSI declares.

Trino's parser also enumerated the continuation set, which is where `TABLE`'s
`followed_by` comes from rather than from a guess:

> Expecting: '.', 'EXCEPT', 'FETCH', 'INTERSECT', 'LIMIT', 'OFFSET', 'ORDER',
> 'UNION', &lt;EOF&gt;

```python
Clause(
    name='TABLE',
    suggests=RELATION_REFERENCE,
    followed_by=_onwards('UNION'),
)
```

**`PRIMARY KEY` on ClickHouse is a corrected reading.** It first measured as
refused, and the refusal was the probe's fault — the statement also carried
`ORDER BY tuple()`, which conflicts with a declared primary key. Re-run alone it
is accepted, and `SHOW CREATE TABLE` reports `PRIMARY KEY tuple(x)`. Recorded
because the wrong reading would have put a word every backend takes into
Postgres's list alone.

So `ansi.py` gets the four words at least two backends accept, `postgres.py`
extends, and `trino.py` narrows:

| dialect | `defines_columns` |
| --- | --- |
| ansi | `('NOT NULL', 'NULL', 'DEFAULT', 'PRIMARY KEY')` |
| postgres | ANSI's four, then `'UNIQUE'`, `'REFERENCES'`, `'CHECK'` |
| trino | `('NOT NULL',)` |
| clickhouse | ANSI's four, unchanged |

ClickHouse inherits ANSI's four unchanged — all four are verified there. Its own
`MATERIALIZED`, `CODEC`, `COMMENT` and the mandatory `ENGINE =` are left out;
that tail is a project of its own, alongside the per-dialect synopsis already
carried forward from the previous design.

### Two supporting changes the measurement forced

**`_QUERY` widens to `{'SELECT', 'TABLE'}`** in `ansi.py`. The clauses that
shape a result set declare `statements=_QUERY`, so with the form named `TABLE`
its entire tail was filtered out and `TABLE users ⌶` answered nothing. `TABLE t`
is `SELECT * FROM t` — it has a result set to shape, and Postgres accepts
`TABLE t ORDER BY id` and `TABLE t LIMIT 1`. Widening only *permits*: what is
offered still comes from `TABLE`'s own `followed_by` and from clauses declaring
`follows`, neither of which names `GROUP BY`.

**`_RELATION_CLAUSES` gains `TABLE`** in `engine/analyse.py`. Without it the
form brings no relation into scope, so `TABLE users ORDER BY ⌶` offers the
columns of *every* relation the catalog holds — a wrong answer created by this
change itself. With it the caret offers `users.id` and `users.email` and no
more.

---

## 5. Testing

- **The three grammar cases stop being pending.** `TABLE ⌶` should hold on
  Trino as well as Postgres; the other two are Postgres's, since `ONLY` is. The
  suite's own rule already forbids a pending case from naming more than one
  dialect, so the marking is added in the same change that makes them pass.
- **`tests/test_create_table.py`**, new: the definition-list positions, the
  nested parens that must stay silent, the multi-word type trade recorded as a
  test rather than only as prose, and `CREATE ⌶` answering `TABLE` without
  `DROP ⌶` or `ALTER ⌶` losing theirs.
- **`tests/test_statement_forms.py`** gains the `TABLE` form: the relation, the
  tail, the scope that `_RELATION_CLAUSES` buys, and `ONLY` on Postgres only.
- **`tests/test_dialect_clauses.py`** for `defines_columns` as a record field,
  and for ClickHouse not having the `TABLE` clause.
- **`DialectConformance`** gains a case: a clause that defines columns answers
  its type position with a type. It is shipped in the wheel for third-party
  dialects, so a new field that changes what a caret admits belongs in it.
- **No integration tests.** This asserts what the engine offers, which is
  decided before a catalog is consulted. The containers settled which words
  belong in which dialect and that question is closed.

The burn-down moves from `57/69 SELECT positions answered, 9 of the 12 gaps
refused` to **`60/69 … 6 of the 9 gaps refused`** — three cases answered, and
the three `refused` notes that named this work deleted with them.

---

## 6. Documentation

- `CHANGELOG.md`: under what changes at a caret, since a great deal does.
- `docs/gaps.md`: entry 1 moves to **Closed since this list was written**,
  keeping the entry rather than deleting it — the section exists because "a list
  whose entries only ever disappear tells a later reader nothing about what was
  decided". The note records what the closed entry got wrong: it called this
  "the clause model and nothing else", and it needed a position rule in
  `engine/` too, because a definition list has no opening word for
  `opens_a_group` to carry.
- `postgres.py`'s long comment refusing `TABLE` is replaced by the clause, with
  the reasoning kept: the comment predicted the fix and should say that it
  landed.

---

## 7. Open questions carried forward

- **`CREATE TABLE … AS SELECT`.** Silent, because `after_as` reads the caret
  past `AS` as an alias being invented. Needs a way for a clause to say that its
  `AS` introduces a statement rather than a name — which is what `WITH` says with
  `opens_a_group`, except there is no paren here to hang it on.
- **Table constraints at an item start.** `PRIMARY KEY (a, b)` and
  `CONSTRAINT c …` begin an item and are not offered. A prefix-gated
  `before_the_item` per *item* rather than per clause would fit, and does not
  exist.
- **ClickHouse's `CREATE TABLE` tail.** `ENGINE =` is mandatory there, so the
  form cannot yet produce a runnable statement on that backend. Fewer
  suggestions rather than an error, which is the rule — but it is the largest
  single absence this design leaves.
- **The sibling `CREATE` forms**, per §1.
- **`EXPLAIN TABLE t`** is valid Postgres and is not offered. `EXPLAINABLE` is
  ANSI's and ClickHouse has no `TABLE` form, so naming it there would need
  `EXPLAIN` restated per dialect — a second mechanism for a phrase almost nobody
  types.
