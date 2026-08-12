"""
Accepting a suggestion must not write SQL the server rejects.

Every other test here asks whether the answer was *useful*. This one asks
whether it was *legal*, which no amount of reading the clause model can settle:
the model is the thing under test, so it cannot also be the judge. Postgres can.

The difficulty is that a query being written is almost never a complete
statement, so "does it parse" answers no to everything. Postgres draws the
distinction itself, in the error it raises:

    syntax error at end of input   the statement stops early, which is the
                                   normal state of a query mid-keystroke
    syntax error at or near "as"   a token that cannot be in that place at all

Only the second is a defect. Semantic complaints — unknown column, missing
FROM-clause entry — say nothing about the suggestion and are ignored: a
half-written query is full of them.

The list below is a burn-down, not a specification. Everything in it is a real
defect this harness found; the test fails if a new one appears, and fails if a
listed one is fixed without being struck off, so the two stay in step.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from pysqlsuggestions.api import apply_suggestion, complete
from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Kind
from tests.integration.conftest import POSTGRES_DSN

pytestmark = pytest.mark.integration

CORPUS = (
    'SELECT u.id, u.username FROM auth_user AS u WHERE u.is_staff = true ORDER BY u.id DESC',
    'SELECT o.id FROM orders AS o JOIN auth_user AS u ON u.id = o.user_id WHERE o.total > 10',
    'SELECT count(*) AS n FROM orders GROUP BY user_id HAVING count(*) > 1',
    'SELECT * FROM auth_user u, orders o WHERE u.id = o.user_id',
    'WITH recent AS (SELECT id FROM orders) SELECT r.id FROM recent AS r',
    "SELECT r.status FROM reports_runlog AS r WHERE r.status = 'queued'",
    'INSERT INTO orders (user_id, total) VALUES (1, 2)',
    'UPDATE orders SET total = 1 WHERE id = 2',
    'DELETE FROM orders WHERE id = 1',
    'SELECT o.total FROM orders o ORDER BY o.total ASC NULLS LAST LIMIT 10',
    'SELECT cast(o.total AS text) FROM orders AS o',
    'SELECT u.id FROM auth_user u LEFT JOIN orders o ON o.user_id = u.id',
    'SELECT a.id FROM billing.invoices AS a WHERE a.amount > 0',
)

UNJUDGEABLE = frozenset({Kind.FUNCTION, Kind.SNIPPET})
"""
Kinds whose insertion is deliberately unfinished.

A function arrives as `count()` with the caret between the parentheses, and a
template as a shape with blanks in it. Both are illegal SQL on purpose, and
Postgres reports them the same way it reports a genuinely misplaced token — so
this harness cannot judge them and says so rather than guessing.
"""

KNOWN: frozenset[str] = frozenset()


@pytest.fixture(scope='module')
def parser() -> Iterator[Any]:
    """A connection used only to parse, never to run anything."""
    psycopg2 = pytest.importorskip('psycopg2')
    try:
        connection = psycopg2.connect(POSTGRES_DSN)
    except Exception as error:  # noqa: BLE001
        pytest.skip(f'postgres not reachable ({error}); run docker/docker-compose.yml')
    connection.autocommit = False
    yield connection
    connection.close()


def carets(sql: str) -> list[int]:
    """Every position a caret plausibly rests: each end of each gap, and the end."""
    spots = {len(sql)}
    for index, char in enumerate(sql):
        if char == ' ':
            spots |= {index, index + 1}
    return sorted(spots)


SYNTAX_ERROR = '42601'
"""
SQLSTATE for a syntax error.

Read off the exception rather than caught by class, so this file imports no
driver — the rest of the integration suite reaches psycopg2 only through
`importorskip`, and a bare `import psycopg2` here would make type checking
depend on stubs for a package the library itself never imports.
"""


def misplaced(connection: Any, sql: str) -> str:
    """
    Postgres's complaint if `sql` has a token that cannot be there, else ''.

    Only queries can be checked this way. The parse happens by prefixing
    `EXPLAIN`, and `EXPLAIN DROP TABLE t` is itself a syntax error — as is
    `EXPLAIN EXPLAIN SELECT 1`. So `CORPUS` holds DML and nothing else, and the
    DDL statement forms are covered by `tests/test_statement_forms.py` instead.

    The alternative would be executing DDL and rolling the savepoint back, which
    would cost this connection its one useful property: it parses, and never
    runs anything.
    """
    with connection.cursor() as cursor:
        try:
            cursor.execute('SAVEPOINT probe')
            # EXPLAIN plans and does not execute, so this is a read even when the
            # statement under test is an INSERT, and the savepoint unwinds it.
            cursor.execute(f'EXPLAIN {sql}')
        except Exception as error:  # noqa: BLE001
            if getattr(error, 'pgcode', None) != SYNTAX_ERROR:
                return ''  # semantic, and a half-written query is full of those
            first = str(error).splitlines()[0]
            return '' if 'at end of input' in first else first
        finally:
            with connection.cursor() as unwind:
                unwind.execute('ROLLBACK TO SAVEPOINT probe')
    return ''


def test_no_suggestion_writes_a_statement_postgres_refuses(
    parser: Any,
    postgres_catalog: DbapiCatalog,
) -> None:
    """
    Accept everything offered at every caret in the corpus, and parse the result.

    The failures are grouped by caret rather than by word, because one wrong
    decision produces as many bad suggestions as there are tables in the schema
    — thirty of them from a single missing check reads as thirty bugs.
    """
    cache: dict[object, object] = {}
    broken: dict[str, list[str]] = {}
    accepted = 0

    for statement in CORPUS:
        for caret in carets(statement):
            prefix = statement[:caret]
            for suggestion in complete(prefix, caret, POSTGRES, postgres_catalog, cache=cache, limit=12):
                if suggestion.kind in UNJUDGEABLE:
                    continue
                written, _ = apply_suggestion(prefix, suggestion, dialect=POSTGRES)
                accepted += 1
                if misplaced(parser, written):
                    broken.setdefault(prefix, []).append(suggestion.text)

    fresh = sorted(set(broken) - KNOWN)
    assert not fresh, 'suggestions that write unparseable SQL at a caret not previously known:\n' + '\n'.join(
        f'  {prefix!r} + {broken[prefix][:5]}' for prefix in fresh
    )

    mended = sorted(KNOWN - set(broken))
    assert not mended, 'these no longer produce invalid SQL — strike them off KNOWN:\n' + '\n'.join(
        f'  {prefix!r}' for prefix in mended
    )

    print(f'\nacceptance burn-down: {accepted - sum(len(v) for v in broken.values())}/{accepted} land valid SQL')  # noqa: T201
