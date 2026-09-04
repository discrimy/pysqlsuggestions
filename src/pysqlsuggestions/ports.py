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
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pysqlsuggestions.types import Column, ColumnValue, ForeignKey, Function, Table


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

    def schemas(self, catalog: str | None = None) -> Sequence[str]:
        """
        Namespace names one level down.

        `catalog` is the level above, and is only meaningful where the dialect
        has three levels: `SELECT * FROM prod.<caret>` in Trino must list the
        schemas of the `prod` catalog, not every schema everywhere. Backends with
        a two-level namespace ignore it.
        """
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
        The `limit` columns matching `prefix` most closely, across every relation.

        The fallback for schemas too large to enumerate. Prefix-dependent, so
        unlike everything else on these protocols it does not cache.

        Most closely, not merely the first found: this is the one place where a
        truncation happens before ranking sees the rows, so an adapter that
        returns them in storage order can hide the exact match behind three
        hundred near-misses. Order at least by whether the name starts with
        `prefix`, then by length. `pysqlsuggestions.engine.rank.matches` is
        exposed for adapters that want the library's own idea of a match.
        """
        ...


@runtime_checkable
class SupportsRelationSearch(Protocol):
    """
    Relations by name across every visible namespace — `FROM ord<caret>` where
    `orders` lives outside the search path.

    Absent: that position offers the default namespace and nothing else, which
    is what it offered before this existed.
    """

    def search_relations(self, prefix: str, limit: int) -> Sequence[Table]:
        """
        The `limit` relations matching `prefix` most closely, in any namespace.

        Empty for an empty prefix. `FROM <caret>` is not a request for every
        relation in the database, and answering it as one is the query a
        completion engine must not make.

        Prefix-dependent, so unlike `Catalog.tables` it does not cache — which
        is why this is a capability and not a fifth `Catalog` method.

        Most closely, not merely the first found: the truncation happens before
        ranking sees the rows, so an adapter returning storage order can hide an
        exact match behind two hundred near-misses. `Table.schema` travels with
        each row, because a relation the search path does not cover has to be
        written qualified.
        """
        ...


@runtime_checkable
class SupportsColumnValues(Protocol):
    """
    The values a column frequently holds, for the right side of a comparison.

    Absent: that position offers columns and functions but no literals.

    Meant to be answered from whatever statistics the backend already keeps for
    its planner, never by reading the table — `SELECT DISTINCT` on a large
    column is a scan, and a completion engine may not start one. Postgres has
    `pg_stats.most_common_vals`, which is also filtered by what the connected
    role may read; a backend without an equivalent should not implement this.
    """

    def common_values(self, schema: str | None, table: str, column: str, limit: int) -> Sequence[ColumnValue]:
        """
        Up to `limit` frequent values of one column, most frequent first.

        Rendering is the engine's problem: it knows the column's type and so
        whether the literal needs quotes. Each value may carry the share of rows
        it accounts for, which is what lets a list of them be read at a glance.
        """
        ...


@runtime_checkable
class SupportsForeignKeys(Protocol):
    """
    Declared relationships between relations, for join completion.

    Absent: `JOIN <caret>` offers relation names and `ON <caret>` offers columns,
    which is what both offered before this existed.

    Only *declared* constraints belong here. A backend that keeps none — ClickHouse
    and Trino keep none — should not implement this rather than infer edges from
    column names, because a wrong join condition is valid SQL that silently returns
    wrong rows.
    """

    def foreign_keys(self, schema: str | None = None) -> Sequence[ForeignKey]:
        """
        Every constraint whose referencing side lives in `schema`, or in the default namespace.

        Schema-scoped rather than per-relation because a join is undirected: the
        proposal at `FROM auth_user u JOIN <caret>` needs the edges that point *at*
        `auth_user`, and no per-relation call could find them without walking every
        relation in the database.
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


@runtime_checkable
class ObjectCache(Protocol):
    """
    Somewhere to keep catalog reads as Python objects. In practice, a dict.

    The key is an opaque string built by `pysqlsuggestions.caches.cache_key`,
    which is the only supported way to make one — the string's shape is not a
    format, and changes whenever a cached type does.

    `None` means miss. No value the library caches is ever `None`, which is what
    makes one channel enough for two answers; the cost of that rule is recorded
    in `docs/gaps.md`, where caching `all_columns` sits blocked by it.

    `ttl` is integer seconds, and `None` means the implementation's own default.
    The library never passes one: it knows what a value is, not how long the
    deployment wants it, so expiry belongs to whoever built the cache.
    """

    def get(self, key: str) -> Any | None:
        """The cached value, or `None`."""
        ...

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value."""
        ...


@runtime_checkable
class ByteCache(Protocol):
    """
    Somewhere to keep catalog reads as bytes. Anything across a process boundary.

    The library encodes and decodes; an implementation never sees a `Table`,
    which is what makes an adapter that forgets to encode unrepresentable rather
    than merely unlikely.

    The method names differ from `ObjectCache`'s deliberately. `isinstance`
    against a `runtime_checkable` Protocol compares method names and nothing
    else, so two protocols both spelling `get` and `set` would be
    indistinguishable at runtime and would need a marker attribute whose only
    job was to say which of two identical shapes was meant. Two smaller things
    fall out: an implementation wrapping a client that already has `get` and
    `set` with other semantics can delegate without shadowing, and a two-tier
    cache can implement both. Where both are present the library uses
    `ObjectCache`, because that path costs no encode.

    `None` means miss, and specifically not `b''`, which is a value.

    **The contract on sharing.** A cache must not be shared across databases. It
    must also not be shared across identities *unless* the caller passes
    `identity`, since that already leads the key. One namespace per database,
    per identity you cannot name — the reads this caches are privilege-filtered,
    so getting it wrong serves one user's readable set to another, which is
    silent and reads as a database privilege bug.
    """

    def get_bytes(self, key: str) -> bytes | None:
        """The stored bytes, or `None`."""
        ...

    def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
        """Store bytes."""
        ...


Cache: TypeAlias = ObjectCache | ByteCache
"""
Either discipline. An implementer satisfies whichever they can.

A plain dict satisfies neither, which is a break from every version before
0.9.0: it has `get` and no `set`. `pysqlsuggestions.caches.MemoryCache` is the
dict this used to be.
"""
