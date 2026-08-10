"""
Golden requests, translated from pgcli's test_sqlcompletion and report_service's test_autocomplete.

Each case marks the caret inline with ⌶, which reads far better than an integer
offset and cannot drift out of sync with the SQL when a case is edited.

`pending=True` means the case is expected to fail: it is an xfail(strict=True)
until the stage that satisfies it lands. The burn-down count is reported by
tests/conftest.py on every run.
"""

from __future__ import annotations

from dataclasses import dataclass

CARET = '⌶'


@dataclass(frozen=True)
class GoldenRequest:
    """One (sql, caret) input and the Request it must produce."""

    sql: str
    """Caret marked with ⌶. The marker is stripped before lexing."""
    kinds: tuple[str, ...]
    """Kind values, in order. Compared against [k.value for k in request.kinds]."""
    prefix: str = ''
    qualifier: tuple[str, ...] = ()
    clause: str | None = None
    relations: tuple[str, ...] = ()
    """Rendered as 'alias:dotted.path' per relation, in scope order. '' alias when unaliased."""
    dialect: str = 'postgres'
    pending: bool = False
    """Set True for a case the current stage cannot yet satisfy: an xfail(strict=True) until it can."""
    note: str = ''


def split_caret(sql: str) -> tuple[str, int]:
    """Strip the ⌶ marker and return (sql without marker, caret offset)."""
    caret = sql.index(CARET)
    return sql[:caret] + sql[caret + len(CARET) :], caret


CASES: tuple[GoldenRequest, ...] = (
    # --- prefix and qualifier ------------------------------------------------
    GoldenRequest(
        sql='SELECT ⌶',
        kinds=('column', 'function'),
        clause='SELECT',
        note='empty prefix, no relations yet',
    ),
    GoldenRequest(
        sql='SELECT id, na⌶ FROM users u',
        kinds=('column', 'function'),
        prefix='na',
        clause='SELECT',
        relations=('u:users',),
        note='plan.md §3.3 worked trace: scope comes from the whole statement',
    ),
    GoldenRequest(
        sql='SELECT * FROM users u WHERE u.⌶',
        kinds=('column',),
        qualifier=('u',),
        clause='WHERE',
        relations=('u:users',),
        note='the qualifier collapses the answer to columns only',
    ),
    GoldenRequest(
        sql='SELECT * FROM orders o JOIN users u ON o.user_id = u.⌶',
        kinds=('column',),
        qualifier=('u',),
        clause='ON',
        relations=('o:orders', 'u:users'),
    ),
    GoldenRequest(
        sql='SELECT * FROM users u WHERE u.em⌶',
        kinds=('column',),
        prefix='em',
        qualifier=('u',),
        clause='WHERE',
        relations=('u:users',),
    ),
    GoldenRequest(
        sql='SELECT * FROM "Mixed Case" m WHERE m.⌶',
        kinds=('column',),
        qualifier=('m',),
        clause='WHERE',
        relations=('m:Mixed Case',),
        note='quoted identifiers keep their case',
    ),
    # --- namespace depth -----------------------------------------------------
    GoldenRequest(
        sql='SELECT * FROM analytics.⌶',
        kinds=('column', 'table'),
        qualifier=('analytics',),
        clause='FROM',
        dialect='postgres',
        note='segment 1 reads as a schema, or as a relation not in the FROM list',
    ),
    GoldenRequest(
        sql='SELECT * FROM analytics.⌶',
        kinds=('column', 'table'),
        qualifier=('analytics',),
        clause='FROM',
        dialect='clickhouse',
        note='segment 1 reads as a database, same answer shape',
    ),
    GoldenRequest(
        sql='SELECT * FROM analytics.⌶',
        kinds=('schema',),
        qualifier=('analytics',),
        clause='FROM',
        dialect='trino',
        note='segment 1 reads as a catalog, so segment 2 is a schema',
    ),
    GoldenRequest(
        sql='SELECT public.users.⌶ FROM public.users',
        kinds=('column',),
        qualifier=('public', 'users'),
        clause='SELECT',
        relations=(':public.users',),
        dialect='postgres',
        note='schema.table.column is legal, so a two-segment qualifier is ambiguous',
    ),
    # --- clause detection ----------------------------------------------------
    GoldenRequest(sql='SELECT * FROM ⌶', kinds=('table', 'schema'), clause='FROM'),
    GoldenRequest(
        sql='SELECT * FROM t JOIN ⌶',
        kinds=('table', 'schema', 'keyword'),
        clause='JOIN',
        relations=(':t',),
        note='a relation is already in scope, so what may follow it is offered too',
    ),
    GoldenRequest(
        sql='SELECT * FROM t GROUP BY ⌶',
        kinds=('column', 'function'),
        clause='GROUP BY',
        relations=(':t',),
    ),
    GoldenRequest(
        sql='SELECT * FROM t ORDER BY ⌶',
        kinds=('column', 'function'),
        clause='ORDER BY',
        relations=(':t',),
    ),
    GoldenRequest(
        sql='SELECT a, (SELECT b FROM t2), ⌶ FROM t1',
        kinds=('column', 'function'),
        clause='SELECT',
        relations=(':t1',),
        note='a subquery that closed before the caret must not capture the clause',
    ),
    GoldenRequest(
        sql='SELECT * FROM t WHERE (a AND ⌶)',
        kinds=('column', 'function'),
        clause='WHERE',
        relations=(':t',),
        note='a non-subquery paren group falls back to the enclosing clause',
    ),
    GoldenRequest(
        sql='SELECT * FROM t1; SELECT * FROM t2 WHERE ⌶',
        kinds=('column', 'function'),
        clause='WHERE',
        relations=(':t2',),
        note='statement isolation: t1 is not in scope',
    ),
    # --- CTEs and subqueries -------------------------------------------------
    GoldenRequest(
        sql='WITH recent AS (SELECT id, total FROM orders) SELECT r.⌶ FROM recent r',
        kinds=('column',),
        qualifier=('r',),
        clause='SELECT',
        relations=('r:recent',),
        note='plan.md §3.3: no catalog call at all',
    ),
    GoldenRequest(
        sql='WITH a AS (SELECT * FROM users) SELECT a.⌶ FROM a',
        kinds=('column',),
        qualifier=('a',),
        clause='SELECT',
        relations=(':a',),
        note='the star case the three-state Projection exists for',
    ),
    GoldenRequest(
        sql='SELECT * FROM (SELECT id FROM orders) d WHERE d.⌶',
        kinds=('column',),
        qualifier=('d',),
        clause='WHERE',
        relations=('d:',),
        note='derived table; path is empty, projection is self-described',
    ),
    GoldenRequest(
        sql='SELECT * FROM users u WHERE id IN (SELECT user_id FROM orders o WHERE o.⌶)',
        kinds=('column',),
        qualifier=('o',),
        clause='WHERE',
        relations=('o:orders', 'u:users'),
        note='inner scope first, outer scope still visible',
    ),
    # --- literals and comments suppress everything ---------------------------
    GoldenRequest(
        sql="SELECT * FROM t WHERE name = 'ab⌶",
        kinds=('value',),
        prefix='ab',
        clause='WHERE',
        relations=(':t',),
        note='inside a literal being written as a value: offer the values that column holds',
    ),
    GoldenRequest(
        sql="SELECT * FROM t WHERE name LIKE '%smith%⌶'",
        kinds=(),
        clause='WHERE',
        relations=(':t',),
        note='inside a terminated literal: still nothing',
    ),
    GoldenRequest(
        sql='SELECT * FROM t -- note ⌶',
        kinds=(),
        clause='FROM',
        relations=(':t',),
        note='inside a comment: nothing',
    ),
)
