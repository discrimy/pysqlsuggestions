"""Text the lexer has to get right before anything else can: literals, comments, parameters."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import USER_COLUMNS, texts


def test_open_string_literal_offers_nothing(cur: MemoryCatalog) -> None:
    """An open string literal is not a place for an identifier."""
    assert texts(cur, "select * from auth_user where email = 'abc") == []


def test_cte_body_with_comment_mentioning_another_table(cur: MemoryCatalog) -> None:
    """A comment naming another table adds nothing to scope."""
    sql = 'WITH a AS ( -- careful: FROM orders in a comment\n    SELECT id FROM auth_user\n) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_string_literal_mentioning_sql_does_not_add_relations(cur: MemoryCatalog) -> None:
    """Neither does SQL inside a string literal."""
    sql = "SELECT * FROM auth_user WHERE email = 'select * from orders' AND "
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_placeholder_inside_a_cte(cur: MemoryCatalog) -> None:
    """Or one inside a CTE body."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user WHERE id = %Ид|ЧИСЛО|%)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_psycopg_named_parameter(cur: MemoryCatalog) -> None:
    """A psycopg named parameter."""
    sql = 'SELECT * FROM auth_user WHERE id = %(user_id)s AND '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_psycopg_positional_parameter(cur: MemoryCatalog) -> None:
    """A positional one."""
    sql = 'SELECT * FROM auth_user WHERE id = %s AND '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_numbered_parameter_is_not_a_dollar_quote(cur: MemoryCatalog) -> None:
    """`$1` is a parameter, not the start of a dollar quote."""
    sql = 'SELECT * FROM auth_user WHERE id = $1 AND username = $2 AND '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_dollar_quoted_string_with_an_apostrophe(cur: MemoryCatalog) -> None:
    """A dollar-quoted body containing an apostrophe."""
    sql = "SELECT $$it's fine$$ FROM auth_user WHERE "
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_tagged_dollar_quote(cur: MemoryCatalog) -> None:
    """A tagged dollar quote."""
    sql = 'SELECT $body$ select * from orders $body$ FROM auth_user WHERE '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_unterminated_dollar_quote_offers_nothing(cur: MemoryCatalog) -> None:
    """An unterminated one swallows the rest, and offers nothing."""
    assert texts(cur, 'SELECT * FROM auth_user WHERE x = $$open') == []


def test_dollar_quote_inside_a_cte(cur: MemoryCatalog) -> None:
    """A dollar quote inside a CTE body."""
    sql = "WITH a AS (SELECT id, $$x'y$$ AS note FROM auth_user)\nSELECT * FROM a WHERE a."
    assert sorted(texts(cur, sql)) == ['id', 'note']


def test_json_operator_then_column(cur: MemoryCatalog) -> None:
    """A column after a JSON operator."""
    sql = "SELECT * FROM auth_user WHERE data->>'k' = 'v' AND "
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_cast_before_a_qualified_column(cur: MemoryCatalog) -> None:
    """A qualified column after a cast."""
    sql = "SELECT * FROM auth_user u WHERE u.id::text = '1' AND u."
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)
