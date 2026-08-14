"""
The parenthesised definition list, and the clause that opens it.

`CREATE TABLE t (id ⌶` had nothing to say, which is gap 1 in `docs/gaps.md`.
Every caret here was silent before this suite, so nothing in it can regress from
a right answer to a wrong one — only from silence to an answer.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {
    ('public', 'users'): [('id', 'bigint'), ('email', 'text')],
    ('public', 'orders'): [('id', 'bigint')],
}


def catalog() -> MemoryCatalog:
    """Two relations, so a case can assert that neither is offered."""
    return MemoryCatalog(SNAPSHOT)


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def clause_at_end(sql: str) -> str | None:
    """The clause the engine believes governs the end of `sql`."""
    return derive_request(sql, len(sql), POSTGRES).clause


def test_create_offers_the_word_that_finishes_it() -> None:
    """Derived from the clause name by `_half_written_clauses`, like `GROUP ⌶`."""
    assert offered('CREATE ') == ['TABLE']


def test_drop_and_alter_still_offer_their_own_words() -> None:
    """
    A new head must not claim theirs.

    `_half_written_clauses` skips a head that is already a phrase, so a clause
    named `CREATE` alone would have made `('CREATE',)` a phrase and silenced
    this. Two words is what keeps all three heads answering.
    """
    assert 'TABLE' in offered('DROP ')
    assert 'TABLE' in offered('ALTER ')


def test_the_longer_clause_wins_over_a_bare_table() -> None:
    """
    `clause_at` ranks by (end offset, word count), so two words beat one.

    This is the whole reason `TABLE` is modellable at all: without it the
    definition list would be governed by the bare form and offer relations.
    """
    assert clause_at_end('CREATE TABLE t (id ') == 'CREATE TABLE'


def test_the_relation_being_created_is_not_suggested() -> None:
    """
    The name is the author's to invent, so `Kind.TABLE` here is a wrong answer.

    `WINDOW` carries the same empty `suggests` for the same reason.
    """
    assert offered('CREATE TABLE ') == []


def test_if_not_exists_is_reached_by_typing() -> None:
    """
    `before_the_item`, which `request.py` gates behind a non-empty prefix.

    The caret after `CREATE TABLE ` is where a name is being typed, and a
    keyword ranked above it would be in the way. Behind a prefix it costs
    nothing.
    """
    assert offered('CREATE TABLE if') == ['IF NOT EXISTS']


def test_the_definition_list_is_not_offered_the_clause_continuations() -> None:
    """
    A clause's `followed_by` reaches inside its parens, where it cannot parse.

    Measured before the clause was written: `followed_by=('AS',)` put `AS` at
    this caret, and `CREATE TABLE t (id AS` parses as nothing. The clause
    declares none, which is why this is silent rather than wrong.
    """
    assert offered('CREATE TABLE t (id ') == []


def test_create_table_is_offered_where_a_statement_may_begin() -> None:
    """An empty editor is exactly where it is a useful suggestion."""
    assert 'CREATE TABLE' in offered('')


def test_explain_does_not_offer_it() -> None:
    """`EXPLAIN CREATE TABLE t (id int)` is a syntax error."""
    assert 'CREATE TABLE' not in offered('EXPLAIN ')
