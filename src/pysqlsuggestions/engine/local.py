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

_SELECT_LIST_CLAUSES = frozenset({'GROUP BY', 'ORDER BY', 'HAVING', 'PARTITION BY', 'LIMIT BY'})
_ALIAS_CLAUSES = frozenset({'FROM', 'JOIN', 'UPDATE', 'DELETE FROM', 'INSERT INTO'})
_MAX_ORDINALS = 9


def local_candidates(request: Request) -> list[Candidate]:
    """Everything answerable from `request` alone."""
    if not request.kinds or request.scope is None:
        return []
    if request.clause in _SELECT_LIST_CLAUSES:
        return _select_list(request)
    if request.clause in _ALIAS_CLAUSES:
        return _alias_suggestions(request)
    return []


def _select_list(request: Request) -> list[Candidate]:
    """
    The output names of this query's own SELECT list.

    `GROUP BY` takes the non-aggregated ones and `ORDER BY` accepts aliases and
    ordinals that are not columns of any table — a catalog cannot supply either.
    """
    scope = request.scope
    projection = scope.projection if scope else None
    if projection is None or not projection.columns:
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

    if request.clause == 'ORDER BY':
        # Ordinals sit after the names they stand for: `ORDER BY 1` is a shorthand,
        # not the first thing to offer. They are literals, so they are never quoted.
        offset = len(projection.columns)
        candidates += [
            Candidate(
                text=str(index + 1),
                kind=Kind.COLUMN,
                detail=f'ordinal: {name}',
                position=offset + index,
                origin='local',
                literal=True,
            )
            for index, name in enumerate(projection.columns[:_MAX_ORDINALS])
        ]
    return candidates


def _alias_suggestions(request: Request) -> list[Candidate]:
    """
    An alias for the relation just written: `FROM reports_report <caret>` -> `rr`.

    Only fires when the relation has no alias yet, and only for the most recently
    named one — suggesting an alias for a relation the user finished with three
    clauses ago is noise.
    """
    scope = request.scope
    if scope is None or request.prefix:
        return []
    unaliased = [r for r in scope.relations if r.alias is None and r.path]
    if not unaliased:
        return []
    name = unaliased[-1].path[-1]
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
