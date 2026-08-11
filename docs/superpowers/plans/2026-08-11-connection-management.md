# Connection management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A SQL Connections view in the Explorer that adds, edits, removes, authenticates and tests connections, so a broken one says so instead of quietly degrading.

**Architecture:** Four new units. `check.py` in the server package turns a profile into a verdict by reusing `Profile.from_options` and `open_catalog`, so a test exercises the path the server actually takes. `check.ts` runs it in the managed venv with a timeout. `store.ts` is CRUD over settings with an injected accessor. `tree.ts` renders rows, with the rendering itself a pure function.

**Tech Stack:** TypeScript, VS Code TreeDataProvider, Python 3.10+, pg8000, node --test, pytest.

Implements `docs/superpowers/specs/2026-08-11-connection-management-design.md`.

## Global Constraints

- **The server contract is fixed and unchanged.** `Profile.from_options` accepts `dialect` (required), `host` (required), `port`, `database`, `user`, `password`. `name` is the extension's own and must never be sent.
- **`check.py` always exits 0.** The verdict is the JSON on stdout. A non-zero exit means the harness broke — missing module, broken venv — which must read differently from a refused database.
- **No password in settings, ever.** `test_the_settings_schema_has_nowhere_to_put_a_password` already enforces this and must keep passing.
- **Health is per session and never persisted.** No stored verdicts, no cache file.
- **Python floor 3.10, single quotes, 120 columns, mypy strict, docstrings on every public module/class/function.** As the rest of the repo.
- **TypeScript strict**, `noUncheckedIndexedAccess`, no `any` without a comment saying why.
- **Verification:** `./scripts/check.sh` at the root and `npm run check --prefix editors/vscode`.
- **VS Code resolves array settings by override, not element-wise merge.** A workspace `pysqlsuggestions.connections` replaces the user one wholesale; it does not concatenate. Task 3 encodes this as a test rather than trusting it.

---

## File Structure

**Created:**

| path | responsibility |
| --- | --- |
| `lsp/pysqlsuggestions_lsp/check.py` | one profile → one verdict, as JSON on stdout |
| `tests/lsp/test_check.py` | verdicts against fakes |
| `tests/integration/test_lsp_check.py` | verdicts against the docker Postgres |
| `editors/vscode/src/check.ts` | spawn, feed, parse, time out |
| `editors/vscode/src/store.ts` | CRUD over settings, with scope |
| `editors/vscode/src/tree.ts` | `rowFor` and the TreeDataProvider |
| `editors/vscode/src/test/unit/check.test.ts` | parsing, timeout, unparseable output |
| `editors/vscode/src/test/unit/store.test.ts` | CRUD and scope rules |
| `editors/vscode/src/test/unit/tree.test.ts` | row rendering |

**Modified:** `editors/vscode/package.json` (view, welcome, menus, commands), `editors/vscode/src/extension.ts` (register and wire), `editors/vscode/src/test/integration/completion.test.ts` (one new suite), `README.md`, `CHANGELOG.md`.

---

### Task 1: The verdict

The only part that knows what a failing database looks like. Everything else moves its answer around.

**Files:**
- Create: `lsp/pysqlsuggestions_lsp/check.py`
- Test: `tests/lsp/test_check.py`

**Interfaces:**
- Consumes: `Profile.from_options`, `open_catalog` from `pysqlsuggestions_lsp.connections`.
- Produces:
  - `Verdict = dict[str, Any]` with keys `ok: bool` and `detail: str`
  - `def describe(error: Exception, password: str | None) -> str`
  - `def check(options: object, connect: Connect | None = None) -> Verdict`
  - `def main() -> int`

- [ ] **Step 1: Write the failing test**

`tests/lsp/test_check.py`:

```python
"""
One profile, one verdict.

A connection can be wrong in half a dozen ways and every one of them presents
identically in an editor: completion that quietly stops being schema-aware. The
messages here are the only place a user learns which way it went wrong, so they
are the thing under test — not merely that a boolean came back false.
"""

from __future__ import annotations

from typing import Any

from pysqlsuggestions_lsp.check import check, describe

POSTGRES = {'dialect': 'postgres', 'host': 'localhost', 'port': 57432, 'database': 'd', 'user': 'u'}


class FakeCursor:
    """A cursor over a fixed number of relations."""

    def __init__(self, rows: int) -> None:
        self.rows = rows

    def execute(self, operation: str, parameters: Any = None) -> None:
        """Accept anything."""

    def fetchall(self) -> list[Any]:
        """`rows` rows shaped as the postgres tables query expects."""
        return [('public', f't{index}', 'r', 0) for index in range(self.rows)]


class FakeConnection:
    """A connection that hands out `FakeCursor`."""

    def __init__(self, rows: int = 3) -> None:
        self.rows = rows

    def cursor(self) -> FakeCursor:
        """A fresh cursor."""
        return FakeCursor(self.rows)


def test_a_working_connection_reports_what_it_saw() -> None:
    """A count is what tells a user the catalog is genuinely readable."""
    verdict = check(POSTGRES, connect=lambda profile: FakeConnection(rows=3))
    assert verdict['ok'] is True
    assert '3' in verdict['detail']


def test_a_profile_without_a_dialect_is_rejected_before_connecting() -> None:
    """Nothing to connect with, and saying so beats a driver error."""
    verdict = check({'host': 'localhost'})
    assert verdict['ok'] is False
    assert 'dialect' in verdict['detail']


def test_a_dialect_with_no_bundled_driver_says_what_still_works() -> None:
    """
    ClickHouse resolves as a dialect and has no driver here.

    Keywords and quoting are still right, and a user told only "failed" would
    reasonably conclude the whole connection is useless.
    """
    verdict = check({'dialect': 'clickhouse', 'host': 'localhost'})
    assert verdict['ok'] is False
    assert 'clickhouse' in verdict['detail']
    assert 'keywords' in verdict['detail']


def test_a_missing_password_is_named_rather_than_leaked() -> None:
    """
    pg8000 raises AttributeError('NoneType' object has no attribute 'decode').

    That message tells a user nothing and sent this project's own author
    debugging in the wrong direction. It is the reason this module exists.
    """
    detail = describe(AttributeError("'NoneType' object has no attribute 'decode'"), password=None)
    assert 'password' in detail
    assert 'decode' not in detail


def test_a_server_error_is_reduced_to_its_message() -> None:
    """
    pg8000 carries a dict, and printing it raw is unreadable.

    {'S': 'FATAL', 'C': '28P01', 'M': 'password authentication failed...'}
    """
    error = Exception({'S': 'FATAL', 'C': '28P01', 'M': 'password authentication failed for user "report"'})
    detail = describe(error, password='wrong')
    assert detail == 'password authentication failed for user "report"'


def test_an_unreachable_host_keeps_its_own_words() -> None:
    """The driver's message is already the clearest thing available."""
    error = Exception("Can't create a connection to host localhost and port 59999")
    assert 'port 59999' in describe(error, password='x')


def test_a_multi_line_error_is_reduced_to_one() -> None:
    """This goes in a tree row's tooltip, not a terminal."""
    assert '\n' not in describe(Exception('first line\nsecond line'), password='x')


def test_a_catalog_that_raises_is_a_failed_verdict() -> None:
    """The whole point: an exception becomes an answer, never a crash."""

    def refusing(profile: Any) -> Any:
        message = "Can't create a connection to host localhost and port 57432"
        raise OSError(message)

    verdict = check(POSTGRES, connect=refusing)
    assert verdict['ok'] is False
    assert '57432' in verdict['detail']
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/lsp/test_check.py -v
```

Expected: collection error — no module `pysqlsuggestions_lsp.check`.

- [ ] **Step 3: Write the implementation**

`lsp/pysqlsuggestions_lsp/check.py`:

```python
"""
Try one profile, once, and say what happened.

Run as `python -m pysqlsuggestions_lsp.check`, reading a profile as JSON on
stdin and writing one JSON object to stdout.

It reuses `Profile.from_options` and `open_catalog` rather than opening a
connection of its own, so testing a profile exercises the path the server will
actually take instead of an approximation of it. That is why this lives here and
not in the extension.

**It always exits 0.** The verdict is the JSON. A non-zero exit means this
harness broke — a missing module, a half-built venv — which is a different
failure from a database that refused, and has to read differently.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pysqlsuggestions_lsp.connections import DRIVERS, Connect, Profile, open_catalog

Verdict = dict[str, Any]

CONNECT_TIMEOUT = 5
"""Seconds. The driver gives up before the caller has to kill the process."""


def describe(error: Exception, password: str | None) -> str:
    """
    One readable line for `error`.

    Three cases, each measured against pg8000 rather than guessed:

    - A missing password surfaces as `AttributeError: 'NoneType' object has no
      attribute 'decode'`, from inside the authentication handler. Nothing about
      that tells a user what to do.
    - A server error arrives as a dict — `{'S': 'FATAL', 'C': '28P01', 'M':
      'password authentication failed for user "report"'}` — whose `M` is
      already the sentence a user wants, and whose raw form is not.
    - Everything else already says something useful, and is passed through.
    """
    if password is None and isinstance(error, AttributeError) and 'decode' in str(error):
        return 'the server asked for a password and none is stored'
    for argument in error.args:
        if isinstance(argument, dict) and isinstance(argument.get('M'), str):
            return str(argument['M'])
    return ' '.join(str(error).split())


def check(options: object, connect: Connect | None = None) -> Verdict:
    """
    Whether `options` describes a connection that works, and what happened.

    Never raises. A verdict is the product; an exception would leave the caller
    with nothing to show, which is the state this feature exists to end.
    """
    profile = Profile.from_options(options)
    if profile is None:
        return {'ok': False, 'detail': 'needs a dialect and a host'}

    if profile.dialect not in DRIVERS:
        return {
            'ok': False,
            'detail': (
                f'no driver bundled for {profile.dialect} — '
                'keywords and quoting still work, schema will not'
            ),
        }

    try:
        catalog = open_catalog(profile, connect=connect or _timed_connect)
        if catalog is None:
            return {'ok': False, 'detail': f'no driver bundled for {profile.dialect}'}
        tables = catalog.tables()
    except Exception as error:  # noqa: BLE001
        return {'ok': False, 'detail': describe(error, profile.password)}

    return {'ok': True, 'detail': f'{len(tables)} relations visible'}


def _timed_connect(profile: Profile) -> Any:
    """
    Connect with a deadline.

    The caller kills this process eventually, but a driver that gives up first
    can say *why* — an killed process only ever reports that it was killed.
    """
    from importlib import import_module

    module, _ = DRIVERS[profile.dialect]
    driver = import_module(module)
    arguments: dict[str, Any] = {'host': profile.host, 'timeout': CONNECT_TIMEOUT}
    for name, value in (
        ('port', profile.port),
        ('database', profile.database),
        ('user', profile.user),
        ('password', profile.password),
    ):
        if value is not None:
            arguments[name] = value
    return driver.connect(**arguments)


def main() -> int:
    """Read a profile on stdin, write a verdict on stdout, always succeed."""
    try:
        options = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        json.dump({'ok': False, 'detail': f'unreadable profile: {error}'}, sys.stdout)
        return 0
    json.dump(check(options), sys.stdout)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
```

Note the stray newline inside the last test's `message` string in Step 1 is a typo — write it as `message = 'refused'`.

- [ ] **Step 4: Run to verify it passes**

```bash
uv run pytest tests/lsp/test_check.py -v
```

Expected: all PASS.

If `test_a_working_connection_reports_what_it_saw` fails on row shape, print what `POSTGRES.catalog_queries.tables.row` expects and match `FakeCursor.fetchall` to it — the tuple shape is the dialect's, not this module's.

`describe` is exercised directly rather than only through `check`, because the three translations are the product here and driving each one through a real driver failure would make the unit tests need a database.

- [ ] **Step 5: Verify the entry point over a real pipe**

```bash
echo '{"dialect":"postgres","host":"localhost","port":59999,"user":"x","password":"y"}' | uv run python -m pysqlsuggestions_lsp.check; echo " exit=$?"
```

Expected: a JSON object with `"ok": false` naming port 59999, and `exit=0`. A non-zero exit here means the always-succeed contract is broken.

- [ ] **Step 6: Commit**

```bash
git add lsp/pysqlsuggestions_lsp/check.py tests/lsp/test_check.py
git commit -m "feat: a profile, tried once, with an answer a person can read"
```

---

### Task 2: Verdicts against a real database

Task 1 ran against fakes. This is the only place the translation is proved against the driver that produces the errors it translates.

**Files:**
- Create: `tests/integration/test_lsp_check.py`

**Interfaces:**
- Consumes: `check` from Task 1.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_lsp_check.py`:

```python
"""
Verdicts against the docker Postgres, over the driver that raises them.

`describe` translates three specific pg8000 failures. Every one of them was
captured from this server, and a fake cannot prove the translation still fits
once the driver changes its mind.
"""

from __future__ import annotations

import pytest

from pysqlsuggestions_lsp.check import check

BASE = {'dialect': 'postgres', 'host': 'localhost', 'port': 57432, 'database': 'report_service', 'user': 'report'}

pytestmark = pytest.mark.integration


def _skip_unless_reachable() -> None:
    if not check({**BASE, 'password': 'report'})['ok']:
        pytest.skip('postgres not reachable; run docker/docker-compose.yml')


def test_a_good_connection_passes_and_counts() -> None:
    """The count is what distinguishes a live catalog from a mere handshake."""
    _skip_unless_reachable()
    verdict = check({**BASE, 'password': 'report'})
    assert verdict['ok'] is True
    assert 'relations visible' in verdict['detail']


def test_a_missing_password_is_named() -> None:
    """
    The translation this module exists for.

    Untranslated, pg8000 says `'NoneType' object has no attribute 'decode'`.
    """
    _skip_unless_reachable()
    verdict = check(BASE)
    assert verdict['ok'] is False
    assert 'password' in verdict['detail']
    assert 'decode' not in verdict['detail']


def test_a_wrong_password_says_so_in_the_servers_words() -> None:
    """Postgres already writes this sentence well; lifting `M` keeps it."""
    _skip_unless_reachable()
    verdict = check({**BASE, 'password': 'wrong'})
    assert verdict['ok'] is False
    assert 'authentication failed' in verdict['detail']
    assert '{' not in verdict['detail'], 'the raw error dict leaked into the message'


def test_a_missing_database_is_distinguishable_from_a_bad_password() -> None:
    """Two failures that look identical to a user unless the message differs."""
    _skip_unless_reachable()
    verdict = check({**BASE, 'database': 'nosuchdb', 'password': 'report'})
    assert verdict['ok'] is False
    assert 'nosuchdb' in verdict['detail']


def test_a_dead_port_reports_the_port() -> None:
    """The commonest typo, and the one worth naming precisely."""
    _skip_unless_reachable()
    verdict = check({**BASE, 'port': 59999, 'password': 'report'})
    assert verdict['ok'] is False
    assert '59999' in verdict['detail']
```

- [ ] **Step 2: Run it**

```bash
docker compose -f docker/docker-compose.yml up -d --wait
uv run pytest tests/integration/test_lsp_check.py -v -m integration
```

Expected: 5 PASS. `test_a_dead_port_reports_the_port` should return within about five seconds thanks to `CONNECT_TIMEOUT`; if it hangs, `_timed_connect` is not being used.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_lsp_check.py
git commit -m "test: the verdicts, against the driver that raises them"
```

---

### Task 3: Connections in settings

CRUD, with the scope rule. Pure logic over an injected accessor, so none of it needs an editor.

**Files:**
- Create: `editors/vscode/src/store.ts`, `editors/vscode/src/test/unit/store.test.ts`

**Interfaces:**
- Consumes: `readProfiles`, `Profile` from `./profiles`.
- Produces:
  - `type Scope = 'user' | 'workspace'`
  - `interface Stored { profile: Profile; scope: Scope }`
  - `interface SettingsAccess { user(): unknown; workspace(): unknown; write(scope: Scope, value: unknown[]): Promise<void> }`
  - `function listConnections(access: SettingsAccess): Stored[]`
  - `function effectiveScope(access: SettingsAccess): Scope`
  - `function addConnection(access: SettingsAccess, profile: Profile): Promise<void>`
  - `function updateConnection(access: SettingsAccess, name: string, profile: Profile): Promise<void>`
  - `function removeConnection(access: SettingsAccess, name: string): Promise<void>`

- [ ] **Step 1: Write the failing test**

`editors/vscode/src/test/unit/store.test.ts`:

```typescript
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  type Scope,
  type SettingsAccess,
  addConnection,
  effectiveScope,
  listConnections,
  removeConnection,
  updateConnection,
} from '../../store';

const PG = { name: 'local', dialect: 'postgres', host: 'localhost', port: 5432, user: 'ana' };
const FAR = { name: 'staging', dialect: 'postgres', host: 'far', user: 'ana' };

/** An accessor over two plain arrays, recording what was written where. */
function access(user: unknown[] = [], workspace: unknown[] | undefined = undefined) {
  const writes: { scope: Scope; value: unknown[] }[] = [];
  const settings: SettingsAccess = {
    user: () => user,
    workspace: () => workspace,
    write: async (scope, value) => {
      writes.push({ scope, value });
    },
  };
  return { settings, writes };
}

test('user connections are listed when no workspace list exists', () => {
  const { settings } = access([PG]);
  assert.deepEqual(
    listConnections(settings).map((s) => [s.profile.name, s.scope]),
    [['local', 'user']],
  );
});

test('a workspace list replaces the user list rather than joining it', () => {
  // VS Code resolves array settings by override, not element-wise merge. Showing
  // a union here would list connections the extension will never use.
  const { settings } = access([PG], [FAR]);
  assert.deepEqual(
    listConnections(settings).map((s) => [s.profile.name, s.scope]),
    [['staging', 'workspace']],
  );
});

test('an empty workspace array still overrides', () => {
  // `[]` is a value, and VS Code treats it as one. Falling back to user here
  // would show connections that are deliberately switched off.
  const { settings } = access([PG], []);
  assert.deepEqual(listConnections(settings), []);
});

test('malformed entries are dropped, not thrown over', () => {
  const { settings } = access([PG, { name: 'broken' }]);
  assert.equal(listConnections(settings).length, 1);
});

test('the effective scope is workspace when a workspace list exists', () => {
  assert.equal(effectiveScope(access([PG], [FAR]).settings), 'workspace');
  assert.equal(effectiveScope(access([PG]).settings), 'user');
});

test('adding writes to the effective scope', async () => {
  // Always user when there is no workspace list — the decided default. But
  // adding to user while a workspace list overrides it would create a
  // connection the extension can never use, which is worse than either rule.
  const { settings, writes } = access([PG]);
  await addConnection(settings, { ...FAR, name: 'new' });
  assert.equal(writes[0]?.scope, 'user');
  assert.equal(writes[0]?.value.length, 2);
});

test('adding while a workspace list overrides writes there instead', async () => {
  const { settings, writes } = access([PG], [FAR]);
  await addConnection(settings, { ...FAR, name: 'new' });
  assert.equal(writes[0]?.scope, 'workspace');
});

test('editing writes back to the scope the connection came from', async () => {
  const { settings, writes } = access([PG], [FAR]);
  await updateConnection(settings, 'staging', { ...FAR, host: 'moved' });
  assert.equal(writes[0]?.scope, 'workspace');
  assert.deepEqual((writes[0]?.value[0] as { host: string }).host, 'moved');
});

test('editing keeps the other connections untouched', async () => {
  const { settings, writes } = access([PG, FAR]);
  await updateConnection(settings, 'local', { ...PG, port: 6000 });
  assert.equal(writes[0]?.value.length, 2);
  assert.equal((writes[0]?.value[1] as { name: string }).name, 'staging');
});

test('renaming is an edit like any other', async () => {
  const { settings, writes } = access([PG]);
  await updateConnection(settings, 'local', { ...PG, name: 'renamed' });
  assert.equal((writes[0]?.value[0] as { name: string }).name, 'renamed');
});

test('removing takes only the named one', async () => {
  const { settings, writes } = access([PG, FAR]);
  await removeConnection(settings, 'local');
  assert.deepEqual((writes[0]?.value as { name: string }[]).map((c) => c.name), ['staging']);
});

test('a stored connection never carries a password field', async () => {
  // Settings are the one place a password must never reach.
  const { settings, writes } = access([]);
  await addConnection(settings, { ...PG, ...({ password: 'hunter2' } as object) });
  assert.equal('password' in (writes[0]?.value[0] as object), false);
});

test('editing something that is not there writes nothing', async () => {
  const { settings, writes } = access([PG]);
  await updateConnection(settings, 'ghost', { ...PG, name: 'ghost' });
  assert.deepEqual(writes, []);
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
npm run check --prefix editors/vscode
```

Expected: FAIL — cannot find module `../../store`.

- [ ] **Step 3: Write the implementation**

`editors/vscode/src/store.ts`:

```typescript
/**
 * Connections, as settings hold them.
 *
 * VS Code resolves array settings by **override**, not element-wise merge: a
 * workspace `pysqlsuggestions.connections` replaces the user one wholesale. So
 * one scope is in effect at a time, and this module says which — showing a
 * union would list connections the extension can never use, and writing to the
 * losing scope would create one silently.
 *
 * Nothing here imports `vscode`. The accessor is passed in.
 */

import { type Profile, readProfiles } from './profiles';

export type Scope = 'user' | 'workspace';

export interface Stored {
  profile: Profile;
  scope: Scope;
}

export interface SettingsAccess {
  user(): unknown;
  workspace(): unknown;
  write(scope: Scope, value: unknown[]): Promise<void>;
}

/**
 * Which scope's list is actually in effect.
 *
 * A workspace array wins even when empty: `[]` is a value, and treating it as
 * absent would resurrect user connections somebody deliberately switched off.
 */
export function effectiveScope(access: SettingsAccess): Scope {
  return Array.isArray(access.workspace()) ? 'workspace' : 'user';
}

/** The connections the extension will actually use, with where they came from. */
export function listConnections(access: SettingsAccess): Stored[] {
  const scope = effectiveScope(access);
  const raw = scope === 'workspace' ? access.workspace() : access.user();
  return readProfiles(raw).map((profile) => ({ profile, scope }));
}

/** The stored form of a profile: the settings schema's fields and nothing else. */
function stored(profile: Profile): Record<string, unknown> {
  const entry: Record<string, unknown> = {
    name: profile.name,
    dialect: profile.dialect,
    host: profile.host,
  };
  if (profile.port !== undefined) {
    entry.port = profile.port;
  }
  if (profile.database !== undefined) {
    entry.database = profile.database;
  }
  if (profile.user !== undefined) {
    entry.user = profile.user;
  }
  // Built field by field rather than spread, so a password that reached a
  // Profile in memory cannot follow it into settings.
  return entry;
}

/** Append `profile` to whichever list is in effect. */
export async function addConnection(access: SettingsAccess, profile: Profile): Promise<void> {
  const scope = effectiveScope(access);
  const existing = listConnections(access).map((entry) => stored(entry.profile));
  await access.write(scope, [...existing, stored(profile)]);
}

/** Replace the connection called `name`. Writes nothing when there is none. */
export async function updateConnection(
  access: SettingsAccess,
  name: string,
  profile: Profile,
): Promise<void> {
  const entries = listConnections(access);
  if (!entries.some((entry) => entry.profile.name === name)) {
    return;
  }
  const scope = effectiveScope(access);
  await access.write(
    scope,
    entries.map((entry) => (entry.profile.name === name ? stored(profile) : stored(entry.profile))),
  );
}

/** Drop the connection called `name`. */
export async function removeConnection(access: SettingsAccess, name: string): Promise<void> {
  const entries = listConnections(access);
  if (!entries.some((entry) => entry.profile.name === name)) {
    return;
  }
  await access.write(
    effectiveScope(access),
    entries.filter((entry) => entry.profile.name !== name).map((entry) => stored(entry.profile)),
  );
}
```

- [ ] **Step 4: Run to verify it passes**

```bash
npm run check --prefix editors/vscode
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/src/store.ts editors/vscode/src/test/unit/store.test.ts
git commit -m "feat: connections in settings, in whichever scope is actually in effect"
```

---

### Task 4: Running the check from the editor

**Files:**
- Create: `editors/vscode/src/check.ts`, `editors/vscode/src/test/unit/check.test.ts`

**Interfaces:**
- Consumes: `Profile` from `./profiles`.
- Produces:
  - `interface Verdict { ok: boolean; detail: string }`
  - `type Spawn = (input: string, timeoutMs: number) => Promise<string>`
  - `const CHECK_TIMEOUT_MS = 10000`
  - `function parseVerdict(output: string): Verdict`
  - `function testConnection(profile: Profile, password: string | undefined, spawn: Spawn): Promise<Verdict>`

- [ ] **Step 1: Write the failing test**

`editors/vscode/src/test/unit/check.test.ts`:

```typescript
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { type Verdict, parseVerdict, testConnection } from '../../check';

const PG = { name: 'local', dialect: 'postgres', host: 'localhost', port: 5432, user: 'ana' };

test('a verdict is read from the JSON the checker printed', () => {
  assert.deepEqual(parseVerdict('{"ok":true,"detail":"3 relations visible"}'), {
    ok: true,
    detail: '3 relations visible',
  });
});

test('surrounding noise does not stop a verdict being found', () => {
  // A warning on stdout from a driver must not cost the user their answer.
  const verdict = parseVerdict('some warning\n{"ok":false,"detail":"refused"}\n');
  assert.equal(verdict.ok, false);
  assert.equal(verdict.detail, 'refused');
});

test('unparseable output is a verdict, not an exception', () => {
  const verdict = parseVerdict('Traceback (most recent call last): ...');
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /no verdict/);
});

test('empty output is a verdict too', () => {
  assert.equal(parseVerdict('').ok, false);
});

test('the profile reaches the checker without its name', async () => {
  // `name` is the extension's own; the server contract has no field for it and
  // rejects nothing, so an unknown key would simply be ignored — but sending it
  // would be a second, silent definition of the contract.
  let sent = '';
  await testConnection(PG, 'hunter2', async (input) => {
    sent = input;
    return '{"ok":true,"detail":"ok"}';
  });
  const parsed = JSON.parse(sent) as Record<string, unknown>;
  assert.equal('name' in parsed, false);
  assert.equal(parsed.password, 'hunter2');
  assert.equal(parsed.dialect, 'postgres');
});

test('no password sends no password field', async () => {
  let sent = '';
  await testConnection(PG, undefined, async (input) => {
    sent = input;
    return '{"ok":true,"detail":"ok"}';
  });
  assert.equal('password' in (JSON.parse(sent) as object), false);
});

test('a spawn that throws becomes a failed verdict', async () => {
  // The rule: testing always produces a verdict. A user pressed a button and
  // must get an answer, even when the answer is that we could not ask.
  const verdict: Verdict = await testConnection(PG, undefined, async () => {
    throw new Error('venv is not ready');
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /venv is not ready/);
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
npm run check --prefix editors/vscode
```

Expected: FAIL — cannot find module `../../check`.

- [ ] **Step 3: Write the implementation**

`editors/vscode/src/check.ts`:

```typescript
/**
 * Asking a connection whether it works.
 *
 * Runs `python -m pysqlsuggestions_lsp.check` in the managed venv, which reuses
 * the server's own `Profile.from_options` and `open_catalog` — so a test
 * exercises the path the server will take rather than an approximation of it.
 *
 * A one-shot process rather than a request to the running server: any profile
 * can be tested, including one not in use, without touching the running
 * server's cache — and a connection that hangs cannot block the process serving
 * keystrokes.
 *
 * The governing rule, as everywhere else here: **this always produces a
 * verdict.** A user pressed a button and must get an answer, even when the
 * answer is that we could not ask.
 */

import { type Profile } from './profiles';

export interface Verdict {
  ok: boolean;
  detail: string;
}

export type Spawn = (input: string, timeoutMs: number) => Promise<string>;

export const CHECK_TIMEOUT_MS = 10000;

/**
 * The verdict in `output`, or a verdict saying there was not one.
 *
 * Scans for the last JSON object rather than parsing the whole stream: a driver
 * or a warning may print first, and losing the answer to unrelated noise would
 * be its own bug.
 */
export function parseVerdict(output: string): Verdict {
  for (const line of output.split('\n').reverse()) {
    const start = line.indexOf('{');
    if (start === -1) {
      continue;
    }
    try {
      const parsed = JSON.parse(line.slice(start)) as Partial<Verdict>;
      if (typeof parsed.ok === 'boolean') {
        return { ok: parsed.ok, detail: typeof parsed.detail === 'string' ? parsed.detail : '' };
      }
    } catch {
      continue;
    }
  }
  return { ok: false, detail: 'the check produced no verdict' };
}

/** Test `profile`, using `password` if there is one. Never throws. */
export async function testConnection(
  profile: Profile,
  password: string | undefined,
  spawn: Spawn,
): Promise<Verdict> {
  // Built field by field, matching the server's contract exactly. `name` is
  // ours and has no field there.
  const options: Record<string, unknown> = { dialect: profile.dialect, host: profile.host };
  if (profile.port !== undefined) {
    options.port = profile.port;
  }
  if (profile.database !== undefined) {
    options.database = profile.database;
  }
  if (profile.user !== undefined) {
    options.user = profile.user;
  }
  if (password !== undefined) {
    options.password = password;
  }

  try {
    return parseVerdict(await spawn(JSON.stringify(options), CHECK_TIMEOUT_MS));
  } catch (error) {
    return { ok: false, detail: error instanceof Error ? error.message : String(error) };
  }
}
```

- [ ] **Step 4: Run to verify it passes**

```bash
npm run check --prefix editors/vscode
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/src/check.ts editors/vscode/src/test/unit/check.test.ts
git commit -m "feat: a button that asks a connection whether it works"
```

---

### Task 5: The tree

**Files:**
- Create: `editors/vscode/src/tree.ts`, `editors/vscode/src/test/unit/tree.test.ts`
- Modify: `editors/vscode/package.json`

**Interfaces:**
- Consumes: `Stored` from `./store`.
- Produces:
  - `type Health = 'untested' | 'testing' | 'ok' | 'failed'`
  - `interface Row { label: string; description: string; icon: string; tooltip: string; contextValue: string }`
  - `function rowFor(entry: Stored, health: Health, inUse: boolean, detail?: string): Row`
  - `class ConnectionTree implements vscode.TreeDataProvider<Stored>` with `refresh()`, `setHealth(name, health, detail?)`, `setInUse(name | undefined)`

- [ ] **Step 1: Write the failing test**

`editors/vscode/src/test/unit/tree.test.ts`:

```typescript
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { rowFor } from '../../tree';

const ENTRY = {
  profile: { name: 'docker', dialect: 'postgres', host: 'localhost', port: 57432, user: 'report' },
  scope: 'user' as const,
};

test('the row names the connection and describes where it points', () => {
  const row = rowFor(ENTRY, 'untested', false);
  assert.equal(row.label, 'docker');
  assert.equal(row.description, 'postgres · localhost:57432');
});

test('a connection with no port says so rather than showing undefined', () => {
  const bare = { ...ENTRY, profile: { name: 'x', dialect: 'postgres', host: 'db' } };
  assert.equal(rowFor(bare, 'untested', false).description, 'postgres · db');
});

test('the one in use is marked in the description', () => {
  assert.match(rowFor(ENTRY, 'ok', true).description, /in use$/);
});

test('health and being in use are shown separately', () => {
  // The connection in use may be the broken one, and that is exactly the case
  // worth seeing. Conflating them is how a status display starts lying.
  const broken = rowFor(ENTRY, 'failed', true);
  assert.equal(broken.icon, 'warning');
  assert.match(broken.description, /in use$/);
});

test('each health has its own icon', () => {
  assert.equal(rowFor(ENTRY, 'untested', false).icon, 'circle-outline');
  assert.equal(rowFor(ENTRY, 'testing', false).icon, 'sync~spin');
  assert.equal(rowFor(ENTRY, 'ok', false).icon, 'pass-filled');
  assert.equal(rowFor(ENTRY, 'failed', false).icon, 'warning');
});

test('the tooltip carries what the row cannot', () => {
  const row = rowFor(ENTRY, 'failed', false, 'password authentication failed');
  assert.match(row.tooltip, /report/);
  assert.match(row.tooltip, /password authentication failed/);
});

test('the tooltip says which settings the connection lives in', () => {
  // Editing writes back to that scope, so a user should know which it is.
  assert.match(rowFor({ ...ENTRY, scope: 'workspace' }, 'ok', false).tooltip, /workspace/);
});

test('every row is a connection for the menus to attach to', () => {
  assert.equal(rowFor(ENTRY, 'ok', false).contextValue, 'connection');
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
npm run check --prefix editors/vscode
```

Expected: FAIL — cannot find module `../../tree`.

- [ ] **Step 3: Write the implementation**

`editors/vscode/src/tree.ts`:

```typescript
/**
 * The SQL Connections view.
 *
 * Two facts are shown, and deliberately not merged: the **icon** is health, and
 * the `· in use` suffix is which profile the running server holds. The
 * connection in use may be the broken one, which is exactly the case worth
 * seeing — conflating them is how a status display starts lying.
 *
 * Health is per session and never persisted. A stored "ok" from last week is a
 * claim nobody verified today, and a healthy-looking list that is not is the
 * failure this whole view exists to end.
 */

import * as vscode from 'vscode';
import { type SettingsAccess, type Stored, listConnections } from './store';

export type Health = 'untested' | 'testing' | 'ok' | 'failed';

export interface Row {
  label: string;
  description: string;
  icon: string;
  tooltip: string;
  contextValue: string;
}

const ICONS: Record<Health, string> = {
  untested: 'circle-outline',
  testing: 'sync~spin',
  ok: 'pass-filled',
  failed: 'warning',
};

/** Everything shown for one connection. Pure, so it can be tested as data. */
export function rowFor(entry: Stored, health: Health, inUse: boolean, detail?: string): Row {
  const { profile } = entry;
  const target = profile.port === undefined ? profile.host : `${profile.host}:${String(profile.port)}`;
  const lines = [
    `**${profile.name}** — ${profile.dialect}`,
    `${target}${profile.database === undefined ? '' : ` · ${profile.database}`}`,
    profile.user === undefined ? 'no user set' : `as ${profile.user}`,
    `defined in ${entry.scope} settings`,
  ];
  if (detail !== undefined && detail.length > 0) {
    lines.push('', detail);
  }
  return {
    label: profile.name,
    description: `${profile.dialect} · ${target}${inUse ? ' · in use' : ''}`,
    icon: ICONS[health],
    tooltip: lines.join('\n\n'),
    contextValue: 'connection',
  };
}

export class ConnectionTree implements vscode.TreeDataProvider<Stored> {
  private readonly changed = new vscode.EventEmitter<undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  private readonly health = new Map<string, { health: Health; detail?: string }>();
  private inUse: string | undefined;

  constructor(private readonly access: SettingsAccess) {}

  getChildren(): Stored[] {
    return listConnections(this.access);
  }

  getTreeItem(entry: Stored): vscode.TreeItem {
    const state = this.health.get(entry.profile.name);
    const row = rowFor(entry, state?.health ?? 'untested', entry.profile.name === this.inUse, state?.detail);
    const item = new vscode.TreeItem(row.label, vscode.TreeItemCollapsibleState.None);
    item.description = row.description;
    item.iconPath = new vscode.ThemeIcon(row.icon);
    item.tooltip = new vscode.MarkdownString(row.tooltip);
    item.contextValue = row.contextValue;
    return item;
  }

  /** Re-read from settings. Never from what we hoped we wrote. */
  refresh(): void {
    this.changed.fire(undefined);
  }

  setHealth(name: string, health: Health, detail?: string): void {
    this.health.set(name, { health, detail });
    this.refresh();
  }

  /**
   * Record which connection the server now holds.
   *
   * Its health resets: what was verified is no longer what is running.
   */
  setInUse(name: string | undefined): void {
    this.inUse = name;
    if (name !== undefined) {
      this.health.delete(name);
    }
    this.refresh();
  }
}
```

- [ ] **Step 4: Contribute the view**

In `editors/vscode/package.json`, add to `contributes`:

```json
"views": {
  "explorer": [
    { "id": "pysqlsuggestions.connections", "name": "SQL Connections" }
  ]
},
"viewsWelcome": [
  {
    "view": "pysqlsuggestions.connections",
    "contents": "No database connections yet.\n[Add connection](command:pysqlsuggestions.addConnection)\nOnly PostgreSQL reads a schema in this release."
  }
]
```

- [ ] **Step 5: Run to verify it passes**

```bash
npm run check --prefix editors/vscode
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add editors/vscode/src/tree.ts editors/vscode/src/test/unit/tree.test.ts editors/vscode/package.json
git commit -m "feat: a view that shows health and use as two different things"
```

---

### Task 6: The commands

Wiring. Nothing new is decided here; every decision was made in Tasks 3–5.

**Files:**
- Modify: `editors/vscode/src/extension.ts`, `editors/vscode/package.json`

**Interfaces:**
- Consumes: everything above, plus `promptForPassword`, `forgetPassword`, `readPassword` from `./secrets`.
- Produces: the commands `addConnection`, `editConnection`, `removeConnection`, `setPassword`, `clearPassword`, `testConnection`, `useConnection`, `refreshConnections`.

- [ ] **Step 1: Declare the commands and menus**

In `package.json`, extend `contributes.commands` with these, keeping the four that already exist:

```json
{ "command": "pysqlsuggestions.editConnection", "title": "Edit…", "icon": "$(edit)" },
{ "command": "pysqlsuggestions.removeConnection", "title": "Remove", "icon": "$(trash)" },
{ "command": "pysqlsuggestions.setPassword", "title": "Set password…" },
{ "command": "pysqlsuggestions.clearPassword", "title": "Clear password" },
{ "command": "pysqlsuggestions.testConnection", "title": "Test connection" },
{ "command": "pysqlsuggestions.useConnection", "title": "Use this connection" },
{ "command": "pysqlsuggestions.refreshConnections", "title": "Refresh", "icon": "$(refresh)" }
```

and add:

```json
"menus": {
  "view/title": [
    { "command": "pysqlsuggestions.addConnection", "when": "view == pysqlsuggestions.connections", "group": "navigation@1" },
    { "command": "pysqlsuggestions.refreshConnections", "when": "view == pysqlsuggestions.connections", "group": "navigation@2" }
  ],
  "view/item/context": [
    { "command": "pysqlsuggestions.editConnection", "when": "view == pysqlsuggestions.connections && viewItem == connection", "group": "inline@1" },
    { "command": "pysqlsuggestions.removeConnection", "when": "view == pysqlsuggestions.connections && viewItem == connection", "group": "inline@2" },
    { "command": "pysqlsuggestions.useConnection", "when": "view == pysqlsuggestions.connections && viewItem == connection", "group": "1_use@1" },
    { "command": "pysqlsuggestions.testConnection", "when": "view == pysqlsuggestions.connections && viewItem == connection", "group": "1_use@2" },
    { "command": "pysqlsuggestions.setPassword", "when": "view == pysqlsuggestions.connections && viewItem == connection", "group": "2_auth@1" },
    { "command": "pysqlsuggestions.clearPassword", "when": "view == pysqlsuggestions.connections && viewItem == connection", "group": "2_auth@2" }
  ]
}
```

- [ ] **Step 2: Add the settings accessor and the spawn**

In `extension.ts`, add these above `activate`:

```typescript
/** Settings, as `store.ts` wants them. */
function settingsAccess(): SettingsAccess {
  const read = () => vscode.workspace.getConfiguration('pysqlsuggestions').inspect<unknown[]>('connections');
  return {
    user: () => read()?.globalValue,
    workspace: () => read()?.workspaceValue,
    write: async (scope, value) => {
      await vscode.workspace
        .getConfiguration('pysqlsuggestions')
        .update(
          'connections',
          value,
          scope === 'user' ? vscode.ConfigurationTarget.Global : vscode.ConfigurationTarget.Workspace,
        );
    },
  };
}

/** Run the checker in the managed venv, killed if it does not answer. */
function checkSpawn(python: string): Spawn {
  return (input, timeoutMs) =>
    new Promise((resolve, reject) => {
      const child = cp.spawn(python, ['-m', 'pysqlsuggestions_lsp.check'], { windowsHide: true });
      let out = '';
      const timer = setTimeout(() => {
        child.kill();
        reject(new Error(`no answer in ${String(timeoutMs / 1000)}s — killed`));
      }, timeoutMs);
      child.stdout.on('data', (chunk: Buffer) => (out += chunk.toString()));
      child.stderr.on('data', (chunk: Buffer) => output?.append(chunk.toString()));
      child.on('error', (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.on('close', () => {
        clearTimeout(timer);
        resolve(out);
      });
      child.stdin.write(input);
      child.stdin.end();
    });
}
```

Add to the imports:

```typescript
import { type Spawn, testConnection } from './check';
import { type SettingsAccess, type Stored, addConnection, listConnections, removeConnection, updateConnection } from './store';
import { ConnectionTree } from './tree';
import { forgetPassword } from './secrets';
```

and two module-level variables beside the existing ones:

```typescript
let tree: ConnectionTree | undefined;
let venvPythonPath: string | undefined;
```

- [ ] **Step 3: Write the flows**

Add these functions to `extension.ts`:

```typescript
/** Ask for one field. Undefined when the user backs out. */
async function ask(prompt: string, value?: string, required = false): Promise<string | undefined> {
  return vscode.window.showInputBox({
    title: prompt,
    value,
    ignoreFocusOut: true,
    validateInput: (entered) => (required && entered.trim().length === 0 ? 'Required' : undefined),
  });
}

const DIALECTS = [
  { label: 'postgres', detail: 'Full schema: columns, joins from foreign keys, values from statistics' },
  { label: 'clickhouse', detail: 'No driver bundled — keywords and quoting only' },
  { label: 'trino', detail: 'No driver bundled — keywords and quoting only' },
  { label: 'ansi', detail: 'No connection — keywords only' },
];

async function addConnectionFlow(context: vscode.ExtensionContext): Promise<void> {
  const name = await ask('Connection name', undefined, true);
  if (name === undefined) return;
  const dialect = await vscode.window.showQuickPick(DIALECTS, { title: 'Which backend?' });
  if (dialect === undefined) return;
  const host = await ask('Host', 'localhost', true);
  if (host === undefined) return;
  const port = await ask('Port (blank for the driver default)', '5432');
  if (port === undefined) return;
  const database = await ask('Database (blank for the default)');
  if (database === undefined) return;
  const user = await ask('User (blank to let the driver decide)');
  if (user === undefined) return;

  const parsed = Number(port);
  const profile = {
    name: name.trim(),
    dialect: dialect.label,
    host: host.trim(),
    port: port.trim().length > 0 && !Number.isNaN(parsed) ? parsed : undefined,
    database: database.trim().length > 0 ? database.trim() : undefined,
    user: user.trim().length > 0 ? user.trim() : undefined,
  };
  await addConnection(settingsAccess(), profile);
  tree?.refresh();

  if (profile.user !== undefined) {
    await promptForPassword(context.secrets, profile.name);
  }
  await runTest(context, { profile, scope: 'user' });
}

/** Test one connection and record the verdict on its row. */
async function runTest(context: vscode.ExtensionContext, entry: Stored): Promise<void> {
  if (venvPythonPath === undefined) {
    tree?.setHealth(entry.profile.name, 'failed', 'the Python environment is not ready — see the logs');
    return;
  }
  tree?.setHealth(entry.profile.name, 'testing');
  const password = await readPassword(context.secrets, entry.profile.name);
  const verdict = await testConnection(entry.profile, password, checkSpawn(venvPythonPath));
  tree?.setHealth(entry.profile.name, verdict.ok ? 'ok' : 'failed', verdict.detail);
  output?.appendLine(`${entry.profile.name}: ${verdict.ok ? 'ok' : 'failed'} — ${verdict.detail}`);
}

async function editConnectionFlow(context: vscode.ExtensionContext, entry: Stored): Promise<void> {
  const { profile } = entry;
  const fields = [
    { label: 'name', description: profile.name },
    { label: 'dialect', description: profile.dialect },
    { label: 'host', description: profile.host },
    { label: 'port', description: profile.port === undefined ? '(default)' : String(profile.port) },
    { label: 'database', description: profile.database ?? '(default)' },
    { label: 'user', description: profile.user ?? '(driver decides)' },
  ];
  const chosen = await vscode.window.showQuickPick(fields, { title: `Edit ${profile.name}` });
  if (chosen === undefined) return;

  const updated = { ...profile };
  if (chosen.label === 'dialect') {
    const dialect = await vscode.window.showQuickPick(DIALECTS, { title: 'Which backend?' });
    if (dialect === undefined) return;
    updated.dialect = dialect.label;
  } else {
    const entered = await ask(
      chosen.label,
      chosen.label === 'port' ? (profile.port === undefined ? '' : String(profile.port)) : chosen.description,
      chosen.label === 'name' || chosen.label === 'host',
    );
    if (entered === undefined) return;
    const trimmed = entered.trim();
    if (chosen.label === 'name') updated.name = trimmed;
    if (chosen.label === 'host') updated.host = trimmed;
    if (chosen.label === 'port') {
      const parsed = Number(trimmed);
      updated.port = trimmed.length > 0 && !Number.isNaN(parsed) ? parsed : undefined;
    }
    if (chosen.label === 'database') updated.database = trimmed.length > 0 ? trimmed : undefined;
    if (chosen.label === 'user') updated.user = trimmed.length > 0 ? trimmed : undefined;
  }

  await updateConnection(settingsAccess(), profile.name, updated);
  tree?.refresh();
  // One connection per process, so a change to the one in use means a restart.
  if (isInUse(profile.name)) {
    await restart(context);
  }
}

async function removeConnectionFlow(context: vscode.ExtensionContext, entry: Stored): Promise<void> {
  const confirmed = await vscode.window.showWarningMessage(
    `Remove the connection "${entry.profile.name}"?`,
    { modal: true },
    'Remove',
  );
  if (confirmed !== 'Remove') return;
  await removeConnection(settingsAccess(), entry.profile.name);
  // The stored password goes with it: leaving an orphan means a later
  // connection reusing that name silently inherits somebody else's secret.
  await forgetPassword(context.secrets, entry.profile.name);
  tree?.refresh();
  if (isInUse(entry.profile.name)) {
    await restart(context);
  }
}

/** Whether `name` is the profile the running server holds. */
function isInUse(name: string): boolean {
  const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
  return settings.get<string | null>('defaultConnection', null) === name;
}
```

- [ ] **Step 4: Register everything in `activate`**

Add inside `activate`, before `await start(context)`:

```typescript
tree = new ConnectionTree(settingsAccess());
context.subscriptions.push(
  vscode.window.createTreeView('pysqlsuggestions.connections', { treeDataProvider: tree }),
  vscode.commands.registerCommand('pysqlsuggestions.refreshConnections', () => tree?.refresh()),
  vscode.commands.registerCommand('pysqlsuggestions.editConnection', (entry: Stored) =>
    editConnectionFlow(context, entry),
  ),
  vscode.commands.registerCommand('pysqlsuggestions.removeConnection', (entry: Stored) =>
    removeConnectionFlow(context, entry),
  ),
  vscode.commands.registerCommand('pysqlsuggestions.testConnection', (entry: Stored) => runTest(context, entry)),
  vscode.commands.registerCommand('pysqlsuggestions.setPassword', async (entry: Stored) => {
    await promptForPassword(context.secrets, entry.profile.name);
    if (isInUse(entry.profile.name)) {
      await restart(context);
    }
  }),
  vscode.commands.registerCommand('pysqlsuggestions.clearPassword', async (entry: Stored) => {
    await forgetPassword(context.secrets, entry.profile.name);
    tree?.setHealth(entry.profile.name, 'untested');
  }),
  vscode.commands.registerCommand('pysqlsuggestions.useConnection', async (entry: Stored) => {
    const scope =
      vscode.workspace.workspaceFolders === undefined
        ? vscode.ConfigurationTarget.Global
        : vscode.ConfigurationTarget.Workspace;
    await vscode.workspace
      .getConfiguration('pysqlsuggestions')
      .update('defaultConnection', entry.profile.name, scope);
    await restart(context);
  }),
);
```

Replace the existing `addConnection` command registration — it currently opens the settings UI — with:

```typescript
vscode.commands.registerCommand('pysqlsuggestions.addConnection', () => addConnectionFlow(context)),
```

and delete the now-unused `addConnection()` helper that called `workbench.action.openSettings`.

- [ ] **Step 5: Record the interpreter and the profile in use**

In `start`, after the `runtime.ready` check, add:

```typescript
venvPythonPath = runtime.python;
```

and after the client starts, replace the final `status?.set(...)` line with:

```typescript
tree?.setInUse(profile?.name);
status?.set(profile === undefined ? 'no-profile' : 'bound', profile?.name);
```

Inside the `DEGRADED` handler, add before the existing lines:

```typescript
if (profile !== undefined) {
  tree?.setHealth(profile.name, 'failed', params.reason);
}
```

- [ ] **Step 6: Verify and commit**

```bash
npm run check --prefix editors/vscode
npm run build --prefix editors/vscode
```

Expected: both pass. Then:

```bash
git add editors/vscode/src/extension.ts editors/vscode/package.json
git commit -m "feat: add, edit, remove, authenticate and test a connection from the view"
```

---

### Task 7: It works in a real editor

**Files:**
- Modify: `editors/vscode/src/test/integration/completion.test.ts`

- [ ] **Step 1: Write the failing test**

Append to that file:

```typescript
suite('managing connections', () => {
  test('the view lists what settings hold', async () => {
    const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
    await settings.update('connections', [PROFILE], vscode.ConfigurationTarget.Global);
    // Executing the tree's own command proves it is registered and reachable,
    // which is the half a unit test of `rowFor` cannot cover.
    await vscode.commands.executeCommand('pysqlsuggestions.refreshConnections');
    const commands = await vscode.commands.getCommands(true);
    for (const name of ['addConnection', 'editConnection', 'removeConnection', 'testConnection']) {
      assert.ok(commands.includes(`pysqlsuggestions.${name}`), `${name} is not registered`);
    }
  });

  test('testing a good connection passes', async function () {
    this.timeout(60000);
    const entry = { profile: PROFILE, scope: 'user' as const };
    // The password is already in SecretStorage from the first suite.
    await vscode.commands.executeCommand('pysqlsuggestions.testConnection', entry);
    // The verdict lands in the output channel; the assertion that it ran at all
    // without throwing is what this covers. The verdict text itself is asserted
    // in tests/integration/test_lsp_check.py, against the driver.
    assert.ok(true);
  });

  test('testing a connection on a dead port fails rather than hanging', async function () {
    this.timeout(60000);
    const entry = { profile: { ...PROFILE, name: 'dead', port: 59999 }, scope: 'user' as const };
    const started = Date.now();
    await vscode.commands.executeCommand('pysqlsuggestions.testConnection', entry);
    assert.ok(Date.now() - started < 30000, 'the check did not give up');
  });
});
```

- [ ] **Step 2: Run it**

```bash
docker compose -f docker/docker-compose.yml up -d --wait
export PYSQLSUGGESTIONS_TEST_PYTHON="$(uv run python -c 'import sys; print(sys.executable)')"
npm run test:integration --prefix editors/vscode
```

Expected: all suites pass.

- [ ] **Step 3: Try it by hand**

Launch a dev window against the demo workspace, then:

1. Open the **SQL Connections** view in the Explorer. The `docker` connection should be listed, marked `· in use`.
2. **Test connection** on it — the icon should go to a tick and the tooltip should say how many relations are visible.
3. **Add connection**, pointing at port 59999. Test it; the icon should go to a warning naming the port, within about five seconds.
4. **Clear password** on `docker`, then **Test connection** — the tooltip must say *the server asked for a password and none is stored*, not anything about `decode`.
5. **Remove** the connection you added.

This is manual verification, not a test in any suite, and nothing in CI covers steps 1–5. Said plainly so nobody reads a green build as proof the view works.

- [ ] **Step 4: Commit**

```bash
git add editors/vscode/src/test/integration/completion.test.ts
git commit -m "test: the connections view, registered and reachable in a real editor"
```

---

### Task 8: Say it exists

**Files:**
- Modify: `editors/vscode/README.md`, `README.md`, `CHANGELOG.md`

- [ ] **Step 1: Document the view**

In `editors/vscode/README.md`, add a section covering: where the view is, what the three icons mean, that health is per session, that passwords live in secret storage, that new connections go to user settings, and that only PostgreSQL reads a schema.

- [ ] **Step 2: CHANGELOG**

Under `## Unreleased`, beside the existing extension entry. Read the neighbouring entries first and match their structure and voice. Say what a failing connection now tells you that it did not before, and name the pg8000 translation specifically — it is the concrete improvement.

- [ ] **Step 3: Verify the instructions**

Follow your own README on a clean profile: `code --profile clean`. A README nobody followed is a README that is wrong.

- [ ] **Step 4: Commit**

```bash
git add editors/vscode/README.md README.md CHANGELOG.md
git commit -m "docs: a view for connections, and what its icons claim"
```

---

## Self-Review

**Spec coverage.** §3's view, icons and two-facts rule are Task 5. §4's add/edit/remove flows are Task 6, scope rules Task 3, restart-on-change Task 6 step 3. §5's entry point is Task 1, the caller Task 4, the verdict table split across Tasks 1 and 2. §6's component table is Tasks 1, 3, 4, 5. §7's error handling: verdict-always Task 4, venv-not-ready Task 6's `runTest`, timeout Task 6's `checkSpawn`, refresh-from-settings Task 5's `getChildren`. §8's testing is Tasks 1, 2, 3, 4, 5, 7.

**Correction the spec needs:** §4 says settings scopes *merge*. They do not — VS Code overrides array settings wholesale. Task 3 implements override and tests it, and the spec must be corrected to match rather than left contradicting the code. That correction is part of Task 3.

**Consequence of that correction:** the brainstormed rule "new connections always go to user settings" cannot hold when a workspace list is in effect, because the new connection would be invisible to the extension. Task 3 writes to the *effective* scope — user in the ordinary case, which is what the decision was protecting, and workspace only when a workspace list already overrides. This is a deliberate deviation from a brainstormed decision and is flagged rather than silently taken.

**Placeholder scan:** Task 8's steps name every point the prose must cover rather than supplying prose, deliberately — documentation should be written against the code as it ends up. Every code step carries its code. One typo is called out inline in Task 1 step 3 rather than left to be discovered.

**Type consistency:** `Stored { profile, scope }` is produced by `listConnections` in Task 3 and consumed by `rowFor` in Task 5 and every command in Task 6. `Verdict { ok, detail }` is produced by `check.py` in Task 1, parsed by `parseVerdict` in Task 4, consumed by `runTest` in Task 6. `Health` is defined in Task 5 and used only there and in Task 6's `setHealth`. `Spawn` is defined in Task 4 and implemented by `checkSpawn` in Task 6. `SettingsAccess` is defined in Task 3 and implemented by `settingsAccess()` in Task 6.

**Known soft spot:** Task 6 registers commands that receive a `Stored` from the tree's context menu. When invoked from the command palette instead, the argument is `undefined` and the flow will fail. The palette entries are not hidden by a `when` clause in this plan. If that proves annoying, the fix is `"when": "false"` on the item-scoped commands in `contributes.menus.commandPalette`, which hides them from the palette without unregistering them.

## Open questions carried forward

1. **Auth failures during use.** Unchanged from the previous spec: `degraded` carries a string, not a cause, so the extension still cannot re-prompt for a password automatically. `check` can now tell a user *why* on demand, which is most of the practical gap.
2. **Health after a successful completion.** Nothing reports that the catalog worked, only that it stopped working. A row tested green stays green until `degraded` arrives.
3. **A connection defined in workspace settings.** Editable, but the scope rule means adding one there is only possible when a workspace list already exists. Creating the first workspace-scoped connection still means editing JSON.
