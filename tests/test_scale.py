"""
Behaviour on inputs far larger than a hand-written query.

A generated report query runs to hundreds of joins, and a caret sits in one of
these on every keystroke. The thresholds are deliberately loose — this asserts
the shape of the cost, not a benchmark figure.
"""

from __future__ import annotations

import gc
import sys
import time
import tracemalloc

from pysqlsuggestions.api import complete, derive_request
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


def test_unclosed_nesting_does_not_cost_its_square() -> None:
    """
    A query with dangling parens is not an edge case — it is what the editor
    holds on most keystrokes, since the caret arrives before the closing paren.

    Two separate costs met here. `_unclosed_call_depth` handed back a lookup that
    counted its open groups by scanning them, once per token. The larger one was
    `_by_first_word`, whose `@cache` re-hashed the entire `ClauseModel` on every
    clause lookup — 118 million hash calls for a query this shape, and 24 seconds
    of its 44. This depth took 5.3 seconds before those two and takes well under
    one after.

    Depth eighty rather than something rounder because it is the point the old
    cost crossed this budget: the assertion is meant to fail if either fix is
    undone, not to leave headroom for a third. What it does *not* cover is
    `clause_at`'s outward widening, a separate mechanism on a separate shape —
    `test_a_run_of_bare_parens_does_not_cost_its_square` has that one.
    """
    sql = 'SELECT * FROM ' + '(SELECT * FROM ' * 80 + 't'
    started = time.perf_counter()
    derive_request(sql, len(sql), POSTGRES)
    assert time.perf_counter() - started < _BUDGET_SECONDS


def test_nested_ctes_do_not_exhaust_the_stack() -> None:
    """
    `_MAX_NESTING` bounded `_scope_level` and nothing else.

    `select_outputs` and `_read_ctes` call each other once per nesting level with
    no bound at all, so a generated document raised RecursionError out of
    `complete` itself — and out of the language server's handler, whose docstring
    says it never raises.

    The interpreter's limit is lowered rather than the input deepened, and that
    is a statement about cost rather than a trick: the only shape reaching this
    recursion is nested *unclosed* subqueries, which is also the shape still
    quadratic in `_by_first_word`'s hashing, so proving the bound at the stock
    limit costs four hundred seconds. Lowering the limit asserts the same thing
    — the walk stops at `_MAX_NESTING` rather than at the interpreter — for
    three. Restore it in `finally`, or every later test inherits it.
    """
    sql = 'WITH a AS(' * 150 + 'SELECT 1'
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(300)
    try:
        assert derive_request(sql, len(sql), POSTGRES) is not None
    finally:
        sys.setrecursionlimit(limit)


def test_a_long_cte_chain_resolves_without_exhausting_the_stack() -> None:
    """
    The same missing bound one stage down, in `resolve`.

    `_columns_of` and `_from_projection` walk a projection's stars recursively.
    CTEs are siblings rather than nested scopes, so `_MAX_NESTING` never applies
    to a chain of them and 495 links was enough to raise — with no catalog at all.
    """
    parts = ['a0 AS (SELECT * FROM users)'] + [f'a{i} AS (SELECT * FROM a{i - 1})' for i in range(1, 600)]
    sql = 'WITH ' + ', '.join(parts) + ' SELECT * FROM a599 z WHERE z.'
    assert complete(sql, len(sql), POSTGRES) is not None


def test_deeply_nested_derived_tables_stay_interactive() -> None:
    """
    The scope walk asked the same question about the same tokens repeatedly.

    `_scope_level` recurses a level at a time and each level rescans ranges the
    level above already scanned, so `_clause_starting_at` was called 235,245
    times for a query holding 324 distinct questions — a 99.9% repeat rate at
    depth eighty. The token stream cannot change while a request is being
    derived, so every one of those repeats had the same answer.

    A generated query is where this arrives: `_MAX_NESTING`'s own comment says
    "nobody writes sixty-four, but a code generator does", and 2.6 KB of it took
    almost two seconds.
    """
    depth = 160
    sql = 'SELECT * FROM ' + '(SELECT * FROM ' * depth + 'users u WHERE u.' + ')' * depth
    started = time.perf_counter()
    derive_request(sql, sql.index('u.') + 2, POSTGRES)
    assert time.perf_counter() - started < _BUDGET_SECONDS


def test_the_memo_does_not_grow_with_the_square_of_a_relation_list() -> None:
    """
    A memo whose keys outnumber the tokens is a memory cost, not a saving.

    `_inside_a_relation_list` scans back to each comma, so it asks
    `_clause_starting_at` with `hi` set to that comma — and a list of k
    comma-separated derived tables therefore opens k families of keys over k
    indices. At k=400 that was 323,207 entries and 41 MiB of peak for 6.7 KB of
    SQL, and the memo was *slower* there than not memoising at all: half of every
    ask was a miss that cost a permanent entry.

    A bound rather than a smarter key: what `hi` truncates is real, so the key
    cannot drop it, and past a few thousand entries the walk is not the shape
    this was built for anyway.
    """
    sql = 'SELECT * FROM ' + ', '.join(f'(SELECT 1) a{index}' for index in range(400)) + ' WHERE '
    gc.collect()
    tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        derive_request(sql, len(sql), POSTGRES)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, f'{peak / 1048576:.1f} MiB for {len(sql)} characters'


def test_a_run_of_bare_parens_does_not_cost_its_square() -> None:
    """
    The other half of finding 17, and the mechanism `clause_at` owns rather than the memo.

    `clause_at` widens outward once per paren depth, and a run of bare `(`
    makes the depth the length of the input — so it called the O(n)
    `_group_start` and `_scan_for_clause` once per token. Cleanly quadratic:
    3.9x per doubling, a second at four thousand and about four at eight.

    A word cannot be found at a depth that holds no words, so the depths worth
    scanning are counted once instead of assumed. On this shape there are none
    at all, which takes both O(n) helpers out of the loop entirely.

    Nobody types four thousand parentheses. A generator emitting a half-written
    statement does, and this is the shape where the cost was worst rather than
    where it was likeliest.
    """

    def cost(count: int) -> float:
        sql = '(' * count
        started = time.perf_counter()
        derive_request(sql, len(sql), POSTGRES)
        return time.perf_counter() - started

    small = max(cost(1000), 1e-4)
    assert cost(4000) / small < 8, 'four times the parens should not be sixteen times the work'
