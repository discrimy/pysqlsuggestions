"""PostgreSQL. Composed from ANSI; nothing here subclasses anything."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI, COLUMN_EXPRESSION, EXPLAINABLE
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import CatalogQueries, Clause, Namespace, Placeholder, Query, Syntax
from pysqlsuggestions.types import Column, ColumnValue, ForeignKey, Function, Kind, Table

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
            SELECT n.nspname, c.relname, c.relkind, c.reltuples
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
        row=lambda row: Table(
            schema=str(row[0]),
            name=str(row[1]),
            kind=_RELKIND.get(str(row[2]), 'table'),
            # -1 is "never analysed", which is not the same as empty.
            rows=int(row[3]) if row[3] is not None and float(row[3]) >= 0 else None,
        ),
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
    # Two sources, exhaustive first. An enum type lists every value it permits,
    # which no statistic can improve on; `format_type` reports only the type's
    # name, so the labels are a read of their own. Failing that, planner
    # statistics: `most_common_vals` is an anyarray ordered by frequency, so
    # WITH ORDINALITY is what carries that order through the unnest. Neither
    # touches the table, and pg_stats already restricts itself to what the
    # connected role may read.
    #
    # `source` picks one of them whole rather than ranking a mixture. An analysed
    # enum column answers both branches with the same labels, and merely ordering
    # by source leaves each value in the list twice — once named, once measured.
    values=Query(
        sql="""
            WITH candidates AS (
                SELECT e.enumlabel::text AS value, NULL::float8 AS freq, 0 AS source, e.enumsortorder::float8 AS ord
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_enum e ON e.enumtypid = a.atttypid
                WHERE c.relname = $2 AND a.attname = $3 AND a.attnum > 0 AND NOT a.attisdropped
                  AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
                UNION ALL
                SELECT v.value, v.freq, 1 AS source, v.ord::float8
                FROM pg_stats s
                JOIN pg_class c ON c.relname = s.tablename
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = s.schemaname
                -- Two arrays unnested together, so each value keeps its own share.
                CROSS JOIN LATERAL unnest(s.most_common_vals::text::text[], s.most_common_freqs)
                    WITH ORDINALITY AS v(value, freq, ord)
                WHERE s.tablename = $2 AND s.attname = $3
                  AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            )
            SELECT q.value, q.freq FROM candidates q
            WHERE q.source = (SELECT min(source) FROM candidates)
            ORDER BY q.ord
            LIMIT 50
        """,
        row=lambda row: ColumnValue(text=str(row[0]), frequency=float(row[1]) if row[1] is not None else None),
    ),
    # `position(... in ...)` rather than LIKE: a prefix comes from what the user
    # typed, and `_` is both a LIKE wildcard and the commonest character in a
    # column name — `user_` would match `usera`. Substring rather than prefix
    # because `mail` finding `email` is behaviour the suite this library
    # inherits already pins, and the ordering puts a true prefix first anyway.
    column_search=Query(
        sql="""
            SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnum
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
              AND position(lower($1) in lower(a.attname)) > 0
            ORDER BY position(lower($1) in lower(a.attname)), length(a.attname), n.nspname, c.relname, a.attname
            LIMIT 500
        """,
        row=lambda row: Column(
            schema=str(row[0]),
            table=str(row[1]),
            name=str(row[2]),
            type=str(row[3]),
            position=int(row[4]),
        ),
    ),
    # No `pg_table_is_visible` here: reaching past the search path is the whole
    # point. The system-schema exclusion stays, because `pg_%` is not what
    # anybody means by `FROM ord`. ORDER BY before LIMIT is the port's contract
    # — the truncation happens before ranking sees the rows.
    relation_search=Query(
        sql="""
            SELECT n.nspname, c.relname, c.relkind, c.reltuples
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
              AND position(lower($1) in lower(c.relname)) > 0
            ORDER BY position(lower($1) in lower(c.relname)), length(c.relname), n.nspname, c.relname
            LIMIT 200
        """,
        row=lambda row: Table(
            schema=str(row[0]),
            name=str(row[1]),
            kind=_RELKIND.get(str(row[2]), 'table'),
            # -1 is "never analysed", which is not the same as empty.
            rows=int(row[3]) if row[3] is not None and float(row[3]) >= 0 else None,
        ),
    ),
    foreign_keys=Query(
        sql="""
            SELECT n.nspname,
                   c.relname,
                   (SELECT array_agg(a.attname ORDER BY k.ord)
                      FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum),
                   rn.nspname,
                   rc.relname,
                   (SELECT array_agg(a.attname ORDER BY k.ord)
                      FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum)
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_class rc ON rc.oid = con.confrelid
            JOIN pg_namespace rn ON rn.oid = rc.relnamespace
            WHERE con.contype = 'f'
              AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            ORDER BY n.nspname, c.relname, con.conname
        """,
        # WITH ORDINALITY rather than a bare unnest: conkey and confkey correspond
        # position by position, and that correspondence is the whole content of a
        # composite key. Aggregating either side in an unspecified order would
        # produce an edge that looks right and joins the wrong columns together.
        row=lambda row: ForeignKey(
            schema=str(row[0]),
            table=str(row[1]),
            columns=tuple(str(name) for name in row[2]),
            ref_schema=str(row[3]),
            ref_table=str(row[4]),
            ref_columns=tuple(str(name) for name in row[5]),
        ),
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
        # No `?`. It is the JSONB existence operator, and `data ? 'key'` is a
        # predicate people write — reading it as a parameter would silence a
        # position that has a real answer.
        placeholders=(Placeholder(opens='$', body='digits'), Placeholder(opens=':')),
    ),
    namespace=Namespace(levels=('schema', 'table')),
    clauses=ANSI.clauses.extend(
        Clause(
            name='LATERAL',
            follows=frozenset({'FROM', 'JOIN'}),
            opens_an_item=True,
            suggests=(Kind.TABLE, Kind.FUNCTION),
        ),
        Clause(name='DISTINCT ON', follows=frozenset({'SELECT'}), suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(
            name='ON CONFLICT',
            follows=frozenset({'VALUES'}),
            statements=frozenset({'INSERT INTO'}),
            suggests=(Kind.COLUMN,),
        ),
        # Not in the ANSI base: ClickHouse and Trino have no RETURNING at all,
        # and it is a syntax error in a SELECT even here. Declaring where it may
        # follow is the whole of what this dialect has to say about it.
        Clause(
            name='RETURNING',
            follows=frozenset({'DELETE FROM', 'SET', 'WHERE', 'VALUES', 'ON CONFLICT'}),
            statements=frozenset({'DELETE FROM', 'INSERT INTO', 'UPDATE'}),
            suggests=COLUMN_EXPRESSION,
        ),
        # ANALYZE and VERBOSE stand between EXPLAIN and its statement, which is
        # what `before_the_item` means. `followed_by` would offer them after the
        # statement, where they cannot go.
        #
        # `extend` replaces a clause of the same name rather than merging into
        # it, so ANSI's `followed_by` has to be restated here.
        Clause(
            name='EXPLAIN',
            suggests=(Kind.SNIPPET, Kind.KEYWORD),
            followed_by=EXPLAINABLE,
            before_the_item=('ANALYZE', 'VERBOSE'),
        ),
    ),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    types=TYPES,
    catalog_queries=QUERIES,
)
