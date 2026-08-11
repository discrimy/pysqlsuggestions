"""
The session, driven directly rather than over a pipe.

One rule governs everything here: a completion request never fails. The library
degrades by design, so an unreachable database, a rejected password or an
unknown dialect all fall back to what the statement itself describes. That is a
useful answer; an error popup on a keystroke is not.

The session holds no pygls state, which is what lets these run without a client
handshake — `server.workspace` does not exist until a client has initialized.
"""

from __future__ import annotations

from typing import Any

from lsprotocol.types import INITIALIZE, TEXT_DOCUMENT_COMPLETION

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions_lsp.connections import Profile
from pysqlsuggestions_lsp.server import Session, create_server

WITH_CTE = 'WITH recent AS (SELECT 1) SELECT * FROM rec'


def refusing(profile: Profile) -> Any:
    """A database that is not answering today."""
    message = 'connection refused'
    raise OSError(message)


def labels(session: Session, text: str, offset: int | None = None) -> list[str]:
    """What a client would show, for a caret at `offset` or at end of text."""
    return [item.label for item in session.suggest(text, len(text) if offset is None else offset)]


def test_a_cte_name_is_offered_from_the_statement_alone() -> None:
    """No profile, no catalog, and still a useful answer."""
    assert 'recent' in labels(Session(), WITH_CTE)


def test_items_arrive_in_the_engines_order() -> None:
    """sort_text is what stops a client re-ranking them."""
    items = Session().suggest(WITH_CTE, len(WITH_CTE))
    assert [item.sort_text for item in items] == sorted(item.sort_text or '' for item in items)


def test_the_caret_in_the_second_statement_does_not_see_the_first() -> None:
    """
    Scope is per statement.

    Handing the engine the whole document would put `alpha` in scope for a
    caret in a statement that never mentions it.
    """
    text = 'SELECT * FROM alpha;\nSELECT * FROM b'
    assert 'alpha' not in labels(Session(), text)


def test_an_empty_document_answers_without_raising() -> None:
    """The completion on a fresh file must not be a special case."""
    assert isinstance(Session().suggest('', 0), list)


def test_a_caret_past_the_end_is_clamped() -> None:
    """A client and a server can disagree about length for one keystroke."""
    assert isinstance(Session().suggest('SELECT ', 999), list)


def test_without_a_profile_the_dialect_is_ansi() -> None:
    """An unknown backend degrades rather than breaking; ANSI is the shipped fallback."""
    assert Session().dialect is ANSI


def test_a_profile_chooses_the_dialect() -> None:
    """Resolved through the entry-point registry, so a third-party dialect works too."""
    assert Session(profile=Profile(dialect='postgres', host='db')).dialect is POSTGRES


def test_an_unknown_dialect_falls_back_to_ansi() -> None:
    """A typo in a setting must not take completion out entirely."""
    assert Session(profile=Profile(dialect='oracle', host='db')).dialect is ANSI


def test_a_database_that_refuses_still_answers() -> None:
    """
    A completion request never fails.

    An unreachable database degrades to what the statement describes. The
    alternative is an error popup arriving on a keystroke.
    """
    session = Session(profile=Profile(dialect='postgres', host='nowhere'), connect=refusing)
    assert 'recent' in labels(session, WITH_CTE)


def test_a_refusal_is_not_retried_on_every_keystroke() -> None:
    """
    A database that is down stays down for the length of a coffee.

    Retrying per keystroke means a connection attempt per character typed, each
    one blocking the request that triggered it.
    """
    attempts: list[Profile] = []

    def counting(profile: Profile) -> Any:
        attempts.append(profile)
        message = 'connection refused'
        raise OSError(message)

    session = Session(profile=Profile(dialect='postgres', host='nowhere'), connect=counting)
    session.suggest(WITH_CTE, len(WITH_CTE))
    session.suggest(WITH_CTE, len(WITH_CTE))
    assert len(attempts) == 1


def test_a_dialect_with_no_bundled_driver_still_completes() -> None:
    """
    ClickHouse resolves as a dialect and has no driver here.

    The dialect must still be used — its keywords and quoting are right even
    when nothing can be read from the server.
    """
    session = Session(profile=Profile(dialect='clickhouse', host='db'))
    assert session.catalog() is None
    assert 'recent' in labels(session, WITH_CTE)


def test_the_server_registers_the_features_a_client_needs() -> None:
    """
    A handler nobody registered is a server that answers nothing.

    Registration is where a pygls major version shows: the handlers are
    closures taking parameters alone, and this asserts they arrived rather
    than trusting that a decorator did what it did in an older release.
    """
    features = create_server().protocol.fm.features
    assert TEXT_DOCUMENT_COMPLETION in features
    assert INITIALIZE in features
