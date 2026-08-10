"""End-to-end completion against the in-memory catalog. No database, no mocks."""

from __future__ import annotations

import pytest

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.types import Kind
from tests.corpus.cases import split_caret

SNAPSHOT = {
    ('public', 'reports_report'): [
        ('id', 'bigint'),
        ('name', 'varchar(100)'),
        ('database_id', 'bigint'),
        ('text', 'text'),
        ('executions', 'bigint'),
        ('is_archived', 'boolean'),
        ('dt_created', 'timestamp with time zone'),
    ],
    ('public', 'reports_database'): [
        ('id', 'bigint'),
        ('title', 'varchar(256)'),
        ('type', 'varchar(256)'),
        ('host', 'varchar(256)'),
    ],
    ('public', 'auth_user'): [
        ('id', 'bigint'),
        ('username', 'varchar(150)'),
        ('email', 'varchar(254)'),
        ('date_joined', 'timestamp with time zone'),
    ],
    ('billing', 'invoices'): [('id', 'bigint'), ('period', 'date'), ('amount', 'numeric')],
    ('billing', 'MonthlyTotals'): [('Period', 'date'), ('Amount', 'numeric')],
}


def catalog() -> MemoryCatalog:
    """A fresh catalog per test, so `calls` is meaningful."""
    return MemoryCatalog(SNAPSHOT)


def texts(marked: str, dialect: Dialect = POSTGRES, cat: MemoryCatalog | None = None) -> list[str]:
    """Suggestion texts for ⌶-marked SQL."""
    sql, caret = split_caret(marked)
    return [s.text for s in complete(sql, caret, dialect, cat if cat is not None else catalog())]


def test_qualified_columns() -> None:
    """The alias resolves and only that relation's columns come back."""
    assert texts('SELECT * FROM reports_report r WHERE r.⌶') == [
        'id',
        'name',
        'database_id',
        'text',
        'executions',
        'is_archived',
        'dt_created',
    ]


def test_columns_are_in_declaration_order_not_alphabetical() -> None:
    """Authors put important columns first; attnum beats sorting by name."""
    assert texts('SELECT * FROM reports_report r WHERE r.⌶')[0] == 'id'


def test_prefix_filters_and_ranks() -> None:
    """An exact prefix outranks a word-boundary match."""
    assert texts('SELECT * FROM reports_report r WHERE r.na⌶') == ['name']


def test_word_boundary_subsequence() -> None:
    """`di` finds database_id without matching everything."""
    assert texts('SELECT * FROM reports_report r WHERE r.di⌶') == ['database_id']


def test_a_whole_word_inside_the_name_matches() -> None:
    """
    `data` finds reports_database.

    Snake_case names bury the meaningful word: nobody types `reports_` to reach
    `reports_database`, they type `data`. The existing helper matched this by
    substring, so failing to would be a regression for its users.
    """
    assert 'reports_database' in texts('SELECT * FROM data⌶')


def test_a_word_match_scores_below_a_real_prefix() -> None:
    """The same relation, reached two ways: `reports` prefixes it, `data` is a word inside it."""

    def score_of(marked: str) -> float:
        sql, caret = split_caret(marked)
        found = complete(sql, caret, POSTGRES, catalog())
        return next(s.score for s in found if s.text == 'reports_database')

    assert score_of('SELECT * FROM data⌶') < score_of('SELECT * FROM reports⌶')


def test_mid_word_fragments_match_but_rank_last() -> None:
    """
    `atabas` is a substring, not a word. The helper this supersedes matched it,
    and its users rely on that — `mail` finding `email` is the same rule — so
    substring is the weakest tier rather than no tier at all.
    """
    assert texts('SELECT * FROM atabas⌶') == ['reports_database']


def details(marked: str) -> dict[str, str]:
    """Suggestion text -> detail."""
    sql, caret = split_caret(marked)
    return {s.text: s.detail or '' for s in complete(sql, caret, POSTGRES, catalog())}


def test_the_detail_names_the_table_not_the_alias() -> None:
    """`u.id` inserts the alias; the detail should say what the alias stands for."""
    found = details('SELECT * FROM auth_user u WHERE u.⌶')
    assert found['id'].startswith('auth_user.id')


def test_the_detail_names_the_table_when_columns_are_qualified() -> None:
    """Both halves at once: insert `u.username`, describe it as `auth_user.username`."""
    found = details('SELECT * FROM auth_user u JOIN reports_report r ON r.author_id = u.id WHERE ⌶')
    assert found['u.username'].startswith('auth_user.username')
    assert found['r.name'].startswith('reports_report.name')


def test_a_cte_describes_itself_by_its_own_name() -> None:
    """`a` is not an alias for auth_user — it is the relation's name."""
    found = details('WITH a AS (SELECT * FROM auth_user) SELECT * FROM a WHERE a.⌶')
    assert found['email'].startswith('a.email')


def test_a_derived_table_falls_back_to_its_alias() -> None:
    """It has no name of its own, so the alias is the only thing to call it."""
    found = details('SELECT * FROM (SELECT id FROM auth_user) d WHERE d.⌶')
    assert found['id'].startswith('d.id')


def test_one_relation_needs_no_qualifier() -> None:
    """A bare name is unambiguous here, and shorter."""
    assert texts('SELECT * FROM auth_user WHERE ⌶')[:3] == ['id', 'username', 'email']


def test_two_relations_qualify_every_column() -> None:
    """`WHERE id` against two tables that both have one does not parse."""
    found = texts('SELECT * FROM auth_user u JOIN reports_report r ON r.author_id = u.id WHERE ⌶')
    assert 'u.id' in found
    assert 'r.id' in found
    assert 'id' not in found


def test_an_unaliased_relation_qualifies_with_its_name() -> None:
    """No alias to use, so the table name stands in."""
    found = texts('SELECT * FROM auth_user JOIN reports_report r ON r.author_id = auth_user.id WHERE ⌶')
    assert 'auth_user.username' in found


def test_qualifying_does_not_change_what_has_to_be_typed() -> None:
    """Matching runs against the bare name: `usern` still finds `u.username`."""
    found = texts('SELECT * FROM auth_user u JOIN reports_report r ON r.author_id = u.id WHERE usern⌶')
    assert found == ['u.username']


def test_a_typed_qualifier_is_not_repeated() -> None:
    """`u.` already names the relation, so the column comes back bare."""
    found = texts('SELECT * FROM auth_user u JOIN reports_report r ON r.author_id = u.id WHERE u.⌶')
    assert found[:3] == ['id', 'username', 'email']


def test_both_relations_columns_survive_deduplication() -> None:
    """Two columns called `id` are two different columns, not one."""
    found = texts('SELECT * FROM auth_user u JOIN reports_report r ON r.author_id = u.id WHERE i⌶')
    assert sorted(t for t in found if t.endswith('.id')) == ['r.id', 'u.id']


def test_a_comparison_narrows_to_its_own_type() -> None:
    """`bigint > timestamp` is an error, so a timestamp comparison offers no bigint."""
    found = texts('SELECT * FROM reports_report r WHERE r.id > ⌶')
    assert 'id' in found
    assert 'database_id' in found
    assert 'name' not in found, 'varchar cannot face a bigint'
    assert 'is_archived' not in found, 'boolean cannot either'


def test_a_temporal_comparison_offers_only_temporal_columns() -> None:
    """The reported case."""
    found = texts('SELECT * FROM reports_report r JOIN auth_user u ON u.id = r.id WHERE r.dt_created > ⌶')
    assert sorted(found) == ['r.dt_created', 'u.date_joined']


def test_an_unqualified_comparand_is_still_resolved() -> None:
    """`WHERE id > ` finds `id` in whatever relation is in scope."""
    found = texts('SELECT * FROM reports_report r WHERE id > ⌶')
    assert 'name' not in found
    assert 'id' in found


def test_a_column_of_unknown_type_is_never_hidden() -> None:
    """A type the classifier does not recognise must stay reachable."""
    odd = MemoryCatalog({('public', 't'): [('n', 'bigint'), ('weird', 'tsvector')]})
    assert 'weird' in texts('SELECT * FROM t WHERE n > ⌶', cat=odd)


def test_narrowing_only_applies_to_a_comparison() -> None:
    """Without one there is nothing to be compatible with."""
    found = texts('SELECT * FROM reports_report r WHERE ⌶')
    assert 'name' in found
    assert 'id' in found


def test_operators_are_offered_after_a_completed_operand() -> None:
    """The likeliest next token leads, and none of them is case-folded or quoted."""
    found = texts('SELECT * FROM auth_user u WHERE u.id ⌶')
    assert found[:6] == ['=', '<>', '<', '<=', '>', '>=']
    assert 'IS NULL' in found
    assert 'AND' not in found, 'no comparison written yet'


def test_a_finished_predicate_offers_connectives_instead() -> None:
    """`WHERE u.id > 1 ` takes AND or the next clause, and no second comparison."""
    found = texts('SELECT * FROM auth_user u WHERE u.id > 1 ⌶')
    assert 'AND' in found
    assert 'ORDER BY' in found
    assert '=' not in found


def test_substring_matches_columns_too() -> None:
    """`mail` finds `email`. The helper this supersedes did this, and its users rely on it."""
    assert texts('SELECT * FROM auth_user u WHERE u.mail⌶') == ['email']


def test_a_prefix_hit_outranks_a_substring_hit() -> None:
    """`e` prefixes email and sits mid-word in the others; the prefix wins."""
    assert texts('SELECT * FROM auth_user u WHERE u.e⌶')[0] == 'email'


def test_tables_in_from_clause() -> None:
    """A relation position offers relations and schemas, never columns."""
    found = texts('SELECT * FROM reports_⌶')
    assert 'reports_report' in found
    assert 'reports_database' in found
    assert 'id' not in found


def test_schema_qualified_tables() -> None:
    """`billing.` lists that schema's relations, each ready to paste."""
    assert sorted(texts('SELECT * FROM billing.⌶')) == ['"MonthlyTotals"', 'invoices']


def test_quoted_identifier_is_quoted_on_the_way_out() -> None:
    """A mixed-case name must come back ready to paste into Postgres."""
    sql, caret = split_caret('SELECT * FROM billing."MonthlyTotals" m WHERE m.⌶')
    found = [s.text for s in complete(sql, caret, POSTGRES, catalog())]
    assert found == ['"Period"', '"Amount"']


def test_clickhouse_preserves_case_without_quoting() -> None:
    """ClickHouse does not fold, so a mixed-case identifier needs no quotes there."""
    sql, caret = split_caret('SELECT * FROM billing."MonthlyTotals" m WHERE m.⌶')
    found = [s.text for s in complete(sql, caret, CLICKHOUSE, catalog())]
    assert found == ['Period', 'Amount']


def test_cte_costs_no_catalog_call() -> None:
    """plan.md §3.3: the statement described the relation, so nothing is fetched."""
    cat = catalog()
    found = texts('WITH recent AS (SELECT id, name FROM reports_report) SELECT r.⌶ FROM recent r', cat=cat)
    assert found == ['id', 'name']
    assert cat.calls == []


def test_cte_selecting_a_star_expands_through_the_catalog() -> None:
    """The star could not be resolved without one, so exactly one lookup happens."""
    cat = catalog()
    found = texts('WITH a AS (SELECT * FROM reports_database) SELECT a.⌶ FROM a', cat=cat)
    assert found == ['id', 'title', 'type', 'host']
    assert cat.calls == [('columns', '', 'reports_database')]


def test_order_by_offers_select_list_names() -> None:
    """No catalog can supply an output alias."""
    found = texts('SELECT count(*) AS total, name FROM reports_report GROUP BY name ORDER BY ⌶')
    assert found[:2] == ['total', 'name']


def test_order_by_offers_ordinals() -> None:
    """`ORDER BY 1` is legal and is not a column of any table."""
    assert '1' in texts('SELECT name, executions FROM reports_report ORDER BY ⌶')


def test_alias_generation() -> None:
    """`FROM reports_report ⌶` -> `rr`."""
    assert texts('SELECT * FROM reports_report ⌶')[0] == 'rr'


def test_no_catalog_still_answers_from_the_query() -> None:
    """The degraded mode a backend without an adapter gets."""
    sql, caret = split_caret('WITH recent AS (SELECT id, name FROM t) SELECT r.⌶ FROM recent r')
    assert [s.text for s in complete(sql, caret, POSTGRES, None)] == ['id', 'name']


def test_literal_offers_nothing() -> None:
    """Inside a string there is nothing useful to say."""
    assert texts("SELECT * FROM reports_report WHERE name = 'ab⌶") == []


def test_limit_is_respected() -> None:
    """The editor asked for a bounded list."""
    sql, caret = split_caret('SELECT ⌶ FROM reports_report r')
    assert len(complete(sql, caret, POSTGRES, catalog(), limit=3)) == 3


def test_cache_is_keyed_by_role() -> None:
    """Two roles must not share a cached read; the key shape is a documented contract."""
    cache: dict[tuple[object, ...], object] = {}
    sql, caret = split_caret('SELECT * FROM reports_report r WHERE r.⌶')
    complete(sql, caret, POSTGRES, catalog(), cache=cache, identity='analyst')
    complete(sql, caret, POSTGRES, catalog(), cache=cache, identity='admin')
    roles = {key[0] for key in cache}
    assert roles == {'analyst', 'admin'}


def test_cache_prevents_a_second_read() -> None:
    """A warm cache means the catalog is not touched again."""
    cache: dict[tuple[object, ...], object] = {}
    cat = catalog()
    sql, caret = split_caret('SELECT * FROM reports_report r WHERE r.⌶')
    complete(sql, caret, POSTGRES, cat, cache=cache, identity='analyst')
    first = len(cat.calls)
    complete(sql, caret, POSTGRES, cat, cache=cache, identity='analyst')
    assert len(cat.calls) == first


def test_column_search_degrades_when_unsupported() -> None:
    """Without SupportsColumnSearch a bare SELECT offers no columns, and does not fail."""

    class Bare:
        """A minimal catalog: the four required methods and nothing else."""

        def schemas(self) -> list[str]:
            return ['public']

        def tables(self, schema: str | None = None) -> list[object]:
            return []

        def columns(self, schema: str | None, table: str) -> list[object]:
            return []

        def functions(self, schema: str | None = None) -> list[object]:
            return []

    sql, caret = split_caret('SELECT ⌶')
    found = complete(sql, caret, POSTGRES, Bare())  # type: ignore[arg-type]
    assert all(s.kind is not Kind.COLUMN for s in found)


def test_bare_select_uses_column_search_when_supported() -> None:
    """With the capability, columns are offered before any FROM clause exists."""
    assert 'username' in texts('SELECT userna⌶')


@pytest.mark.parametrize(
    ('dialect', 'expected'),
    [(POSTGRES, Kind.TABLE), (CLICKHOUSE, Kind.TABLE), (TRINO, Kind.SCHEMA)],
)
def test_namespace_depth_changes_what_a_qualifier_yields(dialect: Dialect, expected: Kind) -> None:
    """One tuple, three answers — the clearest demonstration of Namespace.levels."""
    sql, caret = split_caret('SELECT * FROM billing.⌶')
    found = complete(sql, caret, dialect, catalog())
    assert found
    assert {s.kind for s in found} == {expected}
