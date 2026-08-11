"""Case folding, quoting, and identifiers outside ASCII."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import USER_COLUMNS, suggestions, texts


def test_quoted_cte_name(cur: MemoryCatalog) -> None:
    """A quoted CTE name."""
    sql = 'WITH "My CTE" AS (SELECT id FROM auth_user)\nSELECT * FROM "My CTE" WHERE "My CTE".'
    assert texts(cur, sql) == ['id']


def test_cte_quoted_output_name_is_requoted(cur: MemoryCatalog) -> None:
    """A quoted output name is quoted again on the way out."""
    sql = 'WITH a AS (SELECT id AS "Foo Bar" FROM auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['"Foo Bar"']


def test_uppercase_cte_keywords(cur: MemoryCatalog) -> None:
    """Uppercase keywords around a CTE."""
    sql = 'WITH A AS (SELECT ID, EMAIL FROM AUTH_USER) SELECT * FROM A WHERE A.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_uppercase_plain_table_columns(cur: MemoryCatalog) -> None:
    """An uppercase query over a plain table."""
    assert sorted(texts(cur, 'SELECT * FROM AUTH_USER U WHERE U.')) == sorted(USER_COLUMNS)


def test_uppercase_unqualified_columns(cur: MemoryCatalog) -> None:
    """Uppercase, with no qualifier."""
    got = texts(cur, 'SELECT * FROM AUTH_GROUP WHERE ', limit=50)
    assert sorted(got) == ['auth_group.id', 'auth_group.name']


def test_mixed_case_table_qualifier(cur: MemoryCatalog) -> None:
    """A mixed-case qualifier folds to the name the catalog holds."""
    assert sorted(texts(cur, 'select * from Auth_User where AUTH_user.')) == sorted(USER_COLUMNS)


def test_uppercase_schema_qualified(cur: MemoryCatalog) -> None:
    """Uppercase and schema-qualified."""
    sql = 'SELECT * FROM BILLING.INVOICES WHERE INVOICES.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_quoted_identifier_keeps_its_case(cur: MemoryCatalog) -> None:
    """A quoted identifier keeps the case it was written in."""
    sql = 'WITH a AS (SELECT id AS "MixedCase" FROM auth_user) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['"MixedCase"']


def test_keyword_casing_still_follows_what_was_typed(cur: MemoryCatalog) -> None:
    """Keyword case follows the document."""
    lower = suggestions(cur, 'select * from auth_user wh')
    assert apply_suggestion('select * from auth_user wh', lower[0])[0].endswith('where')
    upper = suggestions(cur, 'SELECT * FROM auth_user WH')
    assert apply_suggestion('SELECT * FROM auth_user WH', upper[0])[0].endswith('WHERE')


def test_cyrillic_cte_and_columns(cur: MemoryCatalog) -> None:
    """Non-ASCII names, throughout."""
    sql = 'WITH отчёт AS (SELECT id AS Номер, email FROM auth_user)\nSELECT * FROM отчёт WHERE отчёт.'
    assert sorted(texts(cur, sql)) == sorted(['номер', 'email'])


def test_cyrillic_cte_name_offered_in_from(cur: MemoryCatalog) -> None:
    """And offered in FROM."""
    sql = 'WITH отчёт AS (SELECT id FROM auth_user)\nSELECT * FROM отч'
    assert texts(cur, sql) == ['отчёт']
