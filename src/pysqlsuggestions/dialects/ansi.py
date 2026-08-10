"""The ANSI baseline. An unknown backend degrades to this rather than failing."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Clause, ClauseModel, Dialect, Namespace, Syntax
from pysqlsuggestions.types import Kind

RESERVED = frozenset(
    """
    all and any array as asc between by case cast check column constraint create cross
    current_date current_time current_timestamp default desc distinct do else end except
    exists false for foreign from full grant group having in inner intersect into is join
    left like limit natural not null offset on only or order outer primary references right
    select some table then to true union unique user using values when where window with
    """.split(),
)

COLUMN_EXPRESSION = (Kind.COLUMN, Kind.FUNCTION)
RELATION_REFERENCE = (Kind.TABLE, Kind.SCHEMA)

CLAUSES = ClauseModel(
    clauses=(
        Clause(name='WITH', suggests=()),
        Clause(name='SELECT', suggests=(Kind.COLUMN, Kind.FUNCTION, Kind.KEYWORD)),
        Clause(name='FROM', follows=frozenset({'SELECT'}), suggests=RELATION_REFERENCE),
        Clause(name='DELETE FROM', suggests=RELATION_REFERENCE),
        Clause(name='INSERT INTO', suggests=RELATION_REFERENCE),
        Clause(name='UPDATE', suggests=RELATION_REFERENCE),
        Clause(name='JOIN', follows=frozenset({'FROM', 'JOIN'}), suggests=RELATION_REFERENCE),
        Clause(name='ON', follows=frozenset({'JOIN'}), suggests=COLUMN_EXPRESSION),
        Clause(name='USING', follows=frozenset({'JOIN'}), suggests=(Kind.COLUMN,)),
        Clause(name='WHERE', suggests=COLUMN_EXPRESSION),
        Clause(name='GROUP BY', follows=frozenset({'FROM', 'WHERE'}), suggests=COLUMN_EXPRESSION),
        Clause(name='HAVING', follows=frozenset({'GROUP BY'}), suggests=COLUMN_EXPRESSION),
        Clause(name='WINDOW', suggests=COLUMN_EXPRESSION),
        Clause(name='ORDER BY', suggests=COLUMN_EXPRESSION),
        Clause(name='PARTITION BY', suggests=COLUMN_EXPRESSION),
        Clause(name='LIMIT', suggests=(Kind.KEYWORD,)),
        Clause(name='OFFSET', suggests=(Kind.KEYWORD,)),
        Clause(name='FETCH', suggests=(Kind.KEYWORD,)),
        Clause(name='SET', follows=frozenset({'UPDATE'}), suggests=(Kind.COLUMN,)),
        Clause(name='VALUES', suggests=COLUMN_EXPRESSION),
        Clause(name='RETURNING', suggests=COLUMN_EXPRESSION),
        Clause(name='UNION', suggests=(Kind.KEYWORD,)),
        Clause(name='INTERSECT', suggests=(Kind.KEYWORD,)),
        Clause(name='EXCEPT', suggests=(Kind.KEYWORD,)),
    ),
)

ANSI = Dialect(
    name='ansi',
    syntax=Syntax(),
    namespace=Namespace(levels=('schema', 'table')),
    clauses=CLAUSES,
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
)
