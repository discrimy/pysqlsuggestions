"""The in-process cache: what `lsp/` and both demos hold."""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator

import pytest

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


def test_an_entry_lives_as_long_as_the_process_when_the_ttl_is_none() -> None:
    """Opt-in from 0.10.0, and the whole behaviour before it. Legal, and a choice somebody has to make."""
    now = [1000.0]
    cache = MemoryCache(default_ttl=None)
    cache._clock = lambda: now[0]  # noqa: SLF001
    cache.set('k', 'v')
    now[0] = 1_000_000.0
    assert cache.get('k') == 'v'


def test_the_least_recently_used_entry_goes_when_the_bound_is_reached() -> None:
    """A bound nobody can exceed is what makes this safe to hold for a session's lifetime."""
    cache = MemoryCache(maxsize=2)
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('c', 3)
    assert cache.get('a') is None
    assert cache.get('b') == 2
    assert cache.get('c') == 3


def test_a_read_makes_an_entry_the_newest() -> None:
    """
    Recency, not insertion order.

    The distinction is the whole reason for an LRU here: `tables(None)` is read
    by nearly every completion and inserted once, so a FIFO would evict the
    hottest entry in the cache first.
    """
    cache = MemoryCache(maxsize=2)
    cache.set('a', 1)
    cache.set('b', 2)
    cache.get('a')
    cache.set('c', 3)
    assert cache.get('a') == 1
    assert cache.get('b') is None


def test_an_overwrite_does_not_grow_the_cache() -> None:
    """A warm entry re-read after DDL replaces one entry; it must not consume a second slot."""
    cache = MemoryCache(maxsize=2)
    cache.set('a', 1)
    cache.set('a', 2)
    cache.set('b', 3)
    assert cache.get('a') == 2
    assert cache.get('b') == 3


def test_an_overwrite_makes_an_entry_the_newest() -> None:
    """Writing to a key is using it, so it goes to the back of the eviction queue like a read."""
    cache = MemoryCache(maxsize=2)
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('a', 3)
    cache.set('c', 4)
    assert cache.get('a') == 3
    assert cache.get('b') is None


def test_without_a_bound_nothing_is_evicted() -> None:
    """`maxsize=None` is the unbounded dict 0.9.0 shipped, kept legal for a catalog you know."""
    cache = MemoryCache(maxsize=None)
    for index in range(5000):
        cache.set(str(index), index)
    assert cache.get('0') == 0
    assert cache.get('4999') == 4999


def test_an_entry_expires_after_five_minutes_by_default() -> None:
    """
    The default is an expiry, because the alternative is a session that never sees a CREATE TABLE.

    Five minutes matches `RedisCache` and is invisible to a person: `_Reader._memo`
    collapses repeats inside one request, so an expiry costs at most one query per
    read kind per completion.
    """
    now = [1000.0]
    cache = MemoryCache()
    cache._clock = lambda: now[0]  # noqa: SLF001
    cache.set('k', 'v')
    now[0] = 1299.0
    assert cache.get('k') == 'v'
    now[0] = 1301.0
    assert cache.get('k') is None


def test_a_bound_below_one_is_refused() -> None:
    """`maxsize=0` is a cache that stores nothing while looking like a cache. Say so at construction."""
    with pytest.raises(ValueError, match='maxsize'):
        MemoryCache(maxsize=0)


def test_clear_drops_everything() -> None:
    """What a caller reaches for after DDL. It needs to know nothing about the key to use it."""
    cache = MemoryCache()
    cache.set('a', 1)
    cache.set('b', 2)
    cache.clear()
    assert cache.get('a') is None
    assert cache.get('b') is None


def test_delete_drops_one_entry_and_leaves_the_rest() -> None:
    """
    Targeted invalidation, addressed by a key from `cache_key` rather than by a prefix.

    Prefix matching would make the key grammar a format, which `caches.keys`
    spends a docstring refusing to let it become. Building the exact key with the
    supported function costs the caller a line and keeps the grammar ours.
    """
    cache = MemoryCache()
    cache.set('a', 1)
    cache.set('b', 2)
    cache.delete('a')
    assert cache.get('a') is None
    assert cache.get('b') == 2


def test_deleting_an_absent_key_is_not_an_error() -> None:
    """Invalidation runs after DDL, where the caller has no idea what was ever read. It must be idempotent."""
    cache = MemoryCache()
    cache.delete('never-seen')


def test_hits_and_misses_are_counted() -> None:
    """A cache with no numbers is a cache nobody can tell is working."""
    cache = MemoryCache()
    cache.set('a', 1)
    cache.get('a')
    cache.get('a')
    cache.get('b')
    stats = cache.stats()
    assert (stats.hits, stats.misses) == (2, 1)


def test_an_expiry_counts_as_both_an_expiry_and_a_miss() -> None:
    """
    Both, and the reason is that they answer different questions.

    The caller got `None` and will re-read, so it is a miss and the hit rate must
    say so. But a miss that was a cold key and a miss that was a five-minute
    expiry mean opposite things about the TTL, and one counter cannot separate
    them.
    """
    now = [1000.0]
    cache = MemoryCache(default_ttl=10)
    cache._clock = lambda: now[0]  # noqa: SLF001
    cache.set('a', 1)
    now[0] = 1011.0
    assert cache.get('a') is None
    stats = cache.stats()
    assert (stats.expiries, stats.misses, stats.hits) == (1, 1, 0)


def test_evictions_are_counted() -> None:
    """The number that says `maxsize` is too small for this catalog, which nothing else would reveal."""
    cache = MemoryCache(maxsize=2)
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('c', 3)
    cache.set('d', 4)
    assert cache.stats().evictions == 2


def test_stats_reports_the_current_size_and_the_bound() -> None:
    """Together they are the answer to "is this cache full", which is the question before every other one."""
    cache = MemoryCache(maxsize=8)
    cache.set('a', 1)
    stats = cache.stats()
    assert (stats.entries, stats.maxsize) == (1, 8)


def test_stats_is_a_snapshot_rather_than_a_view() -> None:
    """Read under the lock and frozen, so a number cannot change between two lines of a caller's report."""
    cache = MemoryCache()
    before = cache.stats()
    cache.set('a', 1)
    cache.get('a')
    assert (before.hits, before.entries) == (0, 0)


def test_clearing_the_cache_does_not_clear_the_counters() -> None:
    """The counters are the process's lifetime record. An invalidation is not a reason to lose the hit rate."""
    cache = MemoryCache()
    cache.set('a', 1)
    cache.get('a')
    cache.clear()
    assert cache.stats().hits == 1


@pytest.fixture
def contended() -> Iterator[None]:
    """
    A scheduler that preempts often enough for a narrow race to be reachable.

    Measured, because the first version of these tests passed against the
    unlocked class and proved nothing: at the default 5 ms switch interval the
    `KeyError` below never appeared in 40 000 operations, and at 10 us it appeared
    on every run of 4 000. The window is a few bytecodes wide, so a test that does
    not narrow the interval is testing the scheduler rather than the cache.
    """
    before = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    try:
        yield
    finally:
        sys.setswitchinterval(before)


def _hammer(cache: MemoryCache, rounds: int, failures: list[BaseException]) -> None:
    """Set and read a small key space, recording anything that escapes. Shared by the concurrency tests."""
    try:
        for round_number in range(rounds):
            key = str(round_number % 4)
            cache.set(key, round_number)
            cache.get(key)
    except BaseException as escaped:  # noqa: BLE001
        failures.append(escaped)


def test_concurrent_readers_and_writers_do_not_raise(contended: None) -> None:
    """
    The defect this class shipped with: `get` checks expiry and then deletes, and two
    threads through that window race into `KeyError`.

    `ttl=0` makes every entry expire the instant it is written, so every read
    takes the deleting branch. `resolve.py` would swallow the `KeyError` and latch
    `_failed`, silently dropping caching for the rest of the request — so nothing
    downstream would ever report this.
    """
    cache = MemoryCache(default_ttl=0)
    failures: list[BaseException] = []
    threads = [threading.Thread(target=_hammer, args=(cache, 500, failures)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not failures, failures


def test_the_bound_is_never_exceeded_even_briefly(contended: None) -> None:
    """
    A bound only holds if no thread can ever observe it broken.

    Unlocked, `set` inserts and only then evicts, so N concurrent writers push the
    cache to `maxsize + N` before the pops catch up — measured at a peak of 9 and
    10 against a bound of 4, on every run. The transient is the whole problem: an
    entry here can be a fifty-thousand-row column list, so overshooting by the size
    of the thread pool is real memory, and it arrives exactly when the process is
    busiest.

    Sampled through `stats()` rather than the private dict, because that is the
    surface a deployment watches, and it is the one that must never report a lie.
    """
    cache = MemoryCache(default_ttl=None, maxsize=4)
    stop = threading.Event()
    peak = [0]

    def write() -> None:
        for round_number in range(3000):
            cache.set(str(round_number % 32), round_number)

    def watch() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], cache.stats().entries)

    writers = [threading.Thread(target=write) for _ in range(8)]
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join(timeout=30)
    stop.set()
    watcher.join(timeout=5)
    assert peak[0] <= 4


def test_the_clock_is_read_outside_the_critical_section() -> None:
    """
    `_clock` is an instance attribute callers replace — these tests replace it on
    nearly every line above — so it is caller-supplied code, and running it inside
    the lock would deadlock anyone whose clock touched the cache.

    The check only means anything against a non-reentrant lock: an `RLock` grants
    a second acquisition to the thread already holding it, so this would pass
    without proving a thing.
    """
    cache = MemoryCache()
    acquired: list[bool] = []

    def clock() -> float:
        free = cache._lock.acquire(blocking=False)  # noqa: SLF001
        acquired.append(free)
        if free:
            cache._lock.release()  # noqa: SLF001
        return 1000.0

    cache._clock = clock  # noqa: SLF001
    cache.set('k', 'v')
    cache.get('k')
    assert acquired
    assert all(acquired)


def test_an_evicted_value_is_dereferenced_outside_the_critical_section() -> None:
    """
    Evicting drops the last reference to a caller's object, and `ObjectCache.set`
    types that object `Any` — so its `__del__` is arbitrary code, and it runs on
    this thread.

    CPython has the same problem in `functools.lru_cache` and does not solve it
    with the `RLock` it is already holding; it keeps a reference until the links
    are consistent. This does the same, because a finalizer re-entering under a
    reentrant lock would read a cache mid-eviction rather than deadlock on it.

    Assumes the eviction is what drops the last reference, which is refcounting
    and so CPython. `pyproject.toml` claims 3.10 through 3.12 and nothing else;
    on an implementation that collects later this would need a `gc.collect()`.
    """
    cache = MemoryCache(maxsize=1)
    acquired: list[bool] = []

    class Tracked:
        """A value that notices when it is collected."""

        def __del__(self) -> None:
            """Record whether the cache's lock was free at the moment of collection."""
            free = cache._lock.acquire(blocking=False)  # noqa: SLF001
            acquired.append(free)
            if free:
                cache._lock.release()  # noqa: SLF001

    cache.set('a', Tracked())
    cache.set('b', 'plain')
    assert acquired == [True]
