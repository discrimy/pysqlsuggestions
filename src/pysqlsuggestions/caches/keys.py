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

ReadKind = Literal['schemas', 'tables', 'columns', 'functions', 'values', 'fk']
"""
Which read a key belongs to.

`ReadKind` rather than `Kind`, which `types` already uses for what a candidate
is. One library with two `Kind`s would be a shadowed import waiting to happen —
`resolve.py` imports both modules.

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


def cache_key(identity: str | None, dialect: str, kind: ReadKind, *parts: str | None) -> str:
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
