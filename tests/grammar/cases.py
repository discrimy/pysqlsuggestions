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
)
