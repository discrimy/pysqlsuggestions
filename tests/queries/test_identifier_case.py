"""Case folding, quoting, and identifiers outside ASCII."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import USER_COLUMNS, suggestions, texts


def test_quoted_cte_name(cur: MemoryCatalog) -> None:
    """
    Quoting is how SQL says "this is a name, not syntax", and a name with a space
    in it can be written no other way. The declaration, the reference and the
    qualifier all have to agree on what was quoted.
    """
    sql = 'WITH "My CTE" AS (SELECT id FROM auth_user)\nSELECT * FROM "My CTE" WHERE "My CTE".'
    assert texts(cur, sql) == ['id']


def test_cte_quoted_output_name_is_requoted(cur: MemoryCatalog) -> None:
    """
    `Foo Bar` cannot be written bare, so it comes back quoted. Losing the quotes
    on the way out produces a suggestion that does not parse when inserted.
    """
    sql = 'WITH a AS (SELECT id AS "Foo Bar" FROM auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['"Foo Bar"']


def test_uppercase_cte_keywords(cur: MemoryCatalog) -> None:
    """
    Keywords and identifiers fold together in Postgres, so a query typed entirely
    in caps has to resolve exactly as the lowercase one does — including the CTE
    name, which the statement declared rather than the catalog.
    """
    sql = 'WITH A AS (SELECT ID, EMAIL FROM AUTH_USER) SELECT * FROM A WHERE A.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_uppercase_plain_table_columns(cur: MemoryCatalog) -> None:
    """
    The catalog holds `auth_user` in lower case and the author typed `AUTH_USER`.
    Matching the two is folding, not luck.
    """
    assert sorted(texts(cur, 'SELECT * FROM AUTH_USER U WHERE U.')) == sorted(USER_COLUMNS)


def test_uppercase_unqualified_columns(cur: MemoryCatalog) -> None:
    """
    And the qualifier this engine adds comes from the catalog's spelling rather
    than the author's, so the suggestion is insertable as written.
    """
    got = texts(cur, 'SELECT * FROM AUTH_GROUP WHERE ', limit=50)
    assert sorted(got) == ['auth_group.id', 'auth_group.name']


def test_mixed_case_table_qualifier(cur: MemoryCatalog) -> None:
    """
    The relation and the qualifier are spelled differently from each other and
    from the catalog. All three fold to the same name, which is the only reason
    unquoted SQL works at all.
    """
    assert sorted(texts(cur, 'select * from Auth_User where AUTH_user.')) == sorted(USER_COLUMNS)


def test_uppercase_schema_qualified(cur: MemoryCatalog) -> None:
    """Folding applies to every segment of a path, not just the last one."""
    sql = 'SELECT * FROM BILLING.INVOICES WHERE INVOICES.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_quoted_identifier_keeps_its_case(cur: MemoryCatalog) -> None:
    """
    The counterpart to folding: quoting turns it off. `"MixedCase"` is a
    different identifier from `mixedcase`, and treating them alike would offer a
    name the database does not have.
    """
    sql = 'WITH a AS (SELECT id AS "MixedCase" FROM auth_user) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['"MixedCase"']


def test_keyword_casing_still_follows_what_was_typed(cur: MemoryCatalog) -> None:
    """
    A completion should not restyle the document it lands in. The case comes from
    what the author has been writing, so the same keystroke gives `where` in one
    query and `WHERE` in the other.
    """
    lower = suggestions(cur, 'select * from auth_user wh')
    assert apply_suggestion('select * from auth_user wh', lower[0])[0].endswith('where')
    upper = suggestions(cur, 'SELECT * FROM auth_user WH')
    assert apply_suggestion('SELECT * FROM auth_user WH', upper[0])[0].endswith('WHERE')


def test_cyrillic_cte_and_columns(cur: MemoryCatalog) -> None:
    """
    Identifiers outside ASCII are ordinary identifiers, including for folding —
    `Номер` arrives as `номер`. A character-class written for ASCII silently
    excludes most of the world's schemas.
    """
    sql = 'WITH отчёт AS (SELECT id AS Номер, email FROM auth_user)\nSELECT * FROM отчёт WHERE отчёт.'
    assert sorted(texts(cur, sql)) == sorted(['номер', 'email'])


def test_cyrillic_cte_name_offered_in_from(cur: MemoryCatalog) -> None:
    """
    And prefix matching works on them too, which needs folding and matching to
    agree about what a letter is.
    """
    sql = 'WITH отчёт AS (SELECT id FROM auth_user)\nSELECT * FROM отч'
    assert texts(cur, sql) == ['отчёт']
