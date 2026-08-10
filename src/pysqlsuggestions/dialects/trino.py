"""Trino."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import Clause, Namespace, Syntax
from pysqlsuggestions.types import Kind

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
)
