"""Inserting a chosen suggestion back into the SQL."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, complete, derive_request
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
    suggestion = Suggestion(
        text='count',
        kind=Kind.FUNCTION,
        replace_span=(7, 9),
        score=1.0,
        takes_arguments=True,
    )
    new_sql, caret = apply_suggestion('SELECT co FROM orders', suggestion)
    assert new_sql == 'SELECT count() FROM orders'
    assert new_sql[caret] == ')'


def test_a_function_taking_no_arguments_leaves_the_caret_after_it() -> None:
    """`now()` is finished on insertion; parking inside means typing past a correct bracket."""
    suggestion = Suggestion(text='now', kind=Kind.FUNCTION, replace_span=(7, 9), score=1.0)
    new_sql, caret = apply_suggestion('SELECT no FROM orders', suggestion)
    assert new_sql == 'SELECT now() FROM orders'
    assert caret == len('SELECT now()')


def test_the_catalog_decides_which_it_is() -> None:
    """Postgres reports the signature, so now() and count("any") end up different."""
    cat = MemoryCatalog(
        SNAPSHOT,
        functions=(
            Function(schema='pg_catalog', name='now', args='', result='timestamp with time zone'),
            Function(schema='pg_catalog', name='counted', args='"any"', result='bigint'),
        ),
    )
    found = {s.text: s for s in complete('SELECT no', 9, POSTGRES, cat)}
    assert found['now'].takes_arguments is False
    assert apply_suggestion('SELECT no', found['now'])[1] == len('SELECT now()')


def test_an_unknown_signature_is_treated_as_taking_arguments() -> None:
    """ClickHouse reports no signatures, and the safe guess is to park inside."""
    cat = MemoryCatalog(SNAPSHOT, functions=(Function(schema=None, name='nowhere', args=None, result='function'),))
    found = next(s for s in complete('SELECT no', 9, POSTGRES, cat) if s.text == 'nowhere')
    assert found.takes_arguments is True


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


def test_keyword_case_follows_the_document() -> None:
    """The keywords already finished decide it."""
    assert accept_first('select * from auth_user wh')[0].endswith('where')
    assert accept_first('SELECT * FROM auth_user WH')[0].endswith('WHERE')


def test_a_half_typed_word_does_not_overrule_the_document() -> None:
    """Two lowercase letters among uppercase keywords means shift has not been pressed yet."""
    assert accept_first('SELECT * FROM auth_user wh')[0].endswith('WHERE')
    assert accept_first('select * from auth_user WH')[0].endswith('where')


def test_the_half_typed_word_decides_when_nothing_else_can() -> None:
    """With no complete keyword written, it is the only evidence there is."""
    assert derive_request('sel', 3, POSTGRES).keyword_case == 'lower'
    assert derive_request('SEL', 3, POSTGRES).keyword_case == 'upper'


def test_the_document_outranks_the_half_typed_word() -> None:
    """The same partial, two documents, two answers."""
    assert derive_request('SELECT * FROM t wh', 18, POSTGRES).keyword_case == 'upper'
    assert derive_request('select * from t wh', 18, POSTGRES).keyword_case == 'lower'


def test_keyword_case_follows_the_last_keyword_when_nothing_is_typed() -> None:
    """With an empty prefix the casing has to come from what came before."""
    lower = complete('select * from auth_user ', 24, POSTGRES, catalog())
    upper = complete('SELECT * FROM auth_user ', 24, POSTGRES, catalog())
    assert [s.text for s in lower if s.kind is Kind.KEYWORD][:1] == [
        w.lower() for w in [s.text for s in upper if s.kind is Kind.KEYWORD][:1]
    ]
    assert all(s.text.islower() for s in lower if s.kind is Kind.KEYWORD)
    assert all(s.text.isupper() for s in upper if s.kind is Kind.KEYWORD)
