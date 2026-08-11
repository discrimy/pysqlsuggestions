"""
The shared corpus every dialect must pass.

A dialect is data, so a new one is a few dozen lines and no code — which makes
it easy to write and easy to get quietly wrong. Nothing else in the suite tells
you whether a dialect *works*: the tests that exist were written against the
backends they were written for, and a third-party dialect has none at all.

Shipped rather than kept in `tests/` because that is the point. Anyone
publishing a dialect through the entry-point group can run this against it and
get the same answer we do.

    from pysqlsuggestions.testing import DialectConformance

    failures = DialectConformance.check(MY_DIALECT)
    assert not failures, failures

The cases are written against what a dialect *says about itself* — its
namespace depth, its quote character, its clause vocabulary — so the same
propositions become different SQL for Postgres, ClickHouse and Trino. Asserting
one fixed string would only ever test the dialect it was written for.
"""

from __future__ import annotations

from dataclasses import dataclass

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import Dialect

__all__ = ['Case', 'DialectConformance']

USERS = (('id', 'integer'), ('email', 'varchar'), ('is_staff', 'boolean'))
ORDERS = (('id', 'integer'), ('user_id', 'integer'), ('total', 'numeric'))

SCHEMA = 'shop'
CATALOG = 'main'


@dataclass(frozen=True, slots=True)
class Case:
    """One proposition, and the SQL that puts it to a dialect."""

    name: str
    sql: str
    """The caret is at the end. Every case is written so that it can be."""
    expect: tuple[str, ...] = ()
    """Texts that must appear."""
    forbid: tuple[str, ...] = ()
    """Texts that must not."""


class DialectConformance:
    """
    What a dialect must satisfy to be usable at all.

    Two kinds of check. `structure` reads the declarations for mistakes that can
    only ever do nothing — a lowercase clause name, a `follows` naming a clause
    that is not there. `cases` puts propositions every caller assumes without
    checking: an alias reaches its columns, a dotted path narrows one level at a
    time, a quoted name is the same name, both sides of a join are in scope.
    Each has been a real defect in this library at some point.

    Not a test of SQL coverage — a dialect may know as few clauses as it likes —
    and not a test against a server. The cases are built from what the dialect
    says about itself, so one that is wrong but self-consistent will pass;
    only the integration suite can catch a namespace that does not match the
    backend it names.
    """

    @staticmethod
    def catalog(dialect: Dialect) -> MemoryCatalog:
        """A fixture shaped to this dialect's namespace depth."""
        snapshot = {(SCHEMA, 'users'): list(USERS), (SCHEMA, 'orders'): list(ORDERS)}
        if len(dialect.namespace.levels) >= 3:  # noqa: PLR2004
            return MemoryCatalog(snapshot, catalogs={CATALOG: [SCHEMA]})
        return MemoryCatalog(snapshot)

    @staticmethod
    def reference(dialect: Dialect, table: str) -> str:
        """A fully qualified relation reference, however many levels that takes."""
        parts = [SCHEMA, table]
        if len(dialect.namespace.levels) >= 3:  # noqa: PLR2004
            parts.insert(0, CATALOG)
        return '.'.join(parts)

    @staticmethod
    def cases(dialect: Dialect) -> list[Case]:
        """The corpus, spelled for `dialect`."""
        users = DialectConformance.reference(dialect, 'users')
        quote = dialect.syntax.identifier_quotes[0]
        levels = dialect.namespace.levels
        orders = DialectConformance.reference(dialect, 'orders')
        cases = [
            Case(
                name='an alias declared with AS reaches its columns',
                sql=f'SELECT * FROM {users} AS u WHERE u.',
                expect=('id', 'email', 'is_staff'),
            ),
            Case(
                name='an alias declared without AS reaches its columns',
                sql=f'SELECT * FROM {users} u WHERE u.',
                expect=('id', 'email', 'is_staff'),
            ),
            Case(
                name='a relation answers to its own name',
                sql=f'SELECT * FROM {users} WHERE users.',
                expect=('id', 'email', 'is_staff'),
            ),
            Case(
                name='a quoted alias is the same alias',
                sql=f'SELECT * FROM {users} AS {quote}u{quote} WHERE {quote}u{quote}.',
                expect=('id', 'email', 'is_staff'),
            ),
            Case(
                name='a CTE is a relation the statement described',
                sql=f'WITH t AS (SELECT id FROM {users}) SELECT * FROM t WHERE t.',
                expect=('id',),
                forbid=('email', 'is_staff'),
            ),
            Case(
                name='a relation position offers relations',
                sql='SELECT * FROM ',
                expect=(CATALOG if len(levels) >= 3 else SCHEMA,),  # noqa: PLR2004
            ),
            Case(
                name='a join position offers what a relation position offers',
                sql=f'SELECT * FROM {users} AS u JOIN ',
                expect=(CATALOG if len(levels) >= 3 else SCHEMA,),  # noqa: PLR2004
            ),
            Case(
                name='both sides of a join are in scope',
                sql=f'SELECT * FROM {users} AS u JOIN {orders} AS o ON o.user_id = u.id WHERE o.',
                expect=('total',),
                forbid=('email',),
            ),
        ]
        # A dotted path narrows one level per segment, however many there are.
        walked: list[str] = []
        for depth, level in enumerate(levels[:-1]):
            walked.append(CATALOG if level == levels[0] and len(levels) >= 3 else SCHEMA)  # noqa: PLR2004
            wanted = SCHEMA if depth + 1 < len(levels) - 1 else 'users'
            cases.append(
                Case(
                    name=f'a {level} qualifier offers what it contains',
                    sql='SELECT * FROM ' + '.'.join(walked) + '.',
                    expect=(wanted,),
                ),
            )
        return [case for case in cases if case.expect or case.forbid]

    @staticmethod
    def structure(dialect: Dialect) -> list[str]:
        """
        Declarations that cannot work, whatever the backend does.

        The behavioural cases below build their SQL from what the dialect says
        about itself, so a dialect that is *wrong but consistent* passes them —
        claim three namespace levels and you get a three-level fixture. Only a
        real server can settle that, which is what the integration suite is for.

        These are the other kind of mistake: a declaration that contradicts the
        engine's own conventions and therefore does nothing at all. Every one of
        them is silent — no error, just a clause that is never offered.
        """
        problems: list[str] = []
        names = {clause.name for clause in dialect.clauses.clauses}

        if len(dialect.namespace.levels) < 2:  # noqa: PLR2004
            problems.append(f'namespace has {len(dialect.namespace.levels)} levels; a relation needs at least two')
        if not dialect.syntax.identifier_quotes:
            problems.append('no identifier quote character, so no name that needs quoting can be inserted')

        for clause in dialect.clauses.clauses:
            if clause.name != clause.name.upper():
                problems.append(f'clause {clause.name!r} is not uppercase, and is compared against uppercased text')
            unknown = sorted(name for name in clause.follows if name not in names)
            if unknown:
                problems.append(f'clause {clause.name!r} follows {unknown}, which no clause here declares')
            if clause.aliases_with and clause.aliases_with not in clause.followed_by:
                problems.append(
                    f'clause {clause.name!r} aliases with {clause.aliases_with!r}, '
                    f'which is not among the words it offers',
                )

        for phrase in dialect.statement_start:
            if phrase.upper() not in names:
                problems.append(f'statement may start with {phrase!r}, which no clause here declares')

        return [f'{dialect.name}: {problem}' for problem in problems]

    @staticmethod
    def check(dialect: Dialect, *, limit: int = 50) -> list[str]:
        """
        Run the corpus. Returns one line per failure, empty when conformant.

        A list rather than an exception so a caller sees every failure at once:
        a dialect with the wrong namespace depth fails most of these, and
        learning that one at a time is a poor way to spend an afternoon.
        """
        catalog = DialectConformance.catalog(dialect)
        failures: list[str] = DialectConformance.structure(dialect)
        for case in DialectConformance.cases(dialect):
            found = [s.text for s in complete(case.sql, len(case.sql), dialect, catalog, limit=limit)]
            plain = {text.rsplit('.', 1)[-1] for text in found}
            missing = [want for want in case.expect if want not in plain]
            present = [deny for deny in case.forbid if deny in plain]
            if missing or present:
                failures.append(
                    f'{dialect.name}: {case.name}\n'
                    f'    sql      {case.sql!r}\n'
                    f'    missing  {missing}\n'
                    f'    unwanted {present}\n'
                    f'    offered  {found[:8]}',
                )
        return failures
