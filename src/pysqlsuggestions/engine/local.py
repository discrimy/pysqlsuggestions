"""
Candidates the query already contains — no catalog access at all.

These fall out of scope analysis alone, and they are the suggestions users notice
first, because no amount of schema knowledge produces them: `GROUP BY` wants the
expressions from *this* SELECT list, not every column in the database.

Kept out of `Request` on purpose. `Request` describes what to look for; this
module answers part of that description from the query text itself, and
`complete()` merges the two before ranking.
"""

from __future__ import annotations

from pysqlsuggestions.types import Candidate, Kind, Request

_SELECT_LIST_CLAUSES = frozenset({'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT BY'})
"""
Clauses answered from this query's own select list.

`PARTITION BY` is not one of them. It sits inside a window spec, which sits
inside a select item, and offering that item its own output name is circular —
Postgres does not make select-list aliases visible there either.
"""
_ALIAS_CLAUSES = frozenset({'FROM', 'JOIN', 'UPDATE', 'DELETE FROM', 'INSERT INTO'})


def local_candidates(request: Request) -> list[Candidate]:
    """Everything answerable from `request` alone."""
    if not request.kinds or request.scope is None:
        return []
    if request.continues:
        # A half-written construct answers itself: `IS ` and `CASE WHEN id = 1 `
        # take their own words, and no catalog holds them.
        return [
            Candidate(text=word, kind=Kind.KEYWORD, detail='continues the expression', position=index, origin='local')
            for index, word in enumerate(request.continues)
        ]
    if request.expecting == 'alias':
        # Only where a relation was just named. The `AS` in `count(*) AS ` names
        # an output column, and a relation's initials are no answer to that.
        return _alias_suggestions(request) if request.clause in _ALIAS_CLAUSES else []
    if request.clause in _SELECT_LIST_CLAUSES:
        return _select_list(request)
    if request.clause in _ALIAS_CLAUSES and request.expecting == 'connective':
        # Only once the reference is complete, which is the one place an alias
        # may be written. Everything else in these clauses is mid-reference: the
        # column list of `INSERT INTO orders (`, which has an unaliased relation
        # in scope and would otherwise be offered names for it, and a half-typed
        # `FROM events.`, which has no relation to name yet.
        return _alias_suggestions(request)
    return []


def _select_list(request: Request) -> list[Candidate]:
    """
    The output names of this query's own SELECT list.

    `GROUP BY` takes the non-aggregated ones and `ORDER BY` accepts aliases that
    are not columns of any table — a catalog cannot supply either.

    Only where an operand is wanted. `GROUP BY d.title ` has one already, and a
    second name there needs a comma first.
    """
    scope = request.scope
    projection = scope.projection if scope else None
    if projection is None or not projection.columns or request.expecting != 'operand':
        return []

    candidates = [
        Candidate(
            text=name,
            kind=Kind.COLUMN,
            detail='select list',
            position=index,
            origin='local',
        )
        for index, name in enumerate(projection.columns)
    ]

    return candidates


def _alias_suggestions(request: Request) -> list[Candidate]:
    """
    An alias for the relation just written: `FROM reports_report <caret>` -> `rr`.

    Only fires when the relation has no alias yet, and only for the most recently
    named one — suggesting an alias for a relation the user finished with three
    clauses ago is noise.

    The most recent relation, that is, rather than the most recent one still
    lacking an alias. An alias attaches to whatever was just written, so where
    that already has one the answer is nothing: `FROM flight JOIN booking AS b `
    offering `f` writes `booking AS b f`, which parses as nothing at all.
    """
    scope = request.scope
    if scope is None or request.prefix or not scope.relations:
        return []
    latest = scope.relations[-1]
    if latest.alias is not None or not latest.path:
        return []
    name = latest.path[-1]
    return [
        Candidate(text=alias, kind=Kind.ALIAS, detail=f'alias for {name}', position=index, origin='local')
        for index, alias in enumerate(_alias_forms(name))
    ]


def _alias_forms(name: str) -> list[str]:
    """
    Alias conventions, most idiomatic first.

    `order_items` -> `oi`, `reports_report` -> `rr`, `users` -> `u`. Initials of
    the underscore-separated words, then the bare first letter, then a truncation.
    """
    words = [word for word in name.lower().split('_') if word]
    if not words:
        return []
    forms = []
    initials = ''.join(word[0] for word in words)
    if len(initials) > 1:
        forms.append(initials)
    forms.append(words[0][0])
    if len(words[0]) > 3:  # noqa: PLR2004
        forms.append(words[0][:3])
    seen: dict[str, None] = {}
    for form in forms:
        seen.setdefault(form, None)
    return list(seen)
