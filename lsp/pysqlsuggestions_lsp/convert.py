"""
A ranked suggestion, as an editor's completion item.

Two things are easy to lose here and hard to notice missing.

The first is *order*. A client re-sorts and re-filters by its own fuzzy score
unless every item carries `sort_text`, and the engine's ranking is the product:
many-to-one joins above one-to-many, values by frequency, exact matches above
near ones. The list still appears and still holds the right items — silently in
the wrong order.

The second is the *span*. `replace_span` travels with the suggestion precisely so
an editor does not re-derive a word boundary and drop a qualifier, so every item
carries a `text_edit` with an explicit range and never an `insert_text`.
"""

from __future__ import annotations

from collections.abc import Sequence

from lsprotocol.types import (
    CompletionItem,
    CompletionItemKind,
    CompletionItemTag,
    InsertTextFormat,
    Position,
    Range,
    TextEdit,
)

from pysqlsuggestions import plan_insertion
from pysqlsuggestions.dialects.base import Dialect
from pysqlsuggestions.types import Availability, Edit, Kind, Suggestion
from pysqlsuggestions_lsp.documents import to_position

ITEM_KINDS: dict[Kind, CompletionItemKind] = {
    Kind.COLUMN: CompletionItemKind.Field,
    Kind.TABLE: CompletionItemKind.Class,
    Kind.CTE: CompletionItemKind.Class,
    Kind.SEQUENCE: CompletionItemKind.Reference,
    Kind.SCHEMA: CompletionItemKind.Module,
    Kind.FUNCTION: CompletionItemKind.Function,
    Kind.PROCEDURE: CompletionItemKind.Method,
    Kind.ALIAS: CompletionItemKind.Variable,
    Kind.KEYWORD: CompletionItemKind.Keyword,
    Kind.OPERATOR: CompletionItemKind.Operator,
    Kind.TYPE: CompletionItemKind.TypeParameter,
    Kind.SNIPPET: CompletionItemKind.Snippet,
    Kind.VALUE: CompletionItemKind.Value,
    Kind.JOIN: CompletionItemKind.Snippet,
    Kind.EXPANSION: CompletionItemKind.Snippet,
}
"""
Every Kind the engine can emit. A test fails if one is added and not mapped.

LSP has no procedure and no sequence, so those two are mapped for visual
distinctness rather than for a natural fit: every closer name is taken by
something the new kind would be confused with — `Class` is a table, `Function`
is a function, `Value` is a literal.
"""

_SNIPPET_SPECIALS = str.maketrans({'\\': r'\\', '$': r'\$', '}': r'\}'})


def match_term(suggestion: Suggestion) -> str:
    """
    What the user types to find this.

    `rank` matches against `match_text or label or text`, but only `label`
    reaches a `Suggestion`, so the term is reconstructed rather than read. Two
    cases matter: a qualified column is hunted for by its column name — `usern`
    must find `u.username` — and a value is hunted for without its quotes.
    """
    if suggestion.label is not None:
        return suggestion.label
    if suggestion.kind is Kind.VALUE:
        return suggestion.text.strip('\'"')
    return suggestion.text.rsplit('.', 1)[-1]


def _range(base: int, starts: Sequence[int], span: tuple[int, int]) -> Range:
    """A span within the statement, as a range within the document."""
    start_line, start_character = to_position(starts, base + span[0])
    end_line, end_character = to_position(starts, base + span[1])
    return Range(
        start=Position(line=start_line, character=start_character),
        end=Position(line=end_line, character=end_character),
    )


def _snippet(text: str, stops: Sequence[int]) -> str:
    """
    `text` with a placeholder at each stop, in visiting order.

    Literal dollars, braces and backslashes are escaped, since a value like
    `'$1 off'` is not a template and must not become one.
    """
    parts: list[str] = []
    cut = 0
    for index, stop in enumerate(stops, start=1):
        parts.append(text[cut:stop].translate(_SNIPPET_SPECIALS))
        parts.append(f'${index}')
        cut = stop
    parts.append(text[cut:].translate(_SNIPPET_SPECIALS))
    return ''.join(parts)


def _detail(suggestion: Suggestion) -> str | None:
    """
    What the thing is, why it outranks its neighbours, and why it would fail.

    `detail` says what it is; `note` says why it won — `fk: flight.id`; `reason`
    says why accepting it will not work. They are separate on the Suggestion and
    a client has one field, so they are joined rather than any being dropped.
    A restricted join proposal carries all three.
    """
    parts = [part for part in (suggestion.detail, suggestion.note, suggestion.reason) if part]
    return '  '.join(parts) if parts else None


def _split_edits(edits: Sequence[Edit], span: tuple[int, int]) -> tuple[Edit, list[Edit]]:
    """
    The edit at the caret, and the others.

    A column chosen before any FROM exists produces two, and only the one at the
    suggestion's own span may be the item's `text_edit` — a client applies that
    one at the caret and the rest wherever they say. `plan_insertion` orders them
    latest-first, so the caret's edit is not reliably either end of the tuple.
    """
    primary = next((edit for edit in edits if edit.span[0] == span[0]), edits[0])
    return primary, [edit for edit in edits if edit is not primary]


def to_item(
    statement: str,
    base: int,
    starts: Sequence[int],
    suggestion: Suggestion,
    index: int,
    dialect: Dialect,
) -> CompletionItem:
    """
    One suggestion, as an item.

    `index` is its place in the engine's ranking and becomes `sort_text`,
    zero-padded so that string order and numeric order agree — unpadded, '10'
    sorts before '9' and the tail of every list quietly reverses.

    A suggestion the connected role may not read is tagged `Deprecated`, which
    renders as strikethrough and is the closest the protocol comes to a disabled
    state. It having sunk in the list needs no work here: `sort_text` already
    carries the engine's order, and the engine has already put it last.

    That tag does not stop a client inserting the item, and nothing here
    pretends otherwise. An empty `text_edit` plus a command would produce an
    item that silently does nothing, which reads as a bug in this server rather
    than as a grant the user lacks.
    """
    plan = plan_insertion(statement, suggestion, dialect=dialect)
    primary, extra = _split_edits(plan.edits, suggestion.replace_span)
    text = _snippet(primary.text, suggestion.stops) if suggestion.stops else primary.text
    return CompletionItem(
        label=suggestion.label or suggestion.text,
        kind=ITEM_KINDS.get(suggestion.kind, CompletionItemKind.Text),
        detail=_detail(suggestion),
        tags=[CompletionItemTag.Deprecated] if suggestion.availability is Availability.RESTRICTED else None,
        sort_text=f'{index:04d}',
        filter_text=match_term(suggestion),
        text_edit=TextEdit(range=_range(base, starts, primary.span), new_text=text),
        additional_text_edits=[TextEdit(range=_range(base, starts, edit.span), new_text=edit.text) for edit in extra],
        insert_text_format=InsertTextFormat.Snippet if suggestion.stops else InsertTextFormat.PlainText,
    )
