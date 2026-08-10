"""PostgreSQL. Composed from ANSI; nothing here subclasses anything."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import Clause, Namespace, Syntax
from pysqlsuggestions.types import Kind

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
)
