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

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.ports import Catalog
from pysqlsuggestions.types import Relation, Request, Scope, Suggestion


def respond(
    sql: str,
    caret: int,
    dialect: Dialect,
    catalog: Catalog | None,
    *,
    cache: dict[Any, Any] | None = None,
    identity: str = 'demo',
    limit: int = 25,
) -> dict[str, Any]:
    """
    Suggestions for the caret, plus the Request that produced them.

    The Request travels alongside so the page can show what the pure stages
    decided before anything was fetched — the part of a completion engine you
    normally cannot see.
    """
    caret = max(0, min(caret, len(sql)))
    started = time.perf_counter()
    request = derive_request(sql, caret, dialect)
    suggestions = complete(sql, caret, dialect, catalog, cache=cache, identity=identity, limit=limit)
    return {
        'available': catalog is not None,
        'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
        'request': describe(request),
        'suggestions': [_suggestion(s) for s in suggestions],
    }


def _suggestion(suggestion: Suggestion) -> dict[str, Any]:
    return {
        'text': suggestion.text,
        'kind': suggestion.kind.value,
        'detail': suggestion.detail,
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
