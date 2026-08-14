"""
One profile, one verdict.

A connection can be wrong in half a dozen ways and every one of them presents
identically in an editor: completion that quietly stops being schema-aware. The
messages here are the only place a user learns which way it went wrong, so they
are the thing under test — not merely that a boolean came back false.
"""

from __future__ import annotations

from typing import Any

from pysqlsuggestions_lsp.check import check, describe

POSTGRES = {'dialect': 'postgres', 'host': 'localhost', 'port': 57432, 'database': 'd', 'user': 'u'}


class FakeCursor:
    """A cursor over a fixed number of relations."""

    def __init__(self, rows: int) -> None:
        self.rows = rows

    def execute(self, operation: str, parameters: Any = None) -> None:
        """Accept anything; the catalog only needs this not to raise."""

    def fetchall(self) -> list[Any]:
        """`rows` rows shaped as the postgres tables query expects."""
        return [('public', f't{index}', 'r', 0, True) for index in range(self.rows)]


class FakeConnection:
    """A connection that hands out `FakeCursor`."""

    def __init__(self, rows: int = 3) -> None:
        self.rows = rows

    def cursor(self) -> FakeCursor:
        """A fresh cursor."""
        return FakeCursor(self.rows)


def test_a_working_connection_reports_what_it_saw() -> None:
    """A count is what tells a user the catalog is genuinely readable."""
    verdict = check(POSTGRES, connect=lambda profile: FakeConnection(rows=3))
    assert verdict['ok'] is True
    assert '3' in verdict['detail']


def test_a_profile_without_a_dialect_is_rejected_before_connecting() -> None:
    """Nothing to connect with, and saying so beats a driver error."""
    verdict = check({'host': 'localhost'})
    assert verdict['ok'] is False
    assert 'dialect' in verdict['detail']


def test_a_dialect_with_no_bundled_driver_says_what_still_works() -> None:
    """
    `ansi` resolves as a dialect and has no driver here, nor could it have one.

    Keywords and quoting are still right, and a user told only "failed" would
    reasonably conclude the whole connection is useless.
    """
    verdict = check({'dialect': 'ansi', 'host': 'localhost'})
    assert verdict['ok'] is False
    assert 'ansi' in verdict['detail']
    assert 'keywords' in verdict['detail']


def test_a_missing_password_is_named_rather_than_leaked() -> None:
    """
    pg8000 raises AttributeError("'NoneType' object has no attribute 'decode'").

    That message tells a user nothing and sent this project's own author
    debugging in the wrong direction. It is the reason this module exists.
    """
    detail = describe(AttributeError("'NoneType' object has no attribute 'decode'"), password=None)
    assert 'password' in detail
    assert 'decode' not in detail


def test_a_password_that_was_supplied_is_not_reported_as_missing() -> None:
    """The same exception with a password stored means something else went wrong."""
    detail = describe(AttributeError("'NoneType' object has no attribute 'decode'"), password='given')
    assert 'none is stored' not in detail


def test_a_server_error_is_reduced_to_its_message() -> None:
    """
    pg8000 carries a dict, and printing it raw is unreadable.

    {'S': 'FATAL', 'C': '28P01', 'M': 'password authentication failed...'}
    """
    error = Exception({'S': 'FATAL', 'C': '28P01', 'M': 'password authentication failed for user "report"'})
    detail = describe(error, password='wrong')
    assert detail == 'password authentication failed for user "report"'


def test_an_unreachable_host_keeps_its_own_words() -> None:
    """The driver's message is already the clearest thing available."""
    error = Exception("Can't create a connection to host localhost and port 59999")
    assert 'port 59999' in describe(error, password='x')


def test_a_multi_line_error_is_reduced_to_one() -> None:
    """This goes in a tree row's tooltip, not a terminal."""
    assert '\n' not in describe(Exception('first line\nsecond line'), password='x')


def test_a_catalog_that_raises_is_a_failed_verdict() -> None:
    """The whole point: an exception becomes an answer, never a crash."""

    def refusing(profile: Any) -> Any:
        message = "Can't create a connection to host localhost and port 57432"
        raise OSError(message)

    verdict = check(POSTGRES, connect=refusing)
    assert verdict['ok'] is False
    assert '57432' in verdict['detail']
