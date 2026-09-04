"""End-to-end completion against the in-memory catalog. No database, no mocks."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.caches import MemoryCache, cache_key
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.ports import Cache
from pysqlsuggestions.types import Column, Function, Kind, Table
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


def test_a_column_is_qualified_even_with_one_relation() -> None:
    """
    A bare name is unambiguous only until a second relation joins, and the
    caret is usually in a query still being written. The relation's own name
    stands in when there is no alias.
    """
    assert texts('SELECT * FROM auth_user u WHERE ⌶')[:3] == ['u.id', 'u.username', 'u.email']
    assert texts('SELECT * FROM auth_user WHERE ⌶')[:2] == ['auth_user.id', 'auth_user.username']


def test_a_relation_with_no_name_has_nothing_to_qualify_with() -> None:
    """An unaliased derived table would otherwise produce `.id`."""
    assert texts('SELECT * FROM (SELECT id FROM auth_user) WHERE ⌶') == ['id']


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


def test_a_cast_offers_type_names() -> None:
    """`'7 days'::` wants a type, not a column."""
    found = texts("SELECT * FROM reports_report r WHERE r.dt_created > '7 days'::⌶")
    assert found[:3] == ['text', 'integer', 'bigint']
    assert 'id' not in found


def test_a_cast_prefix_filters_the_types() -> None:
    """And they match like anything else."""
    assert texts("SELECT * FROM reports_report r WHERE x > '7 days'::inte⌶") == ['integer', 'interval']


def test_a_cast_names_the_type_of_its_comparison() -> None:
    """
    `'7 days'::interval > ` is an interval comparison however the literal is
    spelled, and the bare literal would have said nothing.

    An interval faces an interval: Postgres has no `interval > timestamptz`, so
    the timestamp column this used to return was never a legal answer.
    """
    assert derive_request(*split_caret("SELECT * FROM r WHERE '7 days'::interval > ⌶"), POSTGRES).comparand_type == (
        'interval'
    )
    assert texts("SELECT * FROM reports_report r WHERE '7 days'::interval > ⌶") == []


def test_a_cast_on_a_column_overrides_the_column_type() -> None:
    """`r.id::text > ` compares text, whatever r.id is."""
    found = texts('SELECT * FROM reports_report r WHERE r.id::text > ⌶')
    assert 'r.name' in found
    assert 'r.id' not in found


def test_a_bare_literal_does_not_narrow() -> None:
    """An unadorned literal is of unknown type in Postgres and coerces to what it meets."""
    found = texts("SELECT * FROM reports_report r WHERE '7 days' > ⌶")
    assert 'r.id' in found
    assert 'r.name' in found


def test_ansi_has_no_cast_operator_so_no_type_position() -> None:
    """Strict ANSI writes CAST(x AS interval); `::` is not a cast there."""
    sql, caret = split_caret("SELECT * FROM reports_report r WHERE x > '7 days'::⌶")
    assert derive_request(sql, caret, ANSI).expecting != 'type'


def test_a_comparison_narrows_to_its_own_type() -> None:
    """`bigint > timestamp` is an error, so a timestamp comparison offers no bigint."""
    found = texts('SELECT * FROM reports_report r WHERE r.id > ⌶')
    assert 'r.id' in found
    assert 'r.database_id' in found
    assert 'r.name' not in found, 'varchar cannot face a bigint'
    assert 'r.is_archived' not in found, 'boolean cannot either'


def test_a_temporal_comparison_offers_only_temporal_columns() -> None:
    """The reported case."""
    found = texts('SELECT * FROM reports_report r JOIN auth_user u ON u.id = r.id WHERE r.dt_created > ⌶')
    assert sorted(found) == ['r.dt_created', 'u.date_joined']


def test_an_unqualified_comparand_is_still_resolved() -> None:
    """`WHERE id > ` finds `id` in whatever relation is in scope."""
    found = texts('SELECT * FROM reports_report r WHERE id > ⌶')
    assert 'r.name' not in found
    assert 'r.id' in found


def test_a_column_of_unknown_type_is_never_hidden() -> None:
    """A type the classifier does not recognise must stay reachable."""
    odd = MemoryCatalog({('public', 't'): [('n', 'bigint'), ('weird', 'tsvector')]})
    assert 't.weird' in texts('SELECT * FROM t WHERE n > ⌶', cat=odd)


def test_narrowing_only_applies_to_a_comparison() -> None:
    """Without one there is nothing to be compatible with."""
    found = texts('SELECT * FROM reports_report r WHERE ⌶')
    assert 'r.name' in found
    assert 'r.id' in found


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
    """The statement described the relation, so nothing is fetched."""
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


def test_a_select_list_name_is_only_offered_where_an_operand_fits() -> None:
    """`GROUP BY name ` has its operand; another name there needs a comma first."""
    sql = 'SELECT count(*) AS total, name FROM reports_report GROUP BY name '
    assert 'name' not in [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]
    assert 'name' in [s.text for s in complete(sql + ', ', len(sql) + 2, POSTGRES, catalog())]


def test_order_by_does_not_offer_ordinals() -> None:
    """
    `ORDER BY 1` is legal and is not a column of any table, so it was offered
    for a while. It is noise: the names are what was meant in almost every case,
    the number is one keystroke to type, and there is nothing to complete once
    it has been typed. The suite this library inherits does not offer them.
    """
    assert '1' not in texts('SELECT name, executions FROM reports_report ORDER BY ⌶')


def test_alias_generation() -> None:
    """`FROM reports_report ⌶` -> `rr`."""
    assert texts('SELECT * FROM reports_report ⌶')[0] == 'rr'


def test_after_as_only_generated_aliases_are_offered() -> None:
    """A table or a keyword there would overwrite the name being invented."""
    assert texts('SELECT * FROM reports_report AS ⌶') == ['rr', 'r', 'rep']


def test_a_typed_alias_is_never_overwritten() -> None:
    """`AS u` used to accept UNION, which matches `u` and is a keyword the clause allows."""
    assert texts('SELECT * FROM auth_user AS u⌶') == []


def test_a_typed_alias_survives_in_any_dialect() -> None:
    """The rule is about the position, not about which words happen to collide."""
    for dialect in (POSTGRES, CLICKHOUSE, TRINO):
        sql, caret = split_caret('SELECT * FROM auth_user AS u⌶')
        assert [s.text for s in complete(sql, caret, dialect, catalog())] == []


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
    cache = _Recorder()
    sql, caret = split_caret('SELECT * FROM reports_report r WHERE r.⌶')
    complete(sql, caret, POSTGRES, catalog(), cache=cache, identity='analyst')
    complete(sql, caret, POSTGRES, catalog(), cache=cache, identity='admin')
    analyst = {key for key in cache.writes if ':+analyst:' in key}
    admin = {key for key in cache.writes if ':+admin:' in key}
    assert analyst
    assert admin
    assert not analyst & admin


class _Recorder(MemoryCache):
    """A `MemoryCache` that remembers the keys written to it, so a test can read them."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def set(self, key: str, value: object, ttl: int | None = None) -> None:
        """Record the key, then store as usual."""
        self.writes.append(key)
        super().set(key, value, ttl)


def test_a_completion_stores_the_key_cache_key_builds() -> None:
    """
    What a prewarm has to write, proved rather than assumed.

    `demo/app.py` fills the cache before anybody types, so it constructs keys the
    reader must then find — and it built them by hand, which is exactly the
    contract this asserts. Nothing else in the suite compares the two sides, so
    a prewarm writing keys no read ever looks up would pass every test and warm
    nothing.
    """
    cache = _Recorder()
    complete('SELECT * FROM ', 14, POSTGRES, catalog(), cache=cache, identity='analyst')
    assert cache_key('analyst', 'postgres', 'tables', None) in cache.writes


def test_cache_prevents_a_second_read(cache: Cache) -> None:
    """A warm cache means the catalog is not touched again."""
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
    """
    With the capability, columns are offered before any FROM clause exists —
    named by the relation they would need, because choosing one is choosing
    that relation too.

    Schema included. This asserted `('auth_user',)` until the column search was
    allowed past the search path, at which point a bare relation name was no
    longer enough to reach what had been found: `FROM invoices` is a clause
    Postgres refuses with `relation "invoices" does not exist`.
    """
    assert 'auth_user.username' in texts('SELECT userna⌶')
    sql, caret = split_caret('SELECT userna⌶')
    found = next(s for s in complete(sql, caret, POSTGRES, catalog()) if s.kind is Kind.COLUMN)
    assert found.relation == ('public', 'auth_user'), 'and it says which, so insertion can write the FROM'


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


def test_an_unclosed_call_does_not_hide_the_from_clause() -> None:
    """
    `SELECT count(⌶ FROM t` is what the author has typed one keystroke into a
    call. The closing paren is missing, so every later token is textually inside
    the argument list — but the FROM plainly belongs to the outer query, and
    losing it means falling back to every column in the database.
    """
    assert texts('SELECT count(⌶ FROM reports_report r') == [
        'r.id',
        'r.name',
        'r.database_id',
        'r.text',
        'r.executions',
        'r.is_archived',
        'r.dt_created',
    ]
    assert texts('SELECT count(r.⌶ FROM reports_report r')[:2] == ['id', 'name']


def test_an_unclosed_subquery_still_keeps_its_own_scope() -> None:
    """The counterpart: a group that opens a query is a real level, closed or not."""
    found = texts('SELECT * FROM (SELECT ⌶ FROM auth_user')
    assert any(t.endswith('username') for t in found)
    assert not any(t.endswith('title') for t in found), 'the level has a FROM of its own to answer from'


def test_a_comparison_narrows_through_a_qualifier() -> None:
    """
    `WHERE r.dt_created > r.⌶` compares against a timestamp just as the
    unqualified form does. Typing the alias and the dot must not lose the type
    that was already established on the left.
    """
    assert texts('SELECT * FROM reports_report r WHERE r.dt_created > r.⌶') == ['dt_created']
    assert texts('SELECT * FROM reports_report r WHERE r.dt_created > r.d⌶') == ['dt_created']


def test_a_cast_written_as_a_call_offers_types() -> None:
    """
    `CAST(x AS ⌶)` is the only cast strict ANSI has, and the `AS` there names a
    type rather than an alias.
    """
    assert 'interval' in texts('SELECT CAST(r.id AS ⌶) FROM reports_report r')
    assert 'bigint' in texts('SELECT CAST(r.id AS ⌶) FROM reports_report r', ANSI)
    assert texts('SELECT CAST(r.id AS ⌶) FROM reports_report r', TRINO) != []


def test_an_alias_after_as_is_still_an_alias() -> None:
    """The cast reading must not swallow the ordinary one."""
    assert texts('SELECT * FROM auth_user AS ⌶') == ['au', 'a', 'aut']
    assert texts('SELECT count(*) AS ⌶ FROM auth_user') == []


def test_a_narrow_search_still_finds_the_closest_match() -> None:
    """
    On a schema too large to enumerate, the exact match must survive the fetch.

    `search_columns` truncates on the server, so a catalog returning whatever
    rows come first loses `created` behind three hundred
    `created_at_variant_NNN`. The port asks for the best matches, not the first
    ones, and the shipped catalog is where that contract is demonstrated.
    """
    wide = {('public', f't{index}'): [(f'created_at_variant_{index:03d}', 'date')] for index in range(300)}
    wide[('public', 'events')] = [('created', 'date')]
    found = complete('SELECT crea', 11, POSTGRES, MemoryCatalog(wide, oversized=True))
    assert [s.text for s in found][0] == 'events.created'


def test_a_table_says_roughly_how_big_it_is() -> None:
    """
    Which of two similarly named relations you want is usually decided by size,
    and the planner already knows it. Short on purpose: the estimate is only as
    fresh as the last ANALYZE, so eight digits would claim a precision it lacks.
    """
    sized = MemoryCatalog(
        {('public', 'events'): [('id', 'bigint')], ('public', 'events_archive'): [('id', 'bigint')]},
        table_rows={('public', 'events'): 81_144_552, ('public', 'events_archive'): 940},
    )
    found = {s.text: s.detail for s in complete('SELECT * FROM ev', 16, POSTGRES, sized)}
    assert found['events'] == 'public.events (table) ~81M rows'
    assert found['events_archive'] == 'public.events_archive (table) ~940 rows'


def test_a_table_of_unknown_size_says_nothing_about_it() -> None:
    """A backend that cannot estimate must not be made to look like it did."""
    found = {s.text: s.detail for s in complete('SELECT * FROM reports_rep', 25, POSTGRES, catalog())}
    assert found['reports_report'] == 'public.reports_report (table)'


def test_an_empty_qualifier_does_not_evict_the_relation_list(cache: Cache) -> None:
    """
    `tables` and `columns` shared a cache key when the table name was empty.

    Every reader once shared a four-tuple key and carried a NUL sentinel in its
    last slot to keep it clear of a column read — except `tables`, which used the
    bare empty string, so `tables(None)` and `columns(None, '')` were one key.
    `SELECT "".⌶` is a quoted empty identifier and reaches the second, which meant
    one such caret either crashed the next relation read or silently emptied it
    for as long as the cache lived.

    The sentinel is now `kind`, a field of the key's grammar, so the conflation
    is unrepresentable rather than avoided. This stays as the regression test for
    the caret that found it.
    """
    warm = catalog()
    complete('SELECT * FROM ', 14, POSTGRES, warm, cache=cache, identity='analyst')
    complete('SELECT "".', 10, POSTGRES, warm, cache=cache, identity='analyst')
    after = [s.text for s in complete('SELECT * FROM ', 14, POSTGRES, warm, cache=cache, identity='analyst')]
    cold = [s.text for s in complete('SELECT * FROM ', 14, POSTGRES, catalog(), identity='analyst')]
    assert after == cold


def test_an_empty_namespace_does_not_empty_the_relation_list(cache: Cache) -> None:
    """
    The other order, and the other half of the same conflation.

    `_key` folded None and the empty string together with `schema or ''`, so
    `tables(None)` — every relation the search path reaches — shared a key with
    `tables('')` — the relations in a schema actually named '', which is none at
    all. `SELECT "".` reads the quoted empty identifier as a namespace, so one
    such caret cached the empty answer over the real one, silently, for as long
    as the cache lived. A sentinel on `tables` does not help: both calls are
    `tables`, and it is the argument that was lost.
    """
    warm = catalog()
    complete('SELECT "".', 10, POSTGRES, warm, cache=cache, identity='analyst')
    after = [s.text for s in complete('SELECT * FROM ', 14, POSTGRES, warm, cache=cache, identity='analyst')]
    cold = [s.text for s in complete('SELECT * FROM ', 14, POSTGRES, catalog(), identity='analyst')]
    assert after == cold


def test_a_bare_reserved_word_is_not_an_output_column() -> None:
    """
    `SELECT NULL` names no column; Postgres calls the result `?column?`.

    `_output_of` took any single-token identifier as an output name, where its
    two neighbouring branches both guard with `reserved_upper` first. So `null`
    became a select-list name — then `rank` quoted it, *because* it is reserved,
    and the local-origin bonus put `"null"` above every real column. In a CTE it
    was the only suggestion offered, and `SELECT c."null"` is an error.
    """
    snapshot = MemoryCatalog({('public', 't'): [('id', 'int'), ('name', 'text')]})
    for word in ('NULL', 'TRUE', 'FALSE', 'CURRENT_USER'):
        sql = f'SELECT {word} FROM t GROUP BY '
        assert '"' not in ' '.join(s.text for s in complete(sql, len(sql), POSTGRES, snapshot)), word

    cte = 'WITH c AS (SELECT NULL FROM t) SELECT  FROM c'
    assert [s.text for s in complete(cte, cte.index('SELECT  FROM c') + 7, POSTGRES, snapshot)] == []


def test_a_negative_limit_offers_nothing_rather_than_slicing_from_the_end() -> None:
    """
    A negative limit reached `ordered[:limit]` and dropped the *last* N.

    Nothing documents a negative limit, and the two layers disagreed about what
    had happened: `complete` forwards `limit * 5` to `resolve`, which ignores it.
    Zero already answers with nothing, so that is the boundary to meet.
    """
    snapshot = MemoryCatalog({('public', 'w'): [(f'col{index}', 'int') for index in range(12)]})
    for limit in (0, -1, -3, -100):
        assert complete('SELECT  FROM w', 7, POSTGRES, snapshot, limit=limit) == []


class _InventsEverything:
    """
    A catalog whose extras exist only because `__getattr__` answers for them.

    A lazy wrapper looks like this, and so does any `MagicMock` standing in for a
    catalog in a downstream test suite.
    """

    def schemas(self, catalog: str | None = None) -> Sequence[str]:
        """No namespaces."""
        del catalog
        return []

    def tables(self, schema: str | None = None) -> Sequence[Table]:
        """No relations."""
        del schema
        return []

    def columns(self, schema: str | None, table: str) -> Sequence[Column]:
        """No columns."""
        del schema, table
        return []

    def functions(self, schema: str | None = None) -> Sequence[Function]:
        """No functions."""
        del schema
        return []

    def __getattr__(self, name: str) -> object:
        """Anything else, as something that is not what the port promises."""
        del name
        return lambda *args, **kwargs: object()


def test_a_capability_that_only_answers_to_its_name_is_not_one() -> None:
    """
    `isinstance` against a runtime-checkable Protocol changed meaning in 3.12.

    Before it the check is `hasattr`, which the class above satisfies for every
    name asked of it, so the engine claimed each capability and then failed on
    the first call with `TypeError: object is not iterable`. From 3.12 the check
    uses `inspect.getattr_static` and the same adapter degrades. Both are
    supported — `requires-python` is `>=3.10` and CI runs three — so the engine
    asks the static question itself and answers the same on all of them.

    This regression guards 3.10 and 3.11 specifically: on 3.12 the interpreter
    already refuses the proxy, so the assertion holds there with or without the
    fix. Kept because those are the versions the project ships for.
    """
    catalog = _InventsEverything()
    for sql, caret in (('SELECT sta', 10), ("SELECT * FROM t WHERE s = 'a", 28), ('SELECT * FROM t JOIN ', 21)):
        assert complete(sql, caret, POSTGRES, catalog) is not None, sql


def test_a_qualifier_naming_a_relation_out_of_scope_offers_nothing() -> None:
    """
    Measured on Postgres: `SELECT auth_user.id FROM auth_group` is `missing
    FROM-clause entry for table "auth_user"`, and so is the shape `resolve.py`
    cited to justify offering these — `WITH a AS (...) SELECT * FROM a WHERE
    auth_user.<caret>`. A relation the catalog knows is not thereby a relation
    this statement may reference.

    The unqualified path already answers this way: `SELECT ema<caret> FROM
    orders` offers nothing from a relation the query does not name, while with
    no FROM at all it offers the column *and* the clause to go with it. The
    qualified path disagreed with it, and with all three servers.
    """
    for sql, marker in (
        ('SELECT auth_user. FROM reports_report', 'auth_user.'),
        ('SELECT * FROM reports_report WHERE auth_user.', 'auth_user.'),
        ('WITH a AS (SELECT 1 AS x) SELECT * FROM a WHERE auth_user.', 'WHERE auth_user.'),
    ):
        caret = sql.index(marker) + len(marker)
        assert complete(sql, caret, POSTGRES, catalog()) == [], sql


def test_a_qualifier_still_reaches_a_relation_the_statement_does_name() -> None:
    """The readings that were always right, and must survive the narrowing."""
    cases = {
        'SELECT auth_user. FROM auth_user': 'auth_user.',
        'SELECT u. FROM auth_user u': 'u.',
        'SELECT * FROM public.': 'public.',
        'SELECT public.auth_user. FROM auth_user': 'public.auth_user.',
    }
    for sql, marker in cases.items():
        caret = sql.index(marker) + len(marker)
        assert complete(sql, caret, POSTGRES, catalog()), sql


def test_a_qualifier_before_any_from_still_names_its_relation() -> None:
    """
    With nothing in scope the author has not written the FROM yet, so the
    qualifier is a reasonable guess at what they are about to name rather than a
    reference to something absent.
    """
    sql = 'SELECT auth_user.'
    assert [s.text for s in complete(sql, len(sql), POSTGRES, catalog(), limit=3)]


def test_a_search_capability_is_checked_on_the_method_it_calls() -> None:
    """
    `SupportsColumnSearch` names two methods, and the guard checked the wrong one.

    `all_columns` returns None by design for every DB-API catalog — that is how it
    says "too many to enumerate" — so `search_columns` is what actually answers,
    and it was the one not verified. An adapter declaring `all_columns` while
    inventing `search_columns` through `__getattr__` therefore passed the guard on
    3.10 and returned None where a sequence was promised.
    """

    class DeclaresOneInventsTheOther(_InventsEverything):
        def all_columns(self) -> None:
            """Too many to enumerate, which is what None means here."""
            return

    assert complete('SELECT sta', 10, POSTGRES, DeclaresOneInventsTheOther()) is not None


def test_a_multi_part_qualifier_reaches_its_relation_on_a_three_level_namespace() -> None:
    """
    Trino has three namespace levels, so `schema.table.` is not "deep enough" for
    the branch that answers a fully qualified column, and fell through to the
    catalog fallback — which the out-of-scope gate then closed. The statement
    names the relation; `public.auth_user.id` is exactly what Trino accepts.
    """
    for sql in (
        'SELECT public.auth_user. FROM public.auth_user',
        'SELECT * FROM public.auth_user WHERE public.auth_user.',
    ):
        caret = sql.index('public.auth_user.') + len('public.auth_user.')
        assert complete(sql, caret, TRINO, catalog()), sql
        assert complete(sql, caret, POSTGRES, catalog()), sql


def test_a_functional_cast_names_the_type_of_its_comparison() -> None:
    """
    `CAST(r.id AS text) > ` is `r.id::text > ` written the other way about.

    Only the postfix spelling was read, so the two forms of one operation gave
    different answers — and `::` is an extension. ANSI declares no cast operator
    at all, which `test_ansi_has_no_cast_operator_so_no_type_position` records,
    so on that dialect the functional form is the *only* spelling and narrowing
    never happened there whatever the author wrote.
    """
    found = texts('SELECT * FROM reports_report r WHERE CAST(r.id AS text) > ⌶')
    assert 'r.name' in found
    assert 'r.id' not in found


def test_a_functional_cast_narrows_on_a_dialect_with_no_cast_operator() -> None:
    """The spelling ANSI actually has, which is why reading only `::` left it with none."""
    sql, caret = split_caret('SELECT * FROM r WHERE CAST(x AS interval) > ⌶')
    assert derive_request(sql, caret, ANSI).comparand_type == 'interval'


def test_a_multi_word_cast_type_survives_being_read() -> None:
    """`double precision` and `timestamp with time zone` are one type name each, not two words."""
    sql, caret = split_caret('SELECT * FROM r WHERE CAST(x AS double precision) > ⌶')
    assert derive_request(sql, caret, POSTGRES).comparand_type == 'double precision'


def test_a_call_that_is_not_a_cast_does_not_name_a_type() -> None:
    """
    The control. `upper(name) > ` ends in the same `)` and names no type, and
    `count(*) > ` is the shape this would most easily mistake for one.
    """
    for sql in ('SELECT * FROM r WHERE upper(name) > ⌶', 'SELECT * FROM r WHERE count(*) > ⌶'):
        marked, caret = split_caret(sql)
        assert derive_request(marked, caret, POSTGRES).comparand_type is None, sql
