"""CTEs: the case users spend their time in, and the one no system catalog can answer."""

from __future__ import annotations

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.analyse import scope_of, select_outputs, statement_at
from pysqlsuggestions.engine.lex import lex
from pysqlsuggestions.types import Scope
from tests.corpus.cases import split_caret


def scope(marked: str) -> Scope:
    """Run scope_of on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    tokens = lex(sql, POSTGRES.syntax)
    lo, hi = statement_at(tokens, caret)
    return scope_of(tokens, lo, hi, caret, POSTGRES)


def outputs(sql: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(explicit column names, rendered star sources) for a select body."""
    tokens = lex(sql, POSTGRES.syntax)
    projection = select_outputs(tokens, 0, len(tokens), POSTGRES)
    return projection.columns, tuple(f'{r.alias or ""}:{".".join(r.path)}' for r in projection.stars)


def test_named_outputs() -> None:
    """A plain select list is fully self-described."""
    assert outputs('SELECT id, total FROM orders') == (('id', 'total'), ())


def test_aliased_outputs_use_the_alias() -> None:
    """The output name is what the outer query can reference."""
    assert outputs('SELECT sum(total) AS revenue, id FROM orders') == (('revenue', 'id'), ())


def test_a_bare_call_is_named_after_its_function() -> None:
    """
    `SELECT sum(total)` outputs a column called `sum`, so a CTE wrapping it can
    be queried by that name. Postgres names it, and an author reading the CTE
    back will reach for the same word.
    """
    assert outputs('SELECT sum(total), id FROM orders') == (('sum', 'id'), ())
    assert outputs('SELECT row_number() OVER (PARTITION BY id) FROM orders') == (('row_number',), ())
    assert outputs('SELECT total + 1, id FROM orders') == (('id',), ()), 'an expression names nothing'


def test_bare_star_records_its_source() -> None:
    """The star cannot be expanded without the catalog, so its source is recorded."""
    assert outputs('SELECT * FROM users') == ((), (':users',))


def test_qualified_star_records_only_that_relation() -> None:
    """`o.*` pulls from orders alone."""
    assert outputs('SELECT o.* FROM orders o JOIN users u ON o.user_id = u.id') == ((), ('o:orders',))


def test_mixed_star_and_names() -> None:
    """Both halves are kept."""
    assert outputs('SELECT id, u.* FROM users u') == (('id',), ('u:users',))


def test_cte_is_registered_with_its_projection() -> None:
    """plan.md §3.3: no catalog call at all."""
    result = scope('WITH recent AS (SELECT id, total FROM orders) SELECT r.⌶ FROM recent r')
    relation = next(r for r in result.visible() if r.label == 'r')
    assert relation.source == 'cte'
    assert relation.projection is not None
    assert relation.projection.columns == ('id', 'total')
    assert relation.projection.stars == ()


def test_cte_selecting_a_star_keeps_the_star_unresolved() -> None:
    """The three-state Projection exists for exactly this."""
    result = scope('WITH a AS (SELECT * FROM users) SELECT a.⌶ FROM a')
    relation = next(r for r in result.visible() if r.label == 'a')
    assert relation.projection is not None
    assert relation.projection.columns == ()
    assert [r.path for r in relation.projection.stars] == [('users',)]


def test_declared_column_list_wins() -> None:
    """`WITH a(x, y) AS (...)` names the outputs regardless of the body."""
    result = scope('WITH a(x, y) AS (SELECT id, total FROM orders) SELECT a.⌶ FROM a')
    relation = next(r for r in result.visible() if r.label == 'a')
    assert relation.projection is not None
    assert relation.projection.columns == ('x', 'y')


def test_multiple_ctes() -> None:
    """Comma-separated CTEs are all registered."""
    result = scope('WITH a AS (SELECT id FROM t1), b AS (SELECT n FROM t2) SELECT ⌶ FROM a, b')
    assert sorted(result.ctes) == ['a', 'b']


def test_cte_is_not_a_catalog_table() -> None:
    """A relation resolving to a CTE must carry source='cte', so resolve skips the catalog."""
    result = scope('WITH recent AS (SELECT id FROM orders) SELECT ⌶ FROM recent')
    relation = next(r for r in result.visible() if r.label == 'recent')
    assert relation.source == 'cte'


def test_a_table_sharing_a_name_with_no_cte_stays_a_table() -> None:
    """Only names declared in WITH become CTEs."""
    result = scope('SELECT ⌶ FROM recent')
    relation = next(r for r in result.visible() if r.label == 'recent')
    assert relation.source == 'table'
    assert relation.projection is None
