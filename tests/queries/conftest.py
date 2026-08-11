"""The fixture every module in this package shares."""

from __future__ import annotations

import pytest

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import fake_catalog


@pytest.fixture
def cur() -> MemoryCatalog:
    """The fixture catalog, one per test so `calls` is meaningful."""
    return fake_catalog()
