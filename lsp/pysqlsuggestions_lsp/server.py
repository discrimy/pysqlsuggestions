"""
The server.

One rule governs every handler: a completion request never fails. The library
degrades by design — `resolve.py` implements it so no adapter has to — so an
unreachable database, a rejected password or an unknown dialect all fall back to
what the statement itself describes: keywords, CTE columns, select-list names,
aliases. That is a useful answer. An error popup on a keystroke is not.

One connection per process. The profile arrives in `initializationOptions` and
changing it restarts the server, which is cheap, discards a warm cache only on a
deliberate and rare action, and removes every bug where a server holds state
from a connection it no longer has.

One caret at a time reaches the database. The completion handler runs in pygls's
thread pool rather than on the event loop, so a slow query cannot stop the
server answering; the session's lock is what makes that safe, and it covers the
read as well as the state around it. Serialising costs nothing here — a
completion whose answer arrives late is one the next keystroke has already
replaced — and it means no third-party driver has to be right about sharing a
connection between threads.

The state and the decisions live on `Session`, which knows nothing about pygls.
The handlers below are adapters: find the document, find the offset, ask the
session. That split is not tidiness — `server.workspace` does not exist until a
client has initialized, so logic reachable only through a server object could
not be tested without standing up a client handshake for every case.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from lsprotocol.types import (
    INITIALIZE,
    TEXT_DOCUMENT_COMPLETION,
    CompletionItem,
    CompletionList,
    CompletionOptions,
    CompletionParams,
    InitializeParams,
)
from pygls.lsp.server import LanguageServer

from pysqlsuggestions import complete
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.registry import named
from pysqlsuggestions.types import Suggestion
from pysqlsuggestions_lsp import __version__
from pysqlsuggestions_lsp.check import describe
from pysqlsuggestions_lsp.connections import Connect, Profile, open_catalog
from pysqlsuggestions_lsp.convert import to_item
from pysqlsuggestions_lsp.documents import line_starts, statement_at

log = logging.getLogger(__name__)

TRIGGERS = ['.', ' ', ',', '(']
"""A dot continues a reference; the rest open a position where something is wanted."""

DEGRADED = 'pysqlsuggestions/degraded'
"""
Sent once when the catalog stops being usable, carrying why.

Outside the LSP specification because LSP has no shape for it. A client that
ignores it loses nothing; one that listens can stop claiming the list in front
of the user is schema-aware when it is not.
"""


@dataclass
class Session:
    """
    One connection's worth of state, and the completion decisions that use it.

    Deliberately free of pygls: everything that can be wrong here is reachable
    without a client.
    """

    profile: Profile | None = None
    connect: Connect | None = None
    """How to open a connection. Left None outside tests, where the driver decides."""
    on_degrade: Callable[[str], None] | None = None
    """
    Told once when the catalog stops being usable, with why.

    A degraded list looks entirely healthy — it still holds keywords, CTE
    columns and aliases — so nothing downstream can infer this from the
    suggestions themselves. It has to be said out loud.
    """
    cache: dict[Any, Any] = field(default_factory=dict)
    _catalog: Any = None
    _tried: bool = False
    _announced: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock)
    """
    Serialises everything that touches the catalog or the state around it.

    Reentrant because `catalog()` and `degrade()` are public and are also
    reached from inside the locked region — a plain lock would deadlock the
    first time a read failed.

    Wider than correctness strictly needs: the read itself is serialised too.
    All three bundled drivers report DB-API `threadsafety=2`, so concurrent
    reads on one connection are permitted — but they buy almost nothing here,
    since completions are latest-wins and the cache makes the second read
    instant, and not depending on three third-party contracts is worth the line.
    """

    @property
    def dialect(self) -> Dialect:
        """
        The configured dialect, or ANSI.

        ANSI is not a failure state: an unknown backend degrades rather than
        breaking, and the shipped fallback exists for exactly this. A typo in a
        setting costs schema awareness, not completion.
        """
        if self.profile is None:
            return ANSI
        return named(self.profile.dialect) or ANSI

    def catalog(self) -> Any:
        """
        The catalog, built on first use, or None when there is none to build.

        Building it opens nothing — `open_catalog` defers the connection to the
        first read — so this is cheap and stays out of `initialize`.

        Locked because it is public and reads `_tried` before writing it: two
        callers arriving together would both build one, and the loser's
        connection would be dropped without being closed.
        """
        with self._lock:
            if self._tried or self.profile is None:
                return self._catalog
            self._tried = True
            try:
                self._catalog = open_catalog(self.profile, connect=self.connect)
            except Exception as error:  # noqa: BLE001
                log.exception('could not build a catalog; completing from the statement alone')
                self.degrade(describe(error, self.profile.password if self.profile else None))
            return self._catalog

    def degrade(self, why: str) -> None:
        """
        Stop using the catalog until the server is restarted, and say so once.

        Recorded rather than retried per keystroke: a database that is down
        stays down for the length of a coffee, and retrying would mean a
        blocking connection attempt for every character typed.

        Announced once for the same reason — this is a state change, not a
        running commentary on every keystroke that follows it.
        """
        with self._lock:
            self._catalog = None
            self._tried = True
            if self.on_degrade is not None and not self._announced:
                self._announced = True
                self.on_degrade(why)

    def suggest(self, text: str, offset: int) -> list[CompletionItem]:
        """
        Items for a caret at `offset` in `text`. Never raises.

        `text` is the whole document; the engine sees one statement of it.
        """
        caret = max(0, min(offset, len(text)))
        dialect = self.dialect
        statement, base = statement_at(text, caret, dialect.syntax)
        starts = line_starts(text)
        within = caret - base
        suggestions = self._from_catalog(statement, within, dialect)
        if suggestions is None:
            # Outside the lock, deliberately: a read that failed must not hold
            # it while answering without one.
            suggestions = complete(statement, within, dialect)
        return [to_item(statement, base, starts, s, index, dialect) for index, s in enumerate(suggestions)]

    def _from_catalog(self, statement: str, within: int, dialect: Dialect) -> list[Suggestion] | None:
        """
        Suggestions read through the catalog, or None to complete without one.

        None covers all three ways there is nothing to read through: no profile,
        a dialect with no bundled driver, and a read that just failed. The
        caller answers from the statement alone in each case, which is the
        library's documented degradation and a useful answer.
        """
        with self._lock:
            catalog = self.catalog()
            if catalog is None:
                return None
            try:
                return complete(
                    statement,
                    within,
                    dialect,
                    catalog,
                    cache=self.cache,
                    identity=self.profile.user if self.profile else None,
                )
            except Exception as error:  # noqa: BLE001
                log.exception('the catalog failed; completing from the statement alone')
                self.degrade(describe(error, self.profile.password if self.profile else None))
                return None


class SqlServer(LanguageServer):
    """A language server wrapping one `Session`."""

    def __init__(self, session: Session) -> None:
        super().__init__(name='pysqlsuggestions', version=__version__)
        self.session = session


def create_server(connect: Connect | None = None) -> SqlServer:
    """
    A server with its handlers registered. `connect` is for tests.

    Handlers are closures rather than methods because pygls calls a registered
    feature with the parameters alone.
    """
    server = SqlServer(Session(connect=connect))
    server.session.on_degrade = lambda why: server.protocol.notify(DEGRADED, {'reason': why})

    @server.feature(INITIALIZE)
    def initialize(params: InitializeParams) -> None:
        """Record the profile. Nothing is connected here."""
        server.session.profile = Profile.from_options(params.initialization_options)
        if server.session.profile is None:
            log.info('no connection profile; completing from the statement alone')

    @server.feature(TEXT_DOCUMENT_COMPLETION, CompletionOptions(trigger_characters=TRIGGERS))
    @server.thread()
    def completion(params: CompletionParams) -> CompletionList:
        """
        Suggestions for the caret. Never raises.

        Marked for the thread pool because it may read a database. pygls calls
        an unmarked handler inline on the event loop, where a slow
        introspection query would stop the server answering anything — the
        session's lock is what makes that concurrency safe.
        """
        # `get_text_document` invents one pointing at disk for a URI the client
        # never opened, and reading `.source` then raises OSError out of the
        # handler as a JSON-RPC error — which this module's one rule forbids.
        # Three ordinary routes reach it: a `didClose` racing a completion
        # already dispatched to this pool, one Windows path spelled two ways,
        # and an `untitled:` document, whose URI resolves to a *relative* path
        # and so reads whatever sits at that name in the server's own directory.
        # That last one is the reason this returns empty rather than falling back
        # to `document.source`: answering from an unrelated file is worse than
        # answering with nothing.
        document = server.workspace.get_text_document(params.text_document.uri)
        try:
            text = document.source
        except OSError:
            return CompletionList(is_incomplete=False, items=[])
        offset = document.offset_at_position(params.position)
        return CompletionList(is_incomplete=False, items=server.session.suggest(text, offset))

    del initialize, completion
    return server
