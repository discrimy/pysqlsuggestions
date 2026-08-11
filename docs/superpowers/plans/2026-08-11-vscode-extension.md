# VS Code extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A VS Code extension that provisions a Python venv from bundled wheels, resolves a connection profile, and runs `pysqlsuggestions-lsp` as a language client for `.sql` files.

**Architecture:** TypeScript in `editors/vscode/`. Five focused modules — runtime provisioning, profile resolution, secrets, status bar, and the activation wiring. A build script assembles locally-built wheels plus their pure-Python dependencies into `bundled/wheels/`, and the extension pip-installs from that directory offline on first activation.

**Tech Stack:** TypeScript, `vscode-languageclient`, esbuild, `@vscode/vsce`, `@vscode/test-electron`, Python 3.10+ (user-supplied interpreter, used only to build the venv).

This is plan 2 of 2 for `docs/superpowers/specs/2026-08-11-vscode-extension-design.md`. Plan 1 (`2026-08-11-lsp-server.md`) is complete: the server exists, is tested against live Postgres, and its `initializationOptions` contract is fixed by `Profile.from_options`.

## Global Constraints

- **The server contract is already fixed.** `initializationOptions` is a flat object: `dialect` (string, required), `host` (string, required), `port` (int), `database` (string), `user` (string), `password` (string). Anything else is ignored. A missing `dialect` or `host` means the server runs catalog-free — it does not fail. Source of truth: `lsp/pysqlsuggestions_lsp/connections.py::Profile.from_options`.
- **Postgres only in the bundle.** `DRIVERS` in the server declares postgres and trino, but trino hard-requires `lz4`, `orjson` and `zstandard`, all compiled. Only the pg8000 tree is bundled. A profile naming any other dialect still works — the server resolves the dialect and completes without a catalog.
- **The bundled wheel set is exactly these ten, all `none-any`** (verified by download): `pygls`, `lsprotocol`, `attrs`, `cattrs`, `typing_extensions`, `pg8000`, `scramp`, `asn1crypto`, `python_dateutil`, `six` — plus the two built locally, `pysqlsuggestions` and `pysqlsuggestions_lsp`.
- **No network at activation.** Every wheel installed on first run comes from inside the VSIX. A wheel that is not there is a build bug, not a runtime download.
- **Versions move in lockstep.** `editors/vscode/package.json` `version` equals the root `pyproject.toml` version. Task 1 adds a test.
- **No password field in settings, ever.** Passwords live only in `SecretStorage`. A settings schema that accepts one is a settings schema someone commits to a repository.
- **Node 20+, TypeScript strict.** `"strict": true` in tsconfig; no `any` without a comment saying why.
- **Verification is `npm run check`** in `editors/vscode/` (tsc --noEmit, eslint, node --test) plus `./scripts/check.sh` at the root for the Python side.

---

## File Structure

**Created — extension:**

| path | responsibility |
| --- | --- |
| `editors/vscode/package.json` | manifest: activation, settings schema, commands, scripts |
| `editors/vscode/tsconfig.json` | strict TypeScript config |
| `editors/vscode/src/profiles.ts` | read settings, resolve the profile for a document, validate |
| `editors/vscode/src/secrets.ts` | SecretStorage read/write/prompt, keyed by profile name |
| `editors/vscode/src/runtime.ts` | find python3, build the venv, install bundled wheels |
| `editors/vscode/src/status.ts` | status bar: which profile, and whether the catalog answered |
| `editors/vscode/src/extension.ts` | activate: wire the above, start and restart the client |
| `editors/vscode/src/test/*.test.ts` | unit tests for profiles, secrets, runtime pathing |

**Created — build:**

| path | responsibility |
| --- | --- |
| `scripts/build_vsix.py` | build both wheels, download the pure deps, assemble `bundled/wheels/`, run vsce |
| `tests/test_build_vsix.py` | the bundle is complete, pinned, and contains nothing compiled |

**Modified:** `pyproject.toml` (nothing — the build script is stdlib plus `uv`), `README.md`, `CHANGELOG.md`, `.gitignore` (ignore `editors/vscode/node_modules`, `bundled/`, `out/`, `*.vsix`).

---

### Task 1: The manifest, the settings schema, and the version guard

The manifest is the contract with VS Code and the settings schema is the contract with the user. Both are data, so both are worth getting right before any code depends on them.

**Files:**
- Create: `editors/vscode/package.json`, `editors/vscode/tsconfig.json`, `editors/vscode/.eslintrc.json`, `editors/vscode/.vscodeignore`
- Modify: `.gitignore`
- Test: `tests/test_purity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the setting `pysqlsuggestions.connections` (array of profiles), `pysqlsuggestions.defaultConnection` (string), `pysqlsuggestions.pythonPath` (string, optional override); the commands `pysqlsuggestions.addConnection`, `pysqlsuggestions.selectConnection`, `pysqlsuggestions.restartServer`, `pysqlsuggestions.showLogs`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_purity.py`:

```python
def test_the_extension_version_matches_the_library() -> None:
    """
    The VSIX bundles wheels built from this tree, so the numbers must agree.

    An extension reporting 0.3.0 while carrying a 0.2.1 server is a bug report
    whose version line is a lie, and no other test would notice.
    """
    manifest = json.loads((ROOT / 'editors' / 'vscode' / 'package.json').read_text(encoding='utf-8'))
    declared = re.search(r"^version = '([^']+)'", (ROOT / 'pyproject.toml').read_text(), re.M)
    assert declared is not None, 'pyproject.toml declares no version'
    assert manifest['version'] == declared.group(1)


def test_the_settings_schema_has_no_password_field() -> None:
    """
    A password field in settings is a password in someone's git history.

    Passwords live in SecretStorage. This asserts the schema offers nowhere to
    put one, because a helpful-looking field is all it takes.
    """
    manifest = json.loads((ROOT / 'editors' / 'vscode' / 'package.json').read_text(encoding='utf-8'))
    properties = manifest['contributes']['configuration']['properties']
    profile_schema = properties['pysqlsuggestions.connections']['items']['properties']
    assert 'password' not in profile_schema
```

Add `import json` to the imports at the top of the file.

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_purity.py -k extension_version -v
```

Expected: FAIL with `FileNotFoundError` — `editors/vscode/package.json` does not exist.

- [ ] **Step 3: Write the manifest**

`editors/vscode/package.json`:

```json
{
  "name": "pysqlsuggestions",
  "displayName": "pysqlsuggestions",
  "description": "Schema-aware SQL completion: joins from foreign keys, values from statistics",
  "version": "0.2.1",
  "publisher": "pysqlsuggestions",
  "license": "MIT",
  "engines": { "vscode": "^1.85.0" },
  "categories": ["Programming Languages"],
  "activationEvents": ["onLanguage:sql"],
  "main": "./out/extension.js",
  "contributes": {
    "commands": [
      { "command": "pysqlsuggestions.addConnection", "title": "pysqlsuggestions: Add connection" },
      { "command": "pysqlsuggestions.selectConnection", "title": "pysqlsuggestions: Select connection for this workspace" },
      { "command": "pysqlsuggestions.restartServer", "title": "pysqlsuggestions: Restart server" },
      { "command": "pysqlsuggestions.showLogs", "title": "pysqlsuggestions: Show logs" }
    ],
    "configuration": {
      "title": "pysqlsuggestions",
      "properties": {
        "pysqlsuggestions.connections": {
          "type": "array",
          "default": [],
          "markdownDescription": "Named database connections. Passwords are **not** stored here — they are prompted for once and kept in the editor's secret storage.",
          "items": {
            "type": "object",
            "required": ["name", "dialect", "host"],
            "properties": {
              "name": { "type": "string", "description": "What this connection is called." },
              "dialect": {
                "type": "string",
                "enum": ["postgres", "clickhouse", "trino", "ansi"],
                "description": "Only postgres reads a catalog in this release. The others complete from the statement, with the right keywords and quoting."
              },
              "host": { "type": "string" },
              "port": { "type": "number" },
              "database": { "type": "string" },
              "user": { "type": "string" }
            },
            "additionalProperties": false
          }
        },
        "pysqlsuggestions.defaultConnection": {
          "type": ["string", "null"],
          "default": null,
          "description": "Which named connection to use when nothing else says otherwise."
        },
        "pysqlsuggestions.pythonPath": {
          "type": ["string", "null"],
          "default": null,
          "description": "Interpreter used to build the extension's private environment. Leave unset to search PATH."
        }
      }
    }
  },
  "scripts": {
    "build": "esbuild src/extension.ts --bundle --outfile=out/extension.js --external:vscode --format=cjs --platform=node",
    "check": "tsc --noEmit && eslint src --ext .ts && node --test out/test/",
    "test:compile": "tsc -p . --outDir out"
  },
  "dependencies": { "vscode-languageclient": "^9.0.1" },
  "devDependencies": {
    "@types/node": "^20.11.0",
    "@types/vscode": "^1.85.0",
    "@vscode/test-electron": "^2.3.9",
    "@vscode/vsce": "^2.24.0",
    "esbuild": "^0.20.0",
    "eslint": "^8.56.0",
    "typescript": "^5.3.0"
  }
}
```

`editors/vscode/tsconfig.json`:

```json
{
  "compilerOptions": {
    "module": "commonjs",
    "target": "ES2022",
    "lib": ["ES2022"],
    "outDir": "out",
    "rootDir": "src",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "sourceMap": true
  },
  "include": ["src"]
}
```

`editors/vscode/.eslintrc.json`:

```json
{
  "root": true,
  "parser": "@typescript-eslint/parser",
  "env": { "node": true, "es2022": true },
  "rules": { "eqeqeq": "error", "no-throw-literal": "error" }
}
```

`editors/vscode/.vscodeignore`:

```
src/**
out/test/**
tsconfig.json
.eslintrc.json
node_modules/**
!bundled/wheels/**
```

- [ ] **Step 4: Ignore build output**

Append to `.gitignore`:

```
editors/vscode/node_modules/
editors/vscode/out/
editors/vscode/bundled/
editors/vscode/*.vsix
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_purity.py -v
```

Expected: all PASS, including both new tests.

- [ ] **Step 6: Commit**

```bash
git add editors/vscode/package.json editors/vscode/tsconfig.json editors/vscode/.eslintrc.json editors/vscode/.vscodeignore .gitignore tests/test_purity.py
git commit -m "feat: a manifest for the extension, with nowhere to put a password"
```

---

### Task 2: The wheel bundle

The extension installs offline, so the VSIX must carry every wheel it needs. This is the step that makes that true, and the test is what stops it silently becoming false.

**Files:**
- Create: `scripts/build_vsix.py`
- Test: `tests/test_build_vsix.py`

**Interfaces:**
- Consumes: `uv build`, `pip download`.
- Produces:
  - `PURE_SUFFIX = '-none-any.whl'`
  - `REQUIRED = frozenset({'pysqlsuggestions', 'pysqlsuggestions_lsp', 'pygls', 'lsprotocol', 'attrs', 'cattrs', 'typing_extensions', 'pg8000', 'scramp', 'asn1crypto', 'python_dateutil', 'six'})`
  - `def distribution(filename: str) -> str` — the distribution name from a wheel filename, normalised with underscores.
  - `def build_wheels(destination: Path) -> list[Path]` — assemble the bundle, returning what was written.

- [ ] **Step 1: Write the failing test**

`tests/test_build_vsix.py`:

```python
"""
The bundle carries everything and nothing compiled.

The extension installs with no network, so a wheel missing from the VSIX is not
a slow first run — it is an extension that never starts, on a machine the
developer does not have. And a compiled wheel in there is a VSIX that works on
the machine that built it and fails on every other, which is exactly what
choosing pg8000 over psycopg2 was for.
"""

from __future__ import annotations

from scripts.build_vsix import PURE_SUFFIX, REQUIRED, distribution


def test_a_wheel_filename_yields_its_distribution() -> None:
    """Names are normalised with underscores, as a wheel filename writes them."""
    assert distribution('pg8000-1.31.5-py3-none-any.whl') == 'pg8000'
    assert distribution('python_dateutil-2.9.0.post0-py2.py3-none-any.whl') == 'python_dateutil'
    assert distribution('typing_extensions-4.16.0-py3-none-any.whl') == 'typing_extensions'


def test_the_required_set_names_both_local_distributions() -> None:
    """The library and the server are built here, not downloaded."""
    assert 'pysqlsuggestions' in REQUIRED
    assert 'pysqlsuggestions_lsp' in REQUIRED


def test_the_required_set_names_no_backend_that_needs_compiling() -> None:
    """
    Trino hard-requires lz4, orjson and zstandard; ClickHouse's driver is worse.

    Bundling either means one VSIX per platform, which is the cost this whole
    packaging choice exists to avoid.
    """
    for compiled in ('trino', 'lz4', 'orjson', 'zstandard', 'clickhouse_driver', 'psycopg2', 'psycopg2_binary'):
        assert compiled not in REQUIRED


def test_the_pure_suffix_is_what_a_universal_wheel_ends_with() -> None:
    """`none-any` is the tag that means any interpreter, any platform."""
    assert PURE_SUFFIX == '-none-any.whl'
    assert 'pg8000-1.31.5-py3-none-any.whl'.endswith(PURE_SUFFIX)
    assert not 'lz4-4.4.5-cp312-cp312-win_amd64.whl'.endswith(PURE_SUFFIX)
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_build_vsix.py -v
```

Expected: collection error — no module `scripts.build_vsix`.

- [ ] **Step 3: Write the build script**

`scripts/build_vsix.py`:

```python
"""
Assemble the extension's wheel bundle, then package the VSIX.

The extension installs with no network, so every wheel it will ever need has to
be inside the VSIX. Two come from this tree and the rest from PyPI, and all of
them must be `none-any`: a compiled wheel would mean one VSIX per platform,
which is the cost that choosing pg8000 over psycopg2 exists to avoid.

    uv run python -m scripts.build_vsix
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'editors' / 'vscode'
WHEELS = EXTENSION / 'bundled' / 'wheels'

PURE_SUFFIX = '-none-any.whl'
"""The wheel tag meaning any interpreter, any platform."""

REQUIRED = frozenset({
    'pysqlsuggestions',
    'pysqlsuggestions_lsp',
    'pygls',
    'lsprotocol',
    'attrs',
    'cattrs',
    'typing_extensions',
    'pg8000',
    'scramp',
    'asn1crypto',
    'python_dateutil',
    'six',
})
"""
Every distribution the server needs at runtime, Postgres included.

Measured by resolving `pygls` and `pg8000` rather than guessed. Trino is absent
deliberately: it hard-requires lz4, orjson and zstandard, all compiled.
"""


def distribution(filename: str) -> str:
    """The distribution name from a wheel filename, as the filename spells it."""
    return filename.split('-')[0]


def build_wheels(destination: Path = WHEELS) -> list[Path]:
    """
    Fill `destination` with every wheel the extension installs. Returns them.

    Raises when a wheel is compiled or a required distribution is missing —
    both are build bugs, and both would otherwise surface as an extension that
    fails to start on somebody else's machine.
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(destination)], cwd=ROOT, check=True)
    subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(destination)], cwd=ROOT / 'lsp', check=True)
    subprocess.run(
        [
            sys.executable, '-m', 'pip', 'download',
            '--only-binary=:all:',
            '--dest', str(destination),
            'pygls', 'pg8000',
        ],
        check=True,
    )

    wheels = sorted(destination.glob('*.whl'))
    compiled = [wheel.name for wheel in wheels if not wheel.name.endswith(PURE_SUFFIX)]
    if compiled:
        message = f'compiled wheels in the bundle, which would need one VSIX per platform: {compiled}'
        raise SystemExit(message)

    found = {distribution(wheel.name) for wheel in wheels}
    if missing := REQUIRED - found:
        message = f'missing from the bundle: {sorted(missing)}'
        raise SystemExit(message)

    print(f'{len(wheels)} wheels, all platform-independent')
    return wheels


def main() -> int:
    """Build the bundle and package the VSIX."""
    build_wheels()
    subprocess.run(['npm', 'run', 'build'], cwd=EXTENSION, check=True)
    subprocess.run(['npx', 'vsce', 'package'], cwd=EXTENSION, check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
```

- [ ] **Step 4: Run the unit tests**

```bash
uv run pytest tests/test_build_vsix.py -v
```

Expected: all PASS.

- [ ] **Step 5: Actually build the bundle and inspect it**

```bash
uv run --with pip python -m scripts.build_vsix 2>&1 | tail -5
ls editors/vscode/bundled/wheels/
```

Expected: 12 wheels, every filename ending `-none-any.whl`, and the printed count saying so. If `pip download` pulls something compiled, the script exits non-zero and names it — that is the guard working, and the fix is to remove whatever dragged it in rather than to relax the check.

Stop before `npm run build` if node dependencies are not installed yet; run `npm install` in `editors/vscode/` first.

- [ ] **Step 6: Commit**

```bash
git add scripts/build_vsix.py tests/test_build_vsix.py
git commit -m "feat: the bundle carries everything, and refuses to carry anything compiled"
```

---

### Task 3: Profiles

Reading the user's settings and deciding which connection a document uses. Pure logic over plain data, so it is unit-testable without VS Code — which is why the settings object is passed in rather than read from `vscode.workspace` inside.

**Files:**
- Create: `editors/vscode/src/profiles.ts`, `editors/vscode/src/test/profiles.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `interface Profile { name: string; dialect: string; host: string; port?: number; database?: string; user?: string }`
  - `interface Settings { connections: unknown[]; defaultConnection: string | null }`
  - `function readProfiles(raw: unknown): Profile[]` — the valid entries, invalid ones dropped.
  - `function resolveProfile(profiles: Profile[], preferred: string | null): Profile | undefined`
  - `function initializationOptions(profile: Profile | undefined, password: string | undefined): Record<string, unknown> | undefined`

- [ ] **Step 1: Write the failing test**

`editors/vscode/src/test/profiles.test.ts`:

```typescript
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { initializationOptions, readProfiles, resolveProfile } from '../profiles';

const PG = { name: 'local', dialect: 'postgres', host: 'localhost', port: 5432, user: 'ana' };

test('a well formed entry is read', () => {
  assert.equal(readProfiles([PG]).length, 1);
  assert.equal(readProfiles([PG])[0]?.host, 'localhost');
});

test('an entry missing a dialect is dropped rather than throwing', () => {
  // Settings are user-edited JSON; half a profile must cost that profile, not the extension.
  assert.deepEqual(readProfiles([{ name: 'x', host: 'h' }]), []);
});

test('an entry missing a host is dropped', () => {
  assert.deepEqual(readProfiles([{ name: 'x', dialect: 'postgres' }]), []);
});

test('a non-array is no profiles', () => {
  assert.deepEqual(readProfiles('nonsense'), []);
});

test('a port of the wrong type is dropped but the profile survives', () => {
  const [profile] = readProfiles([{ ...PG, port: '5432' }]);
  assert.equal(profile?.port, undefined);
  assert.equal(profile?.host, 'localhost');
});

test('the preferred connection wins', () => {
  const profiles = readProfiles([PG, { ...PG, name: 'staging', host: 'far' }]);
  assert.equal(resolveProfile(profiles, 'staging')?.host, 'far');
});

test('with no preference and one connection, that one is used', () => {
  assert.equal(resolveProfile(readProfiles([PG]), null)?.name, 'local');
});

test('with no preference and several connections, none is guessed', () => {
  // Guessing means completing against the wrong database, which looks like working.
  const profiles = readProfiles([PG, { ...PG, name: 'prod', host: 'far' }]);
  assert.equal(resolveProfile(profiles, null), undefined);
});

test('a named connection that does not exist resolves to nothing', () => {
  assert.equal(resolveProfile(readProfiles([PG]), 'typo'), undefined);
});

test('initialization options carry the password and drop the name', () => {
  // `name` is ours; the server's contract has no field for it.
  const options = initializationOptions(readProfiles([PG])[0], 'hunter2');
  assert.equal(options?.password, 'hunter2');
  assert.equal(options?.dialect, 'postgres');
  assert.equal('name' in (options ?? {}), false);
});

test('no profile means no options, which is the servers degraded mode', () => {
  assert.equal(initializationOptions(undefined, undefined), undefined);
});

test('a profile with no password still produces options', () => {
  // Trust auth and .pgpass exist; a missing password is not a missing profile.
  assert.equal(initializationOptions(readProfiles([PG])[0], undefined)?.host, 'localhost');
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd editors/vscode && npm install && npm run test:compile && node --test out/test/
```

Expected: FAIL — cannot find module `../profiles`.

- [ ] **Step 3: Write the implementation**

`editors/vscode/src/profiles.ts`:

```typescript
/**
 * Connection profiles, as the user wrote them and as the server wants them.
 *
 * Settings are user-edited JSON, so every field is checked rather than trusted.
 * A malformed entry costs that entry and nothing else: an extension that throws
 * on activation because one profile has a typo is worse than one that quietly
 * offers fewer connections.
 *
 * Nothing here imports `vscode`. The settings object is passed in, which is what
 * makes this testable without an editor.
 */

export interface Profile {
  name: string;
  dialect: string;
  host: string;
  port?: number;
  database?: string;
  user?: string;
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

/** The valid entries in `raw`, in order. Invalid ones are dropped. */
export function readProfiles(raw: unknown): Profile[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const profiles: Profile[] = [];
  for (const entry of raw) {
    if (typeof entry !== 'object' || entry === null) {
      continue;
    }
    const record = entry as Record<string, unknown>;
    const name = text(record.name);
    const dialect = text(record.dialect);
    const host = text(record.host);
    if (name === undefined || dialect === undefined || host === undefined) {
      continue;
    }
    profiles.push({
      name,
      dialect,
      host,
      port: typeof record.port === 'number' ? record.port : undefined,
      database: text(record.database),
      user: text(record.user),
    });
  }
  return profiles;
}

/**
 * The profile to use, or undefined.
 *
 * With several connections and no preference, none is chosen. Guessing would
 * mean completing against the wrong database, and a wrong schema looks exactly
 * like a working one until it matters.
 */
export function resolveProfile(profiles: Profile[], preferred: string | null): Profile | undefined {
  if (preferred !== null) {
    return profiles.find((profile) => profile.name === preferred);
  }
  return profiles.length === 1 ? profiles[0] : undefined;
}

/**
 * What goes in `initializationOptions`, matching the server's `Profile.from_options`.
 *
 * `name` is ours and has no field there. Undefined when there is no profile,
 * which is how the server is told to complete from the statement alone.
 */
export function initializationOptions(
  profile: Profile | undefined,
  password: string | undefined,
): Record<string, unknown> | undefined {
  if (profile === undefined) {
    return undefined;
  }
  const options: Record<string, unknown> = { dialect: profile.dialect, host: profile.host };
  if (profile.port !== undefined) options.port = profile.port;
  if (profile.database !== undefined) options.database = profile.database;
  if (profile.user !== undefined) options.user = profile.user;
  if (password !== undefined) options.password = password;
  return options;
}
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd editors/vscode && npm run test:compile && node --test out/test/
```

Expected: 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/src/profiles.ts editors/vscode/src/test/profiles.test.ts editors/vscode/package-lock.json
git commit -m "feat: profiles read from settings, with a typo costing one connection"
```

---

### Task 4: The managed venv

Finding a Python and building an environment from the bundled wheels. The one failure mode with no graceful answer is no interpreter at all, so it is the one this task is most careful about.

**Files:**
- Create: `editors/vscode/src/runtime.ts`, `editors/vscode/src/test/runtime.test.ts`

**Interfaces:**
- Consumes: `profiles.ts` (nothing directly; sequenced after it).
- Produces:
  - `interface Runtime { python: string; ready: boolean }`
  - `function venvPython(root: string, platform: NodeJS.Platform): string` — path to the venv's interpreter.
  - `function stampPath(root: string): string` — where the installed-version marker lives.
  - `function needsInstall(stamp: string | undefined, version: string): boolean`
  - `async function ensureVenv(options: EnsureOptions): Promise<Runtime>` — with `run`, `readStamp`, `writeStamp` injected so the logic is testable without spawning anything.

- [ ] **Step 1: Write the failing test**

`editors/vscode/src/test/runtime.test.ts`:

```typescript
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { ensureVenv, needsInstall, stampPath, venvPython } from '../runtime';

test('the venv interpreter is where the platform puts it', () => {
  assert.equal(venvPython('/storage', 'linux'), '/storage/venv/bin/python');
  assert.equal(venvPython('C:\\storage', 'win32'), 'C:\\storage/venv/Scripts/python.exe');
});

test('the stamp lives beside the venv, not inside it', () => {
  // Inside, a rebuilt venv would carry a stamp saying it was already built.
  assert.equal(stampPath('/storage').includes('/venv/'), false);
});

test('a missing stamp means install', () => {
  assert.equal(needsInstall(undefined, '0.2.1'), true);
});

test('a matching stamp means skip', () => {
  assert.equal(needsInstall('0.2.1', '0.2.1'), false);
});

test('an older stamp means install', () => {
  // The VSIX carries wheels for its own version; an upgraded extension must reinstall.
  assert.equal(needsInstall('0.2.0', '0.2.1'), true);
});

test('a venv is built and stamped when there is no stamp', async () => {
  const commands: string[][] = [];
  let stamped: string | undefined;
  const runtime = await ensureVenv({
    root: '/storage',
    version: '0.2.1',
    wheelDir: '/ext/bundled/wheels',
    platform: 'linux',
    findPython: async () => 'python3',
    run: async (command, args) => { commands.push([command, ...args]); },
    readStamp: async () => undefined,
    writeStamp: async (value) => { stamped = value; },
  });
  assert.equal(runtime.ready, true);
  assert.equal(stamped, '0.2.1');
  assert.equal(commands[0]?.includes('venv'), true);
  const install = commands.find((c) => c.includes('install'));
  assert.equal(install?.includes('--no-index'), true, 'must not reach the network');
  assert.equal(install?.includes('--find-links'), true);
});

test('a stamped venv is not rebuilt', async () => {
  const commands: string[][] = [];
  const runtime = await ensureVenv({
    root: '/storage',
    version: '0.2.1',
    wheelDir: '/ext/bundled/wheels',
    platform: 'linux',
    findPython: async () => 'python3',
    run: async (command, args) => { commands.push([command, ...args]); },
    readStamp: async () => '0.2.1',
    writeStamp: async () => {},
  });
  assert.equal(runtime.ready, true);
  assert.deepEqual(commands, []);
});

test('no interpreter means not ready, and nothing is run', async () => {
  // The one failure with no graceful answer. It must not half-build anything.
  const commands: string[][] = [];
  const runtime = await ensureVenv({
    root: '/storage',
    version: '0.2.1',
    wheelDir: '/ext/bundled/wheels',
    platform: 'linux',
    findPython: async () => undefined,
    run: async (command, args) => { commands.push([command, ...args]); },
    readStamp: async () => undefined,
    writeStamp: async () => {},
  });
  assert.equal(runtime.ready, false);
  assert.deepEqual(commands, []);
});

test('a failed install leaves no stamp', async () => {
  // A stamp written after a failure is a broken venv that never rebuilds.
  let stamped: string | undefined;
  const runtime = await ensureVenv({
    root: '/storage',
    version: '0.2.1',
    wheelDir: '/ext/bundled/wheels',
    platform: 'linux',
    findPython: async () => 'python3',
    run: async () => { throw new Error('pip exploded'); },
    readStamp: async () => undefined,
    writeStamp: async (value) => { stamped = value; },
  });
  assert.equal(runtime.ready, false);
  assert.equal(stamped, undefined);
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd editors/vscode && npm run test:compile && node --test out/test/
```

Expected: FAIL — cannot find module `../runtime`.

- [ ] **Step 3: Write the implementation**

`editors/vscode/src/runtime.ts`:

```typescript
/**
 * The extension's private Python environment.
 *
 * Built once per version from wheels inside the VSIX, with `--no-index` so the
 * install cannot reach the network: a first run on a train must work, and a
 * wheel that is missing is a build bug rather than a slow download.
 *
 * The workspace's own environment is never touched. A user who has curated a
 * project venv did not curate it for this.
 *
 * Everything that touches the outside world is injected, so the decisions here
 * are testable without spawning a process.
 */

export interface Runtime {
  python: string;
  ready: boolean;
}

export interface EnsureOptions {
  root: string;
  version: string;
  wheelDir: string;
  platform: NodeJS.Platform;
  findPython: () => Promise<string | undefined>;
  run: (command: string, args: string[]) => Promise<void>;
  readStamp: () => Promise<string | undefined>;
  writeStamp: (value: string) => Promise<void>;
}

/** Where the venv's interpreter sits, which differs by platform. */
export function venvPython(root: string, platform: NodeJS.Platform): string {
  return platform === 'win32' ? `${root}/venv/Scripts/python.exe` : `${root}/venv/bin/python`;
}

/**
 * Where the installed-version marker lives — beside the venv, never inside it.
 *
 * Inside, deleting a broken venv would delete the evidence that it was ever
 * built, and a half-deleted one would keep a stamp claiming it was fine.
 */
export function stampPath(root: string): string {
  return `${root}/installed.txt`;
}

/** Whether the environment has to be built for `version`. */
export function needsInstall(stamp: string | undefined, version: string): boolean {
  return stamp !== version;
}

/**
 * The interpreter to run the server with.
 *
 * `ready` false means the caller should report and stay dormant — never
 * half-start. The only unrecoverable case is having no interpreter at all.
 */
export async function ensureVenv(options: EnsureOptions): Promise<Runtime> {
  const python = venvPython(options.root, options.platform);
  if (!needsInstall(await options.readStamp(), options.version)) {
    return { python, ready: true };
  }

  const found = await options.findPython();
  if (found === undefined) {
    return { python, ready: false };
  }

  try {
    await options.run(found, ['-m', 'venv', `${options.root}/venv`]);
    await options.run(python, [
      '-m', 'pip', 'install',
      '--no-index',
      '--find-links', options.wheelDir,
      'pysqlsuggestions-lsp[pg8000]',
    ]);
  } catch {
    // No stamp: a stamp written after a failure is a broken environment that
    // never rebuilds itself, and the user has no way to know that is why.
    return { python, ready: false };
  }

  await options.writeStamp(options.version);
  return { python, ready: true };
}
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd editors/vscode && npm run test:compile && node --test out/test/
```

Expected: 21 tests pass (12 from Task 3, 9 here).

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/src/runtime.ts editors/vscode/src/test/runtime.test.ts
git commit -m "feat: a private environment, built once from wheels that were already there"
```

---

### Task 5: Secrets, status bar, and activation

The three pieces that touch `vscode` directly. They are one task because none of them is independently useful — a secret nobody reads, a status bar showing nothing, and an activation with nothing to start are not three deliverables.

**Files:**
- Create: `editors/vscode/src/secrets.ts`, `editors/vscode/src/status.ts`, `editors/vscode/src/extension.ts`

**Interfaces:**
- Consumes: `profiles.ts`, `runtime.ts`.
- Produces: `activate(context)`, `deactivate()`.

- [ ] **Step 1: Write `secrets.ts`**

```typescript
/**
 * Passwords, kept where a password belongs.
 *
 * Never in settings: a settings field is a field somebody commits. The key is
 * per profile name, so two connections to the same host as different users do
 * not share one secret.
 */

import * as vscode from 'vscode';

function key(profileName: string): string {
  return `pysqlsuggestions.password.${profileName}`;
}

/** The stored password for `profileName`, or undefined. */
export async function readPassword(
  secrets: vscode.SecretStorage,
  profileName: string,
): Promise<string | undefined> {
  return secrets.get(key(profileName));
}

/**
 * Ask for a password and store it. Undefined when the user dismisses the prompt.
 *
 * Dismissing is a legitimate answer — trust authentication and `.pgpass` both
 * mean the server needs no password — so it is not treated as an error.
 */
export async function promptForPassword(
  secrets: vscode.SecretStorage,
  profileName: string,
): Promise<string | undefined> {
  const entered = await vscode.window.showInputBox({
    title: `Password for ${profileName}`,
    prompt: 'Stored in the editor\'s secret storage, never in settings.',
    password: true,
    ignoreFocusOut: true,
  });
  if (entered === undefined || entered.length === 0) {
    return undefined;
  }
  await secrets.store(key(profileName), entered);
  return entered;
}

/** Forget the stored password, so the next connect prompts again. */
export async function forgetPassword(
  secrets: vscode.SecretStorage,
  profileName: string,
): Promise<void> {
  await secrets.delete(key(profileName));
}
```

- [ ] **Step 2: Write `status.ts`**

```typescript
/**
 * What the status bar is for.
 *
 * A completion list is schema-aware or it is not, and the difference is
 * invisible in the list itself — a degraded list still holds keywords and
 * aliases and looks entirely healthy. This is the only place that distinction
 * can be seen, which is why it exists at all.
 */

import * as vscode from 'vscode';

export type State = 'dormant' | 'starting' | 'connected' | 'degraded' | 'no-profile';

const LABELS: Record<State, { text: string; tooltip: string }> = {
  dormant: { text: '$(circle-slash) SQL', tooltip: 'pysqlsuggestions is not running. Run "Show logs" for why.' },
  starting: { text: '$(sync~spin) SQL', tooltip: 'pysqlsuggestions is starting…' },
  connected: { text: '$(database) SQL', tooltip: 'Schema-aware completion from the connected database.' },
  degraded: {
    text: '$(warning) SQL',
    tooltip: 'The database could not be read. Completing from the statement alone.',
  },
  'no-profile': {
    text: '$(database) SQL', tooltip: 'No connection selected. Completing from the statement alone.',
  },
};

export class Status {
  private readonly item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.item.command = 'pysqlsuggestions.selectConnection';
  }

  /** Show `state`, naming `profile` when there is one. */
  set(state: State, profile?: string): void {
    const label = LABELS[state];
    this.item.text = profile === undefined ? label.text : `${label.text} ${profile}`;
    this.item.tooltip = label.tooltip;
    this.item.show();
  }

  dispose(): void {
    this.item.dispose();
  }
}
```

- [ ] **Step 3: Write `extension.ts`**

```typescript
/**
 * Activation: provision, resolve, start.
 *
 * Order matters. The client starts even with no profile, because completing
 * from the statement alone is a useful mode rather than a failure — and it is
 * what a user gets before they have configured anything at all.
 */

import * as cp from 'node:child_process';
import * as fs from 'node:fs/promises';
import * as vscode from 'vscode';
import { LanguageClient, type LanguageClientOptions, type ServerOptions } from 'vscode-languageclient/node';
import { initializationOptions, readProfiles, resolveProfile } from './profiles';
import { ensureVenv, stampPath } from './runtime';
import { readPassword, promptForPassword } from './secrets';
import { Status } from './status';

let client: LanguageClient | undefined;
let status: Status | undefined;
let output: vscode.OutputChannel | undefined;

function run(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = cp.spawn(command, args, { windowsHide: true });
    child.stdout.on('data', (chunk) => output?.append(String(chunk)));
    child.stderr.on('data', (chunk) => output?.append(String(chunk)));
    child.on('error', reject);
    child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`${command} exited ${String(code)}`))));
  });
}

async function findPython(configured: string | null): Promise<string | undefined> {
  for (const candidate of [configured, 'python3', 'python'].filter((c): c is string => c !== null)) {
    try {
      await run(candidate, ['--version']);
      return candidate;
    } catch {
      // Try the next one. Reporting happens once, by the caller, if none works.
    }
  }
  return undefined;
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  output = vscode.window.createOutputChannel('pysqlsuggestions');
  status = new Status();
  status.set('starting');
  context.subscriptions.push(output, status);

  context.subscriptions.push(
    vscode.commands.registerCommand('pysqlsuggestions.showLogs', () => output?.show()),
    vscode.commands.registerCommand('pysqlsuggestions.restartServer', async () => {
      await stop();
      await start(context);
    }),
    vscode.commands.registerCommand('pysqlsuggestions.selectConnection', () => selectConnection(context)),
    vscode.commands.registerCommand('pysqlsuggestions.addConnection', () => addConnection()),
  );

  await start(context);
}

async function start(context: vscode.ExtensionContext): Promise<void> {
  const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
  const root = context.globalStorageUri.fsPath;
  await fs.mkdir(root, { recursive: true });

  const version = (context.extension.packageJSON as { version: string }).version;
  const runtime = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Window, title: 'pysqlsuggestions: preparing Python…' },
    () =>
      ensureVenv({
        root,
        version,
        wheelDir: vscode.Uri.joinPath(context.extensionUri, 'bundled', 'wheels').fsPath,
        platform: process.platform,
        findPython: () => findPython(settings.get<string | null>('pythonPath', null)),
        run,
        readStamp: () => fs.readFile(stampPath(root), 'utf8').then((v) => v.trim()).catch(() => undefined),
        writeStamp: (value) => fs.writeFile(stampPath(root), value, 'utf8'),
      }),
  );

  if (!runtime.ready) {
    status?.set('dormant');
    void vscode.window
      .showErrorMessage('pysqlsuggestions needs a Python 3.10+ interpreter and could not find one.', 'Show logs')
      .then((choice) => choice === 'Show logs' && output?.show());
    return;
  }

  const profiles = readProfiles(settings.get('connections', []));
  const profile = resolveProfile(profiles, settings.get<string | null>('defaultConnection', null));
  const password = profile === undefined ? undefined : await readPassword(context.secrets, profile.name);

  const serverOptions: ServerOptions = { command: runtime.python, args: ['-m', 'pysqlsuggestions_lsp'] };
  const clientOptions: LanguageClientOptions = {
    documentSelector: [{ scheme: 'file', language: 'sql' }, { scheme: 'untitled', language: 'sql' }],
    initializationOptions: initializationOptions(profile, password),
    outputChannel: output,
  };

  client = new LanguageClient('pysqlsuggestions', 'pysqlsuggestions', serverOptions, clientOptions);
  await client.start();
  status?.set(profile === undefined ? 'no-profile' : 'connected', profile?.name);
}

async function stop(): Promise<void> {
  await client?.stop();
  client = undefined;
}

async function selectConnection(context: vscode.ExtensionContext): Promise<void> {
  const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
  const profiles = readProfiles(settings.get('connections', []));
  if (profiles.length === 0) {
    void vscode.window.showInformationMessage('No connections configured yet.', 'Add one').then(
      (choice) => choice === 'Add one' && addConnection(),
    );
    return;
  }
  const chosen = await vscode.window.showQuickPick(profiles.map((p) => p.name), { title: 'Use which connection?' });
  if (chosen === undefined) {
    return;
  }
  await settings.update('defaultConnection', chosen, vscode.ConfigurationTarget.Workspace);
  const profile = resolveProfile(profiles, chosen);
  if (profile !== undefined && (await readPassword(context.secrets, profile.name)) === undefined) {
    await promptForPassword(context.secrets, profile.name);
  }
  // Restarting is how a profile change takes effect: one connection per process.
  await stop();
  await start(context);
}

async function addConnection(): Promise<void> {
  // Opening the settings UI beats reimplementing a form: the schema in
  // package.json already describes every field and its allowed values.
  await vscode.commands.executeCommand('workbench.action.openSettings', 'pysqlsuggestions.connections');
}

export async function deactivate(): Promise<void> {
  await stop();
}
```

- [ ] **Step 4: Typecheck and lint**

```bash
cd editors/vscode && npm run check
```

Expected: PASS. `tsc --noEmit` catches API drift in `vscode-languageclient`; if `ServerOptions` or `LanguageClientOptions` do not match, read the installed version's `.d.ts` rather than guessing — the shape has changed across majors.

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/src/secrets.ts editors/vscode/src/status.ts editors/vscode/src/extension.ts
git commit -m "feat: passwords in secret storage, and a status bar that says what kind of list this is"
```

---

### Task 6: It runs in a real editor

Everything so far is unit-tested logic. This is the first time the extension is loaded by VS Code, and the first time the whole chain — venv, client, server, database — runs together.

**Files:**
- Create: `editors/vscode/src/test/integration/index.ts`, `editors/vscode/src/test/integration/completion.test.ts`, `editors/vscode/src/test/runTests.ts`

**Interfaces:**
- Consumes: everything.
- Produces: nothing.

- [ ] **Step 1: Build the bundle and the extension**

```bash
docker compose -f docker/docker-compose.yml up -d --wait
uv run --with pip python -m scripts.build_vsix
```

Expected: 12 pure wheels, then a `.vsix` produced.

- [ ] **Step 2: Write the integration test**

`editors/vscode/src/test/integration/completion.test.ts`:

```typescript
import { strict as assert } from 'node:assert';
import * as vscode from 'vscode';

const PROFILE = {
  name: 'docker', dialect: 'postgres', host: 'localhost',
  port: 57432, database: 'report_service', user: 'report',
};

async function completionsFor(sql: string): Promise<string[]> {
  const document = await vscode.workspace.openTextDocument({ language: 'sql', content: sql });
  await vscode.window.showTextDocument(document);
  const position = document.positionAt(sql.length);
  const list = await vscode.commands.executeCommand<vscode.CompletionList>(
    'vscode.executeCompletionItemProvider', document.uri, position,
  );
  return list.items.map((item) => (typeof item.label === 'string' ? item.label : item.label.label));
}

suite('pysqlsuggestions', () => {
  suiteSetup(async () => {
    const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
    await settings.update('connections', [PROFILE], vscode.ConfigurationTarget.Global);
    await settings.update('defaultConnection', 'docker', vscode.ConfigurationTarget.Global);
    await vscode.commands.executeCommand('pysqlsuggestions.restartServer');
  });

  test('a CTE defined in the statement is offered', async () => {
    const labels = await completionsFor('WITH recent AS (SELECT 1) SELECT * FROM rec');
    assert.ok(labels.includes('recent'), `no CTE among ${labels.slice(0, 10).join(', ')}`);
  });

  test('a join proposal arrives with its condition', async () => {
    const labels = await completionsFor('SELECT * FROM reports_report r JOIN ');
    assert.ok(labels.some((l) => l.includes(' ON ')), `no join among ${labels.slice(0, 10).join(', ')}`);
  });

  test('values come from the statistics', async () => {
    const labels = await completionsFor('SELECT * FROM reports_runlog r WHERE r.status = ');
    assert.ok(labels.some((l) => l.startsWith("'")), `no literal among ${labels.slice(0, 10).join(', ')}`);
  });
});
```

`editors/vscode/src/test/runTests.ts`:

```typescript
import * as path from 'node:path';
import { runTests } from '@vscode/test-electron';

async function main(): Promise<void> {
  const root = path.resolve(__dirname, '../../..');
  await runTests({
    extensionDevelopmentPath: root,
    extensionTestsPath: path.resolve(__dirname, './integration/index'),
  });
}

void main().catch((error: unknown) => {
  console.error('integration tests failed', error);
  process.exit(1);
});
```

`editors/vscode/src/test/integration/index.ts`:

```typescript
import * as path from 'node:path';
import Mocha from 'mocha';

export function run(): Promise<void> {
  const mocha = new Mocha({ ui: 'tdd', color: true, timeout: 120000 });
  mocha.addFile(path.resolve(__dirname, 'completion.test.js'));
  return new Promise((resolve, reject) => {
    mocha.run((failures) => (failures === 0 ? resolve() : reject(new Error(`${String(failures)} failing`))));
  });
}
```

Add `mocha` and `@types/mocha` to devDependencies, and a script:

```json
"test:integration": "tsc -p . --outDir out && node out/test/runTests.js"
```

- [ ] **Step 3: Run it**

```bash
cd editors/vscode && npm install && npm run test:integration
```

Expected: VS Code downloads once, launches, three tests pass. The timeout is 120 s because the first run builds the venv.

If completion returns nothing, check the output channel contents printed by the harness before assuming the server is wrong — the likeliest cause is the venv failing to build, which `runtime.ts` reports by leaving no stamp.

- [ ] **Step 4: Install the VSIX and use it by hand**

```bash
code --install-extension editors/vscode/pysqlsuggestions-0.2.1.vsix
```

Open a `.sql` file, configure the docker connection, and type `SELECT * FROM reports_report r JOIN `. Confirm by eye: proposals carry `fk:` in the detail, the status bar names the connection, and accepting one inserts the whole clause.

This is a manual verification step, not a test in any suite. Nothing in CI covers it. Said plainly here so nobody later reads a green build as proof the extension works in an editor.

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/src/test editors/vscode/package.json editors/vscode/package-lock.json
git commit -m "test: the extension loaded by a real editor, against a real database"
```

---

### Task 7: Say it exists

**Files:**
- Create: `editors/vscode/README.md`
- Modify: `README.md`, `CHANGELOG.md`

- [ ] **Step 1: Write `editors/vscode/README.md`**

Cover: what it does, that only Postgres reads a catalog in this release and why (compiled dependencies), that a Python 3.10+ interpreter must be on PATH, that passwords go to secret storage and never to settings, and what the status bar states mean. Keep it to what a user needs before installing.

- [ ] **Step 2: Add a section to the root `README.md`**

After the "In an editor" section added by plan 1, name the extension, its one prerequisite, and point at `editors/vscode/README.md`.

- [ ] **Step 3: Add a CHANGELOG entry**

Under `## Unreleased`, beside the language-server entry. Read the most recent entries first and match their structure and voice. Say plainly that the bundle is Postgres-only and why — a user who wants Trino should learn it here, not after installing.

- [ ] **Step 4: Verify the README's instructions**

Follow your own install instructions on a clean VS Code profile (`code --profile clean`). A README nobody followed is a README that is wrong.

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/README.md README.md CHANGELOG.md
git commit -m "docs: an extension, and what it does not yet reach"
```

---

## Self-Review

**Spec coverage.** §6's five modules are Tasks 3–5. §4's managed venv is Task 4, with `--no-index` asserted rather than assumed; the pg8000 bundle and its purity guard are Task 2. §7's error table: no interpreter → Task 4's `ready: false` plus Task 5's single error message; install failure → Task 4's no-stamp-on-failure test; unreachable database → already handled server-side and verified in plan 1. §3's LSP mapping is plan 1's, complete. §2's "out" list is unchanged.

**Gap found and closed:** the spec's §7 row for *authentication rejected* — "clear the stored secret, re-prompt once, then degrade" — is not implemented by any task here. The server cannot report an auth failure distinctly: it catches every exception and degrades, so the extension has no signal to act on. `forgetPassword` exists in Task 5 but nothing calls it. Left deliberately unbuilt rather than faked: implementing it properly means the server distinguishing an auth error from any other and saying so over LSP, which is a server change and a protocol addition. Recorded as an open question below, not silently dropped.

**Second gap found and closed:** the spec says profiles may bind per workspace folder. Task 3 implements only a single `defaultConnection`, scoped to the workspace by `ConfigurationTarget.Workspace`. Multi-root folders each having their own connection is not built. The `resolveProfile` signature takes the preference as a parameter, so adding a folder lookup later changes one caller rather than the module.

**Placeholder scan:** Task 7's steps describe what to write rather than giving the prose, which is deliberate — README and CHANGELOG text should be written against the code as it ends up, and the steps name every point that must be covered. Every code step carries its code.

**Type consistency:** `Profile` has `name` in TypeScript and no `name` in the server's contract; `initializationOptions` is the only thing that crosses that boundary and drops it, asserted by a test. `ensureVenv`'s `EnsureOptions` fields match every call site in Task 5. `venvPython(root, platform)` and `stampPath(root)` take the same `root` that `activate` computes from `globalStorageUri`. `Status.set(state, profile?)` matches all five call sites.

**Known soft spot, stated rather than hidden:** Task 5 writes against `vscode-languageclient` v9's `ServerOptions`/`LanguageClientOptions`. Task 5 step 4 says to read the installed `.d.ts` if `tsc` disagrees rather than to guess, because that shape has changed across majors — the same failure mode that pygls 2.x caused in plan 1, where the plan's registration code was written against an API that no longer existed.

## Open questions carried forward

1. **Authentication failures.** The server degrades on every exception alike, so the extension cannot tell a wrong password from an unreachable host, and cannot re-prompt. Fixing it means a distinguishable error over the wire.
2. **Cache invalidation.** Inherited from the spec and still unanswered: a migration run while the editor is open leaves the list describing a schema that no longer exists. `restartServer` is the manual answer; a refresh command or a TTL is the real one.
3. **Trino and ClickHouse.** Both need compiled wheels. An opt-in "install support" command that reaches PyPI would serve them without platform builds, at the cost of a second provisioning path.
4. **Multi-root workspaces.** One connection per window today. Per-folder bindings need a folder lookup in `resolveProfile` and a client per folder, which is a larger change than it looks.
