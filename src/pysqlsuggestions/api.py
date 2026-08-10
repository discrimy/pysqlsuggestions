"""
The two entry points.

`derive_request` is pure and needs nothing but text — it is what a caller uses
when no catalog is reachable, which is exactly the situation report_service's
unsupported-database path is in today.

`complete` runs the whole pipeline.
"""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.engine.local import local_candidates
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.engine.request import derive_request
from pysqlsuggestions.ports import Cache, Catalog
from pysqlsuggestions.resolve import resolve
from pysqlsuggestions.types import Candidate, Column, Function, Request, Suggestion, Table

DEFAULT_LIMIT = 40


class _NullCatalog:
    """
    Answers nothing, so resolve still runs when no catalog was supplied.

    This is not a stub for testing: a CTE or derived table whose projection is
    fully named needs no catalog at all, and that path runs through resolve. With
    `catalog=None` the alternative would be skipping resolve entirely and losing
    those suggestions, which are the ones a caller without an adapter most wants.
    """

    def schemas(self) -> list[str]:
        """No namespaces are known."""
        return []

    def tables(self, schema: str | None = None) -> list[Table]:
        """No relations are known."""
        del schema
        return []

    def columns(self, schema: str | None, table: str) -> list[Column]:
        """No columns are known."""
        del schema, table
        return []

    def functions(self, schema: str | None = None) -> list[Function]:
        """No functions are known."""
        del schema
        return []


def complete(
    sql: str,
    caret: int,
    dialect: Dialect,
    catalog: Catalog | None = None,
    *,
    cache: Cache | None = None,
    identity: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Suggestion]:
    """
    Suggestions for the caret position in `sql`.

    With no `catalog`, only what the query itself describes is offered — CTE
    columns, select-list names, aliases, keywords. That is a genuinely useful
    degraded mode rather than an error, and it is the behaviour a caller gets for
    a backend it has no adapter for.

    `identity` is the end-user role. It leads the cache key, because a cache
    shared across roles leaks one user's readable set into another's session.
    """
    request = derive_request(sql, caret, dialect)
    return rank(_candidates(request, dialect, catalog, cache, identity, limit), request, dialect, limit)


def _candidates(
    request: Request,
    dialect: Dialect,
    catalog: Catalog | None,
    cache: Cache | None,
    identity: str | None,
    limit: int,
) -> list[Candidate]:
    local = local_candidates(request)
    source = catalog if catalog is not None else _NullCatalog()
    fetched = resolve(request, source, dialect, cache=cache, identity=identity, limit=limit * 5)
    known = {(c.kind, c.text) for c in local}
    return [*local, *(c for c in fetched if (c.kind, c.text) not in known)]


__all__ = ['DEFAULT_LIMIT', 'complete', 'derive_request']
