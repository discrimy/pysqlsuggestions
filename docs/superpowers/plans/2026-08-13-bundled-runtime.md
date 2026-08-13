# Bundled Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a CPython interpreter inside the VSIX with the server already installed, so the extension never looks at the machine's Python and cannot fail on a venv it could not build.

**Architecture:** Nine platform-targeted VSIXes, one per VS Code target. Each carries `bundled/runtime.tar.gz` — a python-build-standalone CPython 3.13 with `pysqlsuggestions`, `pysqlsuggestions-lsp`, `pygls` and `pg8000` installed into its own `site-packages` at build time. Activation extracts it once and spawns it; there is no venv, no `pip` at runtime, and no interpreter discovery. Because every wheel is `none-any`, one wheel set installs into all nine interpreters and the build has no cross-platform resolution to get wrong.

**Tech Stack:** Python 3.10+ for the build script (stdlib `tarfile`, `hashlib`, `urllib`), uv for the cross-target install, TypeScript for the extension, `@vscode/vsce` ≥ 2.24 for `--target` packaging.

**Spec:** `docs/superpowers/specs/2026-08-13-bundled-runtime-design.md` (§3, §4, §6, §8 and §9 are this plan; §5 is the sibling plan `2026-08-13-stdlib-catalog-readers.md`)

**Prerequisite:** `2026-08-13-stdlib-catalog-readers.md` must be complete. Without it, three of the nine targets have no Trino or ClickHouse client and the capability parity this plan assumes does not hold.

## Global Constraints

- **Nine targets, no more and no fewer:** `win32-x64`, `win32-arm64`, `linux-x64`, `linux-arm64`, `linux-armhf`, `alpine-x64`, `alpine-arm64`, `darwin-x64`, `darwin-arm64`.
- **CPython 3.13**, from python-build-standalone release **`20260807`**, `install_only_stripped` variant. 3.11–3.14 are complete across all nine; 3.10 is missing `win32-arm64`.
- **Every bundled wheel stays `none-any`.** `verify()` in `scripts/build_vsix.py` already rejects anything else and must keep doing so.
- **No network at activation.** Everything the extension needs is inside the VSIX.
- **No stamp is written on failure.** A stamp written before the environment is known good is a broken install that never rebuilds itself.
- **Ruff with `D`, mypy `strict`, single quotes, 120 columns** over `src`, `tests` and `lsp`. `scripts/` is covered by the same gate.
- **`scripts/build_vsix.py` must be run as a module** (`uv run --with pip python -m scripts.build_vsix`), never by path.
- **The gate** is `./scripts/check.sh`; the extension's is `cd editors/vscode && npm run check`.
- **Commits** are `feat:`/`fix:`/`test:`/`docs:`/`refactor:`/`chore:` with a lowercase prose summary and a body explaining the decision. No co-author trailers.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/runtime.py` | the nine targets, the lock file, and the pure functions over both |
| `scripts/runtime.lock` | one digest-pinned python-build-standalone asset per target |
| `tests/test_runtime_bundle.py` | the target table and lock verification, without downloading anything |
| `scripts/build_vsix.py` | fetch, install, pack and package — once per target |
| `editors/vscode/src/runtime.ts` | extract the runtime, stamp it, name the interpreter |
| `editors/vscode/src/extension.ts` | wire it; extraction via `tar`; the dormant path's message |
| `editors/vscode/package.json` | `pythonPath` removed |
| `editors/vscode/.vscodeignore` | the comment that names what must ship |
| `editors/vscode/README.md` | the requirements section, inverted |

`scripts/runtime.py` is separate from `build_vsix.py` for the reason `verify()`
was split out of `build_wheels()`: the parts that can be wrong are pure, and a
test of them must not need a 238 MB download.

---

## Task 1: The target table and the lock file

**Files:**
- Create: `scripts/runtime.py`
- Create: `scripts/runtime.lock`
- Test: `tests/test_runtime_bundle.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TARGETS: dict[str, str]` (VS Code target → Rust triple); `PYTHON_VERSION = '3.13'`; `PBS_RELEASE = '20260807'`; `ARCHIVE_SUFFIX = '-install_only_stripped.tar.gz'`; `Asset(target: str, filename: str, digest: str)` frozen dataclass; `read_lock(text: str) -> dict[str, Asset]`; `verify_lock(assets: Mapping[str, Asset]) -> list[str]`; `download_url(asset: Asset) -> str`; `site_packages(root: Path, target: str) -> Path`; `python_platform(target: str) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runtime_bundle.py`:

```python
"""
The runtime bundle's target table, checked without downloading 238 MB.

Every failure this guards produces a build that succeeds and a VSIX that does
not start: a target whose triple names another platform's interpreter, an asset
that is the unstripped variant, a lock file that lost an entry in a rebase.
None of them is visible by looking at the directory afterwards.
"""

from __future__ import annotations

from pathlib import Path

from scripts.runtime import (
    ARCHIVE_SUFFIX,
    PBS_RELEASE,
    PYTHON_VERSION,
    TARGETS,
    Asset,
    download_url,
    python_platform,
    read_lock,
    site_packages,
    verify_lock,
)

LOCK = Path(__file__).resolve().parents[1] / 'scripts' / 'runtime.lock'


def test_every_vs_code_target_is_named() -> None:
    """
    Nine, and exactly the nine VS Code publishes.

    A tenth would be a target the marketplace rejects; a missing one is a
    platform that silently gets no build, which nobody notices until an issue
    arrives from an Alpine container.
    """
    assert set(TARGETS) == {
        'win32-x64',
        'win32-arm64',
        'linux-x64',
        'linux-arm64',
        'linux-armhf',
        'alpine-x64',
        'alpine-arm64',
        'darwin-x64',
        'darwin-arm64',
    }


def test_alpine_targets_use_musl_and_linux_targets_do_not() -> None:
    """The one pair that looks interchangeable and is not."""
    assert TARGETS['alpine-x64'].endswith('-musl')
    assert TARGETS['alpine-arm64'].endswith('-musl')
    assert TARGETS['linux-x64'].endswith('-gnu')
    assert TARGETS['linux-arm64'].endswith('-gnu')


def test_the_lock_covers_every_target() -> None:
    """A lock entry lost in a rebase is a target that stops building."""
    assets = read_lock(LOCK.read_text(encoding='utf-8'))
    assert set(assets) == set(TARGETS)


def test_the_lock_is_internally_consistent() -> None:
    """Each filename must name its own target's triple, the pinned version and the stripped variant."""
    assert verify_lock(read_lock(LOCK.read_text(encoding='utf-8'))) == []


def test_verification_catches_a_filename_from_another_platform() -> None:
    """Copy-paste across nine near-identical lines is the failure this exists for."""
    assets = read_lock(LOCK.read_text(encoding='utf-8'))
    wrong = dict(assets)
    wrong['darwin-arm64'] = Asset(
        target='darwin-arm64',
        filename=assets['linux-x64'].filename,
        digest=assets['darwin-arm64'].digest,
    )
    problems = verify_lock(wrong)
    assert any('darwin-arm64' in problem for problem in problems)


def test_verification_catches_the_unstripped_variant() -> None:
    """`install_only` is the same interpreter and about 40% larger. Nine of those is 100 MB of nothing."""
    assets = read_lock(LOCK.read_text(encoding='utf-8'))
    fat = dict(assets)
    original = assets['linux-x64']
    fat['linux-x64'] = Asset(
        target='linux-x64',
        filename=original.filename.replace(ARCHIVE_SUFFIX, '-install_only.tar.gz'),
        digest=original.digest,
    )
    assert any('linux-x64' in problem for problem in verify_lock(fat))


def test_the_url_points_at_the_pinned_release() -> None:
    """A floating release would make two builds of the same commit differ."""
    asset = read_lock(LOCK.read_text(encoding='utf-8'))['linux-x64']
    url = download_url(asset)
    assert url.startswith('https://github.com/astral-sh/python-build-standalone/releases/download/')
    assert PBS_RELEASE in url
    assert url.endswith(asset.filename)


def test_site_packages_follows_the_platforms_own_layout() -> None:
    """Windows keeps them under `Lib`; everything else under `lib/pythonX.Y`."""
    root = Path('/tmp/x')
    assert site_packages(root, 'win32-x64') == root / 'python' / 'Lib' / 'site-packages'
    assert site_packages(root, 'linux-x64') == root / 'python' / 'lib' / f'python{PYTHON_VERSION}' / 'site-packages'


def test_the_python_platform_is_one_uv_understands() -> None:
    """uv installs into an interpreter it cannot execute, and this is how it is told which."""
    assert python_platform('win32-x64') == 'x86_64-pc-windows-msvc'
    assert python_platform('alpine-arm64') == 'aarch64-unknown-linux-musl'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime_bundle.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.runtime'`

- [ ] **Step 3: Write the module**

Create `scripts/runtime.py`:

```python
"""
The interpreter the VSIX carries, per platform.

The extension used to ask the machine for a Python and build a venv in it. That
fails in an open-ended number of ways — an unbundled `ensurepip`, PEP 668, a
Windows Store stub, a conda environment that shadows what we install — none of
which can be detected reliably from a machine we do not have. Shipping the
interpreter ends the question rather than adding another rule to it.

Split from `build_vsix.py` for the reason `verify()` was: everything that can be
wrong here is a pure function over a table and a lock file, and testing it must
not need a 238 MB download.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PYTHON_VERSION = '3.13'
"""
The CPython series the bundle carries.

Checked across the nine targets rather than assumed: python-build-standalone
`20260807` is complete for 3.11 through 3.14 and missing `win32-arm64` for 3.10.
3.13 is the newest that is both complete and inside the library's supported
range. A constant rather than a range: moving it is a deliberate commit with a
smoke test behind it, not something a resolver decides on a Tuesday.
"""

PBS_RELEASE = '20260807'
"""The python-build-standalone release. Pinned so two builds of a commit agree."""

ARCHIVE_SUFFIX = '-install_only_stripped.tar.gz'
"""
The stripped variant, which is what makes nine builds affordable.

`install_only` is the same interpreter with debug symbols — about 40% larger,
and nothing in an editor extension ever reads them.
"""

DOWNLOAD_BASE = 'https://github.com/astral-sh/python-build-standalone/releases/download'

TARGETS: dict[str, str] = {
    'win32-x64': 'x86_64-pc-windows-msvc',
    'win32-arm64': 'aarch64-pc-windows-msvc',
    'linux-x64': 'x86_64-unknown-linux-gnu',
    'linux-arm64': 'aarch64-unknown-linux-gnu',
    'linux-armhf': 'armv7-unknown-linux-gnueabihf',
    'alpine-x64': 'x86_64-unknown-linux-musl',
    'alpine-arm64': 'aarch64-unknown-linux-musl',
    'darwin-x64': 'x86_64-apple-darwin',
    'darwin-arm64': 'aarch64-apple-darwin',
}
"""
Every VS Code platform target, and the interpreter triple that serves it.

The Alpine pair is the one that looks interchangeable with the Linux pair and is
not: musl and glibc are different C libraries, and an extension that shipped the
glibc build to Alpine would fail at `exec` with a message about a missing
loader, which reads like a corrupt download.
"""


@dataclass(frozen=True, slots=True)
class Asset:
    """One target's interpreter archive, as the lock file pins it."""

    target: str
    filename: str
    digest: str


def read_lock(text: str) -> dict[str, Asset]:
    """
    Parse `runtime.lock`: `<sha256>  <target>  <filename>` per line.

    Same shape as `pyodide.lock`, extended by one column because nine archives
    are otherwise distinguishable only by a substring of their names.
    """
    assets: dict[str, Asset] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        digest, target, filename = stripped.split()
        assets[target] = Asset(target=target, filename=filename, digest=digest)
    return assets


def verify_lock(assets: Mapping[str, Asset]) -> list[str]:
    """
    What is wrong with this lock, as a list of sentences. Empty means nothing.

    Nine near-identical lines is exactly the shape that survives a bad rebase or
    a copied paste looking correct. Each of these produces a VSIX that builds
    and does not start.
    """
    problems = [f'{target} has no entry in runtime.lock' for target in TARGETS if target not in assets]
    problems.extend(f'{target} is not a VS Code target' for target in assets if target not in TARGETS)
    for target, asset in sorted(assets.items()):
        if target not in TARGETS:
            continue
        expected = f'cpython-{PYTHON_VERSION}.'
        if not asset.filename.startswith(expected):
            problems.append(f'{target} is not {expected}x: {asset.filename}')
        if f'+{PBS_RELEASE}-' not in asset.filename:
            problems.append(f'{target} is not from release {PBS_RELEASE}: {asset.filename}')
        if not asset.filename.endswith(f'-{TARGETS[target]}{ARCHIVE_SUFFIX}'):
            problems.append(f'{target} does not name {TARGETS[target]}{ARCHIVE_SUFFIX}: {asset.filename}')
        if len(asset.digest) != 64:
            problems.append(f'{target} has no sha256: {asset.digest}')
    return problems


def download_url(asset: Asset) -> str:
    """Where `asset` is fetched from."""
    return f'{DOWNLOAD_BASE}/{PBS_RELEASE}/{asset.filename}'


def site_packages(root: Path, target: str) -> Path:
    """
    Where packages go inside an extracted interpreter.

    Every python-build-standalone archive unpacks to a single `python/`
    directory; Windows keeps `site-packages` under `Lib` and everyone else under
    `lib/pythonX.Y`. Derived rather than searched for, so a layout change is a
    failed build rather than a silently empty install.
    """
    base = root / 'python'
    if target.startswith('win32-'):
        return base / 'Lib' / 'site-packages'
    return base / 'lib' / f'python{PYTHON_VERSION}' / 'site-packages'


def python_platform(target: str) -> str:
    """
    The `--python-platform` value uv wants for `target`.

    uv's platform names are the same triples, which is why `TARGETS` holds
    triples rather than something friendlier.
    """
    return TARGETS[target]
```

- [ ] **Step 4: Generate the lock file**

Run this, which fetches the release manifest and writes every digest at once —
transcribing nine SHA-256s by hand is the error the lock exists to prevent:

```bash
uv run python - <<'PY'
import json, urllib.request, re
from pathlib import Path
from scripts.runtime import TARGETS, PYTHON_VERSION, PBS_RELEASE, ARCHIVE_SUFFIX

url = f'https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{PBS_RELEASE}'
release = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'build'})))
assets = {asset['name']: asset for asset in release['assets']}

lines = ['# python-build-standalone, one archive per VS Code target.',
         '# Regenerate with the snippet in docs/superpowers/plans/2026-08-13-bundled-runtime.md.',
         f'# release {PBS_RELEASE}, CPython {PYTHON_VERSION}, stripped.']
for target, triple in TARGETS.items():
    pattern = re.compile(rf'^cpython-{re.escape(PYTHON_VERSION)}\.\d+\+{PBS_RELEASE}-{re.escape(triple)}{re.escape(ARCHIVE_SUFFIX)}$')
    matched = sorted(name for name in assets if pattern.match(name))
    if not matched:
        raise SystemExit(f'no archive for {target} ({triple})')
    name = matched[-1]
    digest = urllib.request.urlopen(assets[name]['browser_download_url']).read()
    import hashlib
    lines.append(f'{hashlib.sha256(digest).hexdigest()}  {target}  {name}')
Path('scripts/runtime.lock').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('\n'.join(lines))
PY
```

Expected: `scripts/runtime.lock` with three comment lines and nine entries. This
downloads all nine archives once (238 MB) to hash them; that is the only time
this plan pays that cost outside a build.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_runtime_bundle.py -q`
Expected: PASS, 9 tests

- [ ] **Step 6: Run the gate**

Run: `./scripts/check.sh`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/runtime.py scripts/runtime.lock tests/test_runtime_bundle.py
git commit -m "feat: pin one interpreter per VS Code target

Nine near-identical lock lines is exactly the shape a bad rebase or a
copied paste survives, and every way of getting one wrong produces a
VSIX that builds and does not start. verify_lock checks each filename
against its own target's triple, the pinned release and the stripped
variant, and the test suite does it without downloading anything."
```

---

## Task 2: Build a runtime for one target

**Files:**
- Modify: `scripts/build_vsix.py` (module docstring, `build_wheels` unchanged, new `pack_runtime`, new `main`)
- Test: `tests/test_build_vsix.py`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces: `RUNTIME = EXTENSION / 'bundled' / 'runtime.tar.gz'`; `CACHE = ROOT / '.cache' / 'runtimes'`; `fetch(asset: Asset, cache: Path) -> Path`; `unpack(archive: Path, into: Path) -> None`; `install_into(root: Path, target: str, wheels: Path) -> None`; `pack_runtime(target: str, wheels: Path, cache: Path = CACHE) -> Path`.

The install must **not** use `uv pip install --python <extracted>`: eight of the
nine interpreters cannot execute on the machine doing the building. `--target`
with `--python-version` and `--python-platform` installs into a directory
without running anything, which works precisely because every wheel is
`none-any` and there is no platform-specific resolution to perform.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_build_vsix.py`:

```python
def test_the_required_set_is_unchanged_by_shipping_an_interpreter() -> None:
    """
    The wheels are the same twelve. Only where they are installed changed.

    Worth asserting because the obvious mistake when adding a runtime is to
    start bundling something compiled "since we ship a platform build anyway" —
    which would give up the one property that makes nine targets cheap.
    """
    assert 'pg8000' in REQUIRED
    assert len(REQUIRED) == 12


def test_unpacking_refuses_an_archive_that_escapes_its_directory() -> None:
    """
    A tarball is a file format with a traversal bug in its history.

    The archive is digest-pinned so this cannot happen in practice, which is
    exactly why it would go unnoticed if the guard were dropped.
    """
    import tarfile

    hostile = tmp_archive_with_member('../escaped.txt')
    with pytest.raises(tarfile.TarError):
        unpack(hostile, hostile.parent / 'out')
```

Add at the top of the file:

```python
import io
import tarfile
from pathlib import Path

import pytest

from scripts.build_vsix import PURE_SUFFIX, REQUIRED, distribution, unpack, verify


@pytest.fixture
def tmp_archive_with_member(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A one-member tar.gz whose member name is whatever the test asks for."""

    def build(name: str) -> Path:
        archive = tmp_path / 'hostile.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            info = tarfile.TarInfo(name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b'hi'))
        return archive

    return build
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_vsix.py -q`
Expected: FAIL — `ImportError: cannot import name 'unpack' from 'scripts.build_vsix'`

- [ ] **Step 3: Write the implementation**

In `scripts/build_vsix.py`, replace the module docstring:

```python
"""
Assemble the extension's runtime, then package one VSIX per platform.

The extension carries its own interpreter, so nothing about a user's machine can
make it fail to start. Every wheel that goes into that interpreter is still
`none-any` — a compiled one would mean a per-target wheel set, platform tags,
and a verification step that can only really run on the target itself. Shipping
nine interpreters is cheap; shipping nine wheel sets is not.

    uv run --with pip python -m scripts.build_vsix
    uv run --with pip python -m scripts.build_vsix --target linux-x64

`--with pip` because the project venv has none: uv installs packages without it,
and `pip download` is the only way to resolve a dependency tree to files.
"""
```

Add after the existing constants:

```python
RUNTIME = EXTENSION / 'bundled' / 'runtime.tar.gz'
CACHE = ROOT / '.cache' / 'runtimes'
"""
Where fetched interpreters live between builds.

Load-bearing rather than a convenience: nine archives is 238 MB, and a release
build that re-fetched them would be slower than the packaging it exists to do.
"""
```

Add the four functions:

```python
def fetch(asset: runtime.Asset, cache: Path = CACHE) -> Path:
    """
    `asset` on disk, downloading it once and checking its digest every time.

    Verified on a cache hit too. A truncated download that got as far as the
    cache is indistinguishable from a good one by name and size, and would
    otherwise produce a VSIX with half an interpreter in it.
    """
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset.filename
    if not archive.exists():
        url = runtime.download_url(asset)
        print(f'fetching {asset.target}: {url}')
        with urllib.request.urlopen(url) as answer, archive.open('wb') as handle:
            shutil.copyfileobj(answer, handle)
    found = hashlib.sha256(archive.read_bytes()).hexdigest()
    if found != asset.digest:
        archive.unlink()
        message = f'{asset.filename} hashed {found}, not {asset.digest}; refetch'
        raise SystemExit(message)
    return archive


def unpack(archive: Path, into: Path) -> None:
    """
    Extract `archive` into `into`, refusing anything that escapes it.

    `filter='tar'` rather than `'data'`: python-build-standalone relies on
    symlinks — `bin/python3` is one, and so is most of `lib/` — which the
    stricter filter drops. `'tar'` keeps them and still refuses absolute paths
    and `..`, which is the property that matters.
    """
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as tar:
        tar.extractall(into, filter='tar')


def install_into(root: Path, target: str, wheels: Path) -> None:
    """
    Install the bundle into an extracted interpreter's own site-packages.

    Not `uv pip install --python <root>`: eight of the nine interpreters cannot
    execute on the machine doing the building. `--target` installs into a
    directory without running anything, which works here precisely because every
    wheel is `none-any` — there is no platform-specific resolution to perform,
    so telling uv the platform is a guard rather than a lookup.

    `--no-index` because a build that could reach PyPI could ship something the
    tree did not produce.
    """
    subprocess.run(
        [
            'uv',
            'pip',
            'install',
            '--target',
            str(runtime.site_packages(root, target)),
            '--python-version',
            runtime.PYTHON_VERSION,
            '--python-platform',
            runtime.python_platform(target),
            '--no-index',
            '--find-links',
            str(wheels),
            'pysqlsuggestions-lsp[pg8000]',
        ],
        check=True,
    )


def pack_runtime(target: str, wheels: Path, cache: Path = CACHE) -> Path:
    """
    Build `bundled/runtime.tar.gz` for `target`. Returns its path.

    Packed rather than shipped as a tree: a VSIX is a zip, which compresses per
    file, and the extracted interpreter zipped comes to 74.9 MB against 38.7 MB
    for the same content as a solid tar.gz inside the zip. The cost is one
    extraction on first activation.
    """
    assets = runtime.read_lock((ROOT / 'scripts' / 'runtime.lock').read_text(encoding='utf-8'))
    if problems := runtime.verify_lock(assets):
        message = 'runtime.lock is not shippable:\n  ' + '\n  '.join(problems)
        raise SystemExit(message)

    staging = cache / f'staging-{target}'
    unpack(fetch(assets[target], cache), staging)
    install_into(staging, target, wheels)

    RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    if RUNTIME.exists():
        RUNTIME.unlink()
    with tarfile.open(RUNTIME, 'w:gz') as tar:
        tar.add(staging / 'python', arcname='python')
    print(f'{target}: runtime.tar.gz is {RUNTIME.stat().st_size / 1_048_576:.1f} MiB')
    return RUNTIME
```

Add the imports these need at the top: `hashlib`, `tarfile`, `urllib.request`,
and `from scripts import runtime`.

- [ ] **Step 4: Run the unit tests**

Run: `uv run pytest tests/test_build_vsix.py -q`
Expected: PASS

- [ ] **Step 5: Build one runtime end to end**

Run:

```bash
uv run --with pip python -c "
from pathlib import Path
from scripts.build_vsix import build_wheels, pack_runtime, WHEELS
build_wheels()
pack_runtime('linux-x64', WHEELS)
"
```

Expected: a size line near `38.7 MiB`. Then prove the packed interpreter works —
this is the only target whose runtime can actually be executed on the builder:

```bash
mkdir -p /tmp/rt && tar -xzf editors/vscode/bundled/runtime.tar.gz -C /tmp/rt
/tmp/rt/python/bin/python3 -c "import pysqlsuggestions_lsp, pg8000, pygls; print(pysqlsuggestions_lsp.__version__)"
/tmp/rt/python/bin/python3 -c "from pysqlsuggestions.dialects.registry import named; print(named('clickhouse'))"
```

Expected: the version, then a `Dialect`. The second command is the one that
proves entry points survived `--target` — the dialect registry resolves through
the `pysqlsuggestions.dialects` group, and a `.dist-info` lost in the install
would make it return `None` with no other symptom.

- [ ] **Step 6: Commit**

```bash
git add scripts/build_vsix.py tests/test_build_vsix.py
git commit -m "feat: pack an interpreter with the server already inside it

Installs with --target rather than --python: eight of the nine
interpreters cannot execute on the machine building them, and --target
needs to run nothing. That works only because every wheel is none-any,
which is the property this build must never give up.

Packed rather than shipped as a tree — a VSIX compresses per file, so
the extracted interpreter zips to 74.9 MB against 38.7 MB solid."
```

---

## Task 3: Nine VSIXes

**Files:**
- Modify: `scripts/build_vsix.py` (`main`)
- Modify: `editors/vscode/.vscodeignore`

**Interfaces:**
- Consumes: `pack_runtime`, `build_wheels`.
- Produces: `main(argv: Sequence[str] | None = None) -> int` accepting `--target`, repeatable, defaulting to all nine.

- [ ] **Step 1: Rewrite `main`**

```python
def main(argv: Sequence[str] | None = None) -> int:
    """Build the runtime and package a VSIX, once per target."""
    # Checked rather than imported: this project's venv genuinely has no pip —
    # uv installs without one — so the failure is expected and the message has
    # to name the fix rather than let pip's own error surface from a subprocess.
    if find_spec('pip') is None:
        message = 'no pip here; run: uv run --with pip python -m scripts.build_vsix'
        raise SystemExit(message)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--target',
        action='append',
        choices=sorted(runtime.TARGETS),
        help='package only this target; repeatable. Every target when omitted.',
    )
    arguments = parser.parse_args(argv)
    targets = arguments.target or sorted(runtime.TARGETS)

    # Once, before the loop: the wheels are the same on every target, and
    # rebuilding them nine times would be nine chances for them to differ.
    build_wheels()
    subprocess.run(['npm', 'run', 'build'], cwd=EXTENSION, check=True, shell=sys.platform == 'win32')

    for target in targets:
        pack_runtime(target, WHEELS)
        subprocess.run(
            ['npx', 'vsce', 'package', '--target', target],
            cwd=EXTENSION,
            check=True,
            shell=sys.platform == 'win32',
        )
    print(f'{len(targets)} VSIXes')
    return 0
```

Add `import argparse` and `from collections.abc import Sequence` at the top.

`build_wheels()` still writes to `bundled/wheels/`, which the VSIX no longer
needs to ship. Add to `.vscodeignore`:

```
# `bundled/runtime.tar.gz` is the one build output that must ship: it is the
# interpreter and the server both. `bundled/wheels` is an intermediate — it
# feeds the install that produced the runtime and has no reader at run time.
bundled/wheels/**
```

and replace the file's opening comment with:

```
# The VSIX carries the bundled runtime and the built extension, nothing else.
```

- [ ] **Step 2: Verify one target packages**

Run: `uv run --with pip python -m scripts.build_vsix --target linux-x64`
Expected: `pysqlsuggestions-0.4.1-linux-x64.vsix` in `editors/vscode/`, and

```bash
unzip -l editors/vscode/pysqlsuggestions-0.4.1-linux-x64.vsix | grep -c '\.whl'
unzip -l editors/vscode/pysqlsuggestions-0.4.1-linux-x64.vsix | grep runtime.tar.gz
```

Expected: `0` wheels, and exactly one `runtime.tar.gz`.

- [ ] **Step 3: Commit**

```bash
git add scripts/build_vsix.py editors/vscode/.vscodeignore
git commit -m "feat: package one VSIX per target

Wheels and the TypeScript build happen once before the loop — they are
identical across targets, and rebuilding them nine times is nine chances
for them to differ. bundled/wheels stops shipping: it feeds the install
that produced the runtime and has no reader at run time."
```

---

## Task 4: Extract instead of discover

**Files:**
- Rewrite: `editors/vscode/src/runtime.ts`
- Test: `editors/vscode/src/test/unit/runtime.test.ts`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `Runtime { python: string; ready: boolean }`; `interpreterPath(root: string, platform: NodeJS.Platform): string`; `runtimeRoot(root: string): string`; `stampPath(root: string): string`; `needsInstall(existing: string | undefined, wanted: string): boolean`; `stampFor(version: string, archive: { name: string; size: number } | undefined): string`; `EnsureOptions`; `ensureRuntime(options: EnsureOptions): Promise<Runtime>`.

Deleted: `MINIMUM_PYTHON`, `meetsMinimum`, `findInterpreter`, `venvPython`.

- [ ] **Step 1: Write the failing test**

Replace `editors/vscode/src/test/unit/runtime.test.ts` with:

```ts
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ensureRuntime, interpreterPath, needsInstall, stampFor, stampPath } from '../../runtime';

const ARCHIVE = { name: 'runtime.tar.gz', size: 40_594_112 };

function options(overrides: Partial<Parameters<typeof ensureRuntime>[0]> = {}) {
  const log: string[] = [];
  const base = {
    root: '/store',
    version: '0.4.1+abc',
    archive: '/ext/bundled/runtime.tar.gz',
    platform: 'linux' as NodeJS.Platform,
    extract: async (from: string, into: string) => {
      log.push(`extract ${from} -> ${into}`);
    },
    makeExecutable: async (path: string) => {
      log.push(`chmod ${path}`);
    },
    remove: async (path: string) => {
      log.push(`remove ${path}`);
    },
    readStamp: async () => undefined,
    writeStamp: async (value: string) => {
      log.push(`stamp ${value}`);
    },
  };
  return { options: { ...base, ...overrides }, log };
}

test('a matching stamp extracts nothing', async () => {
  const { options: given, log } = options({ readStamp: async () => '0.4.1+abc' });
  const runtime = await ensureRuntime(given);
  assert.equal(runtime.ready, true);
  assert.deepEqual(log, []);
});

test('a missing stamp extracts, marks executable and stamps', async () => {
  const { options: given, log } = options();
  const runtime = await ensureRuntime(given);
  assert.equal(runtime.ready, true);
  assert.deepEqual(log, [
    'remove /store/runtime',
    'extract /ext/bundled/runtime.tar.gz -> /store/runtime',
    'chmod /store/runtime/python/bin/python3',
    'stamp 0.4.1+abc',
  ]);
});

test('a stale stamp removes what is there before extracting over it', async () => {
  // A half-extracted tree from an activation the user killed would otherwise be
  // merged with the new one, and the result looks like a complete interpreter.
  const { options: given, log } = options({ readStamp: async () => '0.4.0+old' });
  await ensureRuntime(given);
  assert.equal(log[0], 'remove /store/runtime');
});

test('a failed extraction writes no stamp', async () => {
  const { options: given, log } = options({
    extract: async () => {
      throw new Error('tar: unexpected end of file');
    },
  });
  const runtime = await ensureRuntime(given);
  assert.equal(runtime.ready, false);
  assert.equal(
    log.some((line) => line.startsWith('stamp')),
    false,
  );
});

test('a failed chmod writes no stamp either', async () => {
  // An interpreter that is present and not executable is the failure mode that
  // looks most like success, and a stamp over it never rebuilds.
  const { options: given, log } = options({
    makeExecutable: async () => {
      throw new Error('EPERM');
    },
  });
  const runtime = await ensureRuntime(given);
  assert.equal(runtime.ready, false);
  assert.equal(
    log.some((line) => line.startsWith('stamp')),
    false,
  );
});

test('the interpreter sits where each platform puts it', () => {
  assert.equal(interpreterPath('/store', 'linux'), '/store/runtime/python/bin/python3');
  assert.equal(interpreterPath('/store', 'darwin'), '/store/runtime/python/bin/python3');
  assert.equal(interpreterPath('/store', 'win32'), '/store/runtime/python/python.exe');
});

test('the stamp changes when the archive does', () => {
  assert.notEqual(stampFor('0.4.1', ARCHIVE), stampFor('0.4.1', { ...ARCHIVE, size: ARCHIVE.size + 1 }));
  assert.equal(stampFor('0.4.1', ARCHIVE), stampFor('0.4.1', ARCHIVE));
});

test('an unreadable archive still yields a stamp', () => {
  // The extraction step reports a missing archive far more clearly than a
  // stamp mismatch would, so this must not throw on the way there.
  assert.equal(stampFor('0.4.1', undefined).startsWith('0.4.1+'), true);
});

test('any difference means reinstall, including a downgrade', () => {
  assert.equal(needsInstall('0.4.1+abc', '0.4.1+abc'), false);
  assert.equal(needsInstall('0.5.0+xyz', '0.4.1+abc'), true);
  assert.equal(needsInstall(undefined, '0.4.1+abc'), true);
});

test('the stamp sits beside the runtime, not inside it', () => {
  assert.equal(stampPath('/store'), '/store/installed.txt');
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd editors/vscode && npm run check`
Expected: FAIL — `Module '"../../runtime"' has no exported member 'ensureRuntime'`

- [ ] **Step 3: Rewrite `runtime.ts`**

```ts
/**
 * The interpreter the VSIX carries.
 *
 * There is no interpreter discovery here any more, and that is the point. The
 * extension used to find a `python3` and build a venv in it, which fails in an
 * open-ended number of ways — an unbundled `ensurepip`, PEP 668, a Windows
 * Store stub, a conda environment shadowing what we install. Every one of them
 * is a separate detection rule written against a machine we do not have, and
 * the graceful answer to an open-ended set of environment failures is to stop
 * depending on the environment.
 *
 * What is left is: unpack an archive once per version, mark its interpreter
 * executable, remember that it worked. The workspace's own environment is
 * never touched, and neither is the machine's.
 *
 * Everything that touches the outside world is injected, so every decision here
 * is testable without spawning a process — which matters because the failures
 * that count are the ones where nothing should happen at all.
 */

export interface Runtime {
  python: string;
  ready: boolean;
}

/** Where the archive is unpacked. One directory, replaced wholesale. */
export function runtimeRoot(root: string): string {
  return `${root}/runtime`;
}

/**
 * The bundled interpreter's path.
 *
 * Every python-build-standalone archive unpacks to a single `python/`
 * directory. Windows puts the executable at its root; everyone else under
 * `bin/`, where `python3` is a symlink the archive carries.
 */
export function interpreterPath(root: string, platform: NodeJS.Platform): string {
  const base = `${runtimeRoot(root)}/python`;
  return platform === 'win32' ? `${base}/python.exe` : `${base}/bin/python3`;
}

/**
 * Where the installed-version marker lives — beside the runtime, never inside it.
 *
 * Inside, deleting a broken tree would delete the evidence that it was ever
 * unpacked, and a half-deleted one would keep a stamp claiming it was fine.
 */
export function stampPath(root: string): string {
  return `${root}/installed.txt`;
}

/**
 * Whether the runtime has to be unpacked for `stamp`.
 *
 * Any difference means install, not just an older one: a downgraded extension
 * carries an archive its existing tree has never seen either.
 */
export function needsInstall(existing: string | undefined, wanted: string): boolean {
  return existing !== wanted;
}

/**
 * What the installed runtime must match: the version, and the archive.
 *
 * The version alone is not enough, and that is not hypothetical — a server
 * rebuilt under an unchanged version once left an environment holding a package
 * with no `check.py` in it, and nothing noticed because the number had not
 * moved. The archive is the thing actually installed, so the archive is what is
 * fingerprinted.
 *
 * Name and size rather than a content hash: reading forty megabytes on every
 * activation to detect a change that only happens when the extension is rebuilt
 * would be paying constantly for a rare event.
 *
 * `undefined` when the archive cannot be read, which makes the stamp depend on
 * the version alone. The extraction step reports a missing archive far more
 * clearly than a stamp mismatch ever would.
 */
export function stampFor(version: string, archive: { name: string; size: number } | undefined): string {
  const listed = archive === undefined ? '' : `${archive.name}:${String(archive.size)}`;
  let hash = 0;
  for (let index = 0; index < listed.length; index += 1) {
    hash = (Math.imul(hash, 31) + listed.charCodeAt(index)) | 0;
  }
  // The version leads so that a human opening the file learns something.
  return `${version}+${(hash >>> 0).toString(16)}`;
}

export interface EnsureOptions {
  root: string;
  /** What the installed runtime must match — see `stampFor`. */
  version: string;
  /** The bundled archive, inside the VSIX. */
  archive: string;
  platform: NodeJS.Platform;
  extract: (archive: string, into: string) => Promise<void>;
  makeExecutable: (path: string) => Promise<void>;
  remove: (path: string) => Promise<void>;
  readStamp: () => Promise<string | undefined>;
  writeStamp: (value: string) => Promise<void>;
}

/**
 * The interpreter to run the server with.
 *
 * `ready` false means the caller should report and stay dormant — never
 * half-start. Nothing is written on failure, so the next activation tries again
 * rather than trusting a stamp over a tree that is not there.
 */
export async function ensureRuntime(options: EnsureOptions): Promise<Runtime> {
  const python = interpreterPath(options.root, options.platform);
  if (!needsInstall(await options.readStamp(), options.version)) {
    return { python, ready: true };
  }

  try {
    // Removed rather than extracted over: a half-unpacked tree from an
    // activation the user killed would merge with the new one, and the result
    // looks exactly like a complete interpreter.
    await options.remove(runtimeRoot(options.root));
    await options.extract(options.archive, runtimeRoot(options.root));
    // The archive carries the bit, but a zip in the middle of the delivery
    // chain does not, and an interpreter that is present and not executable is
    // the failure that looks most like success.
    await options.makeExecutable(python);
  } catch {
    // No stamp on failure: a stamp written here is a broken runtime that never
    // rebuilds itself, and the user has no way to learn that is why.
    return { python, ready: false };
  }

  await options.writeStamp(options.version);
  return { python, ready: true };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd editors/vscode && npm run check`
Expected: PASS — `extension.ts` still fails to compile, which Task 5 fixes. If
`npm run check` blocks on that, run `npx tsc --noEmit src/runtime.ts` plus
`node --test out/test/unit/runtime.test.js` for this step and let Task 5's step
restore the full gate.

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/src/runtime.ts editors/vscode/src/test/unit/runtime.test.ts
git commit -m "feat: unpack the interpreter instead of looking for one

Deletes findInterpreter, meetsMinimum, MINIMUM_PYTHON and venvPython.
The predicate they implemented — is there an interpreter, is it new
enough — never answered the question that matters, which is whether it
can build the environment we need; the reported failure had a python3
that passed every check and could not create a venv.

Removes the target directory before extracting: a half-unpacked tree
from a killed activation would merge with the new one and look complete."
```

---

## Task 5: Wire it up, and delete the setting

**Files:**
- Modify: `editors/vscode/src/extension.ts` — `bundledWheels` (lines 55-74), `capture` (150), `probePython` (166-175), `findPython` (177-183), `start` (231-280), and the module-level `venvPython`
- Modify: `editors/vscode/package.json` — remove `pysqlsuggestions.pythonPath`
- Modify: `editors/vscode/src/test/integration/completion.test.ts:67`

**Interfaces:**
- Consumes: `ensureRuntime`, `interpreterPath`, `stampFor`, `stampPath` from Task 4.
- Produces: nothing other tasks read.

- [ ] **Step 1: Replace `bundledWheels` with `bundledRuntime`**

```ts
/**
 * The runtime archive the VSIX carries, by name and size.
 *
 * Undefined when it is unreadable, which makes the stamp depend on the version
 * alone — the right fallback: a missing archive is a broken install that the
 * extraction step reports far more clearly than a stamp mismatch would.
 */
async function bundledRuntime(archive: string): Promise<{ name: string; size: number } | undefined> {
  try {
    return { name: 'runtime.tar.gz', size: (await fs.stat(archive)).size };
  } catch {
    return undefined;
  }
}
```

- [ ] **Step 2: Delete `capture`, `probePython` and `findPython`**

All three exist only to serve interpreter discovery: `capture` has one caller
(`probePython`), which has one caller (`findPython`), which has one caller (the
`ensureVenv` options object). `run` stays — Step 3 gives it a new job.

- [ ] **Step 3: Rewrite the runtime section of `start`**

```ts
  const version = (context.extension.packageJSON as { version: string }).version;
  const archive = vscode.Uri.joinPath(context.extensionUri, 'bundled', 'runtime.tar.gz').fsPath;
  const runtime = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Window, title: 'pysqlsuggestions: unpacking Python…' },
    async () =>
      ensureRuntime({
        root,
        // The archive, not just the version: a server rebuilt under an
        // unchanged version would otherwise leave a runtime holding code the
        // VSIX no longer carries, and nothing would notice.
        version: stampFor(version, await bundledRuntime(archive)),
        archive,
        platform: process.platform,
        extract: (from, into) => extract(from, into),
        makeExecutable: (path) => fs.chmod(path, 0o755),
        remove: (path) => fs.rm(path, { recursive: true, force: true }),
        readStamp: () =>
          fs
            .readFile(stampPath(root), 'utf8')
            .then((value) => value.trim())
            .catch(() => undefined),
        writeStamp: (value) => fs.writeFile(stampPath(root), value, 'utf8'),
      }),
  );

  if (!runtime.ready) {
    status?.set('dormant');
    void vscode.window
      .showErrorMessage(
        'pysqlsuggestions could not unpack its Python runtime. Completion will fall back to the statement alone.',
        'Show logs',
      )
      .then((choice) => {
        if (choice === 'Show logs') {
          output?.show();
        }
      });
    return;
  }

  serverPython = runtime.python;
```

Rename the module-level `venvPython` to `serverPython` and update its two other
uses (`runTest`, and the `ServerOptions` at line 298).

- [ ] **Step 4: Add `extract`**

```ts
/**
 * Unpack a tar.gz using the system `tar`.
 *
 * Every target platform ships one, including Windows 10 1803 and later, which
 * is below VS Code's own floor. It handles what a hand-rolled extractor gets
 * wrong: the symlinks the interpreter's `bin/` and `lib/` are built from,
 * execute bits, and — on macOS — extracting without stamping every file with
 * `com.apple.quarantine`, which is what produces a "cannot be opened because
 * the developer cannot be verified" dialog for a binary the user never chose
 * to run.
 *
 * The one genuine dependency on the machine this design otherwise removes. If a
 * target turns out to lack a usable `tar`, the fallback is vendoring an
 * extractor — about 200 lines of file-format code that has to be right about
 * symlinks and permissions on three operating systems, and not worth paying up
 * front for a program that has shipped with every one of them for years.
 */
async function extract(archive: string, into: string): Promise<void> {
  await fs.mkdir(into, { recursive: true });
  await run('tar', ['-xzf', archive, '-C', into]);
}
```

- [ ] **Step 5: Delete the setting**

Remove the whole `"pysqlsuggestions.pythonPath"` block from
`editors/vscode/package.json`'s `configuration.properties`. Remove line 67 of
`src/test/integration/completion.test.ts`, which sets it, and any now-unused
`python` binding above it.

- [ ] **Step 6: Run the gate**

Run: `cd editors/vscode && npm run check`
Expected: PASS. Then confirm nothing still references the removed names:

```bash
grep -rn "pythonPath\|venvPython\|MINIMUM_PYTHON\|findInterpreter\|meetsMinimum\|ensureVenv" editors/vscode/src editors/vscode/package.json
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add editors/vscode/src/extension.ts editors/vscode/package.json editors/vscode/src/test/integration/completion.test.ts
git commit -m "feat: the extension stops asking the machine for a Python

pysqlsuggestions.pythonPath is removed rather than kept as an escape
hatch. Its only remaining purpose would be 'I have a working interpreter
and object to the download size', and honouring it means keeping every
path this change exists to delete alive and — because almost nobody
would set it — untested. A setting that resurrects the bug on the
machines that set it is worse than no setting.

Extraction uses the system tar: it is the one thing that gets symlinks,
execute bits and macOS quarantine right on all three platforms."
```

---

## Task 6: The build's own guards

**Files:**
- Modify: `tests/test_purity.py` (append)
- Modify: `scripts/build_vsix.py` (`smoke_test`)

**Interfaces:**
- Consumes: `runtime.TARGETS`, `RUNTIME`.
- Produces: `smoke_test(target: str, staging: Path) -> None` called from `pack_runtime`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_purity.py`:

```python
def test_the_extension_declares_no_python_requirement_it_no_longer_has() -> None:
    """
    The VSIX carries its interpreter, so nothing may still tell a user to install one.

    Left behind, the README and the settings schema keep describing an extension
    that stopped existing — and a requirement a user cannot satisfy is worse
    than no documentation, because they will go and satisfy it.
    """
    package = json.loads((ROOT / 'editors' / 'vscode' / 'package.json').read_text(encoding='utf-8'))
    settings = package['contributes']['configuration']['properties']
    assert 'pysqlsuggestions.pythonPath' not in settings
    readme = (ROOT / 'editors' / 'vscode' / 'README.md').read_text(encoding='utf-8')
    assert 'on your PATH' not in readme


def test_the_lock_names_a_runtime_for_every_target_the_build_packages() -> None:
    """
    Two lists that must agree, in two files, neither of which reads the other.

    `TARGETS` decides what `vsce package --target` is invoked for and the lock
    decides what can be fetched, so a target in one and not the other is a build
    that fails nine-ninths of the way through.
    """
    from scripts.runtime import TARGETS, read_lock, verify_lock

    assets = read_lock((ROOT / 'scripts' / 'runtime.lock').read_text(encoding='utf-8'))
    assert set(assets) == set(TARGETS)
    assert verify_lock(assets) == []
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_purity.py -q`
Expected: FAIL on the README assertion until Task 7 rewrites it; PASS on the
others. Leave it failing and let Task 7 close it — or reorder Task 7 before this
step if the executor prefers a green gate at every commit.

- [ ] **Step 3: Add the build-time smoke test**

In `scripts/build_vsix.py`, add and call from `pack_runtime` after
`install_into`:

```python
def smoke_test(target: str, staging: Path) -> None:
    """
    Prove the packed runtime holds what it claims, and run it where we can.

    Structural for eight of the nine: the interpreter cannot execute on the
    machine building it, so what is checkable is that the binary exists, that it
    is executable, and that the distributions landed. A test that pretended to
    do more would be lying about eight of nine cases.

    For the host target it goes further and imports the server, which is the
    only check that catches an install that produced files nothing can load.
    """
    binary = staging / 'python' / ('python.exe' if target.startswith('win32-') else 'bin/python3')
    if not binary.exists():
        message = f'{target}: no interpreter at {binary}'
        raise SystemExit(message)

    packages = runtime.site_packages(staging, target)
    for expected in ('pysqlsuggestions', 'pysqlsuggestions_lsp', 'pygls', 'pg8000'):
        if not any(packages.glob(f'{expected}-*.dist-info')):
            message = f'{target}: {expected} is not installed in {packages}'
            raise SystemExit(message)

    if target != HOST_TARGET:
        return
    # The entry-point check matters more than the import: the dialect registry
    # resolves through the `pysqlsuggestions.dialects` group, and a .dist-info
    # lost in a `--target` install makes `named()` return None with no other
    # symptom than a completion list that stops knowing any keywords.
    subprocess.run(
        [
            str(binary),
            '-c',
            'import pysqlsuggestions_lsp;'
            'from pysqlsuggestions.dialects.registry import named;'
            "assert named('clickhouse') is not None, 'entry points did not survive the install'",
        ],
        check=True,
    )
```

Define `HOST_TARGET` beside the other constants, derived from `sys.platform` and
`platform.machine()`; when the host is not one of the nine, set it to `None` so
the execute step is simply skipped.

- [ ] **Step 4: Rebuild one target and confirm the smoke test runs**

Run: `uv run --with pip python -m scripts.build_vsix --target linux-x64`
Expected: PASS, with no output from the smoke test (it is silent on success).
Then prove it bites: temporarily change `'pg8000'` to `'pg9000'` in the expected
tuple, rerun, expect `SystemExit: linux-x64: pg9000 is not installed`. Revert.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_vsix.py tests/test_purity.py
git commit -m "test: the build checks what it packed, honestly

Structural for eight of nine targets — their interpreters cannot run on
the machine building them, and a test pretending otherwise would be
lying about most of its cases. The host target additionally resolves a
dialect through its entry points, which is the failure a --target
install can produce with no other symptom."
```

---

## Task 7: Say what the extension now needs, which is nothing

**Files:**
- Modify: `editors/vscode/README.md` — the requirements section (line 22 area) and the settings table (line 74)
- Modify: `CHANGELOG.md` under `## Unreleased`

**Interfaces:**
- Consumes: everything above.
- Produces: closes the assertion Task 6 Step 2 left failing.

- [ ] **Step 1: Rewrite the requirements section**

`editors/vscode/README.md` currently says "Python 3.10 or newer on your PATH"
and points at `pysqlsuggestions.pythonPath`. Replace that passage with:

```markdown
## What you need

Nothing. The extension carries its own Python — a stripped CPython 3.13 built by
[python-build-standalone](https://github.com/astral-sh/python-build-standalone),
with the language server already installed into it. It is unpacked once, into
the extension's own storage, on first activation.

Your machine's Python is never looked at, let alone used. There is no venv, no
`pip` at install time, and no network: a first run on a train works.

This is why there is one download per platform rather than one for everyone. Pick
the build for your OS and architecture, or let the marketplace pick it for you.
```

- [ ] **Step 2: Fix the settings table**

Delete the `pysqlsuggestions.pythonPath` row from the table at line 74. Add, if
Task 6 of the readers plan has not already:

```markdown
| `pysqlsuggestions.connections[].secure` | speak TLS to this connection; Trino requires it before it will accept a password |
```

- [ ] **Step 3: Write the changelog entry**

Under `## Unreleased`:

```markdown
### The extension carries its own Python

There is no interpreter to install and no setting pointing at one. Each build
ships a stripped CPython 3.13 with the server already inside it, unpacked once
into the extension's storage on first activation.

This fixes a failure that had no graceful answer before: an interpreter that is
present and new enough and still cannot build a virtual environment. Debian
unbundles `ensurepip` from `python3.13`, so `python3 -m venv` fails there with a
message about `apt install python3.13-venv` — and PEP 668, the Windows Store
stub and a shadowing conda environment each fail differently in the same place.
The extension no longer asks.

`pysqlsuggestions.pythonPath` is **removed**. Keeping it would mean keeping
interpreter discovery, venv creation and the whole matrix of environment
failures alive on the machines that set it, and almost nobody would.

The download is now per platform — nine builds rather than one, between about
21 MB and 40 MB. The marketplace picks the right one; an installation from a
`.vsix` file needs the one matching your OS and architecture.
```

- [ ] **Step 4: Run both gates**

Run: `./scripts/check.sh` then `cd editors/vscode && npm run check`
Expected: PASS, including the README assertion from Task 6.

- [ ] **Step 5: Commit**

```bash
git add editors/vscode/README.md CHANGELOG.md
git commit -m "docs: the extension needs nothing installed

The README asked for Python 3.10 on PATH, which is now both false and
actively unhelpful — a requirement a user cannot satisfy is worse than
no documentation, because they go and satisfy it."
```

---

## Task 8: Build all nine and check them

**Files:** none modified. This task produces artifacts and a verdict.

- [ ] **Step 1: Build every target**

Run: `uv run --with pip python -m scripts.build_vsix`
Expected: `9 VSIXes`, and nine `.vsix` files in `editors/vscode/`.

- [ ] **Step 2: Check each one's shape**

```bash
for vsix in editors/vscode/pysqlsuggestions-*.vsix; do
  printf '%s  %s MB  runtime=%s  wheels=%s\n' \
    "$(basename "$vsix")" \
    "$(du -m "$vsix" | cut -f1)" \
    "$(unzip -l "$vsix" | grep -c 'runtime\.tar\.gz')" \
    "$(unzip -l "$vsix" | grep -c '\.whl')"
done
```

Expected: nine rows, each `runtime=1 wheels=0`, sizes between roughly 21 and 40
MB. A row reporting `runtime=0` is a target whose pack step silently reused a
previous target's leftover archive — investigate before publishing anything.

- [ ] **Step 3: Install the host build and use it**

```bash
code --install-extension editors/vscode/pysqlsuggestions-*-linux-x64.vsix --force
rm -rf ~/.config/Code/User/globalStorage/pysqlsuggestions.pysqlsuggestions
```

Then open a `.sql` file, and with `docker compose -f docker/docker-compose.yml up -d --wait`
running, confirm against a ClickHouse profile that `SELECT * FROM ` offers real
relations. That single check exercises the whole chain this plan and its sibling
built: unpacked interpreter, entry-point dialect resolution, stdlib reader,
catalog read.

Removing globalStorage first is not optional — an existing stamp from the venv
era names a file layout that no longer exists.

- [ ] **Step 4: Commit nothing; report**

There is nothing to commit. Report the nine sizes and the result of Step 3.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| §3 nine targets, triples, sizes, CPython 3.13, stripped variant | 1 |
| §3 server pre-installed at build time, tarball not a tree | 2 |
| §4 stamp/extract/spawn; `findInterpreter` etc. deleted | 4 |
| §4 system `tar`, with its fallback named | 5 |
| §4 `pythonPath` removed, with a `CHANGELOG` entry and a README rewrite | 5, 7 |
| §6 target loop, digest-pinned lock, cache, `--no-index` | 1, 2, 3 |
| §8 the two failure rows that go away, and the one that arrives | 5 (the dormant path's message) |
| §9 per-target smoke test: executes for the host, structural for the rest | 6 |
| §9 structural guards: lock digests, one `runtime.tar.gz` per VSIX | 6, 8 |
| §9 `runtime.ts` tests lose discovery, gain stamp-vs-extract | 4 |

**Type consistency:** `Asset`, `TARGETS`, `PYTHON_VERSION`, `site_packages` and
`python_platform` are defined in Task 1 and used by exactly those names in
Tasks 2, 3 and 6. `ensureRuntime`'s `EnsureOptions` fields — `extract`,
`makeExecutable`, `remove`, `readStamp`, `writeStamp` — are declared in Task 4
and supplied under the same names in Task 5. `stampFor` takes a single optional
archive record in both.

**One ordering wrinkle, stated rather than hidden:** Task 6 Step 2 fails on a
README assertion that Task 7 fixes. That is deliberate — the guard belongs with
the build's other guards and the prose belongs with the prose — but an executor
who wants a green gate at every commit should run Task 7 before Task 6.

**Not covered here, and not a gap:** §9's marketplace publishing pipeline. Nine
VSIXes per release is `vsce publish --target` nine times with a PAT, which is
release mechanics rather than implementation, and the spec's open question #2
already carries it.
