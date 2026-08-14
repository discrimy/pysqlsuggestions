"""
The parenthesised definition list, and the clause that opens it.

`CREATE TABLE t (id ⌶` had nothing to say — gap 1 in `docs/gaps.md` until this
suite existed, and recorded there among the closed entries now. Every caret here
was silent beforehand, so nothing in it can regress from a right answer to a
wrong one — only from silence to an answer.
"""

from __future__ import annotations

from pysqlsuggestions.api import complete, derive_request
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES

SNAPSHOT = {
    ('public', 'users'): [('id', 'bigint'), ('email', 'text')],
    ('public', 'orders'): [('id', 'bigint')],
}


def catalog() -> MemoryCatalog:
    """Two relations, so a case can assert that neither is offered."""
    return MemoryCatalog(SNAPSHOT)


def offered(sql: str) -> list[str]:
    """Suggestion texts at the end of `sql`."""
    return [s.text for s in complete(sql, len(sql), POSTGRES, catalog())]


def clause_at_end(sql: str) -> str | None:
    """The clause the engine believes governs the end of `sql`."""
    return derive_request(sql, len(sql), POSTGRES).clause


def test_create_offers_the_word_that_finishes_it() -> None:
    """Derived from the clause name by `_half_written_clauses`, like `GROUP ⌶`."""
    assert offered('CREATE ') == ['TABLE']


def test_drop_and_alter_still_offer_their_own_words() -> None:
    """
    A new head must not claim theirs.

    `_half_written_clauses` skips a head that is already a phrase, so a clause
    named `CREATE` alone would have made `('CREATE',)` a phrase and silenced
    this. Two words is what keeps all three heads answering.
    """
    assert 'TABLE' in offered('DROP ')
    assert 'TABLE' in offered('ALTER ')


def test_the_longer_clause_wins_over_a_bare_table() -> None:
    """
    `clause_at` ranks by (end offset, word count), so two words beat one.

    This is the whole reason `TABLE` is modellable at all: without it the
    definition list would be governed by the bare form and offer relations.
    """
    assert clause_at_end('CREATE TABLE t (id ') == 'CREATE TABLE'


def test_the_relation_being_created_is_not_suggested() -> None:
    """
    The name is the author's to invent, so `Kind.TABLE` here is a wrong answer.

    `WINDOW` carries the same empty `suggests` for the same reason.
    """
    assert offered('CREATE TABLE ') == []


def test_if_not_exists_is_reached_by_typing() -> None:
    """
    `before_the_item`, which `request.py` gates behind a non-empty prefix.

    The caret after `CREATE TABLE ` is where a name is being typed, and a
    keyword ranked above it would be in the way. Behind a prefix it costs
    nothing.
    """
    assert offered('CREATE TABLE if') == ['IF NOT EXISTS']


def test_the_definition_list_is_not_offered_the_clause_continuations() -> None:
    """
    A clause's `followed_by` reaches inside its parens, where it cannot parse.

    Measured before the clause was written: `followed_by=('AS',)` put `AS` at
    this caret, and `CREATE TABLE t (id AS` parses as nothing. The clause
    declares no continuations at all, so there is nothing here to leak.

    Named for what it forbids rather than asserting an empty list, because the
    position is not empty — it answers with types. What must never appear is a
    word about the *statement* in a place that is about one column.
    """
    found = offered('CREATE TABLE t (id ')
    assert 'AS' not in found
    assert not {'WHERE', 'FROM', 'ORDER BY'} & set(found)


def test_create_table_is_offered_where_a_statement_may_begin() -> None:
    """An empty editor is exactly where it is a useful suggestion."""
    assert 'CREATE TABLE' in offered('')


def test_explain_does_not_offer_it() -> None:
    """`EXPLAIN CREATE TABLE t (id int)` is a syntax error."""
    assert 'CREATE TABLE' not in offered('EXPLAIN ')


def test_a_column_name_is_the_authors_to_invent() -> None:
    """
    The first word of each item names something that does not exist yet.

    The same silence `opens_a_name_list` gives a column alias list, reached by a
    different rule: an alias list renames existing columns and never takes a
    type, so the two are separate lists rather than one.
    """
    assert offered('CREATE TABLE t (') == []
    assert offered('CREATE TABLE t (id integer, ') == []


def test_a_type_belongs_after_the_name() -> None:
    """
    `Kind.TYPE`, answered from `dialect.types` — the list `CAST(x AS ⌶)` reads.

    `docs/gaps.md` predicted exactly this: "the candidates already exist".
    """
    found = offered('CREATE TABLE t (id ')
    assert 'text' in found
    assert 'integer' in found


def test_the_type_position_offers_no_relation() -> None:
    """A definition list is not a FROM list, however alike the parens look."""
    found = offered('CREATE TABLE t (id ')
    assert 'users' not in found
    assert 'orders' not in found


def test_a_multi_word_type_is_offered_whole() -> None:
    """
    Which is what pays for the trade the count makes — see the test below.

    Offered from the one caret where a type begins, so accepting it never
    reaches the caret that cannot finish it.
    """
    assert 'double precision' in offered('CREATE TABLE t (id ')


def test_a_hand_typed_half_of_a_two_word_type_reaches_constraints() -> None:
    """
    The known cost of counting words rather than parsing the item.

    `double ` is two words in, so it reads as a constraint position and
    `precision` is not offered. Deliberate: the alternative offers the type list
    at every caret past the name, which puts a second type after a complete one
    and writes `id integer text`. A missing answer for a hand-typist beats a
    wrong answer for everyone.
    """
    assert 'precision' not in offered('CREATE TABLE t (id double ')


def test_constraints_follow_a_type() -> None:
    """The clause's own `defines_columns`, carried on `continues`."""
    found = offered('CREATE TABLE t (id integer ')
    assert 'NOT NULL' in found
    assert 'PRIMARY KEY' in found
    assert 'REFERENCES' in found


def test_a_constraint_may_follow_a_constraint() -> None:
    """`id integer NOT NULL DEFAULT 0` is one item with two of them."""
    assert 'DEFAULT' in offered('CREATE TABLE t (id integer NOT NULL ')


def test_a_written_constraint_is_not_offered_again() -> None:
    """
    `id integer NOT NULL NOT NULL` parses as nothing.

    These words ride on `Request.continues`, and `engine/local.py` renders that
    field verbatim — the `_unchosen` filter that keeps a clause from repeating
    its own continuations lives in `resolve._keywords`, which `continues` skips
    by design. Every earlier user of the field was a set of *alternatives at one
    caret*, where a repeat is impossible; a constraint list is the first where
    several may be written in sequence, so it is the first that needs the
    filter.
    """
    found = offered('CREATE TABLE t (id integer NOT NULL ')
    assert 'NOT NULL' not in found
    assert 'NULL' not in found


def test_a_constraint_written_in_another_item_still_counts_for_nothing() -> None:
    """The filter is per item, so a comma brings the whole list back."""
    assert 'NOT NULL' in offered('CREATE TABLE t (id integer NOT NULL, email text ')


def test_a_type_is_not_offered_where_a_constraint_belongs() -> None:
    """`CREATE TABLE t (id integer text` parses as nothing."""
    assert 'text' not in offered('CREATE TABLE t (id integer ')


def test_a_nested_paren_is_not_the_definition_list() -> None:
    """
    Every construct that nests sits one level deeper, and the depth test
    excludes all of them without naming any: a type's own parameters, a column
    CHECK, a foreign key's column list.
    """
    assert offered('CREATE TABLE t (id numeric(10, ') == []
    assert offered('CREATE TABLE t (id integer CHECK (id > ') == []
    assert offered('CREATE TABLE t (id integer REFERENCES users (') == []


def test_a_qualified_name_still_opens_a_definition_list() -> None:
    """`CREATE TABLE public.t (…)` is the same list, written with a schema."""
    assert 'text' in offered('CREATE TABLE public.t (id ')


def test_a_half_typed_column_name_is_still_a_name() -> None:
    """
    The word under the caret is being typed, not finished.

    Counting it would make the first character of every column name look like a
    completed name and answer with types.
    """
    assert offered('CREATE TABLE t (i') == []


def test_a_half_typed_type_is_narrowed_by_its_prefix() -> None:
    """The word under the caret is skipped by the count and used by the ranker."""
    assert 'integer' in offered('CREATE TABLE t (id int')


def test_a_definition_list_outside_the_clause_is_untouched() -> None:
    """
    `INSERT INTO users (⌶` is a column list of an existing relation, and the
    rule must not reach it — its clause declares no `defines_columns`.

    Compared on the last segment because *how* the column is qualified there is
    a separate decision, and one this test has no business pinning.
    """
    found = offered('INSERT INTO users (')
    assert [text.split('.')[-1] for text in found] == ['id', 'email']
