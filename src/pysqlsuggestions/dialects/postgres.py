"""PostgreSQL. Composed from ANSI; nothing here subclasses anything."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI, COLUMN_EXPRESSION
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
              -- pg_table_is_visible is true for pg_catalog, so an unqualified
              -- position would otherwise open with pg_aggregate. Naming a system
              -- schema explicitly still works.
              AND ($1 <> '' OR n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema')
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
    # pg_proc holds ~3300 entries, most of them type I/O plumbing that cannot be
    # called from SQL — `anynonarray_in`, `RI_FKey_noaction_del`. Excluding
    # anything that takes or returns internal/cstring, and anything returning a
    # handler type, drops those without touching count, now, array_agg or
    # string_agg. The limit is a safety valve, not a filter: set low it silently
    # truncates the alphabet, which is how `now` went missing.
    functions=Query(
        sql="""
            SELECT n.nspname, p.proname,
                   pg_get_function_arguments(p.oid), pg_get_function_result(p.oid)
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE ($1 = '' AND n.nspname IN ('pg_catalog', 'public') OR n.nspname = $1)
              AND p.prokind IN ('f', 'a', 'w')
              AND p.prorettype NOT IN (
                  'internal'::regtype, 'cstring'::regtype, 'trigger'::regtype,
                  'language_handler'::regtype, 'fdw_handler'::regtype,
                  'tsm_handler'::regtype, 'index_am_handler'::regtype, 'event_trigger'::regtype
              )
              AND NOT EXISTS (
                  SELECT 1 FROM unnest(p.proargtypes) a(oid)
                  WHERE a.oid IN ('internal'::regtype, 'cstring'::regtype)
              )
            ORDER BY p.proname
            LIMIT 10000
        """,
        row=lambda row: Function(schema=str(row[0]), name=str(row[1]), args=str(row[2]), result=str(row[3])),
    ),
    # Planner statistics, not a table read. `most_common_vals` is an anyarray
    # ordered by frequency, so WITH ORDINALITY is what keeps that order through
    # the unnest. pg_stats already restricts itself to what the connected role
    # may read, which is the privilege check this would otherwise have to make.
    values=Query(
        sql="""
            SELECT v.value
            FROM pg_stats s
            JOIN pg_class c ON c.relname = s.tablename
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = s.schemaname
            CROSS JOIN LATERAL unnest(s.most_common_vals::text::text[]) WITH ORDINALITY AS v(value, ord)
            WHERE s.tablename = $2 AND s.attname = $3
              AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            ORDER BY v.ord
            LIMIT 50
        """,
        row=lambda row: str(row[0]),
    ),
)

RESERVED = ANSI_RESERVED | frozenset(
    """
    analyse analyze asymmetric both collate current_role current_user deferrable freeze ilike
    initially isnull lateral leading localtime localtimestamp notnull placing returning
    session_user similar symmetric trailing variadic verbose
    """.split(),
)

TYPES = (
    'text',
    'integer',
    'bigint',
    'boolean',
    'numeric',
    'date',
    'timestamptz',
    'timestamp',
    'interval',
    'jsonb',
    'json',
    'uuid',
    'smallint',
    'real',
    'double precision',
    'character varying',
    'bytea',
    'inet',
    'time',
    'money',
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
        escape_string_prefix='E',
        unquoted_extra='$',
        unquoted_non_ascii=True,
    ),
    namespace=Namespace(levels=('schema', 'table')),
    clauses=ANSI.clauses.extend(
        Clause(name='LATERAL', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.TABLE, Kind.FUNCTION)),
        Clause(name='DISTINCT ON', follows=frozenset({'SELECT'}), suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(
            name='ON CONFLICT',
            follows=frozenset({'INSERT INTO', 'VALUES'}),
            statements=frozenset({'INSERT INTO'}),
            suggests=(Kind.COLUMN,),
        ),
        # Not in the ANSI base: ClickHouse and Trino have no RETURNING at all,
        # and it is a syntax error in a SELECT even here. Declaring where it may
        # follow is the whole of what this dialect has to say about it.
        Clause(
            name='RETURNING',
            follows=frozenset({'DELETE FROM', 'INSERT INTO', 'UPDATE', 'SET', 'WHERE', 'VALUES', 'ON CONFLICT'}),
            statements=frozenset({'DELETE FROM', 'INSERT INTO', 'UPDATE'}),
            suggests=COLUMN_EXPRESSION,
        ),
    ),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    types=TYPES,
    catalog_queries=QUERIES,
)
