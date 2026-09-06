"""
The harnesses a third-party implementation needs to prove itself.

Two of them: a shared corpus every dialect must pass, and the doubles and
conformance checks for the cache port.

The corpus first.

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
from pysqlsuggestions.catalogs.dbapi import _markers, _quoted_spans
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.testing.caches import CacheConformance, InMemoryByteCache
from pysqlsuggestions.types import Function, Kind

_QUERY_ARITY = {
    'schemas': 1,
    'tables': 1,
    'queryable_tables': 1,
    'columns': 2,
    'columns_in': 2,
    'functions': 1,
    'column_search': 1,
    'relation_search': 1,
    'foreign_keys': 1,
    'values': 3,
}
"""
How many values `DbapiCatalog` passes each introspection query.

Duplicated from that module rather than derived from it, deliberately: this
package is shipped so a third-party dialect can check itself, and a table that
silently followed a refactor of the caller would stop catching the mistake it
exists for. A change to either has to be a change to both, which is the point.

`columns_in` is the least of what it is given, not the most: its last marker is a
spread and takes as many relation names as the statement holds. Two is therefore
the floor — a schema and at least one name — and the check below is what says the
spread is where a spread may be.
"""

__all__ = ['CacheConformance', 'Case', 'DialectConformance', 'InMemoryByteCache']

USERS = (('id', 'integer'), ('email', 'varchar'), ('is_staff', 'boolean'))
ORDERS = (('id', 'integer'), ('user_id', 'integer'), ('total', 'numeric'))

SEQUENCE = 'orders_id_seq'
"""
A sequence in the fixture, so every dialect is asked to keep one out of a
relation position — including the dialects that have no sequences at all. A
proposition that only applied to backends with the feature could not catch the
dialect that grows it next.
"""
PROCEDURE = 'recalculate_totals'
"""A callable that may only be invoked, never evaluated."""

SCHEMA = 'shop'
OTHER = 'vault'
"""
A second namespace, deliberately off the search path.

Without a relation the bare position cannot see, no case can tell a dialect
that searches from one that only lists.
"""
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
    expect_exact: tuple[str, ...] = ()
    """
    Texts that must appear *verbatim*, qualifier and all.

    `expect` and `forbid` compare against the last segment, which is right for
    almost everything — the proposition is usually about which thing is offered.
    This one is about how it is written: `invoices.amount` and
    `public.invoices.amount` name the same column and only one of them runs when
    two same-named relations are in scope.
    """


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
        """
        A fixture shaped to this dialect's namespace depth.

        `OTHER` sits outside the search path on purpose: a relation the bare
        position cannot see is the only way a case can tell a dialect that
        searches from one that merely lists.

        A sequence and a procedure are always present, whether or not the
        dialect has either. Both exist to be *excluded* from the ordinary
        positions, and a fixture that only held them for backends with the
        feature could not make that proposition at all.

        `OTHER` also holds a relation named like one in `SCHEMA`. Two relations
        of the same name in one FROM is a state every backend here allows and
        every backend here then refuses a bare reference in, so the fixture has
        to be able to produce it.
        """
        snapshot = {
            (SCHEMA, 'users'): list(USERS),
            (SCHEMA, 'orders'): list(ORDERS),
            (OTHER, 'archived_orders'): list(ORDERS),
            (OTHER, 'users'): list(USERS),
            (SCHEMA, SEQUENCE): [('last_value', 'bigint')],
            (SCHEMA, 'users_active'): list(USERS),
        }
        kinds = {(SCHEMA, SEQUENCE): 'sequence', (SCHEMA, 'users_active'): 'view'}
        # `order_count` rather than `total`: the fixture already has a column
        # called `total`, and a forbid clause that could be satisfied by the
        # wrong thing proves nothing.
        functions = (
            Function(schema=SCHEMA, name=PROCEDURE, args='', result=None, kind='procedure'),
            Function(schema=SCHEMA, name='order_count', args='', result='integer'),
        )
        if len(dialect.namespace.levels) >= 3:  # noqa: PLR2004
            return MemoryCatalog(
                snapshot,
                table_kinds=kinds,
                functions=functions,
                catalogs={CATALOG: [SCHEMA, OTHER]},
            )
        return MemoryCatalog(snapshot, table_kinds=kinds, functions=functions, search_path=(SCHEMA,))

    @staticmethod
    def reference(dialect: Dialect, table: str, schema: str = SCHEMA) -> str:
        """A fully qualified relation reference, however many levels that takes."""
        parts = [schema, table]
        if len(dialect.namespace.levels) >= 3:  # noqa: PLR2004
            parts.insert(0, CATALOG)
        return '.'.join(parts)

    @staticmethod
    def parameter(dialect: Dialect) -> str:
        """
        A half-written bound parameter whose stem matches a fixture column, or ''.

        The stem matters: a parameter named `param` collides with nothing in the
        fixture, so a dialect that had forgotten the rule entirely would still
        pass. `is` is a prefix of `is_staff`, which is exactly what must not be
        offered.

        Spellings whose body is `none` are skipped. `?` has no interior, so
        there is no caret inside one to make a proposition about — which is the
        honest answer for Trino rather than a gap in the corpus.
        """
        for placeholder in dialect.syntax.placeholders:
            if placeholder.body in ('name', 'any'):
                return f'{placeholder.opens}is'
        return ''

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
                name='a relation position never offers a sequence',
                sql='SELECT * FROM ',
                forbid=(SEQUENCE,),
            ),
            Case(
                name='a reference to one of two same-named relations is not ambiguous',
                sql=f'SELECT * FROM {users}, {DialectConformance.reference(dialect, "users", schema=OTHER)} WHERE ',
                expect_exact=(f'{users}.id',),
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
        spelled = DialectConformance.parameter(dialect)
        if spelled:
            cases.append(
                Case(
                    name='a caret inside a bound parameter offers no columns',
                    sql=f'SELECT * FROM {users} AS u WHERE u.is_staff = {spelled}',
                    forbid=('is_staff',),
                ),
            )
        if dialect.catalog_queries.relation_search is not None:
            cases.append(
                Case(
                    name='a prefix reaches a relation outside the search path',
                    sql='SELECT * FROM archiv',
                    expect=('archived_orders',),
                ),
            )
        declared = next(iter(dialect.literal_arguments), None)
        if declared is not None:
            cases.append(
                Case(
                    # A prefix, and one that matches a relation as well as the
                    # sequence. A three-level fixture has no default namespace
                    # at all — `tables(None)` is empty there, as it is against a
                    # real Trino — so an empty prefix could only ever be
                    # answered by a two-level dialect. `orders` also makes the
                    # forbid live: it is a relation this position must not offer.
                    name='a literal argument offers what the dialect says it names',
                    sql=f"SELECT {declared.function}('orders",
                    expect=(SEQUENCE,),
                    forbid=('orders',),
                ),
            )
        # Found by what the clause declares rather than by the name `DROP VIEW`,
        # and asserting the fixture relation of that kind — so the case tests
        # the dialect's own claim against the catalog it will really read.
        narrowed = next((c for c in dialect.clauses.clauses if c.relation_kinds == ('view',)), None)
        if narrowed is not None:
            cases.append(
                Case(
                    # A prefix, and one both the table and the view match. A
                    # three-level fixture has no default namespace — `tables(None)`
                    # is empty there, as against a real Trino — so an empty prefix
                    # would answer with catalogs. `users` also keeps the forbid
                    # live: the table is what this clause must not offer.
                    name='a clause narrowed to view kinds offers only those',
                    sql=f'{narrowed.name} users',
                    expect=('users_active',),
                    forbid=('users',),
                ),
            )
        # Found by what the clause declares rather than by the name `WITH`, so a
        # dialect spelling its CTE clause differently is still covered. `expect`
        # names the first word the dialect itself lists, so the case asserts the
        # dialect's own claim rather than one this corpus invented.
        grouped = next((c for c in dialect.clauses.clauses if c.opens_a_group), None)
        if grouped is not None:
            cases.append(
                Case(
                    name='a clause that opens a group says what may begin one',
                    sql=f'{grouped.name} a AS (',
                    expect=(grouped.opens_a_group[0],),
                ),
            )
        # Found by what the clause declares rather than by the name
        # `CREATE TABLE`, so a dialect spelling its DDL differently is still
        # covered. A new field that changes what a caret admits belongs here:
        # the corpus ships in the wheel for third-party dialects, which have no
        # other test at all.
        defines = next((c for c in dialect.clauses.clauses if c.defines_columns), None)
        if defines is not None and dialect.types:
            cases.append(
                Case(
                    name='a definition list answers its type position with a type',
                    sql=f'{defines.name} t (id ',
                    expect=(dialect.types[0],),
                    forbid=('users',),
                ),
            )
        # Found by what the clause suggests rather than by the name `CALL`, so a
        # dialect spelling its call statement differently is still covered.
        calls = next((c.name for c in dialect.clauses.clauses if Kind.PROCEDURE in c.suggests), None)
        if calls is not None:
            cases.append(
                Case(
                    name='a procedure position offers procedures and not functions',
                    sql=f'{calls} ',
                    expect=(PROCEDURE,),
                    forbid=('order_count',),
                ),
            )
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
        return [case for case in cases if case.expect or case.forbid or case.expect_exact]

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
            # Before the uppercase test, which a blank name passes: `''.upper()`
            # is `''` and so is `'   '.upper()`. A name with no words has no
            # first word to be indexed by, so it can never match anything — and
            # it used to raise out of `complete` rather than merely doing
            # nothing, which is the sharper reason to name it here.
            if not clause.name.split():
                problems.append('a clause has a blank name, which no text can match')
                continue
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

        for placeholder in dialect.syntax.placeholders:
            if not placeholder.opens:
                problems.append(
                    'a placeholder has an empty opening delimiter, so it would match at every '
                    'position and consume nothing',
                )
            if placeholder.body == 'any' and not placeholder.closes:
                problems.append(
                    f'placeholder {placeholder.opens!r} has an "any" body and no closing delimiter, '
                    f'so it can never end and is never lexed',
                )

        # `DbapiCatalog` fixes each query's arity, so a marker beyond it is a
        # static contradiction — the same class as a `follows` naming a clause
        # that is not there, and needing neither a server nor a self-consistent
        # dialect to see. Unchecked, a `$N` typo surfaced as an IndexError from
        # inside `render` on the first catalog read, which is precisely the
        # mistake a third-party dialect ships this harness to catch.
        for name, given in _QUERY_ARITY.items():
            query = getattr(dialect.catalog_queries, name, None)
            if query is None:
                continue
            # Counted the way `render` counts, not with a bare regex: a `$N`
            # inside a literal is text to it, and Trino spells regexp
            # backreferences that way — so a working `schemas` query was
            # reported as binding more values than it is given.
            found = [int(marker.group(1)) for marker in _markers(query.sql, _quoted_spans(query.sql, dialect.syntax))]
            if 0 in found:
                # The one `$N` the arity test below cannot see, and the one
                # `render` refuses outright — so a dialect carrying it passed
                # every check here and raised on its first catalog read.
                problems.append(f'catalog query {name!r} uses $0, and markers are one-based')
            # A spread claims every value from its own position on, so anything
            # after it binds what the spread already took. `render` refuses that
            # too, but only against a live server on the first catalog read —
            # and a dialect contradicting itself is exactly what this harness is
            # for seeing without one.
            spreads = [
                marker for marker in _markers(query.sql, _quoted_spans(query.sql, dialect.syntax)) if marker.group(2)
            ]
            if len(spreads) > 1:
                problems.append(f'catalog query {name!r} holds {len(spreads)} spread markers, and one is the most')
            elif spreads and any(
                other.start() > spreads[0].start()
                for other in _markers(query.sql, _quoted_spans(query.sql, dialect.syntax))
            ):
                problems.append(
                    f'catalog query {name!r} has a marker after its spread, which binds what the spread took',
                )

            wanted = max(found, default=0)
            if wanted > given:
                problems.append(
                    f'catalog query {name!r} binds ${wanted} but is only ever given {given} value(s)',
                )

        for declared in dialect.literal_arguments:
            if len(declared.function.split()) != 1 or not declared.function.isidentifier():
                problems.append(
                    f'literal argument {declared.function!r} is not a single word, '
                    f'so it can never equal the name of an enclosing call',
                )
            if not declared.suggests:
                problems.append(f'literal argument {declared.function!r} suggests nothing, so it can never answer')

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
            # A name written into a string literal — a sequence inside
            # `nextval('…')` — is still that name, and every proposition here is
            # about the name. The quotes belong to the position, not the answer.
            plain = {text.rsplit('.', 1)[-1].strip('\'"') for text in found}
            missing = [want for want in case.expect if want not in plain]
            present = [deny for deny in case.forbid if deny in plain]
            exact = [want for want in case.expect_exact if want not in found]
            if missing or present or exact:
                failures.append(
                    f'{dialect.name}: {case.name}\n'
                    f'    sql      {case.sql!r}\n'
                    f'    missing  {missing + exact}\n'
                    f'    unwanted {present}\n'
                    f'    offered  {found[:8]}',
                )
        return failures
