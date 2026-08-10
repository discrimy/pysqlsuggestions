"""ClickHouse."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import Namespace, Syntax

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
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
)
