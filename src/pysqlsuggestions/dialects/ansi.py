"""The ANSI baseline. An unknown backend degrades to this rather than failing."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Clause, ClauseModel, Dialect, Namespace, Syntax, Template
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

_COMPARISON = ('=', '<>', '<', '<=', '>', '>=')
"""Ordered by how often they are what you meant, not alphabetically."""

_CONTINUES_PREDICATE = ('IS NULL', 'IS NOT NULL', 'IN', 'NOT IN', 'LIKE', 'NOT LIKE', 'ILIKE', 'BETWEEN')
"""What can follow an operand that has no comparison yet."""

_ORDER = (
    'FROM',
    'WHERE',
    'GROUP BY',
    'HAVING',
    'WINDOW',
    'UNION',
    'INTERSECT',
    'EXCEPT',
    'ORDER BY',
    'LIMIT',
    'OFFSET',
    'FETCH',
)
"""
The canonical clause sequence of a SELECT.

A set operation binds tighter than ORDER BY and LIMIT, which belong to the whole
result: `SELECT ... ORDER BY id UNION SELECT ...` does not parse, so UNION comes
before them here and nothing offers it afterwards.

Each clause's continuations are derived from this rather than listed by hand.
Curating a dozen independent lists is how `ON` ended up offering ORDER BY but
not HAVING, LIMIT or OFFSET: every one of them was a separate chance to forget.
"""

_JOINS = ('JOIN', 'LEFT JOIN', 'INNER JOIN', 'CROSS JOIN')
"""A join may follow another join's ON, so these are added back where the order alone would not."""


def _onwards(name: str) -> tuple[str, ...]:
    """Every clause that may follow, in canonical order, starting at `name`."""
    return _ORDER[_ORDER.index(name) :]


_AFTER_RELATION = ('AS', *_JOINS, *_onwards('WHERE'))

CLAUSES = ClauseModel(
    clauses=(
        Clause(name='WITH', suggests=()),
        # No KEYWORD here: a select list wants columns and functions, and burying
        # them under reserved words is the failure mode this engine exists to avoid.
        # AS/FROM/DISTINCT arrive through `followed_by`, once an item is written.
        Clause(
            name='SELECT',
            suggests=COLUMN_EXPRESSION,
            followed_by=('AS', 'DISTINCT', *_onwards('FROM')),
            aliases_with='AS',
        ),
        Clause(
            name='FROM',
            follows=frozenset({'SELECT'}),
            suggests=RELATION_REFERENCE,
            followed_by=_AFTER_RELATION,
            aliases_with='AS',
        ),
        Clause(
            name='DELETE FROM',
            suggests=RELATION_REFERENCE,
            followed_by=('WHERE', 'USING'),
        ),
        Clause(
            name='INSERT INTO',
            suggests=RELATION_REFERENCE,
            followed_by=('VALUES', 'SELECT'),
        ),
        Clause(
            name='UPDATE',
            suggests=RELATION_REFERENCE,
            followed_by=('SET', 'FROM', 'WHERE'),
        ),
        Clause(
            name='JOIN',
            follows=frozenset({'FROM', 'JOIN'}),
            repeats=True,
            suggests=RELATION_REFERENCE,
            followed_by=('AS', 'ON', 'USING'),
            aliases_with='AS',
        ),
        Clause(
            name='ON',
            follows=frozenset({'JOIN'}),
            repeats=True,
            suggests=COLUMN_EXPRESSION,
            operators=_COMPARISON,
            after_operand=_CONTINUES_PREDICATE,
            followed_by=('AND', 'OR', *_JOINS, *_onwards('WHERE')),
        ),
        Clause(
            name='USING',
            follows=frozenset({'JOIN'}),
            repeats=True,
            suggests=(Kind.COLUMN,),
            followed_by=(*_JOINS, *_onwards('WHERE')),
        ),
        Clause(
            name='WHERE',
            suggests=COLUMN_EXPRESSION,
            operators=_COMPARISON,
            after_operand=_CONTINUES_PREDICATE,
            followed_by=('AND', 'OR', *_onwards('GROUP BY')),
        ),
        Clause(
            name='GROUP BY',
            follows=frozenset({'FROM', 'WHERE'}),
            suggests=COLUMN_EXPRESSION,
            followed_by=_onwards('HAVING'),
        ),
        Clause(
            name='HAVING',
            follows=frozenset({'GROUP BY'}),
            suggests=COLUMN_EXPRESSION,
            operators=_COMPARISON,
            after_operand=_CONTINUES_PREDICATE,
            followed_by=('AND', 'OR', *_onwards('WINDOW')),
        ),
        Clause(name='WINDOW', suggests=COLUMN_EXPRESSION, followed_by=_onwards('UNION')),
        Clause(
            name='ORDER BY',
            suggests=COLUMN_EXPRESSION,
            followed_by=('ASC', 'DESC', 'NULLS FIRST', 'NULLS LAST', *_onwards('LIMIT')),
        ),
        # A window spec, not a statement: what follows is the frame, never LIMIT.
        Clause(name='PARTITION BY', suggests=COLUMN_EXPRESSION, followed_by=('ORDER BY', 'ROWS', 'RANGE')),
        Clause(name='LIMIT', suggests=(Kind.KEYWORD,), followed_by=_onwards('OFFSET')),
        Clause(name='OFFSET', suggests=(Kind.KEYWORD,), followed_by=_onwards('FETCH')),
        Clause(name='FETCH', suggests=(Kind.KEYWORD,)),
        Clause(
            name='SET',
            follows=frozenset({'UPDATE'}),
            statements=frozenset({'UPDATE'}),
            suggests=(Kind.COLUMN,),
            followed_by=('WHERE', 'FROM'),
            operators=('=',),
        ),
        Clause(name='VALUES', statements=frozenset({'INSERT INTO'}), suggests=COLUMN_EXPRESSION),
        Clause(name='UNION', suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
        Clause(name='INTERSECT', suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
        Clause(name='EXCEPT', suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
    ),
)

STATEMENT_START = ('SELECT', 'WITH', 'INSERT INTO', 'UPDATE', 'DELETE FROM')

TYPES = (
    'varchar',
    'char',
    'integer',
    'bigint',
    'smallint',
    'decimal',
    'numeric',
    'real',
    'double precision',
    'boolean',
    'date',
    'time',
    'timestamp',
    'interval',
)
"""
The standard's own type names, which every dialect here also accepts.

Needed rather than optional: `CAST(x AS <caret>)` is the only cast strict ANSI
has, so an empty list makes that position a dead end.
"""

TEMPLATES = (
    Template(
        label='SELECT … FROM … AS …',
        # Numbered against the order they can be answered, not the order they
        # are written. The relation comes first because nothing can suggest a
        # column until it knows the table, and the alias second because the
        # generated one is derived from that relation's name. The select list is
        # last, by which point both are in scope.
        #
        # `$0` is where filling the last blank leaves the caret. Without it the
        # caret stays in the select list — the blank that happens to be answered
        # last is in the middle of the statement, so finishing the template would
        # otherwise strand the caret there with the query already complete.
        snippet='SELECT $3 FROM $1 AS $2$0',
        detail='a whole query: relation first, then its alias, then the columns',
    ),
)

ANSI = Dialect(
    name='ansi',
    syntax=Syntax(),
    namespace=Namespace(levels=('schema', 'table')),
    clauses=CLAUSES,
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    statement_start=STATEMENT_START,
    templates=TEMPLATES,
    types=TYPES,
)
