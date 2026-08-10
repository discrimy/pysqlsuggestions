"""
Stage five: score candidates, decide casing and quoting, emit suggestions.

Pure and totally ordered, so the output is stable and tests can assert on it.

Alphabetical ordering is what makes a completion engine feel dumb, so ranking
here is deliberate: how well the text matches what was typed, then how relevant
the kind is at this caret, then declaration order — authors put important columns
first — and only then alphabetical, as a tiebreak that never decides anything on
its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.types import Candidate, Kind, Request, Suggestion

_EXACT_PREFIX = 100.0
_FOLDED_PREFIX = 70.0
_WORD_PREFIX = 55.0
_WORD_BOUNDARY = 40.0

_KIND_STEP = 5.0
_LOCAL_BONUS = 15.0
"""Candidates derived from the query itself outrank anything fetched: the user wrote them."""

_MAX_POSITION_PENALTY = 50
_POSITION_WEIGHT = 0.1

_PLAIN_LOWER = re.compile(r'[a-z_][a-z0-9_$]*\Z')
_PLAIN_ANY_CASE = re.compile(r'[A-Za-z_][A-Za-z0-9_$]*\Z')


def rank(
    candidates: Iterable[Candidate],
    request: Request,
    dialect: Dialect,
    limit: int | None = None,
) -> list[Suggestion]:
    """Score, sort and render `candidates` for `request`."""
    kind_rank = {kind: index for index, kind in enumerate(request.kinds)}
    scored: list[tuple[float, str, Suggestion]] = []

    for candidate in candidates:
        strength = _match_strength(candidate.text, request.prefix)
        if strength is None:
            continue
        score = strength + _kind_bonus(candidate.kind, kind_rank, len(request.kinds))
        score -= min(candidate.position, _MAX_POSITION_PENALTY) * _POSITION_WEIGHT
        if candidate.origin == 'local':
            score += _LOCAL_BONUS
        text = _render(candidate, request, dialect)
        scored.append(
            (
                -score,
                text.lower(),
                Suggestion(
                    text=text,
                    kind=candidate.kind,
                    replace_span=request.replace_span,
                    score=round(score, 3),
                    detail=candidate.detail,
                ),
            ),
        )

    scored.sort(key=lambda row: (row[0], row[1]))
    ordered = [row[2] for row in scored]
    return ordered[:limit] if limit is not None else ordered


def _match_strength(text: str, prefix: str) -> float | None:
    """
    How well `text` matches `prefix`, or None when it does not match at all.

    Four tiers, all anchored to a word boundary. Snake_case names bury the
    meaningful word — nobody types `reports_` to reach `reports_database`, they
    type `data` — so a prefix of any word component counts, while a fragment
    starting mid-word (`atabas`) does not. That is the line plan.md §6 draws:
    looser matching demos well and then degrades badly, because on a 400-table
    schema a three-character prefix matching sixty things is worse than matching
    nothing.
    """
    if not prefix:
        return _EXACT_PREFIX
    if text.startswith(prefix):
        return _EXACT_PREFIX
    folded = prefix.lower()
    if text.lower().startswith(folded):
        return _FOLDED_PREFIX
    words = _words(text)
    if any(word.startswith(folded) for word in words):
        return _WORD_PREFIX
    if ''.join(word[0] for word in words).startswith(folded):
        return _WORD_BOUNDARY
    return None


def _words(text: str) -> list[str]:
    """
    The lowercased word components of an identifier.

    Split on underscores and dollars, and on a lower-to-upper transition so
    `MonthlyTotals` reads as two words rather than one.
    """
    words: list[str] = []
    current: list[str] = []
    previous = ''
    for char in text:
        if char in '_$':
            if current:
                words.append(''.join(current).lower())
                current = []
            previous = char
            continue
        if current and previous.islower() and char.isupper():
            words.append(''.join(current).lower())
            current = []
        current.append(char)
        previous = char
    if current:
        words.append(''.join(current).lower())
    return words


def _initials(text: str) -> str:
    """The first letter of each word, so `oi` finds `order_items`."""
    return ''.join(word[0] for word in _words(text) if word)


def _kind_bonus(kind: Kind, kind_rank: dict[Kind, int], total: int) -> float:
    """Kinds earlier in `request.kinds` are more relevant here."""
    index = kind_rank.get(kind)
    return 0.0 if index is None else (total - index) * _KIND_STEP


def _render(candidate: Candidate, request: Request, dialect: Dialect) -> str:
    """The text to insert: quoted if it must be, cased to match what the user is typing."""
    if candidate.literal:
        return candidate.text
    if candidate.kind is Kind.KEYWORD:
        return candidate.text.lower() if _typing_lowercase(request) else candidate.text.upper()
    return quote_if_needed(candidate.text, dialect)


def _typing_lowercase(request: Request) -> bool:
    """Follow the case the user has been writing keywords in."""
    return bool(request.prefix) and request.prefix.islower()


def quote_if_needed(name: str, dialect: Dialect) -> str:
    """
    Quote `name` when the dialect would not read it back as written.

    Reserved words ship offline precisely so this decision can be made before any
    connection exists.
    """
    if not _needs_quoting(name, dialect):
        return name
    quote = dialect.syntax.identifier_quotes[0]
    return f'{quote}{name.replace(quote, quote * 2)}{quote}'


def _needs_quoting(name: str, dialect: Dialect) -> bool:
    if not name:
        return True
    if name.lower() in dialect.reserved:
        return True
    if dialect.syntax.unquoted_case == 'preserve':
        return _PLAIN_ANY_CASE.match(name) is None
    if dialect.syntax.unquoted_case == 'upper':
        return name != name.upper() or _PLAIN_ANY_CASE.match(name) is None
    return name != name.lower() or _PLAIN_LOWER.match(name) is None


def matches(text: str, prefix: str) -> bool:
    """Whether `text` would survive ranking for `prefix`. Exposed for callers that pre-filter."""
    return _match_strength(text, prefix) is not None


def kinds_present(suggestions: Sequence[Suggestion]) -> tuple[Kind, ...]:
    """The distinct kinds in `suggestions`, in first-appearance order. Useful for UIs."""
    seen: dict[Kind, None] = {}
    for suggestion in suggestions:
        seen.setdefault(suggestion.kind, None)
    return tuple(seen)
