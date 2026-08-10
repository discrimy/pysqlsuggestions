"""
Behaviour on inputs far larger than a hand-written query.

A generated report query runs to hundreds of joins, and a caret sits in one of
these on every keystroke. The thresholds are deliberately loose — this asserts
the shape of the cost, not a benchmark figure.
"""

from __future__ import annotations

import time

from pysqlsuggestions.api import derive_request
from pysqlsuggestions.dialects.postgres import POSTGRES

_BUDGET_SECONDS = 1.5


def joined(count: int) -> str:
    """A SELECT joining `count` relations, with the caret at the end."""
    tail = ' '.join(f'JOIN t{index} ON t{index}.id = t0.id' for index in range(1, count))
    return f'SELECT * FROM t0 {tail} WHERE '


def test_a_query_with_a_thousand_joins_is_still_interactive() -> None:
    """
    `_clause_starting_at` located each match by scanning the whole token stream
    for it, once per clause name per token, which is quadratic in a query whose
    size is exactly what makes it worth completing.
    """
    sql = joined(1000)
    started = time.perf_counter()
    derive_request(sql, len(sql), POSTGRES)
    assert time.perf_counter() - started < _BUDGET_SECONDS


def test_the_cost_grows_with_the_query_not_with_its_square() -> None:
    """Four times the query should not be sixteen times the work."""

    def cost(count: int) -> float:
        sql = joined(count)
        started = time.perf_counter()
        derive_request(sql, len(sql), POSTGRES)
        return time.perf_counter() - started

    small = max(cost(250), 1e-4)
    assert cost(1000) / small < 8


def test_absurd_nesting_does_not_exhaust_the_stack() -> None:
    """
    Nobody writes this, but a runaway generator does, and a RecursionError
    reaches the editor as a crash rather than as an empty list.
    """
    depth = 1500
    sql = 'SELECT ' + '(SELECT ' * depth + 'x'
    assert derive_request(sql, len(sql), POSTGRES) is not None
