"""One real report query, read from several positions."""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import ALL_ORDER_COLUMNS, REPORT_SQL, suggestions, texts


def test_report_placeholder_does_not_break_scope(cur: MemoryCatalog) -> None:
    """
    Report macros are not SQL, and the lexer meets them anyway. `%Дата|ДАТА|%`
    has to lex as something inert — pipes and non-ASCII and all — rather than
    derailing the scan that finds the FROM clause.
    """
    sql = 'SELECT * FROM auth_user WHERE date_joined > %Дата|ДАТА|% AND '
    assert sorted(texts(cur, sql, limit=50)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_report_placeholder_mentioning_from(cur: MemoryCatalog) -> None:
    """
    The worst case for the previous rule: a macro whose text contains a clause
    keyword. Anything that reads inside it finds a FROM that is not there.
    """
    sql = 'SELECT * FROM auth_user WHERE username = %Кто|СТРОКА|% AND u'
    assert 'auth_user.username' in texts(cur, sql, limit=50)


def test_report_query_first_cte_columns(cur: MemoryCatalog) -> None:
    """
    A whole report as its author wrote it — two CTEs, an aggregate, non-ASCII
    aliases, several hundred characters. The pieces are tested separately; this
    is the check that they compose.
    """
    assert texts(cur, REPORT_SQL + 'a.') == ['id', 'email', 'date_joined']


def test_report_query_second_cte_columns(cur: MemoryCatalog) -> None:
    """
    The second CTE's outputs are an aggregate and a count under Cyrillic aliases,
    which is where output naming and identifier folding meet.
    """
    assert texts(cur, REPORT_SQL + 's.') == ['user_id', 'итого', 'штук']


def test_report_query_unqualified_scope(cur: MemoryCatalog) -> None:
    """
    Both CTEs in view at once, qualified because two relations are. This is the
    shape an author actually types into, and the one worth being sure of.
    """
    assert sorted(texts(cur, REPORT_SQL, limit=50)) == [
        'a.date_joined',
        'a.email',
        'a.id',
        's.user_id',
        's.итого',
        's.штук',
    ]


def test_report_query_cte_name_completion(cur: MemoryCatalog) -> None:
    """
    A CTE is not a table, and here it is offered as one — reached by a prefix of
    a Cyrillic name, which exercises folding and matching together.
    """
    sql = REPORT_SQL[: REPORT_SQL.rindex('FROM активные a')] + 'FROM ак'
    assert [(s.text, s.kind.value) for s in suggestions(cur, sql, limit=5)] == [('активные', 'cte')]


def test_report_query_inside_the_second_cte_body(cur: MemoryCatalog) -> None:
    """
    Inside a body, the outer CTEs are out of view and the body's own FROM is
    what answers. Truncating a real query mid-clause is also the state an editor
    is in most of the time.
    """
    head = REPORT_SQL[: REPORT_SQL.index('     GROUP BY o.user_id')]
    assert sorted(texts(cur, head + '     WHERE o.', limit=50)) == ALL_ORDER_COLUMNS
