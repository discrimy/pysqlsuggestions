"""Reports how much of each acceptance suite is still pending, on every run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.corpus.cases import CASES
from tests.grammar.cases import CASES as GRAMMAR_CASES

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter

PORTED = 'tests/queries/'
"""
Where the ported report_service suite lives.

Was `tests/reference/` here, which is not a path in this repository — so the
line below counted nothing and, since it also counted *every* xfail in the run,
printed only when some unrelated suite had one. Both halves are scoped to this
prefix now, which is what makes the number mean what it says.
"""


def _in_ported_suite(reports: list[object]) -> int:
    """How many of `reports` belong to the ported suite."""
    return sum(1 for report in reports if PORTED in getattr(report, 'nodeid', ''))


def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    """Print all three burn-downs, so progress is a number rather than a feeling."""
    pending = sum(1 for case in CASES if case.pending)
    total = len(CASES)
    terminalreporter.write_line(f'corpus burn-down: {total - pending}/{total} golden requests passing')

    xfailed = _in_ported_suite(terminalreporter.stats.get('xfailed', []))
    ported = _in_ported_suite(terminalreporter.stats.get('passed', []))
    if ported or xfailed:
        terminalreporter.write_line(
            f'report_service suite: {ported}/{ported + xfailed} passing, {xfailed} known gaps',
        )

    answered = sum(1 for case in GRAMMAR_CASES if not case.pending)
    refused = sum(1 for case in GRAMMAR_CASES if case.pending and case.refused)
    gaps = len(GRAMMAR_CASES) - answered
    terminalreporter.write_line(
        f'grammar burn-down: {answered}/{len(GRAMMAR_CASES)} SELECT positions answered, '
        f'{refused} of the {gaps} gaps refused',
    )

    # A count rather than a ratio. Every case naming a dialect passes on it, so
    # a ratio would read n/n forever; the number worth printing is how much of
    # the shared baseline is asserted there at all, and a case that stopped
    # holding fails the run rather than moving this line.
    shared = sorted({name for case in GRAMMAR_CASES for name in case.dialects} - {'postgres'})
    if shared:
        counts = ', '.join(f'{sum(1 for c in GRAMMAR_CASES if name in c.dialects)} on {name}' for name in shared)
        terminalreporter.write_line(f'  also holding: {counts}')
