# pysqlsuggestions-lsp

A language server over `pysqlsuggestions`. The library stays free of it: this is
an adapter beside `demo/`, not a layer inside `src/`, because a server needs
pygls and a driver and `test_import_pulls_in_no_drivers` exists to keep both out
of `import pysqlsuggestions`.

```bash
uv run python -m pysqlsuggestions_lsp
```

Speaks LSP on stdio. The connection profile arrives in `initializationOptions`:

```json
{
  "dialect": "postgres",
  "host": "localhost",
  "port": 5432,
  "database": "app",
  "user": "ana",
  "password": "…"
}
```

Without one it completes from the statement alone — keywords, CTE columns,
select-list names, aliases — which is a useful degraded mode rather than an
error. Every field is checked rather than trusted; a profile missing a dialect
or a host is no profile, and that is the same degraded mode again.

## What it guarantees

**A completion request never fails.** An unreachable database, a rejected
password, a dialect with no driver here — each falls back to completing from the
statement, because an error popup arriving on a keystroke is worse than a
shorter list. A failure is recorded rather than retried, since retrying would
mean a blocking connection attempt for every character typed.

**The database is not contacted until the first completion.** `DbapiCatalog`
opens a cursor per query, so opening a document opens no socket. A database
behind a VPN that happens to be down costs a completion, not a hung editor.

**The engine's ranking survives.** Every item carries `sortText`, because a
client re-sorts by its own fuzzy score otherwise and the ranking is the product.
Every item carries a `textEdit` with an explicit range rather than an
`insertText`, because re-deriving a word boundary is what drops a qualifier —
`where u.crea` accepting `created_at` must give `where u.created_at`.

**A restricted suggestion is as honest as the protocol allows, and no more.** A
column the connected role may not read carries `tags: [Deprecated]`, which
renders as strikethrough, and its reason in `detail` beside whatever else that
field is carrying. It having sunk to the bottom of the list needs nothing here:
`sortText` already carries the engine's order and the engine has already put it
last.

What cannot be done is stop the client inserting it. LSP has no disabled state,
and the obvious workaround — an empty `textEdit` plus a command — is refused: it
produces an item that silently does nothing, which reads as a bug in this server
rather than as a grant the user lacks. A front end that controls its own accept
handler, as prompt_toolkit and Monaco do, can block insertion outright; this one
tells the truth and lets the server refuse the query, which it would have done
regardless.

## Scope and statements

A `.sql` file holds many statements and the engine builds scope from the whole
statement, so the document is cut at semicolon *tokens* — not at the character,
which occurs inside literals, comments and quoted identifiers. The dialect's own
lexer decides, so this is right per backend rather than approximately right
everywhere.

## Connections

One per process. Changing the profile means restarting the server, which
discards a warm schema cache only on an action the user took deliberately and
rarely, and removes every bug where a server holds state from a connection it no
longer has.

Drivers are pure Python by design — `pg8000` for Postgres, `trino` for Trino —
so the wheels are platform-independent and one VSIX can serve every platform.
ClickHouse is a dialect this library serves and this server does not: its driver
is not pure Python. It resolves as a dialect, so its keywords and quoting are
still right; only the catalog is absent.
