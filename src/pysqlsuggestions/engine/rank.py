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
from functools import cache

from pysqlsuggestions.dialects.base import Dialect, Syntax
from pysqlsuggestions.types import Candidate, Kind, Request, Suggestion

_EXACT_PREFIX = 100.0
_FOLDED_PREFIX = 70.0
_WORD_PREFIX = 55.0
_WORD_BOUNDARY = 40.0
_SUBSTRING = 25.0
_SUBSTRING_POSITION_WEIGHT = 0.5

_KIND_STEP = 5.0
_LOCAL_BONUS = 30.0
"""
Candidates derived from the query itself outrank anything fetched: the user wrote them.

Large enough to clear a whole kind step, so adding a kind to a clause's list
cannot silently demote a generated alias below the tables it was derived from.
"""

_MAX_POSITION_PENALTY = 50
_POSITION_WEIGHT = 0.1

_PLACEHOLDER = re.compile(r'\$(\d+)')

# The unquoted-identifier grammar, which is not the same in all four dialects:
# Postgres reads `отчёты` and `a$b` back as written, Trino rejects both.
_ASCII_LETTERS = 'A-Za-z'
_ASCII = 'A-Za-z0-9_'
_NON_ASCII = '\u0080-\uffff'


def rank(
    candidates: Iterable[Candidate],
    request: Request,
    dialect: Dialect,
    limit: int | None = None,
) -> list[Suggestion]:
    """Score, sort and render `candidates` for `request`."""
    kind_rank = {kind: index for index, kind in enumerate(request.kinds)}
    scored: list[tuple[float, int, str, Suggestion]] = []

    for candidate in candidates:
        # A snippet matches on its label — nobody types the expanded text.
        strength = _match_strength(candidate.label or candidate.text, request.prefix, candidate.kind)
        if strength is None:
            continue
        score = strength + _kind_bonus(candidate.kind, kind_rank, len(request.kinds))
        score -= min(candidate.position, _MAX_POSITION_PENALTY) * _POSITION_WEIGHT
        if candidate.origin == 'local':
            score += _LOCAL_BONUS
        text, stops = _render(candidate, request, dialect)
        scored.append(
            (
                -score,
                len(candidate.text),
                text.lower(),
                Suggestion(
                    text=text,
                    kind=candidate.kind,
                    replace_span=request.replace_span,
                    score=round(score, 3),
                    detail=candidate.detail,
                    takes_arguments=candidate.takes_arguments,
                    stops=stops,
                    label=candidate.label,
                ),
            ),
        )

    # Among equal-strength matches, the shorter name is the closer one: the same
    # prefix covers more of it. `no` should reach `now` before `normalize`, and
    # alphabetical order alone would not.
    scored.sort(key=lambda row: (row[0], row[1], row[2]))

    # Two relations in scope often share a column name. Offering `id` twice is
    # noise: the text inserted would be identical either way, so the
    # highest-scoring occurrence is the one worth keeping. Sorting first means
    # that is simply the first one seen.
    seen: set[tuple[Kind, str]] = set()
    ordered: list[Suggestion] = []
    for *_, suggestion in scored:
        key = (suggestion.kind, suggestion.text)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(suggestion)

    return ordered[:limit] if limit is not None else ordered


def expand_snippet(snippet: str) -> tuple[str, tuple[int, ...]]:
    """
    Strip `$1`-style placeholders, returning the plain text and where they were.

    Offsets come back in visiting order — `$1`, `$2`, then `$0` last, as LSP
    defines it — so a front end can cycle them without parsing anything.
    """
    parts: list[str] = []
    marks: list[tuple[int, int]] = []
    written = 0
    cursor = 0
    for match in _PLACEHOLDER.finditer(snippet):
        chunk = snippet[cursor : match.start()]
        parts.append(chunk)
        written += len(chunk)
        marks.append((int(match.group(1)), written))
        cursor = match.end()
    parts.append(snippet[cursor:])
    ordered = sorted(marks, key=lambda mark: (mark[0] == 0, mark[0]))
    return ''.join(parts), tuple(offset for _, offset in ordered)


def _match_strength(text: str, prefix: str, kind: Kind = Kind.COLUMN) -> float | None:
    """
    How well `text` matches `prefix`, or None when it does not match at all.

    Five tiers, weakest last:

    1. exact-case prefix
    2. case-insensitive prefix
    3. prefix of any word component — `data` finds `reports_database`, because
       snake_case buries the meaningful word and nobody types `reports_`
    4. initials of the words — `oi` finds `order_items`
    5. substring, scored down by how late the match starts

    Tier 5 is what the helper this supersedes did for every identifier, so its
    users already rely on `mail` finding `email`. It is contiguous and
    position-ranked, which makes it considerably tighter than subsequence fuzzy
    matching — whose failure mode is a three-character prefix matching sixty
    unrelated things, and a contiguous match ranked below four stronger tiers
    does not do that.

    Keywords stay prefix-only. There are a few hundred of them and they are not
    what the user is hunting for; `her` should not offer WHERE.
    """
    if not prefix:
        return _EXACT_PREFIX
    if text.startswith(prefix):
        return _EXACT_PREFIX
    folded = prefix.lower()
    lowered = text.lower()
    if lowered.startswith(folded):
        return _FOLDED_PREFIX
    if kind is Kind.KEYWORD:
        return None
    words = _words(text)
    if any(word.startswith(folded) for word in words):
        return _WORD_PREFIX
    if ''.join(word[0] for word in words).startswith(folded):
        return _WORD_BOUNDARY
    found = lowered.find(folded)
    if found >= 0:
        return _SUBSTRING - found * _SUBSTRING_POSITION_WEIGHT
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
    """
    Kinds earlier in `request.kinds` are more relevant here.

    A CTE occupies a relation position, so it scores as one: `kinds` names TABLE
    where a relation belongs, and a CTE is the statement's own relation.
    """
    index = kind_rank.get(kind)
    if index is None and kind is Kind.CTE:
        index = kind_rank.get(Kind.TABLE)
    return 0.0 if index is None else (total - index) * _KIND_STEP


def _render(candidate: Candidate, request: Request, dialect: Dialect) -> tuple[str, tuple[int, ...]]:
    """The text to insert and where the caret should stop in it."""
    if candidate.snippet is not None:
        body = candidate.snippet if not _typing_lowercase(request) else candidate.snippet.lower()
        return expand_snippet(body)
    if candidate.literal or candidate.kind is Kind.OPERATOR:
        return candidate.text, ()
    if candidate.kind is Kind.KEYWORD:
        return (candidate.text.lower() if _typing_lowercase(request) else candidate.text.upper()), ()
    text = quote_if_needed(candidate.text, dialect)
    if candidate.qualifier:
        return f'{quote_if_needed(candidate.qualifier, dialect)}.{text}', ()
    return text, ()


def _typing_lowercase(request: Request) -> bool:
    """
    Follow the case the author has been writing keywords in.

    `Request.prefix` cannot answer this: a case-insensitive dialect folds it, so
    `WH` and `wh` are indistinguishable by the time it gets here. `keyword_case`
    is derived from the raw source slice for exactly that reason.

    Defaults to upper, the convention most SQL style guides assume.
    """
    return request.keyword_case == 'lower'


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
    case = dialect.syntax.unquoted_case
    plain = _plain_identifier(dialect.syntax, first_case='lower' if case == 'lower' else 'any')
    if case == 'preserve':
        return plain.match(name) is None
    if case == 'upper':
        return name != name.upper() or plain.match(name) is None
    return name != name.lower() or plain.match(name) is None


@cache
def _plain_identifier(syntax: Syntax, first_case: str) -> re.Pattern[str]:
    """
    The pattern a name must match to survive unquoted in this dialect.

    Built from the `Syntax` record rather than hardcoded, because the legal
    character set is a real difference between backends: Postgres accepts a
    Cyrillic name and a `$` bare, Trino accepts neither and ClickHouse rejects
    the first. Quoting one that did not need it still runs; leaving one bare
    that did need it does not.
    """
    extra = re.escape(syntax.unquoted_extra)
    tail_non_ascii = _NON_ASCII if syntax.unquoted_non_ascii else ''
    head = 'a-z' if first_case == 'lower' else _ASCII_LETTERS
    return re.compile(f'[{head}_{tail_non_ascii}][{_ASCII}{extra}{tail_non_ascii}]*\\Z')


def matches(text: str, prefix: str, kind: Kind = Kind.COLUMN) -> bool:
    """Whether `text` would survive ranking for `prefix`. Exposed for callers that pre-filter."""
    return _match_strength(text, prefix, kind) is not None


def kinds_present(suggestions: Sequence[Suggestion]) -> tuple[Kind, ...]:
    """The distinct kinds in `suggestions`, in first-appearance order. Useful for UIs."""
    seen: dict[Kind, None] = {}
    for suggestion in suggestions:
        seen.setdefault(suggestion.kind, None)
    return tuple(seen)
