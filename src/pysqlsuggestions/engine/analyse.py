"""
Pure analysis over a token stream.

Every function here takes tokens and a caret offset and returns a plain value.
Nothing performs I/O, and nothing knows what a catalog is.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import cache

from pysqlsuggestions.dialects.base import ClauseModel, Dialect
from pysqlsuggestions.engine.lex import Token, TokenType
from pysqlsuggestions.types import Projection, Relation, Scope

_SKIP = (TokenType.WHITESPACE, TokenType.COMMENT)
_RELATION_CLAUSES = frozenset({'FROM', 'JOIN', 'UPDATE', 'DELETE FROM', 'INSERT INTO'})
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


def in_literal(tokens: Sequence[Token], caret: int) -> bool:
    """
    Whether the caret sits inside a string literal or a comment.

    A caret at the closing delimiter of a *terminated* literal is outside it —
    `'ab'<caret>` is back in ordinary SQL. A caret at the end of an unterminated
    one is inside, because there is no delimiter to have passed.
    """
    for token in tokens:
        if token.type not in (TokenType.STRING, TokenType.COMMENT):
            continue
        if token.start < caret < token.end or (caret == token.end and not token.terminated):
            return True
    return False


def string_under(tokens: Sequence[Token], caret: int) -> Token | None:
    """
    The string literal the caret is inside, if it is inside one.

    Separate from `in_literal`, which also answers for comments: a half-typed
    literal is a position with an answer — the values that column holds — where
    a comment is a position with none.
    """
    for token in tokens:
        if token.type is not TokenType.STRING:
            continue
        if token.start < caret < token.end or (caret == token.end and not token.terminated):
            return token
    return None


def statement_at(tokens: Sequence[Token], caret: int) -> tuple[int, int]:
    """
    The index range [lo, hi) of the statement containing `caret`.

    Statements are separated by semicolons at depth 0; a semicolon inside a
    string or inside parens does not split.
    """
    lo = 0
    for index, token in enumerate(tokens):
        if token.type is TokenType.PUNCT and token.text == ';' and token.depth == 0:
            if token.start >= caret:
                return lo, index
            lo = index + 1
    return lo, len(tokens)


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
    if token.type in (TokenType.NUMBER, TokenType.STRING):
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
    """
    return _words_before(tokens, caret) == tuple(clause.upper().split())


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


def _enclosing_call(tokens: Sequence[Token], index: int) -> str | None:
    """The uppercased name of the function whose argument list encloses `index`, if any."""
    wanted = tokens[index].depth - 1
    for candidate in range(index - 1, -1, -1):
        token = tokens[candidate]
        if token.type is not TokenType.PUNCT or token.text != '(' or token.depth != wanted:
            continue
        before = _skip_back(tokens, candidate - 1)
        if before < 0 or tokens[before].type is not TokenType.IDENT:
            return None
        return tokens[before].value.upper()
    return None


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
            return _scope_level(
                tokens,
                inner_lo,
                inner_hi,
                caret,
                dialect,
                ctes if body_ctes is None else body_ctes,
                parent=None if opaque_here else here,
                cte_scopes=cte_scopes,
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


def _base_depth(tokens: Sequence[Token], lo: int, hi: int) -> int:
    """The shallowest paren depth among the significant tokens in [lo, hi)."""
    return min((t.depth for t in tokens[lo:hi] if t.type not in _SKIP), default=0)


def _subquery_bodies(tokens: Sequence[Token], lo: int, hi: int) -> list[tuple[int, int]]:
    """Index ranges of parenthesised bodies that begin with SELECT, one level down."""
    depth = min((t.depth for t in tokens[lo:hi] if t.type not in _SKIP), default=0)
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
    return bodies


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
) -> list[Relation]:
    """Every table reference introduced between `lo` and `hi`."""
    relations: list[Relation] = []
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
        index = _read_relation_list(tokens, matched[1], hi, caret, dialect, relations, matched[0])
    return relations


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

    An item runs from the last comma to the caret. Some words are one choice
    made once — a sort direction, a nulls placement — and the clause's
    continuation list cannot know which of them the author already picked.

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
    depth = _base_depth(tokens, lo, hi)
    for index in range(lo, hi):
        token = tokens[index]
        if token.type in _SKIP or token.depth != depth or token.end <= caret:
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect.clauses)
        if matched is not None and matched[0] != 'SELECT':
            return _skip_back_over_space(tokens, index)
    return tokens[hi - 1].end if hi > lo else caret


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
    dangling paren, costs one scan and no allocation.
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

    starts = tuple(index for index in open_groups if not _opens_a_query(tokens, index, hi, dialect))
    if not starts:
        return lambda _: 0
    return lambda index: sum(1 for start in starts if start < index)


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


def _clause_starting_at(
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
        grouped.setdefault(name.split()[0], []).append(name)
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
    declared, _ = _read_ctes(tokens, lo, hi, dialect)
    visible = {**(ctes or {}), **declared}
    body_relations = [_bind(r, visible) for r in _relations_in(tokens, lo, hi, -1, dialect)]
    for derived_lo, derived_hi, alias, columns_of in _derived_tables(tokens, lo, hi, dialect):
        nested = (
            Projection(columns=columns_of)
            if columns_of
            else select_outputs(tokens, derived_lo, derived_hi, dialect, visible)
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
) -> tuple[dict[str, Relation], dict[tuple[int, int], dict[str, Relation]]]:
    """
    Every relation declared in a leading WITH clause, keyed by name.

    Also returns what each body can see, which is not the same thing. A
    non-recursive WITH is read in order, so the first body cannot reference the
    second — Postgres answers `relation "b" does not exist` — and RECURSIVE is
    precisely the word that adds the body's own name to its own scope.
    """
    start = _after_clause(tokens, lo, hi, 'WITH', dialect)
    if start is None:
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
            Projection(columns=declared) if declared else select_outputs(tokens, body_lo, body_hi, dialect, ctes)
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
