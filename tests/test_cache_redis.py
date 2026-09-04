"""
The redis adapter, against fakeredis rather than a hand-written double.

fakeredis implements redis-py's actual semantics, so it exercises `ex`, the
bytes-versus-str return under `decode_responses`, and binary-safe keys — the
three details the adapter writes docstrings about. A twenty-line fake would
agree with those docstrings' assumptions, which is the wrong thing for a test.
"""

from __future__ import annotations

import builtins
from typing import Any

import fakeredis
import pytest

from pysqlsuggestions import complete
from pysqlsuggestions.caches.redis import RedisCache
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.testing import CacheConformance
from tests.test_complete import catalog

SQL = 'SELECT * FROM reports_report r WHERE r.'
MINUTE = 60
TEN_MINUTES = 600


def test_it_conforms() -> None:
    """The contract, from the harness that exists to state it."""
    cache = RedisCache(fakeredis.FakeStrictRedis(), namespace='test')
    assert CacheConformance.check(cache) == []


def test_a_namespace_is_required() -> None:
    """
    Required, with no default, because the alternative is a silent cross-database read.

    Role leads the key and dialect follows it, but nothing in the key names the
    *server*. That was safe while every cache was private to one connection; a
    shared store removes the structure, and staging and production both holding
    a role called `analyst` would read each other's entries.
    """
    with pytest.raises(TypeError):
        RedisCache(fakeredis.FakeStrictRedis())  # type: ignore[call-arg]


def test_an_empty_namespace_is_refused_too() -> None:
    """`namespace=''` is the shape a caller reaches for when they have not thought about it."""
    with pytest.raises(ValueError, match='namespace'):
        RedisCache(fakeredis.FakeStrictRedis(), namespace='')


def test_two_namespaces_do_not_see_each_other() -> None:
    """The mitigation has to actually mitigate."""
    client = fakeredis.FakeStrictRedis()
    staging = RedisCache(client, namespace='staging')
    production = RedisCache(client, namespace='production')
    staging.set_bytes('k', b'staging')
    assert production.get_bytes('k') is None


def test_a_ttl_reaches_the_server() -> None:
    """`ex` is what bounds how long an orphaned keyspace lives after an upgrade."""
    client = fakeredis.FakeStrictRedis()
    RedisCache(client, namespace='test', default_ttl=MINUTE).set_bytes('k', b'v')
    assert 0 < client.ttl('test:k') <= MINUTE


def test_an_explicit_ttl_overrides_the_default() -> None:
    """A prewarm may deliberately want a longer life than a keystroke's read."""
    client = fakeredis.FakeStrictRedis()
    RedisCache(client, namespace='test', default_ttl=MINUTE).set_bytes('k', b'v', ttl=TEN_MINUTES)
    assert MINUTE < client.ttl('test:k') <= TEN_MINUTES


def test_no_ttl_means_no_expiry() -> None:
    """Legal, and the docstring says what it costs."""
    client = fakeredis.FakeStrictRedis()
    RedisCache(client, namespace='test', default_ttl=None).set_bytes('k', b'v')
    assert client.ttl('test:k') == -1


def test_a_decoding_client_still_works() -> None:
    """
    `decode_responses=True` makes `get` return `str`.

    That is a perfectly reasonable way to have configured a client used for
    other things too, and it must not silently break this one.
    """
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    cache = RedisCache(client, namespace='test')
    cache.set_bytes('k', b'{"t":null,"v":[]}')
    assert cache.get_bytes('k') == b'{"t":null,"v":[]}'


def test_a_completion_reads_through_it() -> None:
    """End to end, since the point of the adapter is that a caret is faster."""
    cache = RedisCache(fakeredis.FakeStrictRedis(), namespace='test')
    cold = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), identity='analyst')]
    complete(SQL, len(SQL), POSTGRES, catalog(), cache=cache, identity='analyst')
    warm = [s.text for s in complete(SQL, len(SQL), POSTGRES, catalog(), cache=cache, identity='analyst')]
    assert warm == cold


def test_from_url_names_the_extra_when_redis_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing extra should say which one, in the one place that can tell."""
    real = builtins.__import__

    def _refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        """Pretend redis is not installed."""
        if name == 'redis':
            raise ImportError('No module named redis')
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', _refuse)
    with pytest.raises(ImportError, match=r'cache-redis'):
        RedisCache.from_url('redis://localhost:6379/0', namespace='test')
