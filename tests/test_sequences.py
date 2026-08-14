"""
A sequence is a relation you may not put in a FROM list.

Not because the server refuses it — `SELECT * FROM auth_user_id_seq` returns
`last_value | log_cnt | is_called` quite happily — but because a schema created
by Django has one sequence per table, and doubling the commonest caret in the
language with names nobody is reaching for is a cost paid on every keystroke.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.request import derive_request
from pysqlsuggestions.types import Kind, Request, Table

SNAPSHOT = {
    ('public', 'auth_user'): [('id', 'bigint'), ('email', 'varchar')],
    ('public', 'auth_user_id_seq'): [('last_value', 'bigint')],
    ('billing', 'MonthlyTotals_id_seq'): [('last_value', 'bigint')],
}
KINDS = {('public', 'auth_user_id_seq'): 'sequence', ('billing', 'MonthlyTotals_id_seq'): 'sequence'}


def catalog() -> MemoryCatalog:
    """A snapshot holding one table and two sequences, one of them off the search path."""
    return MemoryCatalog(SNAPSHOT, table_kinds=KINDS, search_path=('public',))


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def test_a_relation_position_never_offers_a_sequence() -> None:
    """
    The assertion the whole filter exists to pass. A Django schema has one
    sequence per table, so without this the commonest caret in the language
    doubles in length with names nobody is reaching for.
    """
    found = offered('SELECT * FROM ')
    assert 'auth_user' in found
    assert 'auth_user_id_seq' not in found


def test_a_prefix_search_does_not_reach_one_either() -> None:
    """
    The search path is not what hides a sequence, so reaching past it must not
    reveal one.

    Asserted as a substring rather than against the list: this name is mixed
    case, so it arrives quoted and qualified — `billing."MonthlyTotals_id_seq"`
    — and an equality check would have passed while the sequence was on offer.
    """
    assert not [text for text in offered('SELECT * FROM Month') if 'MonthlyTotals' in text]


def test_a_schema_qualifier_does_not_list_sequences() -> None:
    """`billing.` lists what you can query in `billing`, which is not everything in it."""
    assert not [text for text in offered('SELECT * FROM billing.') if 'MonthlyTotals' in text]


def test_the_postgres_queries_now_fetch_sequences() -> None:
    """Both paths, because a sequence outside the search path has to be reachable by prefix."""
    tables = POSTGRES.catalog_queries.tables
    search = POSTGRES.catalog_queries.relation_search
    assert tables is not None
    assert search is not None
    assert "'S'" in tables.sql
    assert "'S'" in search.sql
    found = tables.row(('public', 'auth_user_id_seq', 'S', 1, None))
    assert isinstance(found, Table)
    assert found.kind == 'sequence'


def test_dropping_a_sequence_offers_sequences_and_nothing_else() -> None:
    """A relation would not parse there: `DROP SEQUENCE auth_user` is refused."""
    found = offered('DROP SEQUENCE ')
    assert 'auth_user_id_seq' in found
    assert 'auth_user' not in found


def test_altering_one_offers_the_same_names() -> None:
    """Same position, same answer. The two clauses differ only in what may follow."""
    assert 'auth_user_id_seq' in offered('ALTER SEQUENCE ')


def test_a_shared_head_answers_with_both_of_its_phrases() -> None:
    """
    `DROP` begins two clause names now, and neither `DROP` nor `ALTER` is a
    clause in its own right — so both continuations are offered. This is the
    case that broke last slice, when a bare `DROP` among ALTER TABLE's
    continuations made ('DROP',) a phrase and `DROP ⌶` stopped answering TABLE.
    """
    assert set(offered('DROP ')) >= {'TABLE', 'SEQUENCE'}
    assert set(offered('ALTER ')) >= {'TABLE', 'SEQUENCE'}


def test_a_schema_qualifier_names_a_sequence_in_it() -> None:
    """`billing.` after this clause lists what the clause is for, not what a schema holds."""
    found = offered('DROP SEQUENCE billing.')
    assert [text for text in found if 'MonthlyTotals' in text]
    assert 'auth_user' not in found


def test_both_clauses_can_start_a_statement() -> None:
    """
    Not optional: the conformance corpus reports a statement start whose clause
    is missing, and the converse — a clause never reachable — is what would make
    these dead on arrival.
    """
    assert 'DROP SEQUENCE' in POSTGRES.statement_start
    assert 'ALTER SEQUENCE' in POSTGRES.statement_start
    assert 'DROP SEQUENCE' not in TRINO.statement_start


def request(sql: str) -> Request:
    """The request at the end of `sql`."""
    return derive_request(sql, len(sql), POSTGRES)


def test_a_literal_naming_a_sequence_is_a_position() -> None:
    """`nextval` takes a regclass, and the dialect is where that fact lives."""
    found = request("SELECT nextval('")
    assert found.kinds == (Kind.SEQUENCE,)
    assert found.writes_a_literal is True


def test_what_is_typed_inside_the_literal_is_the_prefix() -> None:
    """The quote is not part of what the user is hunting for."""
    assert request("SELECT nextval('aut").prefix == 'aut'


def test_the_span_covers_the_whole_literal() -> None:
    """The answer replaces the literal rather than nesting a second one inside it."""
    sql = "SELECT nextval('aut"
    assert request(sql).replace_span == (15, len(sql))


def test_a_later_argument_is_not_the_position() -> None:
    """
    `setval('seq', 1)` names its sequence first. What the first argument names
    says nothing about the second, so a caret past a comma keeps its silence.
    """
    assert request("SELECT setval('s', '").kinds == ()


def test_an_undeclared_function_is_not_the_position() -> None:
    """A literal inside `lower('…')` is a string, and offering a relation there is nonsense."""
    assert request("SELECT lower('").kinds == ()


def test_a_dialect_declaring_none_has_no_such_position() -> None:
    """ANSI has no nextval, so the same SQL is an ordinary literal there."""
    assert derive_request("SELECT nextval('", 16, ANSI).kinds == ()


def test_a_comparison_literal_still_offers_values() -> None:
    """The older reading of a caret inside a literal is untouched by the new one."""
    found = derive_request("SELECT * FROM auth_user WHERE email = 'a", 40, POSTGRES)
    assert found.kinds == (Kind.VALUE,)


def test_a_sequence_inside_a_literal_is_quoted_into_one() -> None:
    """The whole literal is replaced, so the answer supplies its own quotes."""
    assert "'auth_user_id_seq'" in offered("SELECT nextval('")


def test_a_name_needing_identifier_quotes_keeps_them_inside_the_string() -> None:
    """
    Server-verified: `nextval('billing."MonthlyTotals_id_seq"')` runs, and the
    unquoted spelling is refused with `relation … does not exist`. The string is
    parsed as a regclass, not as text, so the quoting rules are the identifier's.
    """
    assert '\'billing."MonthlyTotals_id_seq"\'' in offered("SELECT nextval('Month")


def test_the_bare_name_is_what_matching_and_the_list_show() -> None:
    """Typing `aut` must find it, and a popup should show a name rather than a quoted string."""
    [found] = [s for s in complete("SELECT nextval('aut", 19, POSTGRES, catalog()) if s.kind is Kind.SEQUENCE]
    assert found.label == 'auth_user_id_seq'
    assert found.text == "'auth_user_id_seq'"


def test_the_same_kind_is_written_bare_where_the_position_is_bare() -> None:
    """One kind, two renderings. `DROP SEQUENCE` takes an identifier, not a string."""
    assert 'auth_user_id_seq' in offered('DROP SEQUENCE ')
    assert "'auth_user_id_seq'" not in offered('DROP SEQUENCE ')
