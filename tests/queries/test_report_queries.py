"""One real report query, read from several positions."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, REPORT_SQL, suggestions, texts


def test_report_placeholder_does_not_break_scope(cur: MemoryCatalog) -> None:
    """A report macro in a value position breaks nothing."""
    sql = 'SELECT * FROM auth_user WHERE date_joined > %Дата|ДАТА|% AND '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_report_placeholder_mentioning_from(cur: MemoryCatalog) -> None:
    """Even one whose text contains FROM."""
    sql = 'SELECT * FROM auth_user WHERE username = %Кто|СТРОКА|% AND u'
    assert 'auth_user.username' in texts(cur, sql, limit=50)


def test_report_query_first_cte_columns(cur: MemoryCatalog) -> None:
    """A real report query: the first CTE's columns."""
    assert texts(cur, REPORT_SQL + 'a.') == ['id', 'email', 'date_joined']


def test_report_query_second_cte_columns(cur: MemoryCatalog) -> None:
    """The second CTE's."""
    assert texts(cur, REPORT_SQL + 's.') == ['user_id', 'итого', 'штук']


def test_report_query_unqualified_scope(cur: MemoryCatalog) -> None:
    """Unqualified, in its outer query."""
    assert sorted(texts(cur, REPORT_SQL, limit=50)) == [
        'a.date_joined',
        'a.email',
        'a.id',
        's.user_id',
        's.итого',
        's.штук',
    ]


def test_report_query_cte_name_completion(cur: MemoryCatalog) -> None:
    """And its CTE names."""
    sql = REPORT_SQL[: REPORT_SQL.rindex('FROM активные a')] + 'FROM ак'
    assert [(s.text, s.kind.value) for s in suggestions(cur, sql, limit=5)] == [('активные', 'cte')]


def test_report_query_inside_the_second_cte_body(cur: MemoryCatalog) -> None:
    """Inside the second CTE's body."""
    head = REPORT_SQL[: REPORT_SQL.index('     GROUP BY o.user_id')]
    assert sorted(texts(cur, head + '     WHERE o.', limit=50)) == ALL_ORDER_COLUMNS
