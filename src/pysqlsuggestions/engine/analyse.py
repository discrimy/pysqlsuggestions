"""
Pure analysis over a token stream.

Every function here takes tokens and a caret offset and returns a plain value.
Nothing performs I/O, and nothing knows what a catalog is.
"""

from __future__ import annotations

from collections.abc import Sequence

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
        return token.quoted or word in _LITERAL_KEYWORDS or word not in dialect.keywords
    return False


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
    Whether the caret is naming the target of a cast: `'7 days'::<caret>`.

    Only where the dialect has a cast operator at all — strict ANSI writes
    `CAST(x AS interval)` and has none.
    """
    marker = dialect.syntax.cast_operator
    if not marker:
        return False
    index = _index_before(tokens, caret)
    if index < 0:
        return False
    if tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        index -= 1
    index = _skip_back(tokens, index)
    return index >= 0 and tokens[index].type is TokenType.OPERATOR and tokens[index].text == marker


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
    if tokens[index].type is TokenType.IDENT and tokens[index].end >= caret:
        index -= 1

    index = _skip_back(tokens, index)
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
    """
    depth = depth_at(tokens, caret)
    armed = False
    for index in range(lo, hi):
        token = tokens[index]
        if token.type in _SKIP or token.start >= caret or token.depth != depth:
            continue
        if token.type is TokenType.OPERATOR and token.text in _COMPARISONS:
            armed = True
        elif token.type is TokenType.IDENT and not token.quoted:
            word = token.value.upper()
            if word in _CONNECTIVES or dialect.clauses.get(word) is not None:
                armed = False
            elif word in _PREDICATE_KEYWORDS:
                armed = True
    return armed


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
    words = clauses.names()
    if not words:
        return None
    depth = depth_at(tokens, caret)
    while depth >= 0:
        found = _scan_for_clause(tokens, lo, hi, caret, words, depth)
        if found is not None:
            return found
        depth -= 1
    return None


def _scan_for_clause(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    words: tuple[str, ...],
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
        if token.type is not TokenType.IDENT or token.depth != depth or token.end >= caret:
            continue
        for name in words:
            parts = name.split()
            run = _ident_run(tokens, index, hi, len(parts))
            if run is None or [t.value.upper() for t in run] != parts or run[-1].end >= caret:
                continue
            candidate = (run[-1].end, len(parts), name)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
            break
    return best[2] if best is not None else None


def _ident_run(tokens: Sequence[Token], start: int, hi: int, count: int) -> list[Token] | None:
    """
    `count` consecutive unquoted IDENT tokens beginning at `start`.

    Whitespace and comments are skipped. Quoting is how SQL says "this is a
    name, not syntax", so a quoted word never spells a clause: `FROM "limit"`
    is a relation called `limit`, and reading it as the LIMIT clause loses the
    relation along with the clause.
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
    return run if len(run) == count else None


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
    return _scope_level(tokens, lo, hi, caret, dialect, ctes, parent=None, cte_bodies=cte_bodies)


def _scope_level(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    ctes: dict[str, Relation],
    parent: Scope | None,
    cte_bodies: frozenset[tuple[int, int]] = frozenset(),
) -> Scope:
    """One query level, recursing into whichever subquery holds the caret."""
    lo, hi = _branch_at(tokens, lo, hi, caret)
    relations = [_bind(r, ctes) for r in _relations_in(tokens, lo, hi, caret, dialect)]
    for derived_lo, derived_hi, alias in _derived_tables(tokens, lo, hi, dialect):
        projection = select_outputs(tokens, derived_lo, derived_hi, dialect)
        relations.append(Relation(alias=alias, path=(), source='subquery', projection=projection))

    here = Scope(
        relations=tuple(relations),
        ctes=ctes,
        parent=parent,
        projection=select_outputs(tokens, lo, hi, dialect),
    )

    for inner_lo, inner_hi in _subquery_bodies(tokens, lo, hi):
        if tokens[inner_lo].start <= caret <= tokens[inner_hi - 1].end:
            # A CTE body is evaluated on its own: it cannot reference the outer
            # query's FROM list, nor the CTE being defined. A correlated
            # subquery can, so only CTE bodies drop the parent link.
            enclosing = None if (inner_lo, inner_hi) in cte_bodies else here
            return _scope_level(
                tokens,
                inner_lo,
                inner_hi,
                caret,
                dialect,
                ctes,
                parent=enclosing,
                cte_bodies=cte_bodies,
            )
    return here


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
) -> list[tuple[int, int, str | None]]:
    """Subquery bodies in a FROM position, with the alias that names them."""
    out = []
    for body_lo, body_hi in _subquery_bodies(tokens, lo, hi):
        opener = body_lo - 1
        while opener > lo and tokens[opener].text != '(':
            opener -= 1
        before = _skip_back(tokens, opener - 1)
        if before < lo or tokens[before].type is not TokenType.IDENT:
            continue
        if tokens[before].value.upper() not in _RELATION_CLAUSES | {'JOIN'}:
            continue
        alias, _ = _read_alias(tokens, body_hi + 1, hi, dialect)
        out.append((body_lo, body_hi, alias))
    return out


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
    index = lo
    while index < hi:
        token = tokens[index]
        if token.type in _SKIP:
            index += 1
            continue
        # Relations belong to the level that declared them; a nested subquery's
        # FROM is private to it and is picked up by that level's own scope.
        if token.depth > tokens[lo].depth:
            index += 1
            continue
        matched = _clause_starting_at(tokens, index, hi, dialect)
        if matched is None or matched[0] not in _RELATION_CLAUSES:
            index += 1
            continue
        index = _read_relation_list(tokens, matched[1], hi, caret, dialect, relations)
    return relations


def _clause_starting_at(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    dialect: Dialect,
) -> tuple[str, int] | None:
    """(clause name, index just past it) when a clause name starts at `index`."""
    for name in dialect.clauses.names():
        parts = name.split()
        run = _ident_run(tokens, index, hi, len(parts))
        if run is not None and [t.value.upper() for t in run] == parts:
            return name, _index_of(tokens, run[-1]) + 1
    return None


def _index_of(tokens: Sequence[Token], token: Token) -> int:
    """The position of `token` in `tokens`, located by its start offset."""
    for index, candidate in enumerate(tokens):
        if candidate.start == token.start:
            return index
    raise ValueError('token not in stream')


def _read_relation_list(
    tokens: Sequence[Token],
    index: int,
    hi: int,
    caret: int,
    dialect: Dialect,
    out: list[Relation],
) -> int:
    """Read comma-separated relation references until the next clause keyword."""
    while index < hi:
        index = _skip_forward(tokens, index, hi)
        if index >= hi or _clause_starting_at(tokens, index, hi, dialect) is not None:
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
    if word in dialect.reserved_upper or _clause_starting_at(tokens, probe, hi, dialect) is not None:
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
    """
    body_relations = [_bind(r, ctes or {}) for r in _relations_in(tokens, lo, hi, -1, dialect)]
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
        matched = _clause_starting_at(tokens, index, hi, dialect)
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
        matched = _clause_starting_at(tokens, index, hi, dialect)
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
    """(output name, star sources). At most one of the two is not None."""
    significant = [t for t in tokens[lo:hi] if t.type not in _SKIP]
    if not significant:
        return None, None

    if significant[-1].type is TokenType.OPERATOR and significant[-1].text == '*':
        if len(significant) >= 3 and significant[-2].text == '.':
            label = significant[-3].value
            return None, [r for r in relations if r.label == label]
        return None, list(relations)

    if len(significant) >= 2 and significant[-2].value.upper() == 'AS':
        return significant[-1].value, None

    if len(significant) == 1 and significant[0].type is TokenType.IDENT:
        return significant[0].value, None

    if len(significant) >= 2 and significant[-1].type is TokenType.IDENT:
        if significant[-2].text == '.':
            return significant[-1].value, None
        word = significant[-1].value.upper()
        if word not in dialect.reserved_upper and significant[-2].type is not TokenType.PUNCT:
            return significant[-1].value, None

    return None, None


def _read_ctes(
    tokens: Sequence[Token],
    lo: int,
    hi: int,
    dialect: Dialect,
) -> tuple[dict[str, Relation], frozenset[tuple[int, int]]]:
    """
    Every relation declared in a leading WITH clause, keyed by name.

    Also returns each body's token span, because a caret inside one is scoped
    differently from a caret inside any other parenthesised subquery.
    """
    start = _after_clause(tokens, lo, hi, 'WITH', dialect)
    if start is None:
        return {}, frozenset()
    ctes: dict[str, Relation] = {}
    bodies: list[tuple[int, int]] = []
    index = _skip_forward(tokens, start, hi)
    if index < hi and tokens[index].type is TokenType.IDENT and tokens[index].value.upper() == 'RECURSIVE':
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
        ctes[name] = Relation(alias=None, path=(name,), source='cte', projection=projection)
        bodies.append((body_lo, body_hi))
        index = _skip_forward(tokens, body_hi + 1, hi)
        if index < hi and tokens[index].text == ',':
            index += 1
            continue
        break
    return ctes, frozenset(bodies)


def _read_declared_columns(tokens: Sequence[Token], index: int, hi: int) -> tuple[tuple[str, ...], int]:
    """Read an optional `(x, y)` column list following a CTE name."""
    probe = _skip_forward(tokens, index, hi)
    if probe >= hi or tokens[probe].text != '(':
        return (), index
    close = _matching_paren(tokens, probe, hi)
    names = tuple(t.value for t in tokens[probe + 1 : close] if t.type is TokenType.IDENT)
    return names, close + 1


def _matching_paren(tokens: Sequence[Token], index: int, hi: int) -> int:
    """Index of the `)` closing the `(` at `index`, or `hi`."""
    depth = tokens[index].depth
    for probe in range(index + 1, hi):
        token = tokens[probe]
        if token.type is TokenType.PUNCT and token.text == ')' and token.depth == depth:
            return probe
    return hi
