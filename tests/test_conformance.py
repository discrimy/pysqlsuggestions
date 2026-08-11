"""
Every dialect passes the shipped corpus, and the corpus can fail.

`DialectConformance` is the only thing that says a dialect *works* rather than
merely exists. The four here are parametrised through it — which is what makes
"ClickHouse and Trino are proven-thin rather than aspirational" a claim someone
can check rather than a sentence in a design document.

A conformance suite that passes everything tests nothing, so the second half of
this file breaks dialects on purpose and requires it to notice.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pysqlsuggestions.api import complete
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Clause, ClauseModel, Dialect, Namespace
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.registry import available, named
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.testing import DialectConformance
from pysqlsuggestions.types import Kind

SHIPPED = (ANSI, POSTGRES, CLICKHOUSE, TRINO)


@pytest.mark.parametrize('dialect', SHIPPED, ids=lambda d: d.name)
def test_a_shipped_dialect_conforms(dialect: Dialect) -> None:
    """
    The corpus is the floor: an alias reaches its columns, a dotted path narrows
    one level per segment, a quoted name is the same name, both sides of a join
    are in scope.

    Parametrised rather than written four times, because the point is that all
    four answer the same propositions — with different SQL, since the corpus is
    spelled from what each dialect says about its own namespace and quoting.
    """
    failures = DialectConformance.check(dialect)
    assert not failures, '\n'.join(failures)


@pytest.mark.parametrize('dialect', SHIPPED, ids=lambda d: d.name)
def test_a_dialect_of_three_levels_is_asked_about_three(dialect: Dialect) -> None:
    """
    The corpus grows with the namespace rather than testing two levels everywhere.

    Trino's `catalog.schema.table` gets a case per level; a two-level dialect
    gets one. Without this the suite would pass a three-level dialect that only
    ever resolves two, which is the mistake most worth catching in a new one.
    """
    names = [case.name for case in DialectConformance.cases(dialect)]
    per_level = [name for name in names if 'qualifier offers what it contains' in name]
    assert len(per_level) == len(dialect.namespace.levels) - 1


@pytest.mark.parametrize(
    ('note', 'broken'),
    [
        (
            'a lowercase clause name never matches uppercased text',
            replace(POSTGRES, clauses=POSTGRES.clauses.extend(Clause(name='prewhere'))),
        ),
        (
            'a follows naming a clause that is not there is silently inert',
            replace(POSTGRES, clauses=POSTGRES.clauses.extend(Clause(name='PREWHERE', follows=frozenset({'FORM'})))),
        ),
        (
            'aliasing with a word the clause never offers can never be spent',
            replace(POSTGRES, clauses=POSTGRES.clauses.extend(Clause(name='PREWHERE', aliases_with='AS'))),
        ),
        (
            'one namespace level cannot name a relation',
            replace(POSTGRES, namespace=Namespace(levels=('table',))),
        ),
        (
            'no clause vocabulary answers nothing anywhere',
            replace(POSTGRES, clauses=ClauseModel()),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else '',
)
def test_the_corpus_notices_a_broken_dialect(note: str, broken: Dialect) -> None:
    """
    Each of these is a mistake that produces no error and no crash — just a
    clause that is never offered, which is exactly the kind of thing a dialect
    author would ship without knowing.
    """
    assert DialectConformance.check(broken), note


def test_the_corpus_cannot_catch_a_consistent_lie() -> None:
    """
    Recorded because it is a real limit and an easy one to forget.

    The cases are built from what the dialect says about itself, so a dialect
    claiming a namespace level it does not have gets a fixture with that level
    in it and passes. Only a real server settles that, which is what the
    integration suite is for — and why this file is not the whole story.
    """
    lying = replace(POSTGRES, namespace=Namespace(levels=('catalog', 'schema', 'table')))
    assert not DialectConformance.check(lying), 'self-consistent, so nothing here can tell'


def test_every_shipped_dialect_is_registered() -> None:
    """
    The entry-point group was advertised from 0.1.0 and nothing read it, so a
    third-party dialect could register correctly and never be found.

    Registration returns the module's own object rather than a copy, which is
    what lets a caller compare identity and what makes the four here the same
    four everything else in the suite uses.
    """
    assert set(available()) == {'ansi', 'postgres', 'clickhouse', 'trino'}
    assert named('postgres') is POSTGRES
    assert named('nonesuch') is None


def test_a_registered_dialect_is_held_to_the_same_corpus() -> None:
    """
    The two halves of this file are one idea: anything reachable by name must
    pass the corpus, whoever wrote it.
    """
    for name, dialect in available().items():
        assert not DialectConformance.check(dialect), name


@pytest.mark.parametrize('dialect', SHIPPED, ids=lambda d: d.name)
def test_no_join_is_proposed_without_a_declared_constraint(dialect: Dialect) -> None:
    """
    A proposal comes from a constraint the backend declares, or it does not come.

    The conformance fixture declares none, so this is the guard against a builder
    that infers an edge from `<singular>_id` matching `<table>.id`. That reading is
    right often enough to be tempting and wrong often enough to be dangerous: it
    writes valid SQL that returns the wrong rows, which no parser can catch.
    """
    catalog = DialectConformance.catalog(dialect)
    sql = f'SELECT * FROM {DialectConformance.reference(dialect, "users")} AS u JOIN '
    assert not [s for s in complete(sql, len(sql), dialect, catalog) if s.kind is Kind.JOIN]
