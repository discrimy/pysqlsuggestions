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
from pysqlsuggestions.ports import ByteCache, Cache, Catalog, ObjectCache
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

    A `cache` satisfying neither protocol is refused here rather than ignored. A
    plain dict is exactly that case — it has `get` and no `set` — and treating it
    as "no cache" would leave a caller written against the pre-0.9.0 port
    correct, silent and uncached, with nothing to notice but suggestions that
    had quietly got slower.
    """
    if cache is not None and not isinstance(cache, ObjectCache | ByteCache):
        raise TypeError(
            'cache must satisfy ObjectCache (get/set) or ByteCache (get_bytes/set_bytes). '
            'A plain dict satisfies neither — use pysqlsuggestions.caches.MemoryCache().'
        )
    request = derive_request(sql, caret, dialect)
    return rank(_candidates(request, dialect, catalog, cache, identity), request, dialect, limit)


def _candidates(
    request: Request,
    dialect: Dialect,
    catalog: Catalog | None,
    cache: Cache | None,
    identity: str | None,
) -> list[Candidate]:
    local = local_candidates(request)
    source = catalog if catalog is not None else _NullCatalog()
    fetched = resolve(request, source, dialect, cache=cache, identity=identity)
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
    text = _led_in(sql, start, end, suggestion.text)
    tail = sql[end:]
    caret: int | None = None

    # Two questions with different answers. `finished` is whether accepting this
    # fills the template blank it was offered for; `more` is whether the caret
    # ends up somewhere completion should carry straight on from. A function
    # taking arguments finishes its blank and still wants the list open.
    finished = True
    more = False

    if suggestion.kind is Kind.FUNCTION and close_parens and not tail.lstrip().startswith('('):
        text += '()'
        if suggestion.takes_arguments:
            caret = start + len(text) - 1
            more = True

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
        more = True
        if tail.startswith('.'):
            caret = start + len(text) + 1
        else:
            text += '.'

    # After the kind-specific suffixes, not before them. A schema appends its
    # own dot and a function its parentheses, and neither fuses with what
    # follows — checking first added a separator they were about to make
    # unnecessary, writing `FROM public .orders` and `SELECT lower ()x`.
    text = _led_out(sql, start, end, text)

    here = Edit(span=(start, end), text=text)
    later = _relation_edit(sql, suggestion, dialect)
    edits = (later, here) if later is not None else (here,)

    default = start + len(text)
    if suggestion.stops:
        # A template opens its own blanks, relative to where it was spliced.
        opened = tuple(start + offset for offset in suggestion.stops)
        return Insertion(edits=edits, caret=opened[0], pending=opened[1:], expects_more=True)

    moved = tuple(p + len(text) - (end - start) if p >= end else p for p in pending)
    if moved and finished and caret is None:
        # Accepting *is* filling that blank, so the caret goes to the next one.
        return Insertion(edits=edits, caret=moved[0], pending=moved[1:], expects_more=True)
    if moved and caret is not None:
        # Except when this insertion opened somewhere of its own to type. An
        # empty argument list is the only such place, and it wins: the caret
        # belongs where the next character goes, which is what `more` above
        # already claims for this case — a claim that means nothing if the caret
        # has been sent to the end of the statement instead, which is exactly
        # where the shipped template's trailing stop put it.
        #
        # The blank is *not* consumed. Writing an argument is not filling the
        # select-list blank the function was offered for, so it stays pending
        # and the next tab still reaches it.
        return Insertion(edits=edits, caret=caret, pending=moved, expects_more=True)
    return Insertion(
        edits=edits,
        caret=caret if caret is not None else default,
        pending=moved,
        expects_more=more,
    )


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


def _led_in(sql: str, start: int, end: int, text: str) -> str:
    """
    A leading space when butting `text` against the character before it would
    merge two tokens into one.

    Only when nothing is being replaced. `WHERE id > 1<caret>` accepting `AND`
    must give `1 AND`, not `1AND` — this library's own lexer reads that as two
    tokens, so nothing downstream notices, but Postgres rejects it as trailing
    junk after a numeric literal. A span that *does* cover something ends where
    its own token ends, and a dot, a paren or a space already separates.
    """
    if start != end or not text or not start:
        return text
    return f' {text}' if _fuses(sql[start - 1], text[0]) else text


def _led_out(sql: str, start: int, end: int, text: str) -> str:
    """
    The same rule read forwards, which it was not.

    A caret in front of an existing word is how a column gets added to a
    statement already written — click after `id`, type a comma, ask — and
    accepting there spliced into the next word: `SELECT id ASFROM flight`, which
    the server refuses just as it refuses `1AND`.
    """
    if start != end or not text or end >= len(sql):
        return text
    return f'{text} ' if _fuses(text[-1], sql[end]) else text


_CLOSES_A_NAME = '_$"`\''
"""Characters a name can end with, so that writing a name straight after one merges the two."""

_OPENS_A_NAME = '_$"`'
"""The same, for what a name can begin with. No apostrophe: a literal opens with its own quote."""


def _fuses(before: str, after: str) -> bool:
    """Whether writing `after` hard against `before` would read back as one token."""
    return (before.isalnum() or before in _CLOSES_A_NAME) and (after.isalnum() or after in _OPENS_A_NAME)


__all__ = ['DEFAULT_LIMIT', 'apply_suggestion', 'complete', 'derive_request', 'plan_insertion']
