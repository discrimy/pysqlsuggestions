"""Inserting a chosen suggestion back into the SQL."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Function, Kind, Suggestion

SNAPSHOT = {
    ('public', 'auth_user'): [('id', 'bigint'), ('username', 'varchar'), ('email', 'varchar')],
    ('public', 'orders'): [('id', 'bigint'), ('user_id', 'bigint'), ('created_at', 'date')],
}

FUNCTIONS = (Function(schema='pg_catalog', name='count', args='"any"', result='bigint'),)


def catalog() -> MemoryCatalog:
    """The fixture catalog."""
    return MemoryCatalog(SNAPSHOT, functions=FUNCTIONS)


def accept_first(sql: str) -> tuple[str, int]:
    """Complete at end of input and apply the top suggestion."""
    found = complete(sql, len(sql), POSTGRES, catalog())
    return apply_suggestion(sql, found[0])


def test_a_qualifier_keeps_its_place() -> None:
    """`where u.crea` must become `where u.created_at`, not `where created_at`."""
    new_sql, caret = accept_first('SELECT * FROM orders u WHERE u.crea')
    assert new_sql == 'SELECT * FROM orders u WHERE u.created_at'
    assert caret == len(new_sql)


def test_the_tail_survives() -> None:
    """Text after the caret is untouched, and the caret lands after the insertion."""
    sql = 'SELECT em FROM auth_user u'
    found = complete(sql, 9, POSTGRES, catalog())
    new_sql, caret = apply_suggestion(sql, found[0])
    assert new_sql == 'SELECT email FROM auth_user u'
    assert new_sql[caret:] == ' FROM auth_user u'


def test_nothing_typed_yet_inserts_at_the_caret() -> None:
    """An empty prefix means an empty replace span."""
    sql = 'SELECT * FROM orders o WHERE o.'
    found = complete(sql, len(sql), POSTGRES, catalog())
    new_sql, _ = apply_suggestion(sql, found[0])
    assert new_sql == 'SELECT * FROM orders o WHERE o.id'


def test_a_function_gets_its_parens_closed() -> None:
    """And the caret is parked between them, ready for the argument."""
    suggestion = Suggestion(text='count', kind=Kind.FUNCTION, replace_span=(7, 9), score=1.0)
    new_sql, caret = apply_suggestion('SELECT co FROM orders', suggestion)
    assert new_sql == 'SELECT count() FROM orders'
    assert new_sql[caret] == ')'


def test_a_function_does_not_double_a_paren_the_author_typed() -> None:
    """`SELECT co(` accepting `count` must not produce `count()(`."""
    suggestion = Suggestion(text='count', kind=Kind.FUNCTION, replace_span=(7, 9), score=1.0)
    new_sql, caret = apply_suggestion('SELECT co( FROM orders', suggestion)
    assert new_sql == 'SELECT count( FROM orders'
    assert caret == len('SELECT count')


def test_close_parens_can_be_turned_off() -> None:
    """A front end that manages brackets itself asks for the bare name."""
    suggestion = Suggestion(text='count', kind=Kind.FUNCTION, replace_span=(7, 9), score=1.0)
    assert apply_suggestion('SELECT co', suggestion, close_parens=False)[0] == 'SELECT count'


def test_a_quoted_identifier_is_inserted_as_quoted() -> None:
    """Ranking decided the quoting; apply just splices what it was given."""
    suggestion = Suggestion(text='"Mixed Case"', kind=Kind.TABLE, replace_span=(14, 16), score=1.0)
    assert apply_suggestion('SELECT * FROM Mi', suggestion)[0] == 'SELECT * FROM "Mixed Case"'


def test_keyword_case_follows_the_typed_prefix() -> None:
    """What the author is typing right now wins."""
    assert accept_first('select * from auth_user wh')[0].endswith('where')
    assert accept_first('SELECT * FROM auth_user WH')[0].endswith('WHERE')


def test_keyword_case_follows_the_last_keyword_when_nothing_is_typed() -> None:
    """With an empty prefix the casing has to come from what came before."""
    lower = complete('select * from auth_user ', 24, POSTGRES, catalog())
    upper = complete('SELECT * FROM auth_user ', 24, POSTGRES, catalog())
    assert [s.text for s in lower if s.kind is Kind.KEYWORD][:1] == [
        w.lower() for w in [s.text for s in upper if s.kind is Kind.KEYWORD][:1]
    ]
    assert all(s.text.islower() for s in lower if s.kind is Kind.KEYWORD)
    assert all(s.text.isupper() for s in upper if s.kind is Kind.KEYWORD)
