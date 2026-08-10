"""Context-aware, schema-aware SQL completion as an embeddable library."""

from __future__ import annotations

from pysqlsuggestions.engine.request import derive_request
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
    'Candidate',
    'Column',
    'Function',
    'Kind',
    'Projection',
    'Relation',
    'Request',
    'Scope',
    'Suggestion',
    'Table',
    '__version__',
    'derive_request',
]
