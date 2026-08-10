"""Reports how much of the acceptance corpus is still pending, on every run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.corpus.cases import CASES

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter


def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    """Print the corpus burn-down so progress is a number rather than a feeling."""
    pending = sum(1 for case in CASES if case.pending)
    total = len(CASES)
    terminalreporter.write_line(f'corpus burn-down: {total - pending}/{total} golden requests passing')
