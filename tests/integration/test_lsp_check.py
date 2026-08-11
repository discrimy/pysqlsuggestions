"""
Verdicts against the docker Postgres, over the driver that raises them.

`describe` translates three specific pg8000 failures. Every one of them was
captured from this server rather than guessed, and a fake cannot prove the
translation still fits once the driver changes its mind about how it fails.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions_lsp.check import check

BASE = {'dialect': 'postgres', 'host': 'localhost', 'port': 57432, 'database': 'report_service', 'user': 'report'}

pytestmark = pytest.mark.integration


def _skip_unless_reachable() -> None:
    """Keep the suite runnable without docker, as every fixture here does."""
    if not check({**BASE, 'password': 'report'})['ok']:
        pytest.skip('postgres not reachable; run docker/docker-compose.yml')


def test_a_good_connection_passes_and_counts() -> None:
    """The count is what distinguishes a live catalog from a mere handshake."""
    _skip_unless_reachable()
    verdict = check({**BASE, 'password': 'report'})
    assert verdict['ok'] is True
    assert 'relations visible' in verdict['detail']


def test_a_missing_password_is_named() -> None:
    """
    The translation this module exists for.

    Untranslated, pg8000 says "'NoneType' object has no attribute 'decode'".
    """
    _skip_unless_reachable()
    verdict = check(BASE)
    assert verdict['ok'] is False
    assert 'password' in verdict['detail']
    assert 'decode' not in verdict['detail']


def test_a_wrong_password_says_so_in_the_servers_words() -> None:
    """Postgres already writes this sentence well; lifting `M` keeps it."""
    _skip_unless_reachable()
    verdict = check({**BASE, 'password': 'wrong'})
    assert verdict['ok'] is False
    assert 'authentication failed' in verdict['detail']
    assert '{' not in verdict['detail'], 'the raw error dict leaked into the message'


def test_a_missing_database_is_distinguishable_from_a_bad_password() -> None:
    """Two failures that look identical to a user unless the message differs."""
    _skip_unless_reachable()
    verdict = check({**BASE, 'database': 'nosuchdb', 'password': 'report'})
    assert verdict['ok'] is False
    assert 'nosuchdb' in verdict['detail']


def test_a_dead_port_reports_the_port() -> None:
    """The commonest typo, and the one worth naming precisely."""
    _skip_unless_reachable()
    verdict = check({**BASE, 'port': 59999, 'password': 'report'})
    assert verdict['ok'] is False
    assert '59999' in verdict['detail']


def test_a_dead_port_gives_up_rather_than_hanging() -> None:
    """
    CONNECT_TIMEOUT is what makes this bounded.

    Without it the driver waits on the OS, and the caller can only kill the
    process — which loses the reason and reports a timeout instead.
    """
    _skip_unless_reachable()
    from time import monotonic

    started = monotonic()
    check({**BASE, 'port': 59999, 'password': 'report'})
    assert monotonic() - started < 20
