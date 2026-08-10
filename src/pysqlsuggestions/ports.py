"""
The protocols callers implement. The core defines them; callers bring connections.

`Catalog` stays at four methods so an adapter is cheap to write. Anything richer
is a separate capability protocol, detected at runtime — a fifteen-method Catalog
would force every adapter to stub out what its backend lacks.

Every capability must define what happens when it is absent; see `resolve.py`,
where the degradation lives so no adapter has to repeat it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pysqlsuggestions.types import Column, Function, Table


@runtime_checkable
class Catalog(Protocol):
    """
    Schema reads, unfiltered.

    Every method is prefix-independent, so an implementation may cache each
    result for a whole database rather than per keystroke. Matching and ranking
    against what the user typed happen in the engine.

    `schema=None` means "whatever is visible by default": the search path in
    Postgres, the current database in ClickHouse. Without it an unqualified
    `FROM users` — the most common query shape there is — cannot be expressed.
    """

    def schemas(self) -> Sequence[str]:
        """Schema, database or catalog names, depending on the dialect's namespace."""
        ...

    def tables(self, schema: str | None = None) -> Sequence[Table]:
        """Relations in `schema`, or those visible by default."""
        ...

    def columns(self, schema: str | None, table: str) -> Sequence[Column]:
        """Columns of one relation, in declaration order."""
        ...

    def functions(self, schema: str | None = None) -> Sequence[Function]:
        """Functions, aggregates and window functions."""
        ...


@runtime_checkable
class SupportsColumnSearch(Protocol):
    """
    Columns without a known relation — `SELECT <caret>` before any FROM clause.

    Absent: that position offers keywords and functions but no columns.
    """

    def all_columns(self) -> Sequence[Column] | None:
        """Every column of every visible relation, or None when there are too many."""
        ...

    def search_columns(self, prefix: str, limit: int) -> Sequence[Column]:
        """
        Columns matching `prefix` across every relation.

        The fallback for schemas too large to enumerate. Prefix-dependent, so
        unlike everything else on these protocols it does not cache.
        """
        ...


@runtime_checkable
class SupportsKeywords(Protocol):
    """
    Keywords from the server rather than the shipped set.

    Absent: `dialect.keywords` is used.
    """

    def keywords(self) -> Sequence[tuple[str, str]]:
        """(word, description) pairs."""
        ...


class Cache(Protocol):
    """
    Somewhere to keep catalog reads. A plain dict satisfies this.

    The key shape is a documented contract, because users supply their own cache:

        (role, dialect, schema, table)

    `role` is first and is not optional. Privilege-aware reads evaluate against
    the connection's role, so a cache keyed without it leaks one user's readable
    set into another user's session — a failure that is silent and looks like a
    database privilege bug rather than a caching bug. It is in the key from the
    start so that adding it later cannot silently change the meaning of an
    existing deployment's cache.
    """

    def get(self, key: Any, default: Any = None) -> Any:
        """The cached value, or `default`."""
        ...

    def __setitem__(self, key: Any, value: Any) -> None:
        """Store a value."""
        ...
