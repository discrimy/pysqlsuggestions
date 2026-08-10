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

_AFTER_RELATION = (
    'WHERE',
    'JOIN',
    'LEFT JOIN',
    'INNER JOIN',
    'CROSS JOIN',
    'GROUP BY',
    'ORDER BY',
    'LIMIT',
    'OFFSET',
    'UNION',
    'AS',
)
_AFTER_PREDICATE = ('AND', 'OR', 'NOT', 'IN', 'LIKE', 'BETWEEN', 'IS NULL', 'IS NOT NULL')
_COMPARISON = ('=', '<>', '<', '<=', '>', '>=')
"""Ordered by how often they are what you meant, not alphabetically."""

CLAUSES = ClauseModel(
    clauses=(
        Clause(name='WITH', suggests=()),
        # No KEYWORD here: a select list wants columns and functions, and burying
        # them under reserved words is the failure mode this engine exists to avoid.
        # AS/FROM/DISTINCT arrive through `followed_by`, once an item is written.
        Clause(name='SELECT', suggests=COLUMN_EXPRESSION, followed_by=('AS', 'FROM', 'DISTINCT')),
        Clause(name='FROM', follows=frozenset({'SELECT'}), suggests=RELATION_REFERENCE, followed_by=_AFTER_RELATION),
        Clause(
            name='DELETE FROM',
            suggests=RELATION_REFERENCE,
            followed_by=('WHERE', 'USING', 'RETURNING'),
        ),
        Clause(
            name='INSERT INTO',
            suggests=RELATION_REFERENCE,
            followed_by=('VALUES', 'SELECT', 'ON CONFLICT', 'RETURNING'),
        ),
        Clause(
            name='UPDATE',
            suggests=RELATION_REFERENCE,
            followed_by=('SET', 'FROM', 'WHERE', 'RETURNING'),
        ),
        Clause(
            name='JOIN',
            follows=frozenset({'FROM', 'JOIN'}),
            suggests=RELATION_REFERENCE,
            followed_by=('ON', 'USING', 'AS'),
        ),
        Clause(
            name='ON',
            follows=frozenset({'JOIN'}),
            suggests=COLUMN_EXPRESSION,
            followed_by=('AND', 'OR', 'JOIN', 'LEFT JOIN', 'WHERE', 'GROUP BY', 'ORDER BY'),
            operators=_COMPARISON,
        ),
        Clause(name='USING', follows=frozenset({'JOIN'}), suggests=(Kind.COLUMN,), followed_by=('WHERE', 'JOIN')),
        Clause(
            name='WHERE',
            suggests=COLUMN_EXPRESSION,
            followed_by=(*_AFTER_PREDICATE, 'GROUP BY', 'ORDER BY', 'LIMIT', 'OFFSET', 'RETURNING'),
            operators=_COMPARISON,
        ),
        Clause(
            name='GROUP BY',
            follows=frozenset({'FROM', 'WHERE'}),
            suggests=COLUMN_EXPRESSION,
            followed_by=('HAVING', 'ORDER BY', 'LIMIT'),
        ),
        Clause(
            name='HAVING',
            follows=frozenset({'GROUP BY'}),
            suggests=COLUMN_EXPRESSION,
            followed_by=('AND', 'OR', 'ORDER BY', 'LIMIT'),
            operators=_COMPARISON,
        ),
        Clause(name='WINDOW', suggests=COLUMN_EXPRESSION, followed_by=('ORDER BY', 'LIMIT')),
        Clause(
            name='ORDER BY',
            suggests=COLUMN_EXPRESSION,
            followed_by=('ASC', 'DESC', 'NULLS FIRST', 'NULLS LAST', 'LIMIT', 'OFFSET'),
        ),
        Clause(name='PARTITION BY', suggests=COLUMN_EXPRESSION, followed_by=('ORDER BY',)),
        Clause(name='LIMIT', suggests=(Kind.KEYWORD,), followed_by=('OFFSET',)),
        Clause(name='OFFSET', suggests=(Kind.KEYWORD,), followed_by=('LIMIT', 'FETCH')),
        Clause(name='FETCH', suggests=(Kind.KEYWORD,)),
        Clause(
            name='SET',
            follows=frozenset({'UPDATE'}),
            suggests=(Kind.COLUMN,),
            followed_by=('WHERE', 'FROM', 'RETURNING'),
            operators=('=',),
        ),
        Clause(name='VALUES', suggests=COLUMN_EXPRESSION, followed_by=('RETURNING', 'ON CONFLICT')),
        Clause(name='RETURNING', suggests=COLUMN_EXPRESSION),
        Clause(name='UNION', suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
        Clause(name='INTERSECT', suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
        Clause(name='EXCEPT', suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
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
