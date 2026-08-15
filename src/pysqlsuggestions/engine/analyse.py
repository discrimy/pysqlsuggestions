"""
Pure analysis over a token stream.

Every function here takes tokens and a caret offset and returns a plain value.
Nothing performs I/O, and nothing knows what a catalog is.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from typing import Literal, TypeVar

from pysqlsuggestions.dialects.base import ClauseModel, Dialect
from pysqlsuggestions.engine.lex import Token, TokenType
from pysqlsuggestions.types import Projection, Relation, Scope

_Answer = TypeVar('_Answer')

_MEMO_LIMIT = 20_000
"""
How many answers one token stream will remember.

Well above what the shapes this exists for ask — a query nested three hundred
deep stores a few thousand — and far below the quadratic families a long
comma-separated relation list opens. Reached, the walk simply computes.
"""

_SKIP = (TokenType.WHITESPACE, TokenType.COMMENT)
_RELATION_CLAUSES = frozenset({'FROM', 'JOIN', 'UPDATE', 'DELETE FROM', 'INSERT INTO', 'TABLE'})
"""
Clauses whose items are relations, so a scope is built from them.

`TABLE` is here for the same reason as `FROM`, and adding it is not optional:
without it `TABLE users ORDER BY ⌶` offers the columns of every relation the
catalog holds, which is a wrong answer that modelling the form would have
created.
"""
_JOIN_QUALIFIERS = frozenset(
    """
    LEFT RIGHT FULL INNER OUTER CROSS NATURAL LATERAL ANTI SEMI ASOF GLOBAL ANY ALL
    """.split(),
)
_CTE_MODIFIERS = frozenset({'MATERIALIZED', 'NOT'})
_FUNCTION_SOURCES = frozenset({'FROM', 'JOIN', 'USING', 'LATERAL'})
"""
Where `name(...)` in a relation position is a set-returning function.

`INSERT INTO orders (id)` puts a parenthesis after a relation too, and that one
is the column list — reading it as an argument list loses the target.
"""
_LITERAL_KEYWORDS = frozenset(
    """
    NULL TRUE FALSE CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER SESSION_USER
    LOCALTIME LOCALTIMESTAMP DEFAULT
    """.split(),
)
"""Keywords that are values. `x IS NULL` ends an operand as surely as `x = 1` does."""

_COMPARISONS = frozenset({'=', '<', '>', '<=', '>=', '<>', '!='})
_PREDICATE_KEYWORDS = frozenset({'IS', 'IN', 'LIKE', 'ILIKE', 'BETWEEN', 'SIMILAR', 'EXISTS'})
_CONNECTIVES = frozenset({'AND', 'OR'})
_SET_OPERATORS = frozenset({'UNION', 'INTERSECT', 'EXCEPT'})

_MAX_NESTING = 64
"""
How many levels of subquery the scope chain will descend.

Nobody writes sixty-four, but a code generator does, and the recursion that
follows the caret down reaches Python's stack limit before it reaches anything
interesting — a RecursionError arrives at the editor as a crash rather than as
a slightly-less-precise list.
"""


def _index_before(tokens: Sequence[Token], caret: int) -> int:
    """Index of the last token starting strictly before `caret`, or -1."""
    last = -1
    for index, token in enumerate(tokens):
        if token.start < caret:
            last = index
        else:
            break
    return last


def depth_at(tokens: Sequence[Token], caret: int) -> int:
    """The paren depth the caret sits at."""
    index = _index_before(tokens, caret)
    if index < 0:
        return 0
    token = tokens[index]
    if token.type is TokenType.PUNCT and token.text == '(' and token.end <= caret:
        return token.depth + 1
    return token.depth


def _inside(token: Token, caret: int) -> bool:
    """
    Whether `caret` is within `token` rather than past it.

    A caret at the closing delimiter of a *terminated* token is outside it —
    `'ab'<caret>` is back in ordinary SQL. At the end of an unterminated one it
    is inside, because there is no delimiter to have passed. Three callers need
    the rule and it is subtle enough that three copies would drift.

    A line comment is the one token whose span stops *short* of its delimiter:
    the newline that ends it stays whitespace, deliberately, so `caret == end` is
    the position just before it rather than just after — which is exactly where a
    typist writing the comment sits. Reading that as "past the delimiter" offered
    keywords at the end of a written comment, and accepting one buried it there.
    Detected by the opener because `/*` is hardcoded in the scanner while the
    line-comment marker varies by dialect (`--`, and `#` on ClickHouse).
    """
    if token.type is TokenType.COMMENT and not token.text.startswith('/*'):
        return token.covers(caret)
    return token.start < caret < token.end or (caret == token.end and not token.terminated)


def in_literal(tokens: Sequence[Token], caret: int) -> bool:
    """Whether the caret sits inside a string literal or a comment."""
    return any(token.type in (TokenType.STRING, TokenType.COMMENT) and _inside(token, caret) for token in tokens)


def in_placeholder(tokens: Sequence[Token], caret: int) -> bool:
    """
    Whether the caret is inside a bound parameter.

    Nothing can be suggested there. The engine does not know what the caller
    will bind, and the name is the author's to invent — offering a column called
    `user_settings` for `:us` writes SQL that runs and answers a different
    question.

    Which carets count is the lexer's decision, carried on `terminated` per
    spelling: `?` admits nothing more, so `= ?<caret>` is past it, while a name
    could always take another character, so `= :us<caret>` is still in it.
    """
    return any(token.type is TokenType.PARAM and _inside(token, caret) for token in tokens)


def _string_index_under(tokens: Sequence[Token], caret: int) -> int:
    """Index of the string literal the caret is inside, or -1."""
    for index, token in enumerate(tokens):
        if token.type is TokenType.STRING and _inside(token, caret):
            return index
    return -1


def string_under(tokens: Sequence[Token], caret: int) -> Token | None:
    """
    The string literal the caret is inside, if it is inside one.

    Separate from `in_literal`, which also answers for comments: a half-typed
    literal is a position with an answer — the values that column holds, or the
    sequence a `nextval` names — where a comment is a position with none.
    """
    index = _string_index_under(tokens, caret)
    return tokens[index] if index >= 0 else None


def statement_at(tokens: Sequence[Token], caret: int) -> tuple[int, int]:
    """
    The index range [lo, hi) of the statement containing `caret`.

    Every semicolon *token* separates statements. The one inside a string, a
    comment, a quoted identifier or a dollar-quoted function body never reaches
    here as punctuation — the lexer swallowed it — which is the whole of what
    "a semicolon inside a literal does not split" ever meant.

    This used to require depth 0 as well, and that half guarded nothing: no
    dialect here admits a bare `;` between parentheses, so a semicolon token at
    depth greater than zero is always evidence of a paren the author has not
    closed yet. Refusing to split there merged the two statements and leaked the
    earlier one's relations into the later one's scope — a wrong answer bought
    in exchange for protecting a construct that cannot occur.

    `lsp/documents.statement_at` has always split on any semicolon, so this is
    also the two of them agreeing on where a statement ends.
    """
    lo = 0
    for index, token in enumerate(tokens):
        if token.type is TokenType.PUNCT and token.text == ';':
            if token.start >= caret:
                return lo, index
            lo = index + 1
    return lo, len(tokens)


def statement_has_begun(tokens: Sequence[Token], lo: int, hi: int, caret: int) -> bool:
    """
    Whether a completed token precedes the caret in this statement.

    The empty-editor answer — the words a statement may begin with — is right
    only where a statement has not begun. After `DROP TABLE ` it proposed
    `SELECT`, and accepting that wrote `DROP TABLE SELECT`: a wrong answer where
    the engine simply did not recognise the form.

    Completed is the load-bearing word. `SELEC<caret>` has a token before the
    caret, but the caret is inside it — the word is still being typed, and the
    position is still the one that offers `SELECT`.
    """
    return any(token.type not in _SKIP and token.end < caret for token in tokens[lo:hi])


def qualifier_and_prefix(
    tokens: Sequence[Token],
    caret: int,
) -> tuple[tuple[str, ...], str, tuple[int, int]]:
    """
    The dotted path and half-typed word immediately left of the caret.

    Returns (qualifier segments, prefix, replace_span). The span ends at the
    caret, so choosing a suggestion replaces what was typed and nothing more.

    A closed quoted name is the exception: it is one token and half of it is not
    an identifier, so the span takes all of it. Stopping at the caret would leave
    `"auth_u<caret>ser"` with a stranded `ser"` after the replacement, and the
    odd quote left behind swallows the rest of the statement.
    """
    index = _index_before(tokens, caret)
    prefix, span, cursor = '', (caret, caret), index

    if index >= 0 and tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        token = tokens[index]
        typed = token.text[: caret - token.start]
        prefix = _value_of(typed, token)
        span = (token.start, token.end if token.quoted and token.terminated else caret)
        cursor = index - 1
    elif index >= 0 and tokens[index].type in _SKIP:
        cursor = index

    segments: list[str] = []
    cursor = _skip_back(tokens, cursor)
    while cursor >= 0 and tokens[cursor].type is TokenType.PUNCT and tokens[cursor].text == '.':
        cursor = _skip_back(tokens, cursor - 1)
        if cursor < 0 or tokens[cursor].type is not TokenType.IDENT:
            break
        segments.append(tokens[cursor].value)
        cursor = _skip_back(tokens, cursor - 1)

    return tuple(reversed(segments)), prefix, span


def after_operand(tokens: Sequence[Token], caret: int, dialect: Dialect) -> bool:
    """
    Whether an operand was just completed, so an operator or keyword comes next.

    `WHERE r.id <caret>` cannot take another column: two names in a row is not
    valid SQL. `WHERE r.id = <caret>` and `WHERE r.id = 1 AND <caret>` can, and
    must.

    One token decides it, which is why this needs no expression analysis. A
    name, a literal or a closing paren ends an operand; an operator, a comma, an
    opening paren or a keyword opens the next one.

    A half-typed word is skipped rather than answered from, because it could
    become either: in `WHERE r.id > d.id AN` it is turning into AND, and in
    `WHERE na` into a column name. What separates them is the token before it —
    a completed operand in the first, the WHERE keyword in the second.
    """
    index = _index_before(tokens, caret)
    if index < 0:
        return False
    if tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        index -= 1

    index = _skip_back(tokens, index)
    if index < 0:
        return False
    token = tokens[index]
    # A parameter is a value, whatever it will be bound to. Without this
    # `WHERE id = ? ` looked like an open operand and offered a second column.
    if token.type in (TokenType.NUMBER, TokenType.STRING, TokenType.PARAM):
        return True
    if token.type is TokenType.PUNCT:
        return token.text == ')'
    if token.type is TokenType.OPERATOR and token.text == '*':
        return _star_is_an_item(tokens, index, dialect)
    if token.type is TokenType.IDENT:
        word = token.value.upper()
        if word in _CLOSES_ITEM:
            return True
        return token.quoted or word in _LITERAL_KEYWORDS or word not in dialect.keywords
    return False


_CLOSES_ITEM = frozenset({'ASC', 'DESC', 'FIRST', 'LAST', 'END'})
"""
Keywords that finish the item before them instead of opening a new one.

The general rule reads a reserved word as the start of an operand, which is
right for `AND` and `NOT` and wrong for these: `ORDER BY id ASC ` takes LIMIT,
not a second column, and accepting one there writes `ORDER BY id ASC name`.
"""

_CONTINUES = {
    ('IS',): ('NULL', 'NOT NULL', 'TRUE', 'FALSE', 'DISTINCT FROM'),
    ('IS', 'NOT'): ('NULL', 'TRUE', 'FALSE', 'DISTINCT FROM'),
    ('NULLS',): ('FIRST', 'LAST'),
}
"""
Multi-word constructs and the words that finish them.

The clause model carries `IS NULL` as one string, which is reachable while the
whole thing is being typed and unreachable the moment the author writes `IS`
themselves — at which point `IS` reads as a keyword, an operand looks like it
is starting, and the offer is a column name.
"""

_LONGEST_CONTINUATION = max(len(words) for words in _CONTINUES)


@cache
def _half_written_clauses(dialect: Dialect) -> Mapping[tuple[str, ...], tuple[str, ...]]:
    """
    Clause names of more than one word, and the words that finish them.

    `GROUP ` can only become `GROUP BY`. Without this the first word reads as a
    clause already complete, and the caret after it is offered a relation or a
    column — which is where a typist pauses on the way to writing one.

    Derived from the model rather than listed beside it, so a dialect that adds
    `ARRAY JOIN` or `DISTINCT ON` gets the same treatment without a second edit,
    and no list can fall behind the clauses it describes.

    A head that is a phrase in its own right is left out. `ON` begins
    `ON CONFLICT` and is also a clause, so answering `ON ` with `CONFLICT` alone
    would forbid the predicate that usually follows it.
    """
    phrases = {
        tuple(phrase.upper().split())
        for clause in dialect.clauses.clauses
        for phrase in (clause.name, *clause.followed_by)
    }
    phrases |= {tuple(phrase.upper().split()) for phrase in dialect.statement_start}

    table: dict[tuple[str, ...], set[str]] = {}
    for phrase in phrases:
        for cut in range(1, len(phrase)):
            head = phrase[:cut]
            if head not in phrases:
                table.setdefault(head, set()).add(' '.join(phrase[cut:]))
    return {head: tuple(sorted(words)) for head, words in table.items()}


def at_the_clause_start(tokens: Sequence[Token], caret: int, clause: str) -> bool:
    """
    Whether nothing has been written in `clause` yet.

    True at `SELECT ⌶`, false at `SELECT id, ⌶` and `SELECT * ⌶` — a comma and a
    star are not words, so the run of words before the caret is empty rather
    than the clause's own name. What stands between a clause and its first item
    belongs here and only here.

    A suffix rather than an equality, because `_words_before` walks back through
    consecutive identifiers and does not stop at a clause boundary: the run
    before `GROUP BY rol` is ('USERS', 'GROUP', 'BY'), and comparing that whole
    run to the name reported false wherever a relation preceded the clause.
    `before_the_item` was therefore dead for every clause but a leading one, and
    `DISTINCT` worked by the accident of SELECT coming first. The guards are
    unaffected: a comma or a star breaks the run, so `SELECT id, ⌶` still has
    nothing to match.
    """
    words = _words_before(tokens, caret)
    name = tuple(clause.upper().split())
    return len(words) >= len(name) and words[-len(name) :] == name


def opens_a_name_list(
    tokens: Sequence[Token],
    caret: int,
    clause: str | None,
    clauses: ClauseModel,
) -> bool:
    """
    Whether the paren the caret sits in opens a list of names being defined.

    Four shapes, all of them positions where the author is inventing names and
    a catalog therefore has nothing to say:

        WITH x (a, b) AS (...)      a CTE's column list
        FROM t AS u (a, b)          a relation's column aliases
        FROM f(1) AS t (a int)      a function's column definitions
        FROM f(1) AS (a int)        the same, unnamed

    Depth and the governing clause do not separate them from anything: the
    caret's clause is `WITH` in the first and `FROM` in the rest, exactly as it
    is for the bodies and calls that must go on answering. What separates them
    is the token that introduced the paren.

    A clause declaring `opens_a_group` has already said what its group holds, so
    the only question is whether this paren *is* that group — it is when the
    alias word introduces it, and `WITH x (` is the list that precedes one. A
    clause with no group answers the same question the other way round: a paren
    the alias word introduced, or that a name the alias word introduced
    introduced, is names being defined.

    Read from `Clause.aliases_with` rather than matched against `AS`, so no SQL
    vocabulary enters this module and a dialect aliasing with another word gets
    the same behaviour. `ROWS FROM(` is deliberately not here for that reason:
    it is Postgres spelling, and a clause of its own.
    """
    depth = depth_at(tokens, caret)
    if depth <= 0:
        return False
    start = _group_start(tokens, caret, depth)
    if start <= 0:
        return False

    # `start - 1` is the paren itself, so the word that introduced it is before that.
    at = _skip_back(tokens, start - 2)
    introducer = _plain_word(tokens, at)
    if introducer is None:
        return False

    governing = clauses.get(clause) if clause else None
    if governing is None or not governing.aliases_with:
        return False
    alias = governing.aliases_with.upper()

    # The governing clause has to lie outside this paren for the introducer to
    # say anything about it. `WITH a AS (SELECT * FROM ⌶` is governed by FROM,
    # which is *inside* the CTE body, and `_group_start` returns that body
    # either way — so without this the introducer read as `AS` and the whole
    # body went quiet. A clause written inside the group is that group's
    # business, and this one is not a name list.
    head = governing.name.upper().split()[0]
    for index in range(start, _index_before(tokens, caret) + 1):
        if _plain_word(tokens, index) == head:
            return False

    if governing.opens_a_group:
        return introducer != alias
    # Either the alias word introduced the paren, or it introduced the name that did.
    return introducer == alias or _plain_word(tokens, _skip_back(tokens, at - 1)) == alias


def _plain_word(tokens: Sequence[Token], index: int) -> str | None:
    """The uppercased value at `index`, or None where it is not an unquoted word."""
    if index < 0 or index >= len(tokens):
        return None
    token = tokens[index]
    if token.type is not TokenType.IDENT or token.quoted:
        return None
    return token.value.upper()


def defines_a_column(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    clause: str | None,
    clauses: ClauseModel,
) -> Literal['name', 'type', 'constraint'] | None:
    """
    Where in a parenthesised column definition the caret sits, or None if outside one.

    `CREATE TABLE t (id integer NOT NULL, email text)` is an alternation rather
    than a list of one thing: each item invents a name, then names a type, then
    takes any number of constraints. Only the last two can be answered — a name
    being invented has nothing behind it in any catalog.

    Which of the three it is comes from counting the item's plain words since
    the last comma, at the list's own depth. Counting rather than parsing is
    what makes every nested construct fall out for free: `numeric(10, 2)`,
    `CHECK (x > 0)`, `REFERENCES users (id)` and `PRIMARY KEY (a, b)` all sit
    one level deeper, and none of them is named here.

    A two-word type — `double precision` — reads as a constraint position,
    because a count cannot see that the type is unfinished. Deliberate: the
    caret before it offers `double precision` whole, so only somebody typing the
    first word by hand reaches the bad caret. The alternative, offering types at
    every caret past the name, writes `id integer text`.
    """
    governing = clauses.get(clause) if clause else None
    if governing is None or not governing.defines_columns:
        return None

    opening = _definition_paren(tokens, lo, hi, caret, governing.name, clauses)
    if opening < 0 or depth_at(tokens, caret) != tokens[opening].depth + 1:
        return None

    # The word under the caret is being typed rather than finished, so it is not
    # part of the count: without this the first character of a column name looks
    # like a completed name and the position answers with types.
    last = _index_before(tokens, caret)
    if last >= 0 and tokens[last].type is TokenType.IDENT and tokens[last].end >= caret:
        last -= 1

    depth = tokens[opening].depth + 1
    words = 0
    for index in range(opening + 1, last + 1):
        token = tokens[index]
        if token.type in _SKIP or token.depth != depth:
            continue
        if token.type is TokenType.PUNCT and token.text == ',':
            words = 0
        elif token.type is TokenType.IDENT:
            words += 1
    if words == 0:
        return 'name'
    return 'type' if words == 1 else 'constraint'


def _definition_paren(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    name: str,
    clauses: ClauseModel,
) -> int:
    """
    Index of the first `(` after the clause called `name`, or -1.

    The clause's own list is the first group it opens, and finding it by
    position is what lets the caller tell `CREATE TABLE t (id ⌶` from
    `CREATE TABLE t (id numeric(⌶`. Both are governed by the same clause, and
    only their depth relative to *this* paren separates them.
    """
    after = -1
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.IDENT or token.quoted or token.end >= caret:
            continue
        matched = _clause_starting_at(tokens, index, hi, clauses)
        if matched is not None and matched[0] == name:
            after = matched[1]
    if after < 0:
        return -1
    for index in range(after, hi):
        token = tokens[index]
        if token.start >= caret:
            break
        if token.type is TokenType.PUNCT and token.text == '(':
            return index
    return -1


def _words_before(tokens: Sequence[Token], caret: int) -> tuple[str, ...]:
    """The unbroken run of plain words immediately left of the caret, in order."""
    index = _index_before(tokens, caret)
    if index >= 0 and tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        index -= 1
    written: list[str] = []
    cursor = _skip_back(tokens, index)
    while cursor >= 0:
        token = tokens[cursor]
        if token.type is not TokenType.IDENT or token.quoted:
            break
        written.append(token.value.upper())
        cursor = _skip_back(tokens, cursor - 1)
    return tuple(reversed(written))


def continues_a_keyword(tokens: Sequence[Token], caret: int, dialect: Dialect) -> tuple[str, ...]:
    """
    The words that finish the half-written construct left of the caret, if any.

    Longest match wins, so `IS NOT ` answers with its own list rather than the
    one `IS ` would give. A word still being typed is skipped: `IS NUL` is `IS `
    with a prefix, and the prefix does the narrowing.
    """
    index = _index_before(tokens, caret)
    if index >= 0 and tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        index -= 1

    clauses = _half_written_clauses(dialect)
    longest = max(_LONGEST_CONTINUATION, *(len(head) for head in clauses)) if clauses else _LONGEST_CONTINUATION

    written: list[str] = []
    cursor = _skip_back(tokens, index)
    while cursor >= 0 and len(written) < longest:
        token = tokens[cursor]
        if token.type is not TokenType.IDENT or token.quoted:
            break
        written.append(token.value.upper())
        cursor = _skip_back(tokens, cursor - 1)

    for length in range(len(written), 0, -1):
        head = tuple(reversed(written[:length]))
        found = _CONTINUES.get(head) or clauses.get(head)
        if found is not None:
            return found
    return ()


def _star_is_an_item(tokens: Sequence[Token], index: int, dialect: Dialect) -> bool:
    """
    Whether the `*` at `index` is a select item rather than multiplication.

    The lexer cannot tell them apart — both are the same character — but the
    token before decides it. `SELECT *`, `SELECT id, *`, `SELECT t.*` and
    `count(*)` are items and complete an operand. `SELECT a * ` and `WHERE 5 * `
    are the operator and open one.

    A keyword before it means an item; a plain name means multiplication. That
    is the same test `after_operand` applies everywhere else, which is why a
    quoted identifier counts as a name however it is spelled.
    """
    before = _skip_back(tokens, index - 1)
    if before < 0:
        return True
    token = tokens[before]
    if token.type is TokenType.PUNCT:
        return token.text in {',', '.', '('}
    if token.type is TokenType.IDENT:
        return not token.quoted and token.value.upper() in dialect.keywords
    return False


def star_at(tokens: Sequence[Token], caret: int, dialect: Dialect) -> int | None:
    """
    Index of a select-list `*` the caret sits on, or None.

    Three conditions. The caret is at the star's end — a star is one character,
    so that is the only caret that can be said to be *on* it, and `SELECT * `
    with a space is the position that wants FROM. The star is an item rather
    than multiplication, which `_star_is_an_item` already decides. And it is not
    inside a call: `count(*)` passes that test because a `(` precedes it, and
    expanding there would write `count(id, name, email)`.

    Not conditioned on the clause being SELECT. `RETURNING *` is the same
    construct with the same answer, and a rule naming SELECT would have to be
    extended for it and for every projection clause a dialect adds later.
    """
    for index, token in enumerate(tokens):
        if token.type is not TokenType.OPERATOR or token.text != '*' or caret != token.end:
            continue
        if not _star_is_an_item(tokens, index, dialect):
            return None
        return None if _enclosing_call(tokens, index) is not None else index
    return None


def _star_qualifier(tokens: Sequence[Token], index: int) -> int | None:
    """Index of the identifier qualifying the star at `index`, as in `u.*`, or None."""
    dot = _skip_back(tokens, index - 1)
    if dot < 0 or tokens[dot].type is not TokenType.PUNCT or tokens[dot].text != '.':
        return None
    name = _skip_back(tokens, dot - 1)
    return name if name >= 0 and tokens[name].type is TokenType.IDENT else None


def star_span(tokens: Sequence[Token], index: int) -> tuple[int, int]:
    """
    What accepting an expansion replaces: the star, and any qualifier on it.

    `u.*` goes whole. Each expanded column carries its own `u.`, so leaving the
    written qualifier in place would emit the first column bare and the rest
    qualified.
    """
    qualifier = _star_qualifier(tokens, index)
    start = tokens[qualifier].start if qualifier is not None else tokens[index].start
    return start, tokens[index].end


def star_qualifier(tokens: Sequence[Token], index: int) -> str | None:
    """The label written left of the star's dot, as in `u.*`, or None for a bare star."""
    found = _star_qualifier(tokens, index)
    return tokens[found].value if found is not None else None


def star_relations(tokens: Sequence[Token], index: int, scope: Scope | None) -> tuple[Relation, ...]:
    """
    The relations the star at `index` stands for.

    A qualified star names the one relation answering to the label left of its
    dot, looked up through the whole scope chain because a correlated subquery
    may reach outward. A bare star names every relation of its own query level
    and no more, which is why this reads `relations` rather than `visible()`.
    """
    if scope is None:
        return ()
    qualifier = _star_qualifier(tokens, index)
    if qualifier is None:
        return scope.relations
    label = tokens[qualifier].value
    return tuple(relation for relation in scope.visible() if relation.label == label)


def after_as(tokens: Sequence[Token], caret: int) -> bool:
    """
    Whether the caret is naming something after an explicit `AS`.

    An alias is invented by the author, so nothing in a catalog can propose it.
    Offering tables and keywords there means `as u` gets overwritten by UNION,
    which is worse than offering nothing at all.
    """
    index = _index_before(tokens, caret)
    if index < 0:
        return False
    if tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        index -= 1
    index = _skip_back(tokens, index)
    return index >= 0 and tokens[index].type is TokenType.IDENT and tokens[index].value.upper() == 'AS'


def after_cast(tokens: Sequence[Token], caret: int, dialect: Dialect) -> bool:
    """
    Whether the caret is naming the target of a cast.

    Both spellings: the operator `'7 days'::<caret>`, which not every dialect
    has, and the call `CAST(x AS <caret>)`, which every one does — it is the
    only cast strict ANSI knows. The `AS` inside a CAST names a type, so it has
    to be told apart from the `AS` that names an alias.
    """
    index = _index_before(tokens, caret)
    if index < 0:
        return False
    if tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        index -= 1
    index = _skip_back(tokens, index)
    if index < 0:
        return False
    marker = dialect.syntax.cast_operator
    if marker and tokens[index].type is TokenType.OPERATOR and tokens[index].text == marker:
        return True
    if tokens[index].type is not TokenType.IDENT or tokens[index].value.upper() != 'AS':
        return False
    return _enclosing_call(tokens, index) == 'CAST'


def inside_a_cast_awaiting_as(tokens: Sequence[Token], caret: int) -> bool:
    """
    Whether the caret is inside `CAST(...)` with the value written and no AS yet.

    A cast is a call with a keyword inside it, and after the value only that
    keyword can follow. Nothing else marked the interior as different, so the
    enclosing clause's continuations reached the caret and `SELECT cast(o.total `
    was offered FROM, WHERE and GROUP BY — none of which can appear there.

    `after_cast` answers the position past the AS, where a type belongs. This is
    the one before it.
    """
    index = _index_before(tokens, caret)
    if index < 0:
        return False
    if tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        index -= 1
    index = _skip_back(tokens, index)
    if index < 0 or _enclosing_call(tokens, index) != 'CAST':
        return False
    return not (tokens[index].type is TokenType.IDENT and tokens[index].value.upper() == 'AS')


def _call_opening(tokens: Sequence[Token], index: int) -> int:
    """
    Index of the `(` whose argument list encloses `index`, or -1.

    An opening paren carries the depth *outside* it, so the one being looked for
    sits one level below the token it encloses.
    """
    wanted = tokens[index].depth - 1
    for candidate in range(index - 1, -1, -1):
        token = tokens[candidate]
        if token.type is TokenType.PUNCT and token.text == '(' and token.depth == wanted:
            return candidate
    return -1


def _enclosing_call(tokens: Sequence[Token], index: int) -> str | None:
    """The uppercased name of the function whose argument list encloses `index`, if any."""
    opening = _call_opening(tokens, index)
    if opening < 0:
        return None
    before = _skip_back(tokens, opening - 1)
    if before < 0 or tokens[before].type is not TokenType.IDENT:
        return None
    return tokens[before].value.upper()


def literal_argument_call(tokens: Sequence[Token], caret: int) -> str | None:
    """
    The uppercased name of the call whose *first* argument the caret's literal is.

    None when the caret is not inside a string, when that string is not directly
    inside an argument list, or when a comma at the same depth puts it past the
    first argument. `nextval('<caret>` answers NEXTVAL; `setval('s', '<caret>`
    answers nothing, because what a call's first argument names says nothing
    about its later ones.
    """
    index = _string_index_under(tokens, caret)
    if index < 0:
        return None
    name = _enclosing_call(tokens, index)
    if name is None:
        return None
    opening = _call_opening(tokens, index)
    depth = tokens[index].depth
    if any(
        token.type is TokenType.PUNCT and token.text == ',' and token.depth == depth
        for token in tokens[opening + 1 : index]
    ):
        return None
    return name


def comparand_at(tokens: Sequence[Token], caret: int, dialect: Dialect) -> tuple[tuple[str, ...], str | None]:
    """
    What sits on the left of the comparison the caret is completing.

    Returns (reference path, type text). A cast names its own type outright —
    `'7 days'::interval > <caret>` is temporal whatever the literal says — so
    that is reported directly and no reference needs looking up. Otherwise the
    dotted path is returned for resolve to type from the catalog.

    A bare literal reports neither. `'7 days' > <caret>` is of unknown type in
    Postgres and coerces to whatever it meets, so narrowing on it would be wrong.
    """
    index = _index_before(tokens, caret)
    if index < 0:
        return (), None
    if tokens[index].type in (TokenType.IDENT, TokenType.STRING) and tokens[index].end >= caret:
        index -= 1

    # Step back over whatever is being typed on the *right*. A qualifier is part
    # of that: `> r.<caret>` and `> r.d<caret>` are the same comparison as `> `,
    # and reading only the half-typed word leaves the caret sitting on the dot.
    index = _skip_back(tokens, index)
    while index >= 0 and tokens[index].type is TokenType.PUNCT and tokens[index].text == '.':
        index = _skip_back(tokens, index - 1)
        if index >= 0 and tokens[index].type is TokenType.IDENT:
            index = _skip_back(tokens, index - 1)

    if index < 0 or tokens[index].type is not TokenType.OPERATOR or tokens[index].text not in _COMPARISONS:
        return (), None

    index = _skip_back(tokens, index - 1)
    if index < 0:
        return (), None

    marker = dialect.syntax.cast_operator
    if marker and tokens[index].type is TokenType.IDENT:
        cast = _skip_back(tokens, index - 1)
        if cast >= 0 and tokens[cast].type is TokenType.OPERATOR and tokens[cast].text == marker:
            return (), tokens[index].value

    segments: list[str] = []
    while index >= 0 and tokens[index].type is TokenType.IDENT:
        segments.append(tokens[index].value)
        dot = _skip_back(tokens, index - 1)
        if dot < 0 or tokens[dot].type is not TokenType.PUNCT or tokens[dot].text != '.':
            break
        index = _skip_back(tokens, dot - 1)
    return tuple(reversed(segments)), None


def predicate_complete(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> bool:
    """
    Whether the predicate under the caret is finished, so a connective comes next.

    `WHERE r.id ` has an operand but no comparison: what belongs there is `=` or
    `IS NULL`, not `AND`. `WHERE r.id > 1 ` has both, so `AND` and `ORDER BY`
    belong and another `=` does not.

    Tracked rather than parsed: a comparison operator or a predicate keyword
    arms it, a connective or a new clause disarms it. That is enough to tell the
    two positions apart without an expression grammar.

    A group that closed before the caret is read as a whole. `WHERE (a AND b) `
    is a finished predicate, but its comparisons are a level deeper than the
    scan reaches, so without this the caret looks like it is still waiting for
    an operator and offers `BETWEEN` after a closing paren.
    """
    depth = depth_at(tokens, caret)
    armed = False
    for index in range(lo, hi):
        token = tokens[index]
        if token.type in _SKIP or token.start >= caret or token.depth != depth:
            continue
        if token.type is TokenType.OPERATOR and token.text in _COMPARISONS:
            armed = True
        elif token.type is TokenType.PUNCT and token.text == ')':
            armed = armed or _group_is_a_predicate(tokens, index, dialect)
        elif token.type is TokenType.IDENT and not token.quoted:
            word = token.value.upper()
            if word in _CONNECTIVES or dialect.clauses.get(word) is not None:
                armed = False
            elif word in _PREDICATE_KEYWORDS:
                armed = True
    return armed


def _group_is_a_predicate(tokens: Sequence[Token], close: int, dialect: Dialect) -> bool:
    """
    Whether the group closing at `close` holds a comparison of its own.

    `(a AND b)` does and `count(*)` does not, which is the difference between a
    finished predicate and a value that still needs comparing to something.
    """
    depth = tokens[close].depth
    for index in range(close - 1, -1, -1):
        token = tokens[index]
        if token.type is TokenType.PUNCT and token.text == '(' and token.depth == depth:
            return False
        if token.type is TokenType.OPERATOR and token.text in _COMPARISONS:
            return True
        if token.type is TokenType.IDENT and not token.quoted and token.value.upper() in _PREDICATE_KEYWORDS:
            return True
        if token.type is TokenType.IDENT and not token.quoted and dialect.clauses.get(token.value.upper()):
            # A subquery, not a predicate: `WHERE id IN (SELECT ...)` is armed by
            # the IN that precedes it, and `FROM (SELECT ...)` by nothing at all.
            return False
    return False


_CASE_BRANCHES = frozenset({'WHEN', 'THEN', 'ELSE'})


def case_position(tokens: Sequence[Token], caret: int) -> str | None:
    """
    Where the caret sits inside a CASE expression, or None if it is outside one.

    Answers 'start' straight after CASE, then the branch keyword governing the
    caret. CASE nests, so the opening one is found by counting ENDs back, and
    only at the caret's own depth — a CASE inside parens is its own expression.

    Needed because a CASE lives *inside* a clause: the caret's clause is SELECT,
    whose position rules say a completed operand takes `AS` or `FROM`, and
    `CASE WHEN id AS` is what that produces.
    """
    depth = depth_at(tokens, caret)
    pending = 0
    branch: str | None = None
    for index in range(_index_before(tokens, caret), -1, -1):
        token = tokens[index]
        if token.type is not TokenType.IDENT or token.quoted or token.depth != depth or token.start >= caret:
            continue
        word = token.value.upper()
        if word == 'END':
            pending += 1
        elif word == 'CASE':
            if pending == 0:
                return branch or 'start'
            pending -= 1
        elif pending == 0 and branch is None and word in _CASE_BRANCHES:
            branch = word.lower()
    return None


def clause_at(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    clauses: ClauseModel,
) -> str | None:
    """
    The nearest clause keyword governing the caret.

    Scans back over tokens at the caret's own depth. A subquery that closed
    before the caret sits at a deeper level and is skipped, so
    `SELECT a, (SELECT b FROM t2), <caret>` is still the outer SELECT.

    When the caret's depth holds no clause keyword — `WHERE (a AND <caret>)`,
    `SELECT sum(<caret>` — the search widens to the enclosing depth.
    """
    if not clauses.clauses:
        return None
    depth = depth_at(tokens, caret)
    while depth >= 0:
        found = _scan_for_clause(tokens, max(lo, _group_start(tokens, caret, depth)), hi, caret, clauses, depth)
        if found is not None:
            return found
        depth -= 1
    return None


def _group_start(tokens: Sequence[Token], caret: int, depth: int) -> int:
    """
    Index where the caret's own parenthesised group begins, at `depth`.

    Depth alone does not identify a group: the body of `WITH a AS (SELECT id
    FROM t)` and the column list of `INSERT INTO orders (` are both depth one,
    and scanning across both answers the INSERT with the CTE's FROM.
    """
    if depth <= 0:
        return 0
    for index in range(_index_before(tokens, caret), -1, -1):
        token = tokens[index]
        if token.type is TokenType.PUNCT and token.text == '(' and token.depth == depth - 1:
            return index + 1
    return 0


def _scan_for_clause(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    clauses: ClauseModel,
    depth: int,
) -> str | None:
    """
    The clause name ending nearest to the left of `caret` at exactly `depth`.

    Ranked by (end offset, word count), so `DELETE FROM <caret>` answers
    'DELETE FROM' rather than the bare 'FROM' that ends at the same token.
    """
    best: tuple[int, int, str] | None = None
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.IDENT or token.quoted or token.depth != depth or token.end >= caret:
            continue
        matched = _clause_starting_at(tokens, index, hi, clauses)
        if matched is None:
            continue
        name = matched[0]
        last = tokens[matched[1] - 1]
        if last.end >= caret:
            continue
        candidate = (last.end, len(name.split()), name)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best is not None else None


def _ident_run(tokens: Sequence[Token], start: int, hi: int, count: int) -> tuple[list[Token], int] | None:
    """
    `count` consecutive unquoted IDENT tokens from `start`, and where they end.

    Whitespace and comments are skipped. Quoting is how SQL says "this is a
    name, not syntax", so a quoted word never spells a clause: `FROM "limit"`
    is a relation called `limit`, and reading it as the LIMIT clause loses the
    relation along with the clause.

    The end index comes back with the run because the caller wants it and is
    the only one who can cheaply know it.
    """
    run: list[Token] = []
    index = start
    while index < hi and len(run) < count:
        token = tokens[index]
        if token.type in _SKIP:
            index += 1
            continue
        if token.type is not TokenType.IDENT or token.quoted:
            return None
        run.append(token)
        index += 1
    return (run, index) if len(run) == count else None


def _skip_back(tokens: Sequence[Token], index: int) -> int:
    """The nearest index at or before `index` that is not whitespace or a comment."""
    while index >= 0 and tokens[index].type in _SKIP:
        index -= 1
    return index


def _value_of(typed: str, token: Token) -> str:
    """
    Fold a partially typed identifier the same way the lexer folded the whole one.

    A quoted name that has been typed in full already has its value on the
    token; before that the closing quote has not been reached, so only the
    opening one and any doubled pair need undoing.
    """
    if token.quoted:
        if len(typed) >= len(token.text):
            return token.value
        quote = token.text[0]
        body = typed[1:] if typed.startswith(quote) else typed
        return body.replace(quote * 2, quote)
    folded = token.value
    return folded[: len(typed)] if len(folded) == len(token.text) else typed.lower()


def scope_of(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> Scope:
    """
    The relations visible at `caret`, built from the whole statement.

    Reading only the text left of the caret cannot work: in `SELECT na<caret>
    FROM users u` the relation that answers the question sits to the right.

    Returns the innermost scope containing the caret, chained to its parents.
    """
    ctes, cte_bodies = _read_ctes(tokens, lo, hi, dialect)
    return _scope_level(tokens, lo, hi, caret, dialect, ctes, parent=None, cte_scopes=cte_bodies)


def _scope_level(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    ctes: dict[str, Relation],
    parent: Scope | None,
    cte_scopes: Mapping[tuple[int, int], dict[str, Relation]] | None = None,
    remaining: int = _MAX_NESTING,
) -> Scope:
    """One query level, recursing into whichever subquery holds the caret."""
    cte_scopes = cte_scopes if cte_scopes is not None else {}
    lo, hi = _branch_at(tokens, lo, hi, caret)
    relations = [_bind(r, ctes) for r in _relations_in(tokens, lo, hi, caret, dialect)]
    derived = _derived_tables(tokens, lo, hi, dialect)
    for derived_lo, derived_hi, alias, renamed in derived:
        projection = (
            Projection(columns=renamed) if renamed else select_outputs(tokens, derived_lo, derived_hi, dialect, ctes)
        )
        relations.append(Relation(alias=alias, path=(), source='subquery', projection=projection))

    relations += _conflict_alias(tokens, lo, hi, caret, dialect, relations)
    here = Scope(
        relations=tuple(relations),
        ctes=ctes,
        parent=parent,
        projection=select_outputs(tokens, lo, hi, dialect, ctes),
    )
    opaque = {(body_lo, body_hi) for body_lo, body_hi, _, _ in derived if not _is_lateral(tokens, body_lo, dialect)}

    for inner_lo, inner_hi in _subquery_bodies(tokens, lo, hi) if remaining > 0 else ():
        if tokens[inner_lo].start <= caret <= tokens[inner_hi - 1].end:
            # Three kinds of nested query, and they see three different things.
            # A CTE body sees the CTEs written before it and nothing of the outer
            # FROM; a derived table sees neither, unless LATERAL asks for it; a
            # correlated subquery in an expression sees everything.
            body_ctes = cte_scopes.get((inner_lo, inner_hi))
            opaque_here = body_ctes is not None or (inner_lo, inner_hi) in opaque
            # A body may declare CTEs of its own, and the pass above cannot have
            # read them: it stopped at this level's WITH. `select_outputs` reaches
            # them from outside — `WITH oq AS (WITH iq AS (...) SELECT * FROM iq)`
            # is the shape its docstring names — and without this the same `iq`
            # read as a catalog table once the caret was inside the body.
            inner_ctes, inner_bodies = _read_ctes(tokens, inner_lo, inner_hi, dialect, remaining - 1)
            visible = ctes if body_ctes is None else body_ctes
            return _scope_level(
                tokens,
                inner_lo,
                inner_hi,
                caret,
                dialect,
                {**visible, **inner_ctes},
                parent=None if opaque_here else here,
                cte_scopes={**cte_scopes, **inner_bodies},
                remaining=remaining - 1,
            )
    return here


_CONFLICT_ALIAS = 'excluded'
"""
The row that `ON CONFLICT ... DO UPDATE` could not insert.

Postgres exposes it as a relation shaped exactly like the target, so
`SET total = EXCLUDED.<caret>` wants the target's columns. Named here rather
than in the dialect because the clause it belongs to already is: a dialect
without ON CONFLICT never reaches this.
"""


def _conflict_alias(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    relations: Sequence[Relation],
) -> list[Relation]:
    """The `excluded` pseudo-relation, when the caret is past an ON CONFLICT."""
    if not relations or dialect.clauses.get('ON CONFLICT') is None:
        return []
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.IDENT or token.end >= caret:
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is not None and matched[0] == 'ON CONFLICT':
            target = relations[0]
            return [Relation(alias=_CONFLICT_ALIAS, path=target.path, source=target.source)]
    return []


def _is_lateral(tokens: Sequence[Token], body_lo: int, dialect: Dialect) -> bool:
    """
    Whether the derived table starting at `body_lo` was introduced by LATERAL.

    LATERAL is the keyword that asks for exactly what a plain derived table is
    denied: the other relations of the FROM list it sits in.
    """
    del dialect
    before = _skip_back(tokens, body_lo - 2)  # past the `(`
    return before >= 0 and tokens[before].type is TokenType.IDENT and tokens[before].value.upper() == 'LATERAL'


def _branch_at(tokens: Sequence[Token], lo: int, hi: int, caret: int) -> tuple[int, int]:
    """
    The set-operation branch containing the caret.

    Each branch of a UNION, INTERSECT or EXCEPT has its own FROM clause and so
    its own scope: in `SELECT id FROM auth_user UNION SELECT <caret> FROM orders`
    only `orders` is in view. Merging the branches would offer columns from a
    relation the caret's branch cannot reference.
    """
    base = _base_depth(tokens, lo, hi)
    cuts = [
        index
        for index in range(lo, hi)
        if tokens[index].type is TokenType.IDENT
        and tokens[index].depth == base
        and tokens[index].value.upper() in _SET_OPERATORS
    ]
    start = lo
    for cut in cuts:
        if caret <= tokens[cut].start:
            return start, cut
        start = cut + 1
    return start, hi


_SET_OPERATION_TAIL = frozenset({'ORDER BY', 'LIMIT', 'OFFSET', 'FETCH'})
"""
Clauses written after the last branch that govern the whole set operation.

Named here rather than derived from the clause model because the model records
what a clause *is*, not whose scope it takes. Every dialect here spells these
four the same, and a dialect that adds a fifth gets the shared answer for it —
nothing — which is the safe direction.
"""


def in_set_operation_tail(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> bool:
    """
    Whether the caret is in the trailing clause of a UNION, INTERSECT or EXCEPT.

    The three backends do not agree on what is in scope there, and measurement
    rather than the standard is what settled it. Postgres and Trino bind the
    clause to the *result*: only the first branch's output names and ordinals
    resolve, and a qualified reference is refused outright. ClickHouse binds it
    to the *last branch* — that branch's own columns resolve, including ones the
    select list aliased away — and the query then runs without sorting the union
    at all, which is the "valid SQL, wrong rows" case this library refuses
    elsewhere.

    So there is no answer that is right on all three, and the engine offered the
    last branch's columns to all three: SQL that errors on two and silently
    mis-sorts on the third. Nothing is the one answer that is wrong nowhere, and
    `LIMIT` in this position already said it.
    """
    base = _base_depth(tokens, lo, hi)
    # A parenthesised subquery written *in* the tail is an ordinary query with
    # its own FROM, and all three backends agree about what resolves there.
    if depth_at(tokens, caret) != base:
        return False
    operators = [
        index
        for index in range(lo, hi)
        if tokens[index].type is TokenType.IDENT
        and tokens[index].depth == base
        and tokens[index].value.upper() in _SET_OPERATORS
    ]
    if not operators:
        return False
    for index in range(operators[-1] + 1, hi):
        token = tokens[index]
        if token.type in _SKIP or token.depth != base:
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is not None and matched[0] in _SET_OPERATION_TAIL:
            # Past the clause's own keyword, not from where it starts. A caret on
            # the `O` of ORDER, or between its letters, is completing that word —
            # and blanking it there left `ORDER B⌶Y` unable to finish itself.
            return caret >= tokens[matched[1] - 1].end
    return False


def _base_depth(tokens: Sequence[Token], lo: int, hi: int) -> int:
    """The shallowest paren depth among the significant tokens in [lo, hi)."""
    return _remembered(
        tokens,
        ('base', lo, hi),
        lambda: min((t.depth for t in tokens[lo:hi] if t.type not in _SKIP), default=0),
    )


def _subquery_bodies(tokens: Sequence[Token], lo: int, hi: int) -> tuple[tuple[int, int], ...]:
    """Index ranges of parenthesised bodies that begin with SELECT, one level down."""
    return _remembered(tokens, ('bodies', lo, hi), lambda: _scan_subquery_bodies(tokens, lo, hi))


def _scan_subquery_bodies(tokens: Sequence[Token], lo: int, hi: int) -> tuple[tuple[int, int], ...]:
    """Index ranges of parenthesised bodies that begin with SELECT, one level down."""
    depth = _base_depth(tokens, lo, hi)
    bodies = []
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.PUNCT or token.text != '(' or token.depth != depth:
            continue
        body_lo = _skip_forward(tokens, index + 1, hi)
        if body_lo >= hi or tokens[body_lo].type is not TokenType.IDENT:
            continue
        if tokens[body_lo].value.upper() not in {'SELECT', 'WITH', 'VALUES', 'TABLE'}:
            continue
        bodies.append((body_lo, _matching_paren(tokens, index, hi)))
    return tuple(bodies)


def _derived_tables(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
) -> list[tuple[int, int, str | None, tuple[str, ...]]]:
    """
    Subquery bodies in a FROM position: (body span, alias, declared columns).

    A body qualifies by what introduces it. `FROM (`, `JOIN (` and `LATERAL (`
    do; a comma does too, because `FROM a, (SELECT ...) b` is a join written
    the older way. Anything else is an expression subquery, which is a value
    rather than a relation.

    `(SELECT ...) s(a, b)` renames the outputs, and those names are what the
    author will reference — the body's own are no longer reachable.
    """
    out = []
    for body_lo, body_hi in _subquery_bodies(tokens, lo, hi):
        opener = body_lo - 1
        while opener > lo and tokens[opener].text != '(':
            opener -= 1
        before = _skip_back(tokens, opener - 1)
        if before < lo:
            continue
        token = tokens[before]
        introduced = (
            token.type is TokenType.IDENT
            and not token.quoted
            and token.value.upper() in _RELATION_CLAUSES | {'JOIN', 'LATERAL'}
        ) or (
            token.type is TokenType.PUNCT and token.text == ',' and _inside_a_relation_list(tokens, before, lo, dialect)
        )
        if not introduced:
            continue
        alias, after = _read_alias(tokens, body_hi + 1, hi, dialect)
        declared, _ = _read_declared_columns(tokens, after, hi)
        out.append((body_lo, body_hi, alias, declared))
    return out


def _inside_a_relation_list(tokens: Sequence[Token], comma: int, lo: int, dialect: Dialect) -> bool:
    """Whether the comma at `comma` separates relations rather than expressions."""
    depth = tokens[comma].depth
    for index in range(comma - 1, lo - 1, -1):
        token = tokens[index]
        if token.type in _SKIP or token.depth != depth:
            continue
        matched = _clause_starting_at(tokens, index, comma, dialect.clauses)
        if matched is not None:
            return matched[0] in _RELATION_CLAUSES
    return False


def _bind(relation: Relation, ctes: dict[str, Relation]) -> Relation:
    """Rebind a reference to a declared CTE, so resolve never asks the catalog for it."""
    if len(relation.path) != 1:
        return relation
    declared = ctes.get(relation.path[0])
    if declared is None:
        return relation
    return Relation(
        alias=relation.alias,
        path=relation.path,
        source='cte',
        projection=declared.projection,
    )


def _relations_in(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> tuple[Relation, ...]:
    """Every table reference introduced between `lo` and `hi`."""
    return _remembered(
        tokens,
        # `dialect` too: this scan reads its clauses and reserved words, unlike
        # the other three, so two dialects over one stream would otherwise get
        # each other's answer. Not reachable while every `lex` call serves a
        # single dialect — but reusing a stream is the next thing anyone tries.
        ('relations', lo, hi, caret, dialect.name),
        lambda: _scan_relations_in(tokens, lo, hi, caret, dialect),
    )


def _scan_relations_in(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> tuple[Relation, ...]:
    """The scan behind `_relations_in`."""
    relations: list[Relation] = []
    written_to: list[Relation] = []
    shelter = _unclosed_call_depth(tokens, lo, hi, dialect)
    index = lo
    while index < hi:
        token = tokens[index]
        if token.type in _SKIP:
            index += 1
            continue
        # Relations belong to the level that declared them; a nested subquery's
        # FROM is private to it and is picked up by that level's own scope.
        if token.depth - shelter(index) > tokens[lo].depth:
            index += 1
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is None or not _introduces_relations(tokens, matched, hi):
            index += 1
            continue
        # An INSERT target is kept apart from the rest. It is a relation of the
        # statement but not of every clause in it, which no other entry in
        # `_RELATION_CLAUSES` has to distinguish.
        found = written_to if matched[0] == 'INSERT INTO' else relations
        index = _read_relation_list(tokens, matched[1], hi, caret, dialect, found, matched[0])
    if not written_to:
        return tuple(relations)
    return tuple(_around_an_insert_target(tokens, lo, hi, caret, dialect, written_to, relations))


def _around_an_insert_target(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    written_to: list[Relation],
    relations: list[Relation],
) -> list[Relation]:
    """
    Which relations an INSERT's three positions can see.

    Measured on all three backends, which agree. The target is *not* in scope
    for the source query: `INSERT INTO auth_group (id, name) SELECT id, name
    FROM auth_user` is `column "name" does not exist` on Postgres, `Column
    'name' cannot be resolved` on Trino and `Missing columns: 'name'` on
    ClickHouse, and the qualified form is refused by all three as well.

    `RETURNING` is the mirror: it names the row that was written, so the target
    is all it resolves — `RETURNING username`, against a source called
    `auth_user`, is `column "username" does not exist`. Postgres is the only one
    of the three that has the clause; the other two do not parse the word.

    Which leaves the column list, where the target is the whole answer and is
    why it stays in `_RELATION_CLAUSES` at all.
    """
    base = tokens[lo].depth
    opens_source: int | None = None
    opens_returning: int | None = None
    for index in range(lo, hi):
        token = tokens[index]
        if token.type in _SKIP or token.depth != base:
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is None:
            continue
        if matched[0] == 'SELECT' and opens_source is None:
            opens_source = token.start
        elif matched[0] == 'RETURNING' and opens_returning is None:
            opens_returning = token.start
    if opens_returning is not None and caret >= opens_returning:
        return written_to
    if opens_source is not None and caret >= opens_source:
        return relations
    # Before the source query: the target, and only the target. This used to
    # return both, so `INSERT INTO groups (⌶) SELECT ... FROM users` offered the
    # source's columns in the list of columns being written to — `INSERT INTO
    # groups (username)` is `column "username" of relation "groups" does not
    # exist`. The commit that split these three positions left this one as it
    # found it; the test it shipped used a statement with no source `SELECT`,
    # which is the one shape where target and source agree.
    return written_to if opens_source is not None else [*written_to, *relations]


def clauses_written(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> frozenset[str]:
    """
    Clause names already present in the caret's branch, at the caret's own depth.

    Both sides of the caret count: `SELECT id <caret> FROM t` already has its
    FROM, and offering another one there is what produces `FROM FROM t`. Each
    branch of a set operation is counted separately, because the second branch
    brings its own SELECT and FROM.
    """
    lo, hi = _branch_at(tokens, lo, hi, caret)
    depth = depth_at(tokens, caret)
    found: set[str] = set()
    index = lo
    while index < hi:
        if tokens[index].type in _SKIP or tokens[index].depth != depth:
            index += 1
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is None:
            index += 1
            continue
        found.add(matched[0])
        index = matched[1]
    return frozenset(found)


def words_in_item(tokens: Sequence[Token], caret: int, dialect: Dialect) -> frozenset[str]:
    """
    Unquoted keywords written in the caret's own list item, at its own depth.

    An item runs from the last comma, or from the start of its own clause,
    to the caret. Some words are one choice made once — a sort direction, a
    nulls placement — and the clause's continuation list cannot know which of
    them the author already picked.

    Stopping at the clause is what makes the answer about *this* item. Without
    it the run reached back across the whole statement whenever no comma
    intervened, so `JOIN b USING (id) ORDER BY x ` reported `USING` as a word of
    the ORDER BY item and settled the sort-direction choice that word shares
    with `ASC`. The same shape as the bug `at_the_clause_start` carried, in the
    other function that walks back from the caret.

    The clause's own name stays in the set, because a clause can itself be one
    half of a choice: `LIMIT 10 ` must not go on offering `FETCH`, and `LIMIT`
    is the only evidence it was written.

    A select item's `*` is reported as `*`, which no keyword can collide with.
    It is not a word, but what it rules out is the same kind of thing the words
    rule out: a star takes no alias, so `SELECT * ` may not be offered AS.
    """
    depth = depth_at(tokens, caret)
    found: list[str] = []
    for index in range(_index_before(tokens, caret), -1, -1):
        token = tokens[index]
        if token.depth != depth or token.start >= caret:
            continue
        if token.type is TokenType.PUNCT and token.text == ',':
            break
        if token.type is TokenType.IDENT and not token.quoted:
            found.append(token.value.upper())
            if _clause_starting_at(tokens, index, len(tokens), dialect.clauses) is not None:
                break
        elif token.type is TokenType.OPERATOR and token.text == '*' and _star_is_an_item(tokens, index, dialect):
            found.append('*')
    return frozenset(found)


def select_list_end(tokens: Sequence[Token], caret: int, dialect: Dialect) -> int:
    """
    The offset where a FROM clause belongs: just past the select list.

    Not the end of the statement — `SELECT na ORDER BY 1` has somewhere for a
    FROM to go and it is before the ORDER BY. Falls back to the end of the
    caret's statement when nothing follows the list.
    """
    lo, hi = statement_at(tokens, caret)
    lo, hi = _branch_at(tokens, lo, hi, caret)
    # Neither of those clamps to the caret's own paren group, so a caret in a
    # derived table or a CTE body used to scan straight out of it and answer with
    # an offset in the enclosing statement — which then grew a second FROM while
    # the subquery that needed one still had none.
    # Only a group that opens a *query*. A call's argument list is at the same
    # depth and is not a place a FROM clause can go — clamping to it wrote
    # `SELECT count(auth_user.id FROM public.auth_user)`, which does not parse.
    group = _group_start(tokens, caret, depth_at(tokens, caret))
    if group > lo and _opens_a_query(tokens, group - 1, hi, dialect):
        lo, hi = group, min(hi, _matching_paren(tokens, group - 1, hi))
    depth = _base_depth(tokens, lo, hi)
    for index in range(lo, hi):
        token = tokens[index]
        if token.type in _SKIP or token.depth != depth or token.end <= caret:
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is not None and matched[0] != 'SELECT':
            # Clamped like the fallback below, which was the only one that got
            # it: with an empty select list this walks back over the whitespace
            # *before* the caret, ordering the clause ahead of the column and
            # inverting `Insertion.edits`.
            return max(_skip_back_over_space(tokens, index), caret)
    # Back over trailing trivia, not merely to the last token. A comment closing
    # the buffer is in `_SKIP` for the scan above and was still the answer here,
    # so the clause landed *inside* it and the statement ran with no FROM at all.
    # Whitespace matters for the same reason one step on: `_branch_at` ends the
    # branch at a set operator, so the last token is the space before UNION and
    # stopping there wrote `FROM usersUNION` — one identifier, no set operation.
    #
    # Never before the caret, though. At `SELECT ⌶` the select list ends exactly
    # there, and an offset behind it would put the clause in front of the column
    # it was fetched for, breaking `Insertion.edits`' latest-first ordering.
    # A literal nobody has closed is deliberately *not* stepped over. It runs to
    # end of input, so there is no offset after it to use — every candidate is
    # inside the quote — and putting the clause before it instead only trades
    # that for `, FROM`. The statement cannot be valid until the quote is closed,
    # so neither reading is an answer and this leaves the simpler one.
    last = _skip_back(tokens, hi - 1)
    return max(tokens[last].end, caret) if last >= lo else caret


def _skip_back_over_space(tokens: Sequence[Token], index: int) -> int:
    """The offset just before `index`, ignoring the whitespace in front of it."""
    probe = _skip_back(tokens, index - 1)
    return tokens[probe].end if probe >= 0 else tokens[index].start


def statement_form(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
) -> str | None:
    """
    Which kind of statement the caret is in: SELECT, UPDATE, INSERT INTO...

    Read from the words that can start one, at the caret's depth. `WITH` yields
    to whatever it introduces — `WITH x AS (...) SELECT` is a SELECT, and the
    CTE body sits a level deeper where this does not reach.
    """
    written = clauses_written(tokens, lo, hi, caret, dialect)
    starts = [name for name in dialect.statement_start if name in written]
    if not starts:
        return None
    return next((name for name in starts if name != 'WITH'), starts[0])


def _unclosed_call_depth(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
) -> Callable[[int], int]:
    """
    How much of a token's depth comes from a group the author has not closed yet.

    `SELECT count(<caret> FROM t` puts the FROM textually inside the argument
    list, but the closing paren is simply unwritten and the clause belongs to
    the outer query. A group whose first word *starts a query* is a genuine
    subquery — `FROM (SELECT <caret> FROM t` — and keeps its depth however
    unfinished it is, because its FROM really is private to it.

    Returns a lookup rather than a list so the common case, a statement with no
    dangling paren, costs one scan and no allocation. The dangling case answers
    by bisection rather than by counting: `_relations_in` asks once per token, so
    a scan per ask was O(dangling x tokens) on exactly the half-typed input the
    editor holds between a caret and its closing paren.

    Worth naming what this does *not* fix, since the shape looks solved and is
    not: a query of nested unclosed subqueries is still super-quadratic, and the
    cost is `_by_first_word`'s `@cache` re-hashing the whole `ClauseModel` on
    every lookup — 118 million hash calls for 1.5 KB. That is a separate change.
    """
    open_groups: list[int] = []
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.PUNCT:
            continue
        if token.text == '(':
            open_groups.append(index)
        elif token.text == ')' and open_groups:
            open_groups.pop()

    # `open_groups` is a stack of ascending indices, so what survives the filter
    # is ascending too and `bisect_left` is exactly "how many start before this".
    starts = tuple(index for index in open_groups if not _opens_a_query(tokens, index, hi, dialect))
    if not starts:
        return lambda _: 0
    return lambda index: bisect.bisect_left(starts, index)


def _opens_a_query(tokens: Sequence[Token], index: int, hi: int, dialect: Dialect) -> bool:
    """Whether the group opening at `index` begins with a word that starts a statement."""
    matched = _clause_starting_at(tokens, index + 1, hi, dialect.clauses)
    return matched is not None and matched[0] in dialect.statement_start


def _introduces_relations(tokens: Sequence[Token], matched: tuple[str, int], hi: int) -> bool:
    """
    Whether the clause just matched is followed by a list of relations.

    `USING` is two clauses wearing one name: the join's column list, written
    `USING (a, b)`, and `DELETE FROM t USING other`, which brings a relation
    into scope exactly as a join does. The parenthesis tells them apart.
    """
    name, after = matched
    if name == 'USING':
        probe = _skip_forward(tokens, after, hi)
        return probe >= hi or tokens[probe].text != '('
    return name in _RELATION_CLAUSES


def _remembered(tokens: Sequence[Token], key: object, produce: Callable[[], _Answer]) -> _Answer:
    """
    `produce()`, asked once per key for the life of this token stream.

    Every caller is a pure function of the tokens and an index range, and the
    stream is immutable, so an answer cannot go stale. The scope walk descends a
    level at a time and rescans ranges the level above already scanned, which is
    what turns a generated query of nested subqueries from linear into something
    much worse. A plain sequence — a slice, or a list a test built — has no memo
    and is computed as before.
    """
    memo: dict[object, object] | None = getattr(tokens, 'memo', None)
    if memo is None:
        return produce()
    if key not in memo:
        fresh = produce()
        # Bounded. `_inside_a_relation_list` scans back to each comma, so it asks
        # with `hi` set to that comma, and a long comma-separated relation list
        # opens one family of keys per comma — 323,207 entries and 41 MiB for
        # 6.7 KB of SQL, where the memo was also *slower* than not memoising.
        # The key cannot drop `hi`, since what it truncates is real; past this
        # many entries the walk is not the shape this was built for.
        if len(memo) >= _MEMO_LIMIT:
            return fresh
        memo[key] = fresh
    answer: _Answer = memo[key]  # type: ignore[assignment]
    return answer


def _clause_starting_at(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    clauses: ClauseModel,
) -> tuple[str, int] | None:
    """
    The clause beginning at `index`, memoised against the token stream.

    The scope walk descends a level at a time and each level rescans ranges the
    level above already scanned, so this was asked 235,245 times for a query of
    nested derived tables holding 324 distinct questions — a 99.9% repeat rate at
    depth eighty, and the cubic term in what a code generator produces.

    Safe because the answer is a pure function of its arguments and the stream is
    immutable, so the memo cannot go stale; it lives on the stream, so it is
    discarded with it. A plain sequence — a slice, or a list built by a test —
    simply has no memo and is scanned as before.
    """
    return _remembered(tokens, ('clause', index, hi, clauses), lambda: _scan_clause_start(tokens, index, hi, clauses))


def _scan_clause_start(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    clauses: ClauseModel,
) -> tuple[str, int] | None:
    """
    (clause name, index just past it) when a clause name starts at `index`.

    Only the clauses whose first word is the word actually written are tried,
    and the run reports where it ended. Trying all thirty and then hunting the
    stream for the matched token made this quadratic in the length of the
    query — and a query long enough to matter is exactly the one worth
    completing.
    """
    first = tokens[index] if index < len(tokens) else None
    if first is None or first.type is not TokenType.IDENT or first.quoted:
        return None
    for name in _by_first_word(clauses).get(first.value.upper(), ()):
        parts = name.split()
        run = _ident_run(tokens, index, hi, len(parts))
        if run is not None and [t.value.upper() for t in run[0]] == parts:
            return name, run[1]
    return None


@cache
def _by_first_word(clauses: ClauseModel) -> dict[str, tuple[str, ...]]:
    """Clause names grouped by their first word, longest first so `GROUP BY` beats `GROUP`."""
    grouped: dict[str, list[str]] = {}
    for name in clauses.names():
        # A name with no words in it has no first word, and `split()[0]` raised
        # rather than skipping — so one blank clause reached through
        # `extend(Clause(name=''))` took `complete` down with an IndexError.
        # Dropped silently here because it can never match anything anyway;
        # `DialectConformance.structure` is where it gets said out loud.
        words = name.split()
        if words:
            grouped.setdefault(words[0], []).append(name)
    return {word: tuple(names) for word, names in grouped.items()}


def _read_relation_list(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    out: list[Relation],
    clause: str = 'FROM',
) -> int:
    """Read comma-separated relation references until the next clause keyword."""
    while index < hi:
        index = _skip_forward(tokens, index, hi)
        if index >= hi or _clause_starting_at(tokens, index, hi, dialect.clauses) is not None:
            break
        token = tokens[index]
        if token.type is TokenType.PUNCT and token.text == ',':
            index += 1
            continue
        if token.type is TokenType.IDENT and token.value.upper() in _JOIN_QUALIFIERS:
            index += 1
            continue
        if token.type is not TokenType.IDENT:
            break
        # A reserved word here is a keyword, not a relation. `FROM users AS `
        # leaves the AS unconsumed once no alias follows it, and reading it as a
        # relation would put a phantom `as` in scope — which then supplies the
        # generated alias, and answers `as.` with nothing.
        if not token.quoted and token.value.upper() in dialect.reserved_upper:
            break
        path, index = _read_dotted_path(tokens, index, hi)
        call = _skip_forward(tokens, index, hi)
        if (
            clause in _FUNCTION_SOURCES
            and call < hi
            and tokens[call].type is TokenType.PUNCT
            and tokens[call].text == '('
        ):
            # A set-returning function, not a relation: `FROM generate_series(1, 10) g`.
            # Its rows have no columns this engine can name, but reading it as a
            # relation asks the catalog for a table that does not exist and — worse
            # — leaves the argument list to be read as more relations.
            index = _read_function_source(tokens, call, hi, dialect, out)
            continue
        reference_end = path[-1].end
        # A dangling dot means no identifier followed it, so the reference is
        # still being typed: `FROM analytics.<caret>` names no relation yet.
        probe = _skip_forward(tokens, index, hi)
        if probe < hi and tokens[probe].type is TokenType.PUNCT and tokens[probe].text == '.':
            reference_end = tokens[probe].end
            index = probe + 1
        if path[0].start < caret <= reference_end:
            continue
        alias, index = _read_alias(tokens, index, hi, dialect)
        out.append(Relation(alias=alias, path=tuple(t.value for t in path), source='table'))
    return index


def _read_function_source(
    tokens: Sequence[Token],
    call: int,
    hi: int,
    dialect: Dialect,
    out: list[Relation],
) -> int:
    """
    Skip a function call in a FROM list, keeping any relation it declares.

    `AS t(a int, b text)` is a column definition list: it names the only columns
    this engine can know about such a source, so the alias enters scope with
    them as its projection. Without one there is nothing to offer, and the
    relation is left out rather than sent to the catalog as a table name.
    """
    index = _matching_paren(tokens, call, hi) + 1
    alias, index = _read_alias(tokens, index, hi, dialect)
    declared, index = _read_declared_columns(tokens, index, hi)
    if alias is not None and declared:
        # Only with a column definition list. Without one nothing is known about
        # the rows, and a relation in scope that can answer nothing still counts
        # towards "more than one relation", which qualifies every other column.
        out.append(
            Relation(alias=alias, path=(), source='subquery', projection=Projection(columns=declared)),
        )
    return index


def _skip_forward(tokens: Sequence[Token], index: int, hi: int) -> int:
    """The next index at or after `index` that is not whitespace or a comment."""
    while index < hi and tokens[index].type in _SKIP:
        index += 1
    return index


def _read_dotted_path(tokens: Sequence[Token], index: int, hi: int) -> tuple[list[Token], int]:
    """Read `ident (. ident)*` starting at `index`."""
    path = [tokens[index]]
    index += 1
    while True:
        probe = _skip_forward(tokens, index, hi)
        if probe >= hi or tokens[probe].type is not TokenType.PUNCT or tokens[probe].text != '.':
            return path, index
        after = _skip_forward(tokens, probe + 1, hi)
        if after >= hi or tokens[after].type is not TokenType.IDENT:
            return path, index
        path.append(tokens[after])
        index = after + 1


def _read_alias(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    dialect: Dialect,
) -> tuple[str | None, int]:
    """Read an optional alias, with or without AS."""
    probe = _skip_forward(tokens, index, hi)
    if probe >= hi or tokens[probe].type is not TokenType.IDENT:
        return None, index
    if tokens[probe].value.upper() == 'AS':
        probe = _skip_forward(tokens, probe + 1, hi)
        if probe >= hi or tokens[probe].type is not TokenType.IDENT:
            return None, index
        return tokens[probe].value, probe + 1
    word = tokens[probe].value.upper()
    if word in dialect.reserved_upper or _clause_starting_at(tokens, probe, hi, dialect.clauses) is not None:
        return None, index
    return tokens[probe].value, probe + 1


def select_outputs(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
    ctes: dict[str, Relation] | None = None,
    remaining: int = _MAX_NESTING,
) -> Projection:
    """
    The output columns of the select body spanning [lo, hi).

    Explicit names and aliases go into `columns`. A bare `*` or a qualified
    `t.*` cannot be expanded here — the catalog holds that answer — so the
    relation it refers to is recorded in `stars` for resolve to finish.

    Everything a body declares for itself counts as one of its relations: a
    derived table inside it, and a WITH of its own. `SELECT * FROM (SELECT id
    FROM t) d` has to reach `d` to know what its star stands for, and
    `WITH outer_q AS (WITH inner_q AS (...) SELECT * FROM inner_q)` has to
    reach inner_q — neither is visible from the level above.
    """
    # The same bound `_scope_level` keeps, for the same stated reason: this pair
    # of functions calls back and forth once per nesting level, and `_MAX_NESTING`
    # used to be applied only one stage above. A generated document — 495 nested
    # `WITH a AS(`, or a thousand nested derived tables — reached Python's stack
    # limit and the RecursionError left `complete` entirely, including through
    # the language server's handler, which documents that it never raises.
    if remaining <= 0:
        return Projection()
    declared, _ = _read_ctes(tokens, lo, hi, dialect, remaining - 1)
    visible = {**(ctes or {}), **declared}
    body_relations = [_bind(r, visible) for r in _relations_in(tokens, lo, hi, -1, dialect)]
    for derived_lo, derived_hi, alias, columns_of in _derived_tables(tokens, lo, hi, dialect):
        nested = (
            Projection(columns=columns_of)
            if columns_of
            else select_outputs(tokens, derived_lo, derived_hi, dialect, visible, remaining - 1)
        )
        body_relations.append(Relation(alias=alias, path=(), source='subquery', projection=nested))
    base = min((t.depth for t in tokens[lo:hi] if t.type not in _SKIP), default=0)
    start = _after_clause(tokens, lo, hi, 'SELECT', dialect, depth=base)
    if start is None or start >= hi:
        # `SELECT` with nothing after it: an empty select list, which is what the
        # editor holds for the instant between the keyword and the first item.
        return Projection()
    end = _next_clause_at_depth(tokens, start, hi, dialect, tokens[start].depth, {'FROM'})
    columns: list[str] = []
    stars: list[Relation] = []
    for item_lo, item_hi in _split_items(tokens, start, end):
        name, star_for = _output_of(tokens, item_lo, item_hi, body_relations, dialect)
        if star_for is not None:
            stars.extend(star_for)
        elif name is not None:
            columns.append(name)
    return Projection(columns=tuple(columns), stars=tuple(stars))


def _after_clause(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    name: str,
    dialect: Dialect,
    depth: int | None = None,
) -> int | None:
    """
    Index just past the first occurrence of clause `name`.

    `depth` restricts the search to one query level, which matters for
    `WITH a AS (SELECT ...) SELECT ...`: without it the CTE body's SELECT is
    found first and the outer projection comes out wrong.
    """
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is not TokenType.IDENT or (depth is not None and token.depth != depth):
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is not None and matched[0] == name:
            return matched[1]
    return None


def _next_clause_at_depth(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
    depth: int,
    names: set[str],
) -> int:
    """Index of the next clause in `names` at `depth`, or `hi`."""
    for index in range(lo, hi):
        if tokens[index].type is not TokenType.IDENT or tokens[index].depth != depth:
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is not None and matched[0] in names:
            return index
    return hi


def _split_items(tokens: Sequence[Token], lo: int, hi: int) -> list[tuple[int, int]]:
    """Split [lo, hi) on commas at the shallowest depth present."""
    if lo >= hi:
        return []
    depths = [t.depth for t in tokens[lo:hi] if t.type not in _SKIP]
    if not depths:
        return []
    base = min(depths)
    items, start = [], lo
    for index in range(lo, hi):
        token = tokens[index]
        if token.type is TokenType.PUNCT and token.text == ',' and token.depth == base:
            items.append((start, index))
            start = index + 1
    items.append((start, hi))
    return [(a, b) for a, b in items if a < b]


def _output_of(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    relations: Sequence[Relation],
    dialect: Dialect,
) -> tuple[str | None, list[Relation] | None]:
    """
    (output name, star sources). At most one of the two is not None.

    An item names an output four ways: `AS n`, a bare implicit alias `count(*) n`,
    a plain or qualified column, and a bare call taking the function's own name.
    Anything else — `is_staff AND is_staff` — is an expression Postgres calls
    `?column?`, and nothing useful can be suggested for it.
    """
    significant = _without_quantifier([t for t in tokens[lo:hi] if t.type not in _SKIP])
    if not significant:
        return None, None

    if significant[-1].type is TokenType.OPERATOR and significant[-1].text == '*':
        if len(significant) >= 3 and significant[-2].text == '.':
            label = significant[-3].value
            return None, [r for r in relations if r.label == label]
        return None, list(relations)

    if len(significant) >= 2 and significant[-2].value.upper() == 'AS':  # noqa: PLR2004
        return significant[-1].value, None

    if len(significant) == 1 and significant[0].type is TokenType.IDENT:
        # Guarded like the two branches below it, and for the same reason.
        # `SELECT NULL` names nothing — Postgres calls the result `?column?` —
        # but this read `null` as an output name, and `rank` then quoted it
        # *because* it is reserved, so `"null"` arrived above every real column
        # with the local-origin bonus behind it. In a CTE it was the only answer,
        # and the reference it writes does not exist.
        if not significant[0].quoted and significant[0].value.upper() in dialect.reserved_upper:
            return None, None
        return significant[0].value, None

    if _is_an_implicit_alias(significant, dialect):
        return significant[-1].value, None

    if _is_a_call(significant, dialect):
        # A bare call is named after the function: `SELECT count(*)` outputs
        # `count`, and so does `row_number() OVER (...)`. The head names it,
        # however many parenthesised groups follow.
        return significant[0].value, None

    if len(significant) >= 2 and significant[-1].type is TokenType.IDENT and significant[-2].text == '.':  # noqa: PLR2004
        return significant[-1].value, None

    return None, None


def _without_quantifier(significant: list[Token]) -> list[Token]:
    """
    Drop a leading `DISTINCT`, `ALL` or `DISTINCT ON (...)` from a select item.

    They qualify the whole select rather than name anything, and leaving them
    attached makes `SELECT DISTINCT id` look like an expression: `id` follows a
    reserved word, which is how an operand is told from an alias.
    """
    if not significant or significant[0].type is not TokenType.IDENT:
        return significant
    if significant[0].value.upper() not in {'DISTINCT', 'ALL'} or significant[0].quoted:
        return significant
    rest = significant[1:]
    if rest and rest[0].type is TokenType.IDENT and rest[0].value.upper() == 'ON':
        opening = next((index for index, t in enumerate(rest) if t.text == '('), None)
        if opening is not None:
            level = 0
            for index in range(opening, len(rest)):
                if rest[index].type is not TokenType.PUNCT:
                    continue
                level += 1 if rest[index].text == '(' else -1 if rest[index].text == ')' else 0
                if level == 0:
                    return rest[index + 1 :]
    return rest


def _is_an_implicit_alias(significant: Sequence[Token], dialect: Dialect) -> bool:
    """
    Whether the item ends in a name that renames what precedes it.

    `count(*) n` and `u.id x` do; `is_staff AND is_staff` does not. What tells
    them apart is the token before: an alias follows a *finished* operand, so a
    reserved word there means the trailing name is an operand of its own.
    """
    minimum = 2
    if len(significant) < minimum or significant[-1].type is not TokenType.IDENT:
        return False
    if significant[-1].value.upper() in dialect.reserved_upper and not significant[-1].quoted:
        return False
    before = significant[-2]
    if before.type is TokenType.PUNCT:
        return before.text == ')'
    if before.type is TokenType.IDENT:
        return before.quoted or before.value.upper() not in dialect.reserved_upper
    return before.type in (TokenType.NUMBER, TokenType.STRING)


def _is_a_call(significant: Sequence[Token], dialect: Dialect) -> bool:
    """Whether the item is a function call, which Postgres names after the function."""
    minimum = 2
    if len(significant) < minimum or significant[0].type is not TokenType.IDENT:
        return False
    if not significant[0].quoted and significant[0].value.upper() in dialect.reserved_upper:
        return False
    return significant[1].type is TokenType.PUNCT and significant[1].text == '('


def _read_ctes(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
    remaining: int = _MAX_NESTING,
) -> tuple[dict[str, Relation], dict[tuple[int, int], dict[str, Relation]]]:
    """
    Every relation declared in a leading WITH clause, keyed by name.

    Also returns what each body can see, which is not the same thing. A
    non-recursive WITH is read in order, so the first body cannot reference the
    second — Postgres answers `relation "b" does not exist` — and RECURSIVE is
    precisely the word that adds the body's own name to its own scope.
    """
    # Depth-restricted, like every other caller of `_after_clause`. Without it
    # this found the first WITH at any level, so a CTE declared inside a derived
    # table or an expression subquery joined the *statement's* table — offering a
    # name the server refuses, and, worse, rebinding a real relation that shared
    # it, which lost every one of that relation's columns.
    start = _after_clause(tokens, lo, hi, 'WITH', dialect, depth=_base_depth(tokens, lo, hi))
    if start is None or remaining <= 0:
        return {}, {}
    ctes: dict[str, Relation] = {}
    visible: list[tuple[tuple[int, int], tuple[str, ...]]] = []
    index = _skip_forward(tokens, start, hi)
    recursive = False
    if index < hi and tokens[index].type is TokenType.IDENT and tokens[index].value.upper() == 'RECURSIVE':
        recursive = True
        index = _skip_forward(tokens, index + 1, hi)
    while index < hi:
        index = _skip_forward(tokens, index, hi)
        if index >= hi or tokens[index].type is not TokenType.IDENT:
            break
        name = tokens[index].value
        index += 1
        declared, index = _read_declared_columns(tokens, index, hi)
        index = _skip_forward(tokens, index, hi)
        if index >= hi or tokens[index].value.upper() != 'AS':
            break
        index = _skip_forward(tokens, index + 1, hi)
        # `AS MATERIALIZED (...)` and `AS NOT MATERIALIZED (...)` are both legal.
        while index < hi and tokens[index].type is TokenType.IDENT and tokens[index].value.upper() in _CTE_MODIFIERS:
            index = _skip_forward(tokens, index + 1, hi)
        if index >= hi or tokens[index].text != '(':
            break
        body_lo = index + 1
        body_hi = _matching_paren(tokens, index, hi)
        # Earlier CTEs are in scope inside this one's body, so `WITH a AS (...),
        # b AS (SELECT * FROM a)` can resolve b's star against a's projection.
        projection = (
            Projection(columns=declared)
            if declared
            # `remaining` unchanged, not decremented: this and `select_outputs`
            # are two halves of one nesting level, and spending the budget on
            # both legs halved it — truncating a CTE chain at 32 levels where
            # the stack it guards only gives out near 490.
            else select_outputs(tokens, body_lo, body_hi, dialect, ctes, remaining)
        )
        visible.append(((body_lo, body_hi), (*ctes, *((name,) if recursive else ()))))
        ctes[name] = Relation(alias=None, path=(name,), source='cte', projection=projection)
        index = _skip_forward(tokens, body_hi + 1, hi)
        if index < hi and tokens[index].text == ',':
            index += 1
            continue
        break
    return ctes, {span: {n: ctes[n] for n in names if n in ctes} for span, names in visible}


def _read_declared_columns(tokens: Sequence[Token], index: int, hi: int) -> tuple[tuple[str, ...], int]:
    """
    Read an optional `(x, y)` column list following a CTE or function alias.

    The first identifier of each comma-separated group, because a function's
    column definition list spells the types too — `AS t(a int, b text)` declares
    two columns, not four.
    """
    probe = _skip_forward(tokens, index, hi)
    if probe >= hi or tokens[probe].text != '(':
        return (), index
    close = _matching_paren(tokens, probe, hi)
    names: list[str] = []
    wanted = True
    for token in tokens[probe + 1 : close]:
        if token.type is TokenType.PUNCT and token.text == ',' and token.depth == tokens[probe].depth + 1:
            wanted = True
        elif wanted and token.type is TokenType.IDENT:
            names.append(token.value)
            wanted = False
    return tuple(names), close + 1


def _matching_paren(tokens: Sequence[Token], index: int, hi: int) -> int:
    """Index of the `)` closing the `(` at `index`, or `hi`."""
    depth = tokens[index].depth
    for probe in range(index + 1, hi):
        token = tokens[probe]
        if token.type is TokenType.PUNCT and token.text == ')' and token.depth == depth:
            return probe
    return hi
