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
from pysqlsuggestions_lsp.documents import Lines, to_position

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


def _range(base: int, lines: Lines, span: tuple[int, int]) -> Range:
    """A span within the statement, as a range within the document."""
    start_line, start_character = to_position(lines, base + span[0])
    end_line, end_character = to_position(lines, base + span[1])
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
    # Walked in text order, numbered in visiting order — they are not the same
    # sequence. `expand_snippet` hands these back in the order a front end should
    # tab through them, so the shipped `(13, 17, 7, 17)` sent a single advancing
    # cut past 17 and then back to 7: `text[17:7]` is empty and the tail was
    # emitted a second time.
    parts: list[str] = []
    cut = 0
    for stop, index in sorted((stop, index) for index, stop in enumerate(stops, start=1)):
        parts.append(text[cut:stop].translate(_SNIPPET_SPECIALS))
        parts.append(f'${index}')
        cut = max(cut, stop)
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

    Searched from the back because the span does not always settle it. At
    `SELECT ⌶` the select list ends exactly at the caret, so the column and its
    FROM clause both start there and matching from the front took the clause —
    putting it in `text_edit` and demoting the column to an additional edit at
    the identical range, which the specification leaves undefined. Latest-first
    ordering means the caret's edit is the *last* of any that tie.
    """
    primary = next((edit for edit in reversed(edits) if edit.span[0] == span[0]), edits[0])
    return primary, [edit for edit in edits if edit is not primary]


def to_item(
    statement: str,
    base: int,
    lines: Lines,
    suggestion: Suggestion,
    index: int,
    dialect: Dialect,
    *,
    snippets: bool = True,
) -> CompletionItem:
    """
    One suggestion, as an item.

    `snippets` is the client's `completionItem.snippetSupport`. It is absent by
    default in the protocol, and eglot reports it false whenever yasnippet is not
    loaded, so a client that writes `$1` verbatim is an ordinary configuration
    rather than an exotic one. Everything a placeholder buys here is given up
    when it is false: the caret lands at the end of what was inserted.

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
    # Edits at the primary's own range are folded into it rather than sent
    # beside it. Choosing the right primary was only half of that problem: at
    # `SELECT ⌶` the column and its FROM clause are both zero-width edits at
    # offset 7, so whichever leads, the other is an `additionalTextEdit` at the
    # identical range — which the specification forbids and which has no defined
    # order, leaving a client free to write ` FROM auth_userauth_user.id`.
    #
    # `plan_insertion` orders latest-first and a client applies in that order, so
    # insertions sharing a point come out in reverse: the primary is the last of
    # any tie, and the text reads primary-first. `$0` then holds the caret where
    # `apply_suggestion` puts it — after the column, not after the clause it
    # dragged in.
    folded = [edit for edit in extra if edit.span == primary.span]
    extra = [edit for edit in extra if edit.span != primary.span]
    # The fold happens either way — one edit is the whole point — and only the
    # caret depends on the capability. Checking it for the stops as well is not
    # scope: a statement template is the first item at every empty-statement
    # caret, so a client that cannot read `$1` has been inserting one literally
    # since templates existed, and gating one of these two lines and not the
    # other would leave that standing while looking considered.
    templated = snippets and bool(suggestion.stops or folded)
    if templated:
        # Escaped whether or not the suggestion had stops: unescaped, a column
        # Postgres spells `a$b` would arrive as a placeholder.
        text = _snippet(primary.text, suggestion.stops)
        text += ('$0' if folded else '') + ''.join(_snippet(edit.text, ()) for edit in reversed(folded))
    else:
        text = primary.text + ''.join(edit.text for edit in reversed(folded))
    return CompletionItem(
        label=suggestion.label or suggestion.text,
        kind=ITEM_KINDS.get(suggestion.kind, CompletionItemKind.Text),
        detail=_detail(suggestion),
        tags=[CompletionItemTag.Deprecated] if suggestion.availability is Availability.RESTRICTED else None,
        sort_text=f'{index:04d}',
        filter_text=match_term(suggestion),
        text_edit=TextEdit(range=_range(base, lines, primary.span), new_text=text),
        additional_text_edits=[TextEdit(range=_range(base, lines, edit.span), new_text=edit.text) for edit in extra],
        insert_text_format=InsertTextFormat.Snippet if templated else InsertTextFormat.PlainText,
    )
