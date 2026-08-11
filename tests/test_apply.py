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
    assert new_sql == 'SELECT u.email FROM auth_user u'
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


def test_a_quoted_name_is_replaced_without_stranding_its_quote() -> None:
    """Completing inside `"…"` must not leave an odd number of quotes behind."""
    sql = 'SELECT * FROM "auth_u"'
    found = complete(sql, 21, POSTGRES, catalog())
    text, _ = apply_suggestion(sql, found[0])
    assert text == 'SELECT * FROM auth_user'
    assert text.count('"') % 2 == 0


def test_a_keyword_is_not_glued_onto_a_literal() -> None:
    """
    `... > 1` then AND must not produce `1AND`.

    Nothing is being replaced there, so insertion has to supply the separator
    the author has not typed yet. Postgres rejects `1AND` outright.
    """
    sql = 'SELECT * FROM orders WHERE id > 1'
    found = [s for s in complete(sql, len(sql), POSTGRES, catalog()) if s.kind is Kind.KEYWORD]
    text, caret = apply_suggestion(sql, found[0])
    assert text == f'SELECT * FROM orders WHERE id > 1 {found[0].text}'
    assert caret == len(text)


def test_a_keyword_is_not_glued_onto_a_string_literal() -> None:
    """The same rule, with a closing quote rather than a digit before the caret."""
    sql = "SELECT * FROM auth_user WHERE username = 'x'"
    found = [s for s in complete(sql, len(sql), POSTGRES, catalog()) if s.kind is Kind.KEYWORD]
    assert apply_suggestion(sql, found[0])[0] == f'{sql} {found[0].text}'


def test_a_separator_is_not_invented_where_one_would_break_the_text() -> None:
    """A dot, an open paren and a space all already separate; only a name-to-name join needs help."""
    assert accept_first('SELECT * FROM auth_user u WHERE u.')[0].startswith('SELECT * FROM auth_user u WHERE u.')
    assert accept_first('SELECT count(')[0].startswith('SELECT count(')


def test_accepting_a_namespace_continues_the_reference() -> None:
    """
    A schema is never the end of a relation reference, so it brings its own dot.

    `FROM public⌶` accepting `public` gives `public.`, with the caret past the
    dot and the next level ready to complete. Leaving the caret on `public`
    means the author types a separator the engine already knew was coming — and
    in a three-level namespace it means a reference that looks finished and is
    not.
    """
    sql = 'SELECT * FROM pub'
    schema = Suggestion(text='public', kind=Kind.SCHEMA, replace_span=(14, 17), score=1.0)
    assert apply_suggestion(sql, schema) == ('SELECT * FROM public.', 21)


def test_accepting_a_relation_does_not() -> None:
    """The counterpart: a table finishes the reference, and a trailing dot would not parse."""
    sql = 'SELECT * FROM ord'
    table = Suggestion(text='orders', kind=Kind.TABLE, replace_span=(14, 17), score=1.0)
    assert apply_suggestion(sql, table) == ('SELECT * FROM orders', 20)


def test_a_namespace_already_followed_by_a_dot_gains_no_second_one() -> None:
    """Re-accepting inside `public.` must not produce `public..`."""
    sql = 'SELECT * FROM pub.orders'
    schema = Suggestion(text='public', kind=Kind.SCHEMA, replace_span=(14, 17), score=1.0)
    assert apply_suggestion(sql, schema) == ('SELECT * FROM public.orders', 21)
