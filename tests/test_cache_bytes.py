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
