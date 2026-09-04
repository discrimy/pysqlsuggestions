"""
The JSON the editor page consumes, built in one place.

Two demos produce it: the FastAPI server, which completes against live
databases, and the browser build, which runs the same pipeline over a frozen
snapshot under Pyodide. Sharing this module is what stops the two drifting —
the page cannot tell them apart, and neither should have its own idea of what a
suggestion looks like.

Nothing here imports a web framework or a driver, so it runs anywhere the
library does.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from typing import Any

from pysqlsuggestions.api import complete, derive_request, plan_insertion
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.ports import Cache, Catalog
from pysqlsuggestions.types import Kind, Relation, Request, Scope, Suggestion

MAX_SQL_LENGTH = 20_000
"""
The longest statement either demo will answer.

Here rather than in `app.py`, which is where it was and where only the server
could see it. Both demos run the same pipeline and the page cannot tell which
answered, so a bound on one and not the other is the two of them disagreeing
about what they are — and the browser build is the half that needed it more,
having no process boundary and no request timeout around a statement large
enough to be slow.
"""

MAX_PENDING = 64
"""
How many template blanks a request may still have outstanding.

`plan_insertion` takes these from the caller and the library is total over
them, so this is not a correctness guard — it is a public HTTP surface
declining to allocate a list the size of whatever was posted. Sixty-four is far
past any template this library ships, the longest of which has four.
"""


def respond(
    sql: str,
    caret: int,
    dialect: Dialect,
    catalog: Catalog | None,
    *,
    cache: Cache | None = None,
    identity: str = 'demo',
    limit: int = 25,
    pending: Sequence[int] = (),
) -> dict[str, Any]:
    """
    Suggestions for the caret, plus the Request that produced them.

    The Request travels alongside so the page can show what the pure stages
    decided before anything was fetched — the part of a completion engine you
    normally cannot see.

    Each suggestion carries the edit that applies it, planned against this same
    text. A front end splices and moves the caret; it decides nothing, which is
    the only way a rule about separators or namespaces or template blanks stays
    in one place.
    """
    caret = max(0, min(caret, len(sql)))
    started = time.perf_counter()
    request = derive_request(sql, caret, dialect)
    suggestions = complete(sql, caret, dialect, catalog, cache=cache, identity=identity, limit=limit)
    return {
        'available': catalog is not None,
        'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
        'request': describe(request),
        'kind_words': _kind_words(request, dialect),
        'suggestions': [_suggestion(s, sql, dialect, pending) for s in suggestions],
    }


def _kind_words(request: Request, dialect: Dialect) -> dict[str, str]:
    """
    What this dialect calls each kind, where its own word differs.

    Only the namespace kind does. The engine has one `Kind.SCHEMA` for every
    level of a dotted path because they behave identically — but Postgres calls
    that a schema, ClickHouse a database, and Trino a catalog at the first level
    and a schema at the second. Labelling a Trino catalog `schema` states
    something false about the server the user is connected to, and the dialect
    has carried the right word all along.

    Which level it is depends on how much of the path is already written, so
    this is a fact about the request and not only about the dialect.
    """
    word = dialect.namespace.level_of(len(request.qualifier) + 1)
    return {Kind.SCHEMA.value: word} if word else {}


def _suggestion(suggestion: Suggestion, sql: str, dialect: Dialect, pending: Sequence[int]) -> dict[str, Any]:
    plan = plan_insertion(sql, suggestion, dialect=dialect, pending=pending)
    return {
        'insertion': {
            'edits': [{'span': list(e.span), 'text': e.text} for e in plan.edits],
            'caret': plan.caret,
            'pending': list(plan.pending),
            # Whether the list stays open. Read, never derived: comparing the
            # caret against the end of the inserted text got this backwards for
            # a namespace whose dot had to be written, which is every namespace
            # the user has not already dotted.
            'reopen': plan.expects_more,
        },
        'text': suggestion.text,
        'kind': suggestion.kind.value,
        'detail': suggestion.detail,
        'note': suggestion.note,
        'availability': suggestion.availability.value,
        # Both, and separately. `availability` decides whether the row is drawn
        # as usable; `reason` is text, and one candidate carries a reason
        # without being restricted — a star expansion that dropped columns still
        # runs, and saying so is the whole point of it.
        'reason': suggestion.reason,
        'score': suggestion.score,
        'replace_span': list(suggestion.replace_span),
        'takes_arguments': suggestion.takes_arguments,
        'stops': list(suggestion.stops),
        'label': suggestion.label or suggestion.text,
    }


def describe(request: Request) -> dict[str, Any]:
    """The Request, flattened for the page."""
    return {
        'clause': request.clause,
        'expecting': request.expecting,
        'prefix': request.prefix,
        'qualifier': list(request.qualifier),
        'kinds': [kind.value for kind in request.kinds],
        'replace_span': list(request.replace_span),
        'relations': [
            {
                'label': relation.label,
                'path': list(relation.path),
                'source': relation.source,
                'projection': _projection(relation),
            }
            for relation in _visible(request.scope)
        ],
        'ctes': sorted(request.scope.ctes) if request.scope else [],
    }


def _visible(scope: Scope | None) -> Iterator[Relation]:
    if scope is None:
        return
    yield from scope.visible()


def _projection(relation: Relation) -> dict[str, Any] | None:
    """How much of this relation the statement described itself."""
    if relation.projection is None:
        return None
    return {
        'columns': list(relation.projection.columns),
        'stars': [star.label for star in relation.projection.stars],
    }


def backend_entry(
    key: str,
    label: str,
    dialect: Dialect,
    note: str,
    example: str,
    *,
    available: bool,
    paramstyle: str = '',
) -> dict[str, Any]:
    """One row of `/api/backends`, in the shape the page's tab strip wants."""
    return {
        'key': key,
        'label': label,
        'note': note,
        'levels': list(dialect.namespace.levels),
        'paramstyle': paramstyle,
        'example': example,
        'available': available,
    }


def kinds_of(suggestions: Sequence[dict[str, Any]]) -> list[str]:
    """Distinct kinds in first-appearance order, for a legend."""
    seen: dict[str, None] = {}
    for suggestion in suggestions:
        seen.setdefault(str(suggestion['kind']), None)
    return list(seen)
