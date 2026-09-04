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
