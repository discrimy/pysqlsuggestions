"""ClickHouse."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import Clause, Namespace, Syntax
from pysqlsuggestions.types import Kind

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
    ),
    namespace=Namespace(levels=('database', 'table')),
    clauses=ANSI.clauses.extend(
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
)
