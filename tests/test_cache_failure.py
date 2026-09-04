"""
A cache is an optimisation, and an optimisation may not fail a completion.

`resolve.py` says for every capability what happens when it is absent. `Cache`
had no such paragraph because a dict cannot fail; a store on the other side of a
socket can be down, slow, or hand back something foreign.
"""

from __future__ import annotations

from typing import Any

import pytest

from pysqlsuggestions import complete
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.testing import InMemoryByteCache
from tests.test_complete import catalog

SQL = 'SELECT * FROM reports_report r WHERE r.'


class _Unreachable:
    """A cache whose every read raises, the way a store with no route does."""

    def __init__(self) -> None:
        self.reads = 0

    def get(self, key: str) -> Any | None:
        """Count the attempt, then fail like a socket with nowhere to go."""
        del key
        self.reads += 1
        raise ConnectionError('no route to host')

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Never reached; the read fails first."""
        del key, value, ttl
        raise ConnectionError('no route to host')


class _WriteOnlyFailure:
    """A cache that reads cleanly and cannot be written to, the way a full store behaves."""

    def get(self, key: str) -> Any | None:
        """Always a miss."""
        del key
        return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Refuse the write."""
        del key, value, ttl
        raise OSError('OOM command not allowed when used memory > maxmemory')


def test_a_failing_read_does_not_fail_the_completion() -> None:
    """The documented degradation for every other capability, extended to this one."""
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    found = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=_Unreachable(), identity='analyst')]
    assert found == cold


def test_a_failing_cache_is_asked_once_per_request() -> None:
    """
    The latch, which is the part that matters.

    With a two-second socket timeout an unlatched store costs one timeout per
    read rather than one per request — six times slower than having no cache at
    all, and indistinguishable from the engine hanging.
    """
    unreachable = _Unreachable()
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=unreachable, identity='analyst')
    assert unreachable.reads == 1


def test_a_failing_write_does_not_fail_the_completion() -> None:
    """A store that cannot accept a value is a store that cannot cache, not an error."""
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    found = [
        s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=_WriteOnlyFailure(), identity='analyst')
    ]
    assert found == cold


def test_a_plain_dict_is_refused_at_the_door() -> None:
    """
    Loud, because the alternative is invisible.

    A dict has `.get` and no `.set`, so it satisfies neither protocol. Treating
    "neither" as "no cache" would leave every caller written against the old
    port correct, silent and uncached — for as long as it took somebody to
    notice completions had got slower.
    """
    with pytest.raises(TypeError, match='MemoryCache'):
        # Ignored because mypy is right: this is what the raise exists to catch at
        # runtime, for the callers who are not type-checked. The test is the raise.
        complete(SQL, len(SQL), POSTGRES, catalog(), cache={}, identity='analyst')  # type: ignore[arg-type]


def test_a_byte_cache_serves_the_same_suggestions() -> None:
    """The discipline is a storage decision and must not be an answer decision."""
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    encoded = InMemoryByteCache()
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=encoded, identity='analyst')
    warm = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=encoded, identity='analyst')]
    assert warm == cold


def test_an_undecodable_value_is_a_miss_and_does_not_latch() -> None:
    """
    One foreign value under our namespace must not disable the other five reads.

    A decode failure is not a transport failure. It most likely means somebody
    else's key, and punishing the rest of the request for it would be answering
    the wrong question.

    A JOIN caret rather than `SQL`, because it is the shape that makes three
    distinct reads — the constraints, the relations and the namespaces. One read
    could not tell a latch from a miss: both leave the second read unmade.
    """
    joining = 'SELECT * FROM reports_report r JOIN '
    encoded = InMemoryByteCache()
    complete(joining, len(joining), POSTGRES, catalog(), cache=encoded, identity='analyst')
    stored = list(encoded.writes)
    assert len(stored) > 1, 'the fixture stopped making several reads; this test needs them'
    for key in stored:
        encoded.set_bytes(key, b'not json at all')
    cold = [s.text for s in complete(joining, len(joining), POSTGRES, catalog(), identity='analyst')]
    encoded.writes.clear()
    found = [s.text for s in complete(joining, len(joining), POSTGRES, catalog(), cache=encoded, identity='analyst')]
    assert found == cold
    assert encoded.writes == stored, 'a decode failure latched the cache off; it should only be a miss'
