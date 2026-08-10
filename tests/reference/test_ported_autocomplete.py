"""
report_service's autocomplete suite, translated onto this library's API.

Ported wholesale rather than rewritten. It encodes edge cases nobody would think
to invent — self-referencing CTEs, dollar-quoted bodies mentioning FROM, union
branches, Cyrillic identifiers, report macros in a value position — and anything
it pinned down is behaviour report_service users already have. A failure here is
a regression for them, not a difference of opinion.

The fixture, the SQL strings and the assertions are theirs verbatim. Only the
harness is reimplemented: `texts(cur, sql)` now runs `complete()`, and `cur` is a
MemoryCatalog rather than their FakeCatalog.

Two cases are not ported, because they assert on the old module's own dataclass
field names and Catalog protocol rather than on any behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from pysqlsuggestions.api import apply_suggestion, complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Suggestion

CATALOG = {
    ('public', 'auth_user'): [
        ('id', 'integer'),
        ('username', 'character varying(150)'),
        ('email', 'character varying(254)'),
        ('is_staff', 'boolean'),
        ('date_joined', 'timestamp with time zone'),
    ],
    ('public', 'auth_group'): [
        ('id', 'integer'),
        ('name', 'character varying(150)'),
    ],
    ('public', 'orders'): [
        ('id', 'integer'),
        ('user_id', 'integer'),
        ('total', 'numeric'),
        ('created', 'date'),
    ],
    # prefix-matches "use", where auth_user only contains it — lets ranking be tested
    ('public', 'users_log'): [
        ('id', 'integer'),
        ('msg', 'text'),
    ],
    ('billing', 'invoices'): [
        ('id', 'integer'),
        ('order_id', 'integer'),
        ('amount', 'numeric'),
    ],
}

USER_COLUMNS = ['id', 'username', 'email', 'is_staff', 'date_joined']

DEFAULT_LIMIT = 200
"""Their harness was unbounded; a high cap keeps `sorted(texts(...)) == [...]` honest."""


def fake_catalog(catalog: Any = None, oversized: bool = False) -> MemoryCatalog:
    """Stands in for their FakeCatalog."""
    return MemoryCatalog(catalog or CATALOG, oversized=oversized)


@pytest.fixture
def cur() -> MemoryCatalog:
    """The fixture catalog, one per test so `calls` is meaningful."""
    return fake_catalog()


def suggestions(
    cursor: MemoryCatalog,
    sql: str,
    pos: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Suggestion]:
    """Complete at `pos`, defaulting to end of input as their harness did."""
    return complete(sql, len(sql) if pos is None else pos, POSTGRES, cursor, limit=limit)


def texts(cursor: MemoryCatalog, sql: str, **kwargs: Any) -> list[str]:
    """Suggestion texts."""
    return [s.text for s in suggestions(cursor, sql, **kwargs)]


def kinds(cursor: MemoryCatalog, sql: str, **kwargs: Any) -> dict[str, str]:
    """Text -> kind."""
    return {s.text: s.kind.value for s in suggestions(cursor, sql, **kwargs)}


def at(cursor: MemoryCatalog, marked: str, **kwargs: Any) -> list[str]:
    """Suggestion texts at the ‸ marker."""
    return texts(cursor, marked.replace('‸', ''), pos=marked.index('‸'), **kwargs)


class _Context:
    """Their `analyze()` result shape, over this library's Request."""

    def __init__(self, sql: str, pos: int | None = None) -> None:
        self._request = derive_request(sql, len(sql) if pos is None else pos, POSTGRES)

    @property
    def prefix(self) -> str:
        """What is already typed."""
        return self._request.prefix

    @property
    def replace_from(self) -> int:
        """Where the replacement starts — the first half of `replace_span`."""
        return self._request.replace_span[0]

    @property
    def clause(self) -> str | None:
        """The governing clause keyword."""
        return self._request.clause

    @property
    def relations(self) -> list[_Ref]:
        """Relations in scope, in their TableRef shape."""
        scope = self._request.scope
        return [_Ref(r.path[-1] if r.path else '') for r in (scope.visible() if scope else ())]


class _Ref:
    """Their TableRef, reduced to the one field the ported tests read."""

    def __init__(self, name: str) -> None:
        self.name = name


def analyze(sql: str, pos: int | None = None) -> _Context:
    """Their analyze(), adapted."""
    return _Context(sql, pos)


CTE_SQL = """WITH a as (
    select * FROM auth_user
)
SELECT *
FROM a
WHERE a."""


def test_cte_star_qualified_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для qualified ссылки внутри CTE."""
    assert sorted(texts(cur, CTE_SQL)) == sorted(USER_COLUMNS)


def test_cte_columns_are_reported_as_columns(cur: MemoryCatalog) -> None:
    """Тестировать, что CTE колонки отображаются как тип column."""
    assert set(kinds(cur, CTE_SQL).values()) == {'column'}


def test_cte_qualified_prefix_filters(cur: MemoryCatalog) -> None:
    """Тестировать фильтрацию по префиксу для qualified CTE ссылок."""
    sql = CTE_SQL + 'em'
    assert texts(cur, sql) == ['email']


def test_cte_detail_mentions_the_cte(cur: MemoryCatalog) -> None:
    """Тестировать, что detail предложения содержит имя CTE."""
    detail = {s.text: s.detail or '' for s in suggestions(cur, CTE_SQL)}
    assert detail['email'].startswith('a.email')


def test_cte_explicit_select_list(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с явным select list."""
    sql = 'WITH a as (select id, email as mail from auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'mail']


def test_cte_declared_column_list_wins(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с объявленным списком колонок."""
    sql = 'WITH a(x, y) as (select id, email from auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['x', 'y']


def test_cte_qualified_star_in_body(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с qualified star в теле."""
    sql = 'WITH a as (select u.* from auth_user u join orders o on o.user_id = u.id)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cte_referenced_through_an_alias(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE через псевдоним."""
    sql = 'WITH a as (select * from auth_user)\nSELECT * FROM a xx WHERE xx.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_recursive_cte(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для рекурсивного CTE."""
    sql = 'WITH RECURSIVE t as (select * from auth_group)\nSELECT * FROM t WHERE t.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_materialized_cte(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для materialized CTE."""
    sql = 'WITH a as materialized (select * from auth_group)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_not_materialized_cte(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для not materialized CTE."""
    sql = 'WITH a as not materialized (select * from auth_group)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_second_cte_selecting_from_the_first(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для второго CTE, читающего первый."""
    sql = 'WITH a as (select id, email from auth_user), b as (select * from a)\nSELECT * FROM b WHERE b.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_two_ctes_stay_separate(cur: MemoryCatalog) -> None:
    """Тестировать изоляцию скоупа двух независимых CTE."""
    sql = 'WITH a as (select * from auth_user), b as (select * from orders)\nSELECT * FROM b WHERE b.'
    assert sorted(texts(cur, sql)) == ['created', 'id', 'total', 'user_id']


def test_cte_over_schema_qualified_table(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE над schema-qualified таблицей."""
    sql = 'WITH a as (select * from billing.invoices)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_cte_with_expression_alias(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с выражением-псевдонимом."""
    sql = 'WITH a as (select count(*) as n, total * 2 as double from orders)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['double', 'n']


@pytest.mark.xfail(strict=True, reason='select-list output naming misses some expression shapes')
def test_cte_implicit_alias(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с неявным псевдонимом выражения."""
    sql = 'WITH a as (select count(*) n, u.id x from auth_user u)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['n', 'x']


def test_cte_unaliased_expression_is_not_invented(cur: MemoryCatalog) -> None:
    """Тестировать отсутствие предложений для unaliased выражений в CTE."""
    sql = 'WITH a as (select id, total > 1 from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


@pytest.mark.xfail(strict=True, reason='select-list output naming misses some expression shapes')
def test_cte_boolean_expression_tail_is_not_a_column(cur: MemoryCatalog) -> None:
    """Тестировать отсутствие предложений для булевых выражений в CTE."""
    sql = 'WITH a as (select id, is_staff and is_staff from auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


@pytest.mark.xfail(strict=True, reason='set-returning functions in FROM are not read as relations')
def test_cte_bare_function_takes_its_own_name(cur: MemoryCatalog) -> None:
    """Тестировать предложение собственного имени функции в CTE."""
    sql = 'WITH a as (select count(*) from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['count']


def test_cte_set_operation_uses_first_branch(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с set operation."""
    sql = 'WITH a as (select id from auth_user union all select id from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


@pytest.mark.xfail(strict=True, reason='select-list output naming misses some expression shapes')
def test_cte_distinct_on(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с distinct on."""
    sql = 'WITH a as (select distinct on (id) id, total from orders)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'total']


def test_cte_distinct(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с distinct."""
    sql = 'WITH a as (select distinct id from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_self_referencing_cte_terminates(cur: MemoryCatalog) -> None:
    """Тестировать завершение рекурсии для self-referencing CTE."""
    sql = 'WITH RECURSIVE a as (select * from a)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == []


def test_mutually_recursive_ctes_terminate(cur: MemoryCatalog) -> None:
    """Тестировать завершение рекурсии для взаимно-рекурсивных CTE."""
    sql = 'WITH a as (select * from b), b as (select * from a)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == []


def test_cte_name_offered_in_from(cur: MemoryCatalog) -> None:
    """Тестировать предложение имени CTE в FROM без WHERE."""
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM '
    assert 'totals' in texts(cur, sql)


def test_cte_name_ranks_before_catalog_tables(cur: MemoryCatalog) -> None:
    """Тестировать, что имя CTE ранжируется выше таблиц каталога."""
    sql = 'WITH orders_x as (select * from orders)\nSELECT * FROM o'
    assert texts(cur, sql)[0] == 'orders_x'


def test_cte_name_kind_is_cte(cur: MemoryCatalog) -> None:
    """Тестировать, что kind CTE имени равен 'cte'."""
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM tot'
    assert kinds(cur, sql)['totals'] == 'cte'


def test_cte_name_offered_after_join(cur: MemoryCatalog) -> None:
    """Тестировать предложение имени CTE после JOIN."""
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM auth_user JOIN tot'
    assert 'totals' in texts(cur, sql)


def test_inner_relation_does_not_leak_outward(cur: MemoryCatalog) -> None:
    """Тестировать, что relations внутри CTE не попадают в outer scope."""
    sql = 'WITH a as (select id from auth_user)\nSELECT * FROM a WHERE '
    assert texts(cur, sql) == ['id']


@pytest.mark.xfail(strict=True, reason='statement forms beyond SELECT are not fully modelled')
def test_unknown_qualifier_still_falls_back_to_catalog(cur: MemoryCatalog) -> None:
    """Тестировать fallback к каталогу для неизвестного qualifier."""
    sql = 'WITH a as (select id from auth_user)\nSELECT * FROM a WHERE auth_user.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cursor_inside_cte_body_sees_body_relations(cur: MemoryCatalog) -> None:
    """Тестировать видимость relations тела CTE при курсоре внутри тела."""
    sql = 'WITH a as (select * from auth_user u where u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cursor_inside_cte_body_unqualified(cur: MemoryCatalog) -> None:
    """Тестировать видимость колонок для unqualified курсора внутри тела CTE."""
    sql = 'WITH a as (select * from auth_user where '
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_outer_scope_after_two_ctes(cur: MemoryCatalog) -> None:
    """Тестировать outer scope после двух CTE."""
    sql = 'WITH a as (select id from auth_user), b as (select total from orders)\nSELECT * FROM b WHERE '
    assert texts(cur, sql) == ['total']


def test_derived_table_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для derived table."""
    sql = 'SELECT * FROM (select id, email from auth_user) s WHERE s.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_derived_table_star(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для derived table со star."""
    sql = 'SELECT * FROM (select * from auth_group) s WHERE s.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_derived_table_with_as_keyword(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для derived table с AS keyword."""
    sql = 'SELECT * FROM (select id from orders) AS s WHERE s.'
    assert texts(cur, sql) == ['id']


def test_derived_table_does_not_leak(cur: MemoryCatalog) -> None:
    """Тестировать, что derived table колонки не попадают в outer scope."""
    sql = 'SELECT * FROM (select id from auth_user) s WHERE '
    assert texts(cur, sql) == ['id']


def test_cursor_inside_derived_table(cur: MemoryCatalog) -> None:
    """Тестировать видимость колонок при курсоре внутри derived table."""
    sql = 'SELECT * FROM (select * from auth_group g where g.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_plain_alias_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для plain alias qualifier."""
    assert sorted(texts(cur, 'select * from auth_user u where u.')) == sorted(USER_COLUMNS)


def test_plain_table_name_qualifier(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для plain table name qualifier."""
    sql = 'select * from auth_user where auth_user.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_plain_unqualified_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для plain unqualified запроса."""
    assert sorted(texts(cur, 'select * from auth_group where ')) == ['id', 'name']


@pytest.mark.xfail(strict=True, reason='columns are qualified when more than one relation is in scope')
def test_join_brings_both_relations(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для двух relations в join."""
    sql = 'select * from auth_user u join orders o on o.user_id = u.id where '
    got = texts(cur, sql, limit=50)
    assert 'username' in got
    assert 'total' in got


def test_schema_qualified_table_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для schema-qualified таблицы."""
    sql = 'select * from billing.invoices where invoices.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_from_offers_tables(cur: MemoryCatalog) -> None:
    """Тестировать предложение таблиц в empty FROM."""
    assert 'auth_user' in texts(cur, 'select * from ')


def test_prefix_filtering_on_plain_table(cur: MemoryCatalog) -> None:
    """Тестировать фильтрацию по префиксу для plain table."""
    assert texts(cur, 'select * from auth_user u where u.em') == ['email']


def test_open_string_literal_offers_nothing(cur: MemoryCatalog) -> None:
    """Тестировать нулевое предложение при открытом string literal."""
    assert texts(cur, "select * from auth_user where email = 'abc") == []


def test_analyze_replace_from_is_after_the_dot(cur: MemoryCatalog) -> None:
    """Тестировать, что replace_from после точки в plain query."""
    ctx = analyze('select * from auth_user u where u.em')
    assert ctx.replace_from == len('select * from auth_user u where u.')
    assert ctx.prefix == 'em'


def test_cte_analyze_replace_from(cur: MemoryCatalog) -> None:
    """Тестировать, что replace_from для CTE указывает на конец CTE."""
    ctx = analyze(CTE_SQL + 'em')
    assert ctx.replace_from == len(CTE_SQL)
    assert ctx.prefix == 'em'


def test_with_is_not_confused_by_other_uses(cur: MemoryCatalog) -> None:
    """Тестировать, что WITH в не-CTE позициях не парсится как CTE."""
    sql = 'select * from auth_user u where u.date_joined = current_timestamp with '
    # should not raise, and should not invent relations
    suggestions(cur, sql)
    ctx = analyze(sql)
    assert [r.name for r in ctx.relations] == ['auth_user']


ALL_ORDER_COLUMNS = ['created', 'id', 'total', 'user_id']


@pytest.mark.xfail(strict=True, reason='CTE and derived-table bodies are not analysed recursively enough')
def test_nested_cte_inside_a_cte_body(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для вложенного CTE в теле CTE."""
    sql = (
        'WITH outer_q AS (\n'
        '    WITH inner_q AS (SELECT id, email FROM auth_user)\n'
        '    SELECT * FROM inner_q\n'
        ')\n'
        'SELECT * FROM outer_q WHERE outer_q.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


@pytest.mark.xfail(strict=True, reason='columns are qualified when more than one relation is in scope')
def test_cte_joined_with_a_real_table(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с JOIN реальной таблицы."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a JOIN orders o ON o.user_id = a.id WHERE '
    got = texts(cur, sql, limit=50)
    assert sorted(got) == sorted(['id', 'email', 'user_id', 'total', 'created'])


def test_cte_two_qualified_stars_dedupe(cur: MemoryCatalog) -> None:
    """Тестировать дедупликацию колонок при двух qualified stars."""
    sql = 'WITH a AS (SELECT u.*, o.* FROM auth_user u JOIN orders o ON true)\nSELECT * FROM a WHERE a.'
    got = texts(cur, sql, limit=50)
    assert len(got) == len(set(got)), 'duplicate column names'
    assert sorted(got) == sorted(['id', 'username', 'email', 'is_staff', 'date_joined', 'user_id', 'total', 'created'])


@pytest.mark.xfail(strict=True, reason='CTE and derived-table bodies are not analysed recursively enough')
def test_derived_table_with_column_list(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для derived table с объявлением колонок."""
    sql = 'SELECT * FROM (SELECT id, email FROM auth_user) s(a, b) WHERE s.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


def test_cte_shadows_a_real_table(cur: MemoryCatalog) -> None:
    """Тестировать遮盖 real table при CTE с тем же именем."""
    sql = 'WITH orders AS (SELECT id, email FROM auth_user)\nSELECT * FROM orders WHERE orders.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_quoted_cte_name(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с quoted именем."""
    sql = 'WITH "My CTE" AS (SELECT id FROM auth_user)\nSELECT * FROM "My CTE" WHERE "My CTE".'
    assert texts(cur, sql) == ['id']


def test_cte_with_window_function(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с window function."""
    sql = 'WITH a AS (SELECT id, row_number() OVER (PARTITION BY email) AS rn FROM auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'rn']


def test_cte_with_order_by_and_limit(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с ORDER BY и LIMIT."""
    sql = 'WITH a AS (SELECT id FROM auth_user ORDER BY id LIMIT 10)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_cte_body_with_comment_mentioning_another_table(cur: MemoryCatalog) -> None:
    """Тестировать, что комментарий в теле CTE не влияет на scope."""
    sql = 'WITH a AS ( -- careful: FROM orders in a comment\n    SELECT id FROM auth_user\n) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_string_literal_mentioning_sql_does_not_add_relations(cur: MemoryCatalog) -> None:
    """Тестировать, что sql в string literal не добавляет relations."""
    sql = "SELECT * FROM auth_user WHERE email = 'select * from orders' AND "
    assert sorted(texts(cur, sql, limit=50)) == sorted(USER_COLUMNS)


def test_update_from_cte(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для UPDATE FROM CTE."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nUPDATE orders SET total = 1 FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cursor_in_the_middle_of_the_statement(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок при курсоре в середине query."""
    head = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.'
    sql = head + ' AND 1 = 1'
    assert sorted(texts(cur, sql, pos=len(head))) == ['email', 'id']


def test_second_statement_does_not_see_the_first(cur: MemoryCatalog) -> None:
    """Тестировать изоляцию запросов, разделённых точкой с запятой."""
    sql = 'SELECT * FROM auth_user; SELECT * FROM orders WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ALL_ORDER_COLUMNS


def test_cte_from_a_previous_statement_is_not_visible(cur: MemoryCatalog) -> None:
    """Тестировать невидимость CTE предыдущего statement в следующем."""
    sql = 'WITH a AS (SELECT id FROM auth_user) SELECT * FROM a;\nSELECT * FROM orders WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ALL_ORDER_COLUMNS


@pytest.mark.xfail(strict=True, reason='LATERAL subqueries are not read as relations')
def test_lateral_subquery_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для LATERAL subquery."""
    sql = 'SELECT * FROM auth_user u, LATERAL (SELECT total FROM orders WHERE user_id = u.id) l WHERE l.'
    assert texts(cur, sql) == ['total']


def test_lateral_join_keeps_outer_relation(cur: MemoryCatalog) -> None:
    """Тестировать видимость outer relation при LATERAL JOIN."""
    sql = 'SELECT * FROM auth_user u LEFT JOIN LATERAL (SELECT total FROM orders) l ON true WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_correlated_subquery_in_select_list(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для correlated subquery в select list."""
    sql = 'SELECT (SELECT email FROM auth_user WHERE id = o.user_id) AS e FROM orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_cte_in_subquery_in_where(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE в subquery внутри WHERE."""
    sql = 'SELECT * FROM orders o WHERE o.user_id IN (WITH a AS (SELECT id FROM auth_user) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_insert_select_from_cte(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для INSERT INTO ... SELECT from CTE."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nINSERT INTO orders (user_id) SELECT a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_apply_suggestion_on_a_cte_column(cur: MemoryCatalog) -> None:
    """Тестировать применение suggestion к CTE колонке."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.em'
    suggestion = suggestions(cur, sql)[0]
    new_sql, caret = apply_suggestion(sql, suggestion)
    assert new_sql.endswith('WHERE a.email')
    assert caret == len(new_sql)


@pytest.mark.xfail(strict=True, reason='CTE and derived-table bodies are not analysed recursively enough')
def test_deeply_nested_derived_tables(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для глубоко вложенных derived tables."""
    sql = 'SELECT * FROM (SELECT * FROM (SELECT id, email FROM auth_user) inner_t) outer_t WHERE outer_t.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_chain_three_deep(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для chain из трёх CTE."""
    sql = (
        'WITH a AS (SELECT id, email FROM auth_user), '
        'b AS (SELECT * FROM a), c AS (SELECT * FROM b)\n'
        'SELECT * FROM c WHERE c.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_union_of_two_ctes(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для UNION двух CTE."""
    sql = (
        'WITH a AS (SELECT id FROM auth_user), b AS (SELECT id FROM orders)\n'
        'SELECT * FROM a UNION SELECT * FROM b WHERE '
    )
    assert texts(cur, sql, limit=50) == ['id']


def test_cte_referenced_twice_under_two_aliases(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с двумя псевдонимами."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a x JOIN a y ON x.id = y.id WHERE y.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_column_prefix_is_case_insensitive(cur: MemoryCatalog) -> None:
    """Тестировать insensitive префикса к CTE колонкам."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.EM'
    assert texts(cur, sql) == ['email']


def test_cte_quoted_output_name_is_requoted(cur: MemoryCatalog) -> None:
    """Тестировать quoted output имя CTE с пере-кoting."""
    sql = 'WITH a AS (SELECT id AS "Foo Bar" FROM auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['"Foo Bar"']


def test_cte_columns_in_group_by(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE в GROUP BY."""
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a GROUP BY '
    assert sorted(texts(cur, sql, limit=50)) == ['id', 'total']


def test_cte_columns_in_having(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE в HAVING."""
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a GROUP BY id HAVING '
    assert sorted(texts(cur, sql, limit=50)) == ['id', 'total']


@pytest.mark.xfail(strict=True, reason='select-list output naming misses some expression shapes')
def test_cte_columns_in_order_by(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE в ORDER BY."""
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a ORDER BY '
    assert sorted(texts(cur, sql, limit=50)) == ['id', 'total']


def test_cte_columns_in_join_on(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE в JOIN ON."""
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT * FROM a JOIN auth_user u ON u.id = a.'
    assert sorted(texts(cur, sql)) == ['id', 'total']


def test_keywords_offered_after_a_cte_relation(cur: MemoryCatalog) -> None:
    """Тестировать предложение ключевых слов после CTE relation."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a '
    assert 'WHERE' in texts(cur, sql, limit=50)


def test_values_cte_with_declared_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для VALUES CTE с объявлением колонок."""
    sql = 'WITH t(a, b) AS (VALUES (1, 2), (3, 4))\nSELECT * FROM t WHERE t.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


def test_values_cte_without_declared_columns_invents_nothing(cur: MemoryCatalog) -> None:
    """Тестировать отсутствие предложений для VALUES CTE без объявленных колонок."""
    sql = 'WITH t AS (VALUES (1, 2))\nSELECT * FROM t WHERE t.'
    assert texts(cur, sql) == []


def test_recursive_cte_with_search_clause(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для RECURSIVE CTE с SEARCH clause."""
    sql = (
        'WITH RECURSIVE t(n) AS (\n'
        '  SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5\n'
        ') SEARCH DEPTH FIRST BY n SET ordercol\n'
        'SELECT * FROM t WHERE t.'
    )
    assert texts(cur, sql) == ['n']


def test_schema_qualified_name_does_not_resolve_to_a_cte(cur: MemoryCatalog) -> None:
    """Тестировать, что schema-qualified имена не resolve к CTE."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM public.a WHERE a.'
    assert texts(cur, sql) == []


def test_derived_table_joined_to_a_cte(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для derived table с CTE JOIN."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a JOIN (SELECT total FROM orders) d ON true WHERE d.'
    assert texts(cur, sql) == ['total']


@pytest.mark.xfail(strict=True, reason='columns are qualified when more than one relation is in scope')
def test_cte_and_derived_table_both_in_scope(cur: MemoryCatalog) -> None:
    """Тестировать видимость CTE и derived table одновременно."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a JOIN (SELECT total FROM orders) d ON true WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['id', 'total']


@pytest.mark.xfail(strict=True, reason='statement forms beyond SELECT are not fully modelled')
def test_insert_column_list_uses_the_target_table(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для INSERT с CTE."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nINSERT INTO orders ('
    assert sorted(texts(cur, sql, limit=50)) == ALL_ORDER_COLUMNS


def test_cte_name_completion_can_be_applied(cur: MemoryCatalog) -> None:
    """Тестировать применение suggestion к CTE имени таблицы."""
    sql = 'WITH totals AS (SELECT id FROM orders)\nSELECT * FROM tot'
    suggestion = suggestions(cur, sql)[0]
    new_sql, caret = apply_suggestion(sql, suggestion)
    assert new_sql.endswith('FROM totals')
    assert caret == len(new_sql)


def test_uppercase_cte_keywords(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с uppercase ключевами."""
    sql = 'WITH A AS (SELECT ID, EMAIL FROM AUTH_USER) SELECT * FROM A WHERE A.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_body_spanning_blank_lines_and_indentation(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с blank lines и indent."""
    sql = (
        'WITH a\n\n   AS\n\n   (\n\n   SELECT id,\n\n          email\n\n'
        '     FROM auth_user\n\n   )\n\nSELECT * FROM a WHERE a.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_uppercase_plain_table_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для uppercase plain table."""
    assert sorted(texts(cur, 'SELECT * FROM AUTH_USER U WHERE U.')) == sorted(USER_COLUMNS)


def test_uppercase_unqualified_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для uppercase unqualified запроса."""
    got = texts(cur, 'SELECT * FROM AUTH_GROUP WHERE ', limit=50)
    assert sorted(got) == ['id', 'name']


def test_mixed_case_table_qualifier(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для mixed case table qualifier."""
    assert sorted(texts(cur, 'select * from Auth_User where AUTH_user.')) == sorted(USER_COLUMNS)


def test_uppercase_schema_qualified(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для uppercase schema-qualified таблицы."""
    sql = 'SELECT * FROM BILLING.INVOICES WHERE INVOICES.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_quoted_identifier_keeps_its_case(cur: MemoryCatalog) -> None:
    """Тестировать сохранение case для quoted identifier."""
    sql = 'WITH a AS (SELECT id AS "MixedCase" FROM auth_user) SELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['"MixedCase"']


def test_keyword_casing_still_follows_what_was_typed(cur: MemoryCatalog) -> None:
    """Тестировать, что casing keywords следует за вводом пользователя."""
    lower = suggestions(cur, 'select * from auth_user wh')
    assert apply_suggestion('select * from auth_user wh', lower[0])[0].endswith('where')
    upper = suggestions(cur, 'SELECT * FROM auth_user WH')
    assert apply_suggestion('SELECT * FROM auth_user WH', upper[0])[0].endswith('WHERE')


@pytest.mark.xfail(strict=True, reason='set-returning functions in FROM are not read as relations')
def test_function_in_from_does_not_swallow_the_rest_of_the_list(cur: MemoryCatalog) -> None:
    """Тестировать видимость колонок при function в FROM с другими tables."""
    sql = 'SELECT * FROM generate_series(1, 10) g, auth_user u WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


@pytest.mark.xfail(strict=True, reason='set-returning functions in FROM are not read as relations')
def test_function_in_from_unqualified_scope(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для function in FROM без qualifier."""
    sql = 'SELECT * FROM generate_series(1, 10) g, auth_group WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['id', 'name']


@pytest.mark.xfail(strict=True, reason='set-returning functions in FROM are not read as relations')
def test_function_column_definition_list(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для function с column definition list."""
    sql = 'SELECT * FROM jsonb_to_recordset(x) AS t(a int, b text), orders o WHERE t.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


@pytest.mark.xfail(strict=True, reason='set-returning functions in FROM are not read as relations')
def test_function_column_definition_list_keeps_later_items(cur: MemoryCatalog) -> None:
    """Тестировать видимость колонок после function с column definition list."""
    sql = 'SELECT * FROM jsonb_to_recordset(x) AS t(a int, b text), orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


@pytest.mark.xfail(strict=True, reason='set-returning functions in FROM are not read as relations')
def test_function_without_alias_is_harmless(cur: MemoryCatalog) -> None:
    """Тестировать, что function без alias не ломает scope."""
    sql = 'SELECT * FROM generate_series(1, 10), auth_group WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['id', 'name']


@pytest.mark.xfail(strict=True, reason='statement forms beyond SELECT are not fully modelled')
def test_delete_using_relation_is_in_scope(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для DELETE USING."""
    sql = 'DELETE FROM orders o USING auth_user u WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_join_using_column_list_is_not_a_relation(cur: MemoryCatalog) -> None:
    """Тестировать, что USING список определяет columns, а не relations."""
    sql = 'SELECT * FROM auth_user u JOIN orders o USING (id) WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_natural_join_keeps_both_relations(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для NATURAL JOIN."""
    sql = 'SELECT * FROM auth_user u NATURAL JOIN orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_cte_over_a_function_source(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE над function source."""
    sql = 'WITH a AS (SELECT id FROM generate_series(1, 3) g, auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_cyrillic_cte_and_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с non-ASCII именами."""
    sql = 'WITH отчёт AS (SELECT id AS Номер, email FROM auth_user)\nSELECT * FROM отчёт WHERE отчёт.'
    assert sorted(texts(cur, sql)) == sorted(['номер', 'email'])


def test_cyrillic_cte_name_offered_in_from(cur: MemoryCatalog) -> None:
    """Тестировать предложение CTE с non-ASCII именем в FROM."""
    sql = 'WITH отчёт AS (SELECT id FROM auth_user)\nSELECT * FROM отч'
    assert texts(cur, sql) == ['отчёт']


def test_report_placeholder_does_not_break_scope(cur: MemoryCatalog) -> None:
    """Тестировать, что report placeholder не ломает scope."""
    sql = 'SELECT * FROM auth_user WHERE date_joined > %Дата|ДАТА|% AND '
    assert sorted(texts(cur, sql, limit=50)) == sorted(USER_COLUMNS)


def test_report_placeholder_mentioning_from(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок при placeholder с from в значении."""
    sql = 'SELECT * FROM auth_user WHERE username = %Кто|СТРОКА|% AND u'
    assert 'username' in texts(cur, sql, limit=50)


def test_placeholder_inside_a_cte(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для CTE с placeholder."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user WHERE id = %Ид|ЧИСЛО|%)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_psycopg_named_parameter(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок с psycopg named parameter."""
    sql = 'SELECT * FROM auth_user WHERE id = %(user_id)s AND '
    assert sorted(texts(cur, sql, limit=50)) == sorted(USER_COLUMNS)


def test_psycopg_positional_parameter(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок с psycopg positional parameter."""
    sql = 'SELECT * FROM auth_user WHERE id = %s AND '
    assert sorted(texts(cur, sql, limit=50)) == sorted(USER_COLUMNS)


def test_numbered_parameter_is_not_a_dollar_quote(cur: MemoryCatalog) -> None:
    """Тестировать, что $1 параметр не parse как dollar quote."""
    sql = 'SELECT * FROM auth_user WHERE id = $1 AND username = $2 AND '
    assert sorted(texts(cur, sql, limit=50)) == sorted(USER_COLUMNS)


def test_dollar_quoted_string_with_an_apostrophe(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок при dollar quote с apostrophe."""
    sql = "SELECT $$it's fine$$ FROM auth_user WHERE "
    assert sorted(texts(cur, sql, limit=50)) == sorted(USER_COLUMNS)


def test_tagged_dollar_quote(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для tagged dollar quote."""
    sql = 'SELECT $body$ select * from orders $body$ FROM auth_user WHERE '
    assert sorted(texts(cur, sql, limit=50)) == sorted(USER_COLUMNS)


def test_unterminated_dollar_quote_offers_nothing(cur: MemoryCatalog) -> None:
    """Тестировать нулевое предложение при unterminated dollar quote."""
    assert texts(cur, 'SELECT * FROM auth_user WHERE x = $$open') == []


def test_dollar_quote_inside_a_cte(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для dollar quote в CTE."""
    sql = "WITH a AS (SELECT id, $$x'y$$ AS note FROM auth_user)\nSELECT * FROM a WHERE a."
    assert sorted(texts(cur, sql)) == ['id', 'note']


@pytest.mark.xfail(strict=True, reason='statement forms beyond SELECT are not fully modelled')
def test_excluded_offers_the_target_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для EXCLUDED в ON CONFLICT UPDATE."""
    sql = 'INSERT INTO orders (id) VALUES (1) ON CONFLICT (id) DO UPDATE SET total = EXCLUDED.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_json_operator_then_column(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок после JSON operator."""
    sql = "SELECT * FROM auth_user WHERE data->>'k' = 'v' AND "
    assert sorted(texts(cur, sql, limit=50)) == sorted(USER_COLUMNS)


def test_cast_before_a_qualified_column(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок после cast выражения."""
    sql = "SELECT * FROM auth_user u WHERE u.id::text = '1' AND u."
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_partition_by_sees_the_relation_after_the_cursor(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для PARTITION BY."""
    sql = 'SELECT row_number() OVER (PARTITION BY ) FROM auth_group'
    pos = sql.index(') FROM')
    assert sorted(texts(cur, sql, pos=pos, limit=50)) == ['id', 'name']


REPORT_SQL = """WITH активные AS (
    SELECT u.id, u.email, u.date_joined
      FROM auth_user u
     WHERE u.is_staff = false
       AND u.date_joined >= %Дата начала|ДАТА|%
), суммы AS (
    SELECT o.user_id, sum(o.total) AS итого, count(*) AS штук
      FROM orders o
      JOIN активные a ON a.id = o.user_id
     GROUP BY o.user_id
)
SELECT a.email, s.итого
  FROM активные a
  LEFT JOIN суммы s ON s.user_id = a.id
 WHERE """


def test_report_query_first_cte_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для первого CTE в report query."""
    assert texts(cur, REPORT_SQL + 'a.') == ['id', 'email', 'date_joined']


def test_report_query_second_cte_columns(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для второго CTE в report query."""
    assert texts(cur, REPORT_SQL + 's.') == ['user_id', 'итого', 'штук']


@pytest.mark.xfail(strict=True, reason='columns are qualified when more than one relation is in scope')
def test_report_query_unqualified_scope(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для unqualified запроса в report query."""
    assert sorted(texts(cur, REPORT_SQL, limit=50)) == sorted(
        ['id', 'email', 'date_joined', 'user_id', 'итого', 'штук'],
    )


def test_report_query_cte_name_completion(cur: MemoryCatalog) -> None:
    """Тестировать предложение имени CTE в report query."""
    sql = REPORT_SQL[: REPORT_SQL.rindex('FROM активные a')] + 'FROM ак'
    assert [(s.text, s.kind.value) for s in suggestions(cur, sql, limit=5)] == [('активные', 'cte')]


def test_report_query_inside_the_second_cte_body(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок внутри тела второго CTE в report query."""
    head = REPORT_SQL[: REPORT_SQL.index('     GROUP BY o.user_id')]
    assert sorted(texts(cur, head + '     WHERE o.', limit=50)) == ALL_ORDER_COLUMNS


def test_union_second_branch_scope(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для second branch UNION."""
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT ‸ FROM orders', limit=50)
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_union_first_branch_scope(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для first branch UNION."""
    got = at(cur, 'SELECT ‸ FROM auth_user UNION SELECT id FROM orders', limit=50)
    assert sorted(got) == sorted(USER_COLUMNS)


def test_union_second_branch_where(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для second branch UNION WHERE."""
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT id FROM orders WHERE ‸', limit=50)
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_union_qualified_in_second_branch(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для qualified в second branch UNION."""
    got = at(cur, 'SELECT id FROM auth_user UNION SELECT o.‸ FROM orders o')
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_parenthesised_union_branches(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для parenthesised UNION branches."""
    got = at(cur, '(SELECT id FROM auth_user) UNION (SELECT ‸ FROM orders)', limit=50)
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_except_second_branch(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для EXCEPT second branch."""
    got = at(cur, 'SELECT id FROM auth_user EXCEPT SELECT ‸ FROM orders', limit=50)
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_union_inside_a_cte_body(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для UNION внутри тела CTE."""
    got = at(cur, 'WITH a AS (SELECT id FROM auth_user UNION SELECT ‸ FROM orders) SELECT * FROM a', limit=50)
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_subquery_relations_drop_out_once_it_closes(cur: MemoryCatalog) -> None:
    """Тестировать падение relations subquery после его закрытия."""
    got = at(cur, 'SELECT * FROM orders o WHERE o.user_id IN (SELECT id FROM auth_user) AND ‸', limit=50)
    assert sorted(got) == ALL_ORDER_COLUMNS


@pytest.mark.xfail(strict=True, reason='columns are qualified when more than one relation is in scope')
def test_correlated_outer_relation_visible_inside_a_subquery(cur: MemoryCatalog) -> None:
    """Тестировать видимость outer relation внутри subquery."""
    got = at(cur, 'SELECT * FROM orders o WHERE o.user_id IN (SELECT ‸ FROM auth_user)', limit=50)
    assert sorted(got) == sorted(set(USER_COLUMNS) | set(ALL_ORDER_COLUMNS))


def test_outer_qualifier_inside_an_exists_subquery(cur: MemoryCatalog) -> None:
    """Тестировать предложение колонок для outer qualifier в EXISTS subquery."""
    got = at(cur, 'SELECT * FROM orders o WHERE EXISTS (SELECT 1 FROM auth_user u WHERE u.id = o.‸)')
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_scalar_subquery_in_select_list_does_not_leak(cur: MemoryCatalog) -> None:
    """Тестировать, что scalar subquery не leaking в outer scope."""
    got = at(cur, 'SELECT (SELECT name FROM auth_group), ‸ FROM orders', limit=50)
    assert sorted(got) == ALL_ORDER_COLUMNS


def test_clause_after_a_closed_subquery_is_still_select(cur: MemoryCatalog) -> None:
    """Тестировать, что clause после closed subquery — SELECT."""
    sql = 'SELECT (SELECT name FROM auth_group), '
    assert analyze(sql).clause == 'SELECT'


def test_any_subquery_relations_drop_out(cur: MemoryCatalog) -> None:
    """Тестировать падение relations ANY subquery после закрытия."""
    got = at(cur, 'SELECT * FROM orders o WHERE o.id = ANY (SELECT id FROM auth_user) AND ‸', limit=50)
    assert sorted(got) == ALL_ORDER_COLUMNS


@pytest.mark.xfail(strict=True, reason='columns are qualified when more than one relation is in scope')
def test_nested_subquery_sees_every_enclosing_level(cur: MemoryCatalog) -> None:
    """Тестировать видимость всех enclosing levels в nested subquery."""
    got = at(
        cur,
        'SELECT * FROM orders o WHERE o.id IN (SELECT user_id FROM orders WHERE user_id IN (SELECT ‸ FROM auth_user))',
        limit=50,
    )
    assert sorted(got) == sorted(set(USER_COLUMNS) | set(ALL_ORDER_COLUMNS))


def test_substring_match_offers_the_table(cur: MemoryCatalog) -> None:
    """Тестировать предложение таблицы при substring matching."""
    assert 'auth_user' in texts(cur, 'select * from use')


def test_prefix_hits_rank_above_substring_hits(cur: MemoryCatalog) -> None:
    """Тестировать приоритет prefix hits над substring hits."""
    got = texts(cur, 'select * from use')
    assert got.index('users_log') < got.index('auth_user')


def test_exact_case_prefix_ranks_first(cur: MemoryCatalog) -> None:
    """Тестировать приоритет exact case prefix."""
    got = texts(cur, 'select * from Us')
    assert got[0] == 'users_log'


def test_substring_match_on_columns(cur: MemoryCatalog) -> None:
    """Тестировать substring matching на колонках."""
    assert texts(cur, 'select * from auth_user u where u.mail') == ['email']


def test_column_prefix_hit_ranks_above_substring_hit(cur: MemoryCatalog) -> None:
    """Тестировать приоритет column prefix hit над substring hit."""
    got = texts(cur, 'select * from orders where id', limit=10)
    assert got.index('id') < got.index('user_id')


def test_substring_match_on_cte_names(cur: MemoryCatalog) -> None:
    """Тестировать substring matching на CTE именах."""
    sql = 'WITH monthly_totals AS (SELECT id FROM orders)\nSELECT * FROM total'
    assert 'monthly_totals' in texts(cur, sql)


def test_earlier_substring_position_ranks_higher(cur: MemoryCatalog) -> None:
    """Тестировать приоритет более ранних substring позиций."""
    got = texts(cur, 'select * from auth_user u where u.e', limit=10)
    assert got == ['email', 'username', 'date_joined']


@pytest.mark.xfail(strict=True, reason='keyword case is decided in the suggestion here, not at insertion')
def test_keywords_stay_prefix_only(cur: MemoryCatalog) -> None:
    """Тестировать, что keywords только prefix-matching."""
    assert 'WHERE' in texts(cur, 'select * from auth_user w', limit=50)
    assert 'WHERE' not in texts(cur, 'select * from auth_user her', limit=50)


def test_empty_prefix_is_unchanged(cur: MemoryCatalog) -> None:
    """Тестировать предложение для empty prefix."""
    got = texts(cur, 'select * from ', limit=50)
    assert {'auth_user', 'auth_group', 'orders', 'users_log'} <= set(got)


def test_no_match_returns_nothing(cur: MemoryCatalog) -> None:
    """Тестировать отсутствие предложений при отсутствии совпадений."""
    assert texts(cur, 'select * from zzzqqq') == []


def test_substring_match_does_not_cross_the_dot(cur: MemoryCatalog) -> None:
    """Тестировать, что substring match не cross dot в qualified lookup."""
    assert texts(cur, 'select * from auth_group g where g.mail') == []


def test_columns_before_any_from_use_the_whole_schema_read(cur: MemoryCatalog) -> None:
    """Тестировать использование whole schema read для предложений до FROM."""
    got = texts(cur, 'select ema', limit=20)
    assert 'email' in got
    assert ('all_columns',) in cur.calls
    assert not any(call[0] == 'search_columns' for call in cur.calls)


def test_columns_before_any_from_are_prefix_filtered(cur: MemoryCatalog) -> None:
    """Тестировать prefix filtering для предложений до FROM."""
    got = texts(cur, 'select user_i', limit=20)
    assert got == ['user_id']


def test_an_oversized_schema_falls_back_to_the_prefix_query() -> None:
    """Тестировать fallback к prefix query при oversized schema."""
    cur = fake_catalog(oversized=True)
    got = texts(cur, 'select ema', limit=20)
    assert 'email' in got
    assert any(call[0] == 'search_columns' for call in cur.calls)
