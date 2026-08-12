"""
Star expansion: `SELECT *⌶` offering the column list the star stands for.

Everything it needs was already here — `Projection.stars` records what a star
referred to and `resolve` expands one against the catalog — except the position
and a span of its own. The same caret also offers `FROM`, which is inserted
beside the star where the expansion replaces it, so one span for the position
cannot serve both.
"""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.types import Candidate, Kind, Request


def test_a_candidate_may_carry_its_own_span() -> None:
    """Without this, accepting the FROM offered beside a star would delete the star."""
    request = Request(kinds=(Kind.EXPANSION, Kind.KEYWORD), prefix='', replace_span=(8, 8))
    candidates = [
        Candidate(text='id, name', kind=Kind.EXPANSION, literal=True, span=(7, 8)),
        Candidate(text='FROM', kind=Kind.KEYWORD, origin='keyword'),
    ]
    spans = {s.kind: s.replace_span for s in rank(candidates, request, POSTGRES)}
    assert spans[Kind.EXPANSION] == (7, 8)
    assert spans[Kind.KEYWORD] == (8, 8)


def test_a_candidate_with_no_span_uses_the_position() -> None:
    """The default has to stay the request's span, which is what every other candidate wants."""
    request = Request(kinds=(Kind.COLUMN,), prefix='', replace_span=(3, 7))
    [only] = rank([Candidate(text='email', kind=Kind.COLUMN)], request, POSTGRES)
    assert only.replace_span == (3, 7)


def test_a_zero_span_is_a_span() -> None:
    """`(0, 0)` is falsy-looking and real; the check is against None, not truthiness."""
    request = Request(kinds=(Kind.EXPANSION,), prefix='', replace_span=(5, 5))
    [only] = rank([Candidate(text='id', kind=Kind.EXPANSION, span=(0, 0))], request, POSTGRES)
    assert only.replace_span == (0, 0)
