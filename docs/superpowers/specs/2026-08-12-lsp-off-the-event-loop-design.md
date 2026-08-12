# The editor stops waiting on the database — design

First of three slices clearing the recorded debts. The other two — `WITH`
answering nothing, and relation-kind filtering finer than one notch — are
independent of this and of each other, and get their own specs.

A completion handler that reads a database runs inline on the language server's
event loop, so a slow introspection query stops the server answering anything
at all.

---

## 1. Context

### What pygls actually does

Read from `pygls/protocol/json_rpc.py` at 2.1.1, not assumed:

```python
if asyncio.iscoroutinefunction(handler):
    ...                                    # scheduled on the event loop
elif is_thread_function(handler):
    future = self._server.thread_pool.submit(handler, *args, **kwargs)
else:
    ...                                    # called inline, on the loop
```

`create_server`'s `completion` is a plain function, so it takes the third
branch. While it waits on a socket, the loop is not running: `didChange`,
`shutdown`, and the client's own `$/cancelRequest` for the stuck request all
wait behind it.

`LanguageServer.thread()` exists and is a no-argument decorator. The pool is a
`ThreadPoolExecutor(max_workers=None)`, so `min(32, os.cpu_count() + 4)`
workers — concurrency is real, not theoretical.

### What this was recorded as

`docs/gaps.md` calls it "Async. Every catalog call is synchronous. An LSP server
that blocks its event loop on a slow introspection query is a real cost now that
`lsp/` exists."

**That entry points at the wrong fix.** Nothing here needs to become
asynchronous. The synchronous `Catalog` port is a deliberate decision with a
documented bridge for async callers — pre-fetch into a `MemoryCatalog` — and it
stays. What was missing is one decorator, plus the thread safety that decorator
makes necessary.

### What the drivers say

Measured, not assumed. All three report DB-API `threadsafety=2` — "threads may
share the module and connections", but not cursors:

| driver | threadsafety | paramstyle |
|---|---|---|
| psycopg2 | 2 | pyformat |
| clickhouse_driver.dbapi | 2 | pyformat |
| trino.dbapi | 2 | qmark |

`open_catalog`'s `open_cursor` already takes a fresh cursor per query, so
sharing the connection is within contract.

### Decisions taken during brainstorming

1. **Three separate slices**, in value order, this one first: it is the worst
   symptom — a frozen editor — and, now that the fix is a decorator rather than
   an async rewrite, the cheapest of the three.
2. **Serialise the catalog read**, even though DB-API permits concurrency. See
   §4.

### Rejected approaches

- **An async `Catalog` protocol.** What the gaps entry implied. Large, and it
  would undo a documented decision to buy nothing the thread pool does not.
- **Answering from the statement alone while the lock is busy.** Tempting — the
  degraded path already exists and is well tested — but it would make the same
  caret sometimes schema-aware and sometimes not, which reads as flakiness
  rather than as speed.
- **A connection pool.** One connection per process is a stated decision in
  `server.py`'s module docstring.

---

## 2. Scope

### In

- `@server.thread()` on the completion handler.
- A lock making `Session` safe for concurrent calls, covering the catalog read.
- A lock making `open_catalog`'s lazy connection safe.
- Tests for both, which fail today.

### Out, deliberately

- **`$/cancelRequest`.** A stale completion still runs to completion. Real, and
  its own thing — it needs the client's cancellation and pygls's future
  plumbing.
- **The `INITIALIZE` handler.** It touches no database; `Profile.from_options`
  is pure. Marking it would add a thread hop for nothing.
- **`check.py`.** A one-shot diagnostic command, not a server handler.
- **Async anywhere.**

---

## 3. What the decorator exposes

Three check-then-set races. Each is unreachable today because nothing is
concurrent, and each becomes reachable the moment two completions overlap.

**`Session.catalog()`**

```python
if self._tried or self.profile is None:
    return self._catalog
self._tried = True
```

Two threads both read `_tried` as False. Both call `open_catalog`. One
`_catalog` overwrites the other, and the connection inside the loser is
discarded without being closed.

**`Session.degrade()`**

```python
if self.on_degrade is not None and not self._announced:
    self._announced = True
```

Two threads both see `_announced` False, and the client is told twice that the
catalog has died. The notification's whole point is that it is said once — its
docstring says so.

**`open_catalog`'s `open_cursor`**

```python
def open_cursor() -> Cursor:
    if not held:
        held.append(opener(profile))
```

Two threads both find `held` empty. Two connections open; only the first
element of `held` is ever used again, so the second is leaked — never closed,
and still holding a session on the server.

**The cache is left alone.** It is a plain dict, and `get`/`__setitem__` are
atomic under the GIL: the worst case is two threads missing the same key and
both fetching, which is a duplicate read rather than corruption. The documented
`Cache` protocol admits any object with those two methods, so requiring
something stricter would constrain every caller to fix a duplicate query.

---

## 4. One lock, and how wide

A `threading.Lock` on `Session`, and a second inside `open_catalog`'s closure —
two locks because they guard two objects with different lifetimes, and because
`open_catalog` has no `Session` to reach.

`Session`'s lock is held across `catalog()`, `degrade()`, **and the `complete()`
call that reads through the catalog**. That last part is wider than correctness
strictly demands, and deliberately:

- The concurrency it gives up is worth almost nothing here. Completions are
  latest-wins — a user typing fast produces requests whose earlier answers are
  already stale — and after the first read the cache makes the rest instant.
- The concurrency it gives up costs a dependency on three third-party drivers
  honouring `threadsafety=2` under concurrent cursors on one connection. That
  is their contract to keep, and this is a cheap way not to need it kept.

The event loop is free either way, which is the entire point. A waiting
completion waits in a pool thread, where waiting is what pool threads are for.

**Precisely where it is released**, because `suggest`'s current shape puts the
statement-only fallback inside the `except` and a naive `with` around the whole
body would hold the lock through it. The rule: the lock covers the lazy build,
the catalog read, and the `degrade` bookkeeping — and is released before the
fallback re-completes. A read that fails must not hold the lock while answering
without one.

A session that has already degraded still takes the lock, briefly, to ask for a
catalog that is now None. That is a few microseconds against an uncontended
lock, and buying it back would mean reading `_catalog` outside the lock — a
second unsynchronised read to save nothing measurable.

---

## 5. Testing

### The wiring

Asserted through pygls's own `is_thread_function`, not by looking for our
decorator — the predicate the dispatcher actually branches on is the one worth
asserting. Registered handlers are reachable from `server.protocol.fm.features`.

### The races

Three tests, each failing today, each driving N threads through `Session`
against a catalog whose reads sleep long enough to overlap:

1. **One connection.** A `connect` that counts calls; assert it was called once.
2. **One notification.** An `on_degrade` that appends; assert one entry.
3. **Every caller still gets an answer.** N threads, N non-empty results — the
   guard that serialising did not turn a slow answer into no answer.

The first two fail today with *more than one*, not reliably with N: whether a
given thread loses the race depends on where it is when another sets the flag.
An assertion on the exact losing count would itself be flaky, so each asserts
the number it must be after the fix, and the plan verifies each one fails before
the lock lands rather than predicting by how much.

`Session` is deliberately free of pygls, which is what lets all three run
without a client handshake. That property was designed in for testability and
this is the slice that collects on it.

### Regression

`tests/lsp/test_server.py` is 175 lines and every one of them exercises
`Session` directly. It is the regression suite for the lock, and must pass
unchanged.

### Not tested

That the event loop is genuinely free during a slow read. Proving it needs a
client, a real socket and a timing assertion — the kind of test that fails on a
loaded CI machine for reasons unrelated to the code. The dispatch branch is read
from pygls's source in §1 and the handler's membership in that branch is
asserted; that is the honest limit of what a unit test can say here.

---

## 6. Documentation

- `docs/gaps.md`: the **Async** bullet in "Already named elsewhere" is rewritten
  rather than deleted. It named the wrong fix, and a list that silently drops an
  entry teaches a later reader nothing. What remains true is that every catalog
  call is synchronous; what is no longer true is that this blocks the server.
- `CHANGELOG.md`: an entry under Unreleased. This is user-visible — an editor
  that stops responding is the most visible thing software does.
- `lsp/pysqlsuggestions_lsp/server.py`'s module docstring gains the rule the
  lock enforces, beside the "one connection per process" rule already there.

---

## 7. Open questions carried forward

- **`$/cancelRequest`.** A stale completion still runs. Worth doing if the pool
  is ever seen to fill.
- **The other two debts**, each with its own spec: `WITH` answering nothing, and
  relation kinds finer than "not a sequence".
