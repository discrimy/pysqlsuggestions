"""
Whether a realistic statement can be written using only what the engine offers.

A valid statement is its own oracle: at every caret inside one, whatever comes
next is by construction legal there. So if nothing offered at that caret reaches
it, the engine cannot write a query it plainly should be able to.

This is the half of correctness that needs no server. It measures recall — what
is missing — and says nothing about whether anything *also* offered is legal;
tests/integration/test_acceptance.py asks that of Postgres, because deciding it
needs a grammar and the clause model cannot be both defendant and judge. The two
overlap usefully: a caret that offers a relation where only `BY` belongs fails
both, once for the word it omits and once for the words it invents.

Carets are taken at every gap in the text rather than at the ends of the
engine's own suggestions, and that distinction is the point. `GROUP BY` arrives
as one suggestion, so walking the engine's output never stops between the two
words — while a typist stops there constantly.
"""

from __future__ import annotations

import re

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import ForeignKey, Function

CATALOG = MemoryCatalog(
    {
        ('public', 'auth_user'): [
            ('id', 'integer'),
            ('username', 'character varying(150)'),
            ('is_staff', 'boolean'),
        ],
        ('public', 'orders'): [
            ('id', 'bigint'),
            ('user_id', 'integer'),
            ('total', 'numeric'),
        ],
    },
    functions=[Function(schema='pg_catalog', name='count', args='*', result='bigint')],
    foreign_keys=[
        ForeignKey(
            schema='public',
            table='orders',
            columns=('user_id',),
            ref_schema='public',
            ref_table='auth_user',
            ref_columns=('id',),
        ),
    ],
)

GOLDEN = (
    'SELECT u.id, u.username, count(*) '
    'FROM auth_user AS u '
    'JOIN orders AS o ON o.user_id = u.id '
    'WHERE u.is_staff = true '
    'GROUP BY u.id, u.username '
    'HAVING count(*) > 1 '
    'ORDER BY u.id DESC '
    'LIMIT 10 OFFSET 5',
    'INSERT INTO orders (user_id, total) VALUES (1, 2)',
    'UPDATE orders SET total = 1 WHERE id = 2',
    'DELETE FROM orders WHERE id = 1',
    'SELECT o.total FROM orders AS o WHERE o.total IS NOT NULL',
)

KNOWN: frozenset[tuple[str, str]] = frozenset()
"""
Gaps the engine has, keyed by the word before the caret and the word wanted.

A burn-down of real defects, not a specification. Keyed by the preceding word
because that is what causes them — one clause name misread fails in every
statement using it, and a list of whole prefixes would call that five bugs.

The test fails on a new gap, and on a listed one that has been fixed without
being struck off, so the list cannot drift from the behaviour.
"""

UNKNOWABLE = re.compile(r"^(\d+|'[^']*')")
"""A literal no catalog holds: the author's own number or string."""


def reaches(want: str, remaining: str, offered: str) -> bool:
    """Whether `offered` writes what comes next, allowing a different qualifier."""
    if remaining.lower().startswith(offered.lower()):
        return True
    # `u.id`, `auth_user.id` and a bare `id` all name the same column here.
    # Which qualifier belongs is pinned by its own tests; this one asks only
    # whether the engine can reach the column at all.
    return want.split('.')[-1].lower() == offered.split('.')[-1].lower()


def gaps(sql: str) -> tuple[int, list[tuple[str, str, list[str]]]]:
    """Every caret in `sql` the engine cannot continue. Returns (probed, gaps)."""
    probed = 0
    missing: list[tuple[str, str, list[str]]] = []
    for position in [index + 1 for index, char in enumerate(sql) if char == ' ']:
        remaining = sql[position:]
        if UNKNOWABLE.match(remaining) or remaining[0] in ',()*':
            continue
        if sql[:position].rstrip().upper().endswith(' AS'):
            # What follows AS in a relation clause is a name the author invents.
            # The engine proposes forms derived from the table, and the author
            # is entitled to ignore every one of them.
            continue
        head = re.match(r'[^\s,()]*', remaining)
        want = head.group(0) if head else ''
        found = complete(sql[:position], position, POSTGRES, CATALOG, limit=40)
        probed += 1
        if not any(reaches(want, remaining, s.text) for s in found):
            missing.append((sql[:position], want, [s.text for s in found[:6]]))
    return probed, missing


def test_a_realistic_statement_is_writable_from_suggestions_alone() -> None:
    """
    Walk five statements, and at every caret ask whether anything offered
    continues them.

    Failures group by the word before the caret rather than by position: one
    clause name read wrongly fails in every statement that uses it, and counting
    positions would call that five defects.
    """
    probed = 0
    found: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for statement in GOLDEN:
        count, missing = gaps(statement)
        probed += count
        for prefix, want, offered in missing:
            found[prefix.split()[-1], want] = (prefix, offered)

    fresh = sorted(key for key in found if key not in KNOWN)
    assert not fresh, 'carets the engine cannot write past, not previously known:\n' + '\n'.join(
        f'  {found[before, want][0]!r}\n      wanted {want!r}, offered {found[before, want][1]}'
        for before, want in fresh
    )

    mended = sorted(KNOWN - set(found))
    assert not mended, 'these can be written now — strike them off KNOWN:\n' + '\n'.join(
        f'  after {before!r} wanted {want!r}' for before, want in mended
    )

    print(f'\nwritable burn-down: {probed - len(found)}/{probed} carets can continue the statement')  # noqa: T201
