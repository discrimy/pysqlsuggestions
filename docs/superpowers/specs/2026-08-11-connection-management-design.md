# Managing connections from the editor — design

Date: 2026-08-11
Status: **proposed**. Nothing built yet.

The extension can use a connection. It cannot help you make one, and it cannot
tell you whether the one you made works. This adds both.

---

## 1. Context

### What exists

`pysqlsuggestions.connections` is an array in settings; `profiles.ts` parses and
validates it; `secrets.ts` stores a password per profile name; the status bar
shows which profile is bound and flips to a warning when the server sends
`pysqlsuggestions/degraded`.

Everything else is hand-editing JSON.

### What that costs, measured

A connection written into `settings.json` produced **zero completions** — not a
shorter list, nothing at all — because the server connected unauthenticated,
pg8000 raised, and the session degraded to statement-only completion where the
typed prefix matched no keyword either. The fix in `303ce35` made the extension
prompt for a password before starting, which removes that specific trap.

What it does not remove is the shape of the problem: a connection can be wrong
in half a dozen ways — bad host, bad port, wrong database, no password, a
dialect with no bundled driver — and every one of them presents identically, as
completion that quietly stops being schema-aware. The library degrades by
design, so nothing downstream raises. The only defence is being able to ask a
connection whether it works and get an answer.

pg8000's answer to a missing password is:

```
AttributeError: 'NoneType' object has no attribute 'decode'
```

That is the error behind the bug above. Translating it is most of this
feature's value.

### Prior art in this codebase to follow

- **Degradation is defined once and announced.** `resolve.py` implements it,
  `Session.degrade` announces it. Nothing infers health from suggestions.
- **The parsing lives in one place.** `profiles.ts` is the only thing that turns
  settings into a `Profile`, and `Profile.from_options` is the only thing that
  reads one on the server. Both stay that way.
- **Logic is injected, not reached for.** `runtime.ts` takes `run`, `readStamp`
  and `writeStamp` so its decisions are testable without a process. The new
  modules follow it.
- **A status display must not overstate.** The status bar says `bound`, not
  `connected`, because the database is not contacted until the first completion.

### Decisions taken during brainstorming

1. **A tree view in the Explorer**, not a Quick Pick flow and not a webview.
2. **Test connection runs a one-shot subprocess**, so any profile can be tested —
   including one not currently in use — without disturbing the running server.
3. **New connections are written to user settings, always.**
4. **Edits are written back to the scope the connection came from.**
5. **Health is per session and never persisted.**

---

## 2. Scope

### In

A `SQL Connections` view; add, edit and remove; set and clear a password; test a
connection and show the verdict; a `check` entry point in the server package;
tests for all of it.

### Out, deliberately

**Persisting health.** A stored "ok" from last week is a claim nobody verified
today, and this feature exists because a healthy-looking list that is not is the
failure mode.

**Grouping, folders, or ordering.** A list of connections is a list.

**Importing from other extensions.** Coupling to a third party's private shape,
for a six-field object.

**Editing anything but the fields the schema already defines.** The settings
schema in `package.json` is the description of a connection; a second one here
would need keeping in step by hand.

**A password field anywhere but SecretStorage.** Unchanged, and still asserted
by `test_the_settings_schema_has_nowhere_to_put_a_password`.

---

## 3. The view

`contributes.views.explorer` with id `pysqlsuggestions.connections`, plus
`viewsWelcome` so an empty list explains itself rather than showing a blank box.

One row per connection. A `TreeItem` renders on a single line — `label`, then
`description` in dimmed text — so the row is:

```
● docker     postgres · localhost:57432 · in use     ✎ ✕
```

The tooltip carries what will not fit: the database, the user, the scope the
connection came from, and the last verdict in full.

- **Label** is the name; **description** is `dialect · host:port`, with
  `· in use` appended for the profile the running server holds.
- **Icon is health**, and health alone:

| icon | meaning |
| --- | --- |
| `$(circle-outline)` | configured, never tested this session |
| `$(sync~spin)` | being tested, or its server is restarting |
| `$(pass-filled)` | last test succeeded |
| `$(warning)` | last test failed, or the server degraded while using it |

Health and in-use are two different facts and are shown as two different things.
Conflating them is how a status display starts lying: the connection in use may
be the broken one, and that is precisely the case worth seeing.

Inline actions: edit, remove. View title: add, refresh. Context menu: *Use this
connection*, *Set password…*, *Clear password*, *Test connection*.

*Clear password* gives `forgetPassword` its first caller — it has been dead code
since it was written, which the spec for the extension flagged and left open.

---

## 4. Flows

**Add** — name, dialect (a Quick Pick where anything but `postgres` is labelled
*no catalog in this release*), host, port, database, user; then an offer to set a
password, then an offer to test. Written to user settings.

**Edit** — a Quick Pick of fields showing their current values, then one input
box. Not a six-step chain to change a port.

**Remove** — a confirmation naming the connection, then removal. The stored
password goes with it; leaving an orphaned secret behind means a later
connection reusing that name silently inherits it.

**Scope.** VS Code resolves array settings by **override**, not element-wise
merge: a workspace `pysqlsuggestions.connections` replaces the user one
wholesale, and an empty array in a workspace overrides just as firmly as a full
one. So exactly one scope is in effect at a time, and `inspect()` says which.

> **Corrected while planning.** This section first said the scopes *merge* and
> that only *add* was unconditionally user-scoped. Both were wrong, and the
> second followed from the first: adding to user settings while a workspace list
> is in effect writes a connection the extension can never use, because the
> workspace array is what it reads. Add therefore writes to the *effective*
> scope — user in the ordinary case, which is what the decision was protecting,
> and workspace only when a workspace list already overrides. Edits and removals
> were always going to follow the origin scope, and still do.

The view lists the effective scope's connections only. Showing a union would
list connections that will never be used, which is the same class of lie as a
status bar claiming a connection it never verified.

**Restarting.** Editing or removing the connection the server currently holds
restarts it, because one connection per process is the invariant that keeps the
server's state simple. The row spins while that happens, and its health resets
to untested — the thing that was verified is no longer the thing that is
running.

---

## 5. Testing a connection

### The entry point

`lsp/pysqlsuggestions_lsp/check.py`, run as `python -m pysqlsuggestions_lsp.check`.
Reads a profile as JSON on stdin, performs one catalog read, writes one JSON
object to stdout:

```json
{"ok": true, "detail": "12 relations visible"}
```

It reuses `Profile.from_options` and `open_catalog` unchanged, so testing a
profile exercises the code path the server will actually take rather than an
approximation of it. That is the whole reason it lives in the server package
rather than in TypeScript.

**It always exits 0.** The verdict is the JSON. A non-zero exit means the
harness itself broke — a missing module, a broken venv — which is a different
failure and must read differently from "your database refused the connection".

### The caller

`check.ts` spawns it in the managed venv, writes the profile, reads one object,
and kills the process after ten seconds. It never reuses the running server: no
shared cache to poison, no interference with completions, and a connect that
hangs cannot block the process serving keystrokes.

### Verdicts

| situation | detail |
| --- | --- |
| success | `12 relations visible` |
| profile incomplete | `needs a dialect and a host` |
| no bundled driver | `no driver bundled for clickhouse — keywords and quoting still work, schema will not` |
| password wanted, none stored | `the server asked for a password and none is stored` |
| refused, wrong host, wrong database | the driver's own message, trimmed to one line |
| no answer | `no answer in 10s — killed` |
| unparseable output | `the check produced no verdict` |

The password row is a translation, not a pass-through. pg8000 raises
`AttributeError: 'NoneType' object has no attribute 'decode'` in that case, which
tells a user nothing and sent this project's own author debugging in the wrong
direction. `check.py` detects the condition — the profile carries no password
and the driver failed inside authentication — and says so.

---

## 6. Components

| file | responsibility | depends on |
| --- | --- | --- |
| `src/store.ts` | CRUD over settings: merged read with origin scope, add, update, remove | an injected settings accessor |
| `src/tree.ts` | `TreeDataProvider`, with row rendering split into a pure `rowFor` | `store`, `vscode` |
| `src/check.ts` | spawn, feed, parse, time out | an injected spawn |
| `lsp/pysqlsuggestions_lsp/check.py` | one profile, one verdict | `connections.py` |

`profiles.ts` is unchanged. It remains the only thing that turns settings into a
`Profile`, and `store.ts` uses it rather than parsing again.

Every module that decides something takes its outside world as a parameter, so
the decisions are testable without an editor, a process, or a database — the
pattern `runtime.ts` already follows.

---

## 7. Error handling

**Test always produces a verdict.** Never an exception, never a silent nothing.
Malformed output becomes `the check produced no verdict` with the raw bytes in
the output channel.

| failure | response |
| --- | --- |
| venv not ready | Test says so and does not spawn — there is nothing to spawn |
| spawn fails | the verdict carries the spawn error; the channel carries the detail |
| check times out | the process is killed and the row goes to warning |
| settings write refused | the error is surfaced and the tree refreshes from what is actually stored |

A refused settings write matters more than it looks: the tree must never show a
connection that was not saved. It refreshes from settings rather than from what
it hoped it wrote.

---

## 8. Testing

**Python, no database:** `check()` against fakes — an invalid profile, an unknown
dialect, a catalog that raises inside authentication, a catalog that answers.

**Python, with docker:** a real success, and a real authentication failure with
no password, which is the translation path and the one worth proving.

**TypeScript, no editor:** `store.ts` CRUD against an injected fake settings
object, including that an edit to a workspace-scoped connection writes back to
workspace and not to user; `rowFor()` as a pure function over
`(connection, health, active)`; `check.ts` verdict parsing, timeout, and
unparseable output.

**VS Code integration:** add a connection through the command, assert it appears
in the tree; test it and get a pass; point one at a dead port and assert the row
goes to warning carrying a reason.

---

## 9. Open questions carried forward

1. **Authentication failures during use.** `check` can distinguish them on
   demand, but the running server still catches every exception alike and
   degrades. Re-prompting for a password after a rejection still needs the
   server to say *why* it degraded in a machine-readable way; `degraded` carries
   a string, not a cause.
2. **Health after a successful completion.** Nothing currently reports that the
   catalog *worked*, only that it stopped working. A row tested green stays
   green even if the next completion degrades, until the `degraded` notification
   arrives — which it does, so the gap is small but real.
3. **Trino and ClickHouse.** Testing a connection for either will always answer
   "no driver bundled". Honest, and unsatisfying for anyone who wanted one.
