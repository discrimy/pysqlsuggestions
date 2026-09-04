"""Context-aware, schema-aware SQL completion as an embeddable library."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete, derive_request, plan_insertion
from pysqlsuggestions.ports import (
    ByteCache,
    Cache,
    Catalog,
    ObjectCache,
    SupportsColumnSearch,
    SupportsColumnValues,
    SupportsForeignKeys,
    SupportsKeywords,
)
from pysqlsuggestions.types import (
    Availability,
    Candidate,
    Column,
    ColumnValue,
    ForeignKey,
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

__version__ = '0.9.0'

__all__ = [
    'Availability',
    'ByteCache',
    'Cache',
    'Candidate',
    'Catalog',
    'Column',
    'ColumnValue',
    'ForeignKey',
    'Function',
    'Insertion',
    'Kind',
    'ObjectCache',
    'Projection',
    'Relation',
    'Request',
    'Scope',
    'Suggestion',
    'SupportsColumnSearch',
    'SupportsColumnValues',
    'SupportsForeignKeys',
    'SupportsKeywords',
    'Table',
    '__version__',
    'apply_suggestion',
    'complete',
    'derive_request',
    'plan_insertion',
]
