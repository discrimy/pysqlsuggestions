"""The value types, with emphasis on the two decisions that are easy to regress."""

from __future__ import annotations

import dataclasses

import pytest

from pysqlsuggestions.types import Kind, Projection, Relation, Request, Scope


def test_kind_values_are_json_ready_strings() -> None:
    """Consumers serialise `kind` straight into JSON; auto() integers would be meaningless."""
    assert [k.value for k in Kind] == [
        'column',
        'table',
        'cte',
        'schema',
        'function',
        'alias',
        'keyword',
        'operator',
        'type',
        'snippet',
    ]


def test_request_defaults() -> None:
    """Only kinds, prefix and replace_span are required."""
    request = Request(kinds=(Kind.COLUMN,), prefix='na', replace_span=(11, 13))
    assert request.qualifier == ()
    assert request.clause is None
    assert request.scope is None


def test_request_is_immutable() -> None:
    """Request is a value; nothing downstream may mutate it."""
    request = Request(kinds=(), prefix='', replace_span=(0, 0))
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.prefix = 'x'  # type: ignore[misc]


def test_relation_has_three_projection_states() -> None:
    """A catalog object, a self-described relation, and one needing star expansion."""
    users = Relation(alias='u', path=('users',), source='table')
    assert users.projection is None

    named = Relation(alias='r', path=('recent',), source='cte', projection=Projection(columns=('id', 'total')))
    assert named.projection is not None
    assert named.projection.stars == ()

    starred = Relation(alias='a', path=('a',), source='cte', projection=Projection(stars=(users,)))
    assert starred.projection is not None
    assert starred.projection.columns == ()
    assert starred.projection.stars == (users,)


def test_scope_nests() -> None:
    """Subqueries see their parent's relations."""
    outer = Scope(relations=(Relation(alias='o', path=('orders',), source='table'),))
    inner = Scope(relations=(), parent=outer)
    assert inner.parent is outer
