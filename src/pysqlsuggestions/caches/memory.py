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
