"""
The official SELECT grammar, as cases.

`tests/corpus/cases.py` burns down against expectations somebody observed —
pgcli's tests and a production suite. A corpus can only hold positions somebody
thought to write down. This file burns down against a *specified* set: every
caret the PostgreSQL SELECT synopsis names, whether or not anyone has met it.

The synopsis itself is `select.txt`, verbatim, and `test_grammar_select.py`
asserts that every line of it is cited here. That is what stops this file
drifting from the document it claims to track.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SYNOPSIS = (Path(__file__).parent / 'select.txt').read_text(encoding='utf-8')
"""The grammar as printed, read once at import."""

_WITH_QUERY = (
    'with_query_name [ ( column_name [, ...] ) ] AS [ [ NOT ] MATERIALIZED ] '
    '( select | values | insert | update | delete | merge )'
)
"""
The `with_query` production, which four cases cite and which is 122 columns long.

Named rather than repeated because the line is over the column limit, and a
citation wrapped by a formatter would no longer match the file it cites.
"""

_CYCLE = (
    '[ CYCLE column_name [, ...] SET cycle_mark_col_name [ TO cycle_mark_value DEFAULT '
    'cycle_mark_default ] USING cycle_path_col_name ]'
)
"""The cycle-detection production, named for the same reason as `_WITH_QUERY`."""

_ROWS_FROM = (
    '[ LATERAL ] ROWS FROM( function_name ( [ argument [, ...] ] ) [ AS ( column_definition [, ...] ) ] [, ...] )'
)
"""The multi-function FROM item, named for the same reason as `_WITH_QUERY`."""

_JOIN = 'from_item join_type from_item { ON join_condition | USING ( join_column [, ...] ) [ AS join_using_alias ] }'
"""The join production, cited by four cases."""

_ORDER_BY = '[ ORDER BY expression [ ASC | DESC | USING operator ] [ NULLS { FIRST | LAST } ] [, ...] ]'
"""The ordering production, cited by four cases."""

_FOR = (
    '[ FOR { UPDATE | NO KEY UPDATE | SHARE | KEY SHARE } [ OF from_reference [, ...] ] '
    '[ NOWAIT | SKIP LOCKED ] [...] ]'
)
"""The locking clause, named for the same reason as `_WITH_QUERY`."""


@dataclass(frozen=True)
class GrammarCase:
    """One caret the synopsis names, and what the engine must say there."""

    sql: str
    """Caret marked with ⌶, the convention tests/corpus/cases.py established."""
    cite: str
    """
    The synopsis line this position comes from, verbatim.

    Both a citation and a coverage token: the runner checks each cite against
    `select.txt`, and checks every line of `select.txt` against the cites.
    """
    offers: tuple[str, ...] = ()
    """
    Suggestion texts that must all appear. A subset assertion, not an equality.

    Ranking is `engine/rank.py`'s subject and `tests/test_complete.py` pins it
    already. A conformance case that also asserted order would go red on
    changes that have nothing to do with the grammar.
    """
    refuses: tuple[str, ...] = ()
    """
    Suggestion texts that must not appear at all.

    Where wrong answers die, and the reason this file exists rather than a
    second golden-request corpus: `WINDOW ⌶` offering a column is not a missing
    answer, it is an answer that writes SQL the server refuses.
    """
    dialects: tuple[str, ...] = ('postgres',)
    """
    Which backends this case must hold on. Postgres alone by default.

    Declared rather than derived. Running every case against every dialect and
    recording what passes would absorb a regression as though it were a
    decision — the value of naming them is that a case marked shared and newly
    failing is a backend losing behaviour nothing else covers.

    Postgres is the default because the synopsis is Postgres's. A case naming
    another dialect claims the production is not Postgres's alone, which is a
    claim about SQL rather than about this repository, so it is made explicitly
    and one case at a time.
    """
    pending: bool = False
    """True for a case the engine cannot satisfy today: an xfail(strict=True)."""
    refused: str = ''
    """
    Why this production is a deliberate non-goal. Empty for the rest.

    Independent of `pending`, and the two combine. `TABLESAMPLE ⌶` is refused
    *and* pending: we are not going to model sampling methods, and the engine
    must still stop offering `JOIN` there. Two commitments, both true, so
    `refused` does not excuse a case from the burn-down — it records that the
    fix is silence rather than grammar.
    """
    note: str = ''
    """
    Anything a later reader needs. Used above all for accidental greens.

    `FROM ONLY ⌶` passes because `ONLY` is skipped as an unrecognised token and
    the FROM clause carries the position, not because anything models it. That
    case will go red the day the production is modelled properly, and the note
    is the only warning.
    """


UNCITED = frozenset(
    {
        '( )',
        'expression',
    },
)
"""
Synopsis lines deliberately left uncited, so the coverage test can pass.

Both are `grouping_element` alternatives that are ordinary expression positions
with nothing specific to assert — an empty grouping set offers what any
expression offers. `( expression [, ...] )` *is* cited, by the `GROUP BY (⌶`
case, which covers all three in practice. Listed rather than pattern-matched:
a set of two strings is auditable, and a rule that skipped "short lines" would
silently swallow a real production later.
"""

_EVERY_DIALECT = ('postgres', 'clickhouse', 'trino')
"""
A production all three shipped backends have, at a caret all three answer alike.

Measured before it was claimed — every case carrying this passed against
`CLICKHOUSE` and `TRINO` when it was added. That is the baseline, and a case
here going red means a dialect lost behaviour no other test covers.
"""

_POSTGRES_AND_TRINO = ('postgres', 'trino')
"""
Two things ClickHouse does not have, and the cases that divide on them.

Trino declares `TABLESAMPLE` and ClickHouse does not — three cases. And
ClickHouse answers `TABLE users` with a syntax error at position 1, where
Postgres and Trino both run it — one more.

They are the reason `dialects` is a tuple rather than a boolean: `shared` would
have had to mean "all of them", and these are not.
"""

CASES: tuple[GrammarCase, ...] = (
    # --- with_query -------------------------------------------------------
    GrammarCase(
        sql='⌶',
        cite='[ WITH [ RECURSIVE ] with_query [, ...] ]',
        offers=('WITH', 'SELECT'),
        dialects=_EVERY_DIALECT,
        note='the empty editor, where a statement may begin',
    ),
    GrammarCase(
        sql='WITH rec⌶',
        cite='[ WITH [ RECURSIVE ] with_query [, ...] ]',
        offers=('RECURSIVE',),
        dialects=_EVERY_DIALECT,
        note='prefix-gated: request.py withholds before_the_item at an empty caret, on purpose',
    ),
    GrammarCase(
        sql='WITH x ⌶',
        cite=_WITH_QUERY,
        offers=('AS',),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='WITH x (⌶',
        cite=_WITH_QUERY,
        refuses=('SELECT', 'VALUES', 'users'),
        dialects=_EVERY_DIALECT,
        refused='a CTE column list names columns being defined, so there is nothing to suggest; the fix is silence',
        note='the paren is a name list rather than the CTE body, told apart by the alias word',
    ),
    GrammarCase(
        sql='WITH x AS ⌶',
        cite=_WITH_QUERY,
        offers=('MATERIALIZED', 'NOT MATERIALIZED'),
        pending=True,
        note='offers nothing at all here',
    ),
    GrammarCase(
        sql='WITH x AS (⌶',
        cite=_WITH_QUERY,
        offers=('SELECT', 'VALUES'),
        dialects=_EVERY_DIALECT,
        note='the two body forms every backend has',
    ),
    GrammarCase(
        sql='WITH x AS (⌶',
        cite=_WITH_QUERY,
        offers=('INSERT INTO', 'UPDATE', 'DELETE FROM'),
        note=(
            "data-modifying CTEs, which are Postgres's; ClickHouse refuses the first outright. "
            'MERGE is in the grammar and in no dialect here, so it is not asserted'
        ),
    ),
    GrammarCase(
        sql='WITH RECURSIVE x AS (SELECT 1) SEARCH ⌶',
        cite='[ SEARCH { BREADTH | DEPTH } FIRST BY column_name [, ...] SET search_seq_col_name ]',
        offers=('BREADTH', 'DEPTH'),
        refuses=('SELECT', 'INSERT INTO'),
        refused='recursive search ordering is a production this engine does not intend to model',
        note='reads SEARCH as still inside WITH and offers the CTE body words',
    ),
    GrammarCase(
        sql='WITH RECURSIVE x AS (SELECT 1) CYCLE ⌶',
        cite=_CYCLE,
        refuses=('SELECT', 'INSERT INTO'),
        refused='cycle detection is a production this engine does not intend to model',
        note='same fault as SEARCH: the CTE body words leak past the closing paren',
    ),
    # --- the select list --------------------------------------------------
    GrammarCase(
        sql='SELECT ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('users.id',),
        refuses=('ALL', 'DISTINCT'),
        dialects=_EVERY_DIALECT,
        note='a column is what belongs here; the modifiers are prefix-gated and must not crowd it',
    ),
    GrammarCase(
        sql='SELECT dis⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('DISTINCT',),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT al⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('ALL',),
    ),
    GrammarCase(
        sql='SELECT id, ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        refuses=('DISTINCT', 'ALL'),
        dialects=_EVERY_DIALECT,
        note='both are legal only directly after SELECT; this is the position they must not reach',
    ),
    GrammarCase(
        sql='SELECT DISTINCT ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('ON',),
    ),
    GrammarCase(
        sql='SELECT DISTINCT ON (⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('users.id',),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT id ⌶',
        cite='[ { * | expression [ [ AS ] output_name ] } [, ...] ]',
        offers=('AS', 'FROM'),
        dialects=_EVERY_DIALECT,
    ),
    # --- from_item --------------------------------------------------------
    GrammarCase(
        sql='SELECT * FROM ⌶',
        cite='[ FROM from_item [, ...] ]',
        offers=('users', 'ONLY', 'LATERAL'),
        pending=True,
        note='offers relations; neither ONLY nor LATERAL is offered where an item begins',
    ),
    GrammarCase(
        sql='SELECT * FROM ONLY ⌶',
        cite='[ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('users',),
        dialects=_EVERY_DIALECT,
        note='an accidental green: ONLY is skipped as an unrecognised token and FROM carries the position',
    ),
    GrammarCase(
        sql='SELECT * FROM users ⌶',
        cite='[ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('AS', 'TABLESAMPLE'),
        dialects=_POSTGRES_AND_TRINO,
        note='ClickHouse declares no TABLESAMPLE clause, so this caret offers less there and is not claimed for it',
    ),
    GrammarCase(
        sql='SELECT * FROM users AS u (⌶',
        cite='[ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        refuses=('users', 'orders', 'public'),
        dialects=_EVERY_DIALECT,
        refused='a column alias list names columns being defined; silence is the answer, not a relation',
        note='a list of names being defined; silent by the rule in engine/analyse.py',
    ),
    GrammarCase(
        sql='SELECT * FROM users TABLESAMPLE ⌶',
        cite='[ TABLESAMPLE sampling_method ( argument [, ...] ) [ REPEATABLE ( seed ) ] ]',
        refuses=('JOIN', 'WHERE', 'users'),
        dialects=_POSTGRES_AND_TRINO,
        refused='sampling methods are extensible per installation; a list here could not be kept true',
        note=(
            'the clause exists to stop this caret answering as though it were still inside FROM; '
            'ClickHouse declares no TABLESAMPLE, so it is not claimed there'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users TABLESAMPLE BERNOULLI (10) REPEATABLE (⌶',
        cite='[ TABLESAMPLE sampling_method ( argument [, ...] ) [ REPEATABLE ( seed ) ] ]',
        refuses=('users', 'orders', 'public'),
        dialects=_POSTGRES_AND_TRINO,
        refused='a repeat seed is a number; nothing in a catalog answers it',
        note=(
            'was offering relation names where a seed belongs; ClickHouse declares no TABLESAMPLE, '
            'so it is not claimed there'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM LATERAL (⌶',
        cite='[ LATERAL ] ( select ) [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('SELECT',),
        refuses=('users', 'orders'),
        note='a parenthesised LATERAL takes a whole subquery; the position offers relations instead',
    ),
    GrammarCase(
        sql='WITH x AS (SELECT 1) SELECT * FROM x ⌶',
        cite='with_query_name [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('AS',),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM LATERAL ⌶',
        cite='[ LATERAL ] function_name ( [ argument [, ...] ] )',
        offers=('users',),
        note='LATERAL is modelled in postgres.py, so this green is real',
    ),
    GrammarCase(
        sql='SELECT * FROM generate_series(1, 2) ⌶',
        cite='[ WITH ORDINALITY ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('WITH ORDINALITY', 'AS'),
        pending=True,
        note='offers neither; a function in a FROM list takes an alias and an ordinality marker',
    ),
    GrammarCase(
        sql='SELECT * FROM generate_series(1, 2) AS t (⌶',
        cite='[ LATERAL ] function_name ( [ argument [, ...] ] ) [ AS ] alias ( column_definition [, ...] )',
        refuses=('users', 'orders', 'public'),
        dialects=_EVERY_DIALECT,
        refused='a column definition list is DDL inside a query, the authoring this engine stops short of',
        note='a list of names being defined; silent by the rule in engine/analyse.py',
    ),
    GrammarCase(
        sql='SELECT * FROM generate_series(1, 2) AS (⌶',
        cite='[ LATERAL ] function_name ( [ argument [, ...] ] ) AS ( column_definition [, ...] )',
        refuses=('users', 'orders', 'public'),
        dialects=_EVERY_DIALECT,
        refused='the anonymous spelling of the definition list above, refused for the same reason',
        note='a list of names being defined; silent by the rule in engine/analyse.py',
    ),
    GrammarCase(
        sql='SELECT * FROM ROWS FROM(⌶',
        cite=_ROWS_FROM,
        offers=('now',),
        refuses=('users', 'orders', 'public'),
        note='a function, which the grammar puts here and a catalog has; it was offering relations',
    ),
    # --- joins ------------------------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users u ⌶',
        cite=_JOIN,
        offers=('JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'FULL JOIN', 'CROSS JOIN'),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o ⌶',
        cite=_JOIN,
        offers=('ON', 'USING'),
        refuses=('AS',),
        dialects=_EVERY_DIALECT,
        note='AS is correctly withheld: the alias is spent, and a second one parses as nothing',
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o ON ⌶',
        cite=_JOIN,
        offers=('u.id', 'o.user_id'),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o USING (id) ⌶',
        cite=_JOIN,
        offers=('AS',),
        pending=True,
        refused=(
            'the PG 14 join_using_alias; both aliases_with and a bare followed_by entry are '
            'dropped by the alias-spending machinery before this caret renders'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users u NATURAL ⌶',
        cite='from_item NATURAL join_type from_item',
        offers=('JOIN', 'LEFT JOIN'),
        dialects=_EVERY_DIALECT,
        note='an accidental green: NATURAL is skipped and FROM offers its joins anyway',
    ),
    GrammarCase(
        sql='SELECT * FROM users u CROSS ⌶',
        cite='from_item CROSS JOIN from_item',
        offers=('JOIN',),
        dialects=_EVERY_DIALECT,
    ),
    # --- the clauses that shape a result ----------------------------------
    GrammarCase(
        sql='SELECT * FROM users WHERE ⌶',
        cite='[ WHERE condition ]',
        offers=('users.id',),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY ⌶',
        cite='[ GROUP BY [ ALL | DISTINCT ] grouping_element [, ...] ]',
        offers=('users.id',),
        refuses=('ROLLUP', 'CUBE'),
        dialects=_EVERY_DIALECT,
        note='columns belong here; the grouping words are prefix-gated',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY al⌶',
        cite='[ GROUP BY [ ALL | DISTINCT ] grouping_element [, ...] ]',
        offers=('ALL',),
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY dis⌶',
        cite='[ GROUP BY [ ALL | DISTINCT ] grouping_element [, ...] ]',
        offers=('DISTINCT',),
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY rol⌶',
        cite='ROLLUP ( { expression | ( expression [, ...] ) } [, ...] )',
        offers=('ROLLUP',),
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY cu⌶',
        cite='CUBE ( { expression | ( expression [, ...] ) } [, ...] )',
        offers=('CUBE',),
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY grouping⌶',
        cite='GROUPING SETS ( grouping_element [, ...] )',
        offers=('GROUPING SETS',),
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY (⌶',
        cite='( expression [, ...] )',
        offers=('users.id',),
        dialects=_EVERY_DIALECT,
        note='covers the bare `expression` and `( )` alternatives too; see UNCITED',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY ROLLUP (⌶',
        cite='ROLLUP ( { expression | ( expression [, ...] ) } [, ...] )',
        offers=('users.id',),
        dialects=_EVERY_DIALECT,
        note='an accidental green: ROLLUP is skipped and GROUP BY carries the position',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY CUBE (⌶',
        cite='CUBE ( { expression | ( expression [, ...] ) } [, ...] )',
        offers=('users.id',),
        dialects=_EVERY_DIALECT,
        note='accidental, as ROLLUP is',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY GROUPING SETS (⌶',
        cite='GROUPING SETS ( grouping_element [, ...] )',
        offers=('users.id',),
        dialects=_EVERY_DIALECT,
        note='accidental, as ROLLUP is',
    ),
    GrammarCase(
        sql='SELECT * FROM users GROUP BY id HAVING ⌶',
        cite='[ HAVING condition ]',
        offers=('users.id',),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users WINDOW ⌶',
        cite='[ WINDOW window_name AS ( window_definition ) [, ...] ]',
        refuses=('users.id', 'users.email'),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users WINDOW w AS (⌶',
        cite='[ WINDOW window_name AS ( window_definition ) [, ...] ]',
        offers=('PARTITION BY', 'ORDER BY'),
        dialects=_EVERY_DIALECT,
    ),
    # --- set operations ---------------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users UNION ⌶',
        cite='[ { UNION | INTERSECT | EXCEPT } [ ALL | DISTINCT ] select ]',
        offers=('ALL', 'DISTINCT', 'SELECT'),
        pending=True,
        refused=(
            'offering DISTINCT here would make it a phrase in its own right, and SELECT DISTINCT '
            'would stop completing to DISTINCT ON'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users INTERSECT ⌶',
        cite='[ { UNION | INTERSECT | EXCEPT } [ ALL | DISTINCT ] select ]',
        offers=('ALL', 'DISTINCT'),
        pending=True,
        refused=(
            'offering DISTINCT here would make it a phrase in its own right, and SELECT DISTINCT '
            'would stop completing to DISTINCT ON'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users EXCEPT ⌶',
        cite='[ { UNION | INTERSECT | EXCEPT } [ ALL | DISTINCT ] select ]',
        offers=('ALL', 'DISTINCT'),
        pending=True,
        refused=(
            'offering DISTINCT here would make it a phrase in its own right, and SELECT DISTINCT '
            'would stop completing to DISTINCT ON'
        ),
    ),
    # --- ordering ---------------------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users ORDER BY ⌶',
        cite=_ORDER_BY,
        offers=('users.id',),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users ORDER BY id ⌶',
        cite=_ORDER_BY,
        offers=('ASC', 'DESC', 'NULLS FIRST', 'NULLS LAST', 'USING'),
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o USING (id) ORDER BY u.id ⌶',
        cite=_ORDER_BY,
        offers=('ASC', 'DESC', 'USING'),
        note=(
            "a join's USING is not the ordering one. `words_in_item` stops at the clause, so the "
            'sort direction is still unspent here — without that bound the run reaches back across '
            'the whole statement whenever no comma intervenes'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users ORDER BY id USING ⌶',
        cite=_ORDER_BY,
        offers=('<', '>'),
        refuses=('users.id',),
        pending=True,
        refused=(
            'USING takes an operator, and operators reach a caret only through Clause.operators, '
            'which marks a predicate clause; ORDER BY is not one and making it one would offer '
            '= after every ordering column'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users ORDER BY id ASC ⌶',
        cite=_ORDER_BY,
        offers=('NULLS FIRST', 'NULLS LAST'),
        refuses=('ASC', 'DESC', 'USING'),
        dialects=_EVERY_DIALECT,
        note=(
            'EXCLUSIVE in dialects/base.py settles the direction once, which is what this pins. '
            'USING belongs to that same choice — `ORDER BY id ASC USING >` is refused by the '
            'server — and was offered here until the acceptance sweep caught it'
        ),
    ),
    # --- the row-count clauses --------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users LIMIT ⌶',
        cite='[ LIMIT { count | ALL } ]',
        refuses=('OFFSET', 'FETCH', 'ALL'),
        dialects=_EVERY_DIALECT,
        note=(
            'a row count belongs here and nothing can suggest one. LIMIT deliberately has no kind: '
            'its docstring records that giving it one made this caret offer OFFSET, which goes after '
            'the number rather than instead of it'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users LIMIT al⌶',
        cite='[ LIMIT { count | ALL } ]',
        offers=('ALL',),
    ),
    GrammarCase(
        sql='SELECT * FROM users OFFSET 10 ⌶',
        cite='[ OFFSET start [ ROW | ROWS ] ]',
        offers=('ROW', 'ROWS', 'FETCH'),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users FETCH ⌶',
        cite='[ FETCH { FIRST | NEXT } [ count ] { ROW | ROWS } { ONLY | WITH TIES } ]',
        offers=('FIRST', 'NEXT'),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users FETCH FIRST 10 ⌶',
        cite='[ FETCH { FIRST | NEXT } [ count ] { ROW | ROWS } { ONLY | WITH TIES } ]',
        offers=('ROW', 'ROWS'),
        dialects=_EVERY_DIALECT,
    ),
    GrammarCase(
        sql='SELECT * FROM users FETCH FIRST 10 ROWS ⌶',
        cite='[ FETCH { FIRST | NEXT } [ count ] { ROW | ROWS } { ONLY | WITH TIES } ]',
        offers=('ONLY', 'WITH TIES'),
        dialects=_EVERY_DIALECT,
    ),
    # --- the locking clause -----------------------------------------------
    GrammarCase(
        sql='SELECT * FROM users FOR ⌶',
        cite=_FOR,
        offers=('UPDATE', 'NO KEY UPDATE', 'SHARE', 'KEY SHARE'),
        refuses=('users', 'orders', 'public'),
        note='was the worst answer in the suite: FOR was not a clause, so this caret was still governed by FROM',
    ),
    GrammarCase(
        sql='SELECT * FROM users FOR UPDATE ⌶',
        cite=_FOR,
        offers=('OF', 'NOWAIT', 'SKIP LOCKED'),
        refuses=('users', 'orders'),
    ),
    GrammarCase(
        sql='SELECT * FROM users u FOR UPDATE OF ⌶',
        cite=_FOR,
        offers=('u',),
        refuses=('users',),
        pending=True,
        refused=(
            'OF names a relation already in scope and no Kind means that; Kind.TABLE would answer '
            'an aliased relation with its bare name, which the server refuses'
        ),
        note='silent by choice — see the OF clause in postgres.py',
    ),
    GrammarCase(
        sql='SELECT * FROM users u FOR UPDATE OF u ⌶',
        cite=_FOR,
        offers=('NOWAIT', 'SKIP LOCKED'),
    ),
    GrammarCase(
        sql='SELECT * FROM users JOIN orders ⌶',
        cite=_FOR,
        refuses=('FOR UPDATE', 'FOR NO KEY UPDATE', 'FOR SHARE', 'FOR KEY SHARE'),
        note=(
            'a join is not a place a statement can end — `JOIN orders FOR UPDATE` names no join '
            'condition and the server refuses it. The locking clauses followed JOIN until the '
            'acceptance sweep caught this'
        ),
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o ON u.id = o.user_id ⌶',
        cite=_FOR,
        offers=('FOR UPDATE', 'FOR NO KEY UPDATE', 'FOR SHARE', 'FOR KEY SHARE'),
        note='the position the locking clauses were missing entirely while they followed JOIN instead',
    ),
    GrammarCase(
        sql='SELECT * FROM users u JOIN orders o USING (id) ⌶',
        cite=_JOIN,
        offers=('FOR UPDATE',),
        note='the other way a join becomes complete, and the other clause the locking words follow',
    ),
    GrammarCase(
        sql='SELECT * FROM users CROSS JOIN orders ⌶',
        cite=_FOR,
        offers=('FOR UPDATE',),
        pending=True,
        refused=(
            'every join spelling resolves through the one JOIN clause, so there is no name that '
            'admits the conditionless joins without also admitting the ones that need a condition'
        ),
        note='valid SQL, given up deliberately: a suggestion the server refuses costs more than one never made',
    ),
    # --- the TABLE form ---------------------------------------------------
    GrammarCase(
        sql='TABLE ⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('users',),
        dialects=_POSTGRES_AND_TRINO,
        note='waited on CREATE TABLE being modelled, so the longer clause name wins the match',
    ),
    GrammarCase(
        sql='TABLE on⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('ONLY',),
        note="ONLY is Postgres's: Trino answers `TABLE ONLY t` with mismatched input",
    ),
    GrammarCase(
        sql='TABLE ONLY ⌶',
        cite='TABLE [ ONLY ] table_name [ * ]',
        offers=('users',),
        note=(
            'passes on Trino too, and is deliberately not marked for it: ONLY is skipped there as an '
            'unrecognised token and TABLE carries the position, so the green is an accident inside a '
            'statement Trino rejects outright. A marking is a claim about the production'
        ),
    ),
)
