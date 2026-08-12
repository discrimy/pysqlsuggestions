"""
Callables the catalog can tell apart.

A procedure is not a function you may call in an expression — Postgres answers
`archive_old_reports(date) is a procedure` and refuses to plan — so the record
has to carry which it is, and the engine has to read it.
"""

from __future__ import annotations

from typing import Any

from pysqlsuggestions.dialects.base import Query
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.resolve import _function_candidate
from pysqlsuggestions.types import Function


def mapped(query: Query | None, row: tuple[Any, ...]) -> Function:
    """
    One driver row through a dialect's mapper, as a Function.

    `Query.row` is declared as returning `object`, because a driver hands back
    untyped values and the mapper is the only place their shape is pinned down.
    Asserting the type here is what narrows it and what would fail readably if a
    dialect ever mapped its functions to the wrong record.
    """
    assert query is not None
    found = query.row(row)
    assert isinstance(found, Function)
    return found


def test_a_function_is_a_function_unless_it_says_otherwise() -> None:
    """The default has to be the safe reading: an unfiltered backend keeps working."""
    assert Function(schema=None, name='now', args='', result='timestamptz').kind == 'function'


def test_postgres_reports_which_kind_of_callable_it_found() -> None:
    """prokind is the whole distinction, and the mapper is the only place it is visible."""
    query = POSTGRES.catalog_queries.functions
    assert mapped(query, ('pg_catalog', 'count', '"any"', 'bigint', 'a')).kind == 'aggregate'
    assert mapped(query, ('pg_catalog', 'now', '', 'timestamptz', 'f')).kind == 'function'
    assert mapped(query, ('pg_catalog', 'rank', '', 'bigint', 'w')).kind == 'window'
    assert mapped(query, ('public', 'archive', 'IN cutoff date', None, 'p')).kind == 'procedure'


def test_a_procedure_reports_no_result_rather_than_the_word_none() -> None:
    """pg_get_function_result is NULL for a procedure, and str(None) is 'None'."""
    found = mapped(POSTGRES.catalog_queries.functions, ('public', 'archive', 'IN cutoff date', None, 'p'))
    assert found.result is None


def test_clickhouse_stops_putting_a_kind_in_the_return_type() -> None:
    """`count() -> aggregate` claimed a return type of `aggregate`. It has none to report."""
    query = CLICKHOUSE.catalog_queries.functions
    counted = mapped(query, ('count', 1))
    assert counted.kind == 'aggregate'
    assert counted.result is None
    assert mapped(query, ('abs', 0)).kind == 'function'


def test_trino_reads_the_kind_column_it_was_already_fetching() -> None:
    """SHOW FUNCTIONS returns (name, result, args, kind, deterministic, description)."""
    query = TRINO.catalog_queries.functions
    scalar = mapped(query, ('abs', 'bigint', 'bigint', 'scalar', True, 'Absolute value'))
    assert scalar.kind == 'function'
    assert scalar.result == 'bigint'
    assert mapped(query, ('sum', 'bigint', 'bigint', 'aggregate', True, '')).kind == 'aggregate'


def test_the_detail_drops_the_arrow_when_there_is_no_result() -> None:
    """`count() -> ` reads as a broken signature; `count()` reads as an unknown one."""
    unknown = _function_candidate(Function(schema=None, name='count', args=None, result=None, kind='aggregate'))
    assert unknown.detail == 'count()  aggregate'
    known = _function_candidate(Function(schema='pg_catalog', name='now', args='', result='timestamptz'))
    assert known.detail == 'now() -> timestamptz'
