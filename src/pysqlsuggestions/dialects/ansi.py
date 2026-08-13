"""The ANSI baseline. An unknown backend degrades to this rather than failing."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Clause, ClauseModel, Dialect, Namespace, Placeholder, Syntax, Template
from pysqlsuggestions.types import Kind

RESERVED = frozenset(
    """
    all and any array as asc between by case cast check column constraint create cross
    current_date current_time current_timestamp default desc distinct do else end except
    exists false for foreign from full grant group having in inner intersect into is join
    left like limit natural not null offset on only or order outer primary recursive references right
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

_JOINS = ('JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN', 'CROSS JOIN')
"""
A join may follow another join's ON, so these are added back where the order alone would not.

Ordered by how often each is what you meant. None of them is a `Clause` of its
own: `clause_at` matches the longest clause *name*, and `JOIN` is a name, so
`LEFT JOIN orders ⌶` resolves through `JOIN` with the modifier riding along.
Naming each spelling as its own clause would say nothing the shared one does not.

The `OUTER` spellings are deliberately absent. `LEFT OUTER JOIN` means what
`LEFT JOIN` means, the shorter is what people write, and offering both doubles a
list whose whole value is being short enough to read. `NATURAL` is absent for
the opposite reason: it changes the meaning, choosing the join columns by name,
which is the inference `engine/joins.py` refuses at length.

All three backends accept `FULL OUTER JOIN`, verified against the containers.
"""

EXPLAINABLE = ('SELECT', 'WITH', 'INSERT INTO', 'UPDATE', 'DELETE FROM')
"""
The statement forms a query planner will accept.

Named separately from `STATEMENT_START` because `EXPLAIN` takes these and not
the DDL forms — `EXPLAIN DROP TABLE users` is a syntax error. Written this way
round, adding a statement form later cannot silently start offering it after
`EXPLAIN`.

Declared above the clause model rather than beside `STATEMENT_START`, because
`EXPLAIN` names it and a clause cannot reference what is defined below it.
"""


def _onwards(name: str) -> tuple[str, ...]:
    """Every clause that may follow, in canonical order, starting at `name`."""
    return _ORDER[_ORDER.index(name) :]


_AFTER_RELATION = ('AS', *_JOINS, *_onwards('WHERE'))

_QUERY = frozenset({'SELECT'})
"""
The statement form that has a result set to shape.

GROUP BY, ORDER BY, LIMIT and the rest belong to a query and to nothing else.
An UPDATE or a DELETE has no result to group or order, and every one of these
offered after a finished one wrote SQL the server refuses.
"""

CLAUSES = ClauseModel(
    clauses=(
        # A CTE body takes a whole statement. `VALUES` is in `opens_a_group` and
        # deliberately not in `followed_by`: a VALUES body is the ordinary way
        # to write a literal table, and after the list the clause model filters
        # it out anyway, since VALUES declares itself part of INSERT INTO.
        #
        # `aliases_with` is what separates `WITH a ` from `WITH a AS (…) `: the
        # second has AS among its item words, so `_unspent_alias` drops it. The
        # same machinery `FROM t AS x` already uses.
        Clause(
            name='WITH',
            suggests=(),
            opens_a_group=('SELECT', 'VALUES', 'WITH'),
            followed_by=('AS', 'SELECT'),
            aliases_with='AS',
            before_the_item=('RECURSIVE',),
        ),
        # No KEYWORD here: a select list wants columns and functions, and burying
        # them under reserved words is the failure mode this engine exists to avoid.
        # AS/FROM/DISTINCT arrive through `followed_by`, once an item is written.
        Clause(
            name='SELECT',
            suggests=COLUMN_EXPRESSION,
            followed_by=('AS', *_onwards('FROM')),
            before_the_item=('DISTINCT',),
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
            # Only SET. `UPDATE t FROM y` and `UPDATE t WHERE x` name no
            # assignment and parse as nothing; both follow SET, which says so.
            followed_by=('SET',),
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
            # PG 14's `USING (...) AS join_using_alias` is not offered. Both
            # spellings were tried: `aliases_with='AS'` never reaches this
            # caret, and a bare `AS` in the list is dropped by the same
            # alias-spending machinery before it is rendered. Naming it here
            # would be configuration that does nothing, which is worse than an
            # absence with a reason.
            followed_by=(*_JOINS, *_onwards('WHERE')),
        ),
        Clause(
            name='WHERE',
            suggests=COLUMN_EXPRESSION,
            operators=_COMPARISON,
            after_operand=_CONTINUES_PREDICATE,
            followed_by=('AND', 'OR', *_onwards('GROUP BY')),
        ),
        # Everything from here to FETCH shapes a result set, which only a query
        # has. Offering them after `UPDATE t SET x = 1 WHERE id = 2 ` writes SQL
        # no server accepts — and `statements` is read against the form of the
        # statement the caret is in, so a SELECT nested inside an INSERT or a
        # CTE still gets them.
        Clause(
            name='GROUP BY',
            follows=frozenset({'FROM', 'WHERE'}),
            statements=_QUERY,
            suggests=COLUMN_EXPRESSION,
            followed_by=_onwards('HAVING'),
        ),
        Clause(
            name='HAVING',
            follows=frozenset({'GROUP BY'}),
            statements=_QUERY,
            suggests=COLUMN_EXPRESSION,
            operators=_COMPARISON,
            after_operand=_CONTINUES_PREDICATE,
            followed_by=('AND', 'OR', *_onwards('WINDOW')),
        ),
        Clause(name='WINDOW', statements=_QUERY, suggests=COLUMN_EXPRESSION, followed_by=_onwards('UNION')),
        Clause(
            name='ORDER BY',
            statements=_QUERY,
            suggests=COLUMN_EXPRESSION,
            followed_by=('ASC', 'DESC', 'NULLS FIRST', 'NULLS LAST', *_onwards('LIMIT')),
        ),
        # A window spec, not a statement: what follows is the frame, never LIMIT.
        Clause(name='PARTITION BY', suggests=COLUMN_EXPRESSION, followed_by=('ORDER BY', 'ROWS', 'RANGE')),
        # These take a row count, and there is nothing to suggest for one. With
        # a kind here the position filled with the clause's own successors —
        # `LIMIT ` offered OFFSET, which belongs after the number rather than
        # instead of it. UNION keeps its kind because `UNION ALL` really does
        # come next, which is why this is per-clause and not a rule.
        Clause(name='LIMIT', statements=_QUERY, followed_by=_onwards('OFFSET')),
        # `ROW` and `ROWS` are noise words the standard allows after the count,
        # and all three backends take them — verified against the containers
        # rather than argued from the standard.
        Clause(name='OFFSET', statements=_QUERY, followed_by=('ROW', 'ROWS', *_onwards('FETCH'))),
        # Every word of the tail in one list, with EXCLUSIVE doing the ordering.
        # Naming them per position would need a clause per word, and the count
        # in the middle is not a word at all. Without the EXCLUSIVE entry this
        # list offers `ONLY` at `FETCH ⌶`, where it cannot go.
        Clause(
            name='FETCH',
            statements=_QUERY,
            suggests=(Kind.KEYWORD,),
            followed_by=('FIRST', 'NEXT', 'ROW', 'ROWS', 'ONLY', 'WITH TIES'),
        ),
        Clause(
            name='SET',
            follows=frozenset({'UPDATE'}),
            statements=frozenset({'UPDATE'}),
            suggests=(Kind.COLUMN,),
            followed_by=('WHERE', 'FROM'),
            operators=('=',),
        ),
        Clause(name='VALUES', statements=frozenset({'INSERT INTO'}), suggests=COLUMN_EXPRESSION),
        # A set operator combines two result sets, so it needs one to its left.
        #
        # `DISTINCT` is deliberately *not* offered here, though all three
        # backends accept `UNION DISTINCT`. `_half_written_clauses` builds its
        # phrase set from every `followed_by` entry and then skips any head that
        # is already a phrase — so naming DISTINCT here makes ('DISTINCT',) a
        # phrase and `SELECT DISTINCT ⌶` stops offering `ON`. The same trap the
        # `DROP` comment below records, reached from the other direction.
        #
        # Postgres's `DISTINCT ON` is a feature people write; `UNION DISTINCT`
        # is the default spelled out. Trading the first for the second is a bad
        # exchange, so the word stays out until a mechanism exists that can
        # offer it without claiming the head.
        Clause(name='UNION', statements=_QUERY, suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
        Clause(name='INTERSECT', statements=_QUERY, suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
        Clause(name='EXCEPT', statements=_QUERY, suggests=(Kind.KEYWORD,), followed_by=('ALL', 'SELECT')),
        # A wrapper rather than a statement: it takes one and reports on it.
        # Deliberately absent from `statement_start` — `statement_form` returns
        # the first start that is not WITH, so an EXPLAIN'd query would report
        # its form as EXPLAIN and lose every clause declaring
        # `statements={'SELECT'}`: GROUP BY, ORDER BY, LIMIT.
        Clause(name='EXPLAIN', suggests=(Kind.SNIPPET, Kind.KEYWORD), followed_by=EXPLAINABLE),
        # DDL that names one relation. Each `followed_by` is load-bearing rather
        # than decorative: `_clause_kinds` answers a written relation with
        # keywords only when the clause has continuations, so an empty list
        # leaves `DROP TABLE users ` offering a second relation, which cannot
        # follow without a comma.
        Clause(name='DROP TABLE', suggests=RELATION_REFERENCE, followed_by=('CASCADE', 'RESTRICT')),
        # The one kind-narrowed clause that belongs in the baseline: all three
        # backends have the statement and all three spell the kind `view` —
        # ClickHouse's view engine lowercases to exactly that.
        Clause(
            name='DROP VIEW',
            suggests=RELATION_REFERENCE,
            followed_by=('CASCADE', 'RESTRICT'),
            relation_kinds=('view',),
        ),
        Clause(name='TRUNCATE', suggests=RELATION_REFERENCE, followed_by=('CASCADE', 'RESTRICT')),
        # Two words each, and neither head is a phrase of its own. A bare `DROP`
        # here would make `('DROP',)` a phrase, and `_half_written_clauses`
        # skips a head that is already a phrase — so `DROP ` would stop
        # answering `TABLE`, for the same reason `ON ` does not answer
        # `CONFLICT` alone. `ALTER` would collide with `ALTER TABLE` the same
        # way.
        #
        # `DROP COLUMN` and `ALTER COLUMN` are the casualties, and they are the
        # DDL-authoring territory this dialect deliberately stops short of.
        Clause(name='ALTER TABLE', suggests=RELATION_REFERENCE, followed_by=('ADD COLUMN', 'RENAME TO')),
        # No `followed_by`: a call ends the statement, and an empty continuation
        # list is how a clause says so — the same rule that stops RETURNING and
        # FETCH proposing a successor.
        Clause(name='CALL', suggests=(Kind.PROCEDURE, Kind.SCHEMA)),
    ),
)

STATEMENT_START = (*EXPLAINABLE, 'DROP TABLE', 'DROP VIEW', 'TRUNCATE', 'ALTER TABLE', 'CALL')

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

PLACEHOLDERS = (Placeholder(opens='?', body='none'), Placeholder(opens=':'))
"""
The standard's dynamic parameter marker, plus the embedded-SQL host variable.

Both are safe at the baseline: strict ANSI has no cast operator and no `?`
operator, so neither spelling collides with anything this dialect can lex.
"""

ANSI = Dialect(
    name='ansi',
    syntax=Syntax(placeholders=PLACEHOLDERS),
    namespace=Namespace(levels=('schema', 'table')),
    clauses=CLAUSES,
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    statement_start=STATEMENT_START,
    templates=TEMPLATES,
    types=TYPES,
)
