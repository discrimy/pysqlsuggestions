"""The ANSI baseline. An unknown backend degrades to this rather than failing."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import ClauseModel, Dialect, Namespace, Syntax

RESERVED = frozenset(
    """
    all and any array as asc between by case cast check column constraint create cross
    current_date current_time current_timestamp default desc distinct do else end except
    exists false for foreign from full grant group having in inner intersect into is join
    left like limit natural not null offset on only or order outer primary references right
    select some table then to true union unique user using values when where window with
    """.split(),
)

ANSI = Dialect(
    name='ansi',
    syntax=Syntax(),
    namespace=Namespace(levels=('schema', 'table')),
    clauses=ClauseModel(),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
)
