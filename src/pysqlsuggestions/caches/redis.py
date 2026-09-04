"""
A `ByteCache` over redis, which never imports redis.

`import redis` appears once, inside `RedisCache.from_url`, and that is the whole
reason the `cache-redis` extra exists: it is the one place a missing dependency
can be diagnosed with a sentence naming the fix.

Everywhere else the adapter duck-types a client the caller built. A version
floor is a promise about a package; a two-method contact surface is a guarantee
about the code, and this one holds across redis-py 3 through 6 and gets valkey,
`RedisCluster`, `fakeredis` and any pooling wrapper for free — none of which a
floor would have covered.
"""

from __future__ import annotations

from typing import Any, Protocol


class RedisClient(Protocol):
    """The contact surface. Two methods, because that is the whole adapter."""

    def get(self, name: str) -> Any:
        """The stored value, or None."""
        ...

    def set(self, name: str, value: bytes, ex: int | None = None) -> Any:
        """Store a value, expiring after `ex` seconds when given."""
        ...


class RedisCache:
    """
    Catalog reads in redis, addressed under a namespace the caller owns.

    `namespace` is required and has no default, because nothing in the key names
    the *server*. Role leads it and dialect follows, which was enough while every
    cache was private to one connection — a dict lives inside one session, so the
    isolation was structural. A shared store removes the structure: staging and
    production, both with a role called `analyst`, would otherwise read each
    other's entries, and since these reads are privilege-filtered the result
    looks like a database permission bug rather than a caching one.

    The contract, therefore: one namespace per database, and per identity you
    cannot name — `identity` already leads the key, so a caller passing one needs
    the namespace only to distinguish databases.

    `default_ttl` is 300 seconds rather than `None`. Every library version gets
    its own keyspace, since the key carries a fingerprint of the cached types'
    shapes, so an upgrade orphans the previous one; the TTL is what bounds how
    long the orphans live. `default_ttl=None` is legal and turns a shared cache
    into one that nothing invalidates and nothing reclaims.
    """

    def __init__(self, client: RedisClient, *, namespace: str, default_ttl: int | None = 300) -> None:
        if not namespace:
            raise ValueError('namespace must name the database this cache belongs to; see the class docstring')
        self._client = client
        self._namespace = namespace
        self._default_ttl = default_ttl

    @classmethod
    def from_url(cls, url: str, *, namespace: str, default_ttl: int | None = 300) -> RedisCache:
        """
        A cache over a client this builds. The only place the library imports redis.

        For anything else — a cluster client, a pool the application already
        owns, valkey, a wrapper — construct the client yourself and pass it to
        `RedisCache`. This exists so that a missing extra produces a sentence
        rather than a `ModuleNotFoundError` from a module the caller never named.
        """
        try:
            import redis
        except ImportError as absent:
            raise ImportError(
                'RedisCache.from_url needs the redis client: pip install pysqlsuggestions[cache-redis]. '
                'Any client with get and set works if you build it yourself: RedisCache(client, namespace=...)'
            ) from absent
        client: Any = redis.Redis.from_url(url)
        return cls(client, namespace=namespace, default_ttl=default_ttl)

    def _name(self, key: str) -> str:
        """The key as this deployment addresses it."""
        return f'{self._namespace}:{key}'

    def get_bytes(self, key: str) -> bytes | None:
        """
        The stored bytes, or `None`.

        A client built with `decode_responses=True` returns `str`, which is a
        perfectly reasonable way to have configured a client used for other
        things, and must not silently break this one.
        """
        found = self._client.get(self._name(key))
        if found is None:
            return None
        return found.encode('utf-8') if isinstance(found, str) else bytes(found)

    def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
        """Store bytes, expiring after `ttl` seconds or this cache's default. `ex=None` means never."""
        seconds = self._default_ttl if ttl is None else ttl
        self._client.set(self._name(key), value, ex=seconds)
