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
        )


def _text(value: object) -> str | None:
    """`value` when it is a string, else None."""
    return value if isinstance(value, str) else None


def _connect(profile: Profile) -> Any:
    """Open a connection with the driver the dialect names."""
    module, _ = DRIVERS[profile.dialect]
    driver = import_module(module)
    arguments: dict[str, Any] = {'host': profile.host}
    for name, value in (
        ('port', profile.port),
        ('database', profile.database),
        ('user', profile.user),
        ('password', profile.password),
    ):
        if value is not None:
            arguments[name] = value
    return driver.connect(**arguments)


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
