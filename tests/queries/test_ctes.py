"""Common table expressions: what they declare, and where it is visible."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import CTE_SQL, USER_COLUMNS, analyze, kinds, suggestions, texts


def test_cte_star_qualified_columns(cur: MemoryCatalog) -> None:
    """A qualified reference inside a CTE."""
    assert sorted(texts(cur, CTE_SQL)) == sorted(USER_COLUMNS)


def test_cte_columns_are_reported_as_columns(cur: MemoryCatalog) -> None:
    """A CTE's outputs are columns, not some kind of their own."""
    assert set(kinds(cur, CTE_SQL).values()) == {'column'}


def test_cte_detail_mentions_the_cte(cur: MemoryCatalog) -> None:
    """The detail names the CTE the column came from."""
    detail = {s.text: s.detail or '' for s in suggestions(cur, CTE_SQL)}
    assert detail['email'].startswith('a.email')


def test_cte_explicit_select_list(cur: MemoryCatalog) -> None:
    """A CTE with an explicit select list."""
    sql = 'WITH a as (select id, email as mail from auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'mail']


def test_cte_declared_column_list_wins(cur: MemoryCatalog) -> None:
    """A declared column list renames the body's outputs."""
    sql = 'WITH a(x, y) as (select id, email from auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['x', 'y']


def test_cte_qualified_star_in_body(cur: MemoryCatalog) -> None:
    """A qualified star in the body expands against its relation."""
    sql = 'WITH a as (select u.* from auth_user u join orders o on o.user_id = u.id)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cte_referenced_through_an_alias(cur: MemoryCatalog) -> None:
    """A CTE reached through an alias."""
    sql = 'WITH a as (select * from auth_user)\nSELECT * FROM a xx WHERE xx.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_recursive_cte(cur: MemoryCatalog) -> None:
    """A RECURSIVE CTE."""
    sql = 'WITH RECURSIVE t as (select * from auth_group)\nSELECT * FROM t WHERE t.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_materialized_cte(cur: MemoryCatalog) -> None:
    """AS MATERIALIZED does not hide the body."""
    sql = 'WITH a as materialized (select * from auth_group)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_not_materialized_cte(cur: MemoryCatalog) -> None:
    """AS NOT MATERIALIZED does not either."""
    sql = 'WITH a as not materialized (select * from auth_group)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_second_cte_selecting_from_the_first(cur: MemoryCatalog) -> None:
    """A second CTE reading the first."""
    sql = 'WITH a as (select id, email from auth_user), b as (select * from a)\nSELECT * FROM b WHERE b.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_two_ctes_stay_separate(cur: MemoryCatalog) -> None:
    """Two independent CTEs keep separate scopes."""
    sql = 'WITH a as (select * from auth_user), b as (select * from orders)\nSELECT * FROM b WHERE b.'
    assert sorted(texts(cur, sql)) == ['created', 'id', 'total', 'user_id']


def test_cte_over_schema_qualified_table(cur: MemoryCatalog) -> None:
    """A CTE over a schema-qualified table."""
    sql = 'WITH a as (select * from billing.invoices)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_cte_with_expression_alias(cur: MemoryCatalog) -> None:
    """An aliased expression is an output name."""
    sql = 'WITH a as (select count(*) as n, total * 2 as double from orders)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['double', 'n']


def test_cte_implicit_alias(cur: MemoryCatalog) -> None:
    """So is an alias written without AS."""
    sql = 'WITH a as (select count(*) n, u.id x from auth_user u)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['n', 'x']


def test_cte_unaliased_expression_is_not_invented(cur: MemoryCatalog) -> None:
    """An unaliased expression names nothing, and nothing is invented for it."""
    sql = 'WITH a as (select id, total > 1 from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_cte_boolean_expression_tail_is_not_a_column(cur: MemoryCatalog) -> None:
    """A boolean expression is not a column either."""
    sql = 'WITH a as (select id, is_staff and is_staff from auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_cte_bare_function_takes_its_own_name(cur: MemoryCatalog) -> None:
    """A bare call is named after its function."""
    sql = 'WITH a as (select count(*) from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['count']


def test_cte_set_operation_uses_first_branch(cur: MemoryCatalog) -> None:
    """A set operation takes its output names from the first branch."""
    sql = 'WITH a as (select id from auth_user union all select id from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_cte_distinct_on(cur: MemoryCatalog) -> None:
    """DISTINCT ON qualifies the select, and does not consume the item after it."""
    sql = 'WITH a as (select distinct on (id) id, total from orders)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'total']


def test_cte_distinct(cur: MemoryCatalog) -> None:
    """Nor does a plain DISTINCT."""
    sql = 'WITH a as (select distinct id from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_self_referencing_cte_terminates(cur: MemoryCatalog) -> None:
    """A self-referencing CTE terminates rather than recursing forever."""
    sql = 'WITH RECURSIVE a as (select * from a)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == []


def test_mutually_recursive_ctes_terminate(cur: MemoryCatalog) -> None:
    """So do two that refer to each other."""
    sql = 'WITH a as (select * from b), b as (select * from a)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == []


def test_cte_name_offered_in_from(cur: MemoryCatalog) -> None:
    """The CTE's own name, offered in FROM."""
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM '
    assert 'totals' in texts(cur, sql)


def test_cte_name_kind_is_cte(cur: MemoryCatalog) -> None:
    """And it is a CTE rather than a table, so a UI can say so."""
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM tot'
    assert kinds(cur, sql)['totals'] == 'cte'


def test_cte_name_offered_after_join(cur: MemoryCatalog) -> None:
    """Offered after JOIN as well."""
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM auth_user JOIN tot'
    assert 'totals' in texts(cur, sql)


def test_inner_relation_does_not_leak_outward(cur: MemoryCatalog) -> None:
    """A CTE body's relations stay inside it."""
    sql = 'WITH a as (select id from auth_user)\nSELECT * FROM a WHERE '
    assert texts(cur, sql) == ['a.id']


def test_unknown_qualifier_still_falls_back_to_catalog(cur: MemoryCatalog) -> None:
    """A qualifier naming no CTE is read as a relation the catalog knows."""
    sql = 'WITH a as (select id from auth_user)\nSELECT * FROM a WHERE auth_user.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cursor_inside_cte_body_sees_body_relations(cur: MemoryCatalog) -> None:
    """Inside the body, the body's own FROM is what is in scope."""
    sql = 'WITH a as (select * from auth_user u where u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cursor_inside_cte_body_unqualified(cur: MemoryCatalog) -> None:
    """Unqualified, in the same position."""
    sql = 'WITH a as (select * from auth_user where '
    assert sorted(texts(cur, sql)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_outer_scope_after_two_ctes(cur: MemoryCatalog) -> None:
    """After the WITH, only what the outer FROM names."""
    sql = 'WITH a as (select id from auth_user), b as (select total from orders)\nSELECT * FROM b WHERE '
    assert texts(cur, sql) == ['b.total']


def test_cte_analyze_replace_from(cur: MemoryCatalog) -> None:
    """The same for a CTE reference."""
    ctx = analyze(CTE_SQL + 'em')
    assert ctx.replace_from == len(CTE_SQL)
    assert ctx.prefix == 'em'


def test_nested_cte_inside_a_cte_body(cur: MemoryCatalog) -> None:
    """A WITH inside a CTE body."""
    sql = (
        'WITH outer_q AS (\n'
        '    WITH inner_q AS (SELECT id, email FROM auth_user)\n'
        '    SELECT * FROM inner_q\n'
        ')\n'
        'SELECT * FROM outer_q WHERE outer_q.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_joined_with_a_real_table(cur: MemoryCatalog) -> None:
    """A CTE joined to a catalog relation puts both in scope."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a JOIN orders o ON o.user_id = a.id WHERE '
    got = texts(cur, sql, limit=50)
    assert sorted(got) == ['a.email', 'a.id', 'o.created', 'o.id', 'o.total', 'o.user_id']


def test_cte_two_qualified_stars_dedupe(cur: MemoryCatalog) -> None:
    """Two qualified stars over the same relation name it once."""
    sql = 'WITH a AS (SELECT u.*, o.* FROM auth_user u JOIN orders o ON true)\nSELECT * FROM a WHERE a.'
    got = texts(cur, sql, limit=50)
    assert len(got) == len(set(got)), 'duplicate column names'
    assert sorted(got) == sorted(['id', 'username', 'email', 'is_staff', 'date_joined', 'user_id', 'total', 'created'])


def test_cte_shadows_a_real_table(cur: MemoryCatalog) -> None:
    """A CTE shadows a catalog relation of the same name."""
    sql = 'WITH orders AS (SELECT id, email FROM auth_user)\nSELECT * FROM orders WHERE orders.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_with_window_function(cur: MemoryCatalog) -> None:
    """A window function in the body."""
    sql = 'WITH a AS (SELECT id, row_number() OVER (PARTITION BY email) AS rn FROM auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'rn']


def test_cte_with_order_by_and_limit(cur: MemoryCatalog) -> None:
    """ORDER BY and LIMIT in the body do not disturb its outputs."""
    sql = 'WITH a AS (SELECT id FROM auth_user ORDER BY id LIMIT 10)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_update_from_cte(cur: MemoryCatalog) -> None:
    """UPDATE ... FROM a CTE."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nUPDATE orders SET total = 1 FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_from_a_previous_statement_is_not_visible(cur: MemoryCatalog) -> None:
    """Including the CTEs declared before it."""
    sql = 'WITH a AS (SELECT id FROM auth_user) SELECT * FROM a;\nSELECT * FROM orders WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_insert_select_from_cte(cur: MemoryCatalog) -> None:
    """INSERT INTO ... SELECT from a CTE."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nINSERT INTO orders (user_id) SELECT a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_apply_suggestion_on_a_cte_column(cur: MemoryCatalog) -> None:
    """Applying a CTE column keeps the qualifier that was typed."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.em'
    suggestion = suggestions(cur, sql)[0]
    new_sql, caret = apply_suggestion(sql, suggestion)
    assert new_sql.endswith('WHERE a.email')
    assert caret == len(new_sql)


def test_cte_chain_three_deep(cur: MemoryCatalog) -> None:
    """Three CTEs, each reading the last."""
    sql = (
        'WITH a AS (SELECT id, email FROM auth_user), '
        'b AS (SELECT * FROM a), c AS (SELECT * FROM b)\n'
        'SELECT * FROM c WHERE c.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_referenced_twice_under_two_aliases(cur: MemoryCatalog) -> None:
    """One CTE under two aliases is two relations."""
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a x JOIN a y ON x.id = y.id WHERE y.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_columns_in_group_by(cur: MemoryCatalog) -> None:
    """A CTE's columns in GROUP BY."""
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a GROUP BY '
    assert sorted(texts(cur, sql, limit=50)) == ['a.total', 'id']


def test_cte_columns_in_having(cur: MemoryCatalog) -> None:
    """In HAVING."""
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a GROUP BY id HAVING '
    assert sorted(texts(cur, sql, limit=50)) == ['a.total', 'id']


def test_cte_columns_in_order_by(cur: MemoryCatalog) -> None:
    """In ORDER BY."""
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a ORDER BY '
    assert sorted(texts(cur, sql, limit=50)) == ['a.total', 'id']


def test_cte_columns_in_join_on(cur: MemoryCatalog) -> None:
    """And in a JOIN's ON."""
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT * FROM a JOIN auth_user u ON u.id = a.'
    assert sorted(texts(cur, sql)) == ['id', 'total']


def test_keywords_offered_after_a_cte_relation(cur: MemoryCatalog) -> None:
    """Once a relation is named, what may follow it is offered too."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a '
    assert 'WHERE' in texts(cur, sql, limit=50)


def test_values_cte_with_declared_columns(cur: MemoryCatalog) -> None:
    """A VALUES CTE with a declared column list."""
    sql = 'WITH t(a, b) AS (VALUES (1, 2), (3, 4))\nSELECT * FROM t WHERE t.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


def test_values_cte_without_declared_columns_invents_nothing(cur: MemoryCatalog) -> None:
    """Without one there is nothing to name, and nothing is invented."""
    sql = 'WITH t AS (VALUES (1, 2))\nSELECT * FROM t WHERE t.'
    assert texts(cur, sql) == []


def test_recursive_cte_with_search_clause(cur: MemoryCatalog) -> None:
    """A RECURSIVE CTE with a SEARCH clause."""
    sql = (
        'WITH RECURSIVE t(n) AS (\n'
        '  SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5\n'
        ') SEARCH DEPTH FIRST BY n SET ordercol\n'
        'SELECT * FROM t WHERE t.'
    )
    assert texts(cur, sql) == ['n']


def test_schema_qualified_name_does_not_resolve_to_a_cte(cur: MemoryCatalog) -> None:
    """A schema-qualified name is not a CTE reference."""
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM public.a WHERE a.'
    assert texts(cur, sql) == []


def test_cte_name_completion_can_be_applied(cur: MemoryCatalog) -> None:
    """A CTE name can be applied like any other relation."""
    sql = 'WITH totals AS (SELECT id FROM orders)\nSELECT * FROM tot'
    suggestion = suggestions(cur, sql)[0]
    new_sql, caret = apply_suggestion(sql, suggestion)
    assert new_sql.endswith('FROM totals')
    assert caret == len(new_sql)


def test_cte_body_spanning_blank_lines_and_indentation(cur: MemoryCatalog) -> None:
    """A body spread over blank lines and indentation."""
    sql = (
        'WITH a\n\n   AS\n\n   (\n\n   SELECT id,\n\n          email\n\n'
        '     FROM auth_user\n\n   )\n\nSELECT * FROM a WHERE a.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_over_a_function_source(cur: MemoryCatalog) -> None:
    """A CTE over a function source."""
    sql = 'WITH a AS (SELECT id FROM generate_series(1, 3) g, auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']
