"""Text the lexer has to get right before anything else can: literals, comments, parameters."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import USER_COLUMNS, texts


def test_open_string_literal_offers_nothing(cur: MemoryCatalog) -> None:
    """
    Inside a literal the author is writing data, not SQL. Offering an identifier
    there inserts it into the string, which is worse than offering nothing.
    """
    assert texts(cur, "select * from auth_user where email = 'abc") == []


def test_cte_body_with_comment_mentioning_another_table(cur: MemoryCatalog) -> None:
    """
    A comment is not part of the query. Scanning for clause keywords without
    skipping comments finds a FROM that the database will never see.
    """
    sql = 'WITH a AS ( -- careful: FROM orders in a comment\n    SELECT id FROM auth_user\n) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_string_literal_mentioning_sql_does_not_add_relations(cur: MemoryCatalog) -> None:
    """
    The same trap in a string. Both are why the lexer runs before any analysis
    rather than the analysis pattern-matching over raw text.
    """
    sql = "SELECT * FROM auth_user WHERE email = 'select * from orders' AND "
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_placeholder_inside_a_cte(cur: MemoryCatalog) -> None:
    """
    A report macro inside a CTE body, where a mis-lex costs the whole projection
    rather than one suggestion.
    """
    sql = 'WITH a AS (SELECT id, email FROM auth_user WHERE id = %Ид|ЧИСЛО|%)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_psycopg_named_parameter(cur: MemoryCatalog) -> None:
    """
    `%(user_id)s` is a driver parameter, and the `%` and parentheses are exactly
    the characters a lexer might read as an operator and a group.
    """
    sql = 'SELECT * FROM auth_user WHERE id = %(user_id)s AND '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_psycopg_positional_parameter(cur: MemoryCatalog) -> None:
    """
    The shorter form, where `%s` sits where a value belongs and must not be read
    as an identifier.
    """
    sql = 'SELECT * FROM auth_user WHERE id = %s AND '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_numbered_parameter_is_not_a_dollar_quote(cur: MemoryCatalog) -> None:
    """
    `$1` and `$$...$$` start the same way. Reading a numbered parameter as an
    opening dollar quote swallows the rest of the statement, and two of them
    look convincingly like a matched pair.
    """
    sql = 'SELECT * FROM auth_user WHERE id = $1 AND username = $2 AND '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_dollar_quoted_string_with_an_apostrophe(cur: MemoryCatalog) -> None:
    """
    The reason dollar quoting exists: an apostrophe inside it is data. A lexer
    that tracks single quotes independently opens a literal that never closes.
    """
    sql = "SELECT $$it's fine$$ FROM auth_user WHERE "
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_tagged_dollar_quote(cur: MemoryCatalog) -> None:
    """
    A tagged body containing a whole SELECT. Only the matching tag ends it, so a
    scanner looking for the next `$$` stops in the wrong place.
    """
    sql = 'SELECT $body$ select * from orders $body$ FROM auth_user WHERE '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_unterminated_dollar_quote_offers_nothing(cur: MemoryCatalog) -> None:
    """
    Unterminated is the state every dollar quote passes through while being
    typed. The caret is inside a literal, and nothing belongs there.
    """
    assert texts(cur, 'SELECT * FROM auth_user WHERE x = $$open') == []


def test_dollar_quote_inside_a_cte(cur: MemoryCatalog) -> None:
    """
    Both traps at once: a tagged-free body with an apostrophe, inside a CTE whose
    projection depends on the lexer getting it right.
    """
    sql = "WITH a AS (SELECT id, $$x'y$$ AS note FROM auth_user)\nSELECT * FROM a WHERE a."
    assert sorted(texts(cur, sql)) == ['id', 'note']


def test_json_operator_then_column(cur: MemoryCatalog) -> None:
    """
    `->>` is three operator characters in a row, one of which is `>`. Splitting
    it differently leaves a stray comparison and a predicate that never closes.
    """
    sql = "SELECT * FROM auth_user WHERE data->>'k' = 'v' AND "
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_cast_before_a_qualified_column(cur: MemoryCatalog) -> None:
    """
    `::` is also two characters that mean something else apart, and it sits
    directly between an identifier and a type name — the position where a
    mis-read turns a column reference into two.
    """
    sql = "SELECT * FROM auth_user u WHERE u.id::text = '1' AND u."
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)
