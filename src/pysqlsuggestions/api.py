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
from pysqlsuggestions.types import Candidate, Column, Function, Kind, Request, Suggestion, Table

DEFAULT_LIMIT = 40


class _NullCatalog:
    """
    Answers nothing, so resolve still runs when no catalog was supplied.

    This is not a stub for testing: a CTE or derived table whose projection is
    fully named needs no catalog at all, and that path runs through resolve. With
    `catalog=None` the alternative would be skipping resolve entirely and losing
    those suggestions, which are the ones a caller without an adapter most wants.
    """

    def schemas(self, catalog: str | None = None) -> list[str]:
        """No namespaces are known."""
        del catalog
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


def apply_suggestion(
    sql: str,
    suggestion: Suggestion,
    *,
    close_parens: bool = True,
) -> tuple[str, int]:
    """
    Insert `suggestion` into `sql`. Returns (new sql, new caret offset).

    Splicing at `suggestion.replace_span` rather than at a word boundary is what
    keeps a qualifier in place: `where u.crea` accepting `created_at` gives
    `where u.created_at`, not `where created_at`. Editors that re-derive the
    span from their own idea of a word get that wrong, which is why the span
    travels with the suggestion.

    A function gets its parentheses closed and the caret parked between them,
    unless the author already typed an opening one.
    """
    start, end = suggestion.replace_span
    text = suggestion.text
    tail = sql[end:]
    caret: int | None = None

    if suggestion.kind is Kind.FUNCTION and close_parens and not tail.lstrip().startswith('('):
        text += '()'
        caret = start + len(text) - 1

    return sql[:start] + text + tail, caret if caret is not None else start + len(text)


__all__ = ['DEFAULT_LIMIT', 'apply_suggestion', 'complete', 'derive_request']
