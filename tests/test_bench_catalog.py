"""
The generated schema, checked without a database.

Everything here is a pure function over the ladder, which is the half that can
be wrong in a way the numbers would not reveal: a schema that failed to build
the same way twice would make two runs incomparable and look like a regression.
"""

from __future__ import annotations

import pytest

from scripts.bench_catalog import LADDER, Ladder, build, column_name, column_type, ddl, format_rows, table_name


def test_a_key_column_is_always_an_integer() -> None:
    """
    Every name ending in `id` has to be the same type, or the schema will not build.

    The first version of this generator picked types by position, so `user_id`
    came out `timestamptz` in some tables and the foreign key referencing `id`
    was rejected — `Key columns "user_id" and "id" are of incompatible types`.
    A generator that fails halfway leaves a partly-built database that still
    answers queries, which is the worst way for this to go wrong.
    """
    keyed = [(table, column) for table in range(40) for column in range(30) if column_name(column).endswith('id')]
    assert keyed, 'no key columns in the sample, so this asserts nothing'
    assert {column_type(table, column) for table, column in keyed} == {'bigint'}


def test_the_same_index_always_names_the_same_relation() -> None:
    """Two runs have to produce the same schema, or their timings are not comparable."""
    assert [table_name(index) for index in range(5)] == [table_name(index) for index in range(5)]
    assert table_name(0) != table_name(1)


def test_relation_names_do_not_collide_across_a_large_ladder() -> None:
    """
    A collision would silently shrink the schema being measured.

    `CREATE TABLE` would fail outright, but the interesting case is the one that
    does not: a name reused within the run makes the catalog smaller than the
    ladder claims, so every number is reported against the wrong size.
    """
    names = [table_name(index) for index in range(LADDER[-1].tables)]
    assert len(set(names)) == len(names)


def test_a_column_list_is_as_long_as_the_ladder_asks_for() -> None:
    """Distinct names, so a table really does have the width being measured."""
    names = [column_name(index) for index in range(LADDER[-1].columns)]
    assert len(set(names)) == len(names)


def test_the_ddl_declares_a_key_an_index_and_a_foreign_key() -> None:
    """The three things the reads under measurement actually look at."""
    statements = list(ddl(Ladder('bench_t', tables=3, columns=12, indexes=2)))
    joined = '\n'.join(statements)
    assert joined.count('CREATE TABLE') == 3
    assert joined.count('CREATE INDEX') == 6
    # Two, not three: the first table has nothing before it to reference.
    assert joined.count('FOREIGN KEY') == 2
    assert 'PRIMARY KEY (id)' in joined


def test_one_table_needs_no_foreign_key() -> None:
    """The boundary the chain starts from, which is off-by-one bait."""
    assert 'FOREIGN KEY' not in '\n'.join(ddl(Ladder('bench_t', tables=1, columns=4, indexes=0)))


@pytest.mark.parametrize('indexes', [0, 1, 3])
def test_every_table_gets_the_indexes_asked_for(indexes: int) -> None:
    """Indexes are the point of the `tables()` measurement, so their count has to be exact."""
    statements = '\n'.join(ddl(Ladder('bench_t', tables=4, columns=10, indexes=indexes)))
    assert statements.count('CREATE INDEX') == 4 * indexes


def test_the_report_lines_up_its_numbers() -> None:
    """The output is read by eye across runs, so a row renders one way only."""
    rendered = format_rows('reads', [('tables(None)', 55.7, '20000 rows'), ('columns(...)', 1.4, '30 rows')])
    lines = rendered.splitlines()
    assert 'reads' in lines[0]
    assert '55.7' in rendered
    assert '20000 rows' in rendered
    # The measurement column starts at the same offset on every row.
    offsets = [line.index('ms') for line in lines if 'ms' in line]
    assert len(set(offsets)) == 1


class _FakeCursor:
    """Records statements and the autocommit state each one ran under."""

    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def __enter__(self) -> _FakeCursor:
        """Usable as a context manager, the way psycopg2's is."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Nothing to release."""

    def execute(self, statement: str) -> None:
        """Record the statement against the autocommit state in force."""
        self._connection.ran.append((statement, self._connection.autocommit))


class _FakeConnection:
    """Enough of a psycopg2 connection for `build` to run against nothing."""

    def __init__(self) -> None:
        self.autocommit = False
        self.ran: list[tuple[str, bool]] = []

    def cursor(self) -> _FakeCursor:
        """A cursor writing back to this connection's log."""
        return _FakeCursor(self)

    def commit(self) -> None:
        """Committing changes nothing that is asserted on."""

    def close(self) -> None:
        """Nothing to close."""


def test_analyze_does_not_run_inside_a_transaction() -> None:
    """
    `ANALYZE` over a whole database needs its own transaction per relation.

    Inside an explicit one it must instead hold a lock on every relation at once,
    which on the 5000-table rung is 20 000 of them and exceeds
    `max_locks_per_transaction` — `out of shared memory`, after the several
    minutes it took to create the tables, leaving the schema built but never
    analysed so `reltuples` reads -1 and every relation reports no row count.

    It survived the two smaller rungs, which is what makes this worth pinning:
    the failure appears only at the size the benchmark exists to measure.
    """
    admin, builder = _FakeConnection(), _FakeConnection()
    pending = [admin, builder]
    build(Ladder('bench_t', tables=2, columns=6, indexes=1), lambda _dsn: pending.pop(0))

    analyzed = [autocommit for statement, autocommit in builder.ran if statement.strip() == 'ANALYZE']
    assert analyzed == [True], 'ANALYZE must run with autocommit on, or it holds every lock at once'


def test_the_database_is_dropped_before_it_is_rebuilt() -> None:
    """A rebuild must not measure yesterday's schema, and DROP needs autocommit too."""
    admin, builder = _FakeConnection(), _FakeConnection()
    pending = [admin, builder]
    build(Ladder('bench_t', tables=1, columns=4, indexes=0), lambda _dsn: pending.pop(0))

    assert [statement for statement, _ in admin.ran] == [
        'DROP DATABASE IF EXISTS bench_t WITH (FORCE)',
        'CREATE DATABASE bench_t',
    ]
    assert all(autocommit for _, autocommit in admin.ran), 'CREATE DATABASE cannot run in a transaction'
    assert not builder.ran or builder.ran[0][0].startswith('CREATE TABLE')
