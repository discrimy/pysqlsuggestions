"""Statement templates, and where the caret stops inside one."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.rank import expand_snippet
from pysqlsuggestions.types import Kind

CATALOG = MemoryCatalog({('public', 'auth_user'): [('id', 'bigint'), ('username', 'varchar')]})


def suggest(sql: str) -> list[str]:
    """Suggestion texts, caret at end of input."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, CATALOG)]


def test_placeholders_become_offsets() -> None:
    """The text comes out plain; the positions come out separately."""
    text, stops = expand_snippet('SELECT $1 FROM $2 AS $3')
    assert text == 'SELECT  FROM  AS '
    assert stops == (7, 13, 17)
    assert [text[:s] for s in stops] == ['SELECT ', 'SELECT  FROM ', 'SELECT  FROM  AS ']


def test_numbering_orders_the_visit_not_the_text() -> None:
    """`$3` written first is still visited last, which is how the SELECT template works."""
    text, stops = expand_snippet('SELECT $3 FROM $1 AS $2')
    assert text == 'SELECT  FROM  AS '
    assert [text[:s] for s in stops] == ['SELECT  FROM ', 'SELECT  FROM  AS ', 'SELECT ']


def test_zero_is_visited_last() -> None:
    """LSP orders $0 after every numbered stop, wherever it appears in the text."""
    text, stops = expand_snippet('a$0b$1c$2')
    assert text == 'abc'
    assert stops == (2, 3, 1)


def test_a_snippet_without_placeholders_has_no_stops() -> None:
    """Then it is only text."""
    assert expand_snippet('SELECT 1') == ('SELECT 1', ())


def test_an_empty_query_offers_a_whole_statement() -> None:
    """Nothing written yet, so the useful answer is a shape rather than a word."""
    assert suggest('')[0] == 'SELECT  FROM  AS '


def test_an_empty_query_also_offers_statement_keywords() -> None:
    """A single word is often all that is wanted."""
    found = suggest('')
    assert 'SELECT' in found
    assert 'WITH' in found
    assert 'INSERT INTO' in found


def test_a_statement_keyword_prefix_filters() -> None:
    """
    `ins` reaches INSERT INTO, cased to match.

    With nothing else written the half-typed word is the only evidence there is,
    so it decides — which is why `ins` gives back lowercase and `INS` does not.
    """
    assert suggest('ins') == ['insert into']
    assert suggest('INS') == ['INSERT INTO']


def test_the_snippet_matches_on_its_label() -> None:
    """Nobody types the expanded text, so `sel` has to find it."""
    found = [s for s in complete('sel', 3, POSTGRES, CATALOG) if s.kind is Kind.SNIPPET]
    assert found
    assert found[0].label == 'SELECT … FROM … AS …'


def test_the_first_stop_is_the_relation() -> None:
    """Nothing can suggest a column until it knows the table, so that comes first."""
    chosen = next(s for s in complete('', 0, POSTGRES, CATALOG) if s.kind is Kind.SNIPPET)
    new_sql, caret = apply_suggestion('', chosen)
    assert new_sql == 'SELECT  FROM  AS '
    assert new_sql[:caret] == 'SELECT  FROM '


def test_the_stops_run_relation_then_alias_then_columns() -> None:
    """A front end that can cycle them needs them all, relative to the insertion point."""
    chosen = next(s for s in complete('', 0, POSTGRES, CATALOG) if s.kind is Kind.SNIPPET)
    text = chosen.text
    assert [text[:s] for s in chosen.stops] == ['SELECT  FROM ', 'SELECT  FROM  AS ', 'SELECT ']


def test_each_stop_is_answerable_when_it_is_reached() -> None:
    """The order exists so that every stop has something to offer by the time you get there."""
    relation = complete('SELECT  FROM  AS ', 13, POSTGRES, CATALOG)
    assert 'auth_user' in [s.text for s in relation]

    alias = complete('SELECT  FROM auth_user AS ', 26, POSTGRES, CATALOG)
    assert 'au' in [s.text for s in alias]

    columns = complete('SELECT  FROM auth_user AS au', 7, POSTGRES, CATALOG)
    assert 'au.username' in [s.text for s in columns]


def test_a_snippet_follows_the_document_casing() -> None:
    """Lowercase keywords in, lowercase template out."""
    chosen = next(s for s in complete('select * from auth_user; ', 25, POSTGRES, CATALOG) if s.kind is Kind.SNIPPET)
    assert chosen.text == 'select  from  as '


def test_a_snippet_is_not_offered_once_a_statement_has_started() -> None:
    """It replaces nothing useful there."""
    assert not [s for s in complete('SELECT id FROM ', 15, POSTGRES, CATALOG) if s.kind is Kind.SNIPPET]
