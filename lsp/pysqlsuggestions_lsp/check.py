"""
Try one profile, once, and say what happened.

Run as `python -m pysqlsuggestions_lsp.check`, reading a profile as JSON on
stdin and writing one JSON object to stdout.

It reuses `Profile.from_options` and `open_catalog` rather than opening a
connection of its own, so testing a profile exercises the path the server will
actually take instead of an approximation of it. That is why this lives here and
not in the extension.

**It always exits 0.** The verdict is the JSON. A non-zero exit means this
harness broke — a missing module, a half-built venv — which is a different
failure from a database that refused, and has to read differently.
"""

from __future__ import annotations

import json
import sys
from importlib import import_module
from typing import Any

from pysqlsuggestions_lsp.connections import DRIVERS, Connect, Profile, open_catalog

Verdict = dict[str, Any]

CONNECT_TIMEOUT = 5
"""Seconds. The driver gives up before the caller has to kill the process."""


def describe(error: Exception, password: str | None) -> str:
    """
    One readable line for `error`.

    Three cases, each measured against pg8000 rather than guessed:

    - A missing password surfaces as `AttributeError: 'NoneType' object has no
      attribute 'decode'`, raised inside the authentication handler. Nothing
      about that tells a user what to do about it.
    - A server error arrives as a dict — `{'S': 'FATAL', 'C': '28P01', 'M':
      'password authentication failed for user "report"'}` — whose `M` is
      already the sentence a user wants and whose raw form is not.
    - Everything else already says something useful and is passed through, with
      its whitespace collapsed because this ends up in a tooltip.
    """
    if password is None and isinstance(error, AttributeError) and 'decode' in str(error):
        return 'the server asked for a password and none is stored'
    for argument in error.args:
        if isinstance(argument, dict) and isinstance(argument.get('M'), str):
            return str(argument['M'])
    return ' '.join(str(error).split())


def _timed_connect(profile: Profile) -> Any:
    """
    Connect with a deadline.

    The caller kills this process eventually, but a driver that gives up first
    can say *why*. A killed process only ever reports that it was killed.
    """
    module, _ = DRIVERS[profile.dialect]
    driver = import_module(module)
    arguments: dict[str, Any] = {'host': profile.host, 'timeout': CONNECT_TIMEOUT}
    for name, value in (
        ('port', profile.port),
        ('database', profile.database),
        ('user', profile.user),
        ('password', profile.password),
    ):
        if value is not None:
            arguments[name] = value
    return driver.connect(**arguments)


def check(options: object, connect: Connect | None = None) -> Verdict:
    """
    Whether `options` describes a connection that works, and what happened.

    Never raises. A verdict is the product; an exception would leave the caller
    with nothing to show, which is the state this feature exists to end.
    """
    profile = Profile.from_options(options)
    if profile is None:
        return {'ok': False, 'detail': 'needs a dialect and a host'}

    if profile.dialect not in DRIVERS:
        return {
            'ok': False,
            'detail': (f'no driver bundled for {profile.dialect} — keywords and quoting still work, schema will not'),
        }

    try:
        catalog = open_catalog(profile, connect=connect or _timed_connect)
        if catalog is None:
            return {'ok': False, 'detail': f'no driver bundled for {profile.dialect}'}
        tables = catalog.tables()
    except Exception as error:  # noqa: BLE001
        return {'ok': False, 'detail': describe(error, profile.password)}

    return {'ok': True, 'detail': f'{len(tables)} relations visible'}


def main() -> int:
    """Read a profile on stdin, write a verdict on stdout, always succeed."""
    try:
        options = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        json.dump({'ok': False, 'detail': f'unreadable profile: {error}'}, sys.stdout)
        return 0
    json.dump(check(options), sys.stdout)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
