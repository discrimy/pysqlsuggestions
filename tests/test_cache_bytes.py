"""The byte discipline, exercised without a socket."""

from __future__ import annotations

from pysqlsuggestions import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.testing import InMemoryByteCache
from pysqlsuggestions.types import ForeignKey, Function


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


def _catalog_answering_everything() -> MemoryCatalog:
    """
    A fixture that can answer all six cached reads.

    `MemoryCatalog` implements every capability, so the only thing needed is
    content for each: two relations to constrain, a declared edge between them,
    a function to offer, and statistics on one column.
    """
    return MemoryCatalog(
        {
            ('public', 'users'): [('id', 'bigint'), ('status', 'text')],
            ('public', 'orders'): [('id', 'bigint'), ('user_id', 'bigint')],
        },
        functions=[Function(schema='public', name='lower', args='text', result='text')],
        values={('public', 'users', 'status'): ['active', 'archived']},
        foreign_keys=[
            ForeignKey(
                schema='public',
                table='orders',
                columns=('user_id',),
                ref_schema='public',
                ref_table='users',
                ref_columns=('id',),
            )
        ],
    )


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
    for sql, caret in (
        ('SELECT * FROM ', None),
        ('SELECT * FROM public.', None),
        ('SELECT * FROM users u WHERE u.', None),
        ('SELECT * FROM users u WHERE u.status = ', None),
        ('SELECT * FROM users u JOIN ', None),
        ('SELECT  FROM users u', 7),
    ):
        at = len(sql) if caret is None else caret
        complete(sql, at, POSTGRES, catalog, cache=encoded, identity='analyst')
    kinds = {key.split(':')[4] for key in encoded.writes}
    assert kinds == {'schemas', 'tables', 'columns', 'functions', 'values', 'fk'}
