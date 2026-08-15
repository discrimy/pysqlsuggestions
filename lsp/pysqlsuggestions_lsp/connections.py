"""
A connection profile, as a catalog.

The dialect comes from the entry-point registry rather than a hard-coded map, so
a third-party dialect works here without this file knowing it exists. The driver
does not, because a driver is a module to import and a paramstyle to declare.

Nothing in `DRIVERS` needs a compiled wheel. Postgres uses pg8000, which is pure;
Trino and ClickHouse use the library's own HTTP readers, because both clients
hard-require compression codecs that ship compiled. That is not incidental — it
is what lets the same wheel set install on every platform — and it is why every
dialect the library serves is now served here too. `open_catalog` still returns
None rather than failing for a dialect nothing here can reach, which is what a
third-party dialect with no driver gets.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from pysqlsuggestions.catalogs.dbapi import Cursor, DbapiCatalog
from pysqlsuggestions.dialects.registry import named

CONNECT_TIMEOUT = 5
"""
Seconds a connection attempt may take before the driver gives up.

Lives here rather than beside the check tooling because both paths need it and
this is the one they share: a driver that gives up can say *why*, where a caller
that kills the process only ever reports that it was killed. The live path went
without it and inherited the OS timeout instead — 21 seconds on Windows, about
130 on Linux — while holding the session lock every other caret waits on.

Bounds establishing the connection and nothing after it. See `READ_TIMEOUT`.
"""

READ_TIMEOUT = 30
"""
Seconds a catalog read may take on a connection already up.

Separate from `CONNECT_TIMEOUT` because the two answer different questions and
one number answered both by accident. `socket.create_connection(address,
timeout)` leaves the timeout *on the socket*, so the bound written for the
connect went on governing every later `recv` — and a read that overran it
raised into the `except Exception` in `Session.suggest`, which calls
`degrade()` and drops the catalog for the rest of the session. Five seconds is
right for reaching a host and wrong for reading a catalog out of it: the
databases with enough tables to be slow are the ones worth completing against,
so the bound meant for an unreachable host was disabling the feature on the
healthiest ones.

Thirty rather than none. A read that never returns still has to end, and a
completion nobody is waiting for any more is what the timeout is for.
"""

Connect = Callable[['Profile'], Any]

DRIVERS: dict[str, tuple[str, str]] = {
    'postgres': ('pg8000.dbapi', 'format'),
    'trino': ('pysqlsuggestions.catalogs.trino_http', 'qmark'),
    'clickhouse': ('pysqlsuggestions.catalogs.clickhouse_http', 'named'),
}
"""
Dialect name to (module, paramstyle). Nothing here is compiled.

Named by module rather than imported so this file pulls in no transport at all,
and so `check.py` can reach the same table without keeping a second list.
"""


@dataclass(frozen=True, slots=True)
class Profile:
    """Where to connect, and as whom."""

    dialect: str
    host: str
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    """Kept out of `repr` — this object reaches logs and crash reports."""

    verify: bool = True
    """
    Whether to check the server's certificate. Only meaningful with `secure`.

    Default true, and a malformed value keeps it true — `from_options` reads it
    as `is not False`, so a `"no"` typed into settings.json leaves verification
    on. Every other field there fails towards absence; this one fails towards
    the safe answer, because absence and False are not the same thing here.
    """

    secure: bool = False
    """
    Whether to speak TLS.

    Default false because the docker fixtures and most local backends are
    plaintext, and a default that breaks every local setup to protect a remote
    one nobody described is a default people turn off rather than one that
    protects anybody. Trino refuses password authentication without it and says
    so at connect time, rather than sending the password to find out.
    """

    @classmethod
    def from_options(cls, options: object) -> Profile | None:
        """
        A profile from a client's `initializationOptions`, or None.

        None rather than a raise: no profile is the documented degraded mode,
        where completion answers from the statement alone. A client that sent
        nothing gets a working server, not a failed one.

        Every field is type-checked rather than trusted. This is whatever the
        client put on the wire, and a port arriving as a string would otherwise
        surface as a driver error on the first keystroke.
        """
        if not isinstance(options, dict):
            return None
        dialect = options.get('dialect')
        host = options.get('host')
        if not isinstance(dialect, str) or not isinstance(host, str):
            return None
        port = options.get('port')
        return cls(
            dialect=dialect,
            host=host,
            port=port if isinstance(port, int) else None,
            database=_text(options.get('database')),
            user=_text(options.get('user')),
            password=_text(options.get('password')),
            # `is True` rather than `bool(...)`: this field is type-checked like
            # every other, and a `"yes"` from hand-edited settings is not True.
            secure=options.get('secure') is True,
            # `is not False`: only an explicit false turns verification off, so
            # a missing key and a mistyped one both leave it on.
            verify=options.get('verify') is not False,
        )


def _text(value: object) -> str | None:
    """`value` when it is a string, else None."""
    return value if isinstance(value, str) else None


def _connect(profile: Profile, opener: Callable[..., Any] | None = None) -> Any:
    """
    Open a connection with the driver the dialect names.

    `opener` exists so a test can see what would be passed. Patching
    `import_module` instead would assert against a mock rather than against the
    arguments, which is the part that can be wrong.
    """
    module, _ = DRIVERS[profile.dialect]
    connect_to = opener or import_module(module).connect
    arguments: dict[str, Any] = {'host': profile.host}
    # pg8000 takes `ssl_context`, not `secure`; only the readers understand this
    # flag. Passing it to a driver that has never heard of it is a TypeError
    # raised on the first catalog read, which degrades to a catalog-free list —
    # so the user sees fewer suggestions and no reason for it.
    over_http = module.startswith('pysqlsuggestions.')
    if over_http:
        arguments['secure'] = profile.secure
        arguments['verify'] = profile.verify
    else:
        # Bounded for the reason `check._timed_connect` states and this path did
        # not honour: a host that drops packets rather than refusing them leaves
        # the driver in the OS connect timeout — 21 seconds on Windows, about
        # 130 on Linux — and `Session._lock` covers this call, so every caret
        # arriving meanwhile waits with it. "Test connection" gave up in five
        # seconds while the completion behind it hung, against the same
        # unreachable host.
        #
        # The readers are left out because their `timeout` is a different
        # quantity: `_http.request` hands it to `urlopen`, where it bounds a
        # whole round trip rather than the reaching of a host. Passing this one
        # cut every introspection query at five seconds. `_http.DEFAULT_TIMEOUT`
        # is that module's own answer and is the one that should stand.
        arguments['timeout'] = CONNECT_TIMEOUT
    for name, value in (
        ('port', profile.port),
        ('database', profile.database),
        ('user', profile.user),
        ('password', profile.password),
    ):
        if value is not None:
            arguments[name] = value
    connection = connect_to(**arguments)
    if not over_http:
        _lift_the_connect_bound(connection)
    return connection


def _lift_the_connect_bound(connection: Any) -> None:
    """
    Move the socket off the connect bound and on to the read one.

    `CONNECT_TIMEOUT` reaches pg8000 as `socket.create_connection`'s timeout,
    and that call leaves the timeout *on* the socket — so without this every
    later `recv` is bounded by the number written for reaching the host, and a
    catalog read on a database with enough tables to be slow raises into
    `Session.suggest`'s `except Exception`, which degrades the session.

    Reached through the driver's private attribute because DB-API declares no
    way to ask. There is no public socket on a connection, and the alternative
    to reaching for this one is to leave a bound in place that is known to be
    the wrong one.

    Silent when the connection holds no socket under that name. A driver this
    cannot correct is still a driver that connected, and refusing to use it
    would trade a timeout that is too tight for no completions at all.
    """
    socket = getattr(connection, '_usock', None)
    if socket is not None and hasattr(socket, 'settimeout'):
        socket.settimeout(READ_TIMEOUT)


def open_catalog(profile: Profile, connect: Connect | None = None) -> DbapiCatalog | None:
    """
    A catalog for `profile`, or None when nothing here can serve it.

    Nothing is connected yet. `DbapiCatalog` calls `open_cursor` per query, so
    the socket opens on the first catalog read and a warm cache means an editor
    session touches the database not at all. Opening a document must never open
    a connection: a database behind a VPN that happens to be down would hang the
    editor on file open rather than on a completion the user asked for.
    """
    dialect = named(profile.dialect)
    if dialect is None or profile.dialect not in DRIVERS:
        return None
    _, paramstyle = DRIVERS[profile.dialect]
    opener = connect or _connect
    held: list[Any] = []
    guard = threading.Lock()

    def open_cursor() -> Cursor:
        """
        A cursor on the one connection, opening it on first use.

        Locked because the check and the connect are two steps: two callers
        arriving together would both find `held` empty, and the second
        connection would never be reachable again — nor closed.

        The lock covers the connect, not the cursor: DB-API `threadsafety=2`,
        which all three bundled drivers report, means a connection may be
        shared between threads while a cursor may not, and every caller here
        gets its own.
        """
        with guard:
            if not held:
                held.append(opener(profile))
        cursor: Cursor = held[0].cursor()
        return cursor

    return DbapiCatalog(open_cursor, dialect, paramstyle=paramstyle)
