"""Context-aware, schema-aware SQL completion as an embeddable library."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete, derive_request, plan_insertion
from pysqlsuggestions.ports import (
    ByteCache,
    Cache,
    Catalog,
    ObjectCache,
    SupportsBulkColumns,
    SupportsColumnSearch,
    SupportsColumnValues,
    SupportsForeignKeys,
    SupportsKeywords,
    SupportsQueryableRelations,
    SupportsRelationSearch,
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

__version__ = '0.11.0'

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
    'SupportsBulkColumns',
    'SupportsColumnSearch',
    'SupportsColumnValues',
    'SupportsForeignKeys',
    'SupportsKeywords',
    'SupportsQueryableRelations',
    'SupportsRelationSearch',
    'Table',
    '__version__',
    'apply_suggestion',
    'complete',
    'derive_request',
    'plan_insertion',
]
