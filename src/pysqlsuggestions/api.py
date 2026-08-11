"""
The two entry points.

`derive_request` is pure and needs nothing but text — it is what a caller uses
when no catalog is reachable, which is exactly the situation report_service's
unsupported-database path is in today.

`complete` runs the whole pipeline.
"""

from __future__ import annotations

from collections.abc import Sequence

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.engine.analyse import select_list_end
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.engine.local import local_candidates
from pysqlsuggestions.engine.rank import quote_if_needed, rank
from pysqlsuggestions.engine.request import derive_request
from pysqlsuggestions.ports import Cache, Catalog
from pysqlsuggestions.resolve import resolve
from pysqlsuggestions.types import (
    Candidate,
    Column,
    Edit,
    Function,
    Insertion,
    Kind,
    Request,
    Suggestion,
    Table,
)

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


def plan_insertion(
    sql: str,
    suggestion: Suggestion,
    *,
    dialect: Dialect = ANSI,
    pending: Sequence[int] = (),
    close_parens: bool = True,
) -> Insertion:
    """
    Turn `suggestion` into an edit an editor can apply without deciding anything.

    `pending` is the template blanks still outstanding, as the previous
    insertion handed them back. A suggestion that fills the blank it was
    offered for moves the caret to the next one; one that only half fills it —
    a catalog where a relation belongs — keeps its place.
    """
    start, end = suggestion.replace_span
    text = _separated(sql, start, end, suggestion.text)
    tail = sql[end:]
    caret: int | None = None

    if suggestion.kind is Kind.FUNCTION and close_parens and not tail.lstrip().startswith('('):
        text += '()'
        if suggestion.takes_arguments:
            caret = start + len(text) - 1

    finished = True
    if suggestion.kind is Kind.SCHEMA:
        # A schema is never the end of a relation reference — something follows
        # it, and the dot is the only thing it can be. Leaving the caret on the
        # name means typing a separator the engine already knew was coming, and
        # in a three-level namespace it means a reference that looks finished
        # and is not: `FROM warehouse` with the caret past it reads as a table.
        #
        # Where the dot is already written the caret steps over it instead, so
        # either way the next level is what comes next.
        finished = False
        if tail.startswith('.'):
            caret = start + len(text) + 1
        else:
            text += '.'

    here = Edit(span=(start, end), text=text)
    later = _relation_edit(sql, suggestion, dialect)
    edits = (later, here) if later is not None else (here,)

    default = start + len(text)
    if suggestion.stops:
        # A template opens its own blanks, relative to where it was spliced.
        opened = tuple(start + offset for offset in suggestion.stops)
        return Insertion(edits=edits, caret=opened[0], pending=opened[1:])

    moved = tuple(p + len(text) - (end - start) if p >= end else p for p in pending)
    if moved and finished:
        # Accepting *is* filling that blank, so the caret goes to the next one.
        return Insertion(edits=edits, caret=moved[0], pending=moved[1:])
    return Insertion(edits=edits, caret=caret if caret is not None else default, pending=moved)


def _relation_edit(sql: str, suggestion: Suggestion, dialect: Dialect) -> Edit | None:
    """
    The FROM clause a column needs when the statement has none.

    Comes *after* the column in the text, so it is listed first and applied
    first: making it cannot move the column's own span, and the caret stays
    where the author was rather than jumping to the end of a clause they did
    not type.
    """
    if not suggestion.relation:
        return None
    at = select_list_end(lex(sql, dialect.syntax), suggestion.replace_span[1], dialect)
    reference = '.'.join(quote_if_needed(part, dialect) for part in suggestion.relation)
    return Edit(span=(at, at), text=f' FROM {reference}')


def apply_suggestion(
    sql: str,
    suggestion: Suggestion,
    *,
    dialect: Dialect = ANSI,
    close_parens: bool = True,
) -> tuple[str, int]:
    """
    Insert `suggestion` into `sql`. Returns (new sql, new caret offset).

    The convenience form of `plan_insertion` for callers holding the whole
    statement. An editor should prefer the plan: it carries the span to splice
    and the template blanks still outstanding, neither of which survives being
    reduced to a finished string.

    Splicing at `suggestion.replace_span` rather than at a word boundary is what
    keeps a qualifier in place: `where u.crea` accepting `created_at` gives
    `where u.created_at`, not `where created_at`. Editors that re-derive the
    span from their own idea of a word get that wrong, which is why the span
    travels with the suggestion.
    """
    plan = plan_insertion(sql, suggestion, dialect=dialect, close_parens=close_parens)
    for edit in plan.edits:
        sql = sql[: edit.span[0]] + edit.text + sql[edit.span[1] :]
    return sql, plan.caret


def _separated(sql: str, start: int, end: int, text: str) -> str:
    """
    A leading space when butting `text` against the character before it would
    merge two tokens into one.

    Only when nothing is being replaced. `WHERE id > 1<caret>` accepting `AND`
    must give `1 AND`, not `1AND` — this library's own lexer reads that as two
    tokens, so nothing downstream notices, but Postgres rejects it as trailing
    junk after a numeric literal. A span that *does* cover something ends where
    its own token ends, and a dot, a paren or a space already separates.
    """
    if start != end or start == 0 or not text:
        return text
    before = sql[start - 1]
    merges = (before.isalnum() or before in '_$"`\'') and (text[0].isalnum() or text[0] in '_$"`')
    return f' {text}' if merges else text


__all__ = ['DEFAULT_LIMIT', 'apply_suggestion', 'complete', 'derive_request', 'plan_insertion']
