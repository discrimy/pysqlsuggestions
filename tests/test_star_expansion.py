"""
Star expansion: `SELECT *⌶` offering the column list the star stands for.

Everything it needs was already here — `Projection.stars` records what a star
referred to and `resolve` expands one against the catalog — except the position
and a span of its own. The same caret also offers `FROM`, which is inserted
beside the star where the expansion replaces it, so one span for the position
cannot serve both.
"""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.types import Candidate, Kind, Request, Suggestion

SNAPSHOT = {
    ('public', 'users'): [('id', 'bigint'), ('name', 'text'), ('email', 'text')],
    ('public', 'orders'): [('id', 'bigint'), ('user_id', 'bigint'), ('total', 'numeric')],
}


def catalog() -> MemoryCatalog:
    """Two relations that share a column name, which is what makes qualifying necessary."""
    return MemoryCatalog(SNAPSHOT)


def only_expansion(sql: str, caret: int, source: MemoryCatalog | None = None) -> Suggestion:
    """The one expansion offered at `caret`, asserted to be exactly one."""
    found = [s for s in complete(sql, caret, POSTGRES, source or catalog()) if s.kind is Kind.EXPANSION]
    assert len(found) == 1, f'expected one expansion, got {[s.text for s in found]}'
    return found[0]


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


def test_one_relation_expands_bare() -> None:
    """Nothing is ambiguous, so nothing needs a prefix."""
    assert only_expansion('SELECT * FROM users u', 8).text == 'id, name, email'


def test_two_relations_expand_qualified() -> None:
    """`users` and `orders` both have `id`, and the unqualified list is a query Postgres refuses."""
    sql = 'SELECT * FROM users u JOIN orders o ON o.user_id = u.id'
    assert only_expansion(sql, 8).text == 'u.id, u.name, u.email, o.id, o.user_id, o.total'


def test_a_qualified_star_expands_its_own_relation_qualified() -> None:
    """`u.*` names one relation, and the qualifier it was written with is kept."""
    sql = 'SELECT u.* FROM users u JOIN orders o ON o.user_id = u.id'
    assert only_expansion(sql, 10).text == 'u.id, u.name, u.email'


def test_accepting_replaces_the_star() -> None:
    """The whole point of the candidate's own span, asserted on the resulting text."""
    sql = 'SELECT * FROM users u'
    written = apply_suggestion(sql, only_expansion(sql, 8), dialect=POSTGRES)[0]
    assert written == 'SELECT id, name, email FROM users u'


def test_accepting_a_qualified_star_replaces_the_qualifier_too() -> None:
    """Leaving `u.` in place would give `u.u.id`."""
    sql = 'SELECT u.* FROM users u'
    written = apply_suggestion(sql, only_expansion(sql, 10), dialect=POSTGRES)[0]
    assert written == 'SELECT u.id, u.name, u.email FROM users u'


def test_from_is_still_offered_beside_the_expansion() -> None:
    """The expansion leads; it does not take the position over."""
    found = [s.text for s in complete('SELECT * FROM users u', 8, POSTGRES, catalog())]
    assert found[0] == 'id, name, email'
    assert 'WHERE' in found


def test_a_cte_expands_with_no_catalog_at_all() -> None:
    """A relation the statement described carries its own projection, which is the offline claim."""
    sql = 'WITH recent AS (SELECT id, total FROM orders) SELECT * FROM recent r'
    found = [s for s in complete(sql, sql.index('*') + 1, POSTGRES) if s.kind is Kind.EXPANSION]
    assert [s.text for s in found] == ['id, total']


def test_the_detail_names_the_relations() -> None:
    """The label shows the star as written; the detail says what it covers."""
    offered = only_expansion('SELECT * FROM users u', 8)
    assert offered.label == 'expand *'
    assert offered.detail == '3 columns of users'


def test_a_qualified_star_says_so_in_its_label() -> None:
    """Two stars in one select list are otherwise indistinguishable in the popup."""
    assert only_expansion('SELECT u.* FROM users u', 10).label == 'expand u.*'


def test_a_name_needing_quotes_is_quoted() -> None:
    """The text is inserted verbatim, so quoting is this stage's job, as it is for a join clause."""
    snapshot = {('public', 'users'): [('id', 'bigint'), ('Mixed Case', 'text')]}
    assert only_expansion('SELECT * FROM users u', 8, MemoryCatalog(snapshot)).text == 'id, "Mixed Case"'


def test_a_relation_the_catalog_cannot_answer_for_offers_no_expansion() -> None:
    """An expansion to zero columns is worse than none: it would delete the star."""
    sql = 'SELECT * FROM missing m'
    assert [s for s in complete(sql, 8, POSTGRES, catalog()) if s.kind is Kind.EXPANSION] == []
