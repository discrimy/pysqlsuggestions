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
    restricted = Column(
        schema='public', table='users', name='password', type='text', availability=Availability.RESTRICTED
    )
    decoded = codec.decode(codec.encode([restricted]))[0]
    assert isinstance(decoded, Column)
    assert decoded.availability is Availability.RESTRICTED
