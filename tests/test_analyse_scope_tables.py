"""Scope: the relations a statement puts in view. Tables and aliases only in this task."""

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
    """Relations as 'alias:dotted.path', the corpus spelling."""
    return [f'{r.alias or ""}:{".".join(r.path)}' for r in scope(marked).visible()]


def test_single_table_no_alias() -> None:
    """The simplest case."""
    assert rendered('SELECT ⌶ FROM users') == [':users']


def test_alias() -> None:
    """An alias is what the qualifier will match against."""
    assert rendered('SELECT ⌶ FROM users u') == ['u:users']


def test_explicit_as_alias() -> None:
    """AS is optional and must be skipped, not read as the alias."""
    assert rendered('SELECT ⌶ FROM users AS u') == ['u:users']


def test_qualified_table() -> None:
    """A schema-qualified reference keeps both segments in path."""
    assert rendered('SELECT ⌶ FROM public.users u') == ['u:public.users']


def test_multiple_relations_in_declaration_order() -> None:
    """Order matters for ranking later."""
    assert rendered('SELECT ⌶ FROM orders o, users u') == ['o:orders', 'u:users']


def test_join() -> None:
    """JOIN contributes relations exactly like FROM."""
    assert rendered('SELECT ⌶ FROM orders o JOIN users u ON o.user_id = u.id') == ['o:orders', 'u:users']


def test_left_outer_join() -> None:
    """Join qualifier words are not relations and not aliases."""
    assert rendered('SELECT ⌶ FROM orders o LEFT OUTER JOIN users u ON o.user_id = u.id') == [
        'o:orders',
        'u:users',
    ]


def test_scope_is_built_from_the_whole_statement() -> None:
    """The FROM clause sits to the right of the caret and must still be seen."""
    assert rendered('SELECT na⌶ FROM users u') == ['u:users']


def test_the_half_typed_word_is_not_a_relation() -> None:
    """`FROM us⌶` must not register a relation called `us`."""
    assert rendered('SELECT * FROM us⌶') == []


def test_keywords_are_not_read_as_aliases() -> None:
    """`FROM users WHERE` must not alias users to `where`."""
    assert rendered('SELECT * FROM users WHERE ⌶') == [':users']


def test_quoted_relation_keeps_its_case() -> None:
    """Quoted identifiers are preserved verbatim in path."""
    assert rendered('SELECT ⌶ FROM "Mixed Case" m') == ['m:Mixed Case']


def test_update_and_delete_targets() -> None:
    """Relations come from UPDATE and DELETE FROM as well as SELECT ... FROM."""
    assert rendered('UPDATE users u SET name = ⌶') == ['u:users']
    assert rendered('DELETE FROM users u WHERE ⌶') == ['u:users']
