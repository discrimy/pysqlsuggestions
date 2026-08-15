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
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects import registry
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import (
    Clause,
    ClauseModel,
    Dialect,
    LiteralArgument,
    Namespace,
    Placeholder,
    Query,
    Syntax,
)
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.registry import available, named
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.lex import lex
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


def test_a_placeholder_that_can_never_end_is_reported() -> None:
    """An 'any' body with no closing delimiter is never lexed — silent, like every case here."""
    broken = replace(ANSI, syntax=Syntax(placeholders=(Placeholder(opens='@', body='any'),)))
    problems = DialectConformance.structure(broken)
    assert any('closing delimiter' in problem for problem in problems)


def test_a_placeholder_that_can_never_end_fails_the_corpus_too() -> None:
    """
    Both halves catch the same bad declaration, from different directions.

    `structure` reads it and says it can never end; the behavioural case writes
    `@is` and finds `is_staff` offered inside what the dialect calls a parameter.
    A dialect claiming a spelling the lexer cannot act on is worse than one
    claiming none, because the claim is what a caller reads.

    Stripping the spellings entirely is deliberately *not* caught here. The
    corpus builds each case from what the dialect says about itself, so a
    dialect that declares no parameter gets no parameter case — the same reason
    Trino gets none for `?`, which has no interior to put a caret in.
    """
    broken = replace(ANSI, syntax=replace(ANSI.syntax, placeholders=(Placeholder(opens='@', body='any'),)))
    assert not DialectConformance.check(ANSI)
    failures = DialectConformance.check(broken)
    assert any('bound parameter' in failure for failure in failures)


def test_a_dialect_that_cannot_search_relations_gets_no_case() -> None:
    """
    The corpus asks a dialect only what it claims to do.

    Trino ships no relation-search query, so the proposition does not apply —
    the same way it gets no foreign-key case. A corpus that failed it would be
    asserting a capability nobody claimed.
    """
    assert not DialectConformance.check(TRINO)
    assert not [case for case in DialectConformance.cases(TRINO) if 'search path' in case.name]


def test_the_relation_search_case_exists_where_the_query_does() -> None:
    """Postgres and ClickHouse claim it, so the corpus holds them to it."""
    for dialect in (POSTGRES, CLICKHOUSE):
        assert [case for case in DialectConformance.cases(dialect) if 'search path' in case.name]


def test_the_corpus_asks_every_dialect_to_keep_sequences_out_of_a_relation_position() -> None:
    """
    The fixture always holds a sequence, so the proposition applies to a dialect
    that has none — which is the point. A third-party dialect fetching relkind
    'S' without filtering finds out here rather than from its users.
    """
    for dialect in SHIPPED:
        assert [case for case in DialectConformance.cases(dialect) if 'sequence' in case.name]


def test_a_dialect_that_offers_sequences_for_a_relation_fails_the_corpus() -> None:
    """
    Broken on purpose, like every case in the second half of this file. A clause
    suggesting SEQUENCE where a relation belongs is the exact mistake the filter
    prevents, and it is silent — the list is merely longer.
    """
    broken = replace(
        POSTGRES,
        clauses=POSTGRES.clauses.extend(Clause(name='FROM', follows=frozenset({'SELECT'}), suggests=(Kind.SEQUENCE,))),
    )
    assert DialectConformance.check(broken)


def test_a_literal_argument_that_can_never_match_is_reported() -> None:
    """
    `_enclosing_call` returns a single uppercased word, so a name with a dot, a
    space or parentheses in it can never equal one — and an empty `suggests` can
    never produce a candidate. Both are silent, which is what `structure` is for.
    """
    dotted = replace(
        ANSI,
        literal_arguments=(LiteralArgument(function='pg_catalog.nextval', suggests=(Kind.SEQUENCE,)),),
    )
    assert any('single word' in problem for problem in DialectConformance.structure(dotted))
    empty = replace(ANSI, literal_arguments=(LiteralArgument(function='nextval', suggests=()),))
    assert any('suggests nothing' in problem for problem in DialectConformance.structure(empty))


def test_a_dialect_declaring_no_literal_arguments_gets_no_case() -> None:
    """
    The corpus asks a dialect only what it claims to do — the same bargain
    `parameter()` makes for `?` and `relation_search` makes for Trino.
    """
    assert not [case for case in DialectConformance.cases(TRINO) if 'literal' in case.name]
    assert [case for case in DialectConformance.cases(POSTGRES) if 'literal' in case.name]


def test_the_corpus_asks_every_dialect_for_an_unambiguous_reference() -> None:
    """
    Two same-named relations in one FROM is a state every backend here allows
    and every backend here refuses a bare reference in. A dialect that got this
    wrong would write SQL that does not run.
    """
    for dialect in SHIPPED:
        assert [case for case in DialectConformance.cases(dialect) if 'ambiguous' in case.name]


def test_the_ambiguity_case_is_not_one_a_dialect_can_break() -> None:
    """
    Recorded because it is a real limit of this half of the file.

    Every other broken-dialect test below turns a declaration wrong and watches
    the corpus notice. This rule lives in `resolve`, not in any dialect, so no
    declaration can switch it off — which makes the case a regression guard
    shared with third-party dialects rather than a detector of their mistakes.
    It still earns its place: a dialect whose namespace depth is wrong writes
    the wrong path here, and that the corpus does catch.
    """
    for dialect in SHIPPED:
        assert not DialectConformance.check(dialect), dialect.name


def test_the_corpus_asks_a_dialect_what_its_groups_begin_with() -> None:
    """
    A clause declaring `opens_a_group` and never answering inside one is silent
    rather than wrong, which is the kind of mistake `structure` cannot see and
    only a behavioural case can.
    """
    for dialect in SHIPPED:
        assert [case for case in DialectConformance.cases(dialect) if 'group' in case.name]


def test_a_dialect_declaring_no_group_words_gets_no_case() -> None:
    """
    The corpus asks a dialect only what it claims to do — the bargain
    `parameter()` makes for `?` and `relation_search` makes for Trino.
    """
    bare = replace(ANSI, clauses=ClauseModel(clauses=(Clause(name='SELECT', suggests=(Kind.COLUMN,)),)))
    assert not [case for case in DialectConformance.cases(bare) if 'group' in case.name]


def test_the_corpus_asks_a_dialect_about_the_kinds_it_narrows_to() -> None:
    """
    A clause naming kinds no relation in the catalog has is silent rather than
    wrong — a misspelt kind, or one this backend does not report. Only a
    behavioural case sees it.
    """
    for dialect in SHIPPED:
        narrowed = [c for c in dialect.clauses.clauses if c.relation_kinds]
        cases = [case for case in DialectConformance.cases(dialect) if 'kinds' in case.name]
        assert bool(cases) == bool(narrowed), dialect.name


def test_a_clause_with_a_blank_name_is_reported_rather_than_crashing() -> None:
    """
    Reached through documented composition, and worse than inert.

    `_by_first_word` groups clause names by `name.split()[0]`, which raises on a
    name with no words in it — so a dialect built with `extend(Clause(name=''))`
    took `complete` down with an IndexError, and took `check` down with the same
    one before it could report anything. `structure` said nothing at all, and a
    blank name is exactly the sort of declaration it exists to name.
    """
    broken = replace(ANSI, clauses=ANSI.clauses.extend(Clause(name='')))
    assert any('blank' in problem for problem in DialectConformance.structure(broken))
    assert complete('SELECT ', 7, broken, MemoryCatalog({('public', 'events'): [('id', 'bigint')]})) is not None
    assert DialectConformance.check(broken)


def test_a_zero_length_placeholder_opener_is_reported_rather_than_hanging() -> None:
    """
    `lex` is documented total, and non-termination is worse than raising.

    A placeholder whose `opens` is empty put the scanner's position where it
    already was, so it appended a zero-width token and never advanced. `check`
    inherited the hang, because it runs `complete` over its own corpus — the
    shipped self-test never returning rather than reporting the declaration.
    """
    syntax = Syntax(placeholders=(Placeholder(opens='', body='none'),))
    broken = replace(ANSI, name='broken', syntax=syntax)
    assert any('empty' in problem for problem in DialectConformance.structure(broken))
    assert lex('a', syntax) is not None


def test_a_catalog_query_wanting_more_values_than_it_gets_is_reported() -> None:
    """
    A `$N` typo is the mistake this harness ships to catch and did not.

    `DbapiCatalog` fixes each query's arity — `columns` is given two values, the
    searches one — so a marker beyond that is a static contradiction of exactly
    the kind reported here, needing neither a server nor a consistent dialect to
    see. Left unchecked it surfaced as an IndexError on the first catalog read.
    """
    broken = replace(
        POSTGRES,
        catalog_queries=replace(
            POSTGRES.catalog_queries,
            columns=Query(sql='SELECT 1 WHERE s = $1 AND t = $2 AND x = $3', row=lambda row: row),
        ),
    )
    assert any('$3' in problem for problem in DialectConformance.structure(broken))


def test_the_registry_does_not_hand_out_the_dictionary_it_caches() -> None:
    """
    One caller's mutation must not become every later caller's registry.

    `available` is cached, so it returned the same dict object every time and a
    caller editing what looked like its own copy poisoned the lookup
    process-wide — defeating the `isinstance` guard that is the only thing
    keeping a non-Dialect out of `named`.
    """
    first = available()
    first['postgres'] = 'not a dialect at all'  # type: ignore[assignment]
    assert named('postgres') is POSTGRES
    assert available()['postgres'] is POSTGRES


class _FakeDistribution:
    """Just the attribute the registry reads off a real one."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEntry:
    """An entry point, as `importlib.metadata` hands them over."""

    def __init__(self, name: str, dialect: Dialect, distribution: str) -> None:
        self.name = name
        self.dist = _FakeDistribution(distribution)
        self._dialect = dialect

    def load(self) -> Dialect:
        """The registered object."""
        return self._dialect


def test_a_plugin_overriding_a_built_in_dialect_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Overriding stays possible — a fork, or a fix ahead of a release — but audibly.

    The four built-ins register through the same entry-point group as anyone
    else, so they carry no privilege and the winner was whichever distribution
    `importlib.metadata` enumerated last. That is not a documented order. It
    matters because `lsp/connections.py` resolves the dialect by name and hands
    it to `DbapiCatalog`, so the winner's introspection SQL is what reaches the
    user's database.
    """
    shadow = replace(POSTGRES, name='shadow-of-postgres')
    entries = [
        _FakeEntry('postgres', POSTGRES, 'pysqlsuggestions'),
        _FakeEntry('postgres', shadow, 'somebody-elses-package'),
    ]
    monkeypatch.setattr(registry, 'entry_points', lambda group: entries)
    registry._scan.cache_clear()
    try:
        with pytest.warns(UserWarning, match='somebody-elses-package'):
            found = registry.named('postgres')
        assert found is shadow, 'the plugin still wins; only the silence is fixed'
    finally:
        registry._scan.cache_clear()


def test_two_plugins_claiming_one_name_also_say_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same collision without a built-in involved, which was equally silent."""
    first = replace(POSTGRES, name='first')
    second = replace(POSTGRES, name='second')
    entries = [_FakeEntry('duckdb', first, 'pkg-a'), _FakeEntry('duckdb', second, 'pkg-b')]
    monkeypatch.setattr(registry, 'entry_points', lambda group: entries)
    registry._scan.cache_clear()
    try:
        with pytest.warns(UserWarning, match='pkg-b'):
            assert registry.named('duckdb') is second
    finally:
        registry._scan.cache_clear()
