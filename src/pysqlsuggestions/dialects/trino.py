"""Trino."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import CatalogQueries, Clause, Namespace, Query, Syntax
from pysqlsuggestions.types import Column, Function, Kind, Table

# system.jdbc exposes metadata as ordinary queryable tables, so catalog and schema
# arrive as value parameters. The alternative — SHOW SCHEMAS FROM <catalog> — would
# need identifier interpolation, and interpolating identifiers from caret context
# is exactly the thing not to do.
QUERIES = CatalogQueries(
    schemas=Query(
        sql="""
            SELECT table_schem FROM system.jdbc.schemas
            WHERE ($1 = '' OR table_catalog = $1)
              AND table_schem NOT IN ('information_schema', 'jdbc', 'metadata', 'runtime')
            ORDER BY table_schem
        """,
        row=lambda row: str(row[0]),
    ),
    tables=Query(
        sql="""
            SELECT table_schem, table_name, table_type FROM system.jdbc.tables
            WHERE ($1 = '' OR table_schem = $1)
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
        sql="""
            SELECT function_name, argument_types, return_type FROM system.metadata.table_functions
            WHERE $1 = $1
            ORDER BY function_name
            LIMIT 2000
        """,
        row=lambda row: Function(schema=None, name=str(row[0]), args=str(row[1]), result=str(row[2])),
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
    ),
    namespace=Namespace(levels=('catalog', 'schema', 'table')),
    clauses=ANSI.clauses.extend(
        Clause(name='UNNEST', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(name='MATCH_RECOGNIZE', follows=frozenset({'FROM'}), suggests=(Kind.COLUMN,)),
        Clause(name='TABLESAMPLE', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.KEYWORD,)),
    ),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    catalog_queries=QUERIES,
)
