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
    columns=Query(
        sql="""
            SELECT table_schem, table_name, column_name, type_name, ordinal_position
            FROM system.jdbc.columns
            WHERE table_name = $2 AND ($1 = '' OR table_schem = $1)
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
