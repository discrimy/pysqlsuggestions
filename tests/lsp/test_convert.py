"""
Turning a ranked suggestion into an item without losing the ranking.

Two things are easy to lose here and hard to notice missing: the order the
engine put its suggestions in, and the span it said to replace. A client
re-sorts by its own fuzzy score unless told otherwise, and re-derives a word
boundary unless given a range — and in both cases the list still appears, still
holds the right items, and is quietly wrong.
"""

from __future__ import annotations

from lsprotocol.types import (
    CompletionItem,
    CompletionItemKind,
    CompletionItemTag,
    InsertTextFormat,
    TextEdit,
)

from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.types import Availability, Kind, Suggestion
from pysqlsuggestions_lsp.convert import match_term, to_item
from pysqlsuggestions_lsp.documents import line_starts


def suggestion(text: str, kind: Kind, span: tuple[int, int] = (0, 0), **extra: object) -> Suggestion:
    """A suggestion as rank would emit it."""
    return Suggestion(text=text, kind=kind, replace_span=span, score=1.0, **extra)  # type: ignore[arg-type]


def item(statement: str, offered: Suggestion, index: int = 0) -> CompletionItem:
    """Convert against a single-line document, which is what most cases need."""
    return to_item(statement, 0, line_starts(statement), offered, index, POSTGRES)


def edit_of(result: CompletionItem) -> TextEdit:
    """
    The item's edit, asserted to be the plain kind.

    `text_edit` is typed as a union with `InsertReplaceEdit`, which this module
    never emits — an insert-replace edit lets the client choose between two
    ranges, and the whole point of carrying `replace_span` is that the choice is
    already made.
    """
    assert isinstance(result.text_edit, TextEdit)
    return result.text_edit


def test_sort_text_preserves_the_engine_order() -> None:
    """
    The engine ranked these; a client must not re-rank them.

    Zero-padded so string comparison and numeric order agree: '10' sorts before
    '9' otherwise, which silently reverses the tail of every list.
    """
    first = item('SELECT ', suggestion('a', Kind.COLUMN, (7, 7)), index=0)
    tenth = item('SELECT ', suggestion('b', Kind.COLUMN, (7, 7)), index=10)
    assert first.sort_text is not None
    assert tenth.sort_text is not None
    assert first.sort_text < tenth.sort_text
    assert len(first.sort_text) == len(tenth.sort_text)


def test_a_qualified_column_is_found_by_its_column_name() -> None:
    """`usern` must still find `u.username`, which is the whole point of filter_text."""
    assert match_term(suggestion('u.username', Kind.COLUMN)) == 'username'


def test_a_bare_column_is_found_by_itself() -> None:
    """Nothing to strip, nothing to reconstruct."""
    assert match_term(suggestion('id', Kind.COLUMN)) == 'id'


def test_a_value_is_found_without_typing_the_quote() -> None:
    """Nobody types the leading quote to find a literal."""
    assert match_term(suggestion("'postgres'", Kind.VALUE)) == 'postgres'


def test_a_join_is_found_by_its_label() -> None:
    """
    The label leads with the relation name, which is what gets typed.

    rank matches on `match_text or label or text` but only `label` reaches a
    Suggestion, so the term is reconstructed here rather than read off.
    """
    proposal = suggestion(
        'flight f ON b.flight_id = f.id',
        Kind.JOIN,
        label='flight f ON b.flight_id = f.id',
    )
    assert match_term(proposal) == 'flight f ON b.flight_id = f.id'


def test_the_span_becomes_the_edit_range() -> None:
    """
    The range comes from replace_span, never from a word boundary.

    Re-deriving it is what drops the qualifier: `where u.crea` accepting
    `created_at` must give `where u.created_at`, not `where created_at`.
    """
    edit = edit_of(item('SELECT crea FROM t', suggestion('created_at', Kind.COLUMN, (7, 11))))
    assert edit.range.start.character == 7
    assert edit.range.end.character == 11
    assert edit.new_text == 'created_at'


def test_the_base_offset_shifts_the_range() -> None:
    """A statement after a `;` reports spans relative to itself, not to the document."""
    document = 'SELECT 1;SELECT crea FROM t'
    result = to_item(
        'SELECT crea FROM t',
        9,
        line_starts(document),
        suggestion('created_at', Kind.COLUMN, (7, 11)),
        0,
        POSTGRES,
    )
    assert edit_of(result).range.start.character == 16


def test_a_span_on_a_later_line_becomes_a_range_on_that_line() -> None:
    """Offsets are one number; LSP wants two, and the line is not always zero."""
    document = 'SELECT\ncrea FROM t'
    result = to_item(document, 0, line_starts(document), suggestion('created_at', Kind.COLUMN, (7, 11)), 0, POSTGRES)
    start = edit_of(result).range.start
    assert (start.line, start.character) == (1, 0)


def test_a_column_before_a_from_carries_the_clause_as_an_extra_edit() -> None:
    """plan_insertion returns two edits, and only one of them is at the caret."""
    offered = suggestion('auth_user.email', Kind.COLUMN, (7, 10), relation=('auth_user',))
    result = item('SELECT ema', offered)
    assert result.additional_text_edits is not None
    assert len(result.additional_text_edits) == 1
    assert 'FROM auth_user' in result.additional_text_edits[0].new_text


def test_the_edit_at_the_caret_is_the_primary_one() -> None:
    """
    The FROM clause must not end up in text_edit.

    plan_insertion orders edits latest-first, so the caret's edit is not
    reliably either end of the tuple — it is the one at the suggestion's span.
    """
    offered = suggestion('auth_user.email', Kind.COLUMN, (7, 10), relation=('auth_user',))
    result = item('SELECT ema', offered)
    assert edit_of(result).new_text == 'auth_user.email'


def test_an_ordinary_suggestion_has_no_extra_edits() -> None:
    """One edit is the usual case and must not grow an empty second."""
    result = item('SELECT ', suggestion('id', Kind.COLUMN, (7, 7)))
    assert not result.additional_text_edits


def test_stops_become_snippet_placeholders() -> None:
    """
    A statement shape opens its own blanks, and the client must be told they are blanks.

    Kind.SNIPPET is the kind that carries stops. A join proposal does not: it
    inserts a finished clause, alias and condition included, and leaves nothing
    to fill in.
    """
    offered = suggestion('SELECT  FROM  AS ', Kind.SNIPPET, stops=(13, 17, 7))
    result = item('', offered)
    assert result.insert_text_format == InsertTextFormat.Snippet
    assert '$1' in edit_of(result).new_text
    assert '$3' in edit_of(result).new_text


def test_text_without_stops_is_inserted_literally() -> None:
    """A dollar in a value is a dollar, not a placeholder."""
    result = item('', suggestion("'$1 off'", Kind.VALUE))
    assert result.insert_text_format == InsertTextFormat.PlainText
    assert edit_of(result).new_text == "'$1 off'"


def test_a_dollar_beside_a_stop_is_escaped() -> None:
    """A template's own text may contain what its placeholders are written with."""
    offered = suggestion('a $ b', Kind.SNIPPET, stops=(1,))
    assert edit_of(item('', offered)).new_text == 'a$1 \\$ b'


def test_each_kind_maps_to_an_item_kind() -> None:
    """A client draws its icon from this, so a wrong one is visible on every keystroke."""
    for kind, expected in (
        (Kind.COLUMN, CompletionItemKind.Field),
        (Kind.TABLE, CompletionItemKind.Class),
        (Kind.SCHEMA, CompletionItemKind.Module),
        (Kind.FUNCTION, CompletionItemKind.Function),
        (Kind.KEYWORD, CompletionItemKind.Keyword),
        (Kind.VALUE, CompletionItemKind.Value),
    ):
        assert item('', suggestion('x', kind)).kind == expected


def test_every_kind_the_engine_can_emit_has_an_item_kind() -> None:
    """A Kind added to the library must not silently fall back to plain text here."""
    for kind in Kind:
        assert item('', suggestion('x', kind)).kind != CompletionItemKind.Text


def test_the_note_reaches_the_user() -> None:
    """`fk: flight.id` is the teaching part of a ranked list."""
    offered = suggestion('flight f ON b.flight_id = f.id', Kind.JOIN, note='fk: flight.id')
    assert 'fk: flight.id' in (item('', offered).detail or '')


def test_detail_and_note_both_survive() -> None:
    """They say different things and a client has one field for them."""
    offered = suggestion('id', Kind.COLUMN, detail='bigint', note='fk: flight.id')
    detail = item('', offered).detail or ''
    assert 'bigint' in detail
    assert 'fk: flight.id' in detail


def test_the_label_is_shown_when_the_text_would_read_poorly() -> None:
    """What is inserted and what is displayed are not always the same string."""
    assert item('', suggestion('count()', Kind.FUNCTION, label='count')).label == 'count'


def test_an_expansion_is_offered_as_a_snippet() -> None:
    """It writes several things at once, which is the nearest thing LSP has to a name for it."""
    offered = suggestion('id, name, email', Kind.EXPANSION, (7, 8))
    assert item('SELECT *', offered).kind is CompletionItemKind.Snippet


def test_an_expansion_replaces_the_star_it_was_offered_for() -> None:
    """The span is the candidate's own, and it must survive the trip to a text edit."""
    offered = suggestion('id, name, email', Kind.EXPANSION, (7, 8))
    edit = edit_of(item('SELECT * FROM users u', offered))
    assert (edit.range.start.character, edit.range.end.character) == (7, 8)
    assert edit.new_text == 'id, name, email'


def test_a_restricted_item_is_tagged_and_says_why() -> None:
    """Strikethrough is the closest thing the protocol has to a disabled state."""
    offered = suggestion(
        'password',
        Kind.COLUMN,
        (7, 7),
        detail='users.password :: text',
        availability=Availability.RESTRICTED,
        reason='no SELECT privilege',
    )
    result = item('SELECT ', offered)
    assert result.tags == [CompletionItemTag.Deprecated]
    assert result.detail is not None
    assert 'no SELECT privilege' in result.detail


def test_an_available_item_carries_no_tag() -> None:
    """Every ordinary suggestion, which is nearly all of them."""
    assert item('SELECT ', suggestion('id', Kind.COLUMN, (7, 7))).tags is None


def test_all_three_annotations_reach_the_one_field_a_client_has() -> None:
    """detail says what it is, note why it won, reason why it will fail. None may be dropped."""
    offered = suggestion(
        'flight f ON b.flight_id = f.id',
        Kind.JOIN,
        (7, 7),
        detail='joins booking',
        note='fk: flight.id',
        reason='no SELECT privilege',
    )
    detail = item('SELECT ', offered).detail
    assert detail is not None
    assert 'joins booking' in detail
    assert 'fk: flight.id' in detail
    assert 'no SELECT privilege' in detail


def test_two_edits_at_one_point_are_written_as_one() -> None:
    """
    Choosing the right primary left the other edit at the identical range.

    `SELECT ⌶` is the commonest trigger there is, and the select list ends there,
    so the column and its FROM clause are both zero-width edits at offset 7.
    `_split_edits` used to match from the front of a tuple `plan_insertion` orders
    latest-first and so took the clause, putting ` FROM auth_user` in `text_edit`;
    searching from the back fixed *which* edit leads, and left the other one a
    zero-width `additionalTextEdit` at the same position as the primary.

    The specification requires additional edits not to overlap the main one, and
    two insertions at one point have no defined order: a client is free to write
    ` FROM auth_userauth_user.id`. One edit has no order to get wrong.

    `$0` is what keeps the caret where `apply_suggestion` puts it — after the
    column, not after the clause — which is also where it lands today on a client
    that happens to apply the pair in the helpful order.
    """
    offered = suggestion('auth_user.id', Kind.COLUMN, (7, 7), relation=('auth_user',))
    result = item('SELECT ', offered)
    assert not result.additional_text_edits
    assert edit_of(result).new_text == 'auth_user.id$0 FROM auth_user'
    assert result.insert_text_format is InsertTextFormat.Snippet


def test_a_client_without_snippets_is_given_none() -> None:
    """
    Folding made the commonest completion a template, which not every client reads.

    `completionItem.snippetSupport` is absent by default and eglot reports it
    false whenever yasnippet is not loaded — an ordinary configuration, not an
    exotic one. Such a client writes the placeholder verbatim, so a `$0` added to
    hold the caret would put `auth_user.id$0 FROM auth_user` in the document:
    certain corruption on the hottest path, in place of an ordering the spec
    merely leaves undefined.

    Without snippets the fold still happens — one edit is the point — and the
    caret simply lands after the clause instead of after the column.
    """
    offered = suggestion('auth_user.id', Kind.COLUMN, (7, 7), relation=('auth_user',))
    result = to_item('SELECT ', 0, line_starts('SELECT '), offered, 0, POSTGRES, snippets=False)
    assert not result.additional_text_edits
    assert edit_of(result).new_text == 'auth_user.id FROM auth_user'
    assert result.insert_text_format is InsertTextFormat.PlainText


def test_a_client_without_snippets_gets_a_template_as_plain_text() -> None:
    """
    The same capability, one line away, and it was already being ignored there.

    A statement template is the first item at every empty-statement caret, so
    such a client has been inserting a literal `SELECT $1 FROM $2 AS $3` since
    templates existed. Checking the capability for the fold and not for the stops
    would leave that standing while looking like it had been considered.
    """
    offered = suggestion('SELECT  FROM  AS ', Kind.SNIPPET, (0, 0), stops=(13, 17, 7, 17))
    result = to_item('', 0, line_starts(''), offered, 0, POSTGRES, snippets=False)
    assert edit_of(result).new_text == 'SELECT  FROM  AS '
    assert result.insert_text_format is InsertTextFormat.PlainText


def test_no_extra_edit_ever_shares_the_primary_range() -> None:
    """
    The invariant, over the engine's own output rather than a hand-built suggestion.

    A single fixed case would not have caught this: the collision needs a select
    list that ends exactly at the caret, which is what `SELECT ⌶` and a trailing
    comma both produce and what a written prefix does not.
    """
    from pysqlsuggestions.api import complete
    from pysqlsuggestions.catalogs.memory import MemoryCatalog

    catalog = MemoryCatalog({('public', 'auth_user'): [('id', 'bigint')], ('public', 'orders'): [('id', 'bigint')]})
    for sql in ('SELECT ', 'SELECT id, ', 'SELECT na', 'SELECT (na', 'SELECT count(na', 'WITH c AS (SELECT '):
        for index, offered in enumerate(complete(sql, len(sql), POSTGRES, catalog)):
            result = to_item(sql, 0, line_starts(sql), offered, index, POSTGRES)
            primary = edit_of(result).range
            for extra in result.additional_text_edits or []:
                assert extra.range != primary, (sql, offered.text, primary)


def test_a_template_whose_stops_are_not_in_text_order_expands_correctly() -> None:
    """
    `expand_snippet` documents its offsets as being in *visiting* order — `$1`,
    `$2`, then `$0` last — and `_snippet` walked them as if they were in text
    order, advancing a single cut. The shipped statement template's stops are
    `(13, 17, 7, 17)`, so the cut passed 17 and then met 7: `text[17:7]` is
    empty, the tail was emitted twice, and the tab order was scrambled.

    `sort_text` makes this the first item at every empty-statement caret, so it
    is what a new `.sql` file offers on the first keystroke.
    """
    offered = suggestion('SELECT  FROM  AS ', Kind.SNIPPET, (0, 0), stops=(13, 17, 7, 17))
    written = edit_of(item('', offered)).new_text
    assert written.count('FROM') == 1, written
    assert written.count('AS') == 1, written
    for placeholder in ('$1', '$2', '$3'):
        assert placeholder in written, written
