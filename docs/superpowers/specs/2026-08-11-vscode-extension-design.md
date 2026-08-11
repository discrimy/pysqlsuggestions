# A VS Code extension for SQL completion — design

Date: 2026-08-11
Status: **proposed**. Nothing built yet.

The engine completes SQL better than anything in the VS Code marketplace and is
reachable today only from Python. This puts it in an editor.

---

## 1. Context

### What exists, and what it costs to reach

`complete(sql, caret, dialect, catalog)` runs the whole pipeline; `DbapiCatalog`
turns any PEP 249 cursor into a catalog; the dialect registry resolves a name to
a `Dialect` through entry points. Postgres additionally answers foreign keys,
column search and most-common-values, which is where join proposals, the
`SELECT ema⌶` → `SELECT auth_user.email FROM auth_user` insertion, and value
hints come from.

All of it is Python. VS Code runs TypeScript. Everything below is about carrying
one function call across that boundary without losing anything on the way.

### The three claims this repo makes that constrain the answer

- **"Zero runtime dependencies."** `test_import_pulls_in_no_drivers` enforces it
  by running a subprocess and asserting no driver reached `sys.modules`. A
  language server needs pygls and a driver, so it cannot live in
  `src/pysqlsuggestions/`.
- **"A library, not a CLI and not a language server."** The README says this in
  its second sentence. It is a claim about what the *library* forces on a caller
  — no process boundary — not a prohibition on adapters. `demo/` is already an
  adapter living beside the library rather than inside it, and this follows that
  precedent exactly.
- **"The page reaches nothing."** The browser demo vendors Pyodide rather than
  fetching it, pinned by digest, and the build refuses to publish a page naming
  an absolute URL. An extension that pip-installs from PyPI on first run would be
  the same project taking the opposite position on the same question.

### Prior art in this codebase to follow

- **A build that rewrites artifacts and fails when it cannot.**
  `scripts/build_pages.py` substitutes the wheel filename and returns non-zero if
  it finds no placeholder. Assembling a VSIX from locally-built wheels is the
  same shape of problem.
- **Vendored dependencies pinned by digest.** `scripts/pyodide.lock` and
  `test_vendor_pyodide.py`. Bundled wheels get the same treatment.
- **Structural guards as tests.** `test_purity.py` asserts a property no
  functional test would notice breaking.
- **Degradation defined at the port, once.** Every capability protocol documents
  what happens when it is absent, and `resolve.py` implements it so no adapter
  repeats it. The extension inherits this rather than inventing error states.

### Decisions taken during brainstorming

1. **Completion only.** Not a SQL client. Query execution, a results grid and a
   schema tree are separate subsystems that would make the completion engine a
   minor feature of its own extension.
2. **A Python language server over LSP**, not Pyodide in the extension host and
   not a TypeScript port. Reasoning in §3.
3. **Connection profiles in settings, passwords in SecretStorage.**
4. **A managed venv built from wheels bundled in the VSIX.** No network on first
   run.
5. **Both halves in this repo**, so the VSIX bundles wheels built from the tree
   that produced it and version skew is structurally impossible.
6. **`.sql` files only.**

---

## 2. Scope

### In

A `lsp/` package running a pygls server over stdio; an `editors/vscode/`
TypeScript extension that provisions a venv, resolves a connection profile and
starts the client; a build step assembling wheels into the VSIX; unit tests for
the pure Python modules and the TypeScript logic; one end-to-end integration test
against the existing docker Postgres.

### Out, deliberately

**Query execution, results, schema tree.** Each is additive; none requires
revisiting anything here.

**Embedded SQL in Python strings or TS template literals.** The engine would
handle it — offsets map through — but deciding *which* string literals are SQL
is a heuristic problem with no good answer, and getting it wrong means
completions appearing inside unrelated strings. Worth doing later, on evidence,
not on speculation.

**Notebook cells.** Separate URI scheme and lifecycle, plus stripping the `%%sql`
magic before the engine sees the text.

**ClickHouse in the bundled venv.** `clickhouse-driver` is not pure Python; see
§4. ClickHouse remains fully supported for library users, and arrives in the
extension when either a pure-Python driver or platform-targeted VSIXs are worth
the cost.

**Multiple simultaneous connections per window.** One profile per server process.

---

## 3. Why a language server, and not the alternatives

The decision is not "how do we call Python" — it is **who owns the database
connection**, because that decides whether the existing catalog code is reused or
rewritten.

**Pyodide in the extension host** was the tempting one: the runtime is already
vendored and digest-pinned, and it needs no Python on the user's machine.
Pyodide cannot open TCP sockets, so TypeScript would have to run every catalog
query itself. That is not a small adapter — it is `DbapiCatalog`, the
dialect-specific SQL for foreign keys and `pg_stats`, capability detection and
the caching contract, rewritten in a second language and maintained in parallel.
Worse, `Catalog` is synchronous by protocol while every Node driver is async, so
the only way to bridge is prefetching a whole schema into a `MemoryCatalog` — and
`search_columns` exists precisely because some schemas are too large to
enumerate, while `common_values` is per-column on demand. The two features that
most need the boundary are the two it cannot cross.

**A hand-rolled JSON-RPC sidecar** re-specifies incremental document sync,
cancellation and completion resolve, and produces a server no other editor can
use.

**Porting the engine to TypeScript** doubles the cost of every future change and
makes this repo no longer the source of truth.

LSP wins on a detail that is easy to miss: it has a message shape for everything
the engine already produces. `plan_insertion` returns *two* edits when a column
needs a FROM clause — LSP has `additionalTextEdits`. Join proposals carry
template `stops` — LSP has snippet placeholders. `replace_span` travels with the
suggestion precisely so an editor does not re-derive a word boundary and drop the
qualifier — LSP has `textEdit` with an explicit range. Nothing has to be
flattened or dropped in translation.

The price is a Python interpreter on the user's machine. §4 is about paying it.

---

## 4. The managed venv, and the pure-Python driver

On first activation the extension creates a venv under `globalStorageUri` and
installs wheels shipped inside the VSIX. No network, no user steps, and the
workspace's own environment is untouched.

`python3` must exist on PATH. When it does not, the extension says so once, with
an actionable message, and goes dormant. It does not nag on every `.sql` file.

### Why pg8000 rather than psycopg2

`psycopg2-binary` ships compiled wheels. Bundling it means one VSIX per platform
and architecture — six builds, six pipelines, six things to get wrong at release
— because the venv's contents would no longer be portable.

`pg8000` is a pure-Python PEP 249 driver, and `DbapiCatalog` accepts any PEP 249
cursor. With `pg8000` and `trino` — also pure — every bundled wheel is
platform-independent and **one universal VSIX serves every machine**.

pyproject's own comment anticipated this:

> Extras are named after the driver they install, not the backend: more than one
> driver can serve the same backend, so a future psycopg3 extra sits alongside
> psycopg2 rather than silently changing what `postgres` means.

So this adds a `pg8000` extra beside the existing ones. It changes nothing for
library users, for whom psycopg2 remains the documented choice; it governs only
what the extension bundles.

The cost is honest: a second Postgres driver in the integration matrix.
`DbapiCatalog` takes `paramstyle` as a constructor argument for exactly this
reason, so the surface where they can differ is small and already parameterised
— but "small" is not "zero", and pg8000 gets its own integration coverage
against the docker Postgres rather than an assumption that PEP 249 makes drivers
interchangeable.

### Wheels are pinned, not floated

Bundled wheels are recorded with their digests and a test asserts the bundle
matches what is declared, following `test_vendor_pyodide.py`. A VSIX assembled
from whatever pip resolved that morning is not a reproducible artifact.

---

## 5. The server

`lsp/`, a top-level package beside `demo/`, published as `pysqlsuggestions-lsp`.
Five modules, three of them pure:

| module | does | depends on |
| --- | --- | --- |
| `__main__.py` | starts the server on stdio | pygls |
| `server.py` | LSP handlers: initialize, document sync, completion | pygls |
| `documents.py` | the statement containing the caret, and its base offset | `engine.lex` |
| `convert.py` | `Suggestion` + `plan_insertion` → `CompletionItem` | pysqlsuggestions types |
| `connections.py` | profile → dialect → driver → `DbapiCatalog`, owns the cache | drivers |

`documents.py` and `convert.py` are where the behaviour that can be wrong lives,
and neither needs a server or a database to test.

### Statement splitting is not optional

`derive_request` builds scope from the whole statement — the README is explicit
that the FROM clause answering a caret in the SELECT list sits to the *right* of
it. A `.sql` file holds many statements, so handing the engine the whole document
would put every relation in every statement into scope for all of them.

A semicolon inside a string literal, a quoted identifier or a comment does not
end a statement — and which delimiters those are is a property of the dialect.
That is `engine.lex`'s job and it already does it, so `documents.py` splits on
semicolon *tokens* from `lex(text, dialect.syntax)` rather than on the character.

This is the one place the server reaches into the engine's internals rather than
its API, and it is worth it: a hand-rolled splitter would be a second, untested,
dialect-unaware lexer whose disagreements with the real one appear as scope
silently missing from a completion. `lex` is exercised by `test_lex_core.py`,
`test_lex_literals.py` and `test_dialect_lexing.py` already.

`documents.py` returns the statement containing the caret and its base offset;
every span coming back from the engine is translated by that offset before
becoming a `Range`.

### One connection per process

The profile arrives in `initializationOptions`. Changing it restarts the server.
Restarting is cheap, it discards a warm schema cache only on an action the user
took deliberately and rarely, and it removes the entire class of bugs where a
server holds state from a connection it no longer has.

### The connection is opened lazily

Not at startup — on the first completion request. A database behind a VPN that
happens to be down must not block activation or hang the editor on file open.

### Ranking must survive the trip

VS Code re-sorts and re-filters completion items with its own fuzzy scorer by
default. The engine's ranking *is* the product: many-to-one joins above
one-to-many, values by frequency, exact matches above near ones. Left alone, VS
Code discards all of it.

So `convert.py` sets `sortText` to a zero-padded index of the engine's order, and
`filterText` to the term the engine matched against — the column name, not the
qualified text, so `usern` still finds `u.username`. This is the single easiest
thing in the whole design to omit and the hardest to notice missing: the list
still appears, still contains the right items, and is silently in the wrong
order.

Mapping the rest:

```
Kind.COLUMN   → Field        detail: type
Kind.TABLE    → Class        detail: schema
Kind.FUNCTION → Function     the parens plan_insertion added ride in the textEdit
Kind.KEYWORD  → Keyword
Kind.SCHEMA   → Module
Kind.VALUE    → Value        detail: share of rows, where statistics gave one
```

Every item carries a `textEdit` and never an `insertText` — LSP honours one or
the other, and the whole reason `replace_span` travels with the suggestion is to
state a range rather than let the editor guess a word boundary. `plan_insertion`
is called once per item, and its output is the sole source of the text, the
range and the additional edits.

`expects_more` attaches `editor.action.triggerSuggest`, so accepting a schema, or
a function that takes arguments, opens the next list without a keystroke.

---

## 6. The extension

`editors/vscode/`, TypeScript, activating on the `sql` language id.

| module | does |
| --- | --- |
| `runtime.ts` | locate python3, create the venv, install bundled wheels, report progress |
| `profiles.ts` | read settings, resolve the profile for a document, the add-connection wizard |
| `secrets.ts` | SecretStorage reads, writes and prompts |
| `status.ts` | status bar: which profile, and whether the catalog is reachable |
| `extension.ts` | wire the above, start and restart the LanguageClient |

### Profiles

Named profiles in settings — host, port, database, user, dialect — with a
default and an optional per-workspace-folder binding. **No password field
exists**, so there is no configuration in which a password can be committed to a
repository by accident. It lives in SecretStorage, prompted for on first connect.

### What the status bar is for

A completion list is schema-aware or it is not, and the difference is invisible
in the list itself — a degraded list still contains keywords and aliases and
looks entirely healthy. The status bar carries which profile is bound and whether
its catalog answered, because that is the only place the distinction can be seen.

---

## 7. Error handling

The governing rule: **a completion request never fails.** The library degrades by
design and `resolve.py` already implements the degradation, so every failure here
falls back to catalog-free completion — keywords, CTE columns, select-list names,
aliases — rather than an error popup arriving mid-keystroke.

| failure | response |
| --- | --- |
| no `python3` on PATH | one actionable notification, then dormant |
| venv creation or install fails | pip output to a channel, then dormant |
| database unreachable | catalog-free mode; status bar shows it; one notification offering Retry and Edit profile |
| authentication rejected | clear the stored secret, re-prompt once, then degrade |
| server process crashes | the client restarts it; after repeated crashes, stop and report |

The pattern in every row is the same: fail once, loudly, in a place the user can
act on — never repeatedly, and never in the completion list.

---

## 8. Testing

**Python, no server and no database:** `documents.py` against statements
containing semicolons inside literals, quoted identifiers and comments;
`convert.py` against each `Kind`, the two-edit FROM insertion, snippet stops, and
`sortText` ordering; `connections.py` for dialect resolution and driver
selection.

**Python, with a server:** handlers through pygls' test client.

**Python, with a database:** one end-to-end test — open a document, request a
completion, assert a join proposal arrives with its condition — against the
docker Postgres already in `docker/docker-compose.yml`. Plus pg8000's own
`DbapiCatalog` coverage, mirroring the existing psycopg2 tests.

**TypeScript:** unit tests for profile resolution and venv pathing; one
`@vscode/test-electron` test that opens a `.sql` file and asserts items arrive.

**Structural guards**, in the spirit of `test_purity.py`:

- The purity test's scope stays the *library*. `lsp/` may import drivers;
  `src/pysqlsuggestions/` still may not. Widening the guard to cover `lsp/` would
  make it fail on correct code; narrowing it silently would lose the property it
  exists to protect. It is made explicit rather than left to whichever way the
  path check happens to fall.
- The bundled wheel set matches what is declared, by digest.

---

## 9. Open questions carried forward

1. **Cache invalidation.** Catalog reads are cached and prefix-independent by
   design, so a migration run while the editor is open leaves the completion list
   describing a schema that no longer exists. A refresh command is the small
   answer; a TTL is the automatic one. Neither is in this spec.
2. **ClickHouse.** Blocked on a pure-Python driver or on accepting
   platform-targeted VSIXs. Worth revisiting once there is evidence anyone wants
   it in an editor.
3. **Reusing the server elsewhere.** It speaks LSP, so Neovim and JetBrains cost
   only a client each. Not built, but not designed against either.
4. **`python3` absent.** The one failure mode with no graceful answer under this
   design. If it turns out to be common rather than rare, bundling a standalone
   interpreter is the fallback — at roughly 30–50 MiB per platform, which is the
   trade this design declines to make up front.
