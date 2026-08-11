"""
The whole insertion decided in one place.

An editor applying a suggestion should splice text and move a caret, nothing
more. Every judgement — whether a separator is needed, whether parentheses
close, whether a namespace continues, where a template's next blank is — is
made here, because each one that leaks into a front end is a rule that has to
be reimplemented and then kept in step. The demo drifted three times before
this existed.
"""

from __future__ import annotations

from pysqlsuggestions.api import apply_suggestion, plan_insertion
from pysqlsuggestions.types import Insertion, Kind, Suggestion


def suggestion(text: str, kind: Kind, span: tuple[int, int], **extra: object) -> Suggestion:
    """A suggestion as rank would emit it."""
    return Suggestion(text=text, kind=kind, replace_span=span, score=1.0, **extra)  # type: ignore[arg-type]


def spliced(sql: str, plan: Insertion) -> str:
    """What the editor would end up with, doing only what an editor may do."""
    start, end = plan.span
    return sql[:start] + plan.text + sql[end:]


def test_the_plan_needs_no_interpretation() -> None:
    """Splice at the span, put the caret where it says. That is the whole contract."""
    sql = 'SELECT * FROM auth_user u WHERE u.crea'
    plan = plan_insertion(sql, suggestion('created_at', Kind.COLUMN, (34, 38)))
    assert spliced(sql, plan) == 'SELECT * FROM auth_user u WHERE u.created_at'
    assert plan.caret == 44
    assert plan.pending == ()


def test_a_function_closes_its_parentheses() -> None:
    """And the caret goes inside only when there is an argument to type."""
    takes = plan_insertion('SELECT cou', suggestion('count', Kind.FUNCTION, (7, 10), takes_arguments=True))
    assert spliced('SELECT cou', takes) == 'SELECT count()'
    assert takes.caret == 13

    none = plan_insertion('SELECT no', suggestion('now', Kind.FUNCTION, (7, 9)))
    assert spliced('SELECT no', none) == 'SELECT now()'
    assert none.caret == 12


def test_a_namespace_continues_the_reference() -> None:
    """A schema brings its dot; the caret lands past it, ready for the next level."""
    plan = plan_insertion('SELECT * FROM pub', suggestion('public', Kind.SCHEMA, (14, 17)))
    assert spliced('SELECT * FROM pub', plan) == 'SELECT * FROM public.'
    assert plan.caret == 21


def test_a_keyword_is_separated_from_what_precedes_it() -> None:
    """Nothing is being replaced, so the insertion supplies the space."""
    sql = 'SELECT * FROM t WHERE id > 1'
    plan = plan_insertion(sql, suggestion('AND', Kind.KEYWORD, (28, 28)))
    assert spliced(sql, plan) == 'SELECT * FROM t WHERE id > 1 AND'


def test_a_template_hands_back_the_blanks_it_opened() -> None:
    """The first is where the caret goes; the rest travel with the answer."""
    plan = plan_insertion('', suggestion('SELECT  FROM  AS ', Kind.SNIPPET, (0, 0), stops=(13, 17, 7)))
    assert plan.caret == 13
    assert plan.pending == (17, 7)


def test_filling_a_blank_moves_to_the_next_one() -> None:
    """And the ones after it shift by however much the text grew."""
    sql = 'SELECT  FROM  AS '
    plan = plan_insertion(sql, suggestion('orders', Kind.TABLE, (13, 13)), pending=(17, 7))
    assert spliced(sql, plan) == 'SELECT  FROM orders AS '
    assert plan.caret == 23, 'the alias blank, moved along by the six characters inserted'
    assert plan.pending == (7,)


def test_a_blank_only_half_filled_keeps_its_place() -> None:
    """
    `FROM warehouse.` is not a relation yet, so the caret stays in that blank
    rather than moving to the alias — the case three namespace levels make
    obvious and two hide.
    """
    sql = 'SELECT  FROM  AS '
    plan = plan_insertion(sql, suggestion('warehouse', Kind.SCHEMA, (13, 13)), pending=(17, 7))
    assert spliced(sql, plan) == 'SELECT  FROM warehouse. AS '
    assert plan.caret == 23, 'past the dot, still in the relation blank'
    assert plan.pending == (27, 7), 'the later blanks moved, none were consumed'


def test_apply_suggestion_is_the_same_decision() -> None:
    """The convenience wrapper must not be a second implementation."""
    sql = 'SELECT * FROM pub'
    plan = plan_insertion(sql, suggestion('public', Kind.SCHEMA, (14, 17)))
    assert apply_suggestion(sql, suggestion('public', Kind.SCHEMA, (14, 17))) == (spliced(sql, plan), plan.caret)
