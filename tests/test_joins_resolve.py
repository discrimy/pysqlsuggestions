"""Foreign keys reaching the engine: capability detection, caching, degradation."""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.api import complete, plan_insertion
from pysqlsuggestions.caches import MemoryCache, cache_key
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.resolve import _Reader
from pysqlsuggestions.types import Candidate, ForeignKey, Kind, Request, Suggestion
from tests.corpus.cases import split_caret

EDGE = ForeignKey(
    schema='public',
    table='reports_report',
    columns=('author_id',),
    ref_schema='public',
    ref_table='auth_user',
    ref_columns=('id',),
)


class _Constrained:
    """A catalog that answers only the foreign-key question. Nothing else is needed here."""

    def __init__(self) -> None:
        self.calls = 0

    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """Record the call so the test can prove the cache stopped the second one."""
        del schema
        self.calls += 1
        return [EDGE]


class _Bare:
    """
    The four `Catalog` methods and nothing else — an adapter for a backend with no constraints.

    `MemoryCatalog` cannot stand in for this: it implements `foreign_keys`
    unconditionally and so satisfies the protocol even when a fixture declares no
    edges. The distinction under test is the capability being *absent*.
    """

    def schemas(self, catalog: str | None = None) -> Sequence[str]:
        """No namespaces."""
        del catalog
        return []

    def tables(self, schema: str | None = None) -> Sequence[object]:
        """No relations."""
        del schema
        return []

    def columns(self, schema: str | None, table: str) -> Sequence[object]:
        """No columns."""
        del schema, table
        return []

    def functions(self, schema: str | None = None) -> Sequence[object]:
        """No functions."""
        del schema
        return []


def test_reader_returns_nothing_without_the_capability() -> None:
    """A catalog that cannot answer degrades to silence, as every other capability does."""
    reader = _Reader(_Bare(), POSTGRES, None, None)  # type: ignore[arg-type]
    assert reader.foreign_keys('public') == ()


def test_reader_reads_through_the_capability() -> None:
    """Present: the edges come back as the catalog reported them."""
    reader = _Reader(_Constrained(), POSTGRES, None, None)  # type: ignore[arg-type]
    assert list(reader.foreign_keys('public')) == [EDGE]


def test_reader_caches_edges_under_the_identity_led_key() -> None:
    """Constraints change on DDL, not between keystrokes, so one read serves the session."""
    catalog = _Constrained()
    cache = MemoryCache()
    reader = _Reader(catalog, POSTGRES, cache, 'analyst')  # type: ignore[arg-type]
    reader.foreign_keys('public')
    reader.foreign_keys('public')
    assert catalog.calls == 1
    assert cache_key('analyst', 'postgres', 'fk', 'public') in cache._entries  # noqa: SLF001


def test_a_join_proposal_outranks_the_tables_it_sits_among() -> None:
    """At `JOIN <caret>` the proposal is a better answer than the bare name it contains."""
    request = Request(kinds=(Kind.TABLE, Kind.SCHEMA, Kind.KEYWORD), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(text='auth_user', kind=Kind.TABLE),
        Candidate(
            text='auth_user au ON r.author_id = au.id',
            kind=Kind.JOIN,
            snippet='auth_user au ON r.author_id = au.id',
            match_text='auth_user',
            note='fk: auth_user.id',
        ),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'auth_user au ON r.author_id = au.id'
    assert found[0].note == 'fk: auth_user.id'


def test_a_join_proposal_scores_as_a_column_where_columns_belong() -> None:
    """At `ON <caret>` there is no TABLE kind to borrow, so it takes COLUMN's place."""
    request = Request(kinds=(Kind.COLUMN, Kind.FUNCTION), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(text='id', kind=Kind.COLUMN),
        Candidate(text='r.author_id = u.id', kind=Kind.JOIN, snippet='r.author_id = u.id', match_text='author_id'),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'r.author_id = u.id'


def test_forward_outranks_reverse() -> None:
    """Many-to-one is more often wanted and cannot multiply the result set."""
    request = Request(kinds=(Kind.TABLE,), prefix='', replace_span=(0, 0))
    candidates = [
        Candidate(
            text='b ON u.id = b.user_id', kind=Kind.JOIN, snippet='b ON u.id = b.user_id', match_text='b', position=1
        ),
        Candidate(text='a ON r.a_id = a.id', kind=Kind.JOIN, snippet='a ON r.a_id = a.id', match_text='a', position=0),
    ]
    found = rank(candidates, request, POSTGRES)
    assert found[0].text == 'a ON r.a_id = a.id'


def test_typed_prefix_still_decides() -> None:
    """Match strength stays dominant, so a proposal for another table falls away."""
    request = Request(kinds=(Kind.TABLE,), prefix='auth', replace_span=(0, 4))
    candidates = [
        Candidate(
            text='orders o ON r.o_id = o.id',
            kind=Kind.JOIN,
            snippet='orders o ON r.o_id = o.id',
            match_text='orders',
        ),
        Candidate(text='auth_user', kind=Kind.TABLE),
    ]
    found = rank(candidates, request, POSTGRES)
    assert [s.text for s in found] == ['auth_user']


SNAPSHOT = {
    ('public', 'reports_report'): [('id', 'bigint'), ('title', 'varchar(100)'), ('author_id', 'bigint')],
    ('public', 'auth_user'): [('id', 'bigint'), ('username', 'varchar(150)'), ('email', 'varchar(254)')],
}
JOINED = MemoryCatalog(SNAPSHOT, foreign_keys=[EDGE])
BARE = MemoryCatalog(SNAPSHOT)


def suggest(marked: str, catalog: MemoryCatalog) -> list[str]:
    """Suggestion texts for ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return [s.text for s in complete(sql, caret, POSTGRES, catalog)]


def test_join_position_leads_with_the_whole_clause() -> None:
    """
    The relation, its alias and the condition, in one accept.

    `au` rather than `u`: the generator offers the initials of the underscore-separated
    words first, and `r` is already taken by the relation in the FROM.
    """
    found = suggest('SELECT * FROM reports_report r JOIN ⌶', JOINED)
    assert found[0] == 'auth_user au ON r.author_id = au.id'
    assert 'auth_user' in found


def test_on_position_leads_with_the_whole_condition() -> None:
    """The plain columns stay underneath for a condition the constraints do not describe."""
    found = suggest('SELECT * FROM reports_report r JOIN auth_user u ON ⌶', JOINED)
    assert found[0] == 'r.author_id = u.id'
    assert 'u.email' in found


def test_qualified_on_position_lifts_the_fk_column() -> None:
    """`ON r.⌶` has committed the left side, so the column leads instead."""
    found = suggest('SELECT * FROM reports_report r JOIN auth_user u ON r.⌶', JOINED)
    assert found[0] == 'author_id'
    assert found.count('author_id') == 1


def test_from_position_is_untouched() -> None:
    """Nothing is guessed at a user who has not typed JOIN."""
    found = suggest('SELECT * FROM reports_report r ⌶', JOINED)
    assert found[0] == 'JOIN'
    assert not [text for text in found if ' ON ' in text]


def test_without_constraints_nothing_changes() -> None:
    """The same catalog minus its edges behaves exactly as it did before this feature."""
    at_join = suggest('SELECT * FROM reports_report r JOIN ⌶', BARE)
    assert at_join[:2] == ['auth_user', 'reports_report']
    assert not [text for text in at_join if ' ON ' in text]

    at_on = suggest('SELECT * FROM reports_report r JOIN auth_user u ON ⌶', BARE)
    assert not [text for text in at_on if ' = ' in text]
    assert 'r.author_id' in at_on


def test_the_proposal_is_accepted_as_one_edit() -> None:
    """`plan_insertion` needs no change: one replacement over the span, caret at the end."""
    sql, caret = split_caret('SELECT * FROM reports_report r JOIN ⌶')
    best = complete(sql, caret, POSTGRES, JOINED)[0]
    plan = plan_insertion(sql, best, dialect=POSTGRES)
    assert len(plan.edits) == 1
    written = sql[: plan.edits[0].span[0]] + plan.edits[0].text + sql[plan.edits[0].span[1] :]
    assert written == 'SELECT * FROM reports_report r JOIN auth_user au ON r.author_id = au.id'


def test_a_caret_that_cannot_join_costs_no_catalog_read() -> None:
    """The constraints are fetched only where they can be used, so ordinary typing pays nothing."""
    catalog = MemoryCatalog(SNAPSHOT, foreign_keys=[EDGE])
    sql, caret = split_caret('SELECT * FROM reports_report r WHERE r.⌶')
    complete(sql, caret, POSTGRES, catalog)
    assert not [call for call in catalog.calls if call[0] == 'foreign_keys']


def displayed(suggestion: Suggestion) -> str:
    """
    What a front end puts in the list.

    `label` falling back to `text` is the documented contract and what both the
    demo page and `payload.py` do.
    """
    return suggestion.label or suggestion.text


def test_a_join_proposal_displays_the_clause_it_inserts() -> None:
    """
    The list must show what accepting writes, not the name matching runs against.

    These two were one field, so a proposal displayed as a bare relation name:
    `flight` twice over for a table reachable by two constraints, with nothing to
    tell them apart and no sign that accepting writes a whole clause.
    """
    sql, caret = split_caret('SELECT * FROM reports_report r JOIN ⌶')
    best = complete(sql, caret, POSTGRES, JOINED)[0]
    assert displayed(best) == 'auth_user au ON r.author_id = au.id'


def test_two_proposals_to_one_relation_are_told_apart_in_the_list() -> None:
    """The case a single guess would get wrong half the time, as a reader sees it."""
    catalog = MemoryCatalog(
        {
            ('public', 'flight'): [('id', 'bigint'), ('origin', 'character(3)'), ('destination', 'character(3)')],
            ('public', 'airport'): [('code', 'character(3)'), ('city', 'character varying(80)')],
        },
        foreign_keys=[
            ForeignKey(
                schema='public',
                table='flight',
                columns=(column,),
                ref_schema='public',
                ref_table='airport',
                ref_columns=('code',),
            )
            for column in ('origin', 'destination')
        ],
    )
    sql, caret = split_caret('SELECT * FROM flight f JOIN ⌶')
    shown = [displayed(s) for s in complete(sql, caret, POSTGRES, catalog) if s.kind is Kind.JOIN]
    assert shown == [
        'airport a ON f.origin = a.code',
        'airport a ON f.destination = a.code',
    ]


def test_matching_still_runs_against_the_relation_name() -> None:
    """
    Display and matching are separate fields now, and this is why they had to be.

    A cross-schema target renders `revenue.refund …`, which no prefix of `ref`
    matches; the proposal has to stay findable by the name the user is thinking of.
    """
    catalog = MemoryCatalog(
        {
            ('public', 'booking'): [('id', 'bigint')],
            ('revenue', 'refund'): [('id', 'bigint'), ('booking_id', 'bigint')],
        },
        foreign_keys=[
            ForeignKey(
                schema='revenue',
                table='refund',
                columns=('booking_id',),
                ref_schema='public',
                ref_table='booking',
                ref_columns=('id',),
            ),
        ],
    )
    sql, caret = split_caret('SELECT * FROM booking b JOIN ref⌶')
    assert displayed(complete(sql, caret, POSTGRES, catalog)[0]) == 'revenue.refund r ON b.id = r.booking_id'


def test_two_constraints_on_one_column_both_reach_the_proposals() -> None:
    """
    `_edges` deduped on `(schema, table, *columns)`, which is not what identifies an edge.

    A column may be the referencing end of more than one constraint — the shape
    a polymorphic reference takes, and legal wherever the targets differ. With
    the referenced side left out of the key the two collapsed into one entry and
    the last one read won, so a declared join silently had no proposal. Not a
    duplicate, which is what this was filed as: a deletion.
    """
    catalog = MemoryCatalog(
        {
            ('public', 'orders'): [('id', 'bigint'), ('party_id', 'bigint')],
            ('public', 'users'): [('id', 'bigint')],
            ('public', 'companies'): [('id', 'bigint')],
        },
        foreign_keys=(
            ForeignKey('public', 'orders', ('party_id',), 'public', 'users', ('id',)),
            ForeignKey('public', 'orders', ('party_id',), 'public', 'companies', ('id',)),
        ),
        search_path=('public',),
    )
    sql = 'SELECT * FROM orders JOIN '
    # Whole clauses only. A bare relation name is offered at this caret too, and
    # matching on the name alone would pass on the suggestion the proposal was
    # lost from.
    proposals = [s.text for s in complete(sql, len(sql), POSTGRES, catalog, limit=20) if ' ON ' in s.text]
    assert [text for text in proposals if 'users' in text], proposals
    assert [text for text in proposals if 'companies' in text], proposals


def test_qualifying_a_relation_does_not_cost_it_a_proposal() -> None:
    """
    Writing more of a name must not return less.

    `_edges` fetched constraints for the schemas the statement *names*, and the
    port returns those whose *referencing* side lives there — so `FROM
    public.users` asked for `public` alone and never saw the edge declared in
    `sales` that points at it, while bare `FROM users` reached it through the
    default namespace and did.

    The default namespace is now always asked for, so the qualified form offers
    at least what the bare one does. What this does not reach is a referencing
    side in a schema that is neither named nor on the search path: the port is
    schema-scoped deliberately — `Catalog.foreign_keys` says why, and no
    per-relation call could find those without walking the database — so that
    one needs a capability rather than a fix here.
    """
    catalog = MemoryCatalog(
        {
            ('public', 'users'): [('id', 'bigint')],
            ('sales', 'orders'): [('id', 'bigint'), ('user_id', 'bigint')],
        },
        foreign_keys=(ForeignKey('sales', 'orders', ('user_id',), 'public', 'users', ('id',)),),
        search_path=('public',),
    )

    def proposals(sql: str) -> list[str]:
        return [s.text for s in complete(sql, len(sql), POSTGRES, catalog, limit=20) if ' ON ' in s.text]

    bare = proposals('SELECT * FROM users JOIN ')
    qualified = proposals('SELECT * FROM public.users JOIN ')
    assert [text for text in bare if 'orders' in text], bare
    assert [text for text in qualified if 'orders' in text], qualified
