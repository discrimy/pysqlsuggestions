"""
The cache a process keeps to itself.

An `ObjectCache`, so nothing is serialised: `lsp/` holds one of these per
session and reads it on every keystroke, and charging that path an encode and a
decode to reach a dict in the same process would be paying for a boundary that
is not there.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CacheStats:
    """
    What one `MemoryCache` has done since the process started, as of one moment.

    A snapshot rather than a live view: frozen, and read under the lock, so two
    numbers a caller reports on adjacent lines came from the same instant and
    cannot disagree with each other.

    `expiries` is a subset of `misses` rather than a sibling of it. A read that
    found an expired entry returned `None` and the caller re-read, so the hit
    rate has to count it as a miss — but a cold miss and a five-minute expiry say
    opposite things about whether `default_ttl` is right, and one counter cannot
    separate them.
    """

    hits: int
    misses: int
    expiries: int
    evictions: int
    entries: int
    """How many entries are held now, including any that have expired unread."""
    maxsize: int | None
    """The bound, or `None`. Reported so that `entries` can be read against something."""


class MemoryCache:
    """
    Catalog reads kept in a bounded LRU, with an expiry, safe to share between threads.

    `maxsize` counts *entries*, not bytes, and the difference is worth stating
    because one entry can be a fifty-thousand-row column list. What this bounds
    is the number of distinct namespace paths a long-lived session accumulates —
    `columns` is keyed per relation and `values` per column, so a session
    browsing a warehouse acquires entries for as long as somebody keeps typing.
    The size of any single read is the catalog's shape, and no knob here helps
    with that.

    0.9.0 shipped this unbounded, arguing that entries were bounded by the size
    of the catalog times the number of roles a process serves. That argument is
    still true and is still not a bound: it says the ceiling exists, not that it
    is small, and a warehouse's ceiling is far above what an editor plugin should
    be free to reach. `maxsize=None` keeps the old behaviour, for a catalog whose
    size you know.

    `default_ttl` is 300 seconds rather than `None`, matching `RedisCache`. The
    alternative is what 0.9.0 did: an editor session that never sees a
    `CREATE TABLE` until somebody restarts it, since nothing in this process ever
    hears about DDL. Five minutes is invisible to a person — `_Reader._memo`
    already collapses repeats inside one request, so an expiry costs at most one
    query per read kind per completion. `default_ttl=None` is still legal and
    means an entry lives as long as the process; it is now a choice rather than
    the default.

    Expired entries that nobody reads again are reclaimed by eviction, not by a
    sweeper. A sweeper needs a thread, and a library that starts one inside
    somebody's editor process is a library that has overstepped.
    """

    def __init__(self, default_ttl: int | None = 300, maxsize: int | None = 1024) -> None:
        if maxsize is not None and maxsize < 1:
            raise ValueError('maxsize must hold at least one entry, or be None for no bound')
        self._entries: OrderedDict[str, tuple[float | None, Any]] = OrderedDict()
        self._default_ttl = default_ttl
        self._maxsize = maxsize
        self._hits = 0
        self._misses = 0
        self._expiries = 0
        self._evictions = 0
        self._lock = threading.Lock()
        """
        Plain rather than reentrant, which is a claim and not a default.

        The claim is that nothing under this lock runs code the caller wrote: the
        methods never call each other, the keys are `str` so a dict lookup cannot
        reach a caller's `__hash__`, and the two paths that could have — the
        clock, and the finalizer of an evicted value — are kept outside it
        deliberately, below.

        A plain lock turns any later breach of that claim into a deadlock a test
        catches. An `RLock` would let the same breach through silently, and what
        it would let through is a read of a cache midway through an eviction,
        which is worse than the hang. `lsp/server.py` holds an `RLock` for a
        reason it states — `catalog()` and `degrade()` are public and are also
        reached from inside its locked region — and there is no such path here.
        """
        self._clock = time.monotonic
        """
        Monotonic rather than wall.

        A cache that forgot everything because an NTP daemon stepped the clock
        backwards would be an impossible bug to report, and expiry only ever
        needs elapsed time.

        Read before the lock is taken, never inside it. This is an instance
        attribute and the tests replace it, so it is caller-supplied code, and a
        caller whose clock touched this cache would deadlock against a plain
        lock.
        """

    def get(self, key: str) -> Any | None:
        """The cached value, or `None` for a miss or an expired entry."""
        now = self._clock()
        expired: Any = None
        result: Any = None
        with self._lock:
            found = self._entries.get(key)
            if found is None:
                self._misses += 1
            else:
                expires, value = found
                if expires is not None and now >= expires:
                    expired = self._entries.pop(key, None)
                    self._expiries += 1
                    self._misses += 1
                else:
                    self._entries.move_to_end(key)
                    self._hits += 1
                    result = value
        del expired
        return result

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """
        Store a value, expiring after `ttl` seconds or the cache's default.

        The evicted entry is held in a local until the lock is released and only
        then dropped. `ObjectCache.set` types the value `Any`, so its `__del__`
        is arbitrary code and it runs on this thread. CPython has the same
        problem in `functools.lru_cache` and does not solve it with the `RLock`
        it is already holding: it keeps a reference until the links are
        consistent, to stop "potentially arbitrary object clean-up code" running
        mid-update. This does the same, for the same reason.
        """
        seconds = self._default_ttl if ttl is None else ttl
        now = self._clock()
        evicted: Any = None
        with self._lock:
            self._entries[key] = (None if seconds is None else now + seconds, value)
            self._entries.move_to_end(key)
            if self._maxsize is not None and len(self._entries) > self._maxsize:
                evicted = self._entries.popitem(last=False)
                self._evictions += 1
        del evicted

    def delete(self, key: str) -> None:
        """
        Drop one entry, whether or not it is there.

        The key is one built by `pysqlsuggestions.caches.cache_key`, which is how
        a caller who has just run DDL invalidates exactly the read that went
        stale. Deliberately not a prefix: matching on part of the key would make
        its grammar a format, and `caches.keys` says at length why it must not
        become one.

        Idempotent, because invalidation runs where nobody knows what was read.
        """
        with self._lock:
            dropped = self._entries.pop(key, None)
        del dropped

    def clear(self) -> None:
        """
        Drop everything.

        The blunt instrument, and the one that needs no knowledge of the key at
        all — what a caller reaches for when the schema has moved under them and
        naming each stale read would be its own project.

        The mapping is replaced rather than emptied, so that neither the clearing
        nor the finalizer of anything it held happens inside the lock.
        """
        with self._lock:
            dropped = self._entries
            self._entries = OrderedDict()
        del dropped

    def stats(self) -> CacheStats:
        """
        Everything this cache has done, as of now.

        The counters are the process's lifetime record and no method resets them.
        `clear()` invalidates entries, which is not a reason to lose the hit rate
        that says whether the cache was earning its memory.
        """
        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                expiries=self._expiries,
                evictions=self._evictions,
                entries=len(self._entries),
                maxsize=self._maxsize,
            )
