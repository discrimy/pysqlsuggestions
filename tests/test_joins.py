"""Join proposals built from declared foreign keys. Pure — no catalog, no database."""

from __future__ import annotations

from pysqlsuggestions import ForeignKey
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.engine.joins import relation_joins
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
    assert found[0].label == 'auth_user'
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
    """Both are real answers; picking one for the user would be picking wrong half the time."""
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
        'auth_user aut ON a.user_id = aut.id',
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
