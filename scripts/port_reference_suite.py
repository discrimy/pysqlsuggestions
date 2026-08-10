"""
Translate report_service's autocomplete suite onto the pysqlsuggestions API.

    python scripts/port_reference_suite.py && uv run ruff format .

Kept in the repo so the corpus can be re-synced when that suite grows, rather
than being a one-off translation nobody can reproduce. It rewrites the call shape
and marks the known gaps xfail; everything else is theirs verbatim.

Not run in CI: it reads a path outside this repository.
"""

import ast
import re
from pathlib import Path

SRC = Path('/home/user/Projects/report_service/reports/tests/test_autocomplete.py')
OUT = Path('/home/user/Projects/pysqlsuggestions/tests/reference/test_ported_autocomplete.py')

lines = SRC.read_text(encoding='utf-8').splitlines()

# Everything already provided by the new header, or replaced by it.
PROVIDED = {'CATALOG', 'USER_COLUMNS'}
DROP_DEFS = {'cur', 'texts', 'kinds', 'at'}


tree = ast.parse('\n'.join(lines))


def _source(node: ast.stmt) -> list[str]:
    """The exact source lines of a top-level statement, decorators included."""
    start = min([node.lineno, *[d.lineno for d in getattr(node, 'decorator_list', [])]]) - 1
    return lines[start : node.end_lineno]


blocks: list[tuple[str, list[str]]] = []
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        continue
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        continue  # the module docstring
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.name in DROP_DEFS:
            continue
        blocks.append((node.name if node.name.startswith('test_') else '', _source(node)))
        continue
    if isinstance(node, ast.ClassDef):
        if node.name != 'FakeCatalog':
            blocks.append(('', _source(node)))
        continue
    if isinstance(node, ast.Assign):
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if not names & PROVIDED:
            blocks.append(('', _source(node)))
        continue

# Exercise APIs this library deliberately does not have, or the old module's own
# dataclass shapes. Listed rather than silently dropped.
SKIP = {
    'test_catalog_types_carry_their_fields': "asserts the old module's dataclass field names",
    'test_catalog_protocol_is_structural': "asserts the old module's Catalog protocol",
}

# Known gaps, grouped by root cause. Each is a real behaviour report_service
# users have today and this library does not yet — a burn-down, not a wontfix.
GAPS = {
    # Not a gap: a deliberate difference. With two relations in view a bare name
    # may not parse — `WHERE id` against two tables that both have one is an
    # ambiguity error — and deduplicating bare names hides the second relation's
    # column behind the first. These assert the bare names their engine returned.
    'columns are qualified when more than one relation is in scope': [
        'join_brings_both_relations',
        'cte_joined_with_a_real_table',
        'cte_and_derived_table_both_in_scope',
        'report_query_unqualified_scope',
        'correlated_outer_relation_visible_inside_a_subquery',
        'nested_subquery_sees_every_enclosing_level',
    ],
    # Not a gap: a deliberate difference. Their engine always lists keywords in
    # canonical uppercase and adjusts the case when inserting; this one decides
    # the case in the suggestion, so the list shows what will actually be typed.
    # Both halves of the assertion hold except that ours reads `where`, not `WHERE`.
    'keyword case is decided in the suggestion here, not at insertion': [
        'keywords_stay_prefix_only',
    ],
    # Not a gap: a deliberate difference, and one asked for directly — "do not
    # suggest columns of incompatible types". Their engine offers every column of
    # the qualified relation; this one drops the ones that cannot face the value
    # on the left. `u.id = o.<caret>` withholds `o.created`, and `SET total =
    # EXCLUDED.<caret>` withholds it too, both because Postgres rejects them.
    'a comparison narrows by type here, and their engine did not narrow at all': [
        'outer_qualifier_inside_an_exists_subquery',
        'excluded_offers_the_target_columns',
    ],
}
REASON = {f'test_{name}': reason for reason, names in GAPS.items() for name in names}

out: list[str] = []
skipped: list[str] = []
for name, body in blocks:
    if name and name in SKIP:
        skipped.append(name)
        continue
    src = '\n'.join(body)
    src = re.sub(r'\bautocomplete\(', 'suggestions(', src)
    src = re.sub(r'\bFakeCatalog\(', 'fake_catalog(', src)
    src = re.sub(r'^def (test_\w+)\(cur\):', r'def \1(cur: MemoryCatalog) -> None:', src, flags=re.M)
    src = re.sub(r'^def (test_\w+)\(\):', r'def \1() -> None:', src, flags=re.M)
    # `detail` is optional here; their Suggestion.detail was always a string.
    src = src.replace('{s.text: s.detail for s', "{s.text: s.detail or '' for s")
    # Their kind was a bare string; ours is an enum.
    src = src.replace('(s.text, s.kind)', '(s.text, s.kind.value)')
    if name in REASON:
        src = f"@pytest.mark.xfail(strict=True, reason='{REASON[name]}')\n{src}"
    out.append(src)

print(f'ported {len(out)}, skipped {len(skipped)}: {sorted(skipped)}')

HEADER = '''"""
report_service's autocomplete suite, translated onto this library's API.

Ported wholesale rather than rewritten. It encodes edge cases nobody would think
to invent — self-referencing CTEs, dollar-quoted bodies mentioning FROM, union
branches, Cyrillic identifiers, report macros in a value position — and anything
it pinned down is behaviour report_service users already have. A failure here is
a regression for them, not a difference of opinion.

The fixture, the SQL strings and the assertions are theirs verbatim. Only the
harness is reimplemented: `texts(cur, sql)` now runs `complete()`, and `cur` is a
MemoryCatalog rather than their FakeCatalog.

Two cases are not ported, because they assert on the old module's own dataclass
field names and Catalog protocol rather than on any behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from pysqlsuggestions.api import apply_suggestion, complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Suggestion

CATALOG = {
    ('public', 'auth_user'): [
        ('id', 'integer'),
        ('username', 'character varying(150)'),
        ('email', 'character varying(254)'),
        ('is_staff', 'boolean'),
        ('date_joined', 'timestamp with time zone'),
    ],
    ('public', 'auth_group'): [
        ('id', 'integer'),
        ('name', 'character varying(150)'),
    ],
    ('public', 'orders'): [
        ('id', 'integer'),
        ('user_id', 'integer'),
        ('total', 'numeric'),
        ('created', 'date'),
    ],
    # prefix-matches "use", where auth_user only contains it — lets ranking be tested
    ('public', 'users_log'): [
        ('id', 'integer'),
        ('msg', 'text'),
    ],
    ('billing', 'invoices'): [
        ('id', 'integer'),
        ('order_id', 'integer'),
        ('amount', 'numeric'),
    ],
}

USER_COLUMNS = ['id', 'username', 'email', 'is_staff', 'date_joined']

DEFAULT_LIMIT = 200
"""Their harness was unbounded; a high cap keeps `sorted(texts(...)) == [...]` honest."""


def fake_catalog(catalog: Any = None, oversized: bool = False) -> MemoryCatalog:
    """Stands in for their FakeCatalog."""
    return MemoryCatalog(catalog or CATALOG, oversized=oversized)


@pytest.fixture
def cur() -> MemoryCatalog:
    """The fixture catalog, one per test so `calls` is meaningful."""
    return fake_catalog()


def suggestions(
    cursor: MemoryCatalog,
    sql: str,
    pos: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Suggestion]:
    """Complete at `pos`, defaulting to end of input as their harness did."""
    return complete(sql, len(sql) if pos is None else pos, POSTGRES, cursor, limit=limit)


def texts(cursor: MemoryCatalog, sql: str, **kwargs: Any) -> list[str]:
    """Suggestion texts."""
    return [s.text for s in suggestions(cursor, sql, **kwargs)]


def kinds(cursor: MemoryCatalog, sql: str, **kwargs: Any) -> dict[str, str]:
    """Text -> kind."""
    return {s.text: s.kind.value for s in suggestions(cursor, sql, **kwargs)}


def at(cursor: MemoryCatalog, marked: str, **kwargs: Any) -> list[str]:
    """Suggestion texts at the ‸ marker."""
    return texts(cursor, marked.replace('‸', ''), pos=marked.index('‸'), **kwargs)


class _Context:
    """Their `analyze()` result shape, over this library's Request."""

    def __init__(self, sql: str, pos: int | None = None) -> None:
        self._request = derive_request(sql, len(sql) if pos is None else pos, POSTGRES)

    @property
    def prefix(self) -> str:
        """What is already typed."""
        return self._request.prefix

    @property
    def replace_from(self) -> int:
        """Where the replacement starts — the first half of `replace_span`."""
        return self._request.replace_span[0]

    @property
    def clause(self) -> str | None:
        """The governing clause keyword."""
        return self._request.clause

    @property
    def relations(self) -> list[_Ref]:
        """Relations in scope, in their TableRef shape."""
        scope = self._request.scope
        return [_Ref(r.path[-1] if r.path else '') for r in (scope.visible() if scope else ())]


class _Ref:
    """Their TableRef, reduced to the one field the ported tests read."""

    def __init__(self, name: str) -> None:
        self.name = name


def analyze(sql: str, pos: int | None = None) -> _Context:
    """Their analyze(), adapted."""
    return _Context(sql, pos)
'''

OUT.parent.mkdir(parents=True, exist_ok=True)
(OUT.parent / '__init__.py').write_text(
    '"""report_service\'s autocomplete suite, translated."""\n',
    encoding='utf-8',
)
OUT.write_text(HEADER + '\n\n' + '\n\n\n'.join(out) + '\n', encoding='utf-8')
print(f'wrote {OUT}')
