"""Trino."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import Namespace, Syntax

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
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
)
