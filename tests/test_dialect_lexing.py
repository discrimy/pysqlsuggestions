"""Lexical divergence between the four dialects, which is where most dialect variance lives."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.lex import TokenType, lex
from pysqlsuggestions.engine.rank import quote_if_needed

ALL = [ANSI, POSTGRES, CLICKHOUSE, TRINO]


def significant(src: str, dialect: Dialect) -> list[tuple[TokenType, str]]:
    """(type, value) for every non-whitespace token."""
    return [(t.type, t.value) for t in lex(src, dialect.syntax) if t.type is not TokenType.WHITESPACE]


@pytest.mark.parametrize('dialect', ALL, ids=lambda d: d.name)
def test_every_dialect_has_reserved_words(dialect: Dialect) -> None:
    """Reserved words ship offline because quoting decisions precede any connection."""
    assert 'select' in dialect.reserved
    assert all(word.islower() for word in dialect.reserved)


def test_namespace_depth_differs() -> None:
    """One tuple drives three different answers to `analytics.<caret>`."""
    assert POSTGRES.namespace.levels == ('schema', 'table')
    assert CLICKHOUSE.namespace.levels == ('database', 'table')
    assert TRINO.namespace.levels == ('catalog', 'schema', 'table')


def test_postgres_folds_to_lower_clickhouse_preserves() -> None:
    """Case folding is the divergence users notice first."""
    assert significant('SELECT Foo', POSTGRES)[1] == (TokenType.IDENT, 'foo')
    assert significant('SELECT Foo', CLICKHOUSE)[1] == (TokenType.IDENT, 'Foo')
    assert significant('SELECT Foo', TRINO)[1] == (TokenType.IDENT, 'foo')


def test_clickhouse_accepts_backtick_identifiers() -> None:
    """ClickHouse quotes with either " or `; the others only know about "."""
    assert significant('`My Col`', CLICKHOUSE) == [(TokenType.IDENT, 'My Col')]
    assert significant('`My Col`', POSTGRES)[0][0] is not TokenType.IDENT


def test_clickhouse_hash_comments() -> None:
    """ClickHouse alone treats # as a line comment."""
    assert significant('# note\nSELECT', CLICKHOUSE) == [(TokenType.COMMENT, '# note'), (TokenType.IDENT, 'SELECT')]


def test_postgres_dollar_quoting_and_nested_comments() -> None:
    """Both are Postgres-only among these four."""
    assert significant('$fn$ x $fn$', POSTGRES) == [(TokenType.STRING, '$fn$ x $fn$')]
    assert significant('$fn$ x $fn$', TRINO)[0][0] is not TokenType.STRING
    # Trino does not nest, so its comment stops early and `c */` lexes on as ordinary tokens.
    assert significant('/* a /* b */ c */', POSTGRES) == [(TokenType.COMMENT, '/* a /* b */ c */')]
    assert significant('/* a /* b */ c */', TRINO)[0] == (TokenType.COMMENT, '/* a /* b */')


def test_ansi_has_no_cast_operator() -> None:
    """The conservative fallback does not assume ::; the three real backends do."""
    assert ANSI.syntax.cast_operator is None
    assert POSTGRES.syntax.cast_operator == '::'
    assert CLICKHOUSE.syntax.cast_operator == '::'
    assert TRINO.syntax.cast_operator == '::'


@pytest.mark.parametrize(
    ('dialect', 'expected'),
    [(POSTGRES, 'отчёты'), (CLICKHOUSE, '"отчёты"'), (TRINO, '"отчёты"'), (ANSI, '"отчёты"')],
)
def test_non_ascii_names_are_quoted_where_the_backend_demands_it(dialect: Dialect, expected: str) -> None:
    """
    Only Postgres reads a Cyrillic name back unquoted.

    ClickHouse answers `Unrecognized token` and Trino `mismatched input`, so a
    suggestion inserted bare there produces a query that does not run — and a
    Russian-language schema hits this on the first column.
    """
    assert quote_if_needed('отчёты', dialect) == expected


@pytest.mark.parametrize(('dialect', 'expected'), [(POSTGRES, 'a$b'), (TRINO, '"a$b"')])
def test_a_dollar_in_a_name_follows_the_same_rule(dialect: Dialect, expected: str) -> None:
    """Postgres allows `$` after the first character; Trino does not allow it at all."""
    assert quote_if_needed('a$b', dialect) == expected


@pytest.mark.parametrize('dialect', ALL, ids=lambda d: d.name)
def test_every_dialect_spells_a_bound_parameter(dialect: Dialect) -> None:
    """A dialect with none offers column names inside `:param`, which is an active wrong answer."""
    assert dialect.syntax.placeholders


def test_postgres_reads_both_its_own_spelling_and_the_tooling_one() -> None:
    """`$1` is what the server takes; `:name` is what every tool over it writes."""
    assert significant('WHERE id = $1', POSTGRES)[-1] == (TokenType.PARAM, '$1')
    assert significant('WHERE id = :user_id', POSTGRES)[-1] == (TokenType.PARAM, ':user_id')


def test_postgres_keeps_the_jsonb_existence_operator() -> None:
    """`?` is a real Postgres predicate, which is why the dialect does not claim it."""
    assert (TokenType.PARAM, '?') not in significant("WHERE data ? 'key'", POSTGRES)


def test_trino_takes_the_question_mark() -> None:
    """Trino's prepared statements use it and Trino has no `?` operator to lose."""
    assert significant('WHERE id = ?', TRINO)[-1] == (TokenType.PARAM, '?')


def test_clickhouse_takes_its_braced_form() -> None:
    """ClickHouse spells a parameter `{name:Type}`, brace to brace."""
    assert significant('WHERE id = {id:UInt64}', CLICKHOUSE)[-1] == (TokenType.PARAM, '{id:UInt64}')


def test_the_cast_operator_survives_the_named_spelling() -> None:
    """`::` needs no special case: `:` is not an identifier start, so `a::int` is a cast."""
    assert significant('SELECT a::int', POSTGRES)[2] == (TokenType.OPERATOR, '::')


def test_dollar_quoting_survives_the_numbered_spelling() -> None:
    """A dollar quote fails the digits body at its second character, so it still lexes as a string."""
    assert significant('$$SELECT 1$$', POSTGRES) == [(TokenType.STRING, '$$SELECT 1$$')]
    assert significant('$tag$body$tag$', POSTGRES) == [(TokenType.STRING, '$tag$body$tag$')]
