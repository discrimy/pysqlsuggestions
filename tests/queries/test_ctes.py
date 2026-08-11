"""Common table expressions: what they declare, and where it is visible."""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from tests.queries.harness import CTE_SQL, USER_COLUMNS, analyze, kinds, suggestions, texts


def test_cte_star_qualified_columns(cur: MemoryCatalog) -> None:
    """
    A CTE is a relation the statement described rather than one the catalog
    holds, and a `*` in its body defers to a relation the catalog does know.
    Answering this needs both, joined by the projection carried through analysis.
    """
    assert sorted(texts(cur, CTE_SQL)) == sorted(USER_COLUMNS)


def test_cte_columns_are_reported_as_columns(cur: MemoryCatalog) -> None:
    """
    They come from a different place than catalog columns and must arrive looking
    the same. A kind of their own would leak the implementation into every UI.
    """
    assert set(kinds(cur, CTE_SQL).values()) == {'column'}


def test_cte_detail_mentions_the_cte(cur: MemoryCatalog) -> None:
    """
    The detail says where a column came from, and for a CTE that is the CTE — not
    the table underneath, which the author may not have looked at.
    """
    detail = {s.text: s.detail or '' for s in suggestions(cur, CTE_SQL)}
    assert detail['email'].startswith('a.email')


def test_cte_explicit_select_list(cur: MemoryCatalog) -> None:
    """
    An explicit list is the simple case, and `email as mail` is the reason the
    body cannot simply be forwarded: the outer query knows only `mail`.
    """
    sql = 'WITH a as (select id, email as mail from auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'mail']


def test_cte_declared_column_list_wins(cur: MemoryCatalog) -> None:
    """
    `a(x, y)` renames everything the body produced. Preferring the body's own
    names offers `id` and `email`, which no longer exist by the time anyone can
    reference them.
    """
    sql = 'WITH a(x, y) as (select id, email from auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['x', 'y']


def test_cte_qualified_star_in_body(cur: MemoryCatalog) -> None:
    """
    `u.*` expands to one relation of a join rather than both. Expanding it to
    everything in scope would put `orders`' columns behind a name that has none.
    """
    sql = 'WITH a as (select u.* from auth_user u join orders o on o.user_id = u.id)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cte_referenced_through_an_alias(cur: MemoryCatalog) -> None:
    """A CTE can be aliased like any relation, and then answers only to the alias."""
    sql = 'WITH a as (select * from auth_user)\nSELECT * FROM a xx WHERE xx.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_recursive_cte(cur: MemoryCatalog) -> None:
    """
    RECURSIVE changes what is visible inside the body, not what the CTE offers
    outside it. A path that special-cases the keyword can easily lose the
    ordinary answer.
    """
    sql = 'WITH RECURSIVE t as (select * from auth_group)\nSELECT * FROM t WHERE t.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_materialized_cte(cur: MemoryCatalog) -> None:
    """
    `MATERIALIZED` sits between `AS` and the parenthesis, which is exactly where
    a reader scanning for `AS (` stops looking.
    """
    sql = 'WITH a as materialized (select * from auth_group)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_not_materialized_cte(cur: MemoryCatalog) -> None:
    """And `NOT MATERIALIZED` puts two words there instead of one."""
    sql = 'WITH a as not materialized (select * from auth_group)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'name']


def test_second_cte_selecting_from_the_first(cur: MemoryCatalog) -> None:
    """
    A CTE reading an earlier one has no catalog entry to fall back on: its
    projection can only come from a projection already worked out.
    """
    sql = 'WITH a as (select id, email from auth_user), b as (select * from a)\nSELECT * FROM b WHERE b.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_two_ctes_stay_separate(cur: MemoryCatalog) -> None:
    """
    Two CTEs are two relations, and `b.` must not collect `a`'s columns — the
    failure that a single shared projection would produce.
    """
    sql = 'WITH a as (select * from auth_user), b as (select * from orders)\nSELECT * FROM b WHERE b.'
    assert sorted(texts(cur, sql)) == ['created', 'id', 'total', 'user_id']


def test_cte_over_schema_qualified_table(cur: MemoryCatalog) -> None:
    """
    The body's relation is a two-segment path, which the projection has to carry
    intact for the catalog read to find anything.
    """
    sql = 'WITH a as (select * from billing.invoices)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_cte_with_expression_alias(cur: MemoryCatalog) -> None:
    """
    `AS n` names an aggregate and `AS double` names an arithmetic expression.
    Neither is a column of anything, and both are referenceable afterwards.
    """
    sql = 'WITH a as (select count(*) as n, total * 2 as double from orders)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['double', 'n']


def test_cte_implicit_alias(cur: MemoryCatalog) -> None:
    """
    The same without `AS`. What separates `count(*) n` from an expression is the
    token before the trailing name: an alias follows a finished operand.
    """
    sql = 'WITH a as (select count(*) n, u.id x from auth_user u)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['n', 'x']


def test_cte_unaliased_expression_is_not_invented(cur: MemoryCatalog) -> None:
    """
    `total > 1` has no name, and Postgres calls it `?column?`. Inventing one
    offers an identifier that cannot be used.
    """
    sql = 'WITH a as (select id, total > 1 from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_cte_boolean_expression_tail_is_not_a_column(cur: MemoryCatalog) -> None:
    """
    The trap in the same rule: the last token here *is* a column name, but it is
    an operand of the expression rather than an alias for it.
    """
    sql = 'WITH a as (select id, is_staff and is_staff from auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_cte_bare_function_takes_its_own_name(cur: MemoryCatalog) -> None:
    """
    Postgres names a bare call after its function, so `count(*)` is reachable as
    `count`. Offering nothing would be as wrong as inventing something.
    """
    sql = 'WITH a as (select count(*) from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['count']


def test_cte_set_operation_uses_first_branch(cur: MemoryCatalog) -> None:
    """
    A set operation takes its output names from the first branch, and the second
    contributes none however it is spelled.
    """
    sql = 'WITH a as (select id from auth_user union all select id from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_cte_distinct_on(cur: MemoryCatalog) -> None:
    """
    `DISTINCT ON (id)` qualifies the select and then the list begins. Consuming
    the parenthesised expression as the first item loses `id`.
    """
    sql = 'WITH a as (select distinct on (id) id, total from orders)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'total']


def test_cte_distinct(cur: MemoryCatalog) -> None:
    """
    A plain `DISTINCT` is one word in the same position, and must not be read as
    the first select item either.
    """
    sql = 'WITH a as (select distinct id from orders)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_self_referencing_cte_terminates(cur: MemoryCatalog) -> None:
    """
    `a` reading `a` is a cycle. Following it produces no answer and never
    returns, which in an editor is a hang rather than a wrong suggestion.
    """
    sql = 'WITH RECURSIVE a as (select * from a)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == []


def test_mutually_recursive_ctes_terminate(cur: MemoryCatalog) -> None:
    """The same cycle through two names, which a guard keyed on a single name misses."""
    sql = 'WITH a as (select * from b), b as (select * from a)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == []


def test_cte_name_offered_in_from(cur: MemoryCatalog) -> None:
    """A CTE is a relation, so it belongs among the relations offered in a FROM."""
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM '
    assert 'totals' in texts(cur, sql)


def test_cte_name_kind_is_cte(cur: MemoryCatalog) -> None:
    """
    But it is distinguishable from a table, so a UI can say which it is — and it
    lives nowhere the catalog can confirm.
    """
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM tot'
    assert kinds(cur, sql)['totals'] == 'cte'


def test_cte_name_offered_after_join(cur: MemoryCatalog) -> None:
    """
    And after a join, which is a second relation position and a separate chance
    to forget the statement's own declarations.
    """
    sql = 'WITH totals as (select * from orders)\nSELECT * FROM auth_user JOIN tot'
    assert 'totals' in texts(cur, sql)


def test_inner_relation_does_not_leak_outward(cur: MemoryCatalog) -> None:
    """
    The body's `auth_user` is not in the outer scope; only `a` is. Leaking it
    offers columns through a name the outer query cannot use.
    """
    sql = 'WITH a as (select id from auth_user)\nSELECT * FROM a WHERE '
    assert texts(cur, sql) == ['a.id']


def test_unknown_qualifier_still_falls_back_to_catalog(cur: MemoryCatalog) -> None:
    """
    A qualifier naming no CTE and no alias is still a name the catalog might
    know. Stopping at the scope lookup makes a valid reference answer nothing.
    """
    sql = 'WITH a as (select id from auth_user)\nSELECT * FROM a WHERE auth_user.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cursor_inside_cte_body_sees_body_relations(cur: MemoryCatalog) -> None:
    """
    Inside a body, its own FROM is in scope — with the parenthesis still open,
    which is how every CTE looks while it is being written.
    """
    sql = 'WITH a as (select * from auth_user u where u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_cursor_inside_cte_body_unqualified(cur: MemoryCatalog) -> None:
    """
    The same position without a qualifier, where the body's relation has to be
    found rather than resolved against.
    """
    sql = 'WITH a as (select * from auth_user where '
    assert sorted(texts(cur, sql)) == [
        'auth_user.date_joined',
        'auth_user.email',
        'auth_user.id',
        'auth_user.is_staff',
        'auth_user.username',
    ]


def test_outer_scope_after_two_ctes(cur: MemoryCatalog) -> None:
    """
    Declaring two does not put both in view. Only the one the FROM names is
    referenceable, and a scope holding every declaration would offer both.
    """
    sql = 'WITH a as (select id from auth_user), b as (select total from orders)\nSELECT * FROM b WHERE '
    assert texts(cur, sql) == ['b.total']


def test_cte_analyze_replace_from(cur: MemoryCatalog) -> None:
    """
    The span covers what was typed after the dot and nothing else, so accepting a
    suggestion keeps the qualifier the author wrote.
    """
    ctx = analyze(CTE_SQL + 'em')
    assert ctx.replace_from == len(CTE_SQL)
    assert ctx.prefix == 'em'


def test_nested_cte_inside_a_cte_body(cur: MemoryCatalog) -> None:
    """
    A WITH inside a WITH. The inner declaration is invisible from outside and
    essential from inside, so reading CTEs once per statement gets it wrong.
    """
    sql = (
        'WITH outer_q AS (\n'
        '    WITH inner_q AS (SELECT id, email FROM auth_user)\n'
        '    SELECT * FROM inner_q\n'
        ')\n'
        'SELECT * FROM outer_q WHERE outer_q.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_joined_with_a_real_table(cur: MemoryCatalog) -> None:
    """
    One relation from the statement and one from the catalog, in the same FROM.
    The two sources have to end up in one scope.
    """
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a JOIN orders o ON o.user_id = a.id WHERE '
    got = texts(cur, sql, limit=50)
    assert sorted(got) == ['a.email', 'a.id', 'o.created', 'o.id', 'o.total', 'o.user_id']


def test_cte_two_qualified_stars_dedupe(cur: MemoryCatalog) -> None:
    """
    Two stars over the same relation name each column once. Concatenating the
    expansions offers every one of them twice.
    """
    sql = 'WITH a AS (SELECT u.*, o.* FROM auth_user u JOIN orders o ON true)\nSELECT * FROM a WHERE a.'
    got = texts(cur, sql, limit=50)
    assert len(got) == len(set(got)), 'duplicate column names'
    assert sorted(got) == sorted(['id', 'username', 'email', 'is_staff', 'date_joined', 'user_id', 'total', 'created'])


def test_cte_shadows_a_real_table(cur: MemoryCatalog) -> None:
    """
    A CTE named `orders` hides the table of that name for the whole statement.
    Resolution order decides this, and getting it backwards silently answers
    with the wrong relation's columns.
    """
    sql = 'WITH orders AS (SELECT id, email FROM auth_user)\nSELECT * FROM orders WHERE orders.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_with_window_function(cur: MemoryCatalog) -> None:
    """
    `OVER (PARTITION BY email)` is a parenthesised group inside a select item.
    Reading it as an item boundary loses `rn`, the only name the window has.
    """
    sql = 'WITH a AS (SELECT id, row_number() OVER (PARTITION BY email) AS rn FROM auth_user)\nSELECT * FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['id', 'rn']


def test_cte_with_order_by_and_limit(cur: MemoryCatalog) -> None:
    """
    Clauses after the select list contribute no outputs, and the search for the
    list's end has to stop at the first of them.
    """
    sql = 'WITH a AS (SELECT id FROM auth_user ORDER BY id LIMIT 10)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']


def test_update_from_cte(cur: MemoryCatalog) -> None:
    """
    A CTE feeding an UPDATE. The statement form is different and the scope rules
    are the same, which is only true if the clause model is not SELECT-shaped.
    """
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nUPDATE orders SET total = 1 FROM a WHERE a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_from_a_previous_statement_is_not_visible(cur: MemoryCatalog) -> None:
    """
    A declaration ends with its statement. Carrying it across the semicolon
    offers a relation the second query cannot reference.
    """
    sql = 'WITH a AS (SELECT id FROM auth_user) SELECT * FROM a;\nSELECT * FROM orders WHERE '
    assert sorted(texts(cur, sql, limit=50)) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_insert_select_from_cte(cur: MemoryCatalog) -> None:
    """
    A CTE feeding an INSERT ... SELECT, where the caret is in the select list of
    a statement that began with INSERT.
    """
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nINSERT INTO orders (user_id) SELECT a.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_apply_suggestion_on_a_cte_column(cur: MemoryCatalog) -> None:
    """
    The end of the round trip: a column found through a CTE has to insert as
    cleanly as one found through a table, qualifier intact.
    """
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a WHERE a.em'
    suggestion = suggestions(cur, sql)[0]
    new_sql, caret = apply_suggestion(sql, suggestion)
    assert new_sql.endswith('WHERE a.email')
    assert caret == len(new_sql)


def test_cte_chain_three_deep(cur: MemoryCatalog) -> None:
    """
    Each CTE's projection is built from the last. Two levels can pass by luck;
    three needs the resolution to actually be recursive.
    """
    sql = (
        'WITH a AS (SELECT id, email FROM auth_user), '
        'b AS (SELECT * FROM a), c AS (SELECT * FROM b)\n'
        'SELECT * FROM c WHERE c.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_referenced_twice_under_two_aliases(cur: MemoryCatalog) -> None:
    """
    One declaration, two relations. Keying scope by CTE name rather than by
    reference collapses them and loses one.
    """
    sql = 'WITH a AS (SELECT id, email FROM auth_user)\nSELECT * FROM a x JOIN a y ON x.id = y.id WHERE y.'
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_columns_in_group_by(cur: MemoryCatalog) -> None:
    """
    GROUP BY takes the select list's own outputs, which no catalog holds — and
    the relation's other columns as well, which come from the CTE.
    """
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a GROUP BY '
    assert sorted(texts(cur, sql, limit=50)) == ['a.total', 'id']


def test_cte_columns_in_having(cur: MemoryCatalog) -> None:
    """
    HAVING follows GROUP BY and offers the same, so a clause table listing one
    and not the other is a gap nobody notices.
    """
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a GROUP BY id HAVING '
    assert sorted(texts(cur, sql, limit=50)) == ['a.total', 'id']


def test_cte_columns_in_order_by(cur: MemoryCatalog) -> None:
    """
    And ORDER BY, which is the position where a select-list alias is most often
    what was meant.
    """
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT id FROM a ORDER BY '
    assert sorted(texts(cur, sql, limit=50)) == ['a.total', 'id']


def test_cte_columns_in_join_on(cur: MemoryCatalog) -> None:
    """
    An ON clause resolves qualifiers exactly as a WHERE does; it is simply a
    different clause, and clause handling is per-clause.
    """
    sql = 'WITH a AS (SELECT id, total FROM orders)\nSELECT * FROM a JOIN auth_user u ON u.id = a.'
    assert sorted(texts(cur, sql)) == ['id', 'total']


def test_keywords_offered_after_a_cte_relation(cur: MemoryCatalog) -> None:
    """
    Once a relation is named, what may follow it is worth offering — and a CTE
    reference is a relation for that purpose too.
    """
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM a '
    assert 'WHERE' in texts(cur, sql, limit=50)


def test_values_cte_with_declared_columns(cur: MemoryCatalog) -> None:
    """
    A VALUES CTE has no select list at all, so the declared column list is the
    only thing that can name its outputs.
    """
    sql = 'WITH t(a, b) AS (VALUES (1, 2), (3, 4))\nSELECT * FROM t WHERE t.'
    assert sorted(texts(cur, sql)) == ['a', 'b']


def test_values_cte_without_declared_columns_invents_nothing(cur: MemoryCatalog) -> None:
    """
    Without one, Postgres names them `column1`, `column2` — which this engine
    does not guess at. Offering nothing is the honest answer.
    """
    sql = 'WITH t AS (VALUES (1, 2))\nSELECT * FROM t WHERE t.'
    assert texts(cur, sql) == []


def test_recursive_cte_with_search_clause(cur: MemoryCatalog) -> None:
    """
    `SEARCH DEPTH FIRST BY n SET ordercol` sits between the body and the outer
    query, full of words that look like clauses. It must not be read as one.
    """
    sql = (
        'WITH RECURSIVE t(n) AS (\n'
        '  SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5\n'
        ') SEARCH DEPTH FIRST BY n SET ordercol\n'
        'SELECT * FROM t WHERE t.'
    )
    assert texts(cur, sql) == ['n']


def test_schema_qualified_name_does_not_resolve_to_a_cte(cur: MemoryCatalog) -> None:
    """
    `public.a` is a two-segment path and a CTE has no schema, so this names
    nothing. Matching the last segment against CTE names would resolve it.
    """
    sql = 'WITH a AS (SELECT id FROM auth_user)\nSELECT * FROM public.a WHERE a.'
    assert texts(cur, sql) == []


def test_cte_name_completion_can_be_applied(cur: MemoryCatalog) -> None:
    """
    A CTE name inserts like any other relation, which is the other half of
    offering it in a FROM position.
    """
    sql = 'WITH totals AS (SELECT id FROM orders)\nSELECT * FROM tot'
    suggestion = suggestions(cur, sql)[0]
    new_sql, caret = apply_suggestion(sql, suggestion)
    assert new_sql.endswith('FROM totals')
    assert caret == len(new_sql)


def test_cte_body_spanning_blank_lines_and_indentation(cur: MemoryCatalog) -> None:
    """
    Real SQL is formatted. Blank lines between every token is the extreme of it,
    and nothing about the parse may depend on whitespace.
    """
    sql = (
        'WITH a\n\n   AS\n\n   (\n\n   SELECT id,\n\n          email\n\n'
        '     FROM auth_user\n\n   )\n\nSELECT * FROM a WHERE a.'
    )
    assert sorted(texts(cur, sql)) == ['email', 'id']


def test_cte_over_a_function_source(cur: MemoryCatalog) -> None:
    """
    A function in the body's FROM, where a wrong reading loses the relation
    beside it and with it the projection this CTE offers.
    """
    sql = 'WITH a AS (SELECT id FROM generate_series(1, 3) g, auth_user)\nSELECT * FROM a WHERE a.'
    assert texts(cur, sql) == ['id']
