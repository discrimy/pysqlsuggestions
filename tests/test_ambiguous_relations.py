"""
Two relations with the same name, in different schemas, both in scope.

Postgres allows it — `SELECT 1 FROM public.invoices, billing.invoices` plans,
and the second is aliased internally as `invoices_1` — and then refuses every
bare reference to either: `table reference "invoices" is ambiguous`. So this is
the one position where the engine wrote SQL that does not run.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.types import Candidate, Kind, Request


def test_a_qualifier_of_several_segments_renders_as_a_path() -> None:
    """
    Each segment is quoted on its own. A dotted string in a single-segment field
    would come back as `"public.invoices"` — one quoted name, and not a path,
    which is why the field's type had to change rather than its contents.
    """
    request = Request(kinds=(Kind.COLUMN,), prefix='', replace_span=(0, 0))
    candidate = Candidate(text='amount', kind=Kind.COLUMN, qualifier=('public', 'invoices'))
    [found] = rank([candidate], request, POSTGRES)
    assert found.text == 'public.invoices.amount'


def test_a_segment_that_needs_quoting_gets_it_alone() -> None:
    """A mixed-case relation in a lowercase-folding dialect: only that segment is quoted."""
    request = Request(kinds=(Kind.COLUMN,), prefix='', replace_span=(0, 0))
    candidate = Candidate(text='amount', kind=Kind.COLUMN, qualifier=('billing', 'MonthlyTotals'))
    [found] = rank([candidate], request, POSTGRES)
    assert found.text == 'billing."MonthlyTotals".amount'


SNAPSHOT = {
    ('public', 'invoices'): [('amount', 'numeric'), ('id', 'bigint')],
    ('billing', 'invoices'): [('amount', 'numeric'), ('period', 'date')],
    ('public', 'auth_user'): [('email', 'varchar')],
}


def catalog() -> MemoryCatalog:
    """Two same-named relations in different schemas, and one that is unique."""
    return MemoryCatalog(SNAPSHOT, search_path=('public',))


def offered(sql: str, caret: int | None = None) -> list[str]:
    """Suggestion texts at `caret`, or at the end of `sql`."""
    at = len(sql) if caret is None else caret
    return [s.text for s in complete(sql, at, POSTGRES, catalog())]


RELATIONS = 'public.invoices, billing.invoices'
BOTH = f'FROM {RELATIONS}'


def test_two_same_named_relations_in_scope_get_their_whole_path() -> None:
    """
    Server-verified: `SELECT invoices.amount FROM public.invoices, billing.invoices`
    is refused with `table reference "invoices" is ambiguous`, and both
    `public.invoices.amount` and `billing.invoices.amount` plan.
    """
    found = offered(f'SELECT amou {BOTH}', caret=11)
    assert 'public.invoices.amount' in found
    assert 'billing.invoices.amount' in found
    assert 'invoices.amount' not in found


def test_a_relation_whose_label_is_unique_keeps_its_label() -> None:
    """The rule is per label, not per statement: one collision must not lengthen everything."""
    found = offered(f'SELECT ema FROM public.auth_user, {RELATIONS}', caret=10)
    assert 'auth_user.email' in found


def test_aliases_are_not_a_collision() -> None:
    """
    `FROM public.invoices a, billing.invoices b` answers to `a` and `b`, which
    the server resolves without help. Keyed on the label rather than the
    relation name, and this is the test that says so.
    """
    found = offered('SELECT amou FROM public.invoices a, billing.invoices b', caret=11)
    assert 'a.amount' in found
    assert 'b.amount' in found
    assert not [text for text in found if text.startswith('public.')]


def test_one_relation_is_untouched() -> None:
    """The constraint the whole design is shaped around: no collision, no change."""
    assert offered('SELECT amou FROM billing.invoices', caret=11) == ['invoices.amount']


def test_a_star_over_two_same_named_relations_names_both() -> None:
    """
    Today this expands to `invoices.amount, invoices.id, invoices.amount,
    invoices.period` — every reference ambiguous, and `amount` written twice
    because the two relations render identically.
    """
    sql = f'SELECT * {BOTH}'
    [found] = [s for s in complete(sql, 8, POSTGRES, catalog()) if s.kind is Kind.EXPANSION]
    assert found.text == (
        'public.invoices.amount, public.invoices.id, billing.invoices.amount, billing.invoices.period'
    )


def test_a_star_over_one_relation_is_untouched() -> None:
    """No collision, no change — a one-relation star still expands bare."""
    sql = 'SELECT * FROM billing.invoices'
    [found] = [s for s in complete(sql, 8, POSTGRES, catalog()) if s.kind is Kind.EXPANSION]
    assert found.text == 'amount, period'
