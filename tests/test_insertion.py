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


def applied(sql: str, plan: Insertion) -> str:
    """
    What the editor would end up with, doing only what an editor may do.

    Edits arrive last-first, so applying them in order needs no arithmetic.
    """
    for edit in plan.edits:
        sql = sql[: edit.span[0]] + edit.text + sql[edit.span[1] :]
    return sql


def test_the_plan_needs_no_interpretation() -> None:
    """Splice at the span, put the caret where it says. That is the whole contract."""
    sql = 'SELECT * FROM auth_user u WHERE u.crea'
    plan = plan_insertion(sql, suggestion('created_at', Kind.COLUMN, (34, 38)))
    assert applied(sql, plan) == 'SELECT * FROM auth_user u WHERE u.created_at'
    assert plan.caret == 44
    assert plan.pending == ()


def test_a_function_closes_its_parentheses() -> None:
    """And the caret goes inside only when there is an argument to type."""
    takes = plan_insertion('SELECT cou', suggestion('count', Kind.FUNCTION, (7, 10), takes_arguments=True))
    assert applied('SELECT cou', takes) == 'SELECT count()'
    assert takes.caret == 13

    none = plan_insertion('SELECT no', suggestion('now', Kind.FUNCTION, (7, 9)))
    assert applied('SELECT no', none) == 'SELECT now()'
    assert none.caret == 12


def test_a_namespace_continues_the_reference() -> None:
    """A schema brings its dot; the caret lands past it, ready for the next level."""
    plan = plan_insertion('SELECT * FROM pub', suggestion('public', Kind.SCHEMA, (14, 17)))
    assert applied('SELECT * FROM pub', plan) == 'SELECT * FROM public.'
    assert plan.caret == 21


def test_the_plan_says_whether_completion_should_carry_on() -> None:
    """
    A front end cannot work this out from the caret, and one that tries gets the
    commonest case backwards.

    `FROM pub` and `FROM pub.` both leave the caret past a dot — one written by
    the insertion, one stepped over — so the caret sits at the end of the
    inserted text in the first and beyond it in the second. Same meaning,
    opposite arithmetic. The demo derived it that way and so closed its list on
    every namespace whose dot it had had to supply, which is every namespace the
    user had not already dotted.
    """
    schema = suggestion('public', Kind.SCHEMA, (14, 17))
    assert plan_insertion('SELECT * FROM pub', schema).expects_more
    assert plan_insertion('SELECT * FROM pub.', schema).expects_more

    # A function finishes its blank and still wants the list, but only when
    # there is an argument to type: the two questions have different answers.
    takes = suggestion('count', Kind.FUNCTION, (7, 10), takes_arguments=True)
    assert plan_insertion('SELECT cou', takes).expects_more
    assert not plan_insertion('SELECT no', suggestion('now', Kind.FUNCTION, (7, 9))).expects_more

    template = suggestion('SELECT  FROM  AS ', Kind.SNIPPET, (0, 0), stops=(13, 17, 7))
    assert plan_insertion('', template).expects_more
    filled = plan_insertion('SELECT  FROM  AS ', suggestion('orders', Kind.TABLE, (13, 13)), pending=(17, 7))
    assert filled.expects_more, 'the caret moved to the next blank, which wants the list open'

    ordinary = suggestion('created_at', Kind.COLUMN, (24, 28))
    assert not plan_insertion('SELECT * FROM t WHERE u.crea', ordinary).expects_more


def test_a_keyword_is_separated_from_what_precedes_it() -> None:
    """Nothing is being replaced, so the insertion supplies the space."""
    sql = 'SELECT * FROM t WHERE id > 1'
    plan = plan_insertion(sql, suggestion('AND', Kind.KEYWORD, (28, 28)))
    assert applied(sql, plan) == 'SELECT * FROM t WHERE id > 1 AND'


def test_a_template_hands_back_the_blanks_it_opened() -> None:
    """The first is where the caret goes; the rest travel with the answer."""
    plan = plan_insertion('', suggestion('SELECT  FROM  AS ', Kind.SNIPPET, (0, 0), stops=(13, 17, 7)))
    assert plan.caret == 13
    assert plan.pending == (17, 7)


def test_filling_a_blank_moves_to_the_next_one() -> None:
    """And the ones after it shift by however much the text grew."""
    sql = 'SELECT  FROM  AS '
    plan = plan_insertion(sql, suggestion('orders', Kind.TABLE, (13, 13)), pending=(17, 7))
    assert applied(sql, plan) == 'SELECT  FROM orders AS '
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
    assert applied(sql, plan) == 'SELECT  FROM warehouse. AS '
    assert plan.caret == 23, 'past the dot, still in the relation blank'
    assert plan.pending == (27, 7), 'the later blanks moved, none were consumed'
    assert plan.expects_more, 'and the catalog needs a schema after it, so the list stays open'


def test_apply_suggestion_is_the_same_decision() -> None:
    """The convenience wrapper must not be a second implementation."""
    sql = 'SELECT * FROM pub'
    plan = plan_insertion(sql, suggestion('public', Kind.SCHEMA, (14, 17)))
    assert apply_suggestion(sql, suggestion('public', Kind.SCHEMA, (14, 17))) == (applied(sql, plan), plan.caret)


def test_a_column_with_no_relation_in_scope_brings_its_relation() -> None:
    """
    `SELECT na⌶` picking a column only helps if the table comes with it.

    Choosing `auth_user.name` where nothing is in the FROM leaves a reference
    to a relation the query does not have — so the same suggestion writes the
    FROM clause too, and the caret lands after the column, where the author was.
    """
    sql = 'SELECT na'
    pick = suggestion('auth_user.name', Kind.COLUMN, (7, 9), relation=('auth_user',))
    plan = plan_insertion(sql, pick)
    assert applied(sql, plan) == 'SELECT auth_user.name FROM auth_user'
    assert plan.caret == 21, 'after the column, not after the FROM'


def test_the_relation_goes_before_whatever_follows_the_select_list() -> None:
    """A FROM clause has a place in the statement, and it is not the end of it."""
    sql = 'SELECT na ORDER BY 1'
    pick = suggestion('auth_user.name', Kind.COLUMN, (7, 9), relation=('auth_user',))
    assert applied(sql, plan_insertion(sql, pick)) == 'SELECT auth_user.name FROM auth_user ORDER BY 1'


def test_a_column_from_a_relation_already_in_scope_brings_nothing() -> None:
    """The FROM is written; a second one would not parse."""
    sql = 'SELECT * FROM auth_user u WHERE na'
    pick = suggestion('u.name', Kind.COLUMN, (32, 34))
    plan = plan_insertion(sql, pick)
    assert applied(sql, plan) == 'SELECT * FROM auth_user u WHERE u.name'
    assert len(plan.edits) == 1


def test_every_plan_is_one_edit_unless_it_needs_two() -> None:
    """The ordinary case stays a single splice, which is what most callers see."""
    plan = plan_insertion('SELECT * FROM ord', suggestion('orders', Kind.TABLE, (14, 17)))
    assert len(plan.edits) == 1
    assert plan.edits[0].span == (14, 17)
