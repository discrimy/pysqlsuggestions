"""
What an in-process fake structurally cannot prove.

Two things, and only two: that a *server* enforces the expiry, and that a client
whose socket has gone latches the cache off rather than raising into a
completion. The second is the failure this whole design is built around, and it
deserves to be proven against something that can genuinely be unplugged.

Everything else about the adapter is covered in the fast suite against fakeredis.
"""

from __future__ import annotations

from typing import Any

import pytest

from pysqlsuggestions import complete
from pysqlsuggestions.caches.redis import RedisCache
from pysqlsuggestions.dialects.postgres import POSTGRES
from tests.test_complete import catalog

pytestmark = pytest.mark.integration

SQL = 'SELECT * FROM reports_report r WHERE r.'
URL = 'redis://localhost:57379/0'
NOWHERE = 'redis://localhost:57399/0'
NAMESPACE = 'pysqlsuggestions-test'
"""A port nothing listens on, which is how a socket is unplugged without stopping a container."""

HALF_A_MINUTE = 30


@pytest.fixture
def client() -> Any:
    """The compose service's client, skipping when it is not up."""
    redis = pytest.importorskip('redis')
    found = redis.Redis.from_url(URL, socket_connect_timeout=1)
    try:
        found.ping()
    except Exception as unreachable:  # noqa: BLE001
        pytest.skip(f'redis is not reachable: {unreachable}')
    found.flushdb()
    return found


@pytest.fixture
def cache(client: Any) -> RedisCache:
    """A cache over that client."""
    return RedisCache(client, namespace=NAMESPACE, default_ttl=60)


def test_a_completion_reads_through_a_real_server(cache: RedisCache) -> None:
    """The whole point, over a socket."""
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=cache, identity='analyst')
    warm = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=cache, identity='analyst')]
    assert warm == cold


def test_the_server_enforces_the_expiry(cache: RedisCache, client: Any) -> None:
    """
    fakeredis agrees with our reading of `ex`; a server is what settles it.

    The TTL is what bounds how long a previous library version's orphaned
    keyspace survives, so an `ex` the server ignored would be an unbounded leak
    nobody would notice until the store filled.
    """
    cache.set_bytes('expiring', b'v', ttl=HALF_A_MINUTE)
    remaining = client.ttl(f'{NAMESPACE}:expiring')
    assert 0 < remaining <= HALF_A_MINUTE


def test_a_dead_socket_costs_suggestions_and_not_the_completion() -> None:
    """
    The failure the latch exists for, against a socket that is genuinely gone.

    A real client pointed at a port nothing listens on, which unplugs the socket
    without stopping a container. The completion must still answer, with what it
    would have answered cold.

    No `client` fixture, so this runs whether or not the compose service is up:
    a refused connection is a refused connection either way, and the point is
    what the library does with one.
    """
    redis = pytest.importorskip('redis')
    dead = RedisCache(redis.Redis.from_url(NOWHERE, socket_connect_timeout=1), namespace=NAMESPACE)
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    found = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=dead, identity='analyst')]
    assert found == cold
