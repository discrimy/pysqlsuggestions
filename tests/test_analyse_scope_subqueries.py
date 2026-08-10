"""Nested scopes: a subquery sees its own relations first and its parent's as well."""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import scope_of, statement_at
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Scope
from tests.corpus.cases import split_caret


def scope(marked: str) -> Scope:
    """Run scope_of on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    return scope_of(tokens, lo, hi, caret, POSTGRES)


def rendered(marked: str) -> list[str]:
    """Visible relations as 'alias:dotted.path', innermost first."""
    return [f'{r.alias or ""}:{".".join(r.path)}' for r in scope(marked).visible()]


def test_derived_table_is_registered() -> None:
    """`(SELECT ...) d` is a relation the statement described itself."""
    result = scope('SELECT * FROM (SELECT id FROM orders) d WHERE d.⌶')
    relation = next(r for r in result.visible() if r.label == 'd')
    assert relation.source == 'subquery'
    assert relation.projection is not None
    assert relation.projection.columns == ('id',)


def test_derived_table_selecting_a_star() -> None:
    """Same three-state projection as a CTE."""
    result = scope('SELECT * FROM (SELECT * FROM orders) d WHERE d.⌶')
    relation = next(r for r in result.visible() if r.label == 'd')
    assert relation.projection is not None
    assert [r.path for r in relation.projection.stars] == [('orders',)]


def test_caret_inside_a_subquery_sees_the_inner_relation_first() -> None:
    """Inner scope first, outer scope still visible."""
    assert rendered('SELECT * FROM users u WHERE id IN (SELECT user_id FROM orders o WHERE o.⌶)') == [
        'o:orders',
        'u:users',
    ]


def test_parent_link_is_set() -> None:
    """Correlated subqueries reference the outer query, so the link must exist."""
    result = scope('SELECT * FROM users u WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = ⌶)')
    assert result.parent is not None
    assert [r.label for r in result.relations] == ['o']
    assert [r.label for r in result.parent.relations] == ['u']


def test_caret_outside_a_subquery_does_not_see_its_relations() -> None:
    """A subquery's FROM is private to it."""
    assert rendered('SELECT ⌶ FROM users u WHERE id IN (SELECT user_id FROM orders o)') == ['u:users']


def test_two_levels_of_nesting() -> None:
    """Scopes chain all the way out."""
    sql = 'SELECT * FROM a x WHERE id IN (SELECT id FROM b y WHERE id IN (SELECT id FROM c z WHERE z.⌶))'
    assert rendered(sql) == ['z:c', 'y:b', 'x:a']


def test_derived_table_body_relations_are_not_visible_outside() -> None:
    """`orders` inside the derived table must not leak into the outer scope."""
    assert rendered('SELECT ⌶ FROM (SELECT id FROM orders) d') == ['d:']


def test_a_derived_table_cannot_see_the_enclosing_from() -> None:
    """
    `FROM a JOIN (SELECT a.⌶ FROM b) s` is not a correlated subquery.

    A derived table is evaluated before the join it sits in, so the other
    relations of that FROM list are not in scope. Postgres answers `invalid
    reference to FROM-clause entry for table "u"`, and offering the column is
    offering a query that does not run.
    """
    found = scope('SELECT * FROM auth_user u JOIN (SELECT ⌶ FROM orders) s ON 1 = 1')
    assert [r.label for r in found.visible()] == ['orders']


def test_a_lateral_derived_table_can_see_it() -> None:
    """LATERAL is the keyword that asks for exactly what the plain form forbids."""
    found = scope('SELECT * FROM auth_user u, LATERAL (SELECT ⌶ FROM orders) s')
    assert 'u' in [r.label for r in found.visible()]


def test_an_expression_subquery_still_sees_the_outer_query() -> None:
    """The counterpart: a correlated subquery in WHERE is the case that must keep working."""
    found = scope('SELECT * FROM auth_user u WHERE EXISTS (SELECT ⌶ FROM orders)')
    assert 'u' in [r.label for r in found.visible()]


def test_a_cte_body_sees_only_the_ctes_declared_before_it() -> None:
    """
    `WITH a AS (SELECT ⌶ FROM b), b AS (...)` cannot reference `b`.

    A non-recursive WITH is read in order, so Postgres answers `relation "b"
    does not exist`. Declaration and visibility are not the same thing.
    """
    found = scope('WITH a AS (SELECT ⌶ FROM b), b AS (SELECT id FROM orders) SELECT * FROM a')
    assert list(found.ctes) == []


def test_a_later_cte_body_sees_the_earlier_ones() -> None:
    """The counterpart, and the reason the declaration order is worth keeping."""
    found = scope('WITH a AS (SELECT id FROM orders), b AS (SELECT ⌶ FROM a) SELECT * FROM b')
    assert list(found.ctes) == ['a']


def test_a_recursive_cte_sees_itself() -> None:
    """RECURSIVE is what makes the self-reference legal, and it is the whole point of the form."""
    found = scope('WITH RECURSIVE t AS (SELECT id FROM orders UNION ALL SELECT ⌶ FROM t) SELECT * FROM t')
    assert 't' in found.ctes
