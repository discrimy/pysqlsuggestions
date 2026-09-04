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

from pysqlsuggestions.ports import ByteCache


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
