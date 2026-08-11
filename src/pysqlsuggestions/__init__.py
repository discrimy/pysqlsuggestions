"""Context-aware, schema-aware SQL completion as an embeddable library."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete, derive_request, plan_insertion
from pysqlsuggestions.ports import (
    Cache,
    Catalog,
    SupportsColumnSearch,
    SupportsColumnValues,
    SupportsKeywords,
)
from pysqlsuggestions.types import (
    Candidate,
    Column,
    ColumnValue,
    Function,
    Insertion,
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
    'ColumnValue',
    'Function',
    'Insertion',
    'Kind',
    'Projection',
    'Relation',
    'Request',
    'Scope',
    'Suggestion',
    'SupportsColumnSearch',
    'SupportsColumnValues',
    'SupportsKeywords',
    'Table',
    '__version__',
    'apply_suggestion',
    'complete',
    'derive_request',
    'plan_insertion',
]
