"""
Star expansion: `SELECT *⌶` offering the column list the star stands for.

Everything it needs was already here — `Projection.stars` records what a star
referred to and `resolve` expands one against the catalog — except the position
and a span of its own. The same caret also offers `FROM`, which is inserted
beside the star where the expansion replaces it, so one span for the position
cannot serve both.
"""

from __future__ import annotations

from pysqlsuggestions.api import derive_request
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


def request_at(sql: str, caret: int | None = None) -> Request:
    """The request at `caret`, or at the end of `sql`."""
    return derive_request(sql, len(sql) if caret is None else caret, POSTGRES)


def test_a_star_under_the_caret_is_recorded_with_its_span() -> None:
    """The span covers the star alone, so accepting replaces it and nothing else."""
    found = request_at('SELECT * FROM users u', caret=8)
    assert found.star == (7, 8)
    assert [r.label for r in found.star_of] == ['u']


def test_a_qualified_star_is_replaced_whole() -> None:
    """`u.*` goes with its qualifier: every expanded column carries its own `u.`."""
    found = request_at('SELECT u.* FROM users u', caret=10)
    assert found.star == (7, 10)
    assert [r.label for r in found.star_of] == ['u']


def test_a_qualified_star_names_only_its_own_relation() -> None:
    """`o.*` is one relation however many are in the FROM."""
    sql = 'SELECT o.* FROM users u JOIN orders o ON o.user_id = u.id'
    assert [r.label for r in request_at(sql, caret=10).star_of] == ['o']


def test_a_bare_star_names_every_relation_at_its_own_level() -> None:
    """Order follows the FROM clause, which is the order the columns are written in."""
    sql = 'SELECT * FROM users u JOIN orders o ON o.user_id = u.id'
    assert [r.label for r in request_at(sql, caret=8).star_of] == ['u', 'o']


def test_the_expansion_leads_the_kinds_at_a_star() -> None:
    """Putting the caret on the star is the gesture that asks for this, so it comes first."""
    assert request_at('SELECT * FROM users u', caret=8).kinds == (Kind.EXPANSION, Kind.KEYWORD)


def test_a_star_with_nothing_to_expand_records_no_star() -> None:
    """`SELECT *` before any FROM keeps answering FROM and claims nothing more."""
    found = request_at('SELECT *')
    assert found.star is None
    assert found.kinds == (Kind.KEYWORD,)


def test_a_space_past_the_star_is_the_position_that_wants_from() -> None:
    """A star is one character; only the caret at its end is on it."""
    found = request_at('SELECT * FROM users u', caret=9)
    assert found.star is None
    assert Kind.EXPANSION not in found.kinds


def test_a_star_inside_a_call_is_not_expanded() -> None:
    """`count(*)` passes the is-an-item test because a paren precedes it."""
    assert request_at('SELECT count(*) FROM users u', caret=14).star is None


def test_multiplication_is_not_a_star() -> None:
    """`SELECT a * ⌶` and `WHERE 5 * ⌶` are the operator, and open an operand."""
    assert request_at('SELECT a * 2 FROM users u', caret=10).star is None
