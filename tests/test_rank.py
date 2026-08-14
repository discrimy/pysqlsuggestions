"""Ranking's availability rule: restricted loses to everything, whatever it matched."""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.types import Availability, Candidate, Kind, Request


def _request(prefix: str = '') -> Request:
    """The three fields `Request` requires; everything else on it defaults."""
    return Request(kinds=(Kind.COLUMN,), prefix=prefix, replace_span=(0, 0))


def test_restricted_sinks_below_a_worse_match() -> None:
    """An exact prefix hit that cannot be read still loses to a word-boundary hit that can."""
    candidates = [
        Candidate(
            text='password',
            kind=Kind.COLUMN,
            availability=Availability.RESTRICTED,
            reason='no SELECT privilege',
        ),
        Candidate(text='user_passphrase', kind=Kind.COLUMN),
    ]
    assert [s.text for s in rank(candidates, _request('pass'), POSTGRES)] == ['user_passphrase', 'password']


def test_the_reason_and_state_reach_the_suggestion() -> None:
    """A front end cannot render what rank drops."""
    candidate = Candidate(
        text='password',
        kind=Kind.COLUMN,
        availability=Availability.RESTRICTED,
        reason='no SELECT privilege',
    )
    suggestion = rank([candidate], _request('pass'), POSTGRES)[0]
    assert suggestion.availability is Availability.RESTRICTED
    assert suggestion.reason == 'no SELECT privilege'


def test_the_readable_duplicate_wins_the_dedup() -> None:
    """Two relations in scope, one column name, one grant: rank keys on (kind, text) and keeps the first."""
    candidates = [
        Candidate(
            text='id',
            kind=Kind.COLUMN,
            availability=Availability.RESTRICTED,
            reason='no SELECT privilege',
        ),
        Candidate(text='id', kind=Kind.COLUMN),
    ]
    found = rank(candidates, _request('id'), POSTGRES)
    assert len(found) == 1
    assert found[0].availability is Availability.AVAILABLE


def test_an_unknown_column_ranks_exactly_as_it_always_did() -> None:
    """The degradation, asserted rather than assumed: UNKNOWN is not a sink."""
    candidates = [
        Candidate(text='password', kind=Kind.COLUMN, availability=Availability.UNKNOWN),
        Candidate(text='user_passphrase', kind=Kind.COLUMN),
    ]
    assert [s.text for s in rank(candidates, _request('pass'), POSTGRES)] == ['password', 'user_passphrase']
