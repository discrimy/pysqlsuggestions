# The Editor Stops Waiting on the Database — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A slow introspection query stops blocking the language server's event
loop, and the concurrency that allows is made safe.

**Architecture:** One decorator moves the completion handler into pygls's thread
pool. One reentrant lock on `Session` serialises the lazy catalog build, the
read, and the degrade bookkeeping; one lock inside `open_catalog`'s closure
makes its lazy connection safe for any caller. No async, and no change to the
`Catalog` port.

**Tech Stack:** Python 3.10+, pygls 2.1.1, `threading` from the standard
library. `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy`.

## Global Constraints

- **Python 3.10 floor.** No `match`, no `X | Y` in `isinstance`.
- **Line length 120, single quotes.** `ruff format` decides.
- **Docstrings required** (`ruff` rule set `D`) on every public module, class,
  function and method — including the test helpers added here, which the
  existing ones all have.
- **The library takes no new dependencies.** `lsp/` is a separate distribution
  and this adds nothing to it either; `threading` is standard library.
- **A completion request never fails.** The rule in `server.py`'s module
  docstring. Nothing in this plan may introduce a path that raises to the
  client.
- **`Session` stays free of pygls.** It is what lets its tests run without a
  client handshake, and this plan collects on that property rather than
  spending it.
- **Every task ends green.** `uv run pytest`, `ruff check`,
  `ruff format --check` and `mypy` all clean before the commit.

---

## File Structure

| file | change |
|---|---|
| `lsp/pysqlsuggestions_lsp/server.py` | `Session._lock`; `suggest` split so the lock's scope is explicit; `@server.thread()`; module docstring |
| `lsp/pysqlsuggestions_lsp/connections.py` | a lock inside `open_catalog`'s `open_cursor` |
| `tests/lsp/test_server.py` | a concurrency helper and three tests |
| `tests/lsp/test_connections.py` | one test, reusing the existing `FakeConnection` |
| `docs/gaps.md`, `CHANGELOG.md` | the corrected entry and the release note |

**Task order is deliberate.** The lock lands before the decorator, so no commit
in this branch's history has concurrency enabled without the safety for it. And
Task 1 lands before Task 2, so Task 1's "one connection" test genuinely fails
first — Task 2's lock would otherwise fix it from underneath and Task 1 would
prove nothing.

---

## Task 1: one caret at a time reaches the catalog

**Files:**
- Modify: `lsp/pysqlsuggestions_lsp/server.py` (`Session`)
- Test: `tests/lsp/test_server.py`

**Interfaces:**
- Produces: `Session._lock: threading.RLock`, and
  `Session._from_catalog(statement: str, within: int, dialect: Dialect) -> list[Suggestion] | None`
  — None meaning "complete without a catalog".

**Background.** `Session` has three pieces of mutable state and no lock:
`_catalog`, `_tried`, `_announced`. Each is read-then-written, so two concurrent
completions can both take the branch. Today nothing is concurrent; Task 3 makes
it so, which is why this lands first.

The lock is **reentrant** because `catalog()` and `degrade()` are public and are
also called from inside the locked region. mypy accepts `threading.RLock` as an
annotation — verified against this project's strict config — so no `Any` is
needed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/lsp/test_server.py`:

```python
def slow_refusal(attempts: list[Profile]) -> Callable[[Profile], Any]:
    """
    A database that takes its time and then refuses, recording each attempt.

    The delay is what makes the race reachable: without it the threads arrive
    one after another and the check-then-set windows never overlap.
    """

    def connect(profile: Profile) -> Any:
        attempts.append(profile)
        time.sleep(0.05)
        message = 'connection refused'
        raise OSError(message)

    return connect


def concurrently(session: Session, workers: int = 8) -> list[list[str]]:
    """
    Drive `workers` completions through one session, released together.

    A barrier rather than staggered starts: the point is that they overlap, and
    a test that only sometimes overlaps only sometimes tests anything.
    """
    ready = threading.Barrier(workers)
    guard = threading.Lock()
    found: list[list[str]] = []

    def run() -> None:
        ready.wait()
        answer = labels(session, WITH_CTE)
        with guard:
            found.append(answer)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return found


def refusing_session(attempts: list[Profile], told: list[str]) -> Session:
    """A session pointed at a database that will refuse, with both counters wired."""
    return Session(
        profile=Profile(dialect='postgres', host='nowhere'),
        connect=slow_refusal(attempts),
        on_degrade=told.append,
    )


def test_one_connection_is_opened_however_many_carets_arrive_at_once() -> None:
    """
    Two threads both finding the connection unopened both open one, and only
    one is kept — the other is leaked, still holding a session on the server.
    """
    attempts: list[Profile] = []
    concurrently(refusing_session(attempts, []))
    assert len(attempts) == 1


def test_degrading_is_announced_once_however_many_carets_arrive_at_once() -> None:
    """
    The notification is a state change, not a running commentary — its own
    docstring says so, and `_announced` is read then written.
    """
    told: list[str] = []
    concurrently(refusing_session([], told))
    assert len(told) == 1


def test_every_concurrent_caret_still_gets_an_answer() -> None:
    """
    The guard on serialising: waiting for the lock must not turn a slow answer
    into no answer. Every one of them still finds the CTE the statement names.
    """
    found = concurrently(refusing_session([], []))
    assert len(found) == 8  # noqa: PLR2004
    assert all('recent' in answer for answer in found)
```

and extend that file's imports:

```python
import threading
import time
from collections.abc import Callable
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/lsp/test_server.py -k at_once -v`
Expected: both `at_once` tests FAIL, with counts greater than 1. The exact
number varies by scheduling — that is why the assertions name the number they
must be rather than the number they currently are.

If either *passes* on this run, the threads did not overlap: raise `workers` to
16 and re-run before concluding anything. A green run here would mean the rest
of the task is unverified.

- [ ] **Step 3: Give `Session` a lock**

In `lsp/pysqlsuggestions_lsp/server.py`, add `import threading` to the imports,
and add the field to `Session` after `_announced`:

```python
    _lock: threading.RLock = field(default_factory=threading.RLock)
    """
    Serialises everything that touches the catalog or the state around it.

    Reentrant because `catalog()` and `degrade()` are public and are also
    reached from inside the locked region — a plain lock would deadlock the
    first time a read failed.

    Wider than correctness strictly needs: the read itself is serialised too.
    All three bundled drivers report DB-API `threadsafety=2`, so concurrent
    reads on one connection are permitted — but they buy almost nothing here,
    since completions are latest-wins and the cache makes the second read
    instant, and not depending on three third-party contracts is worth the line.
    """
```

- [ ] **Step 4: Take it in the two public methods**

In `catalog()`, wrap the body:

```python
    def catalog(self) -> Any:
        """
        The catalog, built on first use, or None when there is none to build.

        Building it opens nothing — `open_catalog` defers the connection to the
        first read — so this is cheap and stays out of `initialize`.

        Locked because it is public and reads `_tried` before writing it: two
        callers arriving together would both build one, and the loser's
        connection would be dropped without being closed.
        """
        with self._lock:
            if self._tried or self.profile is None:
                return self._catalog
            self._tried = True
            try:
                self._catalog = open_catalog(self.profile, connect=self.connect)
            except Exception as error:  # noqa: BLE001
                log.exception('could not build a catalog; completing from the statement alone')
                self.degrade(str(error) or error.__class__.__name__)
            return self._catalog
```

and in `degrade()`, wrap its body the same way:

```python
        with self._lock:
            self._catalog = None
            self._tried = True
            if self.on_degrade is not None and not self._announced:
                self._announced = True
                self.on_degrade(why)
```

**The callback fires while the lock is held, and that is deliberate.** The
tidier-looking alternative — record that an announcement is due, release, then
announce — cannot actually be reached here: every caller of `degrade` is already
inside the locked region, so the outer frame holds the reentrant lock either
way, and threading a pending announcement through two return values would buy
nothing while costing a return type.

It is safe because the callback does not touch the session. In the server it is
`server.protocol.notify`, and in the tests it is `list.append`; neither can
re-enter `Session`, so no lock ordering exists to get wrong. If a future
callback ever does reach back in, the lock is reentrant and the same thread
proceeds.

- [ ] **Step 5: Split `suggest` so the lock's scope is visible**

Replace `Session.suggest` with the pair below. The point of the split is that
the lock's boundary is a function boundary rather than an indentation level
somebody has to read carefully.

```python
    def suggest(self, text: str, offset: int) -> list[CompletionItem]:
        """
        Items for a caret at `offset` in `text`. Never raises.

        `text` is the whole document; the engine sees one statement of it.
        """
        caret = max(0, min(offset, len(text)))
        dialect = self.dialect
        statement, base = statement_at(text, caret, dialect.syntax)
        starts = line_starts(text)
        within = caret - base
        suggestions = self._from_catalog(statement, within, dialect)
        if suggestions is None:
            # Outside the lock, deliberately: a read that failed must not hold
            # it while answering without one.
            suggestions = complete(statement, within, dialect)
        return [to_item(statement, base, starts, s, index, dialect) for index, s in enumerate(suggestions)]

    def _from_catalog(self, statement: str, within: int, dialect: Dialect) -> list[Suggestion] | None:
        """
        Suggestions read through the catalog, or None to complete without one.

        None covers all three ways there is nothing to read through: no profile,
        a dialect with no bundled driver, and a read that just failed. The
        caller answers from the statement alone in each case, which is the
        library's documented degradation and a useful answer.
        """
        with self._lock:
            catalog = self.catalog()
            if catalog is None:
                return None
            try:
                return complete(
                    statement,
                    within,
                    dialect,
                    catalog,
                    cache=self.cache,
                    identity=self.profile.user if self.profile else None,
                )
            except Exception as error:  # noqa: BLE001
                log.exception('the catalog failed; completing from the statement alone')
                self.degrade(str(error) or error.__class__.__name__)
                return None
```

Add `Suggestion` to the library imports at the top of the file:

```python
from pysqlsuggestions.types import Suggestion
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass, including the eight existing `Session` tests in
`tests/lsp/test_server.py`, which are the regression suite for this split.

- [ ] **Step 7: Commit**

```bash
git add -A lsp tests
git commit -m "fix: one caret at a time reaches the catalog"
```

---

## Task 2: the lazy connection is safe at its source

**Files:**
- Modify: `lsp/pysqlsuggestions_lsp/connections.py` (`open_catalog`)
- Test: `tests/lsp/test_connections.py`

**Interfaces:**
- Consumes: nothing from Task 1. This is deliberately independent.

**Why this is a task and not a line in Task 1.** Task 1's lock already stops the
server reaching `open_cursor` concurrently, so the server is safe without this.
But `open_catalog` returns an object whose closure is not safe, and it is public
— `check.py` uses it, and so could anything else. Making it safe at the source
beats requiring every caller to know. The test drives the catalog directly, with
no `Session` involved, so it proves the source and not Task 1's lock.

- [ ] **Step 1: Write the failing test**

Append to `tests/lsp/test_connections.py`:

```python
def test_one_connection_is_opened_when_queries_arrive_together() -> None:
    """
    `open_cursor` checks whether it has connected and then connects — so two
    callers arriving together both connect, and only the first is ever used
    again. The second is leaked: never closed, still holding a session.

    Driven through the catalog rather than through a `Session`, because the
    fault is in this closure and would otherwise be masked by the server's own
    lock.
    """
    opened: list[FakeConnection] = []
    ready = threading.Barrier(8)

    def connect(profile: Profile) -> FakeConnection:
        time.sleep(0.05)
        connection = FakeConnection()
        opened.append(connection)
        return connection

    catalog = open_catalog(Profile(dialect='postgres', host='db'), connect=connect)
    assert catalog is not None

    def read() -> None:
        ready.wait()
        catalog.schemas()

    threads = [threading.Thread(target=read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(opened) == 1
```

and extend that file's imports:

```python
import threading
import time
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/lsp/test_connections.py -k arrive_together -v`
Expected: FAIL, with more than one connection opened.

- [ ] **Step 3: Lock the closure**

In `lsp/pysqlsuggestions_lsp/connections.py`, add `import threading` to the
imports and replace the closure inside `open_catalog`:

```python
    opener = connect or _connect
    held: list[Any] = []
    guard = threading.Lock()

    def open_cursor() -> Cursor:
        """
        A cursor on the one connection, opening it on first use.

        Locked because the check and the connect are two steps: two callers
        arriving together would both find `held` empty, and the second
        connection would never be reachable again — nor closed.

        The lock covers the connect, not the cursor: DB-API `threadsafety=2`,
        which all three bundled drivers report, means a connection may be
        shared between threads while a cursor may not, and every caller here
        gets its own.
        """
        with guard:
            if not held:
                held.append(opener(profile))
        cursor: Cursor = held[0].cursor()
        return cursor
```

- [ ] **Step 4: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass, including `test_the_connection_is_reused_across_queries`,
which is this test's single-threaded twin and must be unaffected.

- [ ] **Step 5: Commit**

```bash
git add -A lsp tests
git commit -m "fix: the lazy connection opens once even when asked twice at once"
```

---

## Task 3: the handler runs off the event loop

**Files:**
- Modify: `lsp/pysqlsuggestions_lsp/server.py` (`create_server`)
- Test: `tests/lsp/test_server.py`

**Interfaces:**
- Consumes: the locks from Tasks 1 and 2, which is why this is third.

**What pygls does with it**, read from `pygls/protocol/json_rpc.py` at 2.1.1:

```python
if asyncio.iscoroutinefunction(handler):
    ...                                    # event loop
elif is_thread_function(handler):
    future = self._server.thread_pool.submit(handler, *args, **kwargs)
else:
    ...                                    # inline, blocking the loop
```

The test asserts membership in the middle branch using pygls's own predicate,
rather than looking for our decorator — the predicate the dispatcher actually
branches on is the one worth asserting, and it is what would change under a
pygls major version.

- [ ] **Step 1: Write the failing test**

Append to `tests/lsp/test_server.py`:

```python
def test_completion_is_dispatched_off_the_event_loop() -> None:
    """
    A completion may read a database, and pygls calls an unmarked handler
    inline on the event loop — so a slow introspection query would stop the
    server answering anything at all, including the client's own cancellation
    of the request that is stuck.

    Asserted through pygls's own predicate rather than by looking for our
    decorator: `is_thread_function` is the branch the dispatcher takes, and it
    is what a pygls major version would change.
    """
    handler = create_server().protocol.fm.features[TEXT_DOCUMENT_COMPLETION]
    assert is_thread_function(handler)


def test_initialize_stays_on_the_event_loop() -> None:
    """
    It touches no database — `Profile.from_options` is pure — so a thread hop
    would buy nothing and cost a context switch on the one request that
    everything else waits for anyway.
    """
    handler = create_server().protocol.fm.features[INITIALIZE]
    assert not is_thread_function(handler)
```

and extend that file's imports:

```python
from pygls.feature_manager import is_thread_function
```

- [ ] **Step 2: Run the tests to verify one fails**

Run: `uv run pytest tests/lsp/test_server.py -k event_loop -v`
Expected: `test_completion_is_dispatched_off_the_event_loop` FAILS;
`test_initialize_stays_on_the_event_loop` passes and must keep passing.

- [ ] **Step 3: Mark the handler**

In `lsp/pysqlsuggestions_lsp/server.py`, in `create_server`, add the decorator
**below** the feature registration, which is the order pygls's own examples use:

```python
    @server.feature(TEXT_DOCUMENT_COMPLETION, CompletionOptions(trigger_characters=TRIGGERS))
    @server.thread()
    def completion(params: CompletionParams) -> CompletionList:
        """
        Suggestions for the caret. Never raises.

        Marked for the thread pool because it may read a database. pygls calls
        an unmarked handler inline on the event loop, where a slow
        introspection query would stop the server answering anything — the
        session's lock is what makes that concurrency safe.
        """
        document = server.workspace.get_text_document(params.text_document.uri)
        offset = document.offset_at_position(params.position)
        return CompletionList(is_incomplete=False, items=server.session.suggest(document.source, offset))
```

- [ ] **Step 4: Run everything**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. If `test_the_server_registers_the_features_a_client_needs`
fails, the decorators are in the wrong order and the feature never registered —
`@server.feature` must be the outer one.

- [ ] **Step 5: Commit**

```bash
git add -A lsp tests
git commit -m "fix: a slow query no longer stops the server answering anything"
```

---

## Task 4: the record says what was actually wrong

**Files:**
- Modify: `docs/gaps.md`
- Modify: `CHANGELOG.md`
- Modify: `lsp/pysqlsuggestions_lsp/server.py` (module docstring)

- [ ] **Step 1: Correct the gaps entry**

In `docs/gaps.md`, in the "Already named elsewhere" list, replace the **Async**
bullet:

```markdown
- **Async.** Every catalog call is synchronous, and that is a decision rather
  than a gap — the port is documented, and the bridge for async callers is to
  pre-fetch into a `MemoryCatalog`.

  This entry used to say that a synchronous call "blocks its event loop on a
  slow introspection query", and named async as the fix. The blocking was real
  and the fix was wrong: pygls dispatches a thread-marked handler to a pool, and
  the completion handler simply was not marked. It is now, and the state that
  concurrency exposed is locked. Nothing here became asynchronous.
```

- [ ] **Step 2: Write the changelog entry**

In `CHANGELOG.md`, directly under `## Unreleased`:

```markdown
### A slow database no longer freezes the editor

The language server ran its completion handler on the event loop, so a slow
introspection query — a database behind a VPN, a cold connection — stopped the
server answering anything at all until it returned. Including the client's own
cancellation of the request that was stuck.

The handler now runs in pygls's thread pool. Nothing became asynchronous: the
`Catalog` port is synchronous by design, and pre-fetching into a
`MemoryCatalog` is still the bridge for callers who need otherwise.

Concurrency that the server never had before is now possible, so the state
behind it is locked: two carets arriving together used to be able to open two
connections and leak one, and to announce a degraded catalog twice. One caret at
a time reaches the database — which costs nothing, since completions are
latest-wins and the cache makes the second read instant.
```

- [ ] **Step 3: Record the rule where the other one lives**

In `lsp/pysqlsuggestions_lsp/server.py`, add a paragraph to the module docstring
after the "One connection per process" paragraph:

```python
One caret at a time reaches the database. The completion handler runs in pygls's
thread pool rather than on the event loop, so a slow query cannot stop the
server answering; the session's lock is what makes that safe, and it covers the
read as well as the state around it. Serialising costs nothing here — a
completion whose answer arrives late is one the next keystroke has already
replaced — and it means no third-party driver has to be right about sharing a
connection between threads.
```

- [ ] **Step 4: Verify**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass. `tests/test_build_pages.py` renders the changelog, so a
malformed heading surfaces here.

- [ ] **Step 5: Commit**

```bash
git add -A docs CHANGELOG.md lsp
git commit -m "docs: the async entry named the wrong fix"
```

---

## Self-review notes

**Spec coverage.** §1 → Task 3's preamble. §2 in → Tasks 1–3; out → nothing
implements them, by design. §3's three races → Task 1 (the first two, and the
third via the read lock) and Task 2 (the third at its source). §4's lock width
and release point → Task 1 Steps 3–5. §5 wiring → Task 3; races → Task 1
Step 1; regression → Task 1 Step 6. §6 → Task 4.

**Ordering constraints, all load-bearing.**
- Task 1 before Task 2: Task 2's lock would otherwise make Task 1's
  "one connection" test pass before Task 1 was written, and it would prove
  nothing.
- Tasks 1 and 2 before Task 3: no commit should enable concurrency before the
  state behind it is safe.
- Task 3's two decorators have a required order — `@server.feature` outermost —
  and Step 4 names the test that catches getting it wrong.

**One thing the spec got wrong, corrected here.** §4 said the lock is "released
before the fallback re-completes", and separately implied the degrade callback
could fire outside it. The first is true and Task 1 Step 5 implements it. The
second is not reachable: every caller of `degrade` is inside the locked region,
so the outer frame holds the reentrant lock whatever `degrade` itself does.
Task 1 Step 4 fires the callback under the lock and says why that is safe rather
than pretending otherwise.

**One deliberate redundancy.** After Task 1, the server can no longer reach
`open_cursor` concurrently, so Task 2 is not needed for the server to be
correct. It is kept because `open_catalog` is public and returns an object whose
closure would be unsafe in any other caller's hands, and because its test drives
the closure directly rather than through the lock that would mask it.

**Not tested, and why.** That the event loop is genuinely free during a slow
read. Proving it needs a client, a real socket and a timing assertion — a test
that fails on a loaded CI machine for reasons unrelated to the code. The
dispatch branch is quoted from pygls's source in Task 3 and the handler's
membership in it is asserted; that is the honest limit here.
