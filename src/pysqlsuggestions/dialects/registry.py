"""
Dialects by name, including ones this package has never heard of.

`pyproject.toml` has advertised the `pysqlsuggestions.dialects` entry-point
group since 0.1.0, and nothing read it — a third-party dialect could register
itself correctly and never be found. This is the half that was missing.

Discovery is deferred to the first call rather than done at import, because it
imports every registered module: a package that ships a dialect should cost
nothing until someone asks for one by name.
"""

from __future__ import annotations

import warnings
from functools import cache
from importlib.metadata import entry_points

from pysqlsuggestions.dialects.base import Dialect

GROUP = 'pysqlsuggestions.dialects'


@cache
def _scan() -> dict[str, Dialect]:
    """
    Every registered dialect, keyed by its entry-point name.

    A broken entry point is skipped rather than raised: one unimportable
    third-party package would otherwise take out completion for every backend,
    including the four that ship here.

    Cached, so the scan happens once. A process that installs a distribution
    while running will not see it, which is the same bargain every entry-point
    consumer makes.
    """
    found: dict[str, Dialect] = {}
    providers: dict[str, str] = {}
    for entry in entry_points(group=GROUP):
        try:
            loaded = entry.load()
        except Exception:  # noqa: BLE001, S112
            continue
        if not isinstance(loaded, Dialect):
            continue
        provider = getattr(getattr(entry, 'dist', None), 'name', None) or 'an unnamed distribution'
        if entry.name in found:
            _announce_collision(entry.name, providers[entry.name], provider)
        found[entry.name] = loaded
        providers[entry.name] = provider
    return found


_BUILT_IN = 'pysqlsuggestions'
"""
The distribution the four shipped dialects register from.

They come through the same entry-point group as anybody else's, so they hold no
privilege here — the name is only how a collision is described.
"""


def _announce_collision(name: str, previous: str, winner: str) -> None:
    """
    Say that two distributions claim one dialect name, and which one won.

    Last-wins is kept rather than made to favour the built-ins, because
    overriding one is a real thing to want: a fork of a backend, or a fix
    carried locally ahead of a release. What was wrong was the silence. The
    winner is whichever distribution `importlib.metadata` enumerates last, which
    is not an order anything documents, so a shadowed dialect could otherwise
    change under an environment that merely got rebuilt.

    Worth hearing because `lsp/connections.py` resolves a dialect by name and
    hands it to `DbapiCatalog`: the winner's introspection SQL is what reaches
    the user's database, while the paramstyle still comes from a table keyed on
    that same name.
    """
    shadowed = f'the built-in dialect {name!r}' if previous == _BUILT_IN else f'dialect {name!r} from {previous!r}'
    warnings.warn(
        f'distribution {winner!r} overrides {shadowed}; {winner!r} is what `named({name!r})` will return',
        UserWarning,
        stacklevel=3,
    )


def available() -> dict[str, Dialect]:
    """
    Every registered dialect, keyed by its entry-point name.

    A copy per call. The scan behind it is cached, and handing that same dict
    back meant a caller editing what looked like its own mapping poisoned the
    registry for the whole process — defeating the `isinstance` check above,
    which is the only thing keeping a non-`Dialect` out of `named`.
    """
    return dict(_scan())


def named(name: str) -> Dialect | None:
    """
    The dialect registered as `name`, or None.

    None rather than a raise: the caller is usually mapping a connection string
    or a user's backend setting, where "no adapter for this one" is an ordinary
    answer and the documented degradation — `complete` without a catalog — is
    still useful.
    """
    return available().get(name)
