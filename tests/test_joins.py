"""Join proposals built from declared foreign keys. Pure — no catalog, no database."""

from __future__ import annotations

from pysqlsuggestions import ForeignKey
from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.joins import condition_columns, join_conditions, relation_joins
from pysqlsuggestions.types import Candidate, Kind, Projection, Relation, Scope, Suggestion


def test_foreign_key_carries_both_sides() -> None:
    """Column tuples on both sides, positionally aligned, so a composite key needs no special case."""
    edge = ForeignKey(
        schema='public',
        table='reports_report',
        columns=('author_id',),
        ref_schema='public',
        ref_table='auth_user',
        ref_columns=('id',),
    )
    assert edge.columns == ('author_id',)
    assert edge.ref_columns == ('id',)


def test_join_is_its_own_kind() -> None:
    """A whole clause is not a table and not a column; a front end may say so."""
    assert Kind.JOIN.value == 'join'


def test_note_defaults_to_none_on_both_carriers() -> None:
    """Additive: every existing construction site keeps working untouched."""
    assert Candidate(text='id', kind=Kind.COLUMN).note is None
    assert Suggestion(text='id', kind=Kind.COLUMN, replace_span=(0, 0), score=1.0).note is None


AUTHOR = ForeignKey(
    schema='public',
    table='reports_report',
    columns=('author_id',),
    ref_schema='public',
    ref_table='auth_user',
    ref_columns=('id',),
)


def scope_of(*relations: tuple[str, str | None]) -> Scope:
    """A scope of plain catalog relations, written as (name, alias) pairs."""
    return Scope(relations=tuple(Relation(alias=alias, path=(name,), source='table') for name, alias in relations))


def test_forward_edge_proposes_the_referenced_relation() -> None:
    """
    The scope relation holds the FK column, so the proposal joins what it points at.

    `au` rather than `u`: the alias generator offers the initials of the
    underscore-separated words first.
    """
    found = relation_joins(scope_of(('reports_report', 'r')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['auth_user au ON r.author_id = au.id']
    assert found[0].match_text == 'auth_user'
    assert found[0].kind is Kind.JOIN
    assert found[0].note == 'fk: auth_user.id'
    assert found[0].position == 0


def test_reverse_edge_proposes_the_referencing_relation() -> None:
    """auth_user holds no FK columns; forward-only would leave this position empty."""
    found = relation_joins(scope_of(('auth_user', 'u')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['reports_report rr ON u.id = rr.author_id']
    assert found[0].note == 'fk: reports_report.author_id'
    assert found[0].position == 1


def test_an_unaliased_relation_qualifies_with_its_own_name() -> None:
    """`FROM reports_report JOIN <caret>` has no alias to point back at."""
    found = relation_joins(scope_of(('reports_report', None)), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['auth_user au ON reports_report.author_id = au.id']


def test_alias_avoids_one_already_in_scope() -> None:
    """`au` is free here, but the generator must still check what the statement already uses."""
    found = relation_joins(scope_of(('reports_report', 'r'), ('users', 'au')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['auth_user a ON r.author_id = a.id']


def test_self_reference_gets_a_distinct_alias() -> None:
    """A table referencing itself must not alias to the copy already written."""
    parent = ForeignKey(
        schema='public',
        table='reports_reportgroup',
        columns=('parent_id',),
        ref_schema='public',
        ref_table='reports_reportgroup',
        ref_columns=('id',),
    )
    found = relation_joins(scope_of(('reports_reportgroup', 'rr')), [parent], POSTGRES)
    assert 'reports_reportgroup r ON rr.parent_id = r.id' in [c.snippet for c in found]


def test_two_edges_to_one_target_stay_two_proposals() -> None:
    """
    Both are real answers; picking one for the user would be picking wrong half the time.

    They share an alias, which is right: only one will be accepted, and the
    condition is what tells them apart in the list.
    """
    created = ForeignKey(
        schema='public',
        table='reports_databaseaccess',
        columns=('user_created_id',),
        ref_schema='public',
        ref_table='auth_user',
        ref_columns=('id',),
    )
    owned = ForeignKey(
        schema='public',
        table='reports_databaseaccess',
        columns=('user_id',),
        ref_schema='public',
        ref_table='auth_user',
        ref_columns=('id',),
    )
    found = relation_joins(scope_of(('reports_databaseaccess', 'a')), [created, owned], POSTGRES)
    assert [c.snippet for c in found] == [
        'auth_user au ON a.user_created_id = au.id',
        'auth_user au ON a.user_id = au.id',
    ]


def test_composite_key_renders_an_and_chain() -> None:
    """Both column pairs, in the constraint's own order."""
    composite = ForeignKey(
        schema='public',
        table='usage',
        columns=('queryfilter_id', 'database_id'),
        ref_schema='public',
        ref_table='links',
        ref_columns=('queryfilter_id', 'database_id'),
    )
    found = relation_joins(scope_of(('usage', 'u')), [composite], POSTGRES)
    assert found[0].snippet == 'links l ON u.queryfilter_id = l.queryfilter_id AND u.database_id = l.database_id'


def test_a_target_in_another_schema_is_qualified() -> None:
    """The bare name would not resolve from a default search path."""
    cross = ForeignKey(
        schema='public',
        table='orders',
        columns=('invoice_id',),
        ref_schema='billing',
        ref_table='invoices',
        ref_columns=('id',),
    )
    found = relation_joins(scope_of(('orders', 'o')), [cross], POSTGRES)
    assert found[0].snippet == 'billing.invoices i ON o.invoice_id = i.id'


def test_a_name_needing_quotes_gets_them() -> None:
    """The snippet path never reaches `quote_if_needed`, so the builder must do it."""
    mixed = ForeignKey(
        schema='public',
        table='orders',
        columns=('total_id',),
        ref_schema='billing',
        ref_table='MonthlyTotals',
        ref_columns=('id',),
    )
    found = relation_joins(scope_of(('orders', 'o')), [mixed], POSTGRES)
    assert found[0].snippet == 'billing."MonthlyTotals" m ON o.total_id = m.id'


def test_a_cte_has_no_constraints() -> None:
    """A relation the statement defined itself is in no catalog and carries no edges."""
    scope = Scope(relations=(Relation(alias='c', path=('c',), source='cte', projection=Projection(columns=('id',))),))
    assert relation_joins(scope, [AUTHOR], POSTGRES) == []


def test_condition_pairs_the_latest_relation_with_an_earlier_one() -> None:
    """`JOIN auth_user u ON <caret>` — one accept finishes the join."""
    found = join_conditions(scope_of(('reports_report', 'r'), ('auth_user', 'u')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['r.author_id = u.id']
    assert found[0].kind is Kind.JOIN
    assert found[0].match_text == 'author_id'
    assert found[0].note == 'fk: auth_user.id'


def test_condition_reads_earlier_relation_first() -> None:
    """Text order follows the statement, not the constraint's direction."""
    found = join_conditions(scope_of(('auth_user', 'u'), ('reports_report', 'r')), [AUTHOR], POSTGRES)
    assert [c.snippet for c in found] == ['u.id = r.author_id']
    assert found[0].match_text == 'author_id'


def test_condition_needs_two_relations() -> None:
    """A single relation has nothing to be joined to."""
    assert join_conditions(scope_of(('reports_report', 'r')), [AUTHOR], POSTGRES) == []


def test_condition_ignores_an_unrelated_pair() -> None:
    """No constraint connects these two, so the position keeps its columns and nothing else."""
    found = join_conditions(scope_of(('reports_report', 'r'), ('billing_invoice', 'b')), [AUTHOR], POSTGRES)
    assert found == []


def test_qualified_left_side_degrades_to_annotated_columns() -> None:
    """`ON r.<caret>` has committed the left side, so the whole condition is no longer expressible."""
    relation = Relation(alias='r', path=('reports_report',), source='table')
    found = condition_columns(relation, [AUTHOR], POSTGRES)
    assert [c.text for c in found] == ['author_id']
    assert found[0].note == 'fk: auth_user.id'
    assert found[0].snippet == 'author_id'


def test_each_proposal_is_an_alternative_and_may_reuse_an_alias() -> None:
    """
    Proposals at one caret are alternatives; exactly one of them will ever be accepted.

    Reserving `a` for the first pushed the rest to `a2` and `a3` — numbered around
    relations the statement does not contain and never will, since accepting any
    one proposal discards the others. Only what the query already says is taken.
    """
    edges = [
        ForeignKey(
            schema='public',
            table='flight',
            columns=('airline_id',),
            ref_schema='public',
            ref_table='airline',
            ref_columns=('id',),
        ),
        ForeignKey(
            schema='public',
            table='flight',
            columns=('origin',),
            ref_schema='public',
            ref_table='airport',
            ref_columns=('code',),
        ),
    ]
    found = relation_joins(scope_of(('flight', 'f')), edges, POSTGRES)
    assert [c.snippet for c in found] == [
        'airline a ON f.airline_id = a.id',
        'airport a ON f.origin = a.code',
    ]


def test_an_edge_with_no_column_pairs_proposes_nothing() -> None:
    """
    `ForeignKey` is a public record with no validation, and an empty side crashed.

    `_fk_column` and `condition_columns` both index `pairs[0]`, so an edge whose
    `columns` or `references` is empty raised IndexError out of `complete` — the
    one thing this library says it never does. Postgres cannot emit such an edge,
    but `SupportsForeignKeys` is a port and a third-party adapter can.
    """
    catalog = MemoryCatalog(
        {('public', 'usage'): [('a', 'bigint')], ('public', 'links'): [('id', 'bigint')]},
        foreign_keys=[
            ForeignKey('public', 'usage', (), 'public', 'links', ('id',)),
            ForeignKey('public', 'usage', ('a',), 'public', 'links', ()),
        ],
    )
    for tail in ('JOIN ', 'JOIN links l ON ', 'JOIN links l ON u.'):
        sql = f'SELECT * FROM usage u {tail}'
        assert complete(sql, len(sql), POSTGRES, catalog) is not None, tail


def test_an_edge_whose_sides_disagree_in_length_proposes_nothing() -> None:
    """
    `zip(strict=False)` turned the one shape that cannot be answered into the one
    failure this module exists to refuse.

    `types.ForeignKey` states the invariant — "both sides are tuples and
    correspond positionally" — and nothing enforced it, so a two-column
    referencing side against a one-column referenced side silently dropped the
    second pair and produced a half-composite condition. That is legal SQL which
    fans out rows the constraint says are unrelated: the `docs/gaps.md` case for
    refusing name-inferred joins, arrived at from the other direction.
    """
    catalog = MemoryCatalog(
        {('public', 'usage'): [('q', 'bigint'), ('d', 'bigint')], ('public', 'links'): [('q', 'bigint')]},
        foreign_keys=[ForeignKey('public', 'usage', ('q', 'd'), 'public', 'links', ('q',))],
    )
    sql = 'SELECT * FROM usage u JOIN '
    assert not [s for s in complete(sql, len(sql), POSTGRES, catalog) if s.kind is Kind.JOIN]


def test_a_generated_alias_that_is_a_reserved_word_is_quoted() -> None:
    """
    Three of the snippet's four identifier slots went through `quote_if_needed`.

    The alias is written bare into `f'{reference} {alias} ON {condition}'` while
    the condition already spells it quoted, so a table whose initials make a
    reserved word produced `order_note on ON r.note_id = "on".id` — which
    Postgres answers with `syntax error at or near "ON"`. The plain ALIAS
    position quotes the same word correctly, so the two disagreed.
    """
    catalog = MemoryCatalog(
        {('public', 'report'): [('note_id', 'bigint')], ('public', 'order_note'): [('id', 'bigint')]},
        foreign_keys=[ForeignKey('public', 'report', ('note_id',), 'public', 'order_note', ('id',))],
    )
    sql = 'SELECT * FROM report r JOIN '
    for offered in complete(sql, len(sql), POSTGRES, catalog):
        if offered.kind is Kind.JOIN:
            assert ' on ON ' not in offered.text, offered.text
            assert '"on"' not in offered.text or ' "on" ON ' in offered.text, offered.text
