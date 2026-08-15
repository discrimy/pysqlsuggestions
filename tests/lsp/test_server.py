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

import threading
import time
from collections.abc import Callable
from typing import Any

from lsprotocol.types import INITIALIZE, TEXT_DOCUMENT_COMPLETION
from pygls.feature_manager import is_thread_function

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
    `ansi` resolves as a dialect and has no driver here, nor could it have one.

    The dialect must still be used — its keywords and quoting are right even
    when nothing can be read from the server.
    """
    session = Session(profile=Profile(dialect='ansi', host='db'))
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


def test_a_degraded_session_says_so() -> None:
    """
    A degraded list looks entirely healthy, so something has to announce it.

    The status bar is the only place a user can tell schema-aware completion
    from statement-only, and it cannot know without being told.
    """
    told: list[str] = []
    session = Session(
        profile=Profile(dialect='postgres', host='nowhere'),
        connect=refusing,
        on_degrade=told.append,
    )
    session.suggest(WITH_CTE, len(WITH_CTE))
    assert told, 'the session degraded without saying so'


def test_a_healthy_session_says_nothing() -> None:
    """No profile, nothing to degrade from, nothing to announce."""
    told: list[str] = []
    Session(on_degrade=told.append).suggest(WITH_CTE, len(WITH_CTE))
    assert told == []


def test_degrading_is_announced_once_not_per_keystroke() -> None:
    """The notification is a state change, not a running commentary."""
    told: list[str] = []
    session = Session(
        profile=Profile(dialect='postgres', host='nowhere'),
        connect=refusing,
        on_degrade=told.append,
    )
    session.suggest(WITH_CTE, len(WITH_CTE))
    session.suggest(WITH_CTE, len(WITH_CTE))
    assert len(told) == 1


def slow_refusal(attempts: list[Profile]) -> Callable[[Profile], Any]:
    """
    A database that takes its time and then refuses, recording each attempt.

    The delay is what makes the race reachable: without it the threads arrive
    one after another and the check-then-set windows never overlap.
    """

    def connect(profile: Profile) -> Any:
        attempts.append(profile)
        time.sleep(0.05)
        message = 'connection refused'
        raise OSError(message)

    return connect


def concurrently(session: Session, workers: int = 8) -> list[list[str]]:
    """
    Drive `workers` completions through one session, released together.

    A barrier rather than staggered starts: the point is that they overlap, and
    a test that only sometimes overlaps only sometimes tests anything.
    """
    ready = threading.Barrier(workers)
    guard = threading.Lock()
    found: list[list[str]] = []

    def run() -> None:
        ready.wait()
        answer = labels(session, WITH_CTE)
        with guard:
            found.append(answer)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return found


def refusing_session(attempts: list[Profile], told: list[str]) -> Session:
    """A session pointed at a database that will refuse, with both counters wired."""
    return Session(
        profile=Profile(dialect='postgres', host='nowhere'),
        connect=slow_refusal(attempts),
        on_degrade=told.append,
    )


def test_one_connection_is_opened_however_many_carets_arrive_at_once() -> None:
    """
    Two threads both finding the connection unopened both open one, and only
    one is kept — the other is leaked, still holding a session on the server.
    """
    attempts: list[Profile] = []
    concurrently(refusing_session(attempts, []))
    assert len(attempts) == 1


def test_degrading_is_announced_once_however_many_carets_arrive_at_once() -> None:
    """
    The notification is a state change, not a running commentary.

    A regression guard rather than a demonstration, and honestly labelled as
    one: this passes without the lock too. `_announced` is read and written by
    two adjacent operations with no I/O between them, and CPython switches
    threads every 5ms by default, so losing that race takes a preemption in a
    window microseconds wide. Measured at eight concurrent carets it never
    happened, where the connection race lost every single time.

    Kept because the invariant is worth pinning: whatever the lock does, this
    must stay at one.
    """
    told: list[str] = []
    concurrently(refusing_session([], told))
    assert len(told) == 1


def test_every_concurrent_caret_still_gets_an_answer() -> None:
    """
    The guard on serialising: waiting for the lock must not turn a slow answer
    into no answer. Every one of them still finds the CTE the statement names.
    """
    found = concurrently(refusing_session([], []))
    assert len(found) == 8  # noqa: PLR2004
    assert all('recent' in answer for answer in found)


def dispatched_in_a_thread(handler: Any) -> bool:
    """
    Whether pygls will run `handler` in its thread pool rather than on the loop.

    Wrapped because pygls ships `is_thread_function` untyped and this project
    type-checks strictly — one ignore in one place rather than one per caller.
    """
    marked: bool = is_thread_function(handler)  # type: ignore[no-untyped-call]
    return marked


def test_completion_is_dispatched_off_the_event_loop() -> None:
    """
    A completion may read a database, and pygls calls an unmarked handler
    inline on the event loop — so a slow introspection query would stop the
    server answering anything at all, including the client's own cancellation
    of the request that is stuck.

    Asserted through pygls's own predicate rather than by looking for our
    decorator: `is_thread_function` is the branch the dispatcher takes, and it
    is what a pygls major version would change.
    """
    handler = create_server().protocol.fm.features[TEXT_DOCUMENT_COMPLETION]
    assert dispatched_in_a_thread(handler)


def test_initialize_stays_on_the_event_loop() -> None:
    """
    It touches no database — `Profile.from_options` is pure — so a thread hop
    would buy nothing and cost a context switch on the one request that
    everything else waits for anyway.
    """
    handler = create_server().protocol.fm.features[INITIALIZE]
    assert not dispatched_in_a_thread(handler)


def test_a_degraded_session_reduces_the_driver_error_to_its_sentence() -> None:
    """
    The notification carries `why` for a person to read, so it gets the sentence.

    `check.describe` exists to turn pg8000's dict into one — its own test calls
    the raw form unreadable — and `Session.degrade` was announcing that raw form
    instead. The two paths now answer the same way for the same failure.
    """

    class DatabaseError(Exception):
        pass

    def rejecting(profile: object) -> object:
        raise DatabaseError({'S': 'FATAL', 'C': '28P01', 'M': 'password authentication failed for user "report"'})

    told: list[str] = []
    session = Session(profile=Profile(dialect='postgres', host='db'), connect=rejecting, on_degrade=told.append)
    session.suggest('SELECT * FROM ', 14)
    assert told == ['password authentication failed for user "report"']
