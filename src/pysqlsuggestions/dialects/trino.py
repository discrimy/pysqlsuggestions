"""Trino."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import CatalogQueries, Clause, Namespace, Placeholder, Query, Syntax
from pysqlsuggestions.types import Column, Function, Kind, Table

# system.jdbc exposes metadata as ordinary queryable tables, so catalog and schema
# arrive as value parameters. The alternative — SHOW SCHEMAS FROM <catalog> — would
# need identifier interpolation, and interpolating identifiers from caret context
# is exactly the thing not to do.
QUERIES = CatalogQueries(
    # With three levels, "one level down from nothing" is a catalog, not a schema.
    # Returning schemas for an empty argument would offer the second level at the
    # first position — `FROM <caret>` would suggest `public` where `postgresql`
    # belongs.
    schemas=Query(
        sql="""
            SELECT table_cat AS name FROM system.jdbc.catalogs
            WHERE $1 = '' AND table_cat <> 'system'
            UNION ALL
            SELECT table_schem AS name FROM system.jdbc.schemas
            WHERE $1 <> '' AND table_catalog = $1
              AND table_schem NOT IN ('information_schema', 'jdbc', 'metadata', 'runtime')
            ORDER BY name
        """,
        row=lambda row: str(row[0]),
    ),
    # Deliberately empty for an unqualified position. With three levels there is
    # no useful "visible by default" set — a bare `FROM <caret>` in Trino wants
    # catalogs, which `schemas` supplies. Enumerating every table in every
    # catalog would also mean scanning each connector's metadata on a keystroke.
    tables=Query(
        sql="""
            SELECT table_schem, table_name, table_type FROM system.jdbc.tables
            WHERE $1 <> '' AND table_schem = $1
              AND table_schem NOT IN ('information_schema', 'jdbc', 'metadata', 'runtime')
            ORDER BY table_schem, table_name
        """,
        row=lambda row: Table(schema=str(row[0]), name=str(row[1]), kind=str(row[2]).lower()),
    ),
    # Two guarded conjuncts, and the shape is load-bearing.
    #
    # An unqualified name means the catalog this connection is bound to. Without
    # that filter the predicate was `($1 = '' OR table_schem = $1)`, and an empty
    # `$1` makes the OR vacuously true — so nothing constrained `table_cat` and
    # `system.jdbc.columns` answered from every connector at once. That was wrong
    # before it was slow: a postgresql-bound catalog returned ClickHouse's columns
    # for a relation Postgres does not have, so `FROM a_pg_table o WHERE o.<caret>`
    # could offer columns that are not in the relation being queried.
    #
    # The obvious repair is one disjunction —
    #
    #     ($1 = '' AND table_cat = current_catalog OR table_schem = $1)
    #
    # which is the shape ClickHouse's `columns` uses for the same question, is
    # correct, and is *no faster than having no filter at all*. Trino pushes
    # conjuncts down into a connector and cannot push a disjunction, so that form
    # scans every catalog and applies `table_cat` to the rows afterwards: 9.8s,
    # against 0.05s for the form below. It was the slowest read in the library.
    #
    # So each half is its own conjunct, guarded by the argument that selects it.
    # `$1` arrives bound rather than interpolated, which is what lets Trino fold
    # `$1 <> ''` to a constant and drop whichever guard is inert — leaving a bare
    # `table_cat = current_catalog` to push down when the name is unqualified, and
    # a bare `table_schem = $1` when it is not.
    #
    # `current_catalog` rather than a value threaded down from the connection: the
    # session already knows which catalog it is on, and it pushes down as well as a
    # literal. A parameter would have meant a marker every dialect's `columns` had
    # to accept and only this one would use.
    #
    # The catalog is deliberately *not* constrained once a schema is named.
    # Federating across catalogs is what Trino is for, and `_split_path` hands a
    # three-segment `clickhouse.analytics.report_executions` down as schema
    # `analytics`; constraining it there would empty every cross-catalog join.
    # `test_trino_federated_join_across_catalogs` is the test that says so.
    columns=Query(
        sql="""
            SELECT table_schem, table_name, column_name, type_name, ordinal_position
            FROM system.jdbc.columns
            WHERE table_name = $2
              AND ($1 <> '' OR table_cat = current_catalog)
              AND ($1 = '' OR table_schem = $1)
            ORDER BY ordinal_position
        """,
        row=lambda row: Column(
            schema=str(row[0]),
            table=str(row[1]),
            name=str(row[2]),
            type=str(row[3]),
            position=int(row[4]),
        ),
    ),
    functions=Query(
        # `SHOW FUNCTIONS` rather than a system table: `system.metadata` has no
        # functions relation, and `table_functions` — which does not exist on
        # 468 either — would have listed polymorphic table functions, not `abs`.
        # It takes no parameters, so the schema argument goes unused; Trino's
        # built-ins are not per-schema anyway.
        #
        # Columns are (name, return type, argument types, kind, deterministic,
        # description). An overloaded name appears once per signature and the
        # zero-argument overload spells its arguments `''`, which is exactly the
        # distinction `Function.takes_arguments` reads.
        sql='SHOW FUNCTIONS',
        row=lambda row: Function(
            schema=None,
            name=str(row[0]),
            args=str(row[2]),
            result=str(row[1]),
            # Column 3 is the kind, which was fetched and ignored. Trino spells
            # a plain function `scalar`; the other two spellings match ours.
            kind='function' if str(row[3]) == 'scalar' else str(row[3]),
        ),
    ),
)

RESERVED = ANSI_RESERVED | frozenset(
    """
    alter catalogs current_catalog current_path current_role current_schema current_user
    deallocate describe execute extract localtime localtimestamp normalize prepare recursive
    rollup schemas skip unnest
    """.split(),
)

TRINO = replace(
    ANSI,
    name='trino',
    syntax=Syntax(
        identifier_quotes=('"',),
        line_comments=('--',),
        nested_block_comments=False,
        string_escape_backslash=False,
        unquoted_case='lower',
        dollar_quoting=False,
        cast_operator='::',
        # Trino's own prepared-statement marker, and there is no `?` operator to lose.
        placeholders=(Placeholder(opens='?', body='none'),),
    ),
    namespace=Namespace(levels=('catalog', 'schema', 'table')),
    clauses=ANSI.clauses.extend(
        # Trino takes `NOT NULL` in a column definition and nothing else — NULL,
        # DEFAULT and PRIMARY KEY are all `mismatched input … Expecting: ')', ','`.
        # Restated rather than refined through a helper, which is safe only
        # because this clause deliberately carries no `followed_by`: the trap
        # `postgres.py`'s `_ansi` exists to close is a hand-copied continuation
        # list falling behind the canonical clause order, and there is none here.
        Clause(
            name='CREATE TABLE',
            suggests=(),
            before_the_item=('IF NOT EXISTS',),
            defines_columns=('NOT NULL',),
        ),
        # Like LATERAL, and unlike a join: `FROM t, UNNEST(a)` and
        # `CROSS JOIN UNNEST(a)` are right, `FROM t UNNEST(a)` is not.
        Clause(
            name='UNNEST',
            follows=frozenset({'FROM', 'JOIN'}),
            opens_an_item=True,
            suggests=(Kind.COLUMN, Kind.FUNCTION),
        ),
        Clause(name='MATCH_RECOGNIZE', follows=frozenset({'FROM'}), suggests=(Kind.COLUMN,)),
        Clause(name='TABLESAMPLE', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.KEYWORD,)),
    ),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    types=(
        'varchar',
        'bigint',
        'integer',
        'boolean',
        'double',
        'decimal',
        'date',
        'timestamp',
        'real',
        'smallint',
        'varbinary',
        'json',
        'uuid',
        'interval day to second',
    ),
    catalog_queries=QUERIES,
)
