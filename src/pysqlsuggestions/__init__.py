"""Context-aware, schema-aware SQL completion as an embeddable library."""

from __future__ import annotations

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.ports import (
    Cache,
    Catalog,
    SupportsColumnSearch,
    SupportsKeywords,
)
from pysqlsuggestions.types import (
    Candidate,
    Column,
    Function,
    Kind,
    Projection,
    Relation,
    Request,
    Scope,
    Suggestion,
    Table,
)

__version__ = '0.1.0.dev0'

__all__ = [
    'Cache',
    'Candidate',
    'Catalog',
    'Column',
    'Function',
    'Kind',
    'Projection',
    'Relation',
    'Request',
    'Scope',
    'Suggestion',
    'SupportsColumnSearch',
    'SupportsKeywords',
    'Table',
    '__version__',
    'complete',
    'derive_request',
]
