# FK-derived join completion — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offer the whole `JOIN … ON …` clause once `JOIN` is typed, and the whole join condition at `ON ⌶`, both derived from declared foreign keys.

**Architecture:** A new optional capability (`SupportsForeignKeys`) returns an edge list per schema; a new pure module `engine/joins.py` turns edges plus the statement's scope into candidates; `resolve.py` does the fetching and `rank.py` scores the new kind. Nothing in lex, analyse or request derivation changes.

**Tech Stack:** Python ≥3.10, standard library only for the runtime. uv, ruff, mypy strict, pytest. Postgres/ClickHouse/Trino via docker compose for integration tests.

**Spec:** `docs/superpowers/specs/2026-08-11-fk-derived-joins-design.md`

## Global Constraints

- **Zero runtime dependencies.** Nothing under `src/` may import a driver or any third-party package.
- **The engine stays pure.** `src/pysqlsuggestions/engine/` may not import `pysqlsuggestions.ports` or `pysqlsuggestions.resolve` — enforced by `tests/test_purity.py`.
- **ruff:** line length 120, single quotes, `D` docstring rules on. Every public function and class needs a docstring.
- **mypy strict.** Full annotations, including `-> None` on tests.
- **Style:** docstrings explain *why*, following the surrounding files. Comments earn their place.
- **Verification command:** `./scripts/check.sh` (ruff format --check, ruff check, mypy, pytest). Offline subset: `uv run pytest -m 'not integration'`.
- **Declared constraints only.** No inference from column names anywhere in this plan.

---

## File Structure

**Created**
- `src/pysqlsuggestions/engine/joins.py` — pure builders: edges + scope → candidates.
- `tests/test_joins.py` — unit tests for the pure builders.
- `tests/test_joins_resolve.py` — end-to-end offline tests through `complete()`.

**Modified**
- `src/pysqlsuggestions/types.py` — `ForeignKey`, `Kind.JOIN`, `note` on `Candidate` and `Suggestion`.
- `src/pysqlsuggestions/__init__.py` — export `ForeignKey` and `SupportsForeignKeys`.
- `src/pysqlsuggestions/ports.py` — `SupportsForeignKeys`.
- `src/pysqlsuggestions/resolve.py` — `_Reader.foreign_keys`, wiring at both positions.
- `src/pysqlsuggestions/engine/rank.py` — `_JOIN_BONUS`, `Kind.JOIN` in `_kind_bonus`, `note` onto `Suggestion`.
- `src/pysqlsuggestions/catalogs/memory.py` — `foreign_keys=` keyword.
- `src/pysqlsuggestions/catalogs/dbapi.py` — `foreign_keys()` method.
- `src/pysqlsuggestions/dialects/base.py` — `CatalogQueries.foreign_keys` slot.
- `src/pysqlsuggestions/dialects/postgres.py` — the `pg_constraint` query.
- `src/pysqlsuggestions/testing/__init__.py` — one conformance case.
- `docker/postgres/01-schema.sql`, `docker/README.md` — a composite foreign key.
- `tests/integration/test_backends.py`, `tests/test_writable.py`, `tests/integration/test_acceptance.py`.
- `demo/schema.py`, `demo/payload.py`, `demo/static/index.html`, `README.md`.

---

## Task 1: Types

**Files:**
- Modify: `src/pysqlsuggestions/types.py:11-34` (Kind), `:256-289` (Candidate), `:348-377` (Suggestion)
- Modify: `src/pysqlsuggestions/__init__.py`
- Test: `tests/test_joins.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ForeignKey(schema, table, columns, ref_schema, ref_table, ref_columns)` — all `str` except the two `tuple[str, ...]`; `Kind.JOIN` with value `'join'`; `Candidate.note: str | None = None`; `Suggestion.note: str | None = None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_joins.py`:

```python
"""Join proposals built from declared foreign keys. Pure — no catalog, no database."""

from __future__ import annotations

from pysqlsuggestions import ForeignKey
from pysqlsuggestions.types import Candidate, Kind, Suggestion


def test_foreign_key_carries_both_sides() -> None:
    """Column tuples on both sides, positionally aligned, so a composite key needs no special case."""
    edge = ForeignKey(
        schema='public',
        table='reports_report',
        columns=('author_id',),
        ref_schema='public',
        ref_table='auth_user',
        ref_columns=('id',),
    )
    assert edge.columns == ('author_id',)
    assert edge.ref_columns == ('id',)


def test_join_is_its_own_kind() -> None:
    """A whole clause is not a table and not a column; a front end may say so."""
    assert Kind.JOIN.value == 'join'


def test_note_defaults_to_none_on_both_carriers() -> None:
    """Additive: every existing construction site keeps working untouched."""
    assert Candidate(text='id', kind=Kind.COLUMN).note is None
    assert Suggestion(text='id', kind=Kind.COLUMN, replace_span=(0, 0), score=1.0).note is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_joins.py -v`
Expected: FAIL — `ImportError: cannot import name 'ForeignKey' from 'pysqlsuggestions'`

- [ ] **Step 3: Add `ForeignKey` to `types.py`**

Insert after the `ColumnValue` class (after line 104), before `Projection`:

```python
@dataclass(frozen=True, slots=True)
class ForeignKey:
    """
    One declared relationship: `columns` of `table` reference `ref_columns` of `ref_table`.

    Both sides are tuples and correspond positionally, so a composite key is
    representable from the start and renders as an `AND` chain. A backend with no
    constraints reports none rather than guessing from column names: a wrong join
    condition is valid SQL that returns wrong rows, which is a worse failure than
    offering nothing.
    """

    schema: str
    table: str
    columns: tuple[str, ...]
    ref_schema: str
    ref_table: str
    ref_columns: tuple[str, ...]
```

- [ ] **Step 4: Add `Kind.JOIN` and fix the misplaced docstring**

`Kind` currently has OPERATOR's docstring stranded below `VALUE` (line 34). Replace lines 26-34:

```python
    KEYWORD = 'keyword'
    OPERATOR = 'operator'
    """`=`, `<>`, `>=`. Separate from KEYWORD because it has no case to follow."""
    TYPE = 'type'
    """A data type name, wanted after a cast: `'7 days'::interval`."""
    SNIPPET = 'snippet'
    """A whole statement shape with places to fill in, offered where one can start."""
    VALUE = 'value'
    """A literal the compared column actually holds: `WHERE type = 'postgres'`."""
    JOIN = 'join'
    """
    A whole join clause or join condition, derived from a declared foreign key.

    Not a TABLE: accepting it writes `auth_user u ON r.author_id = u.id`, not a
    name. Ranking treats it as whatever the position wanted — see `_kind_bonus`.
    """
```

- [ ] **Step 5: Add `note` to `Candidate` and `Suggestion`**

In `Candidate`, after `relation` (line 288-289):

```python
    note: str | None = None
    """
    Why this candidate is worth more than its neighbours: `fk: auth_user.id`.

    Distinct from `detail`, which says what the thing *is*. A front end may render
    it differently — the annotation is the teaching part of a ranked list.
    """
```

In `Suggestion`, after `relation` (line 369-377), the same field with the same docstring.

- [ ] **Step 6: Export from the package root**

In `src/pysqlsuggestions/__init__.py`, add `ForeignKey` to the `pysqlsuggestions.types` import block and to `__all__`, both in alphabetical position (after `Function`, and after `'Function',` respectively).

- [ ] **Step 7: Run the test and the full offline suite**

Run: `uv run pytest tests/test_joins.py -v && uv run pytest -m 'not integration' -q`
Expected: PASS, and no existing test changes behaviour.

- [ ] **Step 8: Commit**

```bash
git add src/pysqlsuggestions/types.py src/pysqlsuggestions/__init__.py tests/test_joins.py
git commit -m "feat: a foreign key is a value type, and a join is a kind"
```

---

## Task 2: The port and the reader

**Files:**
- Modify: `src/pysqlsuggestions/ports.py:112` (before `SupportsKeywords`)
- Modify: `src/pysqlsuggestions/resolve.py:20` (imports), `:168` (after `common_values`)
- Modify: `src/pysqlsuggestions/__init__.py`
- Test: `tests/test_joins_resolve.py`

**Interfaces:**
- Consumes: `ForeignKey` from Task 1.
- Produces: `SupportsForeignKeys.foreign_keys(schema: str | None = None) -> Sequence[ForeignKey]`; `_Reader.foreign_keys(schema: str | None) -> Sequence[ForeignKey]` returning `()` when the capability is absent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_joins_resolve.py`:

```python
"""Foreign keys reaching the engine: capability detection, caching, degradation."""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.resolve import _Reader
from pysqlsuggestions.types import ForeignKey

EDGE = ForeignKey(
    schema='public',
    table='reports_report',
    columns=('author_id',),
    ref_schema='public',
    ref_table='auth_user',
    ref_columns=('id',),
)


class _Constrained:
    """A catalog that answers only the foreign-key question. Nothing else is needed here."""

    def __init__(self) -> None:
        self.calls = 0

    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """Record the call so the test can prove the cache stopped the second one."""
        self.calls += 1
        return [EDGE]


def test_reader_returns_nothing_without_the_capability() -> None:
    """A catalog that cannot answer degrades to silence, as every other capability does."""
    plain = MemoryCatalog({('public', 'auth_user'): [('id', 'bigint')]})
    reader = _Reader(plain, POSTGRES, None, None)
    assert reader.foreign_keys('public') == ()


def test_reader_reads_through_the_capability() -> None:
    """Present: the edges come back as the catalog reported them."""
    reader = _Reader(_Constrained(), POSTGRES, None, None)  # type: ignore[arg-type]
    assert list(reader.foreign_keys('public')) == [EDGE]


def test_reader_caches_edges_under_the_identity_led_key() -> None:
    """Constraints change on DDL, not between keystrokes, so one read serves the session."""
    catalog = _Constrained()
    cache: dict[object, object] = {}
    reader = _Reader(catalog, POSTGRES, cache, 'analyst')  # type: ignore[arg-type]
    reader.foreign_keys('public')
    reader.foreign_keys('public')
    assert catalog.calls == 1
    assert ('analyst', 'postgres', 'public', '\x00fk') in cache
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_joins_resolve.py -v`
Expected: FAIL — `AttributeError: '_Reader' object has no attribute 'foreign_keys'`

- [ ] **Step 3: Add the protocol to `ports.py`**

Add `ForeignKey` to the `pysqlsuggestions.types` import on line 17, then insert before `SupportsKeywords`:

```python
@runtime_checkable
class SupportsForeignKeys(Protocol):
    """
    Declared relationships between relations, for join completion.

    Absent: `JOIN <caret>` offers relation names and `ON <caret>` offers columns,
    which is what both offered before this existed.

    Only *declared* constraints belong here. A backend that keeps none — ClickHouse
    and Trino keep none — should not implement this rather than infer edges from
    column names, because a wrong join condition is valid SQL that silently returns
    wrong rows.
    """

    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """
        Every constraint whose referencing side lives in `schema`, or in the default namespace.

        Schema-scoped rather than per-relation because a join is undirected: the
        proposal at `FROM auth_user u JOIN <caret>` needs the edges that point *at*
        `auth_user`, and no per-relation call could find them without walking every
        relation in the database.
        """
        ...
```

- [ ] **Step 4: Add `_Reader.foreign_keys`**

In `resolve.py`, add `SupportsForeignKeys` to the `pysqlsuggestions.ports` import (line 20) and `ForeignKey` to the `pysqlsuggestions.types` import. Insert after `common_values` (after line 168):

```python
    def foreign_keys(self, schema: str | None) -> Sequence[ForeignKey]:
        """
        Declared relationships, for join proposals.

        Degrades to nothing when the catalog cannot answer, which is the documented
        behaviour when SupportsForeignKeys is absent. Cached like everything else:
        constraints change when someone runs DDL, not between keystrokes.
        """
        catalog = self._catalog
        if not isinstance(catalog, SupportsForeignKeys):
            return ()
        return self._read(self._key(schema or '', '\x00fk'), lambda: catalog.foreign_keys(schema))
```

- [ ] **Step 5: Export the protocol**

Add `SupportsForeignKeys` to the `pysqlsuggestions.ports` import block and `__all__` in `src/pysqlsuggestions/__init__.py`, alphabetically (before `SupportsColumnSearch` — `ForeignKeys` sorts before `ColumnSearch`? No: `SupportsColumnSearch` < `SupportsColumnValues` < `SupportsForeignKeys` < `SupportsKeywords`; place it third).

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_joins_resolve.py -v && uv run pytest -m 'not integration' -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/ports.py src/pysqlsuggestions/resolve.py src/pysqlsuggestions/__init__.py tests/test_joins_resolve.py
git commit -m "feat: a catalog may be asked what references what"
```

---

## Task 3: MemoryCatalog answers the question

**Files:**
- Modify: `src/pysqlsuggestions/catalogs/memory.py:35-46` (signature), `:70-80` (init body), `:169` (after `keywords`)
- Test: `tests/test_memory_catalog.py`

**Interfaces:**
- Consumes: `ForeignKey`, `SupportsForeignKeys`.
- Produces: `MemoryCatalog(snapshot, foreign_keys=[ForeignKey(...)])`, satisfying `SupportsForeignKeys`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_catalog.py`:

```python
def test_foreign_keys_are_declared_and_filtered_by_schema() -> None:
    """A fixture declares edges; the port hands back the ones the schema owns."""
    edge = ForeignKey(
        schema='public',
        table='orders',
        columns=('user_id',),
        ref_schema='public',
        ref_table='users',
        ref_columns=('id',),
    )
    billing = ForeignKey(
        schema='billing',
        table='invoices',
        columns=('order_id',),
        ref_schema='public',
        ref_table='orders',
        ref_columns=('id',),
    )
    catalog = MemoryCatalog(
        {('public', 'orders'): [('id', 'bigint')], ('public', 'users'): [('id', 'bigint')]},
        foreign_keys=[edge, billing],
    )
    assert list(catalog.foreign_keys('public')) == [edge]
    assert list(catalog.foreign_keys(None)) == [edge, billing]


def test_foreign_keys_default_to_none_declared() -> None:
    """The overwhelming majority of fixtures declare none, and must behave exactly as before."""
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint')]})
    assert list(catalog.foreign_keys(None)) == []
```

Add `ForeignKey` to that file's imports from `pysqlsuggestions.types`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_memory_catalog.py -k foreign -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'foreign_keys'`

- [ ] **Step 3: Implement**

In `memory.py`, add `ForeignKey` to the imports from `pysqlsuggestions.types`. Add the keyword to `__init__` after `catalogs`:

```python
        foreign_keys: Iterable[ForeignKey] = (),
```

In the body, beside the other tuple conversions (after `self._values = {...}`):

```python
        self._foreign_keys = tuple(foreign_keys)
```

And the method, after `keywords`:

```python
    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """
        Declared relationships, when the fixture supplied any.

        Filtered by the *referencing* side's schema, matching what the Postgres
        query does — an edge is owned by the table that carries the constraint.
        """
        self.calls.append(('foreign_keys', schema or ''))
        if schema is None:
            return list(self._foreign_keys)
        return [edge for edge in self._foreign_keys if edge.schema == schema]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_memory_catalog.py -v && uv run pytest -m 'not integration' -q`
Expected: PASS. Every existing fixture declares no edges, so nothing else moves.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/catalogs/memory.py tests/test_memory_catalog.py
git commit -m "feat: a snapshot may declare its foreign keys"
```

---

## Task 4: `relation_joins` — the whole clause

**Files:**
- Create: `src/pysqlsuggestions/engine/joins.py`
- Test: `tests/test_joins.py`

**Interfaces:**
- Consumes: `ForeignKey`, `Kind.JOIN`, `Candidate.note`, `_alias_forms` from `engine/local.py`, `quote_if_needed` from `engine/rank.py`.
- Produces: `relation_joins(scope: Scope | None, edges: Sequence[ForeignKey], dialect: Dialect) -> list[Candidate]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_joins.py`:

```python
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.joins import relation_joins
from pysqlsuggestions.types import Relation, Scope

AUTHOR = ForeignKey(
    schema='public',
    table='reports_report',
    columns=('author_id',),
    ref_schema='public',
    ref_table='auth_user',
    ref_columns=('id',),
)


def scope_of(*relations: tuple[str, str | None]) -> Scope:
    """A scope of plain catalog relations, written as (name, alias) pairs."""
    return Scope(relations=tuple(Relation(alias=alias, path=(name,), source='table') for name, alias in relations))


def test_forward_edge_proposes_the_referenced_relation() -> None:
    """The scope relation holds the FK column, so the proposal joins what it points at."""
    found = relation_joins(scope_of(('reports_report', 'r')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['auth_user au ON r.author_id = au.id']
    assert found[0].label == 'auth_user'
    assert found[0].kind is Kind.JOIN
    assert found[0].note == 'fk: auth_user.id'
    assert found[0].position == 0


def test_reverse_edge_proposes_the_referencing_relation() -> None:
    """auth_user holds no FK columns; forward-only would leave this position empty."""
    found = relation_joins(scope_of(('auth_user', 'u')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['reports_report rr ON u.id = rr.author_id']
    assert found[0].note == 'fk: reports_report.author_id'
    assert found[0].position == 1


def test_an_unaliased_relation_qualifies_with_its_own_name() -> None:
    """`FROM reports_report JOIN <caret>` has no alias to point back at."""
    found = relation_joins(scope_of(('reports_report', None)), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['auth_user au ON reports_report.author_id = au.id']


def test_alias_avoids_one_already_in_scope() -> None:
    """`u` is taken, so the proposal must not write a second relation answering to it."""
    found = relation_joins(scope_of(('reports_report', 'r'), ('users', 'u')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['auth_user au ON r.author_id = au.id']


def test_self_reference_gets_a_distinct_alias() -> None:
    """A table referencing itself must not alias to the copy already written."""
    parent = ForeignKey(
        schema='public',
        table='reports_reportgroup',
        columns=('parent_id',),
        ref_schema='public',
        ref_table='reports_reportgroup',
        ref_columns=('id',),
    )
    found = relation_joins(scope_of(('reports_reportgroup', 'rr')), [parent], POSTGRES)
    snippets = [c.snippet for c in found]
    assert 'reports_reportgroup rr ON' not in ' '.join(str(s) for s in snippets)
    assert any(s is not None and s.startswith('reports_reportgroup r ON rr.parent_id = r.id') for s in snippets)


def test_two_edges_to_one_target_stay_two_proposals() -> None:
    """Both are real answers; picking one for the user would be picking wrong half the time."""
    created = ForeignKey(
        schema='public',
        table='reports_databaseaccess',
        columns=('user_created_id',),
        ref_schema='public',
        ref_table='auth_user',
        ref_columns=('id',),
    )
    owned = ForeignKey(
        schema='public',
        table='reports_databaseaccess',
        columns=('user_id',),
        ref_schema='public',
        ref_table='auth_user',
        ref_columns=('id',),
    )
    found = relation_joins(scope_of(('reports_databaseaccess', 'a')), [created, owned], POSTGRES)
    assert [c.snippet for c in found] == [
        'auth_user au ON a.user_created_id = au.id',
        'auth_user aut ON a.user_id = aut.id',
    ]


def test_composite_key_renders_an_and_chain() -> None:
    """Both column pairs, in the constraint's own order."""
    composite = ForeignKey(
        schema='public',
        table='usage',
        columns=('queryfilter_id', 'database_id'),
        ref_schema='public',
        ref_table='links',
        ref_columns=('queryfilter_id', 'database_id'),
    )
    found = relation_joins(scope_of(('usage', 'u')), [composite], POSTGRES)
    assert found[0].snippet == 'links l ON u.queryfilter_id = l.queryfilter_id AND u.database_id = l.database_id'


def test_a_target_in_another_schema_is_qualified() -> None:
    """The bare name would not resolve from a default search path."""
    cross = ForeignKey(
        schema='public',
        table='orders',
        columns=('invoice_id',),
        ref_schema='billing',
        ref_table='invoices',
        ref_columns=('id',),
    )
    found = relation_joins(scope_of(('orders', 'o')), [cross], POSTGRES)
    assert found[0].snippet == 'billing.invoices i ON o.invoice_id = i.id'


def test_a_name_needing_quotes_gets_them() -> None:
    """The snippet path never reaches `quote_if_needed`, so the builder must do it."""
    mixed = ForeignKey(
        schema='billing',
        table='orders',
        columns=('total_id',),
        ref_schema='billing',
        ref_table='MonthlyTotals',
        ref_columns=('id',),
    )
    found = relation_joins(scope_of(('orders', 'o')), [mixed], POSTGRES)
    assert found[0].snippet == 'billing."MonthlyTotals" m ON o.total_id = m.id'


def test_a_cte_has_no_constraints() -> None:
    """A relation the statement defined itself is in no catalog and carries no edges."""
    scope = Scope(relations=(Relation(alias='c', path=('c',), source='cte'),))
    assert relation_joins(scope, [AUTHOR], POSTGRES) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_joins.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pysqlsuggestions.engine.joins'`

- [ ] **Step 3: Write `engine/joins.py`**

```python
"""
Join proposals from declared foreign keys.

Pure: the edges arrive as data, so this module never imports `ports` and the
purity guard holds. `resolve` does the fetching and calls in here.

Two positions are answered. `JOIN <caret>` takes a whole clause — relation,
alias and condition in one accept — and `ON <caret>` takes the condition alone.
Both are built only from constraints the backend declares; nothing here guesses
an edge from a column name, because a wrong join condition is valid SQL that
returns wrong rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.engine.local import alias_forms
from pysqlsuggestions.engine.rank import quote_if_needed
from pysqlsuggestions.types import Candidate, ForeignKey, Kind, Relation, Scope

_FORWARD = 0
"""Many-to-one: the relation in scope holds the FK column. Ranked first — it cannot multiply rows."""
_REVERSE = 1
"""One-to-many: the relation in scope is the referenced side."""

_Link = tuple[str, str, tuple[tuple[str, str], ...], int]
"""(target schema, target table, (source column, target column) pairs, direction)."""


def relation_joins(scope: Scope | None, edges: Sequence[ForeignKey], dialect: Dialect) -> list[Candidate]:
    """
    Whole `JOIN` clauses for `JOIN <caret>`: relation, alias and condition together.

    Fires from either end of a constraint. A join is undirected even though a
    constraint is not, and a query starting from a relation that holds no FK
    columns — `auth_user` is referenced by seven tables in the docker fixture and
    references none — would otherwise be offered nothing at all.
    """
    if scope is None:
        return []
    taken = {relation.label.lower() for relation in scope.visible() if relation.label}
    candidates: list[Candidate] = []
    for relation in _catalog_relations(scope):
        source = _split(relation.path)
        for link in _links(source, edges):
            candidates.append(_clause_candidate(relation, source, link, taken, dialect))
    return candidates


def join_conditions(scope: Scope | None, edges: Sequence[ForeignKey], dialect: Dialect) -> list[Candidate]:
    """
    Whole conditions for `ON <caret>`, pairing the relation just joined with an earlier one.

    One accept finishes the join. The columns stay underneath, so a condition the
    constraints do not describe is still reachable by writing it.
    """
    if scope is None or len(scope.relations) < 2:  # noqa: PLR2004
        return []
    latest = scope.relations[-1]
    if latest.projection is not None:
        return []
    target = _split(latest.path)
    candidates: list[Candidate] = []
    for earlier in _catalog_relations(scope)[:-1]:
        source = _split(earlier.path)
        for link in _links(source, edges):
            if (link[0], link[1]) != target:
                continue
            condition = _condition(earlier.label, latest.label, link[2], dialect)
            candidates.append(
                Candidate(
                    text=condition,
                    kind=Kind.JOIN,
                    detail=f'joins {earlier.declared_name}',
                    position=link[3],
                    origin='catalog',
                    snippet=condition,
                    label=_fk_column(link),
                    note=_note(link),
                ),
            )
    return candidates


def condition_columns(relation: Relation, edges: Sequence[ForeignKey], dialect: Dialect) -> list[Candidate]:
    """
    FK columns of one relation, for `ON r.<caret>` where the qualifier has committed the left side.

    A whole condition is no longer expressible there — the text already says which
    relation the left operand belongs to — so the feature degrades to lifting that
    relation's FK columns and annotating them.
    """
    if relation.projection is not None:
        return []
    source = _split(relation.path)
    candidates: list[Candidate] = []
    for link in _links(source, edges):
        name = link[2][0][0]
        candidates.append(
            Candidate(
                text=name,
                kind=Kind.JOIN,
                detail=f'joins {link[1]}',
                position=link[3],
                origin='catalog',
                snippet=quote_if_needed(name, dialect),
                label=name,
                note=_note(link),
            ),
        )
    return candidates


def _catalog_relations(scope: Scope) -> list[Relation]:
    """Relations the catalog knows. A CTE or a derived table has no constraints to read."""
    return [relation for relation in scope.relations if relation.projection is None and relation.path]


def _split(path: tuple[str, ...]) -> tuple[str, str]:
    """(schema, table) from a relation path, with `''` for a schema the text did not name."""
    if len(path) >= 2:  # noqa: PLR2004
        return path[-2], path[-1]
    return '', path[-1] if path else ''


def _links(source: tuple[str, str], edges: Sequence[ForeignKey]) -> list[_Link]:
    """Every edge touching `source`, from either end, forward first."""
    schema, table = source
    forward: list[_Link] = []
    reverse: list[_Link] = []
    for edge in edges:
        if edge.table == table and _same_schema(schema, edge.schema):
            forward.append((edge.ref_schema, edge.ref_table, tuple(zip(edge.columns, edge.ref_columns)), _FORWARD))
        elif edge.ref_table == table and _same_schema(schema, edge.ref_schema):
            reverse.append((edge.schema, edge.table, tuple(zip(edge.ref_columns, edge.columns)), _REVERSE))
    return forward + reverse


def _same_schema(named: str, declared: str) -> bool:
    """An unqualified reference matches whatever schema the edge names; the search path decided it."""
    return not named or named == declared


def _clause_candidate(
    relation: Relation,
    source: tuple[str, str],
    link: _Link,
    taken: set[str],
    dialect: Dialect,
) -> Candidate:
    """One whole `JOIN` clause, with an alias that collides with nothing already in scope."""
    target_schema, target_table, pairs, direction = link
    alias = _free_alias(target_table, taken)
    taken.add(alias.lower())
    reference = _reference(source[0], target_schema, target_table, dialect)
    condition = _condition(relation.label, alias, pairs, dialect)
    snippet = f'{reference} {alias} ON {condition}'
    return Candidate(
        text=snippet,
        kind=Kind.JOIN,
        detail=f'joins {relation.declared_name}',
        position=direction,
        origin='catalog',
        snippet=snippet,
        label=target_table,
        note=_note(link),
    )


def _reference(source_schema: str, target_schema: str, target_table: str, dialect: Dialect) -> str:
    """The target's name, qualified when it lives somewhere the source's schema would not find it."""
    name = quote_if_needed(target_table, dialect)
    if target_schema and target_schema != source_schema:
        return f'{quote_if_needed(target_schema, dialect)}.{name}'
    return name


def _condition(left: str, right: str, pairs: tuple[tuple[str, str], ...], dialect: Dialect) -> str:
    """`a.x = b.x`, or an AND chain when the constraint names more than one column."""
    left_label = quote_if_needed(left, dialect)
    right_label = quote_if_needed(right, dialect)
    return ' AND '.join(
        f'{left_label}.{quote_if_needed(source, dialect)} = {right_label}.{quote_if_needed(target, dialect)}'
        for source, target in pairs
    )


def _free_alias(name: str, taken: set[str]) -> str:
    """
    The first idiomatic alias nothing in scope answers to.

    Falls through to a numbered form, which is what a self-join and a second edge
    to the same target both need — `auth_user u` twice would write a statement
    where neither reference resolves.
    """
    for form in alias_forms(name):
        if form.lower() not in taken:
            return form
    stem = alias_forms(name)[0] if alias_forms(name) else name[:1].lower()
    suffix = 2
    while f'{stem}{suffix}'.lower() in taken:
        suffix += 1
    return f'{stem}{suffix}'


def _fk_column(link: _Link) -> str:
    """The referencing side's first column — the name a user would type looking for this join."""
    _, _, pairs, direction = link
    return pairs[0][0] if direction == _FORWARD else pairs[0][1]


def _note(link: _Link) -> str:
    """
    Where the constraint lands on the far side: `fk: auth_user.id`.

    Names the target rather than the source, in both directions, because the
    source is already visible in the statement being written.
    """
    _, table, pairs, _ = link
    columns = [target for _, target in pairs]
    rendered = columns[0] if len(columns) == 1 else f'({", ".join(columns)})'
    return f'fk: {table}.{rendered}'
```

- [ ] **Step 4: Rename `_alias_forms` to `alias_forms` in `engine/local.py`**

`joins.py` needs it, and a private name crossing module boundaries is worse than a public one. In `src/pysqlsuggestions/engine/local.py`, rename the function at line 117 and its call site at line 113. Keep the docstring as it is.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_joins.py -v`
Expected: PASS

- [ ] **Step 6: Prove the engine stayed pure and the types check**

Run: `uv run pytest tests/test_purity.py -v && uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: all PASS. If ruff reports the `zip()` call needs `strict=`, add `strict=False` — the two sides of a valid constraint are the same length, but the linter cannot know that.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/engine/joins.py src/pysqlsuggestions/engine/local.py tests/test_joins.py
git commit -m "feat: a foreign key becomes a whole join clause"
```

---

## Task 5: `join_conditions` — the condition alone

**Files:**
- Test: `tests/test_joins.py`

**Interfaces:**
- Consumes: `join_conditions` and `condition_columns`, both written in Task 4.
- Produces: no new API — this task proves the two functions Task 4 introduced.

Task 4 wrote all three functions because they share every helper; splitting the file would have meant writing `_links`, `_condition` and `_note` twice. This task tests the two it did not cover.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_joins.py`:

```python
from pysqlsuggestions.engine.joins import condition_columns, join_conditions


def test_condition_pairs_the_latest_relation_with_an_earlier_one() -> None:
    """`JOIN auth_user u ON <caret>` — one accept finishes the join."""
    found = join_conditions(scope_of(('reports_report', 'r'), ('auth_user', 'u')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['r.author_id = u.id']
    assert found[0].kind is Kind.JOIN
    assert found[0].label == 'author_id'
    assert found[0].note == 'fk: auth_user.id'


def test_condition_reads_earlier_relation_first() -> None:
    """Text order follows the statement, not the constraint's direction."""
    found = join_conditions(scope_of(('auth_user', 'u'), ('reports_report', 'r')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['u.id = r.author_id']
    assert found[0].label == 'author_id'


def test_condition_needs_two_relations() -> None:
    """A single relation has nothing to be joined to."""
    assert join_conditions(scope_of(('reports_report', 'r')), [AUTHOR], POSTGRES) == []


def test_condition_ignores_an_unrelated_pair() -> None:
    """No constraint connects these two, so the position keeps its columns and nothing else."""
    found = join_conditions(scope_of(('reports_report', 'r'), ('billing_invoice', 'b')), [AUTHOR], POSTGRES)
    assert found == []


def test_qualified_left_side_degrades_to_annotated_columns() -> None:
    """`ON r.<caret>` has committed the left side, so the whole condition is no longer expressible."""
    relation = Relation(alias='r', path=('reports_report',), source='table')
    found = condition_columns(relation, [AUTHOR], POSTGRES)
    assert [c.text for c in found] == ['author_id']
    assert found[0].note == 'fk: auth_user.id'
    assert found[0].snippet == 'author_id'
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/test_joins.py -v`
Expected: PASS if Task 4 was implemented correctly. **If any fail, fix `joins.py` — do not weaken the test.** These are the behaviours the spec committed to.

- [ ] **Step 3: Commit**

```bash
git add tests/test_joins.py
git commit -m "test: the ON position offers a condition, and degrades when the left side is written"
```

---

## Task 6: Ranking

**Files:**
- Modify: `src/pysqlsuggestions/engine/rank.py:29-39` (constants), `:60-87` (the loop), `:208-218` (`_kind_bonus`)
- Test: `tests/test_joins_resolve.py`

**Interfaces:**
- Consumes: `Kind.JOIN`, `Candidate.note`.
- Produces: `Suggestion.note` populated from the candidate; a `Kind.JOIN` candidate outranks the plain names it sits among.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_joins_resolve.py`:

```python
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.types import Candidate, Kind, Request


def test_a_join_proposal_outranks_the_tables_it_sits_among() -> None:
    """At `JOIN <caret>` the proposal is a better answer than the bare name it contains."""
    request = Request(kinds=(Kind.TABLE, Kind.SCHEMA, Kind.KEYWORD), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(text='auth_user', kind=Kind.TABLE),
        Candidate(
            text='auth_user u ON r.author_id = u.id',
            kind=Kind.JOIN,
            snippet='auth_user u ON r.author_id = u.id',
            label='auth_user',
            note='fk: auth_user.id',
        ),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'auth_user u ON r.author_id = u.id'
    assert found[0].note == 'fk: auth_user.id'


def test_a_join_proposal_scores_as_a_column_where_columns_belong() -> None:
    """At `ON <caret>` there is no TABLE kind to borrow, so it takes COLUMN's place."""
    request = Request(kinds=(Kind.COLUMN, Kind.FUNCTION), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(text='id', kind=Kind.COLUMN),
        Candidate(text='r.author_id = u.id', kind=Kind.JOIN, snippet='r.author_id = u.id', label='author_id'),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'r.author_id = u.id'


def test_forward_outranks_reverse() -> None:
    """Many-to-one is more often wanted and cannot multiply the result set."""
    request = Request(kinds=(Kind.TABLE,), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(text='b ON u.id = b.user_id', kind=Kind.JOIN, snippet='b ON u.id = b.user_id', label='b', position=1),
        Candidate(text='a ON r.a_id = a.id', kind=Kind.JOIN, snippet='a ON r.a_id = a.id', label='a', position=0),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'a ON r.a_id = a.id'


def test_typed_prefix_still_decides() -> None:
    """Match strength stays dominant, so a proposal for another table falls away."""
    request = Request(kinds=(Kind.TABLE,), prefix='auth', replace_span=(0, 4))
    candidates = [
        Candidate(text='orders o ON r.o_id = o.id', kind=Kind.JOIN, snippet='orders o ON r.o_id = o.id', label='orders'),
        Candidate(text='auth_user', kind=Kind.TABLE),
    ]
    found = rank(candidates, request, POSTGRES)
    assert [s.text for s in found] == ['auth_user']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_joins_resolve.py -k rank -v`
Expected: FAIL — the plain table sorts first, and `Suggestion` has no `note` set.

- [ ] **Step 3: Add the constant**

In `rank.py`, after `_LOCAL_BONUS`'s docstring (line 36):

```python
_JOIN_BONUS = 12.0
"""
A whole join clause beats the bare relation name it contains.

Large enough to clear two kind steps, so adding a kind to a clause's list cannot
silently demote it; smaller than `_LOCAL_BONUS`, because a CTE the user wrote
themselves is still the better answer at the same position.
"""
```

- [ ] **Step 4: Apply it in the loop and carry the note**

In `rank()`, after the `_LOCAL_BONUS` line (line 67-68):

```python
        if candidate.kind is Kind.JOIN:
            score += _JOIN_BONUS
```

And in the `Suggestion(...)` construction, after `relation=candidate.relation,`:

```python
                    note=candidate.note,
```

- [ ] **Step 5: Teach `_kind_bonus` about the new kind**

Replace the body of `_kind_bonus` (lines 216-218):

```python
    index = kind_rank.get(kind)
    if index is None and kind is Kind.CTE:
        index = kind_rank.get(Kind.TABLE)
    if index is None and kind is Kind.JOIN:
        # A join proposal occupies the position of whatever it completes: a
        # relation where relations go, a condition where columns go.
        index = kind_rank.get(Kind.TABLE, kind_rank.get(Kind.COLUMN))
    return 0.0 if index is None else (total - index) * _KIND_STEP
```

Extend the docstring's second paragraph to name the JOIN case alongside CTE.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_joins_resolve.py -v && uv run pytest -m 'not integration' -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/engine/rank.py tests/test_joins_resolve.py
git commit -m "feat: a join proposal ranks as the thing it completes, and leads it"
```

---

## Task 7: Wiring — the feature working end to end, offline

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py:177-205` (`_qualified`), `:206-241` (`_unqualified`)
- Test: `tests/test_joins_resolve.py`

**Interfaces:**
- Consumes: `relation_joins`, `join_conditions`, `condition_columns`, `_Reader.foreign_keys`.
- Produces: no new public API. `complete()` now returns join proposals at both positions.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_joins_resolve.py`:

```python
from pysqlsuggestions.api import complete
from tests.corpus.cases import split_caret

SNAPSHOT = {
    ('public', 'reports_report'): [('id', 'bigint'), ('title', 'varchar(100)'), ('author_id', 'bigint')],
    ('public', 'auth_user'): [('id', 'bigint'), ('username', 'varchar(150)'), ('email', 'varchar(254)')],
}
JOINED = MemoryCatalog(SNAPSHOT, foreign_keys=[EDGE])
BARE = MemoryCatalog(SNAPSHOT)


def suggest(marked: str, catalog: MemoryCatalog) -> list[str]:
    """Suggestion texts for ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return [s.text for s in complete(sql, caret, POSTGRES, catalog)]


def test_join_position_leads_with_the_whole_clause() -> None:
    """The relation, its alias and the condition, in one accept.

    `au` rather than `u`: the generator offers the initials of the underscore-separated
    words first, and `r` is already taken by the relation in the FROM.
    """
    found = suggest('SELECT * FROM reports_report r JOIN ⌶', JOINED)
    assert found[0] == 'auth_user au ON r.author_id = au.id'
    assert 'auth_user' in found


def test_on_position_leads_with_the_whole_condition() -> None:
    """The plain columns stay underneath for a condition the constraints do not describe."""
    found = suggest('SELECT * FROM reports_report r JOIN auth_user u ON ⌶', JOINED)
    assert found[0] == 'r.author_id = u.id'
    assert 'u.email' in found


def test_qualified_on_position_lifts_the_fk_column() -> None:
    """`ON r.⌶` has committed the left side, so the column leads instead."""
    found = suggest('SELECT * FROM reports_report r JOIN auth_user u ON r.⌶', JOINED)
    assert found[0] == 'author_id'
    assert found.count('author_id') == 1


def test_from_position_is_untouched() -> None:
    """Nothing is guessed at a user who has not typed JOIN."""
    found = suggest('SELECT * FROM reports_report r ⌶', JOINED)
    assert found[0] == 'JOIN'
    assert not any('ON' in text and '=' in text for text in found)


def test_without_constraints_nothing_changes() -> None:
    """The same catalog minus its edges behaves exactly as it did before this feature."""
    assert suggest('SELECT * FROM reports_report r JOIN ⌶', BARE)[0] == 'reports_report'
    assert suggest('SELECT * FROM reports_report r JOIN auth_user u ON ⌶', BARE)[0].startswith('r.')


def test_the_proposal_is_accepted_as_one_edit() -> None:
    """`plan_insertion` needs no change: one replacement over the span, caret at the end."""
    from pysqlsuggestions.api import plan_insertion

    sql, caret = split_caret('SELECT * FROM reports_report r JOIN ⌶')
    best = complete(sql, caret, POSTGRES, JOINED)[0]
    plan = plan_insertion(sql, caret, best)
    assert len(plan.edits) == 1
    written = sql[: plan.edits[0].span[0]] + plan.edits[0].text + sql[plan.edits[0].span[1] :]
    assert written == 'SELECT * FROM reports_report r JOIN auth_user au ON r.author_id = au.id'


def test_a_caret_that_cannot_join_costs_no_catalog_read() -> None:
    """The constraints are fetched only where they can be used, so ordinary typing pays nothing."""
    catalog = MemoryCatalog(SNAPSHOT, foreign_keys=[EDGE])
    sql, caret = split_caret('SELECT * FROM reports_report r WHERE r.⌶')
    complete(sql, caret, POSTGRES, catalog)
    assert not [call for call in catalog.calls if call[0] == 'foreign_keys']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_joins_resolve.py -k "position or constraints or accepted" -v`
Expected: FAIL — `assert 'reports_report' == 'auth_user u ON r.author_id = u.id'`

- [ ] **Step 3: Add the edge fetch helper to `resolve.py`**

Add the import beside the other engine imports (line 19):

```python
from pysqlsuggestions.engine import datatypes, joins
```

Add after `_catalog_columns` (after line 98):

```python
def _edges(scope: Scope | None, reader: _Reader) -> Sequence[ForeignKey]:
    """
    Constraints for every schema the statement names, and the default namespace.

    Fetched only at the two positions that can use them, so a statement whose caret
    never reaches a JOIN or an ON pays nothing for this.
    """
    if scope is None:
        return ()
    wanted = {_split_path(r.path)[0] for r in scope.relations if r.projection is None and r.path}
    found: dict[tuple[str, ...], ForeignKey] = {}
    for schema in sorted(wanted, key=lambda name: (name is not None, name or '')):
        for edge in reader.foreign_keys(schema):
            found[edge.schema, edge.table, *edge.columns] = edge
    return list(found.values())
```

- [ ] **Step 4: Wire the unqualified positions**

In `_unqualified`, immediately after `scope = request.scope` (line 209):

```python
    if request.clause == 'JOIN' and Kind.TABLE in request.kinds:
        candidates += joins.relation_joins(scope, _edges(scope, reader), dialect)
    elif request.clause == 'ON' and Kind.COLUMN in request.kinds:
        candidates += joins.join_conditions(scope, _edges(scope, reader), dialect)
```

- [ ] **Step 5: Wire the qualified `ON` position**

`_qualified` has several return paths; this belongs on the first one only — the one that resolved a relation label to its columns (lines 182-185). Replace:

```python
    if scope is not None:
        relation = _find_relation(head, scope)
        if relation is not None:
            return _columns_of(relation, reader, seen=set())
```

with:

```python
    if scope is not None:
        relation = _find_relation(head, scope)
        if relation is not None:
            columns = _columns_of(relation, reader, seen=set())
            if request.clause != 'ON':
                return columns
            # `ON r.<caret>` has committed the left side, so a whole condition is
            # no longer expressible: lift and annotate that relation's FK columns
            # instead. The name filter is what stops the column appearing twice —
            # rank dedups on (kind, text), and these two candidates differ in kind.
            lifted = joins.condition_columns(relation, _edges(scope, reader), dialect)
            names = {candidate.text for candidate in lifted}
            return lifted + [candidate for candidate in columns if candidate.text not in names]
```

The other paths — a qualifier naming a schema, or a relation not in scope — keep today's behaviour untouched.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_joins_resolve.py -v && uv run pytest -m 'not integration' -q`
Expected: PASS, with every pre-existing test still green.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/resolve.py tests/test_joins_resolve.py
git commit -m "feat: the two join positions answer from the catalog's constraints"
```

---

## Task 8: Postgres introspection

**Files:**
- Modify: `src/pysqlsuggestions/dialects/base.py:289-305` (`CatalogQueries`)
- Modify: `src/pysqlsuggestions/dialects/postgres.py:22` onwards (`QUERIES`)
- Modify: `src/pysqlsuggestions/catalogs/dbapi.py` (after `common_values`)
- Test: `tests/test_dialect_records.py`

**Interfaces:**
- Consumes: `ForeignKey`.
- Produces: `CatalogQueries.foreign_keys: Query | None = None`; `POSTGRES.catalog_queries.foreign_keys` populated; `DbapiCatalog.foreign_keys(schema)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dialect_records.py`:

```python
def test_only_postgres_ships_a_foreign_key_query() -> None:
    """ClickHouse and Trino keep no constraints, so the slot stays empty and the capability is inert."""
    assert POSTGRES.catalog_queries.foreign_keys is not None
    assert CLICKHOUSE.catalog_queries.foreign_keys is None
    assert TRINO.catalog_queries.foreign_keys is None
    assert ANSI.catalog_queries.foreign_keys is None


def test_the_foreign_key_row_mapper_builds_an_edge() -> None:
    """Arrays in, ForeignKey out. The mapper is the only place a driver's shape is visible."""
    query = POSTGRES.catalog_queries.foreign_keys
    assert query is not None
    edge = query.row(('public', 'reports_report', ['author_id'], 'public', 'auth_user', ['id']))
    assert edge == ForeignKey(
        schema='public',
        table='reports_report',
        columns=('author_id',),
        ref_schema='public',
        ref_table='auth_user',
        ref_columns=('id',),
    )
```

Add `ForeignKey` to that file's imports, and whichever of `ANSI`/`CLICKHOUSE`/`TRINO` it does not already import.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dialect_records.py -k foreign -v`
Expected: FAIL — `AttributeError: 'CatalogQueries' object has no attribute 'foreign_keys'`

- [ ] **Step 3: Add the slot**

In `dialects/base.py`, in `CatalogQueries` after `column_search`:

```python
    foreign_keys: Query | None = None
    """
    Declared relationships whose referencing side is in one schema. `$1` is the schema.

    Absent means that backend keeps no constraints, which is the truth for
    ClickHouse and Trino — and the reason join proposals are Postgres-only. A
    dialect must not fill this with a name-matching heuristic.
    """
```

- [ ] **Step 4: Add the Postgres query**

In `dialects/postgres.py`, add `ForeignKey` to the imports from `pysqlsuggestions.types`, then add to `QUERIES` after `column_search`:

```python
    foreign_keys=Query(
        sql="""
            SELECT n.nspname,
                   c.relname,
                   (SELECT array_agg(a.attname ORDER BY k.ord)
                      FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum),
                   rn.nspname,
                   rc.relname,
                   (SELECT array_agg(a.attname ORDER BY k.ord)
                      FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum)
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_class rc ON rc.oid = con.confrelid
            JOIN pg_namespace rn ON rn.oid = rc.relnamespace
            WHERE con.contype = 'f'
              AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            ORDER BY n.nspname, c.relname, con.conname
        """,
        # WITH ORDINALITY rather than a bare unnest: conkey and confkey correspond
        # position by position, and that correspondence is the whole content of a
        # composite key. array_agg over a plain unnest may reorder either side.
        row=lambda row: ForeignKey(
            schema=str(row[0]),
            table=str(row[1]),
            columns=tuple(str(name) for name in row[2]),
            ref_schema=str(row[3]),
            ref_table=str(row[4]),
            ref_columns=tuple(str(name) for name in row[5]),
        ),
    ),
```

- [ ] **Step 5: Add the DbapiCatalog method**

In `catalogs/dbapi.py`, add `ForeignKey` to the type imports and the method after `common_values`:

```python
    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """Declared relationships, when the dialect ships the query. Empty when it does not."""
        rows = self._rows(self._dialect.catalog_queries.foreign_keys, schema or '')
        return [row for row in rows if isinstance(row, ForeignKey)]
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_dialect_records.py -v && uv run pytest -m 'not integration' -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/dialects/base.py src/pysqlsuggestions/dialects/postgres.py src/pysqlsuggestions/catalogs/dbapi.py tests/test_dialect_records.py
git commit -m "feat: pg_constraint answers what references what"
```

---

## Task 9: The container proves the SQL

**Files:**
- Modify: `docker/postgres/01-schema.sql:155-160` (after `reports_queryfilter_databases`)
- Modify: `docker/README.md:18-25`
- Test: `tests/integration/test_backends.py`

**Interfaces:**
- Consumes: everything above.
- Produces: a composite foreign key in the fixture; integration coverage of the query text.

- [ ] **Step 1: Add the composite foreign key to the fixture**

`reports_queryfilter_databases` already carries `UNIQUE (queryfilter_id, database_id)`, which is a legal composite target. Add after it:

```sql
CREATE TABLE reports_queryfilter_usage (
    id              bigserial PRIMARY KEY,
    queryfilter_id  bigint NOT NULL,
    database_id     bigint NOT NULL,
    used_at         timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (queryfilter_id, database_id)
        REFERENCES reports_queryfilter_databases (queryfilter_id, database_id) ON DELETE CASCADE
);
```

In `docker/README.md`, extend the foreign-key sentence around line 24 to say the fixture also carries a composite key, two self-references and a table referencing `auth_user` twice — the shapes join completion has to get right.

- [ ] **Step 2: Write the failing test**

Append to `tests/integration/test_backends.py`:

```python
def test_postgres_reads_declared_foreign_keys(postgres_catalog: DbapiCatalog) -> None:
    """The query text itself — only a real server can say whether it runs."""
    edges = {(e.table, e.columns): (e.ref_table, e.ref_columns) for e in postgres_catalog.foreign_keys('public')}
    assert edges[('reports_report', ('author_id',))] == ('auth_user', ('id',))
    assert edges[('reports_report', ('database_id',))] == ('reports_database', ('id',))


def test_postgres_reads_a_composite_key_in_order(postgres_catalog: DbapiCatalog) -> None:
    """WITH ORDINALITY is what keeps the two sides aligned; a reordered array would pass every unit test."""
    edges = {(e.table, e.columns): (e.ref_table, e.ref_columns) for e in postgres_catalog.foreign_keys('public')}
    key = ('reports_queryfilter_usage', ('queryfilter_id', 'database_id'))
    assert edges[key] == ('reports_queryfilter_databases', ('queryfilter_id', 'database_id'))


def test_postgres_joins_a_real_schema(postgres_catalog: DbapiCatalog) -> None:
    """End to end against the server: the clause the engine writes is the one the schema implies."""
    found = suggest('SELECT * FROM reports_report r JOIN ⌶', POSTGRES, postgres_catalog)
    assert 'auth_user au ON r.author_id = au.id' in found[:5]


def test_clickhouse_and_trino_keep_their_positions(
    clickhouse_catalog: DbapiCatalog,
    trino_catalog: DbapiCatalog,
) -> None:
    """Neither backend declares constraints, so neither offers a proposal."""
    assert list(clickhouse_catalog.foreign_keys('analytics')) == []
    assert list(trino_catalog.foreign_keys('public')) == []
```

- [ ] **Step 3: Rebuild the container and run**

```bash
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d --wait
uv run pytest tests/integration/test_backends.py -v
```

Expected: PASS. `down -v` is required — the schema only runs on an empty volume.

- [ ] **Step 4: Check nothing else in the integration suite assumed the old table list**

Run: `uv run pytest tests/integration -v`
Expected: PASS. `tests/integration/test_backends.py:76` and `:342` build dicts over `tables('public')` and assert on named keys, so a new relation is harmless — but confirm rather than assume.

- [ ] **Step 5: Commit**

```bash
git add docker/postgres/01-schema.sql docker/README.md tests/integration/test_backends.py
git commit -m "test: a real server validates the constraint query, composite key included"
```

---

## Task 10: Conformance and the acceptance harnesses

**Files:**
- Modify: `src/pysqlsuggestions/testing/__init__.py`
- Modify: `tests/test_writable.py`
- Modify: `tests/integration/test_acceptance.py`

**Interfaces:**
- Consumes: everything above.
- Produces: a conformance case for the absent capability; both harnesses walking a statement whose joins the engine now writes.

- [ ] **Step 1: Add the conformance case**

`DialectConformance.cases` returns `Case(name, sql, expect, forbid)` values that assert on suggestion *texts*, so it cannot express "no candidate of kind JOIN". What it can express — and what is worth guarding — is that the position still offers relation names now that new code runs there. Add to the `cases` list in `src/pysqlsuggestions/testing/__init__.py`, beside `'both sides of a join are in scope'`:

```python
            Case(
                name='a join position offers relations',
                sql=f'SELECT * FROM {users} AS u JOIN ',
                expect=('orders',),
            ),
```

- [ ] **Step 2: Guard the stronger claim where kinds are visible**

The "nothing is invented" check belongs in `tests/test_conformance.py`, which can see kinds. Add there:

```python
@pytest.mark.parametrize('dialect', [ANSI, POSTGRES, CLICKHOUSE, TRINO], ids=lambda d: d.name)
def test_no_join_is_proposed_without_a_declared_constraint(dialect: Dialect) -> None:
    """A proposal comes from a constraint the backend declares, or it does not come."""
    catalog = DialectConformance.catalog(dialect)
    sql = f'SELECT * FROM {DialectConformance.reference(dialect, "users")} AS u JOIN '
    assert not [s for s in complete(sql, len(sql), dialect, catalog) if s.kind is Kind.JOIN]
```

Match the file's existing import list and parametrisation style — read it before adding.

- [ ] **Step 3: Extend the offline writability harness**

In `tests/test_writable.py`, add the two foreign keys the `GOLDEN` statement implies to the `CATALOG` fixture:

```python
CATALOG = MemoryCatalog(
    {...},  # unchanged
    functions=[Function(schema='pg_catalog', name='count', args='*', result='bigint')],
    foreign_keys=[
        ForeignKey(
            schema='public',
            table='orders',
            columns=('user_id',),
            ref_schema='public',
            ref_table='auth_user',
            ref_columns=('id',),
        ),
    ],
)
```

The `GOLDEN` statement already contains `JOIN orders AS o ON o.user_id = u.id`, so the harness now walks a statement whose join the engine can write itself. Add `ForeignKey` to the imports.

- [ ] **Step 4: Run the offline harnesses**

Run: `uv run pytest tests/test_writable.py tests/test_conformance.py -v`
Expected: PASS. That harness measures *recall* — whether anything offered reaches the next text — so extra candidates can only help it. A regression means a proposal displaced something from a truncated result; investigate rather than deleting the assertion. Note the golden statement writes `orders AS o` while a proposal writes `orders o`; the plain `orders` candidate is what carries that caret, and it is untouched.

- [ ] **Step 5: Judge the synthesized SQL against a real parser**

`tests/integration/test_acceptance.py` walks every caret in *complete* statements and `EXPLAIN`s the result, so a multi-token insert collides with whatever follows the caret by construction — `JOIN auth_user au ON r.author_id = au.id` spliced before an existing `auth_user AS u ON …` is a syntax error no matter how correct the proposal is. That is exactly why `Kind.SNIPPET` is already in `UNJUDGEABLE`, and `Kind.JOIN` belongs there for the same reason:

```python
UNJUDGEABLE = frozenset({Kind.FUNCTION, Kind.SNIPPET, Kind.JOIN})
```

Extend that constant's docstring with a third paragraph: a join proposal is legal SQL on its own and illegal spliced into the middle of a finished statement, which is a property of this harness's method rather than of the suggestion.

The claim in spec §9.2 — that this harness is where a synthesized join gets judged — is therefore wrong as written, and the honest replacement is a dedicated test where nothing follows the caret. Add to the same file:

```python
JOIN_PREFIXES = (
    'SELECT * FROM reports_report r JOIN ',
    'SELECT * FROM auth_user u JOIN ',
    'SELECT * FROM reports_report r JOIN auth_user u ON ',
)


def test_a_join_proposal_is_sql_postgres_takes(parser: Any, postgres_catalog: DbapiCatalog) -> None:
    """
    The synthesized clause, judged by a real parser with nothing after it to collide with.

    This is the only test that asks Postgres about multi-token text the engine
    wrote rather than copied out of a catalog.
    """
    for prefix in JOIN_PREFIXES:
        proposals = [s for s in complete(prefix, len(prefix), POSTGRES, postgres_catalog) if s.kind is Kind.JOIN]
        assert proposals, f'no join proposal at {prefix!r}'
        for suggestion in proposals[:3]:
            written = apply_suggestion(prefix, len(prefix), suggestion).text
            assert misplaced(parser, written) == '', written
```

`apply_suggestion` returns an `Insertion`; check its shape in `api.py` and take the spliced text the way the existing test in this file does.

- [ ] **Step 6: Run it**

Run: `uv run pytest tests/integration/test_acceptance.py -v`
Expected: PASS. A failure here is a real defect in `joins.py` — the text is being judged with nothing after it, so there is nothing to blame but the proposal.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/testing/__init__.py tests/test_conformance.py tests/test_writable.py tests/integration/test_acceptance.py
git commit -m "test: an accepted join proposal is still a statement a server takes"
```

---

## Task 11: Demo and documentation

**Files:**
- Modify: `demo/schema.py`, `demo/payload.py:95`, `demo/static/index.html:323`
- Modify: `README.md`
- Test: `tests/test_demo_browser.py`

**Interfaces:**
- Consumes: `MemoryCatalog(foreign_keys=…)`, `Suggestion.note`.
- Produces: the feature visible in the published browser demo.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_demo_browser.py`:

```python
def test_the_demo_schema_declares_its_joins() -> None:
    """The flight-booking schema has obvious relationships; declaring them is what makes them work."""
    catalog = schema.postgres()
    edges = {(e.table, e.columns[0]): e.ref_table for e in catalog.foreign_keys(None)}
    assert edges[('flight', 'airline_id')] == 'airline'
    assert edges[('booking', 'flight_id')] == 'flight'


def test_the_demo_offers_a_join_proposal() -> None:
    """What a visitor to the published page sees when they type JOIN."""
    sql = 'SELECT * FROM booking b JOIN '
    found = [s.text for s in complete(sql, len(sql), POSTGRES, schema.postgres())]
    assert found[0].startswith('flight f ON b.flight_id = f.id')
```

Match the imports and fixture accessors the file already uses — read it first.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_demo_browser.py -k join -v`
Expected: FAIL — `KeyError: ('flight', 'airline_id')`

- [ ] **Step 3: Declare the demo's edges**

In `demo/schema.py`, add a `POSTGRES_FOREIGN_KEYS` tuple above `postgres()` and pass it as `foreign_keys=POSTGRES_FOREIGN_KEYS`. These are the edges the existing columns already imply — invent no columns:

| referencing | → referenced |
| --- | --- |
| `public.aircraft.airline_id` | `public.airline.id` |
| `public.flight.airline_id` | `public.airline.id` |
| `public.flight.aircraft_id` | `public.aircraft.id` |
| `public.flight.origin` | `public.airport.code` |
| `public.flight.destination` | `public.airport.code` |
| `public.booking.passenger_id` | `public.passenger.id` |
| `public.booking.flight_id` | `public.flight.id` |
| `public.baggage.booking_id` | `public.booking.id` |
| `revenue.invoice.airline_id` | `public.airline.id` |
| `revenue.refund.booking_id` | `public.booking.id` |

Two of these earn their place beyond making the demo work. `flight.origin` and `flight.destination` both reference `airport.code`, so the published page shows the two-edges-to-one-target case — two proposals with different aliases, which is the behaviour a single guess would have got wrong half the time. The two `revenue` edges cross a schema boundary, so the page also shows a qualified target.

ClickHouse and Trino get none: the demo must not show a capability the real backends lack. Extend the module docstring to say so, in the sentence that already explains what the schema is shaped to exercise.

- [ ] **Step 4: Render the note**

In `demo/payload.py`, add `'note': suggestion.note,` beside `'detail'` in the suggestion dict (line 95).

In `demo/static/index.html`, render it in `paintPopup` (line 323) after the `det` span:

```html
      <span class="note">${esc(s.note || '')}</span>
```

Add a `.note` rule to the stylesheet in the same file, styled quieter than `.det` — read the existing CSS and match it.

- [ ] **Step 5: Run the demo tests and rebuild the page**

```bash
uv run pytest tests/test_demo_browser.py -v
uv build --wheel && uv run python scripts/build_pages.py
```

Expected: tests PASS, page builds. Optionally serve it with `python -m http.server -d site 8001` and type `SELECT * FROM booking b JOIN ` to see the proposal.

- [ ] **Step 6: Document it in the README**

Add a section after "Qualified columns", in the shape of the existing ones: what the feature does, the two positions, and the honest per-backend line — Postgres declares constraints, ClickHouse and Trino do not, so they offer relation names there as before. Say that inference from column names was rejected rather than forgotten, and why. Update the Status paragraph at `README.md:12-15` to move FK-derived joins out of "still to come".

- [ ] **Step 7: Full check and commit**

Run: `./scripts/check.sh`
Expected: all green.

```bash
git add demo/ README.md tests/test_demo_browser.py
git commit -m "feat: the browser demo joins, and the README says who can"
```

---

## Verification

After Task 11:

```bash
./scripts/check.sh                                    # ruff, mypy, pytest
docker compose -f docker/docker-compose.yml up -d --wait
uv run pytest tests/integration -v                    # all three backends
```

The feature is done when: `SELECT * FROM reports_report r JOIN ⌶` leads with `auth_user u ON r.author_id = u.id` against docker Postgres; the same caret against ClickHouse and Trino offers relation names exactly as it does on `main`; and every pre-existing test passes without modification.
