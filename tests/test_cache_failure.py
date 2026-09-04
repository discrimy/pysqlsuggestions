"""
A cache is an optimisation, and an optimisation may not fail a completion.

`resolve.py` says for every capability what happens when it is absent. `Cache`
had no such paragraph because a dict cannot fail; a store on the other side of a
socket can be down, slow, or hand back something foreign.
"""

from __future__ import annotations

from typing import Any

from pysqlsuggestions import complete
from pysqlsuggestions.dialects.postgres import POSTGRES
from tests.test_complete import catalog

SQL = 'SELECT * FROM reports_report r WHERE r.'


class _Unreachable:
    """A cache whose every read raises, the way a store with no route does."""

    def __init__(self) -> None:
        self.reads = 0

    def get(self, key: Any, default: Any = None) -> Any:
        """Count the attempt, then fail like a socket with nowhere to go."""
        del key, default
        self.reads += 1
        raise ConnectionError('no route to host')

    def __setitem__(self, key: Any, value: Any) -> None:
        """Never reached; the read fails first."""
        raise ConnectionError('no route to host')


class _WriteOnlyFailure:
    """A cache that reads cleanly and cannot be written to, the way a full store behaves."""

    def get(self, key: Any, default: Any = None) -> Any:
        """Always a miss."""
        del key
        return default

    def __setitem__(self, key: Any, value: Any) -> None:
        """Refuse the write."""
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
