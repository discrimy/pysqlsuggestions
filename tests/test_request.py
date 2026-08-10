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


def test_unqualified_select_offers_columns_functions_and_keywords() -> None:
    """Narrowing only happens when there is something to narrow on."""
    assert request('SELECT ⌶ FROM t').kinds == (Kind.COLUMN, Kind.FUNCTION, Kind.KEYWORD)


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
    """An empty document is not an error."""
    result = derive_request('', 0, POSTGRES)
    assert result.kinds == (Kind.KEYWORD,)
    assert result.clause is None


def test_readme_example_is_accurate() -> None:
    """The example in README.md must actually work, verbatim."""
    sql = 'SELECT id, na FROM users u'
    result = derive_request(sql, 13, POSTGRES)
    assert result.prefix == 'na'
    assert result.clause == 'SELECT'
    assert result.replace_span == (11, 13)
    assert result.kinds == (Kind.COLUMN, Kind.FUNCTION, Kind.KEYWORD)
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
