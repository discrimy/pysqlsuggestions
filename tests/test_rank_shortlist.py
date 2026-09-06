"""
Ranking renders the candidates it is going to show, not the ones it discards.

A `FROM ⌶` on a 5000-relation schema built five thousand `Suggestion`s, each with
its quoting decided and its text rendered, in order to return forty. Rendering is
about half of what ranking costs and none of it was needed.

The first three elements of the sort key — availability, score, name length —
come from the `Candidate` alone. Only the fourth, the rendered text, needs the
work. So the shortlist is chosen on the first three and only what survives is
rendered.

This is the one change in this area that *could* alter output, which is why the
tests here are about equality with the unoptimised answer rather than about
speed. `_reference` is that answer, computed the slow way.
"""

from __future__ import annotations

import random

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.engine import rank as rank_mod
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.types import Availability, Candidate, Kind, Request, Suggestion


def _request(prefix: str = '', kinds: tuple[Kind, ...] = (Kind.COLUMN, Kind.TABLE)) -> Request:
    """The three fields `Request` requires; everything else defaults."""
    return Request(kinds=kinds, prefix=prefix, replace_span=(0, 0))


def _reference(candidates: list[Candidate], request: Request, limit: int) -> list[Suggestion]:
    """
    What ranking answered before it learned to shortlist: rank everything, then cut.

    Deliberately not a reimplementation. It calls the real thing with no limit,
    which is the path that still renders every candidate, and slices afterwards.
    """
    return rank(candidates, request, ANSI, None)[:limit]


def _spread(count: int, seed: int = 0) -> list[Candidate]:
    """
    Candidates that collide on the sort key as hard as real ones do.

    Ties are the whole risk here. Scores come from a handful of match strengths
    and a kind bonus, so a large catalog produces long runs of candidates
    identical on the first three key elements, and which of them a shortlist
    keeps is exactly what the fourth element decides.
    """
    rng = random.Random(seed)
    kinds = (Kind.COLUMN, Kind.TABLE)
    return [
        Candidate(
            text=f'{rng.choice(("order", "invoice", "user", "report"))}_{index}',
            kind=rng.choice(kinds),
            detail='generated',
            position=rng.choice((0, 1, 2)),
            availability=rng.choice((Availability.UNKNOWN, Availability.RESTRICTED)),
        )
        for index in range(count)
    ]


def test_a_limited_ranking_matches_an_unlimited_one_cut_to_size() -> None:
    """The property the whole change rests on, over a set full of ties."""
    candidates = _spread(2000)
    for prefix in ('', 'order', 'o', 'user_1', 'zzz'):
        request = _request(prefix)
        assert rank(candidates, request, ANSI, 40) == _reference(candidates, request, 40), prefix


def test_it_holds_for_every_limit_around_the_boundary() -> None:
    """
    Off-by-one in the shortlist would show up at one limit and not its neighbours.

    Small limits are where a tie run is most likely to straddle the cut, so the
    interesting sizes are the small ones rather than the realistic ones.
    """
    candidates = _spread(500, seed=7)
    request = _request('o')
    for limit in (0, 1, 2, 3, 5, 13, 40, 499, 500, 501):
        assert rank(candidates, request, ANSI, limit) == _reference(candidates, request, limit), limit


def test_it_holds_when_the_shortlist_collapses_on_dedupe() -> None:
    """
    Duplicates are removed *after* rendering, so a shortlist can come up short.

    Five hundred candidates rendering to four distinct texts is the degenerate
    case: taking the best forty and deduping leaves four, and the answer has to
    reach past the shortlist rather than return a short list.
    """
    candidates = [
        Candidate(text=f'{name}', kind=Kind.COLUMN, detail=f'r{index}', position=index % 3)
        for index in range(500)
        for name in ('id', 'name', 'total', 'created_at')
    ]
    request = _request('')
    assert rank(candidates, request, ANSI, 40) == _reference(candidates, request, 40)
    assert len(rank(candidates, request, ANSI, 40)) == 4


def test_an_unlimited_ranking_is_unchanged() -> None:
    """No limit means no shortlist: everything is rendered, as it always was."""
    candidates = _spread(300, seed=3)
    request = _request('')
    assert len(rank(candidates, request, ANSI, None)) == len({(c.kind, c.text) for c in candidates})


def test_only_a_bounded_number_of_candidates_are_rendered() -> None:
    """
    The point of the exercise, and the only thing that says it actually happened.

    Every assertion above would pass just as well against the version that
    rendered all five thousand, because they are about the answer and this is
    about the work. Counted rather than timed: a duration would be measuring the
    machine, and the claim is that the count no longer grows with the catalog.
    """
    candidates = _spread(5000, seed=11)
    request = _request('')

    calls = 0
    original = rank_mod._render

    def counting(candidate: Candidate, request: Request, dialect: Dialect) -> tuple[str, tuple[int, ...]]:
        """
        The real thing, counted.

        Parameter names match `_render`'s exactly, because assigning over a
        module-level function is a typed assignment and a caller could pass any
        of them by keyword.
        """
        nonlocal calls
        calls += 1
        return original(candidate, request, dialect)

    rank_mod._render = counting
    try:
        found = rank(candidates, request, ANSI, 40)
    finally:
        rank_mod._render = original

    assert len(found) == 40
    assert calls < 500, f'{calls} renders for 40 suggestions out of 5000 candidates'
