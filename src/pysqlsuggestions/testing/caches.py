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
