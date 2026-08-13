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

CASES: tuple[GrammarCase, ...] = (
    # --- with_query -------------------------------------------------------
    GrammarCase(
        sql='⌶',
        cite='[ WITH [ RECURSIVE ] with_query [, ...] ]',
        offers=('WITH', 'SELECT'),
        note='the empty editor, where a statement may begin',
    ),
    GrammarCase(
        sql='WITH ⌶',
        cite='[ WITH [ RECURSIVE ] with_query [, ...] ]',
        offers=('RECURSIVE',),
        pending=True,
        note='offers nothing; RECURSIVE is in before_the_item and never reaches this caret',
    ),
    GrammarCase(
        sql='WITH x ⌶',
        cite=_WITH_QUERY,
        offers=('AS',),
    ),
    GrammarCase(
        sql='WITH x (⌶',
        cite=_WITH_QUERY,
        refuses=('SELECT', 'VALUES', 'users'),
        pending=True,
        refused='a CTE column list names columns being defined, so there is nothing to suggest; the fix is silence',
        note='offers the CTE body words — SELECT, VALUES, WITH — inside the column list',
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
        offers=('SELECT', 'VALUES', 'INSERT INTO', 'UPDATE', 'DELETE FROM'),
        note='MERGE is in the grammar and in no dialect here; not asserted',
    ),
    GrammarCase(
        sql='WITH RECURSIVE x AS (SELECT 1) SEARCH ⌶',
        cite='[ SEARCH { BREADTH | DEPTH } FIRST BY column_name [, ...] SET search_seq_col_name ]',
        offers=('BREADTH', 'DEPTH'),
        refuses=('SELECT', 'INSERT INTO'),
        pending=True,
        refused='recursive search ordering is a production this engine does not intend to model',
        note='reads SEARCH as still inside WITH and offers the CTE body words',
    ),
    GrammarCase(
        sql='WITH RECURSIVE x AS (SELECT 1) CYCLE ⌶',
        cite=_CYCLE,
        refuses=('SELECT', 'INSERT INTO'),
        pending=True,
        refused='cycle detection is a production this engine does not intend to model',
        note='same fault as SEARCH: the CTE body words leak past the closing paren',
    ),
    # --- the select list --------------------------------------------------
    GrammarCase(
        sql='SELECT ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        offers=('ALL', 'DISTINCT'),
        pending=True,
        note='offers columns only; `before_the_item` carries DISTINCT and omits ALL',
    ),
    GrammarCase(
        sql='SELECT id, ⌶',
        cite='SELECT [ ALL | DISTINCT [ ON ( expression [, ...] ) ] ]',
        refuses=('DISTINCT', 'ALL'),
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
    ),
    GrammarCase(
        sql='SELECT id ⌶',
        cite='[ { * | expression [ [ AS ] output_name ] } [, ...] ]',
        offers=('AS', 'FROM'),
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
        note='an accidental green: ONLY is skipped as an unrecognised token and FROM carries the position',
    ),
    GrammarCase(
        sql='SELECT * FROM users ⌶',
        cite='[ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('AS', 'TABLESAMPLE'),
        pending=True,
        note='AS is offered, TABLESAMPLE is not',
    ),
    GrammarCase(
        sql='SELECT * FROM users AS u (⌶',
        cite='[ ONLY ] table_name [ * ] [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='a column alias list names columns being defined; silence is the answer, not a relation',
        note='offers relation names inside the alias list',
    ),
    GrammarCase(
        sql='SELECT * FROM users TABLESAMPLE ⌶',
        cite='[ TABLESAMPLE sampling_method ( argument [, ...] ) [ REPEATABLE ( seed ) ] ]',
        offers=('BERNOULLI', 'SYSTEM'),
        refuses=('JOIN', 'WHERE'),
        pending=True,
        refused='sampling methods are extensible per installation; a list here could not be kept true',
        note='offers the clauses that follow a relation, so accepting writes TABLESAMPLE JOIN',
    ),
    GrammarCase(
        sql='SELECT * FROM users TABLESAMPLE BERNOULLI (10) REPEATABLE (⌶',
        cite='[ TABLESAMPLE sampling_method ( argument [, ...] ) [ REPEATABLE ( seed ) ] ]',
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='a repeat seed is a number; nothing in a catalog answers it',
        note='offers relation names where a seed belongs',
    ),
    GrammarCase(
        sql='SELECT * FROM LATERAL (⌶',
        cite='[ LATERAL ] ( select ) [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('SELECT',),
        refuses=('users', 'orders'),
        pending=True,
        note='a parenthesised LATERAL takes a whole subquery; the position offers relations instead',
    ),
    GrammarCase(
        sql='WITH x AS (SELECT 1) SELECT * FROM x ⌶',
        cite='with_query_name [ [ AS ] alias [ ( column_alias [, ...] ) ] ]',
        offers=('AS',),
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
        pending=True,
        refused='a column definition list is DDL inside a query, the authoring this engine stops short of',
        note='offers relation names where a column name and type belong',
    ),
    GrammarCase(
        sql='SELECT * FROM generate_series(1, 2) AS (⌶',
        cite='[ LATERAL ] function_name ( [ argument [, ...] ] ) AS ( column_definition [, ...] )',
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='the anonymous spelling of the definition list above, refused for the same reason',
        note='offers relation names',
    ),
    GrammarCase(
        sql='SELECT * FROM ROWS FROM(⌶',
        cite=_ROWS_FROM,
        refuses=('users', 'orders', 'public'),
        pending=True,
        refused='a multi-function FROM item is exotica; the position must stay silent',
        note='reads ROWS FROM( as an ordinary FROM and offers relations',
    ),
)
