"""
Statement forms this engine does not have a clause model for.

`DROP TABLE ⌶` offered `SELECT`, because no clause matched and no clause meant
the empty-editor position — the words a statement may *begin* with, inside a
statement that had already begun. Accepting one wrote `DROP TABLE SELECT`.

Two rules answer it. The forms whose answer is a relation are modelled, and a
form the engine does not recognise says nothing at all.
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
    """Two relations, which is all any of these positions needs."""
    return MemoryCatalog(SNAPSHOT)


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def clause_at_end(sql: str) -> str | None:
    """The clause the engine believes governs the end of `sql`."""
    return derive_request(sql, len(sql), POSTGRES).clause


def test_explain_is_a_clause_rather_than_an_unrecognised_word() -> None:
    """It behaved correctly by being skipped, which is not the same as being understood."""
    assert clause_at_end('EXPLAIN ') == 'EXPLAIN'


def test_explain_offers_the_statements_it_can_explain() -> None:
    """A query, not a DROP: `EXPLAIN DROP TABLE users` is a syntax error."""
    found = offered('EXPLAIN ')
    assert 'SELECT' in found
    assert 'WITH' in found
    assert 'INSERT INTO' in found


def test_explain_still_analyses_the_statement_inside_it() -> None:
    """The inner statement is the one being completed, and it always was."""
    assert offered('EXPLAIN SELECT * FROM users u WHERE u.') == ['id', 'email']


def test_explain_analyze_still_analyses_the_statement_inside_it() -> None:
    """A modifier between EXPLAIN and its statement must not break the scope."""
    assert offered('EXPLAIN ANALYZE SELECT * FROM users u WHERE u.') == ['id', 'email']


def test_explain_with_options_still_analyses_the_statement_inside_it() -> None:
    """`EXPLAIN (FORMAT JSON) …` puts a parenthesised group in the way."""
    assert offered('EXPLAIN (FORMAT JSON) SELECT * FROM users u WHERE u.') == ['id', 'email']


def test_postgres_offers_its_own_explain_modifiers() -> None:
    """
    ANALYZE stands between EXPLAIN and its statement, which is what
    `before_the_item` means — the same field that keeps DISTINCT out of the
    middle of a select list. Offered behind a prefix only, like DISTINCT.
    """
    assert 'ANALYZE' in offered('EXPLAIN ana')
