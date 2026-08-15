# The request pipeline

Three pure stages turn text into a `Request`. None of them performs I/O, so all
of them are testable with no database and no mocks.

## Lex

`engine/lex.py` scans the source once, driven entirely by the dialect's `Syntax`
record. It never raises: an unterminated string, quoted identifier or comment
produces a token running to end of input with `terminated=False`, because
completion works on invalid input by definition.

Two properties matter downstream. Each token carries its paren `depth`,
precomputed during the scan, which turns every "nearest keyword at depth 0"
question into a filter. And the lexer does not classify keywords — every word is
an `IDENT`, and analyse consults the dialect's vocabulary. That keeps the lexer
dependent on dialect *syntax* only, never dialect *vocabulary*.

`terminated` is load-bearing beyond tolerance. A caret at the closing quote of a
finished literal is *outside* it — `'ab'⌶` is back in ordinary SQL — while a
caret at the end of an unterminated one is inside, since there is no delimiter
to have passed. A line comment reaching end of input is therefore unterminated,
which is what makes `-- note ⌶` suppress suggestions.

## Analyse

`engine/analyse.py` is four pure functions over the token stream:

- `statement_at` isolates the statement containing the caret, splitting on every
  semicolon *token*. A `;` inside a string, a comment, a quoted identifier or a
  dollar-quoted function body never reaches it as punctuation, because the lexer
  swallowed it — which is the whole of what "a semicolon inside a literal does
  not divide" means, and it costs no rule here to get.

  It used to also require depth 0, which read as the same guarantee for
  parentheses and was not one: no dialect here admits a bare `;` between parens,
  so a semicolon token at depth greater than zero is always a paren the author
  has not closed yet. Declining to split there merged the two statements and put
  the earlier one's relations into the later one's scope.
- `qualifier_and_prefix` reads the dotted path and half-typed word to its left,
  tolerating whitespace around the dots. `replace_span` always ends at the
  caret, so accepting a suggestion replaces what was typed and nothing more.
- `clause_at` scans back to the nearest clause keyword at the caret's depth, so
  a subquery that closed before the caret does not capture it. When the caret's
  own depth holds no clause keyword — `WHERE (a AND ⌶)`, `SELECT sum(⌶` — the
  search widens outward. Matches rank by (end offset, word count), which is why
  `DELETE FROM ⌶` answers `DELETE FROM` rather than the `FROM` ending at the
  same token.
- `scope_of` walks the whole statement, recursing into CTE bodies and returning
  the innermost scope with `parent` links outward.

A reference still being typed is not a relation. That covers the obvious
`FROM us⌶` and the less obvious `FROM analytics.⌶`, where a dangling dot means
no identifier has followed it yet — without that, `analytics` would register as
a relation and the alias-first rule would wrongly collapse the answer to
columns.

## Request

`engine/request.py` narrows. A qualifier matching a relation in scope collapses
the answer to columns; otherwise it is read as a namespace level, and one tuple
(`Namespace.levels`) gives three different answers to `analytics.` across the
three backends. A qualifier deeper than the namespace has nowhere left to go but
a column.

Resolution order is **alias first, then namespace**.

## Projections

`Relation.projection` has three states, because two cannot describe real SQL:

- `None` — the relation lives in the catalog; ask it.
- `Projection(columns=..., stars=())` — fully self-described; no catalog call.
- `Projection(columns=..., stars=(...))` — partly self-described.

The third exists for `WITH a AS (SELECT * FROM users) SELECT a.⌶`. The CTE's
output columns are whatever `users` has, which no amount of text analysis can
determine; recording the star's source relation lets resolve finish the job
without the analyser guessing or the resolver looking for a table named `a`.
