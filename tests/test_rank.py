"""Ranking's availability rule: restricted loses to everything, whatever it matched."""

from __future__ import annotations

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.lex import TokenType, lex
from pysqlsuggestions.engine.rank import quote_if_needed, rank
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


def _round_trips(name: str, dialect: Dialect) -> bool:
    """Whether the engine can re-read a name it just wrote as that same name."""
    written = quote_if_needed(name, dialect)
    tokens = [t for t in lex(f'SELECT {written} FROM t', dialect.syntax) if t.type is not TokenType.WHITESPACE]
    return len(tokens) == 4 and tokens[1].type is TokenType.IDENT and tokens[1].text == written  # noqa: PLR2004


def test_a_name_the_lexer_would_split_is_quoted() -> None:
    """
    The quoter asked whether the *server* would accept a name bare, and never
    whether this engine could read it back.

    Postgres accepts most of the byte range above ASCII, so `unquoted_non_ascii`
    admitted the whole basic plane -- punctuation, symbols, private use, and
    nineteen code points Python calls whitespace. The lexer is far narrower, so a
    column whose name holds a non-breaking space was inserted bare and read back
    as two identifiers, leaving every later completion in that statement working
    from a prefix of the second half.
    """
    for name in ('total\xa0due', 'a\u200bb', 'a\ufeffb', 'a–b', 'a«b', 'a\u202eb'):
        assert quote_if_needed(name, POSTGRES) != name, repr(name)
        assert _round_trips(name, POSTGRES), repr(name)


def test_the_names_postgres_reads_back_are_still_bare() -> None:
    """The point of `unquoted_non_ascii`, which the narrowing must not undo."""
    for name in ('отчёты', 'a$b', 'café', 'straße'):
        assert quote_if_needed(name, POSTGRES) == name, repr(name)
        assert _round_trips(name, POSTGRES), repr(name)


def test_every_dialect_can_re_read_what_it_writes() -> None:
    """
    The property the whole rule exists for, over the shapes that broke it.

    Quoting a name that did not need it merely runs; leaving one bare that did
    need it is a statement the engine cannot parse back.
    """
    names = [
        'plain',
        'MixedCase',
        'select',
        'a b',
        'a"b',
        'a`b',
        "a'b",
        'a.b',
        'a;b',
        'a--b',
        'a/*b',
        '1abc',
        '123',
        '_leading',
        'a$b',
        'отчёты',
        'café',
        'total\xa0due',
        'a\u200bb',
        'a\u3000b',
        'a\xa0b',
        'a\x85b',
        'a𝐀b',
        '🙂',
        'a\x09b',
        'a\nb',
        'a\x00b',
        '',
        ' ',
    ]
    for dialect in (ANSI, POSTGRES, CLICKHOUSE, TRINO):
        for name in names:
            assert _round_trips(name, dialect), (dialect.name, repr(name))
