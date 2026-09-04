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
    return Function(schema=raw['schema'], name=raw['name'], args=raw['args'], result=raw['result'], kind=raw['kind'])


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


def _identity(row: Any) -> Any:
    """A bare string, which needs no reshaping in either direction."""
    return row


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
    'str': _identity,
    'Column': _from_column,
    'ColumnValue': _from_column_value,
    'ForeignKey': _from_foreign_key,
    'Function': _from_function,
    'Table': _from_table,
}

_DECODERS: dict[str, Any] = {
    'str': _identity,
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
        # `str(...)` because `type.__mro__` is `type[Any]`, so `__name__` reads as Any to mypy.
        if TAGS.get(candidate.__name__) is candidate:
            return str(candidate.__name__)
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
    encoder = _identity if tag is None else _ENCODERS[tag]
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
