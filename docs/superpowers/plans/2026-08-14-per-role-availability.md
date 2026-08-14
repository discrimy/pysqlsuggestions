# Per-role availability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suggestions the connected role may not read arrive sunk and annotated rather than offered as if they would work, and no value literal is ever drawn from a column the role cannot select.

**Architecture:** A three-state `Availability` rides on the `Column` and `Table` rows the four `Catalog` methods already return, defaulting to `UNKNOWN` so no adapter claims knowledge it lacks. `resolve.py` lifts it onto `Candidate`; `rank.py` sorts restricted items below everything available; three knock-on rules (star expansion, value hints, join proposals) consume it. Only Postgres answers, through `has_column_privilege` / `has_any_column_privilege` columns added to four existing queries.

**Tech Stack:** Python 3.10+, stdlib only in `src/`. pytest, ruff (`D` enabled), mypy strict. psycopg2 and pygls in `lsp/` and the integration tests only.

**Spec:** `docs/superpowers/specs/2026-08-14-per-role-availability-design.md`

## Global Constraints

- **Zero runtime dependencies.** `import pysqlsuggestions` must pull in no driver. `tests/test_purity.py` enforces this.
- **`engine/` may not import `ports` or `resolve`.** `engine/rank.py` and `engine/joins.py` may import `pysqlsuggestions.types`, which is where `Availability` lives.
- **Python 3.10.** No `X | Y` in `isinstance`, no star-expression directly inside a subscript (`resolve.py:172` documents this trap).
- **Every function needs a docstring and full annotations.** Ruff `D` and mypy `strict` cover `src`, `tests` and `lsp`.
- **Single quotes, 120 columns.**
- **Every existing test must pass unchanged.** This is the acceptance criterion for the degradation being honest, not merely a regression check. If an existing assertion moves, stop and report rather than editing the assertion.
- **The gate is `./scripts/check.sh`** — `ruff format --check`, `ruff check`, `mypy strict`, `pytest`.
- **Commits** are `feat:`/`fix:`/`test:`/`docs:` with a lowercase prose summary and a body explaining the decision. No `Co-Authored-By` trailers.

---

### Task 1: `Availability` and the record fields

**Files:**
- Modify: `src/pysqlsuggestions/types.py` (enum after `Kind`; fields on `Column` 68-77, `Table` 80-97, `Candidate` 361-428, `Suggestion` 487-523)
- Modify: `src/pysqlsuggestions/__init__.py:32-57` (`__all__`)
- Test: `tests/test_types.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Availability` with members `AVAILABLE`, `RESTRICTED`, `UNKNOWN`; `Column.availability`, `Table.availability` (default `Availability.UNKNOWN`); `Candidate.availability`, `Suggestion.availability` (default `Availability.AVAILABLE`); `Candidate.reason`, `Suggestion.reason` (default `None`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_types.py`:

```python
def test_catalog_records_default_to_unknown_availability() -> None:
    """A row nobody asked about claims nothing: AVAILABLE would be an assertion with no evidence."""
    column = Column(schema='public', table='users', name='id', type='bigint')
    table = Table(schema='public', name='users')
    assert column.availability is Availability.UNKNOWN
    assert table.availability is Availability.UNKNOWN


def test_engine_records_default_to_available() -> None:
    """A keyword or a generated alias has no privilege question — it is insertable by construction."""
    candidate = Candidate(text='SELECT', kind=Kind.KEYWORD)
    assert candidate.availability is Availability.AVAILABLE
    assert candidate.reason is None


def test_availability_values_are_strings_like_kind() -> None:
    """Same reason as Kind: consumers serialise these straight into an editor payload."""
    assert Availability.RESTRICTED.value == 'restricted'
```

Add `Availability` to that file's imports from `pysqlsuggestions.types`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_types.py -k availability -q`
Expected: FAIL with `ImportError: cannot import name 'Availability'`

- [ ] **Step 3: Write the implementation**

In `src/pysqlsuggestions/types.py`, immediately after the `Kind` enum:

```python
class Availability(Enum):
    """
    Whether the connected role may actually read a thing, as far as anyone can tell.

    Values are explicit strings for the reason `Kind`'s are: they are serialised
    straight into an editor payload.

    Three states rather than two, and the third is the load-bearing one. The
    catalog records default to `UNKNOWN` because `AVAILABLE` would be a *claim*:
    a snapshot fixture, a ClickHouse column and every third-party adapter's rows
    would all assert that the connected role may read them, on no evidence at
    all. The engine records default to `AVAILABLE` because a keyword, an
    operator or a generated alias has no privilege question — it is insertable
    by construction, and defaulting those to `UNKNOWN` would make the ordinary
    case the uncertain one.

    Every decision downstream tests `is RESTRICTED`, so `UNKNOWN` and
    `AVAILABLE` behave identically today. That is intended. `UNKNOWN` earns its
    place as the honest default rather than as a behaviour, and it is the state
    a policy source for Trino would later replace.
    """

    AVAILABLE = 'available'
    RESTRICTED = 'restricted'
    UNKNOWN = 'unknown'
```

On `Column`, after `position`:

```python
    availability: Availability = Availability.UNKNOWN
    """Whether the connected role may select this column. See `Availability`."""
```

On `Table`, after `rows`:

```python
    availability: Availability = Availability.UNKNOWN
    """
    Whether the connected role may read anything in this relation at all.

    Its own field despite being implied by the columns, and the reason is cost
    rather than logic: `FROM <caret>` lists every relation in the namespace, and
    finding out by fetching each one's columns is the read a completion engine
    must not make.
    """
```

On `Candidate`, after `span`, and on `Suggestion`, after `note` — the same pair in both:

```python
    availability: Availability = Availability.AVAILABLE
    """Whether accepting this produces a statement the role may run. See `Availability`."""
    reason: str | None = None
    """
    Why it would not: `no SELECT privilege`.

    Separate from `note` rather than reusing it, because the two point in
    opposite directions and a candidate can carry both. A join proposal to a
    restricted column has `note='fk: users.id'`, which says why it ranks high,
    and `reason`, which says why it will fail; one field would silently
    overwrite the other.
    """
```

Add `'Availability',` to `__all__` in `src/pysqlsuggestions/__init__.py`, alphabetically before `'Cache'`, and to that module's import from `pysqlsuggestions.types`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_types.py -q && uv run pytest -m 'not integration' -q`
Expected: PASS, and the full count unchanged from before this task except for the three new tests.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/types.py src/pysqlsuggestions/__init__.py tests/test_types.py
git commit -m "feat: availability as a value type

Three states because the catalog records need a default that claims
nothing. AVAILABLE on a MemoryCatalog row would assert the connected
role may read it, which no snapshot knows. The engine records default
the other way, since a keyword has no privilege question.

reason is separate from note: a restricted join proposal carries both,
and one field would overwrite the other."
```

---

### Task 2: Ranking sinks restricted suggestions

**Files:**
- Modify: `src/pysqlsuggestions/engine/rank.py:74-135`
- Create: `tests/test_rank.py` (does not exist today — ranking is currently covered end-to-end through `complete()`)

**Interfaces:**
- Consumes: `Candidate.availability`, `Candidate.reason` from Task 1.
- Produces: `rank()` emits `Suggestion.availability` and `Suggestion.reason`, and orders every `RESTRICTED` suggestion after every non-restricted one.

- [ ] **Step 1: Write the failing test**

```python
"""Ranking's availability rule: restricted loses to everything, whatever it matched."""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.types import Availability, Candidate, Kind, Request


def _request(prefix: str = '') -> Request:
    """The three fields `Request` requires; everything else on it defaults."""
    return Request(kinds=(Kind.COLUMN,), prefix=prefix, replace_span=(0, 0))


def test_restricted_sinks_below_a_worse_match() -> None:
    """An exact prefix hit that cannot be read still loses to a substring hit that can."""
    candidates = [
        Candidate(text='password', kind=Kind.COLUMN, availability=Availability.RESTRICTED, reason='no SELECT privilege'),
        Candidate(text='user_passphrase', kind=Kind.COLUMN),
    ]
    found = [s.text for s in rank(candidates, _request('pass'), POSTGRES)]
    assert found == ['user_passphrase', 'password']


def test_the_reason_and_state_reach_the_suggestion() -> None:
    """A front end cannot render what rank drops."""
    candidate = Candidate(
        text='password',
        kind=Kind.COLUMN,
        availability=Availability.RESTRICTED,
        reason='no SELECT privilege',
    )
    suggestion = rank([candidate], _request('pass'), POSTGRES)[0]
    assert suggestion.availability is Availability.RESTRICTED
    assert suggestion.reason == 'no SELECT privilege'


def test_the_readable_duplicate_wins_the_dedup() -> None:
    """Two relations in scope, one column name, one grant: rank keys on (kind, text) and keeps the first."""
    candidates = [
        Candidate(text='id', kind=Kind.COLUMN, availability=Availability.RESTRICTED, reason='no SELECT privilege'),
        Candidate(text='id', kind=Kind.COLUMN),
    ]
    found = rank(candidates, _request('id'), POSTGRES)
    assert len(found) == 1
    assert found[0].availability is Availability.AVAILABLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rank.py -q`
Expected: FAIL — `password` first, and `AttributeError`/`TypeError` on `Suggestion.availability`.

- [ ] **Step 3: Write the implementation**

In `rank.py`, widen the scored tuple's type at line 74:

```python
    scored: list[tuple[int, float, int, str, Suggestion]] = []
```

Add the leading term when appending (line 95), and carry the two fields onto the `Suggestion`:

```python
        restricted = 1 if candidate.availability is Availability.RESTRICTED else 0
        scored.append(
            (
                restricted,
                -score,
                len(candidate.text),
                text.lower(),
                Suggestion(
                    ...,
                    note=candidate.note,
                    availability=candidate.availability,
                    reason=candidate.reason,
                ),
            ),
        )
```

Replace the sort at line 120, keeping the existing comment above it and adding to it:

```python
    # Among equal-strength matches, the shorter name is the closer one: the same
    # prefix covers more of it. `no` should reach `now` before `normalize`, and
    # alphabetical order alone would not.
    #
    # Availability leads, so a restricted item is last whatever it matched. Not
    # "bottom of its kind group", which §7 of the design asked for and no
    # constant expresses: _KIND_STEP is 5.0 against match strengths spanning 25
    # to 100, so any penalty small enough to stay inside a kind leaves a
    # restricted exact-prefix match above an available substring one — which is
    # not sunk. It also settles the dedup below in the readable candidate's
    # favour, which was previously a coin flip between two relations' `id`.
    scored.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
```

Import `Availability` in that module's `from pysqlsuggestions.types import ...` line.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_rank.py -q && uv run pytest -m 'not integration' -q`
Expected: PASS, with every pre-existing test still passing — nothing sets `RESTRICTED` yet, so the new leading term is 0 for every candidate in the suite.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/engine/rank.py tests/test_rank.py
git commit -m "feat: restricted suggestions sink below everything available

A leading sort term rather than a score penalty. The design asked for
the bottom of the kind group and no constant expresses it: _KIND_STEP
is 5.0 while match strengths span 25 to 100, so a restricted exact
match would outrank an available substring match in the same kind.

Falls out of it: the (kind, text) dedup now prefers the readable of two
relations' identically named columns instead of whichever scored higher."
```

---

### Task 3: `MemoryCatalog` declares restricted columns and relations

**Files:**
- Modify: `src/pysqlsuggestions/catalogs/memory.py:35-91` (constructor), `49-71` (row construction)
- Test: `tests/test_memory_catalog.py`

**Interfaces:**
- Consumes: `Availability` from Task 1.
- Produces: `MemoryCatalog(snapshot, restricted={('public', 'users'): ['password']})`. A relation mapped to `None` — `{('public', 'secrets'): None}` — is wholly unreadable: its `Table.availability` is `RESTRICTED` and every one of its columns is too.

- [ ] **Step 1: Write the failing test**

```python
def test_restricted_columns_come_back_restricted() -> None:
    """The fixture says what a privilege query would; everything unnamed stays AVAILABLE."""
    catalog = MemoryCatalog(
        {('public', 'users'): [('id', 'bigint'), ('password', 'text')]},
        restricted={('public', 'users'): ['password']},
    )
    found = {c.name: c.availability for c in catalog.columns('public', 'users')}
    assert found == {'id': Availability.AVAILABLE, 'password': Availability.RESTRICTED}


def test_a_wholly_unreadable_relation_restricts_itself_and_its_columns() -> None:
    """None means no grant at all, which is what has_any_column_privilege reports as false."""
    catalog = MemoryCatalog(
        {('public', 'users'): [('id', 'bigint')], ('public', 'secrets'): [('id', 'bigint'), ('body', 'text')]},
        restricted={('public', 'secrets'): None},
    )
    tables = {t.name: t.availability for t in catalog.tables()}
    assert tables == {'users': Availability.UNKNOWN, 'secrets': Availability.RESTRICTED}
    assert all(c.availability is Availability.RESTRICTED for c in catalog.columns('public', 'secrets'))


def test_a_snapshot_that_says_nothing_still_says_unknown() -> None:
    """No `restricted` argument must leave every existing fixture exactly as it was."""
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint')]})
    assert catalog.columns('public', 'users')[0].availability is Availability.UNKNOWN
    assert catalog.tables()[0].availability is Availability.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_memory_catalog.py -k restricted -q`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'restricted'`

- [ ] **Step 3: Write the implementation**

Add the parameter to the constructor signature, after `foreign_keys`:

```python
        restricted: Mapping[tuple[str, str], Sequence[str] | None] | None = None,
```

Before the column loop, resolve it into a lookup:

```python
        # A relation mapped to None has no grant at all, which is what
        # has_any_column_privilege reports as false: the relation is restricted
        # and so is every column in it. A list names the columns individually,
        # which is the case a column-level GRANT produces.
        withheld = restricted or {}
        unreadable = {key for key, columns in withheld.items() if columns is None}
```

In the column loop, replace the bare `Column(...)` construction's tail:

```python
                    position=spec[2] if len(spec) > 2 else index,  # noqa: PLR2004
                    availability=_declared_state((schema, table), spec[0], withheld, unreadable),
```

In the `Table` construction:

```python
                availability=(
                    Availability.RESTRICTED if (schema, table) in unreadable else Availability.UNKNOWN
                ),
```

And a module-level helper:

```python
def _declared_state(
    relation: tuple[str, str],
    column: str,
    withheld: Mapping[tuple[str, str], Sequence[str] | None],
    unreadable: set[tuple[str, str]],
) -> Availability:
    """
    What a privilege query would say about one column of this snapshot.

    UNKNOWN unless the fixture mentioned the relation at all. A snapshot that
    says nothing about privileges knows nothing about them, and claiming
    AVAILABLE for every column of every existing fixture would be the one
    assertion this type exists to avoid.
    """
    if relation in unreadable:
        return Availability.RESTRICTED
    named = withheld.get(relation)
    if named is None:
        return Availability.UNKNOWN
    return Availability.RESTRICTED if column in named else Availability.AVAILABLE
```

Note the asymmetry the docstring names: naming a relation in `restricted` promotes its *other* columns to `AVAILABLE`, because the fixture has now spoken about that relation. A relation absent from the mapping stays `UNKNOWN` throughout.

Import `Availability` in that module.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_memory_catalog.py -q && uv run pytest -m 'not integration' -q`
Expected: PASS, existing count unchanged plus three.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/catalogs/memory.py tests/test_memory_catalog.py
git commit -m "feat: a snapshot can declare what the role may not read

restricted={(schema, table): [columns]} for a column-level grant, and
None for a relation with no grant at all — the two shapes Postgres
distinguishes with has_column_privilege and has_any_column_privilege.

A relation the mapping never names stays UNKNOWN rather than becoming
AVAILABLE, so every existing fixture keeps claiming nothing."
```

---

### Task 4: `resolve` lifts availability onto candidates

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py:953-968` (`_column_candidate`), `971-988` (`_table_candidate`)
- Test: `tests/test_values.py` is the wrong home — create `tests/test_availability.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: every column and relation candidate carries the row's `availability`, with `reason='no SELECT privilege'` when restricted. Later tasks assume `complete()` already sinks a restricted column.

- [ ] **Step 1: Write the failing test**

Create `tests/test_availability.py`:

```python
"""What the engine does with a column the connected role may not read."""

from __future__ import annotations

from pysqlsuggestions import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Availability

USERS = {('public', 'users'): [('id', 'bigint'), ('email', 'text'), ('password', 'text')]}


def _catalog() -> MemoryCatalog:
    return MemoryCatalog(USERS, restricted={('public', 'users'): ['password']})


def test_a_restricted_column_is_offered_last_and_says_why() -> None:
    """Still offered: the name exists, and vanishing reads as the engine not knowing it."""
    sql = 'SELECT * FROM users u WHERE u.'
    found = complete(sql, len(sql), POSTGRES, _catalog())
    assert [s.text for s in found] == ['id', 'email', 'password']
    assert found[-1].availability is Availability.RESTRICTED
    assert found[-1].reason == 'no SELECT privilege'
    assert all(s.availability is Availability.AVAILABLE for s in found[:-1])


def test_an_unreadable_relation_sinks_in_a_from_list() -> None:
    sql = 'SELECT * FROM '
    catalog = MemoryCatalog(
        {('public', 'users'): [('id', 'bigint')], ('public', 'secrets'): [('id', 'bigint')]},
        restricted={('public', 'secrets'): None},
    )
    found = [s.text for s in complete(sql, len(sql), POSTGRES, catalog) if s.text in {'users', 'secrets'}]
    assert found == ['users', 'secrets']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_availability.py -q`
Expected: FAIL — `password` is second (declaration order), and `availability` is `AVAILABLE`.

- [ ] **Step 3: Write the implementation**

In `resolve.py`, a module-level helper beside the candidate builders:

```python
_NO_PRIVILEGE = 'no SELECT privilege'
"""
Why a restricted candidate would fail, in the words the server uses.

One string because there is one cause. A column withheld individually and a
column inside a relation with no grant at all are the same refusal to whoever
typed it, and Postgres reports both as `permission denied`.
"""


def _restriction(state: Availability) -> tuple[Availability, str | None]:
    """The availability and reason a candidate carries, given what the catalog said."""
    return state, _NO_PRIVILEGE if state is Availability.RESTRICTED else None
```

In `_column_candidate`, add to the `Candidate(...)`:

```python
    availability, reason = _restriction(column.availability)
    return Candidate(
        ...,
        relation=relation,
        availability=availability,
        reason=reason,
    )
```

The same two lines in `_table_candidate`, from `table.availability`.

Import `Availability` in `resolve.py`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_availability.py -q && uv run pytest -m 'not integration' -q`
Expected: PASS. Existing tests are untouched because every fixture without `restricted=` reports `UNKNOWN`, which `_restriction` maps to no reason and no sink.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/resolve.py tests/test_availability.py
git commit -m "feat: columns and relations carry what the role may read

Lifted at the two candidate builders, so every path that produces a
column or a relation gets it — including the two search capabilities,
whose rows come from the same records. That is the whole reason
availability rides on the rows rather than arriving through a method
of its own: search_columns returns rows from many relations at once,
and a per-row privilege lookup is the uncacheable read the port design
refuses."
```

---

### Task 5: Star expansion omits what it may not read

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py:661-715` (`_expansion`)
- Test: `tests/test_availability.py`

**Interfaces:**
- Consumes: Task 4's lifted `Candidate.availability` on the columns `_columns_of` returns.
- Produces: the `Kind.EXPANSION` candidate omits restricted columns, stays `AVAILABLE`, and carries `reason`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_expansion_omits_what_the_role_cannot_read() -> None:
    """`SELECT *` over a partly-restricted relation is refused outright, so the expansion is the fix."""
    sql = 'SELECT * FROM users'
    found = [s for s in complete(sql, 8, POSTGRES, _catalog()) if s.kind is Kind.EXPANSION]
    assert found[0].text == 'id, email'
    assert found[0].reason == '1 column omitted: no SELECT privilege'


def test_the_expansion_stays_available_despite_the_reason() -> None:
    """It is the one statement at that caret the server accepts; sinking it would bury the answer."""
    sql = 'SELECT * FROM users'
    found = [s for s in complete(sql, 8, POSTGRES, _catalog()) if s.kind is Kind.EXPANSION]
    assert found[0].availability is Availability.AVAILABLE


def test_an_unreadable_relation_expands_to_nothing() -> None:
    """Line 697's guard already covers it: an expansion to nothing would delete the star."""
    catalog = MemoryCatalog({('public', 'secrets'): [('id', 'bigint')]}, restricted={('public', 'secrets'): None})
    sql = 'SELECT * FROM secrets'
    assert not [s for s in complete(sql, 8, POSTGRES, catalog) if s.kind is Kind.EXPANSION]
```

Add `Kind` to the imports. Confirm the caret offset 8 lands on the star by checking an existing case in `tests/test_star_expansion.py` and copying its convention.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_availability.py -k expansion -q`
Expected: FAIL — the text is `id, email, password` and `reason` is `None`.

- [ ] **Step 3: Write the implementation**

In `_expansion`, filter inside the loop at line 694 and count what was dropped:

```python
    omitted = 0
    for relation in relations:
        path = _qualifier_for(relation, ambiguous) if qualify else ()
        prefix = '.'.join(quote_if_needed(part, dialect) for part in path)
        for column in _columns_of(relation, reader, seen):
            if column.availability is Availability.RESTRICTED:
                # Omitted rather than listed, because this is the only candidate
                # here the server would accept. Over a partly-restricted relation
                # `SELECT *` is refused outright — table SELECT implies every
                # column, so losing one means losing the table-level grant — and
                # the expansion turns a statement that errors into one that runs.
                omitted += 1
                continue
            rendered = quote_if_needed(column.text, dialect)
            names.append(f'{prefix}.{rendered}' if prefix else rendered)
```

And on the returned `Candidate`:

```python
            span=request.star,
            # Not RESTRICTED: accepting this works. `reason` explains the
            # omission, `availability` says whether accepting succeeds, and here
            # it does — marking it restricted would sink the one suggestion at
            # this caret the server accepts underneath the columns it is made of.
            reason=f'{omitted} column{"" if omitted == 1 else "s"} omitted: {_NO_PRIVILEGE}' if omitted else None,
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_availability.py tests/test_star_expansion.py -q && uv run pytest -m 'not integration' -q`
Expected: PASS, `tests/test_star_expansion.py` unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/resolve.py tests/test_availability.py
git commit -m "feat: star expansion emits only the columns the role may read

Not a filter but a repair. Table-level SELECT implies every column, so
a relation with one column withheld has no table-level grant and its
SELECT * is refused outright — the expansion turns a statement that
errors into one that runs.

Which is why the expansion itself stays AVAILABLE and carries only a
reason. It is the single candidate at that caret the server accepts;
sinking it would bury the answer under the columns it is assembled from."
```

---

### Task 6: Value hints refuse a restricted column

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py:775-814` (`_values`)
- Test: `tests/test_availability.py`

**Interfaces:**
- Consumes: Task 4.
- Produces: no `Kind.VALUE` candidate for a `RESTRICTED` column, from either source.

- [ ] **Step 1: Write the failing test**

```python
def test_no_literal_is_drawn_from_a_column_the_role_cannot_read() -> None:
    """The one interaction where today's behaviour leaks data rather than wasting a keystroke."""
    catalog = MemoryCatalog(
        {('public', 'users'): [('id', 'bigint'), ('password', 'text')]},
        restricted={('public', 'users'): ['password']},
        values={('public', 'users', 'password'): ['hunter2', 'letmein']},
    )
    sql = 'SELECT * FROM users u WHERE u.password = '
    assert not [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.VALUE]


def test_a_self_enumerating_type_is_refused_too() -> None:
    """A boolean's values come from the type, not the rows — but referencing the column still fails."""
    catalog = MemoryCatalog(
        {('public', 'users'): [('id', 'bigint'), ('is_admin', 'boolean')]},
        restricted={('public', 'users'): ['is_admin']},
    )
    sql = 'SELECT * FROM users u WHERE u.is_admin = '
    assert not [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.VALUE]


def test_an_unrestricted_column_still_offers_its_values() -> None:
    catalog = MemoryCatalog(
        {('public', 'users'): [('state', 'text')]},
        restricted={('public', 'users'): ['nothing_by_this_name']},
        values={('public', 'users', 'state'): ['active']},
    )
    sql = 'SELECT * FROM users u WHERE u.state = '
    assert [s.text for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.VALUE] == ["'active'"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_availability.py -k literal -q`
Expected: FAIL — `'hunter2'` and `'letmein'` are offered.

- [ ] **Step 3: Write the implementation**

In `_values`, after the column is found at line 794-796:

```python
        column = next((c for c in reader.columns(schema, table) if c.name == path[-1]), None)
        if column is None:
            continue
        if column.availability is Availability.RESTRICTED:
            # The check sits here rather than in `_Reader.common_values` for two
            # reasons. The Column is already in hand, so it costs no lookup —
            # and this also covers `datatypes.literals`, whose values come from
            # the type rather than from statistics. Those leak nothing, but a
            # literal compared against a column the role cannot reference is a
            # statement the server refuses either way.
            #
            # In the resolver rather than in each adapter, because Postgres is
            # the only backend whose statistics are already role-filtered by the
            # server: it is every *other* adapter that needs this rule, which is
            # exactly why it cannot live in them.
            return []
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_availability.py tests/test_values.py -q && uv run pytest -m 'not integration' -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/resolve.py tests/test_availability.py
git commit -m "fix: no value literal from a column the role cannot read

The one knock-on where the old behaviour leaked data rather than
wasting a keystroke. In _values rather than in _Reader.common_values,
which the design named: the Column is already in hand there, so the
check costs nothing, and it also covers the type-derived literals —
an enum's labels leak nothing but the comparison is still refused.

In the resolver rather than in each adapter, because Postgres is the
only backend whose statistics the server already filters by role."
```

---

### Task 7: Join proposals to an unreadable relation sink

**Files:**
- Modify: `src/pysqlsuggestions/engine/joins.py:35-55` (`relation_joins`), `151-174` (`_clause_candidate`)
- Modify: `src/pysqlsuggestions/resolve.py:180-200` (`_Reader`), `417-418` (the call site)
- Test: `tests/test_availability.py`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: `relation_joins(scope, edges, dialect, restricted=frozenset())` where `restricted` holds `(schema, table)` pairs; `_Reader` memoises within one request.

**Narrowing from the spec, deliberate.** §6.3 says "a proposal whose condition touches a restricted column on either side". This implements the *relation* level only. At `JOIN ⌶` the target's columns have not been fetched and fetching them would be one read per proposal — the cost the design refuses everywhere else. The narrowing is defensible on its own terms: an FK column is a key column, and key columns are essentially always granted, while the columns that get withheld individually — `password`, `ssn` — are not the ones constraints are declared on. Joining to a relation you cannot read at all is both plausible and useless, and that is the case this covers. Update §6.3 of the spec to say so (Task 12).

- [ ] **Step 1: Write the failing test**

```python
def test_a_join_to_an_unreadable_relation_sinks_but_stays() -> None:
    """The constraint is real and the user's next move may be to ask for the grant."""
    catalog = MemoryCatalog(
        {
            ('public', 'orders'): [('id', 'bigint'), ('user_id', 'bigint')],
            ('public', 'users'): [('id', 'bigint')],
            ('public', 'secrets'): [('id', 'bigint'), ('order_id', 'bigint')],
        },
        restricted={('public', 'secrets'): None},
        foreign_keys=[
            ForeignKey('public', 'orders', ('user_id',), 'public', 'users', ('id',)),
            ForeignKey('public', 'secrets', ('order_id',), 'public', 'orders', ('id',)),
        ],
    )
    sql = 'SELECT * FROM orders o JOIN '
    found = [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.JOIN]
    assert 'users' in found[0].text
    assert 'secrets' in found[-1].text
    assert found[-1].availability is Availability.RESTRICTED
    assert found[-1].reason == 'no SELECT privilege'


def test_one_request_reads_the_relation_list_once() -> None:
    """Availability must not cost a second round trip on a caller with no cache."""
    catalog = MemoryCatalog({('public', 'orders'): [('id', 'bigint')]})
    sql = 'SELECT * FROM orders o JOIN '
    complete(sql, len(sql), POSTGRES, catalog)
    assert len([call for call in catalog.calls if call[0] == 'tables']) == 1
```

Add `ForeignKey` to the imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_availability.py -k join -q`
Expected: FAIL — the proposal is `AVAILABLE`, and the second test reports 2 `tables` calls.

- [ ] **Step 3: Write the implementation**

First, memoise `_Reader` so asking twice in one request costs once. In `__init__`:

```python
        self._memo: dict[tuple[str | None, ...], object] = {}
        """
        Answers already given during this request.

        Distinct from `cache`, which is the caller's and may be absent. A single
        completion can ask the same question twice — the relation list serves
        both the TABLE candidates and the join proposals' availability — and
        with no cache supplied that would be two round trips for one answer.
        """
```

And in `_read`, before consulting `self._cache`:

```python
    def _read(self, key: tuple[str | None, ...], produce: Callable[[], _T]) -> _T:
        if key in self._memo:
            remembered: _T = self._memo[key]  # type: ignore[assignment]
            return remembered
        value = self._read_through(key, produce)
        self._memo[key] = value
        return value

    def _read_through(self, key: tuple[str | None, ...], produce: Callable[[], _T]) -> _T:
        """The caller's cache, when there is one. Unchanged behaviour."""
        if self._cache is None:
            return produce()
        cached = self._cache.get(key)
        if cached is not None:
            found: _T = cached
            return found
        value = produce()
        self._cache[key] = value
        return value
```

Add the reader helper:

```python
    def unreadable_relations(self, schema: str | None = None) -> frozenset[tuple[str, str]]:
        """
        Relations in `schema` the role may read nothing in, as (schema, name) pairs.

        Free at a JOIN caret: the same relation list already answers the TABLE
        candidates there, and `_read` remembers it within the request.
        """
        return frozenset(
            (table.schema, table.name)
            for table in self.tables(schema)
            if table.availability is Availability.RESTRICTED
        )
```

In `resolve.py:417-418`:

```python
    if request.clause == 'JOIN' and Kind.TABLE in request.kinds:
        candidates += joins.relation_joins(
            scope,
            _edges(scope, reader),
            dialect,
            restricted=reader.unreadable_relations(),
        )
```

In `joins.py`, widen the signature and thread it through:

```python
def relation_joins(
    scope: Scope | None,
    edges: Sequence[ForeignKey],
    dialect: Dialect,
    restricted: frozenset[tuple[str, str]] = frozenset(),
) -> list[Candidate]:
```

with an added paragraph in the docstring:

```
    `restricted` names relations the role may read nothing in. A proposal to one
    sinks rather than disappearing: the constraint is declared, the join is real,
    and asking for the grant is a reasonable next move. Relations rather than
    columns, because at this caret the target's columns have not been fetched and
    fetching them would be one read per proposal — an FK column is a key column
    and those are granted; the columns that get withheld are not the ones
    constraints are declared on.
```

Pass it into the builder at line 54 — `_clause_candidate(relation, source, link, taken, dialect, restricted)` — and in `_clause_candidate`, before the `return Candidate(...)`:

```python
    withheld = (link[1], link[2]) in restricted
```

adding to the returned `Candidate`:

```python
        note=_note(link),
        availability=Availability.RESTRICTED if withheld else Availability.AVAILABLE,
        reason=_NO_PRIVILEGE if withheld else None,
```

`joins.py` spells `_NO_PRIVILEGE = 'no SELECT privilege'` itself rather than importing Task 4's — `engine/` may not import `resolve`, and `tests/test_purity.py` fails the build over it. Two module constants with one value is the price of that rule; do not "fix" it by moving the string into `types.py`, which would put a resolver's wording in the value types.

Import `Availability` in `joins.py`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_availability.py tests/test_joins.py tests/test_joins_resolve.py -q && uv run pytest -m 'not integration' -q`
Expected: PASS. Watch `tests/test_scale.py` in particular — the memo changes call counts, and if a scale test asserts on them, read it before touching it.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/engine/joins.py src/pysqlsuggestions/resolve.py tests/test_availability.py
git commit -m "feat: a join proposal to an unreadable relation sinks

Relation level, not column level, and the narrowing is the point: at
JOIN the target's columns are not fetched, and fetching them would be
one read per proposal. An FK column is a key column and key columns are
granted; password and ssn are not what constraints are declared on.

_Reader gains a per-request memo so the relation list, which now answers
both the TABLE candidates and this, is read once even when the caller
supplied no cache."
```

---

### Task 8: Postgres reports the privilege

**Files:**
- Modify: `src/pysqlsuggestions/dialects/postgres.py:63-104` (`tables`, `columns`), `185-227` (`column_search`, `relation_search`)
- Test: `tests/test_dialect_records.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: the four Postgres queries select a privilege column; their row mappers translate `True → AVAILABLE`, `False → RESTRICTED`, `None → UNKNOWN`, and report `UNKNOWN` for relkinds where the question does not apply.

- [ ] **Step 1: Write the failing test**

```python
def test_the_column_mapper_reads_the_privilege_flag() -> None:
    """True, False and None are three different answers and none of them is a guess."""
    query = POSTGRES.catalog_queries.columns
    assert query is not None
    assert query.row(('public', 'users', 'id', 'bigint', 1, True)).availability is Availability.AVAILABLE
    assert query.row(('public', 'users', 'pw', 'text', 2, False)).availability is Availability.RESTRICTED
    assert query.row(('public', 'users', 'pw', 'text', 2, None)).availability is Availability.UNKNOWN


def test_a_relation_whose_columns_are_not_the_question_reports_unknown() -> None:
    """An index has no grantable columns and SELECT on a sequence means something else."""
    query = POSTGRES.catalog_queries.tables
    assert query is not None
    assert query.row(('public', 'users', 'r', 100, False)).availability is Availability.RESTRICTED
    assert query.row(('public', 'users_pkey', 'i', 0, False)).availability is Availability.UNKNOWN
    assert query.row(('public', 'users_id_seq', 'S', 0, False)).availability is Availability.UNKNOWN


def test_the_search_queries_report_it_too() -> None:
    """Or a column found across schemas would know less than the same column fetched by relation."""
    columns = POSTGRES.catalog_queries.column_search
    relations = POSTGRES.catalog_queries.relation_search
    assert columns is not None and relations is not None
    assert columns.row(('public', 'users', 'pw', 'text', 2, False)).availability is Availability.RESTRICTED
    assert relations.row(('public', 'users', 'r', 100, False)).availability is Availability.RESTRICTED


def test_the_other_dialects_say_nothing_rather_than_guessing() -> None:
    """ClickHouse has no has_column_privilege equivalent and Trino exposes nothing through SQL."""
    for dialect in (CLICKHOUSE, TRINO):
        query = dialect.catalog_queries.columns
        assert query is not None
        assert 'privilege' not in query.sql
        row = query.row(_a_row_for(dialect))
        assert row.availability is Availability.UNKNOWN
```

`_a_row_for` is a local helper returning a tuple of the width each dialect's `columns` mapper reads — check both with `grep -n "columns=Query" -A 20 src/pysqlsuggestions/dialects/clickhouse.py src/pysqlsuggestions/dialects/trino.py` and build the tuples from what the mappers index. Add `Availability` to the imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dialect_records.py -k privilege -q`
Expected: FAIL with `IndexError: tuple index out of range` — the mappers read five and four columns.

- [ ] **Step 3: Write the implementation**

`tables`: add the expression to the select list and a `_relation_state` mapper.

```sql
            SELECT n.nspname, c.relname, c.relkind, c.reltuples,
                   -- Whether the role may read anything in it at all. The
                   -- narrower question — whether `SELECT *` works — needs no
                   -- column: table-level SELECT implies every column, so losing
                   -- it means some column was granted individually, which the
                   -- column rows already say.
                   CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f')
                        THEN pg_catalog.has_any_column_privilege(c.oid, 'SELECT')
                   END
```

and the mapper gains `availability=_relation_state(row[2], row[4])`. The same two changes in `relation_search`.

`columns` and `column_search`: add `pg_catalog.has_column_privilege(c.oid, a.attnum, 'SELECT')` to the select list, and `availability=_column_state(row[5])` to both mappers. `column_search` already filters `c.relkind IN ('r', 'p', 'v', 'm', 'f')`, so no `CASE` is needed there; `columns` does not filter relkind, so guard it the same way `tables` does.

Two module-level helpers in `postgres.py`:

```python
def _column_state(flag: object) -> Availability:
    """
    What `has_column_privilege` said, or that it said nothing.

    NULL is not false. The query returns it where the question does not apply,
    and reading that as "restricted" would grey out every column of every index.
    """
    if flag is None:
        return Availability.UNKNOWN
    return Availability.AVAILABLE if flag else Availability.RESTRICTED


def _relation_state(relkind: object, flag: object) -> Availability:
    """
    The same, for a relation, with the relkinds the question does not fit.

    `tables` fetches indexes and sequences too, because both are relations in
    every sense pg_class knows. An index has no grantable columns, and SELECT on
    a sequence means something else entirely — so both report UNKNOWN rather
    than whatever the server makes of the question, since the failure mode here
    is a wrong answer rather than an error.
    """
    if str(relkind) not in {'r', 'p', 'v', 'm', 'f'}:
        return Availability.UNKNOWN
    return _column_state(flag)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_dialect_records.py -q && uv run pytest -m 'not integration' -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/dialects/postgres.py tests/test_dialect_records.py
git commit -m "feat: postgres reports what the connected role may read

One column on each of four queries — the two search queries included,
or a column found across schemas would know less than the same column
fetched by relation. No new round trip: has_*_privilege is answered
from cached ACL data.

NULL is not false. Indexes and sequences are fetched by the same query
and the question does not fit either, so they report UNKNOWN rather
than whatever the server makes of it."
```

---

### Task 9: The LSP renders it as honestly as the protocol allows

**Files:**
- Modify: `lsp/pysqlsuggestions_lsp/convert.py:107-117` (`_detail`), `132-160` (`to_item`)
- Test: `tests/lsp/test_convert.py`

**Interfaces:**
- Consumes: `Suggestion.availability`, `Suggestion.reason` from Task 2.
- Produces: a restricted item carries `tags=[CompletionItemTag.Deprecated]`; `reason` joins `detail` and `note` in the item's `detail`.

- [ ] **Step 1: Write the failing test**

Append to `tests/lsp/test_convert.py`, using that module's own `suggestion()` and `item()` helpers (lines 21-29):

```python
def test_a_restricted_item_is_tagged_and_says_why() -> None:
    """Strikethrough is the closest thing the protocol has to a disabled state."""
    offered = suggestion(
        'password',
        Kind.COLUMN,
        (7, 7),
        detail='users.password :: text',
        availability=Availability.RESTRICTED,
        reason='no SELECT privilege',
    )
    result = item('SELECT ', offered)
    assert result.tags == [CompletionItemTag.Deprecated]
    assert result.detail is not None
    assert 'no SELECT privilege' in result.detail


def test_an_available_item_carries_no_tag() -> None:
    assert item('SELECT ', suggestion('id', Kind.COLUMN, (7, 7))).tags is None
```

Add `CompletionItemTag` to that module's `lsprotocol.types` import and `Availability` to its `pysqlsuggestions.types` import.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/lsp -k restricted -q`
Expected: FAIL — `item.tags` is `None`.

- [ ] **Step 3: Write the implementation**

Extend `_detail`'s body and docstring:

```python
def _detail(suggestion: Suggestion) -> str | None:
    """
    What the thing is, why it outranks its neighbours, and why it would fail.

    `detail` says what it is; `note` says why it won — `fk: flight.id`; `reason`
    says why accepting it will not work. They are separate on the Suggestion and
    a client has one field, so they are joined rather than any being dropped.
    """
    parts = [part for part in (suggestion.detail, suggestion.note, suggestion.reason) if part]
    return '  '.join(parts) if parts else None
```

In `to_item`, add to the returned `CompletionItem`:

```python
        tags=[CompletionItemTag.Deprecated] if suggestion.availability is Availability.RESTRICTED else None,
```

and to `to_item`'s docstring:

```
    A restricted item is tagged Deprecated, which renders as strikethrough and
    is the closest the protocol comes to a disabled state. It does not stop a
    client inserting it, and nothing here pretends otherwise: an empty
    `text_edit` plus a command would produce an item that silently does nothing,
    which reads as a bug in the server rather than as a privilege the user lacks.
```

Import `CompletionItemTag` from `lsprotocol.types` alongside the existing imports, and `Availability` from `pysqlsuggestions.types`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/lsp -q && uv run pytest -m 'not integration' -q`
Expected: PASS. The sink itself needs no change — `sort_text` already carries the engine's index.

- [ ] **Step 5: Commit**

```bash
git add lsp/pysqlsuggestions_lsp/convert.py tests/lsp
git commit -m "feat: the server tags a restricted item and says why

sortText already carried the engine's ranking, so the sink arrived for
free; the additions are the Deprecated tag and the reason joining
detail and note in the one field a client has.

A client will still insert whatever we return. Faking a block with an
empty textEdit and a command is refused: an item that silently does
nothing reads as a bug in the server rather than as a missing grant."
```

---

### Task 10: The container proves it

**Files:**
- Modify: `docker/postgres/03-roles.sql`
- Modify: `tests/integration/conftest.py:22` (a second DSN and fixture)
- Test: `tests/integration/test_availability.py` (create)

**Interfaces:**
- Consumes: Task 8.
- Produces: an `analyst_catalog` pytest fixture, skipping like its siblings when the backend is unreachable.

- [ ] **Step 1: Add the missing fixture case**

`03-roles.sql` seeds both column cases already — `reports_database.password` withheld individually, and `mattermost_mattermostchannel` arranged so `has_any_column_privilege` is true while `has_table_privilege` is false. It has no relation with no grant at all, so `Table.availability = RESTRICTED` has nothing to detect. Append:

```sql
-- A relation with no grant at all, so has_any_column_privilege is false and the
-- table half of Availability has something real to detect. Without this the
-- relation-level rule ships tested only against MemoryCatalog.
REVOKE SELECT ON reports_reportexecution FROM analyst;
```

Confirm that relation exists in the seed — `grep -rn "reports_reportexecution" docker/postgres/` — and pick another from `01-schema.sql` if it does not. It must be a relation `analyst` is otherwise granted, or the `REVOKE` is a no-op.

While there: the file's header comment gives the port as 55432 and `tests/integration/conftest.py:22` uses 57432. Fix the comment to match the compose file.

- [ ] **Step 2: Write the failing test**

`tests/integration/test_availability.py`:

```python
"""Availability against the real server, as the restricted role rather than the owner."""

from __future__ import annotations

import pytest

from pysqlsuggestions import complete
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Availability, Kind

pytestmark = pytest.mark.integration


def test_a_withheld_column_is_restricted(analyst_catalog) -> None:
    found = {c.name: c.availability for c in analyst_catalog.columns('public', 'reports_database')}
    assert found['password'] is Availability.RESTRICTED
    assert found['title'] is Availability.AVAILABLE


def test_a_relation_with_no_grant_is_restricted(analyst_catalog) -> None:
    tables = {t.name: t.availability for t in analyst_catalog.tables('public')}
    assert tables['reports_reportexecution'] is Availability.RESTRICTED
    assert tables['reports_database'] is Availability.AVAILABLE


def test_an_index_and_a_sequence_report_unknown(analyst_catalog) -> None:
    """The relkind guard: the question does not apply, so the answer is not a guess."""
    states = {(t.kind, t.availability) for t in analyst_catalog.tables('public')}
    assert all(state is Availability.UNKNOWN for kind, state in states if kind in {'index', 'sequence'})


def test_the_expansion_omits_the_ungranted_columns(analyst_catalog) -> None:
    """mattermost_mattermostchannel is seeded for exactly this: SELECT * errors, id and name work."""
    sql = 'SELECT * FROM mattermost_mattermostchannel'
    found = [s for s in complete(sql, 8, POSTGRES, analyst_catalog) if s.kind is Kind.EXPANSION]
    assert found and found[0].text == 'id, name'
    assert found[0].reason is not None
```

Annotate the fixture parameter with the catalog type once the fixture exists — mypy strict covers `tests`.

- [ ] **Step 3: Add the fixture**

In `tests/integration/conftest.py`, beside `POSTGRES_DSN`:

```python
ANALYST_DSN = 'postgresql://analyst:analyst@localhost:57432/report_service'
"""
The restricted role from `docker/postgres/03-roles.sql`.

A second connection rather than `SET ROLE` on the first: `has_column_privilege`
evaluates against the current role, and a fixture that has to remember to reset
it is a fixture that will eventually leak one test's privileges into another's.
"""
```

and a fixture in the shape of the existing `postgres` one — same skip-on-unreachable behaviour, yielding `DbapiCatalog(connection.cursor, POSTGRES, paramstyle=psycopg2.paramstyle)`.

- [ ] **Step 4: Run the tests**

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d --wait
uv run pytest tests/integration/test_availability.py -q
```

Expected: PASS. `down -v` is required — `03-roles.sql` runs only on an empty data directory, so an existing volume keeps the old grants.

- [ ] **Step 5: Commit**

```bash
git add docker/postgres/03-roles.sql tests/integration/conftest.py tests/integration/test_availability.py
git commit -m "test: availability against the container, as the restricted role

A second connection as analyst rather than SET ROLE on the owner's:
has_column_privilege evaluates against the current role, and a fixture
that must remember to reset it will eventually leak one test's
privileges into another's.

The seed gains a relation with no grant at all. It had both column
cases already and no relation case, so Table.availability was testable
only against MemoryCatalog."
```

---

### Task 11: The cache key stops being an argument

**Files:**
- Test: `tests/test_availability.py`

**Interfaces:**
- Consumes: Tasks 3-4.
- Produces: nothing. This task adds the test that gives `Cache`'s documented key its first enforcement.

- [ ] **Step 1: Write the failing test**

Check `complete()`'s signature for the argument names first — `grep -n "def complete" -A 30 src/pysqlsuggestions/api.py` — and match them.

```python
def test_one_cache_two_roles_do_not_leak() -> None:
    """
    `role` has led the documented cache key since v0.1 on an argument alone.

    This is the first feature that gives it meaning, and the failure it prevents
    is silent: user A's readable set served to user B reads as a database
    privilege bug rather than a caching one.
    """
    shared: dict[object, object] = {}
    permissive = MemoryCatalog(USERS, restricted={('public', 'users'): []})
    restrictive = MemoryCatalog(USERS, restricted={('public', 'users'): ['password']})
    sql = 'SELECT * FROM users u WHERE u.'

    for catalog, identity, expected in (
        (permissive, 'alice', Availability.AVAILABLE),
        (restrictive, 'bob', Availability.RESTRICTED),
        (permissive, 'alice', Availability.AVAILABLE),
    ):
        found = complete(sql, len(sql), POSTGRES, catalog, cache=shared, identity=identity)
        password = next(s for s in found if s.text == 'password')
        assert password.availability is expected, f'{identity} saw the wrong readable set'
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_availability.py -k cache -q`
Expected: PASS on the first run. This test is a guard rather than a driver — the key has been right since v0.1 and this proves it. If it *fails*, stop: that is a live cross-user data leak and it outranks the rest of this plan.

- [ ] **Step 3: Add the same assertion without an identity**

```python
def test_an_unnamed_role_still_gets_its_own_line_in_the_key() -> None:
    """identity=None is a role like any other, not a wildcard that matches every entry."""
    shared: dict[object, object] = {}
    permissive = MemoryCatalog(USERS, restricted={('public', 'users'): []})
    restrictive = MemoryCatalog(USERS, restricted={('public', 'users'): ['password']})
    sql = 'SELECT * FROM users u WHERE u.'

    complete(sql, len(sql), POSTGRES, permissive, cache=shared)
    found = complete(sql, len(sql), POSTGRES, restrictive, cache=shared, identity='bob')
    assert next(s for s in found if s.text == 'password').availability is Availability.RESTRICTED
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_availability.py -q && ./scripts/check.sh`
Expected: PASS throughout.

- [ ] **Step 5: Commit**

```bash
git add tests/test_availability.py
git commit -m "test: one cache, two roles, no leak

role has led the documented cache key since v0.1 on the strength of
this feature's argument. Availability is what finally gives it meaning,
so it gets the test the argument implies — the failure is silent and
reads as a database privilege bug rather than a caching one, which is
exactly the kind that should fail in CI instead."
```

---

### Task 12: Documentation, the demo, and the spec's own corrections

**Files:**
- Modify: `README.md:12-17` (status paragraph) and a new section
- Modify: `docs/gaps.md:137-162` ("Already named elsewhere") → move into "Closed since this list was written" at line 59
- Modify: `CHANGELOG.md` (new `## Unreleased` above `## 0.5.0`)
- Modify: `lsp/README.md`
- Modify: `demo/schema.py:324-345` (the Postgres snapshot)
- Modify: `docs/superpowers/specs/2026-08-14-per-role-availability-design.md` (§6.2, §6.3, §10.4, and the `auto()` in §3)

- [ ] **Step 1: The demo**

Give `demo.schema.postgres()` one restricted column via `restricted=`, so the panel shows the annotation. The schema is invented rather than exported from anywhere — that is the standing rule for a page that gets published — so pick a column where a withheld grant is plausible in a flight-booking database, such as a passenger's contact detail.

- [ ] **Step 2: The README**

Drop `per-role availability` from the still-to-come sentence at line 15, leaving physical layout ranking, history ranking and the syntax extensions. Add a section in the shape of the existing ones ("Value suggestions", "Joins"), showing a restricted column arriving last with its reason, and stating plainly: Postgres has this, ClickHouse and Trino report `UNKNOWN`, and the engine reports rather than enforces.

- [ ] **Step 3: `docs/gaps.md`**

Move the "Per-role availability" bullet out of "Already named elsewhere" into "Closed since this list was written", following that section's established register — what was built, and what the original specification got wrong. Three things to record: the second table-level boolean that the column rows already implied; the kind-group sink that no constant expresses; and the join rule narrowing to relations, with the cost argument. Note that `Availability` is now the only one of that section's five entries that has moved.

- [ ] **Step 4: `CHANGELOG.md`**

A new `## Unreleased` section above `## 0.5.0`, grouped the way that file is — by what changes at a caret, not by commit. The three groups this touches: a restricted column now arrives last and says why; `SELECT *` over a partly-restricted relation expands to the columns that work; and the right-hand side of a comparison on a restricted column offers nothing where it previously offered literals.

- [ ] **Step 5: `lsp/README.md`**

State what a client can and cannot be made to do with a restricted item: strikethrough via the `Deprecated` tag, the reason in `detail`, the sink via `sortText` — and that insertion cannot be blocked, with the reason the empty-`textEdit` trick is refused.

- [ ] **Step 6: Correct the spec**

The spec is committed and should stay true to what was built. Four edits:

- §3's enum uses `auto()`, copied from `plan.md`. `types.py` uses explicit strings for `Kind` because consumers serialise them into an editor payload, and `Availability` follows suit.
- §6.2 puts the value-hint check in `_Reader.common_values`. It is in `_values`, where the `Column` is already in hand — which costs no lookup and additionally covers `datatypes.literals`, the type-derived path the spec did not consider.
- §6.3 says a join proposal sinks when its condition touches a restricted column on either side. It sinks on the *relation* level, for the cost reason in Task 7.
- §10.4 assigns a check to `DialectConformance`. That suite runs completion cases against a `MemoryCatalog` and never sees a row mapper, so the check lives in `tests/test_dialect_records.py` instead, which already tests mappers with fabricated rows.

- [ ] **Step 7: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS. Then confirm the three burn-downs printed by the test run are unmoved: `corpus 34/34`, `report_service 158/158`, `grammar 61/74`.

- [ ] **Step 8: Commit**

```bash
git add README.md docs/gaps.md CHANGELOG.md lsp/README.md demo/schema.py docs/superpowers/specs/2026-08-14-per-role-availability-design.md
git commit -m "docs: per-role availability, and what the spec got wrong

gaps.md gains the entry in the register that section uses — what was
built and what the original specification missed. Three corrections
travel back into the spec: the value-hint check moved to _values where
the Column is already in hand and the type-derived literals are covered
too; the join rule narrowed to relations; and the conformance check
moved to test_dialect_records, since DialectConformance runs completion
cases and never sees a row mapper."
```

---

## Notes for the executor

**The negative test is the real gate.** After every task, `uv run pytest -m 'not integration' -q` must show the pre-existing tests passing unchanged. Defaults of `UNKNOWN` on catalog records and `AVAILABLE` on engine records are what make that true. An existing assertion that moves is evidence the degradation is not as honest as the design claims — stop and report rather than editing the assertion to match.

**Task 7 changes `_Reader` for everyone.** The per-request memo is a behaviour change for callers with no cache: a repeated question now costs one read instead of two. `tests/test_scale.py` and any test asserting on `MemoryCatalog.calls` are where that will surface. Read such a test before changing it — if it was asserting that a CTE costs no catalog reads, it should still pass.

**Task 10 needs `down -v`.** `03-roles.sql` is an init script and runs only against an empty data directory.
