"""Request derivation: kind narrowing, the part that decides answer quality."""

from __future__ import annotations

from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.request import derive_request
from pysqlsuggestions.types import Kind, Request
from tests.corpus.cases import split_caret


def request(marked: str, dialect: Dialect = POSTGRES) -> Request:
    """Run derive_request on ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return derive_request(sql, caret, dialect)


def test_alias_qualifier_narrows_to_columns() -> None:
    """plan.md §10's worked example. No keywords, no functions, no tables."""
    result = request('SELECT * FROM users u WHERE u.⌶')
    assert result.kinds == (Kind.COLUMN,)
    assert result.qualifier == ('u',)


def test_unqualified_select_offers_columns_and_functions() -> None:
    """A select list wants columns and functions; keywords there would bury them."""
    assert request('SELECT ⌶ FROM t').kinds == (Kind.COLUMN, Kind.FUNCTION)


def test_a_relation_position_offers_what_may_follow_once_it_has_one() -> None:
    """`FROM t JOIN ⌶` can be followed by ON or USING; `FROM ⌶` cannot be followed by anything yet."""
    assert request('SELECT * FROM ⌶').kinds == (Kind.TABLE, Kind.SCHEMA)
    assert request('SELECT * FROM t JOIN ⌶').kinds == (Kind.TABLE, Kind.SCHEMA, Kind.KEYWORD)


def test_from_clause_offers_tables_and_schemas() -> None:
    """A relation position never suggests columns."""
    assert request('SELECT * FROM ⌶').kinds == (Kind.TABLE, Kind.SCHEMA)


def test_namespace_qualifier_postgres() -> None:
    """One segment names a schema, so the answer is tables."""
    assert request('SELECT * FROM analytics.⌶').kinds == (Kind.TABLE,)


def test_namespace_qualifier_trino() -> None:
    """Trino's first segment is a catalog, so the answer is schemas."""
    assert request('SELECT * FROM analytics.⌶', TRINO).kinds == (Kind.SCHEMA,)


def test_namespace_qualifier_clickhouse() -> None:
    """ClickHouse's first segment is a database, so the answer is tables."""
    assert request('SELECT * FROM analytics.⌶', CLICKHOUSE).kinds == (Kind.TABLE,)


def test_qualifier_deeper_than_the_namespace_reads_as_a_column() -> None:
    """Postgres allows schema.table.column, so two segments leave only columns."""
    assert request('SELECT public.users.⌶ FROM public.users').kinds == (Kind.COLUMN,)


def test_trino_two_segment_qualifier_reaches_tables() -> None:
    """Three namespace levels mean catalog.schema. still has a table level to offer."""
    assert request('SELECT * FROM prod.analytics.⌶', TRINO).kinds == (Kind.TABLE,)


def test_alias_beats_a_schema_of_the_same_name() -> None:
    """Resolution order is alias first, then namespace."""
    result = request('SELECT * FROM orders public WHERE public.⌶')
    assert result.kinds == (Kind.COLUMN,)


def test_a_completed_operand_wants_an_operator_not_another_column() -> None:
    """`WHERE r.id ` cannot take another column: two names in a row is not valid SQL."""
    assert request('SELECT * FROM users r WHERE r.id ⌶').kinds == (Kind.OPERATOR, Kind.KEYWORD)


def test_an_operator_reopens_the_operand_position() -> None:
    """`= ` and `AND ` both expect a value, so columns come back."""
    assert request('SELECT * FROM users r WHERE r.id = ⌶').kinds == (Kind.COLUMN, Kind.FUNCTION)
    assert request('SELECT * FROM users r WHERE r.id = 1 AND ⌶').kinds == (Kind.COLUMN, Kind.FUNCTION)


def test_a_half_typed_word_is_judged_by_what_precedes_it() -> None:
    """
    A partial word could become either a column or a keyword.

    `WHERE na` follows the WHERE keyword, so it is naming a column. `> 1 AN`
    follows a finished predicate, so it is turning into AND.
    """
    assert request('SELECT * FROM users r WHERE na⌶').kinds == (Kind.COLUMN, Kind.FUNCTION)
    assert request('SELECT * FROM users r WHERE r.id > 1 AN⌶').kinds == (Kind.KEYWORD,)
    assert request('SELECT * FROM users r WHERE r.id = 1 AND na⌶').kinds == (Kind.COLUMN, Kind.FUNCTION)


def test_a_select_star_completes_an_item() -> None:
    """`SELECT * ` takes FROM; the star is the item, not an operator."""
    assert request('SELECT * ⌶').expecting == 'connective'
    assert request('SELECT * ⌶').kinds == (Kind.KEYWORD,)


def test_a_qualified_star_completes_an_item_too() -> None:
    """So does `SELECT t.* `."""
    assert request('SELECT t.* ⌶ FROM t').expecting == 'connective'
    assert request('SELECT id, * ⌶ FROM t').expecting == 'connective'


def test_multiplication_is_still_an_operator() -> None:
    """`SELECT a * ` opens an operand; the same character, the other meaning."""
    assert request('SELECT a * ⌶ FROM t').expecting == 'operand'
    assert request('SELECT 5 * ⌶ FROM t').expecting == 'operand'


def test_a_star_inside_a_call_completes_an_item() -> None:
    """`count(*` is the item form."""
    assert request('SELECT count(* ⌶) FROM t').expecting == 'connective'


def test_the_three_expression_positions() -> None:
    """An operand is wanted, then an operator, then a connective."""
    assert request('SELECT * FROM users r WHERE ⌶').expecting == 'operand'
    assert request('SELECT * FROM users r WHERE r.id ⌶').expecting == 'operator'
    assert request('SELECT * FROM users r WHERE r.id > 1 ⌶').expecting == 'connective'


def test_an_unfinished_predicate_takes_no_connective() -> None:
    """`WHERE r.id ` has no comparison yet, so AND would not parse."""
    assert request('SELECT * FROM users r WHERE r.id ⌶').kinds == (Kind.OPERATOR, Kind.KEYWORD)


def test_a_finished_predicate_takes_no_second_comparison() -> None:
    """`WHERE r.id > 1 ` cannot be compared again."""
    assert request('SELECT * FROM users r WHERE r.id > 1 ⌶').kinds == (Kind.KEYWORD,)


def test_a_connective_reopens_the_predicate() -> None:
    """After AND the next predicate starts from nothing."""
    assert request('SELECT * FROM users r WHERE r.id > 1 AND r.name ⌶').expecting == 'operator'


def test_is_null_finishes_a_predicate() -> None:
    """NULL is a value, and IS is a comparison, so the predicate is complete."""
    assert request('SELECT * FROM users r WHERE r.id IS NULL ⌶').expecting == 'connective'


def test_the_start_of_a_clause_expects_an_operand() -> None:
    """A clause keyword opens the position rather than closing one."""
    assert request('SELECT * FROM users r WHERE ⌶').kinds == (Kind.COLUMN, Kind.FUNCTION)
    assert request('SELECT * FROM users r GROUP BY ⌶').kinds == (Kind.COLUMN, Kind.FUNCTION)


def test_a_comma_reopens_the_operand_position() -> None:
    """`GROUP BY a, ` wants another expression."""
    assert request('SELECT * FROM users r GROUP BY id, ⌶').kinds == (Kind.COLUMN, Kind.FUNCTION)


def test_a_closing_paren_completes_an_operand() -> None:
    """`WHERE count(*) ` is a finished expression."""
    assert request('SELECT * FROM users r WHERE count(*) ⌶').kinds == (Kind.OPERATOR, Kind.KEYWORD)


def test_a_literal_completes_an_operand() -> None:
    """A string or a number closes the predicate it was compared into."""
    assert request("SELECT * FROM users r WHERE r.name = 'x' ⌶").kinds == (Kind.KEYWORD,)
    assert request('SELECT * FROM users r WHERE r.id = 1 ⌶').kinds == (Kind.KEYWORD,)


def test_a_clause_without_operators_offers_only_keywords() -> None:
    """`SELECT r.name ` takes AS or FROM; no comparison belongs in a select list."""
    assert request('SELECT r.name ⌶ FROM users r').kinds == (Kind.KEYWORD,)


def test_a_completed_select_item_wants_as_or_from() -> None:
    """The same rule in the select list."""
    assert request('SELECT r.name ⌶ FROM users r').kinds == (Kind.KEYWORD,)


def test_caret_in_a_literal_offers_nothing() -> None:
    """Suggesting identifiers inside a string is worse than suggesting nothing."""
    result = request("SELECT * FROM t WHERE name = 'ab⌶")
    assert result.kinds == ()
    assert result.prefix == ''


def test_caret_in_a_comment_offers_nothing() -> None:
    """Same rule."""
    assert request('SELECT * FROM t -- note ⌶').kinds == ()


def test_replace_span_covers_only_the_typed_prefix() -> None:
    """The qualifier keeps its place when a suggestion is accepted."""
    result = request('SELECT * FROM users u WHERE u.em⌶')
    assert result.replace_span == (30, 32)
    assert result.prefix == 'em'


def test_scope_is_attached() -> None:
    """Resolve needs the scope; it must never arrive as None for a real statement."""
    result = request('SELECT na⌶ FROM users u')
    assert result.scope is not None
    assert [r.label for r in result.scope.visible()] == ['u']


def test_empty_input() -> None:
    """An empty document offers the ways a statement can begin."""
    result = derive_request('', 0, POSTGRES)
    assert result.kinds == (Kind.SNIPPET, Kind.KEYWORD)
    assert result.clause is None


def test_readme_example_is_accurate() -> None:
    """The example in README.md must actually work, verbatim."""
    sql = 'SELECT id, na FROM users u'
    result = derive_request(sql, 13, POSTGRES)
    assert result.prefix == 'na'
    assert result.clause == 'SELECT'
    assert result.replace_span == (11, 13)
    assert result.kinds == (Kind.COLUMN, Kind.FUNCTION)
    assert [r.label for r in (result.scope.visible() if result.scope else ())] == ['u']


def test_readme_qualifier_example_is_accurate() -> None:
    """The caret must sit past the dot. plan.md §10 writes 29, which is one short."""
    sql = 'SELECT * FROM users u WHERE u.'
    assert derive_request(sql, 30, POSTGRES).kinds == (Kind.COLUMN,)
    assert derive_request(sql, 29, POSTGRES).kinds == (Kind.COLUMN, Kind.FUNCTION)


def test_readme_dialect_example_is_accurate() -> None:
    """One tuple, three answers to the same text."""
    sql = 'SELECT * FROM analytics.'
    assert derive_request(sql, len(sql), POSTGRES).kinds == (Kind.TABLE,)
    assert derive_request(sql, len(sql), TRINO).kinds == (Kind.SCHEMA,)


def test_a_statement_that_is_only_its_first_keyword() -> None:
    """
    `SELECT` with nothing after it is what the editor holds mid-keystroke.

    The select list is empty rather than absent, and an empty list has no
    outputs — reading one must not run off the end of the token stream. The
    word is still being typed, so it is not yet a clause: what belongs here is
    the offer to finish `SELECT` itself.
    """
    for marked in ('SELECT⌶', 'select⌶', 'SELECT 1; SELECT⌶', 'SELECT * FROM t WHERE x = (SELECT⌶'):
        assert request(marked).prefix == 'select', marked
    assert Kind.KEYWORD in request('SELECT⌶').kinds
    assert request('SELECT ⌶').clause == 'SELECT'


def test_a_caret_outside_the_text_is_pulled_back_into_it() -> None:
    """
    A span must always index the string it came from.

    A negative caret otherwise reaches `replace_span=(-1, -1)`, and splicing
    there wraps around to the end of the query rather than failing loudly.
    """
    sql = 'SELECT * FROM auth_user u WHERE u.'
    for caret in (-1, -1000, len(sql) + 5):
        span = derive_request(sql, caret, POSTGRES).replace_span
        assert 0 <= span[0] <= span[1] <= len(sql), f'caret={caret} gave {span}'
