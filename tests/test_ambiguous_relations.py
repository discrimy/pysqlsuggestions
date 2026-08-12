"""
Two relations with the same name, in different schemas, both in scope.

Postgres allows it — `SELECT 1 FROM public.invoices, billing.invoices` plans,
and the second is aliased internally as `invoices_1` — and then refuses every
bare reference to either: `table reference "invoices" is ambiguous`. So this is
the one position where the engine wrote SQL that does not run.
"""

from __future__ import annotations

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
