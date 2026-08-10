"""PostgreSQL. Composed from ANSI; nothing here subclasses anything."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import CatalogQueries, Clause, Namespace, Query, Syntax
from pysqlsuggestions.types import Column, Function, Kind, Table

_RELKIND = {
    'r': 'table',
    'p': 'partitioned table',
    'v': 'view',
    'm': 'materialized view',
    'f': 'foreign table',
}

# `$1 = '' AND visible OR nspname = $1` reads as `($1='' AND visible) OR (nspname=$1)`,
# so an empty schema means "whatever is on the search path".
QUERIES = CatalogQueries(
    schemas=Query(
        sql="""
            SELECT nspname FROM pg_namespace
            WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema' AND $1 = $1
            ORDER BY nspname
        """,
        row=lambda row: str(row[0]),
    ),
    tables=Query(
        sql="""
            SELECT n.nspname, c.relname, c.relkind
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            ORDER BY n.nspname, c.relname
        """,
        row=lambda row: Table(schema=str(row[0]), name=str(row[1]), kind=_RELKIND.get(str(row[2]), 'table')),
    ),
    columns=Query(
        sql="""
            SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnum
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relname = $2
              AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            ORDER BY a.attnum
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
            SELECT n.nspname, p.proname,
                   pg_get_function_arguments(p.oid), pg_get_function_result(p.oid)
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE ($1 = '' AND n.nspname IN ('pg_catalog', 'public') OR n.nspname = $1)
            ORDER BY p.proname
            LIMIT 2000
        """,
        row=lambda row: Function(schema=str(row[0]), name=str(row[1]), args=str(row[2]), result=str(row[3])),
    ),
)

RESERVED = ANSI_RESERVED | frozenset(
    """
    analyse analyze asymmetric both collate current_role current_user deferrable freeze ilike
    initially isnull lateral leading localtime localtimestamp notnull placing returning
    session_user similar symmetric trailing variadic verbose
    """.split(),
)

POSTGRES = replace(
    ANSI,
    name='postgres',
    syntax=Syntax(
        identifier_quotes=('"',),
        line_comments=('--',),
        nested_block_comments=True,
        string_escape_backslash=False,
        unquoted_case='lower',
        dollar_quoting=True,
        cast_operator='::',
    ),
    namespace=Namespace(levels=('schema', 'table')),
    clauses=ANSI.clauses.extend(
        Clause(name='LATERAL', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.TABLE, Kind.FUNCTION)),
        Clause(name='DISTINCT ON', follows=frozenset({'SELECT'}), suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(name='ON CONFLICT', follows=frozenset({'INSERT INTO', 'VALUES'}), suggests=(Kind.COLUMN,)),
    ),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    catalog_queries=QUERIES,
)
