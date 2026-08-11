"""Join proposals built from declared foreign keys. Pure — no catalog, no database."""

from __future__ import annotations

from pysqlsuggestions import ForeignKey
from pysqlsuggestions.types import Candidate, Kind, Suggestion


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
