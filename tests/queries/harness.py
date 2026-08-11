"""
The corpus these tests run against, and the calls they make.

The SQL and the fixture come from a production autocomplete suite this library
replaced, which is why they cover ground nobody would think to invent: a CTE
that refers to itself, a dollar-quoted body containing the word FROM, union
branches, Cyrillic identifiers, report macros in a value position, a parameter
that looks like a dollar quote. The assertions are ours — they say what this
library returns, qualified columns and all.
"""

from __future__ import annotations

from typing import Any

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Suggestion

CATALOG = {
    ('public', 'auth_user'): [
        ('id', 'integer'),
        ('username', 'character varying(150)'),
        ('email', 'character varying(254)'),
        ('is_staff', 'boolean'),
        ('date_joined', 'timestamp with time zone'),
    ],
    ('public', 'auth_group'): [
        ('id', 'integer'),
        ('name', 'character varying(150)'),
    ],
    ('public', 'orders'): [
        ('id', 'integer'),
        ('user_id', 'integer'),
        ('total', 'numeric'),
        ('created', 'date'),
    ],
    # prefix-matches "use", where auth_user only contains it — lets ranking be tested
    ('public', 'users_log'): [
        ('id', 'integer'),
        ('msg', 'text'),
    ],
    ('billing', 'invoices'): [
        ('id', 'integer'),
        ('order_id', 'integer'),
        ('amount', 'numeric'),
    ],
}

USER_COLUMNS = ['id', 'username', 'email', 'is_staff', 'date_joined']

DEFAULT_LIMIT = 200


def fake_catalog(catalog: Any = None, oversized: bool = False) -> MemoryCatalog:
    """Stands in for their FakeCatalog."""
    return MemoryCatalog(catalog or CATALOG, oversized=oversized)


def suggestions(
    cursor: MemoryCatalog,
    sql: str,
    pos: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Suggestion]:
    """Complete at `pos`, defaulting to end of input as their harness did."""
    return complete(sql, len(sql) if pos is None else pos, POSTGRES, cursor, limit=limit)


def texts(cursor: MemoryCatalog, sql: str, **kwargs: Any) -> list[str]:
    """Suggestion texts, exactly as this library returns them."""
    return [s.text for s in suggestions(cursor, sql, **kwargs)]


def kinds(cursor: MemoryCatalog, sql: str, **kwargs: Any) -> dict[str, str]:
    """Text -> kind."""
    return {s.text: s.kind.value for s in suggestions(cursor, sql, **kwargs)}


def at(cursor: MemoryCatalog, marked: str, **kwargs: Any) -> list[str]:
    """Suggestion texts at the ‸ marker."""
    return texts(cursor, marked.replace('‸', ''), pos=marked.index('‸'), **kwargs)


class _Context:
    """Their `analyze()` result shape, over this library's Request."""

    def __init__(self, sql: str, pos: int | None = None) -> None:
        self._request = derive_request(sql, len(sql) if pos is None else pos, POSTGRES)

    @property
    def prefix(self) -> str:
        """What is already typed."""
        return self._request.prefix

    @property
    def replace_from(self) -> int:
        """Where the replacement starts — the first half of `replace_span`."""
        return self._request.replace_span[0]

    @property
    def clause(self) -> str | None:
        """The governing clause keyword."""
        return self._request.clause

    @property
    def relations(self) -> list[_Ref]:
        """Relations in scope, in their TableRef shape."""
        scope = self._request.scope
        return [_Ref(r.path[-1] if r.path else '') for r in (scope.visible() if scope else ())]


class _Ref:
    """Their TableRef, reduced to the one field the ported tests read."""

    def __init__(self, name: str) -> None:
        self.name = name


def analyze(sql: str, pos: int | None = None) -> _Context:
    """Their analyze(), adapted."""
    return _Context(sql, pos)


CTE_SQL = """WITH a as (
    select * FROM auth_user
)
SELECT *
FROM a
WHERE a."""

ALL_ORDER_COLUMNS = ['created', 'id', 'total', 'user_id']

REPORT_SQL = """WITH активные AS (
    SELECT u.id, u.email, u.date_joined
      FROM auth_user u
     WHERE u.is_staff = false
       AND u.date_joined >= %Дата начала|ДАТА|%
), суммы AS (
    SELECT o.user_id, sum(o.total) AS итого, count(*) AS штук
      FROM orders o
      JOIN активные a ON a.id = o.user_id
     GROUP BY o.user_id
)
SELECT a.email, s.итого
  FROM активные a
  LEFT JOIN суммы s ON s.user_id = a.id
 WHERE """
