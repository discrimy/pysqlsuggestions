"""ClickHouse."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import CatalogQueries, Clause, Namespace, Placeholder, Query, Syntax
from pysqlsuggestions.types import Column, Function, Kind, Table

_INTERNAL = "('system', 'INFORMATION_SCHEMA', 'information_schema')"

QUERIES = CatalogQueries(
    schemas=Query(
        sql=f"""
            SELECT name FROM system.databases
            WHERE name NOT IN {_INTERNAL} AND $1 = $1
            ORDER BY name
        """,
        row=lambda row: str(row[0]),
    ),
    tables=Query(
        sql=f"""
            SELECT database, name, engine, total_rows FROM system.tables
            WHERE ($1 = '' AND database = currentDatabase() OR database = $1)
              AND database NOT IN {_INTERNAL}
            ORDER BY database, name
        """,
        row=lambda row: Table(
            schema=str(row[0]),
            name=str(row[1]),
            kind=str(row[2]).lower(),
            # Null for engines that cannot say — a View, a Merge, most integrations.
            rows=int(row[3]) if row[3] is not None else None,
        ),
    ),
    columns=Query(
        sql="""
            SELECT database, table, name, type, position FROM system.columns
            WHERE table = $2 AND ($1 = '' AND database = currentDatabase() OR database = $1)
            ORDER BY position
        """,
        row=lambda row: Column(
            schema=str(row[0]),
            table=str(row[1]),
            name=str(row[2]),
            type=str(row[3]),
            position=int(row[4]),
        ),
    ),
    # ClickHouse exposes thousands of functions and no signatures, so they are
    # introspected rather than shipped, and the detail column stays empty.
    column_search=Query(
        sql=f"""
            SELECT database, table, name, type, position FROM system.columns
            WHERE database NOT IN {_INTERNAL}
              AND position(lower(name), lower($1)) > 0
            ORDER BY position(lower(name), lower($1)), length(name), database, table, name
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
    # `position` here takes (haystack, needle), the opposite of Postgres's
    # `position(needle in haystack)`. Same shape of query, mirrored arguments.
    relation_search=Query(
        sql=f"""
            SELECT database, name, engine, total_rows FROM system.tables
            WHERE database NOT IN {_INTERNAL}
              AND position(lower(name), lower($1)) > 0
            ORDER BY position(lower(name), lower($1)), length(name), database, name
            LIMIT 200
        """,
        row=lambda row: Table(
            schema=str(row[0]),
            name=str(row[1]),
            kind=str(row[2]).lower(),
            rows=int(row[3]) if row[3] is not None else None,
        ),
    ),
    functions=Query(
        sql="""
            SELECT name, is_aggregate FROM system.functions
            WHERE $1 = $1
            ORDER BY name
            LIMIT 2000
        """,
        # args is None, not '': system.functions carries no signatures, and an
        # empty string would claim these take no arguments, which would put the
        # caret after `count()` instead of inside it. `result` is None for the
        # same reason — there is no return type to report, and the word
        # `aggregate` used to sit in that field for want of anywhere else.
        row=lambda row: Function(
            schema=None,
            name=str(row[0]),
            args=None,
            result=None,
            kind='aggregate' if row[1] else 'function',
        ),
    ),
)

RESERVED = ANSI_RESERVED | frozenset(
    """
    anti asof cluster final format global prewhere sample semi settings ttl
    """.split(),
)

CLICKHOUSE = replace(
    ANSI,
    name='clickhouse',
    syntax=Syntax(
        identifier_quotes=('"', '`'),
        line_comments=('--', '#'),
        nested_block_comments=False,
        string_escape_backslash=True,
        unquoted_case='preserve',
        dollar_quoting=False,
        cast_operator='::',
        # ClickHouse spells a parameter `{name:Type}`. The interior is `any`
        # rather than a name because the type is part of the token.
        placeholders=(Placeholder(opens='{', body='any', closes='}'),),
    ),
    namespace=Namespace(levels=('database', 'table')),
    # ClickHouse has no CALL. Its parser answers `CALL foo()` with a syntax
    # error whose message lists every form it does accept, and none of them is
    # this one. Both the clause and the statement start have to go: the
    # conformance corpus reports a statement start whose clause is missing, so
    # doing only one of the two fails the suite.
    statement_start=tuple(phrase for phrase in ANSI.statement_start if phrase != 'CALL'),
    clauses=ANSI.clauses.without('CALL').extend(
        Clause(
            name='PREWHERE',
            follows=frozenset({'FROM', 'SAMPLE', 'FINAL'}),
            suggests=(Kind.COLUMN, Kind.FUNCTION),
        ),
        Clause(name='FINAL', follows=frozenset({'FROM'}), suggests=()),
        Clause(name='SAMPLE', follows=frozenset({'FROM', 'FINAL'}), suggests=(Kind.KEYWORD,)),
        Clause(
            name='ARRAY JOIN',
            follows=frozenset({'FROM', 'PREWHERE'}),
            suggests=(Kind.COLUMN, Kind.FUNCTION),
        ),
        Clause(
            name='LIMIT BY',
            follows=frozenset({'ORDER BY', 'LIMIT'}),
            suggests=(Kind.COLUMN, Kind.FUNCTION),
        ),
        Clause(name='SETTINGS', suggests=(Kind.KEYWORD,)),
    ),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    types=(
        'String',
        'UInt64',
        'Int64',
        'UInt32',
        'Int32',
        'Float64',
        'Date',
        'DateTime',
        'DateTime64',
        'Decimal',
        'Bool',
        'UUID',
        'IPv4',
        'IPv6',
    ),
    catalog_queries=QUERIES,
)
