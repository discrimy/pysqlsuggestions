"""
The server against a real Postgres, over the pg8000 driver.

Everything in `tests/lsp/` runs against fakes, so this is the only place a
paramstyle mismatch, a dialect query the driver rejects, or rows shaped
differently by a different driver would be caught. pg8000 is here rather than
psycopg2 because it is pure Python, which is what lets one VSIX serve every
platform — and it is exercised nowhere else in this suite.

The fixture proves the catalog answers before any test runs. `Session.suggest`
degrades instead of raising, so a test written carelessly here would pass just
as happily against an unreachable database and prove nothing at all.
"""

from __future__ import annotations

import pytest
from lsprotocol.types import InsertTextFormat, TextEdit

from pysqlsuggestions_lsp.connections import Profile
from pysqlsuggestions_lsp.server import Session

PROFILE = Profile(
    dialect='postgres',
    host='localhost',
    port=57432,
    database='report_service',
    user='report',
    password='report',
)
"""Matches POSTGRES_DSN in tests/integration/conftest.py."""

pytestmark = pytest.mark.integration


@pytest.fixture(scope='module')
def session() -> Session:
    """
    A session whose catalog has been proven to answer.

    Skipping rather than failing keeps the suite runnable without docker, which
    is the pattern every other fixture here follows.
    """
    live = Session(profile=PROFILE)
    catalog = live.catalog()
    if catalog is None:
        pytest.skip('no pg8000 catalog could be built')
    try:
        tables = catalog.tables()
    except Exception as error:  # noqa: BLE001
        pytest.skip(f'postgres not reachable ({error}); run docker/docker-compose.yml')
    assert tables, 'the catalog answered, but with no relations — the fixture schema is missing'
    return live


def labels(live: Session, text: str) -> list[str]:
    """What a client would show for a caret at the end of `text`."""
    return [item.label for item in live.suggest(text, len(text))]


def test_columns_come_from_the_database(session: Session) -> None:
    """Nothing in the statement names these, so they can only have been read."""
    offered = labels(session, 'SELECT * FROM auth_user u WHERE u.')
    assert 'username' in offered


def test_a_qualifier_collapses_the_answer_to_columns(session: Session) -> None:
    """No keywords, no functions, no tables — the README's own example."""
    offered = labels(session, 'SELECT * FROM auth_user u WHERE u.')
    assert 'SELECT' not in offered
    assert 'select' not in offered


def test_the_pg8000_path_reads_the_catalog_at_all(session: Session) -> None:
    """
    A paramstyle mismatch surfaces here and nowhere else.

    `render` doubles literal `%` for format-style drivers and the introspection
    SQL is full of them; getting it wrong raises an opaque IndexError from
    inside the driver rather than anything readable.
    """
    catalog = session.catalog()
    assert catalog is not None
    assert [table for table in catalog.tables() if table.name == 'auth_user']


JOIN_CARET = 'SELECT * FROM reports_report r JOIN '
"""The caret goes at the end of this, past the space — `len`, not a magic number."""


def test_a_join_proposal_arrives_with_its_condition(session: Session) -> None:
    """
    The foreign keys the database declares, as a whole clause.

    This is the feature with the most moving parts between the server and the
    backend — SupportsForeignKeys reached through a driver nothing else here
    uses — and the one a fake catalog cannot stand in for.
    """
    offered = labels(session, JOIN_CARET)
    conditions = [label for label in offered if ' ON ' in label]
    assert conditions, f'no join proposal among {offered[:10]}'


def test_a_join_proposal_names_the_constraint_it_came_from(session: Session) -> None:
    """
    `fk: auth_user.id` is the teaching part of the list.

    It travels on `note`, separately from `detail`, and a client has one field
    for both — so this is also the check that neither was dropped on the way.
    """
    details = [item.detail or '' for item in session.suggest(JOIN_CARET, len(JOIN_CARET))]
    assert any('fk: ' in detail for detail in details), f'no constraint named among {details[:5]}'


def test_join_proposals_fire_from_both_ends_of_a_constraint(session: Session) -> None:
    """
    A constraint is directed and a join is not.

    reports_report references auth_user, and reports_runlog references
    reports_report. Both belong at this caret, and only a real catalog has both.
    """
    offered = labels(session, JOIN_CARET)
    assert any(label.startswith('auth_user ') for label in offered), 'no many-to-one proposal'
    assert any(label.startswith('reports_runlog ') for label in offered), 'no one-to-many proposal'


def test_a_statement_snippet_is_offered_as_a_template(session: Session) -> None:
    """
    Stops become placeholders, on the kind that actually carries them.

    Only Kind.SNIPPET does — a join proposal inserts a finished clause and
    leaves no blanks, so this is the one position where the snippet path is
    reachable against a live catalog.
    """
    snippets = [item for item in session.suggest('', 0) if item.insert_text_format == InsertTextFormat.Snippet]
    assert snippets, 'no template offered where a statement can start'
    assert '$1' in (snippets[0].text_edit.new_text if isinstance(snippets[0].text_edit, TextEdit) else '')


def test_the_cache_spares_the_database_a_second_read(session: Session) -> None:
    """
    Catalog reads are prefix-independent, so a warm session stops touching the server.

    The cache is keyed by role first; this only checks that something was
    stored, which is what stops a keystroke becoming a query.
    """
    session.suggest('SELECT * FROM auth_user u WHERE u.', 34)
    assert session.cache
