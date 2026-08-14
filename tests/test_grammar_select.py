"""
The SELECT conformance suite: every caret the official synopsis names.

Runs against `complete`, not `derive_request`, because the question here is
which *words* a position offers — `tests/test_golden_requests.py` already pins
the Request shape. Same caret convention, a different assertion.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import Function
from tests.corpus.cases import CARET, split_caret
from tests.grammar.cases import CASES, SYNOPSIS, UNCITED, GrammarCase

SNAPSHOT = {
    ('public', 'users'): [('id', 'bigint'), ('email', 'text')],
    ('public', 'orders'): [('id', 'bigint'), ('user_id', 'bigint'), ('total', 'numeric')],
}
"""
Two relations, five columns, one plausible join.

The shape `tests/test_statement_forms.py` already uses. Small enough that a
`refuses` list can name every column by hand, which is what makes an exclusion
assertion trustworthy.
"""


FUNCTIONS = (Function(schema='public', name='now', args='', result='timestamptz'),)
"""
One function, because two positions need the catalog to have any.

`ROWS FROM(⌶` takes a function call and `SELECT ⌶` offers functions beside
columns, and a fixture with none cannot tell an empty answer from a wrong one.
"""


def catalog() -> MemoryCatalog:
    """A fresh catalog per case, so no case can be affected by another's caching."""
    return MemoryCatalog(SNAPSHOT, functions=FUNCTIONS)


def offered(sql: str, caret: int, dialect: Dialect = POSTGRES) -> list[str]:
    """The suggestion texts at `caret` in `sql`, for `dialect`."""
    return [suggestion.text for suggestion in complete(sql, caret, dialect, catalog())]


DIALECTS = {'ansi': ANSI, 'postgres': POSTGRES, 'clickhouse': CLICKHOUSE, 'trino': TRINO}
"""
Every dialect a case may name, by the name it names it with.

The same mapping `tests/test_golden_requests.py` keeps, and for the same reason:
a misspelt dialect looked up here raises, where a bare string compared against
the shipped dialects would silently match nothing and skip the case.
"""


def _collapse(text: str) -> str:
    """Runs of whitespace to one space, ends trimmed. The synopsis is indented for print."""
    return ' '.join(text.split())


def _pairs() -> list[tuple[GrammarCase, str]]:
    """Every (case, dialect) the suite runs, one per dialect a case names."""
    return [(case, name) for case in CASES for name in case.dialects]


def _params() -> list[object]:
    """Each pair, marked xfail(strict=True) while the case is still pending."""
    return [
        pytest.param(case, name, marks=pytest.mark.xfail(strict=True, reason=case.note or 'pending'))
        if case.pending
        else pytest.param(case, name)
        for case, name in _pairs()
    ]


@pytest.mark.parametrize(
    ('case', 'dialect'),
    _params(),
    ids=[f'{name}: {case.cite[:32]} :: {case.sql}' for case, name in _pairs()],
)
def test_grammar_position(case: GrammarCase, dialect: str) -> None:
    """Every word the synopsis puts at this caret is offered, and none it forbids."""
    sql, caret = split_caret(case.sql)
    found = offered(sql, caret, DIALECTS[dialect])

    missing = [word for word in case.offers if word not in found]
    assert not missing, f'not offered: {missing}; got {found}'

    wrong = [word for word in case.refuses if word in found]
    assert not wrong, f'wrongly offered: {wrong}; got {found}'


# --- the data itself ------------------------------------------------------


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_every_case_marks_exactly_one_caret(case: GrammarCase) -> None:
    """Two markers or none would produce a nonsense offset."""
    assert case.sql.count(CARET) == 1


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_every_case_asserts_something(case: GrammarCase) -> None:
    """A case with neither an offer nor a refusal passes vacuously and measures nothing."""
    assert case.offers or case.refuses


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_every_cite_is_a_line_of_the_synopsis(case: GrammarCase) -> None:
    """A citation invented at the keyboard would make the coverage test meaningless."""
    assert _collapse(case.cite) in {_collapse(line) for line in SYNOPSIS.splitlines()}


def _grammar_lines() -> list[str]:
    """
    The productions in `select.txt`, without the provenance header or the prose.

    Lines ending in a colon are the document's own connective tissue — "where
    from_item can be one of:" — and name no position.
    """
    lines = []
    for raw in SYNOPSIS.splitlines():
        line = _collapse(raw)
        if not line or line.startswith('#') or line.endswith(':'):
            continue
        lines.append(line)
    return lines


def test_every_synopsis_line_is_cited() -> None:
    """
    The suite tracks a document, and this is what keeps that claim true.

    Re-sync `select.txt` with a later server and any production nobody wrote a
    case for is named here, rather than silently going unmeasured.
    """
    cited = {_collapse(case.cite) for case in CASES}
    uncovered = [line for line in _grammar_lines() if line not in cited and line not in UNCITED]
    assert not uncovered, f'synopsis lines with no case: {uncovered}'


def test_uncited_lines_are_really_in_the_synopsis() -> None:
    """An UNCITED entry that matches nothing is an exemption for a line that no longer exists."""
    lines = set(_grammar_lines())
    assert UNCITED.issubset(lines)


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_every_named_dialect_exists(case: GrammarCase) -> None:
    """A misspelt name would skip the dialect rather than fail it, which is the worse failure."""
    assert set(case.dialects) <= set(DIALECTS)


@pytest.mark.parametrize('case', CASES, ids=[c.sql for c in CASES])
def test_only_a_passing_case_names_more_than_one_dialect(case: GrammarCase) -> None:
    """
    A pending case marked shared would xfail once per dialect for one reason.

    The burn-down would then count a single gap two or three times, and the
    reason printed beside each would be the same sentence. A case earns its
    other dialects by passing on Postgres first.
    """
    assert not case.pending or len(case.dialects) == 1
