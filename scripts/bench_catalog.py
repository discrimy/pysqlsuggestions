"""
What catalog reads cost as a schema gets large.

    docker compose -f docker/docker-compose.yml up -d --wait
    uv run python -m scripts.bench_catalog --build     # once; a few minutes
    uv run python -m scripts.bench_catalog

The docker fixture has nineteen relations, so nothing in the suite measures the
shape of these costs at the size where they bite. This builds a ladder of
generated schemas and reports against them, which is the harness
`docs/superpowers/specs/2026-09-06-catalog-reads-on-large-schemas-design.md`
argues from — every number in that document came from here, and none of them can
be re-checked against the fixture.

Postgres only, deliberately. The ladder is what makes the numbers mean anything
and there is no point in three generators; the ClickHouse and Trino figures in
that document are per-query floors measured against the fixtures, which needed no
ladder. Trino's own pathology has an integration test rather than a benchmark,
because it was a correctness bug that happened to be slow.

Run as a module, not as a path, for the reason the other scripts are: the tests
import `scripts.bench_catalog`, which resolves only with the repository root on
`sys.path`.

Reported in layers, because the three costs are separate and a single total
hides which one moved:

1. one introspection query, no cache, no engine
2. the cached payload, which is what a `ByteCache` pays per keystroke
3. `complete()` cold — every read a query
4. `complete()` warm — no I/O at all, so what is left is Python
5. the warm split, `resolve()` against `rank()`

`--rtt` re-runs layer 3 with a delay injected per query, since these all run
against a local server and round trips are the cost that a local server hides.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from pysqlsuggestions.api import complete
from pysqlsuggestions.caches import MemoryCache
from pysqlsuggestions.caches.codec import decode, encode
from pysqlsuggestions.catalogs.dbapi import DbapiCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import rank
from pysqlsuggestions.engine.request import derive_request
from pysqlsuggestions.resolve import resolve

DSN = 'postgresql://report:report@localhost:57432/{database}'
"""The docker fixture's Postgres. `docker/docker-compose.yml` offsets the port."""


@dataclass(frozen=True)
class Ladder:
    """One rung: a database of `tables` relations, each `columns` wide."""

    name: str
    tables: int
    columns: int
    indexes: int = 2


LADDER = (
    Ladder('bench_s', tables=100, columns=20, indexes=1),
    Ladder('bench_m', tables=1000, columns=25, indexes=2),
    Ladder('bench_l', tables=5000, columns=30, indexes=2),
)
"""
Three rungs rather than one, because the question is the shape of the cost.

A single large schema says a number; three say whether it grows with the catalog,
and that is the difference between a slow query and a design that will not hold.
The largest is about the size of a warehouse an editor plugin is actually pointed
at, and takes a couple of minutes to build.
"""

_WORDS = (
    'user',
    'order',
    'invoice',
    'payment',
    'report',
    'account',
    'customer',
    'product',
    'shipment',
    'ledger',
    'session',
    'event',
    'metric',
    'audit',
    'contract',
    'campaign',
    'channel',
    'device',
    'region',
    'vendor',
)

_COLUMNS = (
    'id',
    'created_at',
    'updated_at',
    'name',
    'email',
    'status',
    'amount',
    'currency',
    'user_id',
    'order_id',
    'external_ref',
    'description',
    'is_active',
    'deleted_at',
    'quantity',
    'unit_price',
    'tax_rate',
    'country_code',
    'postal_code',
    'phone',
    'notes',
    'meta',
    'version',
    'source',
    'checksum',
    'started_at',
    'finished_at',
    'retry_count',
    'priority',
    'category',
)

_TYPES = ('bigint', 'text', 'timestamptz', 'numeric(12,2)', 'boolean', 'integer', 'varchar(64)')


def table_name(index: int) -> str:
    """
    A relation name for `index`, stable across runs and unique within a ladder.

    Two words and the index. Real names share prefixes and word components —
    which is exactly what ranking scores on — so a generator emitting `t0 … t4999`
    would measure matching against a distribution no catalog has. The index is
    what keeps them unique once the vocabulary runs out.
    """
    first = _WORDS[index % len(_WORDS)]
    second = _WORDS[(index // len(_WORDS)) % len(_WORDS)]
    return f'{first}_{second}_{index}'


def column_name(index: int) -> str:
    """A column name for `index`, suffixed once the vocabulary is exhausted."""
    base = _COLUMNS[index % len(_COLUMNS)]
    return base if index < len(_COLUMNS) else f'{base}_{index}'


def column_type(table: int, column: int) -> str:
    """
    A type for one column, varied across the schema but fixed for key columns.

    Anything named `id` or `..._id` is `bigint` whatever else varies. The foreign
    key chain references `id`, and a generator that typed `user_id` by position
    produced `Key columns "user_id" and "id" are of incompatible types` partway
    through the build — leaving a half-built database that still answers queries,
    so the run reported real numbers against the wrong schema.
    """
    if column_name(column).endswith('id'):
        return 'bigint'
    return _TYPES[(table + column) % len(_TYPES)]


def ddl(spec: Ladder) -> Iterator[str]:
    """
    Every statement building `spec`, in order.

    A foreign key chain rather than a star, so `foreign_keys()` returns as many
    edges as there are relations and the join proposals have something to walk.
    """
    for index in range(spec.tables):
        name = table_name(index)
        body = ', '.join(f'{column_name(column)} {column_type(index, column)}' for column in range(spec.columns))
        yield f'CREATE TABLE {name} ({body}, PRIMARY KEY (id))'
        for which in range(spec.indexes):
            yield f'CREATE INDEX {name}_idx{which} ON {name} ({column_name(1 + which)})'
        if index:
            yield (
                f'ALTER TABLE {name} ADD CONSTRAINT {name}_fk '
                f'FOREIGN KEY (user_id) REFERENCES {table_name(index - 1)} (id)'
            )


def format_rows(title: str, rows: Sequence[tuple[str, float, str]]) -> str:
    """
    One labelled block of measurements, aligned so two runs can be read side by side.

    The alignment is the whole point of having this rather than an f-string per
    caller: these are compared by eye against a previous run, and a column that
    shifts by a character defeats that.
    """
    lines = [f'-- {title} --']
    width = max((len(label) for label, _, _ in rows), default=0)
    lines.extend(f'  {label:<{width}}  {millis:9.1f} ms   {detail}' for label, millis, detail in rows)
    return '\n'.join(lines)


def timed(call: Callable[[], Any], repeats: int = 5) -> tuple[float, Any]:
    """Median milliseconds over `repeats`, and the last result. Median, so one stall does not lead."""
    samples: list[float] = []
    result: Any = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = call()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples), result


class _CountingCursor:
    """
    A cursor that counts statements and can pretend the server is far away.

    Round trips are free against a local server and are the dominant cost against
    a real one, so the count is reported as its own number rather than left inside
    a total that a fast fixture flatters.
    """

    def __init__(self, inner: Any, log: list[str], delay: float) -> None:
        self._inner = inner
        self._log = log
        self._delay = delay

    def execute(self, operation: str, parameters: Any = None) -> Any:
        """Record, stall if asked, then run."""
        self._log.append(' '.join(operation.split())[:60])
        if self._delay:
            time.sleep(self._delay)
        return self._inner.execute(operation, parameters)

    def fetchall(self) -> Any:
        """Every remaining row."""
        return self._inner.fetchall()


def _carets(sample: str, wide: Sequence[str]) -> dict[str, str]:
    """The positions worth measuring, as SQL whose caret is at the end."""
    joins = ' '.join(f'JOIN {name} t{index} ON t{index}.id = t0.id' for index, name in enumerate(wide[1:], start=1))
    return {
        'FROM (relations)': 'SELECT * FROM ',
        'FROM prefix "ord"': 'SELECT * FROM ord',
        'SELECT (loose columns)': 'SELECT ',
        'SELECT prefix "user"': 'SELECT user',
        'JOIN (fk proposals)': f'SELECT * FROM {sample} o JOIN ',
        f'WHERE, {len(wide)} relations': f'SELECT * FROM {wide[0]} t0 {joins} WHERE ',
        'WHERE column = <value>': f'SELECT * FROM {sample} o WHERE o.status = ',
    }


def build(spec: Ladder, connect: Any) -> None:
    """Create `spec` from scratch, dropping any previous copy of it."""
    admin = connect(DSN.format(database='postgres'))
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f'DROP DATABASE IF EXISTS {spec.name} WITH (FORCE)')
        cursor.execute(f'CREATE DATABASE {spec.name}')
    admin.close()

    started = time.perf_counter()
    connection = connect(DSN.format(database=spec.name))
    with connection.cursor() as cursor:
        batch: list[str] = []
        for statement in ddl(spec):
            batch.append(statement)
            # Batched, because five thousand round trips to build the fixture is
            # its own wait; committed as it goes so a failure leaves something
            # inspectable rather than one enormous rollback.
            if len(batch) >= 400:
                cursor.execute('; '.join(batch))
                connection.commit()
                batch = []
        if batch:
            cursor.execute('; '.join(batch))
        connection.commit()
    # `reltuples` is what the relation list reports as a row count, and it is -1
    # until something has analysed the table.
    #
    # Outside a transaction, which is not a detail. A bare ANALYZE gives each
    # relation its own transaction and releases the lock as it goes; inside an
    # explicit one it has to hold all of them at once, and 20 000 relations is
    # far past `max_locks_per_transaction`. It failed as `out of shared memory`
    # after the minutes spent creating the tables, and only on the largest rung —
    # the two smaller ones fit inside the lock table and passed, so the benchmark
    # broke at exactly the size it exists to measure.
    connection.commit()
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute('ANALYZE')
    connection.close()
    elapsed = time.perf_counter() - started
    print(f'  {spec.name}: {spec.tables} tables x {spec.columns} columns x {spec.indexes} indexes in {elapsed:.1f}s')


def measure(spec: Ladder, connect: Any, paramstyle: str, rtt: float) -> None:
    """Report every layer for one rung of the ladder."""
    connection = connect(DSN.format(database=spec.name))
    log: list[str] = []
    delay = 0.0

    def cursor() -> _CountingCursor:
        return _CountingCursor(connection.cursor(), log, delay)

    catalog = DbapiCatalog(cursor, POSTGRES, paramstyle=paramstyle)

    print(f'\n{"=" * 78}\n  {spec.name}: {spec.tables} tables of {spec.columns} columns\n{"=" * 78}')
    relations = catalog.tables(None)
    queryable = [table for table in relations if table.kind not in ('index', 'sequence')]
    print(f'  {len(relations)} relations from tables(), {len(queryable)} of them queryable\n')
    sample = queryable[0].name
    wide = [table.name for table in queryable[:20]]

    reads: list[tuple[str, Callable[[], Any]]] = [
        ('tables(None)', lambda: catalog.tables(None)),
        ('columns(None, one)', lambda: catalog.columns(None, sample)),
        ('functions(None)', lambda: catalog.functions(None)),
        ('foreign_keys(None)', lambda: catalog.foreign_keys(None)),
        ("search_columns('u')", lambda: catalog.search_columns('u', 500)),
        ("search_columns('user')", lambda: catalog.search_columns('user', 500)),
        ("search_relations('o')", lambda: catalog.search_relations('o', 200)),
        ("search_relations('ord')", lambda: catalog.search_relations('ord', 200)),
    ]
    rows = []
    for label, call in reads:
        millis, value = timed(call)
        rows.append((label, millis, f'{len(value)} rows'))
    print(format_rows('1. one introspection query, no cache, no engine', rows))

    rows = []
    for label, value in (('tables(None)', relations), ('functions(None)', catalog.functions(None))):
        encode_ms, blob = timed(partial(encode, value))
        decode_ms, _ = timed(partial(decode, blob))
        rows.append((f'{label} encode', encode_ms, f'{len(blob) / 1024:.0f} KiB'))
        rows.append((f'{label} decode', decode_ms, 'per keystroke on a ByteCache'))
    print('\n' + format_rows('2. the cached payload, which redis pays for on every read', rows))

    carets = _carets(sample, wide)
    rows = []
    for label, sql in carets.items():
        log.clear()
        complete(sql, len(sql), POSTGRES, catalog)
        queries = len(log)
        millis, found = timed(partial(complete, sql, len(sql), POSTGRES, catalog), repeats=3)
        rows.append((label, millis, f'{queries} queries, {len(found)} suggestions'))
    print('\n' + format_rows('3. complete(), cold cache', rows))

    warm = MemoryCache(default_ttl=None, maxsize=None)
    rows = []
    for label, sql in carets.items():
        complete(sql, len(sql), POSTGRES, catalog, cache=warm)
        # Counted from one call of its own rather than divided out of the timed
        # run: dividing ties this number to `timed`'s repeat count, which is a
        # default someone will change without thinking about this line.
        log.clear()
        complete(sql, len(sql), POSTGRES, catalog, cache=warm)
        queries = len(log)
        millis, found = timed(partial(complete, sql, len(sql), POSTGRES, catalog, cache=warm))
        rows.append((label, millis, f'{queries} queries, {len(found)} suggestions'))
    print('\n' + format_rows('4. complete(), warm cache — what is left is Python', rows))

    rows = []
    for label, sql in carets.items():
        request = derive_request(sql, len(sql), POSTGRES)
        resolve_ms, candidates = timed(partial(resolve, request, catalog, POSTGRES, cache=warm, limit=200))
        rank_ms, _ = timed(partial(rank, candidates, request, POSTGRES, 40))
        rows.append(
            (
                label,
                resolve_ms + rank_ms,
                f'resolve {resolve_ms:.1f} + rank {rank_ms:.1f}, {len(candidates)} candidates',
            )
        )
    print('\n' + format_rows('5. the warm split', rows))

    if rtt:
        # One whole round trip per query, not half of one. A statement is a
        # request and a response, so what a distant server adds to each is the
        # full figure; halving it here reported `--rtt 20` while measuring 10 and
        # would not have reproduced the numbers this harness exists to defend.
        delay = rtt / 1000
        rows = []
        for label, sql in carets.items():
            millis, _ = timed(partial(complete, sql, len(sql), POSTGRES, catalog), repeats=3)
            rows.append((label, millis, f'at {rtt:.0f} ms round trip'))
        print('\n' + format_rows(f'6. complete(), cold, with {rtt:.0f} ms injected per query', rows))
        delay = 0.0

    connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Build the ladder, measure it, or both."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build', action='store_true', help='create the schemas first; takes a few minutes')
    parser.add_argument('--only', action='append', help='one rung by name, repeatable (default: all of them)')
    parser.add_argument('--rtt', type=float, default=0.0, help='also report the cold path at this round trip, in ms')
    arguments = parser.parse_args(argv)

    try:
        import psycopg2
    except ImportError:
        print('needs psycopg2: uv sync, or uv run --with psycopg2-binary python -m scripts.bench_catalog')
        return 1

    wanted = [rung for rung in LADDER if not arguments.only or rung.name in arguments.only]
    if not wanted:
        print(f'no such rung; the ladder is {", ".join(rung.name for rung in LADDER)}')
        return 1

    for rung in wanted:
        if arguments.build:
            build(rung, psycopg2.connect)
    for rung in wanted:
        measure(rung, psycopg2.connect, psycopg2.paramstyle, arguments.rtt)
    return 0


if __name__ == '__main__':
    sys.exit(main())
