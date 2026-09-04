# Generic Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pysqlsuggestions.ports.Cache` implementable by any store, and ship a redis adapter.

**Architecture:** The port splits into `ObjectCache` (keeps Python objects; in practice a dict) and `ByteCache` (keeps bytes; anything crossing a process boundary), told apart at runtime by their distinct method names. The library owns the key — one opaque string carrying a fingerprint of the cached types' shapes — and owns the codec, applying it only on the byte path so an in-process dict pays no serialisation. `resolve.py` gains the failure policy: a transport error latches the cache off for the rest of the request, a decode failure is a miss.

**Tech Stack:** Python 3.10+, no runtime dependencies in `src/`. `redis>=3.0` and `fakeredis` are dev/extra only. pytest, mypy strict, ruff with `D`.

**Spec:** `docs/superpowers/specs/2026-09-04-generic-cache-design.md` — read it before starting. This plan argues from it; where the two disagree, the spec is right and the plan has a bug.

## Global Constraints

- **Zero runtime dependencies.** `import pysqlsuggestions` must pull in no driver and no redis. `tests/test_purity.py` fails the build otherwise.
- **`engine/` may not import `ports` or `resolve`.** Nothing in this plan touches `engine/`.
- **Style:** single quotes, 120 columns, ruff `D` (every function needs a docstring), mypy `strict` over `src`, `tests`, `lsp`.
- **Prose is the point.** Docstrings and comments record *why* a shape was chosen and which alternative was rejected. A change that adds behaviour without saying what it refused is out of keeping with this codebase. Reuse the spec's reasoning verbatim where it fits.
- **Commits** are `feat:`/`fix:`/`test:`/`docs:`/`refactor:`/`chore:` with a lowercase prose summary and a body explaining the decision. End every commit message with:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  ```
- **The gate** is `./scripts/check.sh` (ruff format --check, ruff check, mypy, pytest). It must pass at the end of every task. Fast loop: `uv run pytest -m 'not integration'`.
- **Version floor for the extra:** `redis>=3.0`, a floor and not a pin.
- **Key format version:** `'1'`. **TTL** is integer seconds. **`RedisCache` default TTL** is `300`. **`MemoryCache` default TTL** is `None`.

---

## File Structure

| Path | Responsibility | Task |
| --- | --- | --- |
| `src/pysqlsuggestions/caches/__init__.py` | the package's exports | 1 |
| `src/pysqlsuggestions/caches/keys.py` | `cache_key`, `KEY_VERSION`, `FINGERPRINT`, `CACHED_TYPES` | 1 |
| `src/pysqlsuggestions/caches/codec.py` | `encode`, `decode`, the tag allowlist | 2 |
| `src/pysqlsuggestions/caches/memory.py` | `MemoryCache` | 3 |
| `src/pysqlsuggestions/caches/redis.py` | `RedisCache`, `RedisClient` | 10 |
| `src/pysqlsuggestions/testing/caches.py` | `InMemoryByteCache`, `CacheConformance` | 6, 9 |
| `src/pysqlsuggestions/ports.py` | `ObjectCache`, `ByteCache`, `Cache` | 7 |
| `src/pysqlsuggestions/resolve.py` | key encoding, discipline dispatch, failure policy | 4, 5, 7 |
| `src/pysqlsuggestions/api.py` | the `TypeError` at the door | 7 |

Tests: `tests/test_cache_keys.py` (1, 4), `tests/test_cache_codec.py` (2), `tests/test_cache_memory.py` (3), `tests/test_cache_failure.py` (5, 7), `tests/test_cache_bytes.py` (6, 8), `tests/test_cache_conformance.py` (9), `tests/test_cache_redis.py` (10), `tests/integration/test_redis_cache.py` (11).

**Task order is green-at-every-commit.** Tasks 1–6 add code nothing calls yet, so the suite stays passing. Task 7 is the breaking change and is one commit because a protocol cannot be half-replaced.

---

### Task 1: The key encoder and its fingerprint

**Files:**
- Create: `src/pysqlsuggestions/caches/__init__.py`
- Create: `src/pysqlsuggestions/caches/keys.py`
- Test: `tests/test_cache_keys.py`

**Interfaces:**
- Consumes: `pysqlsuggestions.types.{Table, Column, Function, ColumnValue, ForeignKey}`
- Produces: `cache_key(identity: str | None, dialect: str, kind: Kind, *parts: str | None) -> str`; `KEY_VERSION: str`; `FINGERPRINT: str`; `CACHED_TYPES: tuple[Any, ...]`; `Kind = Literal['schemas', 'tables', 'columns', 'functions', 'values', 'fk']`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_keys.py`:

```python
"""The key is one opaque string, and distinct reads must never collide."""

from __future__ import annotations

from pysqlsuggestions.caches import FINGERPRINT, KEY_VERSION, cache_key


def test_none_and_the_empty_string_are_different_keys() -> None:
    """
    The collision that cost a session its relation list, made impossible.

    `SELECT "".` is a quoted empty identifier, so `tables('')` — the relations in
    a namespace actually named that, which is none — and `tables(None)` —
    everywhere the search path reaches — are both reachable from ordinary text.
    Folding them together cached the empty answer over the real one, silently.
    """
    assert cache_key('analyst', 'postgres', 'tables', None) != cache_key('analyst', 'postgres', 'tables', '')


def test_a_relation_read_never_collides_with_a_column_read() -> None:
    """`columns(None, '')` used to compute the very key `tables(None)` occupied."""
    assert cache_key('analyst', 'postgres', 'tables', None) != cache_key('analyst', 'postgres', 'columns', None, '')


def test_a_literal_dash_is_not_the_none_marker() -> None:
    """`-` means None, so a namespace actually named `-` has to encode as something else."""
    assert cache_key('analyst', 'postgres', 'tables', '-') != cache_key('analyst', 'postgres', 'tables', None)


def test_the_role_leads_the_key() -> None:
    """Two roles must not share a cached read; this is the oldest rule in the port."""
    assert cache_key('alice', 'postgres', 'tables', None) != cache_key('bob', 'postgres', 'tables', None)


def test_an_unnamed_role_is_a_role_and_not_a_wildcard() -> None:
    """identity=None gets its own line in the key rather than matching every entry."""
    assert cache_key(None, 'postgres', 'tables', None) != cache_key('alice', 'postgres', 'tables', None)


def test_the_dialect_separates_two_backends() -> None:
    """One process may serve several, and a Trino schema is not a Postgres one."""
    assert cache_key('analyst', 'trino', 'tables', None) != cache_key('analyst', 'postgres', 'tables', None)


def test_keys_are_ascii_and_carry_no_separator_from_the_data() -> None:
    """A name containing the separator, a percent or a non-ASCII character stays one component."""
    key = cache_key('analyst', 'postgres', 'columns', 'we:ird', 'na%meé')
    assert key.isascii()
    assert len(key.split(':')) == len(cache_key('analyst', 'postgres', 'columns', 'a', 'b').split(':'))


def test_every_key_is_stamped_with_the_version_and_the_shape() -> None:
    """A library upgrade that changes a cached type must not read the old entries."""
    assert cache_key('analyst', 'postgres', 'tables', None).startswith(f'{KEY_VERSION}:{FINGERPRINT}:')


def test_the_fingerprint_is_pinned() -> None:
    """
    A change to what is cached should be a deliberate line in a diff.

    Not a test of the hash function: it is a tripwire. When this fails, confirm
    the shape change was intended and write the new value in. Deriving the
    fingerprint rather than hand-bumping it is what stops someone forgetting;
    pinning it is what stops it changing unnoticed.
    """
    assert FINGERPRINT == 'PIN_ME'


def test_the_fingerprint_is_stable_across_processes() -> None:
    """`hashlib`, never the builtin `hash()`, which PYTHONHASHSEED randomises."""
    import os
    import subprocess
    import sys

    code = 'from pysqlsuggestions.caches import FINGERPRINT; print(FINGERPRINT)'
    seen = {
        subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, 'PYTHONHASHSEED': seed},
        ).stdout.strip()
        for seed in ('0', '1', '12345')
    }
    assert seen == {FINGERPRINT}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cache_keys.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pysqlsuggestions.caches'`

- [ ] **Step 3: Write `keys.py`**

Create `src/pysqlsuggestions/caches/keys.py`:

```python
"""
The cache key: one opaque string, encoded here and nowhere else.

Callers supply their own cache, so the key shape has always been a public
contract. It used to be a tuple carrying NUL sentinels, which every external
adapter would have had to flatten for itself — and flattening it wrongly is
silent, as `resolve.py:_Reader.tables` records at length. Encoding it in the
library means the dict path and the redis path are byte-identical, and no
adapter is asked to re-solve a problem this codebase has already got wrong once.

`cache_key` is supported. The string it returns is not a format: `FINGERPRINT`
changes it whenever a cached type changes, which is the point. Anyone
constructing one by hand is broken by the next release and should be.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from hashlib import blake2b
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from pysqlsuggestions.types import Column, ColumnValue, ForeignKey, Function, Table

KEY_VERSION = '1'
"""The grammar's version. Bumped by hand when the *shape* of the key changes, not its contents."""

Kind = Literal['schemas', 'tables', 'columns', 'functions', 'values', 'fk']
"""
Which read a key belongs to.

A field of the grammar rather than a sentinel folded into the data, which is
what makes `tables(None)` against `columns(None, '')` structurally impossible
rather than merely avoided.
"""

CACHED_TYPES: tuple[Any, ...] = (Column, ColumnValue, ForeignKey, Function, Table)
"""
Every record type that can end up in a cache.

Typed `Any` rather than `type`: `dataclasses.fields` is annotated against a
`DataclassInstance` protocol these five satisfy structurally, and which a tuple
of concrete classes cannot be spelled as under mypy strict.
"""

_SAFE = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.')


def _quote(text: str) -> str:
    """
    One component, escaped so that nothing in the data can be a separator.

    Byte-wise over UTF-8 rather than character-wise, so the output is ASCII for
    any input — redis keys are bytes, and a key you cannot paste into
    `redis-cli` is a key nobody can debug. The `+` prefix is what distinguishes
    a present string from the `-` that means None: a literal `-` in the data
    escapes to `+%2D`, and `''` encodes as a bare `+`.
    """
    out = ['+']
    for byte in text.encode('utf-8'):
        character = chr(byte)
        out.append(character if character in _SAFE else f'%{byte:02X}')
    return ''.join(out)


def _component(part: str | None) -> str:
    """A component that may be absent. `None` is `-`, which `_quote` can never produce."""
    return '-' if part is None else _quote(part)


def _render_type(hint: object) -> str:
    """A type as a stable string, for the fingerprint. Deterministic across runs and processes."""
    origin = get_origin(hint)
    if origin is None:
        return getattr(hint, '__name__', str(hint))
    if origin is UnionType or origin is Union:
        return ' | '.join(_render_type(arg) for arg in get_args(hint))
    name = getattr(origin, '__name__', str(origin))
    return f'{name}[{", ".join(_render_type(arg) for arg in get_args(hint))}]'


def _enums(hint: object) -> set[type[Enum]]:
    """Every enum reachable from a field's type, including through a union."""
    if isinstance(hint, type) and issubclass(hint, Enum):
        return {hint}
    found: set[type[Enum]] = set()
    for arg in get_args(hint):
        found |= _enums(arg)
    return found


def _fingerprint() -> str:
    """
    Eight hex characters standing for the shape of everything cached.

    Field names and field type names, plus the *members* of every enum reachable
    from them. The enum half is not decoration: `Availability` gaining a member
    changes no field name and no field type name, so a fingerprint over field
    shapes alone would not see it — and the consequence would be a permanent,
    silent miss for as long as two versions ran, rather than a clean keyspace
    split.

    `hashlib`, never the builtin `hash()`, which `PYTHONHASHSEED` randomises:
    two processes sharing a store must agree on the key.
    """
    rendered: list[str] = []
    enums: set[type[Enum]] = set()
    for record in sorted(CACHED_TYPES, key=lambda found: str(found.__name__)):
        hints = get_type_hints(record)
        fields = dataclasses.fields(record)
        shape = ','.join(f'{field.name}:{_render_type(hints[field.name])}' for field in fields)
        rendered.append(f'{record.__name__}({shape})')
        for field in fields:
            enums |= _enums(hints[field.name])
    for enum in sorted(enums, key=lambda found: found.__name__):
        rendered.append(f'{enum.__name__}[{",".join(f"{m.name}={m.value}" for m in enum)}]')
    return blake2b('\n'.join(rendered).encode('utf-8'), digest_size=4).hexdigest()


FINGERPRINT = _fingerprint()
"""The shape of the cached types, computed once at import. See `_fingerprint`."""


def cache_key(identity: str | None, dialect: str, kind: Kind, *parts: str | None) -> str:
    """
    The key for one catalog read.

    `identity` leads, and is not optional, because privilege-aware reads evaluate
    against the connection's role: a cache keyed without it serves one user's
    readable set to another, which is silent and reads as a database privilege
    bug rather than a caching one.

    `parts` are the namespace path the read is scoped to, one component per
    level — a catalog for `schemas`, a schema for `tables`, `functions` and `fk`,
    a schema and a relation for `columns`, and those plus a column for `values`.
    """
    encoded = (KEY_VERSION, FINGERPRINT, _component(identity), _quote(dialect), kind, *(_component(p) for p in parts))
    return ':'.join(encoded)
```

Create `src/pysqlsuggestions/caches/__init__.py`:

```python
"""
Caches, and the key every one of them is addressed by.

Mirrors `catalogs/`: `memory` is the dependency-free implementation, and a
module named after a backend adapts that one and takes its imports lazily.
"""

from __future__ import annotations

from pysqlsuggestions.caches.keys import CACHED_TYPES, FINGERPRINT, KEY_VERSION, Kind, cache_key

__all__ = ['CACHED_TYPES', 'FINGERPRINT', 'KEY_VERSION', 'Kind', 'cache_key']
```

- [ ] **Step 4: Fill in the pinned fingerprint**

Run: `uv run python -c "from pysqlsuggestions.caches import FINGERPRINT; print(FINGERPRINT)"`

Replace `'PIN_ME'` in `test_the_fingerprint_is_pinned` with the value printed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cache_keys.py -q`
Expected: PASS, 10 tests.

- [ ] **Step 6: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS. If mypy objects to `dataclasses.fields(record)`, confirm `CACHED_TYPES` is annotated `tuple[Any, ...]` — that annotation is the fix and its docstring says why.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/caches tests/test_cache_keys.py
git commit -F - <<'MSG'
feat: the cache key becomes one string the library encodes

A tuple is a good dict key and is not a redis key, so every external adapter
would have had to flatten it — and flattening it wrongly is silent, which this
codebase already knows because `tables(None)` and `columns(None, '')` were once
the same key and one quoted empty identifier emptied a session's relation list.

The sentinel folded into the data becomes a field of the grammar, so that
collision is now unrepresentable rather than avoided. `None` is `-` and a
present string is `+` followed by a byte-wise percent escaping, so a namespace
actually named `-` encodes as `+%2D` and nothing in the data can be a separator.

Every key carries a fingerprint of the cached types' shapes, enum members
included: `Availability` gaining a member changes no field name and no field
type name, so a fingerprint over field shapes alone would leave two library
versions missing against each other permanently and silently instead of using
disjoint keyspaces. It is derived rather than hand-bumped so nobody has to
remember, and pinned by a test so it cannot change unnoticed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 2: The codec, and the two guards that keep it honest

**Files:**
- Create: `src/pysqlsuggestions/caches/codec.py`
- Modify: `src/pysqlsuggestions/caches/__init__.py`
- Test: `tests/test_cache_codec.py`

**Interfaces:**
- Consumes: `CACHED_TYPES` from Task 1.
- Produces: `codec.encode(value: Sequence[object]) -> bytes`; `codec.decode(data: bytes | str) -> tuple[object, ...]`; `codec.UnencodableValue(TypeError)`; `codec.TAGS: dict[str, Any]`

- [ ] **Step 1: Write the failing round-trip and guard tests**

Create `tests/test_cache_codec.py`:

```python
"""
The codec is written by hand; these are what keep it complete.

Two guards, catching different failures. Coverage says which types must be
handled, read off `_Reader` rather than off a list somebody maintains.
Completeness says no field was dropped — which is the one that matters, because
a missing type crashes on the first encode while a new field is silent, correct
uncached and empty cached.
"""

from __future__ import annotations

import ast
import dataclasses
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

import pytest

from pysqlsuggestions.caches import codec
from pysqlsuggestions.resolve import _Reader
from pysqlsuggestions.types import Availability, Column, ColumnValue, ForeignKey, Function, Table

ROOT = Path(__file__).resolve().parents[1]


def _cached_reads() -> set[str]:
    """
    The `_Reader` methods whose answers go through the cache.

    Found by walking the source for a `self._read` call rather than by keeping a
    list: which reads cache is then a fact about the code, and adding one that
    the codec cannot encode fails here instead of in production. Same mechanism
    `test_purity.py` uses to assert structural properties.
    """
    tree = ast.parse((ROOT / 'src' / 'pysqlsuggestions' / 'resolve.py').read_text(encoding='utf-8'))
    reader = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == '_Reader')
    found: set[str] = set()
    for node in reader.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            is_read = (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == '_read'
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == 'self'
            )
            if is_read:
                found.add(node.name)
    return found


def _element_type(method: str) -> Any:
    """The `X` in a cached read's `Sequence[X]` return annotation."""
    hints = get_type_hints(getattr(_Reader, method))
    return get_args(hints['return'])[0]


def test_the_codec_covers_exactly_what_is_cached() -> None:
    """
    Both directions: an uncoverable type fails, and a tag nothing caches fails as dead.

    Add a capability whose `_Reader` method calls `_read` and this names the type
    the codec has not learned — before it reaches a store, rather than after.
    """
    cached = {_element_type(method).__name__ for method in _cached_reads()}
    assert cached == set(codec.TAGS), f'codec tags {sorted(codec.TAGS)} do not match cached types {sorted(cached)}'


def _member(enum: type[Enum], default: object) -> Enum:
    """A member that is not the field's default, so a codec hardcoding the default is caught."""
    return next(found for found in enum if found is not default)


def _synthetic(hint: Any, default: object) -> Any:
    """
    A distinguishable value for one field, so a dropped field fails an equality check.

    This is reflection, which the codec itself deliberately does not use. The
    difference is what an unknown type does: here it is a red build telling
    somebody to teach the guard, at the moment they are adding the type.
    """
    origin = get_origin(hint)
    if origin is UnionType or origin is Union:
        return _synthetic(next(arg for arg in get_args(hint) if arg is not type(None)), default)
    if origin is tuple:
        return ('one', 'two')
    if isinstance(hint, type) and issubclass(hint, Enum):
        return _member(hint, default)
    if hint is str:
        return 'x'
    if hint is int:
        return 7
    if hint is float:
        return 0.25
    raise AssertionError(f'teach the guard about {hint!r}')


def _populated(record: Any) -> Any:
    """An instance with every field set, built from `dataclasses.fields` so new ones appear on their own."""
    hints = get_type_hints(record)
    values = {field.name: _synthetic(hints[field.name], field.default) for field in dataclasses.fields(record)}
    return record(**values)


@pytest.mark.parametrize('record', [Column, ColumnValue, ForeignKey, Function, Table], ids=lambda r: str(r.__name__))
def test_every_field_survives_a_round_trip(record: Any) -> None:
    """
    A field added to a cached type and not to the codec is silent otherwise.

    The fingerprint changes, so nothing stale is decoded — the damage is subtler:
    the field is correct uncached and empty cached, and a bug that depends on
    whether the cache was warm is the worst thing this design can produce.
    """
    original = _populated(record)
    assert codec.decode(codec.encode([original])) == (original,)


def test_a_list_of_names_round_trips() -> None:
    """`schemas` caches bare strings, which is the sixth cached type and not a record."""
    assert codec.decode(codec.encode(['public', 'analytics'])) == ('public', 'analytics')


def test_an_empty_answer_round_trips() -> None:
    """`tables('')` legitimately answers with nothing, and nothing is not a miss."""
    assert codec.decode(codec.encode([])) == ()


def test_a_subclass_encodes_as_the_type_it_extends() -> None:
    """An adapter may hand back a subclass; that is not a reason to fail a completion."""

    @dataclasses.dataclass(frozen=True)
    class Extended(Table):
        """A third-party adapter's own record."""

    encoded = codec.encode([Extended(schema='public', name='users')])
    assert codec.decode(encoded) == (Table(schema='public', name='users'),)


def test_an_unencodable_value_says_so_loudly() -> None:
    """Silence here would mean a cache that quietly stores nothing."""
    with pytest.raises(codec.UnencodableValue):
        codec.encode([object()])


def test_a_foreign_tag_is_refused_rather_than_imported() -> None:
    """
    The allowlist is the whole reason this is not pickle.

    A tag read out of cache bytes must never name a class to import — a shared
    store is a trust boundary, and that is the hazard the codec exists to avoid.
    """
    with pytest.raises(codec.UnencodableValue):
        codec.decode(b'{"t": "os.system", "v": []}')


def test_an_availability_survives_by_value() -> None:
    """The enum is what `_of_comparable_type` and the ranker read; a wrong member is a wrong answer."""
    restricted = Column(schema='public', table='users', name='password', type='text',
                        availability=Availability.RESTRICTED)
    assert codec.decode(codec.encode([restricted]))[0].availability is Availability.RESTRICTED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cache_codec.py -q`
Expected: FAIL — `ImportError: cannot import name 'codec'`

- [ ] **Step 3: Write `codec.py`**

Create `src/pysqlsuggestions/caches/codec.py`:

```python
"""
Cached values as bytes, for the stores that cannot hold objects.

Applied by `resolve.py` on the byte path and by nothing else — an adapter never
sees a `Table`, which is what makes an adapter that forgets to encode
unrepresentable rather than merely unlikely.

JSON rather than pickle. A shared store is a trust boundary, and a pickle
happily reconstructs a shape the running library no longer has; JSON in redis is
also readable from `redis-cli`, which is worth more than the bytes. The tag is
resolved against an allowlist and never imported — that is the pickle hazard,
and a clever codec is exactly how it would come back.

Written by hand, one entry per type. `tests/test_cache_codec.py` is what keeps
that honest: an AST pass says which types must be covered, and a round-trip of
fully populated instances says no field was dropped.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pysqlsuggestions.types import Availability, Column, ColumnValue, ForeignKey, Function, Table


class UnencodableValue(TypeError):
    """A value the codec has no entry for. Loud on purpose; see `resolve.py`."""


def _from_column(row: Column) -> dict[str, Any]:
    """One `Column` as JSON-safe fields."""
    return {
        'schema': row.schema,
        'table': row.table,
        'name': row.name,
        'type': row.type,
        'position': row.position,
        'availability': row.availability.value,
    }


def _to_column(raw: dict[str, Any]) -> Column:
    """One `Column` back."""
    return Column(
        schema=raw['schema'],
        table=raw['table'],
        name=raw['name'],
        type=raw['type'],
        position=raw['position'],
        availability=Availability(raw['availability']),
    )


def _from_table(row: Table) -> dict[str, Any]:
    """One `Table` as JSON-safe fields."""
    return {
        'schema': row.schema,
        'name': row.name,
        'kind': row.kind,
        'rows': row.rows,
        'availability': row.availability.value,
    }


def _to_table(raw: dict[str, Any]) -> Table:
    """One `Table` back."""
    return Table(
        schema=raw['schema'],
        name=raw['name'],
        kind=raw['kind'],
        rows=raw['rows'],
        availability=Availability(raw['availability']),
    )


def _from_function(row: Function) -> dict[str, Any]:
    """One `Function` as JSON-safe fields."""
    return {'schema': row.schema, 'name': row.name, 'args': row.args, 'result': row.result, 'kind': row.kind}


def _to_function(raw: dict[str, Any]) -> Function:
    """One `Function` back."""
    return Function(
        schema=raw['schema'], name=raw['name'], args=raw['args'], result=raw['result'], kind=raw['kind']
    )


def _from_column_value(row: ColumnValue) -> dict[str, Any]:
    """One `ColumnValue` as JSON-safe fields."""
    return {'text': row.text, 'frequency': row.frequency}


def _to_column_value(raw: dict[str, Any]) -> ColumnValue:
    """One `ColumnValue` back."""
    return ColumnValue(text=raw['text'], frequency=raw['frequency'])


def _from_foreign_key(row: ForeignKey) -> dict[str, Any]:
    """One `ForeignKey` as JSON-safe fields. Tuples become arrays; `_to_foreign_key` puts them back."""
    return {
        'schema': row.schema,
        'table': row.table,
        'columns': list(row.columns),
        'ref_schema': row.ref_schema,
        'ref_table': row.ref_table,
        'ref_columns': list(row.ref_columns),
    }


def _to_foreign_key(raw: dict[str, Any]) -> ForeignKey:
    """One `ForeignKey` back. `tuple`, not `list`: these are frozen hashable records."""
    return ForeignKey(
        schema=raw['schema'],
        table=raw['table'],
        columns=tuple(raw['columns']),
        ref_schema=raw['ref_schema'],
        ref_table=raw['ref_table'],
        ref_columns=tuple(raw['ref_columns']),
    )


TAGS: dict[str, Any] = {
    'str': str,
    'Column': Column,
    'ColumnValue': ColumnValue,
    'ForeignKey': ForeignKey,
    'Function': Function,
    'Table': Table,
}
"""The allowlist. A tag outside it is refused rather than resolved."""

_ENCODERS: dict[str, Any] = {
    'str': lambda row: row,
    'Column': _from_column,
    'ColumnValue': _from_column_value,
    'ForeignKey': _from_foreign_key,
    'Function': _from_function,
    'Table': _from_table,
}

_DECODERS: dict[str, Any] = {
    'str': lambda raw: raw,
    'Column': _to_column,
    'ColumnValue': _to_column_value,
    'ForeignKey': _to_foreign_key,
    'Function': _to_function,
    'Table': _to_table,
}


def _tag_of(record: type) -> str:
    """
    The tag for a value's type, walking the MRO if the exact type is unknown.

    An adapter is entitled to hand back a subclass of `Table`, and refusing one
    would fail a completion over a shape that carries every field the codec
    needs. What it may not do is invent a record with no cacheable base.
    """
    for candidate in record.__mro__:
        if candidate.__name__ in TAGS and TAGS[candidate.__name__] is candidate:
            return candidate.__name__
    raise UnencodableValue(f'nothing in the codec encodes {record.__name__}')


def encode(value: Sequence[object]) -> bytes:
    """
    One cached answer as bytes.

    The tag comes from the first row rather than from the caller, so an empty
    answer carries no tag at all — `tables('')` legitimately answers with
    nothing, and nothing has no element to read a type off. It decodes back to
    an empty tuple, which is a hit and not a miss.
    """
    rows = tuple(value)
    tag = None if not rows else _tag_of(type(rows[0]))
    encoder = (lambda row: row) if tag is None else _ENCODERS[tag]
    payload = {'t': tag, 'v': [encoder(row) for row in rows]}
    return json.dumps(payload, separators=(',', ':')).encode('utf-8')


def decode(data: bytes | str) -> tuple[object, ...]:
    """
    One cached answer back.

    Accepts `str` as well as `bytes` because a redis client built with
    `decode_responses=True` returns one, and that is a perfectly reasonable way
    for a caller to have configured a client they also use for other things.

    Raises on anything malformed. `resolve.py` treats that as a miss and does
    not disable the cache: one undecodable value most likely means somebody
    else's key, and punishing the other five reads for it would be wrong.
    """
    payload = json.loads(data)
    tag = payload['t']
    if tag is None:
        return ()
    if tag not in _DECODERS:
        raise UnencodableValue(f'no cached type is tagged {tag!r}')
    decoder = _DECODERS[tag]
    return tuple(decoder(row) for row in payload['v'])
```

- [ ] **Step 4: Export it**

In `src/pysqlsuggestions/caches/__init__.py`, add `codec` to the imports and to `__all__`:

```python
from pysqlsuggestions.caches import codec
from pysqlsuggestions.caches.keys import CACHED_TYPES, FINGERPRINT, KEY_VERSION, Kind, cache_key

__all__ = ['CACHED_TYPES', 'FINGERPRINT', 'KEY_VERSION', 'Kind', 'cache_key', 'codec']
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cache_codec.py -q`
Expected: PASS.

If `test_the_codec_covers_exactly_what_is_cached` fails, read the message: it names the mismatch. The six cached reads today are `schemas`, `tables`, `columns`, `functions`, `common_values` and `foreign_keys`, whose element types are `str`, `Table`, `Column`, `Function`, `ColumnValue` and `ForeignKey`.

- [ ] **Step 6: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/caches tests/test_cache_codec.py
git commit -F - <<'MSG'
feat: cached values encode to bytes for the stores that need them

JSON in a tagged envelope, written by hand, one entry per cached type. Not
pickle: a shared store is a trust boundary and a pickle reconstructs shapes the
running library no longer has, so the tag resolves against an allowlist and is
never imported.

An explicit codec has one failure worth designing against — a field added to a
cached type and not to the codec — and the guard for it is reflective even
though the codec is not. An AST pass over `_Reader` says which types must be
covered, in both directions, so a new capability that caches something unknown
fails here rather than in a store; a round-trip of instances built from
`dataclasses.fields` says no field was dropped. The second is the one that
matters. A missing type crashes on the first encode; a missing field is silent,
and leaves a value correct uncached and empty cached.

The tag walks the MRO, because an adapter is entitled to return a subclass of
`Table` and refusing one would fail a completion over a shape carrying every
field the codec needs. An empty answer carries no tag at all: `tables('')`
answers with nothing, and nothing has no element to read a type off.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 3: `MemoryCache`

**Files:**
- Create: `src/pysqlsuggestions/caches/memory.py`
- Modify: `src/pysqlsuggestions/caches/__init__.py`
- Test: `tests/test_cache_memory.py`

**Interfaces:**
- Produces: `MemoryCache(default_ttl: int | None = None)` with `get(key: str) -> Any | None` and `set(key: str, value: Any, ttl: int | None = None) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_memory.py`:

```python
"""The in-process cache: what `lsp/` and both demos hold."""

from __future__ import annotations

from pysqlsuggestions.caches import MemoryCache


def test_a_stored_value_comes_back() -> None:
    """The whole contract, in one line."""
    cache = MemoryCache()
    cache.set('k', [1, 2])
    assert cache.get('k') == [1, 2]


def test_an_unseen_key_is_a_miss() -> None:
    """`None` means miss, which is why no cached value is ever None."""
    assert MemoryCache().get('k') is None


def test_an_empty_answer_is_a_hit() -> None:
    """`tables('')` answers with nothing, and storing that must not read as a miss."""
    cache = MemoryCache()
    cache.set('k', ())
    assert cache.get('k') == ()


def test_a_value_is_overwritten() -> None:
    """A warm entry re-read after DDL should not be two entries."""
    cache = MemoryCache()
    cache.set('k', 'first')
    cache.set('k', 'second')
    assert cache.get('k') == 'second'


def test_an_expired_value_is_a_miss() -> None:
    """
    `ttl` means the same thing on both protocols, so an implementation ignoring it lies.

    The clock is monotonic rather than wall — a cache that forgot everything
    when somebody's NTP daemon stepped the clock backwards would be a very hard
    bug to report.
    """
    now = [1000.0]
    cache = MemoryCache()
    cache._clock = lambda: now[0]  # noqa: SLF001
    cache.set('k', 'v', ttl=10)
    now[0] = 1009.0
    assert cache.get('k') == 'v'
    now[0] = 1011.0
    assert cache.get('k') is None


def test_the_default_ttl_applies_when_none_is_given() -> None:
    """The library always passes `ttl=None`; the adapter is what owns expiry."""
    now = [1000.0]
    cache = MemoryCache(default_ttl=5)
    cache._clock = lambda: now[0]  # noqa: SLF001
    cache.set('k', 'v')
    now[0] = 1006.0
    assert cache.get('k') is None


def test_without_a_ttl_a_value_lives_as_long_as_the_process() -> None:
    """The default, and exactly the behaviour of the dict this replaces."""
    now = [1000.0]
    cache = MemoryCache()
    cache._clock = lambda: now[0]  # noqa: SLF001
    cache.set('k', 'v')
    now[0] = 1_000_000.0
    assert cache.get('k') == 'v'
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cache_memory.py -q`
Expected: FAIL — `ImportError: cannot import name 'MemoryCache'`

- [ ] **Step 3: Write `memory.py`**

Create `src/pysqlsuggestions/caches/memory.py`:

```python
"""
The cache a process keeps to itself.

An `ObjectCache`, so nothing is serialised: `lsp/` holds one of these per
session and reads it on every keystroke, and charging that path an encode and a
decode to reach a dict in the same process would be paying for a boundary that
is not there.
"""

from __future__ import annotations

import time
from typing import Any


class MemoryCache:
    """
    Catalog reads kept in a dict, optionally with an expiry.

    No `maxsize`, and that is a decision rather than an omission. The key is
    role, dialect, kind and namespace path, so entries are bounded by the size
    of the catalog times the number of roles this process serves — not by
    keystrokes, not by documents, not by anything that grows while somebody is
    typing. An LRU here would be a knob whose documentation had to admit nobody
    needs it.

    `default_ttl=None` is exactly the behaviour of the bare dict this replaces:
    an entry lives as long as the process. A `ttl` is honoured when given
    because the port has one, and a parameter an implementation silently ignores
    is a lie in a signature.
    """

    def __init__(self, default_ttl: int | None = None) -> None:
        self._entries: dict[str, tuple[float | None, Any]] = {}
        self._default_ttl = default_ttl
        self._clock = time.monotonic
        """
        Monotonic rather than wall.

        A cache that forgot everything because an NTP daemon stepped the clock
        backwards would be an impossible bug to report, and expiry only ever
        needs elapsed time.
        """

    def get(self, key: str) -> Any | None:
        """The cached value, or `None` for a miss or an expired entry."""
        found = self._entries.get(key)
        if found is None:
            return None
        expires, value = found
        if expires is not None and self._clock() >= expires:
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value, expiring after `ttl` seconds or the cache's default."""
        seconds = self._default_ttl if ttl is None else ttl
        self._entries[key] = (None if seconds is None else self._clock() + seconds, value)
```

- [ ] **Step 4: Export it**

In `src/pysqlsuggestions/caches/__init__.py`:

```python
from pysqlsuggestions.caches import codec
from pysqlsuggestions.caches.keys import CACHED_TYPES, FINGERPRINT, KEY_VERSION, Kind, cache_key
from pysqlsuggestions.caches.memory import MemoryCache

__all__ = ['CACHED_TYPES', 'FINGERPRINT', 'KEY_VERSION', 'Kind', 'MemoryCache', 'cache_key', 'codec']
```

- [ ] **Step 5: Run the tests and the gate**

Run: `uv run pytest tests/test_cache_memory.py -q && ./scripts/check.sh`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pysqlsuggestions/caches tests/test_cache_memory.py
git commit -F - <<'MSG'
feat: MemoryCache, the dict with the expiry the port implies

The in-process cache, kept an ObjectCache so the language server's per-keystroke
reads are not charged an encode and a decode to reach a dict in the same
process.

It honours a ttl because the port has one and a parameter an implementation
ignores is a lie in a signature, and the clock is monotonic because a cache that
emptied itself when an NTP daemon stepped the clock backwards would be an
impossible bug to report. The default is no expiry, which is exactly the bare
dict this replaces.

No maxsize, said out loud in the docstring so it does not read as an oversight:
the key is role, dialect, kind and namespace path, so entries are bounded by the
catalog times the roles a process serves, and by nothing that grows while
somebody types.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 4: `_key` returns a string, and the demo prewarm goes through it

The cache is still the old `get`/`__setitem__` protocol here, so a dict still works — a key is opaque to the store, which is what lets this land as its own green commit ahead of the breaking change.

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py` (`_Reader._memo`, `_key`, `_read`, `_read_through`, and the six read methods)
- Modify: `demo/app.py:210-218`
- Test: `tests/test_complete.py` (four `dict[tuple[object, ...], object]` annotations, and `test_cache_is_keyed_by_role`), `tests/test_availability.py` (two `dict[object, object]` annotations)

**Interfaces:**
- Consumes: `cache_key`, `Kind` from Task 1.
- Produces: `_Reader._key(kind: Kind, *parts: str | None) -> str`. Every cache key in the process is now a `str`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_complete.py`, and add `from pysqlsuggestions.caches import cache_key` to its imports:

```python
def test_a_completion_stores_the_key_cache_key_builds() -> None:
    """
    What a prewarm has to write, proved rather than assumed.

    `demo/app.py` fills the cache before anybody types, so it constructs keys the
    reader must then find — and it built them by hand, which is exactly the
    contract this asserts. Nothing else in the suite compares the two sides, so
    a prewarm writing keys no read ever looks up would pass every test and warm
    nothing.
    """
    cache: dict[str, object] = {}
    complete('SELECT * FROM ', 14, POSTGRES, catalog(), cache=cache, identity='analyst')
    assert cache_key('analyst', 'postgres', 'tables', None) in cache
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_complete.py::test_a_completion_stores_the_key_cache_key_builds -q`
Expected: FAIL — the cache holds tuples, so the string is not in it.

- [ ] **Step 3: Change `_Reader` to build string keys**

In `src/pysqlsuggestions/resolve.py`:

Add to the imports:

```python
from pysqlsuggestions.caches.keys import Kind, cache_key
```

Change `_memo`'s annotation in `__init__` from `dict[tuple[str | None, ...], Any]` to `dict[str, Any]`.

Replace `_key` entirely:

```python
    def _key(self, kind: Kind, *parts: str | None) -> str:
        """
        The documented cache key, encoded by `caches.keys`.

        The NUL sentinels this used to carry are now `kind`, a field of the
        grammar — which is what makes `tables(None)` and `columns(None, '')`
        structurally distinct rather than distinct by convention. `None` is
        still carried through rather than folded to `''`, because to every one
        of these readers the two mean different things: `None` is "wherever the
        search path reaches" and `''` is a namespace actually named that.
        """
        return cache_key(self._identity, self._dialect.name, kind, *parts)
```

Change the signatures of `_read` and `_read_through` from `key: tuple[str | None, ...]` to `key: str`.

Replace the six call sites:

| method | was | becomes |
| --- | --- | --- |
| `schemas` | `self._key(catalog, '\x00schemas')` | `self._key('schemas', catalog)` |
| `tables` | `self._key(schema, '\x00tables')` | `self._key('tables', schema)` |
| `columns` | `self._key(schema, table)` | `self._key('columns', schema, table)` |
| `functions` | `self._key(schema, '\x00functions')` | `self._key('functions', schema)` |
| `common_values` | `self._key(schema, table, f'\x00values:{column}')` | `self._key('values', schema, table, column)` |
| `foreign_keys` | `self._key(schema, '\x00fk')` | `self._key('fk', schema)` |

In `common_values`, the local `key = self._key(...)` line changes with it.

- [ ] **Step 4: Update the test annotations and the key-shape test**

In `tests/test_complete.py`, change all four `cache: dict[tuple[object, ...], object] = {}` to `cache: dict[str, object] = {}`.

In `tests/test_availability.py`, change both `shared: dict[object, object] = {}` to `shared: dict[str, object] = {}`.

Replace `test_cache_is_keyed_by_role` in `tests/test_complete.py`, which read `key[0]` off a tuple:

```python
def test_cache_is_keyed_by_role() -> None:
    """Two roles must not share a cached read; the key shape is a documented contract."""
    cache: dict[str, object] = {}
    sql, caret = split_caret('SELECT * FROM reports_report r WHERE r.⌶')
    complete(sql, caret, POSTGRES, catalog(), cache=cache, identity='analyst')
    complete(sql, caret, POSTGRES, catalog(), cache=cache, identity='admin')
    analyst = {key for key in cache if ':+analyst:' in key}
    admin = {key for key in cache if ':+admin:' in key}
    assert analyst and admin and not analyst & admin
```

- [ ] **Step 5: Fix the demo prewarm**

In `demo/app.py`, add `from pysqlsuggestions.caches import cache_key` to the imports and replace the two lines at the end of `_warm`:

```python
    with suppress(Exception):
        for name in ('', *catalog.schemas()):
            schema = name or None
            cache[cache_key('demo', dialect.name, 'schemas', schema)] = catalog.schemas(schema)
            cache[cache_key('demo', dialect.name, 'tables', schema)] = catalog.tables(schema)
```

The `schema = name or None` line is load-bearing and is a fix, not a rename: the old code stored the relation list under the schema name `''` where the reader looks under `None`, and under a bare `''` discriminator where the reader uses `'\x00tables'`. Both halves of the default namespace's prewarm were being written where nothing read them.

- [ ] **Step 6: Run the tests and the gate**

Run: `uv run pytest -m 'not integration' -q && ./scripts/check.sh`
Expected: PASS. `test_a_quoted_empty_namespace_does_not_break_the_relation_cache` and `test_an_empty_namespace_does_not_empty_the_relation_list` in `test_complete.py` must still pass — they are the regression tests for the collision the new grammar makes impossible.

- [ ] **Step 7: Commit**

```bash
git add src/pysqlsuggestions/resolve.py demo/app.py tests/test_complete.py tests/test_availability.py
git commit -F - <<'MSG'
refactor: the reader addresses its cache by string

`_key` returns what `caches.keys` encodes. The NUL sentinels become `kind`, a
field of the grammar, so the collision that once emptied a session's relation
list is now unrepresentable rather than avoided by convention.

The cache is still the old protocol, so a dict still satisfies it and this lands
on its own — a key is opaque to the store, which is the property that lets the
encoding move before the port changes.

It also fixes the demo's prewarm, which had drifted from the reader it warms:
the default namespace's relations were written under the schema name '' where
the reader looks under None, and under a bare '' discriminator where the reader
uses a sentinel. Both halves went somewhere nothing read. Nothing in the suite
compared the two sides, so a prewarm that warmed nothing passed every test;
there is now a test that builds the key from `cache_key` and asserts a real
completion stored it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 5: A cache that fails does not fail a completion

Still the old protocol. A dict cannot raise, so this is written against a dict subclass that can.

**Files:**
- Modify: `src/pysqlsuggestions/resolve.py` (`_Reader.__init__`, `_read_through`)
- Test: `tests/test_cache_failure.py`

**Interfaces:**
- Produces: `_Reader._failed: bool` — the latch, read by `_read_through`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_failure.py`:

```python
"""
A cache is an optimisation, and an optimisation may not fail a completion.

`resolve.py` says for every capability what happens when it is absent. `Cache`
had no such paragraph because a dict cannot fail; a store on the other side of a
socket can be down, slow, or hand back something foreign.
"""

from __future__ import annotations

from typing import Any

from pysqlsuggestions import complete
from pysqlsuggestions.dialects.postgres import POSTGRES
from tests.test_complete import catalog

SQL = 'SELECT * FROM reports_report r WHERE r.'


class _Unreachable(dict[str, Any]):
    """A cache whose every read raises, the way a store with no route does."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get(self, key: str, default: Any = None) -> Any:
        """Count the attempt, then fail like a socket with nowhere to go."""
        self.reads += 1
        raise ConnectionError('no route to host')


class _WriteOnlyFailure(dict[str, Any]):
    """A cache that reads cleanly and cannot be written to, the way a full store behaves."""

    def __setitem__(self, key: str, value: Any) -> None:
        """Refuse the write."""
        raise OSError('OOM command not allowed when used memory > maxmemory')


def test_a_failing_read_does_not_fail_the_completion() -> None:
    """The documented degradation for every other capability, extended to this one."""
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    found = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=_Unreachable(), identity='analyst')]
    assert found == cold


def test_a_failing_cache_is_asked_once_per_request() -> None:
    """
    The latch, which is the part that matters.

    With a two-second socket timeout an unlatched store costs one timeout per
    read rather than one per request — six times slower than having no cache at
    all, and indistinguishable from the engine hanging.
    """
    unreachable = _Unreachable()
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=unreachable, identity='analyst')
    assert unreachable.reads == 1


def test_a_failing_write_does_not_fail_the_completion() -> None:
    """A store that cannot accept a value is a store that cannot cache, not an error."""
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    found = [
        s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=_WriteOnlyFailure(), identity='analyst')
    ]
    assert found == cold
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cache_failure.py -q`
Expected: FAIL — `ConnectionError: no route to host` propagates out of `complete`.

- [ ] **Step 3: Add the latch**

In `src/pysqlsuggestions/resolve.py`, add to `_Reader.__init__`:

```python
        self._failed = False
        """
        Whether the cache has already failed during this request.

        Latched rather than retried. A store behind a socket with a two-second
        timeout costs one timeout per read otherwise, so a request making six
        reads waits twelve seconds to answer what it could have answered in
        none — slower than having no cache at all, and indistinguishable from
        the engine hanging.
        """
```

Replace `_read_through`:

```python
    def _read_through(self, key: str, produce: Callable[[], _T]) -> _T:
        """
        The caller's cache, when there is one and it is still answering.

        Every failure here is caught and none reaches the caller. A cache is an
        optimisation, and the rule the rest of this module follows — a missing
        capability costs suggestions and never raises — holds for this one too.
        `Exception` broadly, because the library cannot name a driver's errors
        without importing it and a caller's object may raise anything.
        """
        if self._cache is None or self._failed:
            return produce()
        try:
            cached = self._cache.get(key)
        except Exception:  # noqa: BLE001
            self._failed = True
            return produce()
        if cached is not None:
            found: _T = cached
            return found
        value = produce()
        try:
            self._cache[key] = value
        except Exception:  # noqa: BLE001
            self._failed = True
        return value
```

- [ ] **Step 4: Run the tests and the gate**

Run: `uv run pytest tests/test_cache_failure.py -q && ./scripts/check.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pysqlsuggestions/resolve.py tests/test_cache_failure.py
git commit -F - <<'MSG'
fix: a cache that fails costs suggestions, never the completion

`resolve.py` states for every capability what happens when it is absent, and
`Cache` was the one port with no such paragraph — because a dict cannot fail,
and until now every cache was a dict.

Both the read and the write are caught, broadly, since the library cannot name a
driver's errors without importing it. The read then latches the cache off for
the rest of the request, which is the half worth arguing for: with a two-second
socket timeout an unlatched store costs one timeout per read rather than one per
request, so a request making six reads waits twelve seconds to answer what it
could have answered in none. That is slower than having no cache at all and
looks exactly like the engine hanging.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 6: `InMemoryByteCache`

**Files:**
- Create: `src/pysqlsuggestions/testing/caches.py`
- Modify: `src/pysqlsuggestions/testing/__init__.py`
- Test: `tests/test_cache_bytes.py`

**Interfaces:**
- Produces: `InMemoryByteCache(default_ttl: int | None = None)` with `get_bytes(key: str) -> bytes | None`, `set_bytes(key: str, value: bytes, ttl: int | None = None) -> None`, and `writes: list[str]` recording the keys stored.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_bytes.py`:

```python
"""The byte discipline, exercised without a socket."""

from __future__ import annotations

from pysqlsuggestions.testing import InMemoryByteCache


def test_bytes_round_trip() -> None:
    """The whole contract."""
    cache = InMemoryByteCache()
    cache.set_bytes('k', b'\x00\xff\x80')
    assert cache.get_bytes('k') == b'\x00\xff\x80'


def test_an_unseen_key_is_a_miss() -> None:
    """`None`, and specifically not `b''`, which is a value somebody could store."""
    assert InMemoryByteCache().get_bytes('k') is None


def test_empty_bytes_are_a_value_and_not_a_miss() -> None:
    """A store conflating the two would turn every empty answer into a re-read."""
    cache = InMemoryByteCache()
    cache.set_bytes('k', b'')
    assert cache.get_bytes('k') == b''


def test_it_records_what_was_written() -> None:
    """Tests need to see which keys a completion stored, without reaching into privates."""
    cache = InMemoryByteCache()
    cache.set_bytes('a', b'1')
    cache.set_bytes('b', b'2')
    assert cache.writes == ['a', 'b']
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cache_bytes.py -q`
Expected: FAIL — `ImportError: cannot import name 'InMemoryByteCache'`

- [ ] **Step 3: Write `testing/caches.py`**

Create `src/pysqlsuggestions/testing/caches.py`:

```python
"""
Harnesses for the cache port, shipped for the same reason `DialectConformance` is.

`InMemoryByteCache` lives here rather than in `caches/`, and the distinction is
not filing. In memory it is strictly worse than `MemoryCache` — it pays an
encode and a decode to reach a dict in the same process — so putting it beside
`MemoryCache` would be an invitation somebody eventually accepts. `testing` says
what it is for.
"""

from __future__ import annotations

import time


class InMemoryByteCache:
    """
    A `ByteCache` with no socket, for exercising the encoded path in the fast suite.

    Records every key written, so a test can assert what a completion stored
    without reaching into a private. Not for production: `MemoryCache` is the
    same dict without the serialisation.
    """

    def __init__(self, default_ttl: int | None = None) -> None:
        self._entries: dict[str, tuple[float | None, bytes]] = {}
        self._default_ttl = default_ttl
        self._clock = time.monotonic
        self.writes: list[str] = []
        """Every key stored, in order. A cheap window for tests."""

    def get_bytes(self, key: str) -> bytes | None:
        """The stored bytes, or `None` for a miss or an expired entry."""
        found = self._entries.get(key)
        if found is None:
            return None
        expires, value = found
        if expires is not None and self._clock() >= expires:
            del self._entries[key]
            return None
        return value

    def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
        """Store bytes, expiring after `ttl` seconds or the cache's default."""
        seconds = self._default_ttl if ttl is None else ttl
        self._entries[key] = (None if seconds is None else self._clock() + seconds, value)
        self.writes.append(key)
```

- [ ] **Step 4: Export it**

In `src/pysqlsuggestions/testing/__init__.py`, add the import near the other imports and extend `__all__`:

```python
from pysqlsuggestions.testing.caches import InMemoryByteCache

__all__ = ['Case', 'DialectConformance', 'InMemoryByteCache']
```

Extend the module docstring's opening line — it currently says the package is "the shared corpus every dialect must pass", which is now half of what it holds. Say instead that it ships the harnesses a third-party implementation needs: a conformance corpus for dialects, and the cache doubles.

- [ ] **Step 5: Run the tests and the gate**

Run: `uv run pytest tests/test_cache_bytes.py -q && ./scripts/check.sh`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/pysqlsuggestions/testing tests/test_cache_bytes.py
git commit -F - <<'MSG'
test: a byte cache with no socket, for the fast suite

Shipped in `testing` rather than in `caches`, and that is not filing. In memory
this is strictly worse than MemoryCache — it pays an encode and a decode to
reach a dict in the same process — so sitting it next to MemoryCache would be an
invitation somebody eventually accepts. `testing` says what it is for, and it
gives the conformance harness a reference implementation to check itself
against.

It records the keys written, so a test can assert what a completion stored
without reaching into a private.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 7: The protocol break

One commit, because a protocol cannot be half-replaced. Everything that touches a cache moves together.

**Files:**
- Modify: `src/pysqlsuggestions/ports.py` (replace `Cache`)
- Modify: `src/pysqlsuggestions/__init__.py` (exports)
- Modify: `src/pysqlsuggestions/api.py` (`complete`, the `TypeError`)
- Modify: `src/pysqlsuggestions/resolve.py` (`_Reader.__init__`, `_read_through`)
- Modify: `lsp/pysqlsuggestions_lsp/server.py:117`
- Modify: `demo/app.py:128`, `demo/app.py:210`, `demo/app.py:268`, `demo/browser.py:64`, `demo/payload.py:54`
- Test: `tests/test_cache_failure.py`, `tests/test_complete.py`, `tests/test_availability.py`, `tests/integration/test_acceptance.py:153`

**Interfaces:**
- Consumes: `MemoryCache` (Task 3), `codec` (Task 2), `InMemoryByteCache` (Task 6).
- Produces: `ports.ObjectCache`, `ports.ByteCache`, `ports.Cache` (union alias). `_Reader._encoded: bool`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache_failure.py`:

```python
def test_a_plain_dict_is_refused_at_the_door() -> None:
    """
    Loud, because the alternative is invisible.

    A dict has `.get` and no `.set`, so it satisfies neither protocol. Treating
    "neither" as "no cache" would leave every caller written against the old
    port correct, silent and uncached — for as long as it took somebody to
    notice completions had got slower.
    """
    with pytest.raises(TypeError, match='MemoryCache'):
        complete(SQL, len(SQL), POSTGRES, catalog(), cache={}, identity='analyst')


def test_a_byte_cache_serves_the_same_suggestions() -> None:
    """The discipline is a storage decision and must not be an answer decision."""
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    encoded = InMemoryByteCache()
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=encoded, identity='analyst')
    warm = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=encoded, identity='analyst')]
    assert warm == cold


def test_an_undecodable_value_is_a_miss_and_does_not_latch() -> None:
    """
    One foreign value under our namespace must not disable the other five reads.

    A decode failure is not a transport failure. It most likely means somebody
    else's key, and punishing the rest of the request for it would be answering
    the wrong question.
    """
    encoded = InMemoryByteCache()
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=encoded, identity='analyst')
    for key in list(encoded.writes):
        encoded.set_bytes(key, b'not json at all')
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    encoded.writes.clear()
    found = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=encoded, identity='analyst')]
    assert found == cold
    assert len(encoded.writes) > 1, 'a decode failure latched the cache off; it should only be a miss'
```

Add `import pytest` and `from pysqlsuggestions.testing import InMemoryByteCache` to that module.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cache_failure.py -q`
Expected: FAIL — no `TypeError` is raised, and `InMemoryByteCache` is not accepted.

- [ ] **Step 3: Replace the port**

In `src/pysqlsuggestions/ports.py`, replace the whole `Cache` class. Add `TypeAlias` to the `typing` import.

```python
@runtime_checkable
class ObjectCache(Protocol):
    """
    Somewhere to keep catalog reads as Python objects. In practice, a dict.

    The key is an opaque string built by `pysqlsuggestions.caches.cache_key`,
    which is the only supported way to make one — the string's shape is not a
    format, and changes whenever a cached type does.

    `None` means miss. No value the library caches is ever `None`, which is what
    makes one channel enough for two answers; the cost of that rule is recorded
    in `docs/gaps.md`, where caching `all_columns` sits blocked by it.

    `ttl` is integer seconds, and `None` means the implementation's own default.
    The library never passes one: it knows what a value is, not how long the
    deployment wants it, so expiry belongs to whoever built the cache.
    """

    def get(self, key: str) -> Any | None:
        """The cached value, or `None`."""
        ...

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value."""
        ...


@runtime_checkable
class ByteCache(Protocol):
    """
    Somewhere to keep catalog reads as bytes. Anything across a process boundary.

    The library encodes and decodes; an implementation never sees a `Table`,
    which is what makes an adapter that forgets to encode unrepresentable rather
    than merely unlikely.

    The method names differ from `ObjectCache`'s deliberately. `isinstance`
    against a `runtime_checkable` Protocol compares method names and nothing
    else, so two protocols both spelling `get` and `set` would be
    indistinguishable at runtime and would need a marker attribute whose only
    job was to say which of two identical shapes was meant. Two smaller things
    fall out: an implementation wrapping a client that already has `get` and
    `set` with other semantics can delegate without shadowing, and a two-tier
    cache can implement both. Where both are present the library uses
    `ObjectCache`, because that path costs no encode.

    `None` means miss, and specifically not `b''`, which is a value.

    **The contract on sharing.** A cache must not be shared across databases. It
    must also not be shared across identities *unless* the caller passes
    `identity`, since that already leads the key. One namespace per database,
    per identity you cannot name — the reads this caches are privilege-filtered,
    so getting it wrong serves one user's readable set to another, which is
    silent and reads as a database privilege bug.
    """

    def get_bytes(self, key: str) -> bytes | None:
        """The stored bytes, or `None`."""
        ...

    def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
        """Store bytes."""
        ...


Cache: TypeAlias = ObjectCache | ByteCache
"""
Either discipline. An implementer satisfies whichever they can.

A plain dict satisfies neither, which is a break from every version before
0.9.0: it has `get` and no `set`. `pysqlsuggestions.caches.MemoryCache` is the
dict this used to be.
"""
```

- [ ] **Step 4: Export the two protocols**

In `src/pysqlsuggestions/__init__.py`, add `ByteCache` and `ObjectCache` to the `pysqlsuggestions.ports` import and to `__all__`, keeping both lists alphabetical.

- [ ] **Step 5: Refuse a cache that is neither**

In `src/pysqlsuggestions/api.py`, import `ByteCache, ObjectCache` from `pysqlsuggestions.ports` and add to `complete`, immediately before `derive_request`:

```python
    if cache is not None and not isinstance(cache, ObjectCache | ByteCache):
        raise TypeError(
            'cache must satisfy ObjectCache (get/set) or ByteCache (get_bytes/set_bytes). '
            'A plain dict satisfies neither — use pysqlsuggestions.caches.MemoryCache().'
        )
```

Extend `complete`'s docstring with a sentence saying a dict is refused here rather than ignored, because a silently uncached caller is correct, slow, and gives nothing to notice.

- [ ] **Step 6: Teach `_Reader` both disciplines**

In `src/pysqlsuggestions/resolve.py`, add `from pysqlsuggestions.caches import codec` and `ByteCache, ObjectCache` to the `ports` import, and `cast` to the `typing` import.

In `_Reader.__init__`, after `self._cache = cache`:

```python
        self._encoded = cache is not None and not isinstance(cache, ObjectCache)
        """
        Whether this cache takes bytes.

        Decided once rather than per read, and `ObjectCache` wins a tie: an
        implementation satisfying both is a two-tier cache, and the object path
        is the one that costs no encode.
        """
```

Replace `_read_through` and add the two helpers:

```python
    def _read_through(self, key: str, produce: Callable[[], _T]) -> _T:
        """
        The caller's cache, when there is one and it is still answering.

        Every failure is caught here and none reaches the caller. A cache is an
        optimisation, and the rule the rest of this module follows — a missing
        capability costs suggestions and never raises — holds for this one too.
        """
        if self._cache is None or self._failed:
            return produce()
        cached = self._cached(key)
        if cached is not None:
            found: _T = cached
            return found
        value = produce()
        self._store(key, value)
        return value

    def _cached(self, key: str) -> Any | None:
        """
        One read, or `None` for a miss, a failure, or a value we cannot decode.

        A transport failure latches; a decode failure does not. They are
        different events: the first says the store is unreachable and every
        further read this request will pay the same timeout, the second says one
        value under our namespace is not ours, and disabling the other five
        reads over it would punish the wrong thing.
        """
        cache = self._cache
        try:
            if not self._encoded:
                return cast(ObjectCache, cache).get(key)
            raw = cast(ByteCache, cache).get_bytes(key)
        except Exception:  # noqa: BLE001
            self._failed = True
            return None
        if raw is None:
            return None
        try:
            return codec.decode(raw)
        except Exception:  # noqa: BLE001
            return None

    def _store(self, key: str, value: Any) -> None:
        """
        One write, failing silently.

        The encode happens *outside* the guard on purpose. A value the codec
        cannot handle is our bug and not the store's, and swallowing it would
        turn a red build into a cache that quietly stores nothing;
        `tests/test_cache_codec.py` exists so it never reaches here.
        """
        cache = self._cache
        payload = codec.encode(value) if self._encoded else None
        try:
            if payload is None:
                cast(ObjectCache, cache).set(key, value)
            else:
                cast(ByteCache, cache).set_bytes(key, payload)
        except Exception:  # noqa: BLE001
            self._failed = True
```

- [ ] **Step 7: Update every caller**

| file | change |
| --- | --- |
| `lsp/pysqlsuggestions_lsp/server.py:117` | `cache: MemoryCache = field(default_factory=MemoryCache)`, importing from `pysqlsuggestions.caches` |
| `demo/app.py:128` | `_caches: dict[str, MemoryCache] = {}` |
| `demo/app.py:210` | `cache = _caches.setdefault(key, MemoryCache())` |
| `demo/app.py:216-218` | `cache.set(cache_key(...), ...)` for both lines |
| `demo/app.py:268` | `cache = _caches.setdefault(backend.key, MemoryCache())` |
| `demo/browser.py:64` | `self._caches: dict[str, MemoryCache] = {key: MemoryCache() for key in self._catalogs}` |
| `demo/payload.py:54` | `cache: Cache \| None = None`, importing `Cache` from `pysqlsuggestions` |

- [ ] **Step 8: Update every test that passes a cache**

In `tests/test_complete.py`, add a recorder next to the other helpers and use it where a test inspects keys:

```python
class _Recorder(MemoryCache):
    """A `MemoryCache` that remembers the keys written to it, so a test can read them."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Record the key, then store as usual."""
        self.writes.append(key)
        super().set(key, value, ttl)
```

- `test_cache_is_keyed_by_role`: `cache = _Recorder()`, and filter `cache.writes` rather than the mapping.
- `test_a_completion_stores_the_key_cache_key_builds`: `cache = _Recorder()`, then `assert cache_key('analyst', 'postgres', 'tables', None) in cache.writes`.
- The other four `dict[str, object]` caches in `test_complete.py` become `MemoryCache()`.
- Both `shared: dict[str, object] = {}` in `tests/test_availability.py` become `shared = MemoryCache()`.
- `tests/integration/test_acceptance.py:153` `cache: dict[object, object] = {}` becomes `cache = MemoryCache()`.

In `tests/test_cache_failure.py`, the two doubles no longer satisfy anything as dict subclasses. Rewrite them as `ObjectCache`s:

```python
class _Unreachable:
    """A cache whose every read raises, the way a store with no route does."""

    def __init__(self) -> None:
        self.reads = 0

    def get(self, key: str) -> Any | None:
        """Count the attempt, then fail like a socket with nowhere to go."""
        self.reads += 1
        raise ConnectionError('no route to host')

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Never reached; the read fails first."""
        raise ConnectionError('no route to host')


class _WriteOnlyFailure:
    """A cache that reads cleanly and cannot be written to, the way a full store behaves."""

    def get(self, key: str) -> Any | None:
        """Always a miss."""
        return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Refuse the write."""
        raise OSError('OOM command not allowed when used memory > maxmemory')
```

- [ ] **Step 9: Run everything**

Run: `uv run pytest -m 'not integration' -q && ./scripts/check.sh`
Expected: PASS. Also run `cd editors/vscode && npm run check` — it does not touch Python, but confirm it is still green before a release-shaped commit.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -F - <<'MSG'
feat!: the cache port splits into an object and a byte discipline

`Cache` becomes two protocols and a union of them. `ObjectCache` keeps Python
objects and is what a process holds for itself; `ByteCache` keeps bytes and is
everything that crosses a boundary. An implementer satisfies whichever they can,
and byte stores are almost everything that is not a dict.

The method names differ deliberately. `isinstance` against a runtime_checkable
Protocol compares method names and nothing else, so two protocols both spelling
`get` and `set` would be indistinguishable and would need a marker attribute
whose only job was to say which of two identical shapes was meant. Distinct
names make the detection structural, the way every `Supports*` protocol here
already is, and let a two-tier cache implement both — where it does, the object
path wins, because it costs no encode.

The codec applies on the byte path and nowhere else, which is why the split is
worth having: the language server's per-keystroke dict pays no serialisation,
and an adapter that forgets to encode is unrepresentable rather than unlikely.

A plain dict now satisfies neither — it has `get` and no `set` — so `complete`
raises rather than ignoring it. Treating "neither" as "no cache" would have left
every existing caller correct, silent and uncached, which is the worst failure
available here: invisible, and slow only in a way nobody attributes to this.

Decode failures are misses and do not latch. That is a different event from a
transport failure: one says the store is unreachable and every further read pays
the same timeout, the other says one value under our namespace is not ours.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 8: Every read through the byte path, and the existing suite through both

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_cache_bytes.py`
- Modify: `tests/test_complete.py`, `tests/test_availability.py`

**Interfaces:**
- Produces: a `cache` fixture parametrised over `MemoryCache` and `InMemoryByteCache`.

- [ ] **Step 1: Write the failing test for the six reads**

Add to `tests/test_cache_bytes.py`:

```python
def test_every_cached_read_survives_the_byte_path() -> None:
    """
    All six reads encoded and decoded, which the borrowed coverage does not reach.

    The suite's existing cache sites exercise relations and columns and nothing
    else, so `common_values` and `foreign_keys` — the two capability-gated
    reads, whose value types are least like the others — would otherwise never
    be encoded at all. Asserting on the `kind` component rather than on
    suggestions makes this a statement about coverage rather than about ranking.
    """
    encoded = InMemoryByteCache()
    catalog = _catalog_answering_everything()
    for sql in (
        'SELECT * FROM ',
        'SELECT * FROM public.',
        'SELECT * FROM users u WHERE u.',
        'SELECT * FROM users u WHERE u.status = ',
        'SELECT * FROM users u JOIN ',
        'SELECT lo',
    ):
        complete(sql, len(sql), POSTGRES, catalog, cache=encoded, identity='analyst')
    kinds = {key.split(':')[4] for key in encoded.writes}
    assert kinds == {'schemas', 'tables', 'columns', 'functions', 'values', 'fk'}
```

Write `_catalog_answering_everything` as a `MemoryCatalog` carrying two relations, a declared foreign key between them, and common values on `users.status`. `MemoryCatalog` already implements every capability — check its `__init__` signature at `src/pysqlsuggestions/catalogs/memory.py:61` and pass the arguments it names. If a `kind` is missing from the assertion, add a caret that reaches that read rather than weakening the assertion.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cache_bytes.py::test_every_cached_read_survives_the_byte_path -q`
Expected: FAIL — either on the helper not existing, or on a `kind` missing from the set.

- [ ] **Step 3: Make it pass**

Write the helper. Do not change the codec: if a type fails to encode here, `tests/test_cache_codec.py` should have caught it and the guard has a hole worth fixing first.

- [ ] **Step 4: Add the parametrised fixture**

In `tests/conftest.py`, adding `import pytest`, `from pysqlsuggestions.caches import MemoryCache`,
`from pysqlsuggestions.ports import Cache` and `from pysqlsuggestions.testing import InMemoryByteCache`
to its imports:

```python
@pytest.fixture(params=['object', 'bytes'])
def cache(request: pytest.FixtureRequest) -> Cache:
    """
    Both cache disciplines, so tests written for other reasons exercise the encoded path.

    Unplanned coverage is the point: the sites using this were written about
    roles, about the empty namespace and about second reads, and each of them
    now says the same thing about bytes for free.
    """
    return MemoryCache() if request.param == 'object' else InMemoryByteCache()
```

- [ ] **Step 5: Use it at the sites that do not inspect keys**

In `tests/test_complete.py` and `tests/test_availability.py`, the tests that only pass a cache through take `cache: Cache` as a parameter and drop their local construction. The two using `_Recorder` keep it — they assert on keys, which is a property of one discipline's bookkeeping rather than of the port.

- [ ] **Step 6: Run and commit**

Run: `uv run pytest -m 'not integration' -q && ./scripts/check.sh`
Expected: PASS, with the parametrised tests reported twice each.

```bash
git add tests/
git commit -F - <<'MSG'
test: the encoded path, from the suite and from a dedicated pass

Two halves doing different jobs. A fixture parametrised over both disciplines
puts the thirteen existing cache sites through bytes as well as objects, so
tests written about roles and about the empty namespace now say the same thing
about encoding for free — unplanned coverage, which is the useful kind.

Those sites reach relations and columns and nothing else, so a dedicated pass
drives all six reads and asserts on the key's `kind` component. Without it
`common_values` and `foreign_keys` — the two capability-gated reads, whose value
types are least like the others — would never be encoded at all, and "every
cached type round-trips" would be a hope rather than a statement.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 9: `CacheConformance`

**Files:**
- Modify: `src/pysqlsuggestions/testing/caches.py`, `src/pysqlsuggestions/testing/__init__.py`
- Test: `tests/test_cache_conformance.py`

**Interfaces:**
- Produces: `CacheConformance.check(cache: ByteCache) -> list[str]` — an empty list when the implementation conforms, one sentence per failure otherwise. Mirrors `DialectConformance.check`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_conformance.py`:

```python
"""The harness a third-party ByteCache is measured against, measured itself."""

from __future__ import annotations

from typing import Any

from pysqlsuggestions.testing import CacheConformance, InMemoryByteCache


def test_the_reference_implementation_conforms() -> None:
    """If the shipped one fails its own harness, the harness is wrong."""
    assert CacheConformance.check(InMemoryByteCache()) == []


def test_a_cache_conflating_a_miss_with_empty_bytes_is_caught() -> None:
    """`b''` is a value somebody can store; returning it for a miss turns hits into re-reads."""

    class _Conflating(InMemoryByteCache):
        def get_bytes(self, key: str) -> bytes | None:
            """Answer a miss with empty bytes, the way a careless wrapper does."""
            return super().get_bytes(key) or b''

    assert CacheConformance.check(_Conflating()) != []


def test_a_cache_that_mangles_binary_is_caught() -> None:
    """A store that round-trips through text loses exactly the bytes JSON puts in it."""

    class _Lossy(InMemoryByteCache):
        def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
            """Drop everything above ASCII, the way a text column would."""
            super().set_bytes(key, bytes(b for b in value if b < 128), ttl)

    assert CacheConformance.check(_Lossy()) != []


def test_a_cache_that_never_overwrites_is_caught() -> None:
    """A warm entry re-read after DDL must replace the old one, not sit behind it."""

    class _WriteOnce(InMemoryByteCache):
        def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
            """Keep the first value forever."""
            if self.get_bytes(key) is None:
                super().set_bytes(key, value, ttl)

    assert CacheConformance.check(_WriteOnce()) != []


def test_a_cache_that_reinterprets_keys_is_caught() -> None:
    """Keys are opaque. A store lowercasing or truncating them merges distinct reads."""

    class _Folding(InMemoryByteCache):
        def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
            """Fold case, the way a case-insensitive store does."""
            super().set_bytes(key.lower(), value, ttl)

        def get_bytes(self, key: str) -> bytes | None:
            """Fold case on the way out too, which is what makes it merge."""
            return super().get_bytes(key.lower())

    assert CacheConformance.check(_Folding()) != []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cache_conformance.py -q`
Expected: FAIL — `ImportError: cannot import name 'CacheConformance'`

- [ ] **Step 3: Write the harness**

Add to `src/pysqlsuggestions/testing/caches.py`:

```python
class CacheConformance:
    """
    What a `ByteCache` must satisfy to be usable.

    Shipped for the same reason `DialectConformance` is: two method names look
    like the whole contract and are not. A store may conflate a miss with empty
    bytes, mangle binary on the way through a text column, fold the case of a
    key, or refuse to overwrite — each of which is silent, and each of which
    turns correct suggestions into stale or absent ones.

    Not a test of expiry. A portable check would have to sleep, and a harness
    that takes seconds is a harness nobody runs; the shipped redis adapter has
    an integration test against a real server for that.

        from pysqlsuggestions.testing import CacheConformance

        failures = CacheConformance.check(MyCache())
        assert not failures, failures
    """

    @staticmethod
    def check(cache: ByteCache) -> list[str]:
        """Every way `cache` departs from the contract, as sentences. Empty when it conforms."""
        failures: list[str] = []
        prefix = 'pysqlsuggestions-conformance'
        binary = bytes(range(256))

        if cache.get_bytes(f'{prefix}:absent') is not None:
            failures.append('an unseen key must be a miss, and a miss is None')

        cache.set_bytes(f'{prefix}:empty', b'')
        if cache.get_bytes(f'{prefix}:empty') != b'':
            failures.append('empty bytes are a value, not a miss: b"" must come back as b""')

        cache.set_bytes(f'{prefix}:binary', binary)
        if cache.get_bytes(f'{prefix}:binary') != binary:
            failures.append('values are arbitrary binary and must round-trip byte for byte')

        cache.set_bytes(f'{prefix}:over', b'first')
        cache.set_bytes(f'{prefix}:over', b'second')
        if cache.get_bytes(f'{prefix}:over') != b'second':
            failures.append('a second write to one key must replace the first')

        cache.set_bytes(f'{prefix}:Case', b'upper')
        cache.set_bytes(f'{prefix}:case', b'lower')
        if cache.get_bytes(f'{prefix}:Case') != b'upper':
            failures.append('keys are opaque: two keys differing only in case are two keys')

        return failures
```

Import `ByteCache` from `pysqlsuggestions.ports` at the top of the module, and add `CacheConformance` to `testing/__init__.py`'s import and `__all__`.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/test_cache_conformance.py -q && ./scripts/check.sh`
Expected: PASS.

```bash
git add src/pysqlsuggestions/testing tests/test_cache_conformance.py
git commit -F - <<'MSG'
feat: a conformance harness for third-party byte caches

Two method names look like the whole contract and are not. A store may conflate
a miss with empty bytes, lose the high half of a byte through a text column,
fold the case of a key, or refuse to overwrite — each silent, and each turning
correct suggestions into stale or absent ones.

Shipped in the wheel for the reason DialectConformance is: the difference
between a protocol somebody can implement and one somebody can implement
correctly. It checks itself against InMemoryByteCache, and each departure has a
test that deliberately breaks one rule, because a harness nothing can fail is
a harness that proves nothing.

Expiry is deliberately not covered: a portable check would have to sleep, and a
harness measured in seconds is one nobody runs. The shipped adapter's integration
test covers it against a server that genuinely enforces it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 10: `RedisCache`, the extra, and the purity guards

**Files:**
- Create: `src/pysqlsuggestions/caches/redis.py`
- Modify: `pyproject.toml` (the extras comment, `cache-redis`, the `dev` group)
- Modify: `tests/test_purity.py`
- Test: `tests/test_cache_redis.py`

**Interfaces:**
- Consumes: `ByteCache` (Task 7).
- Produces: `RedisCache(client: RedisClient, *, namespace: str, default_ttl: int | None = 300)`; `RedisCache.from_url(url: str, *, namespace: str, default_ttl: int | None = 300) -> RedisCache`; `RedisClient` protocol.

**Do not re-export `RedisCache` from `caches/__init__.py`.** Importing `pysqlsuggestions.caches.keys` runs the package's `__init__`, so a re-export would load the adapter on a bare `import pysqlsuggestions` and fail the guard added in Step 5. Mirror `catalogs/__init__.py`, which does the same for `trino_http` and `clickhouse_http` — read it first and follow it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_redis.py`:

```python
"""
The redis adapter, against fakeredis rather than a hand-written double.

fakeredis implements redis-py's actual semantics, so it exercises `ex`, the
bytes-versus-str return under `decode_responses`, and binary-safe keys — the
three details the adapter writes docstrings about. A twenty-line fake would
agree with those docstrings' assumptions, which is the wrong thing for a test.
"""

from __future__ import annotations

import fakeredis
import pytest

from pysqlsuggestions.caches.redis import RedisCache
from pysqlsuggestions.testing import CacheConformance


def test_it_conforms() -> None:
    """The contract, from the harness that exists to state it."""
    cache = RedisCache(fakeredis.FakeStrictRedis(), namespace='test')
    assert CacheConformance.check(cache) == []


def test_a_namespace_is_required() -> None:
    """
    Required, with no default, because the alternative is a silent cross-database read.

    Role leads the key and dialect follows it, but nothing in the key names the
    *server*. That was safe while every cache was private to one connection; a
    shared store removes the structure, and staging and production both holding
    a role called `analyst` would read each other's entries.
    """
    with pytest.raises(TypeError):
        RedisCache(fakeredis.FakeStrictRedis())  # type: ignore[call-arg]


def test_two_namespaces_do_not_see_each_other() -> None:
    """The mitigation has to actually mitigate."""
    client = fakeredis.FakeStrictRedis()
    staging = RedisCache(client, namespace='staging')
    production = RedisCache(client, namespace='production')
    staging.set_bytes('k', b'staging')
    assert production.get_bytes('k') is None


def test_a_ttl_reaches_the_server() -> None:
    """`ex` is what bounds how long an orphaned keyspace lives after an upgrade."""
    client = fakeredis.FakeStrictRedis()
    RedisCache(client, namespace='test', default_ttl=60).set_bytes('k', b'v')
    assert 0 < client.ttl('test:k') <= 60


def test_an_explicit_ttl_overrides_the_default() -> None:
    """A prewarm may deliberately want a longer life than a keystroke's read."""
    client = fakeredis.FakeStrictRedis()
    RedisCache(client, namespace='test', default_ttl=60).set_bytes('k', b'v', ttl=600)
    assert 60 < client.ttl('test:k') <= 600


def test_no_ttl_means_no_expiry() -> None:
    """Legal, and the docstring says what it costs."""
    client = fakeredis.FakeStrictRedis()
    RedisCache(client, namespace='test', default_ttl=None).set_bytes('k', b'v')
    assert client.ttl('test:k') == -1


def test_a_decoding_client_still_works() -> None:
    """
    `decode_responses=True` makes `get` return `str`.

    That is a perfectly reasonable way to have configured a client used for
    other things too, and it must not silently break this one.
    """
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    cache = RedisCache(client, namespace='test')
    cache.set_bytes('k', b'{"t":null,"v":[]}')
    assert cache.get_bytes('k') == b'{"t":null,"v":[]}'


def test_a_completion_reads_through_it() -> None:
    """End to end, since the point of the adapter is that a caret is faster."""
    from pysqlsuggestions import complete
    from pysqlsuggestions.dialects.postgres import POSTGRES
    from tests.test_complete import catalog

    sql = 'SELECT * FROM reports_report r WHERE r.'
    cache = RedisCache(fakeredis.FakeStrictRedis(), namespace='test')
    cold = [s.text for s in complete(sql, len(sql), POSTGRES, catalog(), identity='analyst')]
    complete(sql, len(sql), POSTGRES, catalog(), cache=cache, identity='analyst')
    warm = [s.text for s in complete(sql, len(sql), POSTGRES, catalog(), cache=cache, identity='analyst')]
    assert warm == cold


def test_from_url_names_the_extra_when_redis_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing extra should say which one, in the one place that can tell."""
    import builtins

    real = builtins.__import__

    def _refuse(name: str, *args: object, **kwargs: object) -> object:
        """Pretend redis is not installed."""
        if name == 'redis':
            raise ImportError('No module named redis')
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', _refuse)
    with pytest.raises(ImportError, match=r'cache-redis'):
        RedisCache.from_url('redis://localhost:6379/0', namespace='test')
```

- [ ] **Step 2: Add the dependencies**

In `pyproject.toml`, extend the comment above `[project.optional-dependencies]` — it describes catalog drivers only, and `demo` is already an exception nobody wrote down:

```toml
# Extras are named after the driver they install, not the backend: more than one
# driver can serve the same backend, so a future psycopg3 extra sits alongside
# psycopg2 rather than silently changing what `postgres` means. Anything that is
# not a catalog driver is named for its role first, so `cache-redis` and a future
# `cache-valkey` are siblings the way `psycopg2` and `pg8000` are.
```

Add to `[project.optional-dependencies]`:

```toml
cache-redis = ['redis>=3.0']
```

A floor and not a pin. `3.0` is where `set(name, value, ex=...)` landed, in 2018, and 4.x, 5.x and 6.x all still have it — but the compatibility is actually delivered by the adapter never importing redis at all.

Add to `[dependency-groups].dev`, under the existing comment about integration tests and the demo:

```toml
    'redis>=3.0',
    'fakeredis>=2.21',
```

Run `uv sync`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cache_redis.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pysqlsuggestions.caches.redis'`

- [ ] **Step 4: Write the adapter**

Create `src/pysqlsuggestions/caches/redis.py`:

```python
"""
A `ByteCache` over redis, which never imports redis.

`import redis` appears once, inside `RedisCache.from_url`, and that is the whole
reason the `cache-redis` extra exists: it is the one place a missing dependency
can be diagnosed with a sentence naming the fix.

Everywhere else the adapter duck-types a client the caller built. A version
floor is a promise about a package; a two-method contact surface is a guarantee
about the code, and this one holds across redis-py 3 through 6 and gets valkey,
`RedisCluster`, `fakeredis` and any pooling wrapper for free — none of which a
floor would have covered.
"""

from __future__ import annotations

from typing import Any, Protocol


class RedisClient(Protocol):
    """The contact surface. Two methods, because that is the whole adapter."""

    def get(self, name: str) -> bytes | str | None:
        """The stored value, or None."""
        ...

    def set(self, name: str, value: bytes, ex: int | None = None) -> object:
        """Store a value, expiring after `ex` seconds when given."""
        ...


class RedisCache:
    """
    Catalog reads in redis, addressed under a namespace the caller owns.

    `namespace` is required and has no default, because nothing in the key names
    the *server*. Role leads it and dialect follows, which was enough while every
    cache was private to one connection — a dict lives inside one session, so the
    isolation was structural. A shared store removes the structure: staging and
    production, both with a role called `analyst`, would otherwise read each
    other's entries, and since these reads are privilege-filtered the result
    looks like a database permission bug rather than a caching one.

    The contract, therefore: one namespace per database, and per identity you
    cannot name — `identity` already leads the key, so a caller passing one needs
    the namespace only to distinguish databases.

    `default_ttl` is 300 seconds rather than `None`. Every library version gets
    its own keyspace, since the key carries a fingerprint of the cached types'
    shapes, so an upgrade orphans the previous one; the TTL is what bounds how
    long the orphans live. `default_ttl=None` is legal and turns a shared cache
    into one that nothing invalidates and nothing reclaims.
    """

    def __init__(self, client: RedisClient, *, namespace: str, default_ttl: int | None = 300) -> None:
        if not namespace:
            raise ValueError('namespace must name the database this cache belongs to; see the class docstring')
        self._client = client
        self._namespace = namespace
        self._default_ttl = default_ttl

    @classmethod
    def from_url(cls, url: str, *, namespace: str, default_ttl: int | None = 300) -> RedisCache:
        """
        A cache over a client this builds. The only place the library imports redis.

        For anything else — a cluster client, a pool the application already
        owns, valkey, a wrapper — construct the client yourself and pass it to
        `RedisCache`. This exists so that a missing extra produces a sentence
        rather than a `ModuleNotFoundError` from a module the caller never named.
        """
        try:
            import redis
        except ImportError as absent:  # pragma: no cover - exercised by monkeypatching __import__
            raise ImportError(
                'RedisCache.from_url needs the redis client: pip install pysqlsuggestions[cache-redis]. '
                'Any client with get and set works if you build it yourself: RedisCache(client, namespace=...)'
            ) from absent
        client: Any = redis.Redis.from_url(url)
        return cls(client, namespace=namespace, default_ttl=default_ttl)

    def _name(self, key: str) -> str:
        """The key as this deployment addresses it."""
        return f'{self._namespace}:{key}'

    def get_bytes(self, key: str) -> bytes | None:
        """
        The stored bytes, or `None`.

        A client built with `decode_responses=True` returns `str`, which is a
        perfectly reasonable way to have configured a client used for other
        things, and must not silently break this one.
        """
        found = self._client.get(self._name(key))
        if found is None:
            return None
        return found.encode('utf-8') if isinstance(found, str) else found

    def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
        """Store bytes, expiring after `ttl` seconds or this cache's default. `ex=None` means never."""
        seconds = self._default_ttl if ttl is None else ttl
        self._client.set(self._name(key), value, ex=seconds)
```

If mypy objects that `redis` has no stubs, add to `pyproject.toml`:

```toml
[[tool.mypy.overrides]]
module = ['redis.*', 'fakeredis.*']
ignore_missing_imports = true
```

- [ ] **Step 5: Add the purity guards**

In `tests/test_purity.py`, add `'redis'` to `DRIVERS`, and add a test next to `test_import_pulls_in_no_catalog_readers`:

```python
def test_import_pulls_in_no_cache_adapter() -> None:
    """
    `pysqlsuggestions.caches.redis` must not load on a bare import.

    It takes no dependency — it duck-types a client rather than importing one —
    so neither of the guards above can see it. What this actually pins is that
    `caches/__init__.py` does not re-export it: importing any submodule of that
    package runs its `__init__`, so a convenience re-export would drag the
    adapter into every import of the key encoder, which `resolve.py` does.
    """
    code = 'import sys, pysqlsuggestions; print(" ".join(sorted(sys.modules)))'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert 'pysqlsuggestions.caches.redis' not in set(result.stdout.split())
```

- [ ] **Step 6: Run and commit**

Run: `uv run pytest tests/test_cache_redis.py tests/test_purity.py -q && ./scripts/check.sh`
Expected: PASS.

```bash
git add src/pysqlsuggestions/caches/redis.py pyproject.toml uv.lock tests/test_cache_redis.py tests/test_purity.py
git commit -F - <<'MSG'
feat: a redis cache, in an extra, that never imports redis

`import redis` appears once, inside `from_url`, which is the whole reason the
`cache-redis` extra exists — the one place a missing dependency can be diagnosed
with a sentence naming the fix. Everywhere else the adapter duck-types a client
the caller built.

That is what actually delivers the compatibility. A version floor is a promise
about a package; a two-method contact surface is a guarantee about the code, and
this one holds across redis-py 3 through 6 and gets valkey, RedisCluster,
fakeredis and any pooling wrapper for free — none of which `redis>=3.0` would
have covered.

`namespace` is required with no default. Nothing in the key names the server:
role leads it and dialect follows, which was enough while every cache was
private to one connection, because a dict lives inside one session and the
isolation was structural. A shared store removes the structure, and since these
reads are privilege-filtered, staging and production sharing a role name would
produce something that reads as a database permission bug.

The default TTL is 300 rather than None. Each library version gets its own
keyspace, so an upgrade orphans the previous one, and the TTL is what bounds how
long the orphans live.

Tested against fakeredis rather than a hand-written double: it implements
redis-py's real semantics, so it exercises `ex`, the str return under
decode_responses, and binary-safe keys — the three things the docstrings claim.
A hand-written fake would only have agreed with those claims.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 11: A real redis, and the two things a fake cannot prove

**Files:**
- Modify: `docker/docker-compose.yml`
- Create: `tests/integration/test_redis_cache.py`

- [ ] **Step 1: Add the service**

In `docker/docker-compose.yml`, following the shape of the existing services (including their healthcheck and `--wait` compatibility):

```yaml
  redis:
    image: redis:7-alpine
    ports:
      - '6379:6379'
    healthcheck:
      test: ['CMD', 'redis-cli', 'ping']
      interval: 2s
      timeout: 2s
      retries: 15
```

- [ ] **Step 2: Write the test**

Create `tests/integration/test_redis_cache.py`:

```python
"""
What an in-process fake structurally cannot prove.

Two things, and only two: that a *server* enforces the expiry, and that a client
whose socket has gone latches the cache off rather than raising into a
completion. The second is the failure this whole design is built around, and it
deserves to be proven against something that can genuinely be unplugged.

Everything else about the adapter is covered in the fast suite against fakeredis.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions import complete
from pysqlsuggestions.caches.redis import RedisCache
from pysqlsuggestions.dialects.postgres import POSTGRES
from tests.test_complete import catalog

pytestmark = pytest.mark.integration

SQL = 'SELECT * FROM reports_report r WHERE r.'


@pytest.fixture
def cache() -> RedisCache:
    """A cache over the compose service, skipping when it is not up."""
    redis = pytest.importorskip('redis')
    client = redis.Redis.from_url('redis://localhost:6379/0', socket_connect_timeout=1)
    try:
        client.ping()
    except Exception as unreachable:  # noqa: BLE001
        pytest.skip(f'redis is not reachable: {unreachable}')
    client.flushdb()
    return RedisCache(client, namespace='pysqlsuggestions-test', default_ttl=60)


def test_a_completion_reads_through_a_real_server(cache: RedisCache) -> None:
    """The whole point, over a socket."""
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=cache, identity='analyst')
    warm = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=cache, identity='analyst')]
    assert warm == cold


def test_the_server_enforces_the_expiry(cache: RedisCache) -> None:
    """
    fakeredis agrees with our reading of `ex`; a server is what settles it.

    The TTL is what bounds how long a previous library version's orphaned
    keyspace survives, so an `ex` the server ignored would be an unbounded leak
    nobody would notice until the store filled.
    """
    cache.set_bytes('expiring', b'v', ttl=30)
    remaining = cache._client.ttl('pysqlsuggestions-test:expiring')  # noqa: SLF001
    assert 0 < remaining <= 30


def test_a_dead_socket_costs_suggestions_and_not_the_completion(cache: RedisCache) -> None:
    """
    The failure the latch exists for, against a socket that is genuinely gone.

    Warm the cache, then point the same adapter at a port with nothing behind
    it. A completion must still answer, with what it would have answered cold.
    """
    redis = pytest.importorskip('redis')
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=cache, identity='analyst')
    cache._client = redis.Redis.from_url('redis://localhost:6399/0', socket_connect_timeout=1)  # noqa: SLF001
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    found = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=cache, identity='analyst')]
    assert found == cold
```

- [ ] **Step 3: Run it both ways**

Run: `uv run pytest -m 'not integration' -q` — expected PASS, with these skipped by the marker.
Run: `docker compose -f docker/docker-compose.yml up -d --wait redis && uv run pytest tests/integration/test_redis_cache.py -q` — expected PASS.
Run: `docker compose -f docker/docker-compose.yml down -v && uv run pytest tests/integration/test_redis_cache.py -q` — expected SKIPPED, never failed. Integration tests in this repo skip when their backend is unreachable; one that fails instead would break every checkout without docker.

- [ ] **Step 4: Commit**

```bash
git add docker/docker-compose.yml tests/integration/test_redis_cache.py
git commit -F - <<'MSG'
test: the two things about redis a fake cannot prove

fakeredis carries the semantics and covers the adapter in the fast suite. Two
claims are outside what any in-process fake can settle, and both are load-bearing.

That a server enforces the expiry: the TTL is what bounds how long a previous
library version's orphaned keyspace survives an upgrade, so an `ex` the server
quietly ignored would be an unbounded leak nobody notices until the store fills.

That a client whose socket has gone latches the cache off instead of raising
into a completion. That is the failure the whole design is built around, and
proving it wants something that can genuinely be unplugged — here, the same
adapter repointed at a port with nothing behind it.

Skips rather than fails when redis is not up, like the other three backends: a
checkout without docker must stay green.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 12: Documentation

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `docs/gaps.md`

- [ ] **Step 1: `docs/gaps.md`**

Add three numbered gaps, in the register the existing ones use — measurements or a concrete failure, the shapes considered, and why each is a feature rather than a repair.

1. **Caching `all_columns`.** Prefix-independent and the single most valuable thing a shared cache could hold. Blocked by the rule this release introduced: it returns `Sequence[Column] | None`, where `None` is a real answer meaning "too many to enumerate", and `None` is also how a miss is spelled. Closing it needs a sentinel or a value envelope, which is a different design from the one that shipped.
2. **Whether `identity` belongs in the key at all.** It has led the key since v0.1 on an argument, and it is optional — `server.py:241` passes `self.profile.user if self.profile else None`, and `Profile.user` is itself `str | None`, so peer auth or a bare DSN has none to pass. Sharing a store makes the argument load-bearing rather than precautionary. The two mechanisms considered: making it required on `complete`, which removes the hazard by construction and is a second breaking change for an unsettled question; and a `SupportsIdentity` capability reading the session role, which is the most correct source and reintroduces the same problem one level down, since something still has to happen when the capability is absent. What shipped instead is the scoping contract on `ByteCache`.
3. **Batch reads.** `_Reader` discovers its keys as the request resolves — nothing knows it needs `columns(schema, table)` until scope resolution has named the relation — so batching means restructuring it into a plan-then-execute pass. At roughly six distinct reads per completion, with `_memo` already collapsing repeats, the win is one round trip against six, which matters at twenty milliseconds of latency and not at one.

- [ ] **Step 2: `README.md`**

Update wherever the cache is described: the two protocols, that a dict no longer satisfies either, `MemoryCache` as its replacement, and `pip install pysqlsuggestions[cache-redis]` with a four-line `RedisCache` example. Include the sharing contract — one namespace per database, per identity you cannot name — because a README example is what people copy.

- [ ] **Step 3: `CHANGELOG.md`**

A `0.9.0` section, grouped by what changes at a caret, which is this release's whole point: **nothing does**. Say that plainly, then the breaking entry naming `MemoryCache` and the `TypeError`, then what is new — the two protocols, `cache_key`, the codec, `RedisCache`, `CacheConformance`, `InMemoryByteCache`.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md README.md docs/gaps.md
git commit -F - <<'MSG'
docs: what the cache release changes at a caret, which is nothing

The changelog is grouped by what moves at a caret and this release moves
nothing, which is worth saying rather than leaving a reader to infer. What
changes is who can hold the answers.

Three gaps go on the numbered list, each a feature with a reason rather than a
defect. Caching `all_columns` is blocked by the rule this release introduced —
it answers `None` for "too many to enumerate", and `None` is also how a miss is
spelled. Whether `identity` belongs in the key is the question deferred out of
this design, with both mechanisms considered and why neither shipped. Batch
reads need `_Reader` restructured into a plan-then-execute pass, because it
discovers its keys as the request resolves.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 13: `0.9.0`

**Files:**
- Modify: `pyproject.toml`, `src/pysqlsuggestions/__init__.py`, `lsp/pyproject.toml` (version *and* the `pysqlsuggestions==` pin), `lsp/pysqlsuggestions_lsp/__init__.py`, `editors/vscode/package.json`

- [ ] **Step 1: Bump every declaration**

`test_purity.py` checks the root against four of them, and `test_the_server_pins_the_library_release_it_belongs_to` checks the pin. `editors/vscode/package-lock.json` holds the version twice and also holds a dependency's node engine range that looks like a version — bumping that file by search-and-replace is how this goes wrong. Change the two `"version"` fields belonging to this package only, or regenerate with `npm install --package-lock-only`.

- [ ] **Step 2: Verify**

Run: `./scripts/check.sh`
Expected: PASS, including all five version guards.
Run: `cd editors/vscode && npm run check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -F - <<'MSG'
chore: 0.9.0

A minor bump carrying one breaking change, which pre-1.0 is what minor is for.
Nothing answers differently at a caret; what changes is where the answers can be
kept. A plain dict no longer satisfies `Cache` and `complete` says so rather
than silently caching nothing — the one thing an upgrade has to act on, and the
reason it raises is that the alternative failure is invisible.

The version lives in six files and `test_purity` guards five of them against the
root. `package-lock.json` holds it twice and also holds a dependency's node
engine range that reads like a version; bumping that file by search and replace
is how this goes wrong.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```
