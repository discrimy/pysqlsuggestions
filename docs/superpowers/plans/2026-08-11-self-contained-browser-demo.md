# Self-contained browser demo — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The published demo loads the Pyodide runtime from its own origin, so the page depends on no host but the one serving it.

**Architecture:** A new `scripts/vendor_pyodide.py` fetches five runtime files, verified against a committed sha256 lock, into a gitignored cache. `scripts/build_pages.py` copies them into `site/pyodide/` and then refuses to write a site containing any absolute URL. `demo/static/browser.js` points at the vendored copy and installs the wheel with `unpackArchive` instead of `micropip`.

**Tech Stack:** Python ≥3.10, standard library only (`urllib.request`, `hashlib`). Pyodide 0.28.3. uv, ruff, mypy strict, pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-self-contained-browser-demo-design.md`

## Global Constraints

- **Zero runtime dependencies**, and the build must not introduce one either — downloads use `urllib.request`, never `requests`.
- **ruff:** line length 120, single quotes, `D` docstring rules. `scripts/*` already has `T201` ignored in `pyproject.toml`, so `print()` needs no `# noqa`.
- **mypy strict.** `files = ['src', 'tests']`, but mypy follows imports, so anything a test imports from `scripts/` is type-checked too. `scripts/build_pages.py` is already strict-clean; keep the new module that way.
- **`scripts/` is an implicit namespace package.** `import scripts.vendor_pyodide` resolves from tests with no `__init__.py` and no `pythonpath` setting — verified. It resolves inside `build_pages.py` **only** under `python -m scripts.build_pages`, which is why Task 2 changes the documented invocation.
- **Pyodide version is `0.28.3`** and does not change in this work.
- **Verification:** `./scripts/check.sh`. Offline subset: `uv run pytest -m 'not integration'`.

---

## File Structure

**Created**
- `scripts/vendor_pyodide.py` — verified Pyodide bytes in a cache directory. Network, hashing, the lock.
- `scripts/pyodide.lock` — committed sha256 digests, `sha256sum` format.
- `tests/test_vendor_pyodide.py` — the vendor module, offline via an injected downloader.
- `tests/test_build_pages.py` — the external-URL gate.

**Modified**
- `scripts/build_pages.py` — call `vendor()`, add the gate, split the size report, rewrite the docstring.
- `demo/static/browser.js` — relative runtime URL, `unpackArchive` instead of `micropip`.
- `.gitignore` — `.pyodide/`.
- `.github/workflows/pages.yml` — cache `.pyodide/`, invoke via `-m`.
- `README.md` — the browser-demo section: self-contained, `python3`, `-m`.

---

## Task 1: The vendor module

**Files:**
- Create: `scripts/vendor_pyodide.py`, `scripts/pyodide.lock`
- Test: `tests/test_vendor_pyodide.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PYODIDE_VERSION: str`, `FILES: tuple[str, ...]`, `CACHE: Path`, `LOCK: Path`, `Downloader = Callable[[str], bytes]`, `vendor(destination: Path, *, download: Downloader = _fetch) -> None`, `update(*, download: Downloader = _fetch) -> None`, `read_lock() -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vendor_pyodide.py`:

```python
"""The vendoring step, offline. No test here reaches the network."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import vendor_pyodide

RUNTIME = b'not really a wasm runtime, but bytes are bytes'
DIGEST = hashlib.sha256(RUNTIME).hexdigest()


def lock_for(names: tuple[str, ...], digest: str) -> str:
    """A lock file naming every file with the same digest, in `sha256sum` format."""
    return ''.join(f'{digest}  {name}\n' for name in names)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module's cache and lock at a temporary directory."""
    lock = tmp_path / 'pyodide.lock'
    lock.write_text(lock_for(vendor_pyodide.FILES, DIGEST))
    monkeypatch.setattr(vendor_pyodide, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(vendor_pyodide, 'LOCK', lock)
    return tmp_path


def test_a_digest_that_does_not_match_stops_the_build(sandbox: Path) -> None:
    """
    The whole point of the lock is that nobody has to notice.

    A silent overwrite would publish whatever arrived, under our name.
    """
    with pytest.raises(SystemExit) as raised:
        vendor_pyodide.vendor(sandbox / 'out', download=lambda url: b'something else entirely')
    message = str(raised.value)
    assert 'pyodide.mjs' in message
    assert DIGEST in message


def test_a_matching_cached_file_is_never_downloaded(sandbox: Path) -> None:
    """The property that keeps a second build, and an offline build, working."""
    cache = sandbox / 'cache'
    cache.mkdir()
    for name in vendor_pyodide.FILES:
        (cache / name).write_bytes(RUNTIME)

    def refuse(url: str) -> bytes:
        raise AssertionError(f'downloaded {url} when the cache was good')

    vendor_pyodide.vendor(sandbox / 'out', download=refuse)
    assert sorted(p.name for p in (sandbox / 'out').iterdir()) == sorted(vendor_pyodide.FILES)


def test_a_download_fills_the_cache_and_the_destination(sandbox: Path) -> None:
    """A cold build fetches once; the copy in the cache is what makes the next one free."""
    calls: list[str] = []

    def record(url: str) -> bytes:
        calls.append(url)
        return RUNTIME

    vendor_pyodide.vendor(sandbox / 'out', download=record)
    assert len(calls) == len(vendor_pyodide.FILES)
    assert (sandbox / 'cache' / 'pyodide.asm.wasm').read_bytes() == RUNTIME
    assert (sandbox / 'out' / 'pyodide.asm.wasm').read_bytes() == RUNTIME


def test_a_lock_missing_an_entry_says_to_update(sandbox: Path) -> None:
    """A file added to FILES without a digest must not be fetched unverified."""
    (sandbox / 'pyodide.lock').write_text(lock_for(vendor_pyodide.FILES[:2], DIGEST))
    with pytest.raises(SystemExit) as raised:
        vendor_pyodide.vendor(sandbox / 'out', download=lambda url: RUNTIME)
    assert '--update' in str(raised.value)


def test_update_rewrites_the_lock_in_file_order(sandbox: Path) -> None:
    """A version bump should read as a diff, which means a stable order."""
    (sandbox / 'pyodide.lock').write_text('')
    vendor_pyodide.update(download=lambda url: RUNTIME)
    written = (sandbox / 'pyodide.lock').read_text()
    assert written == lock_for(vendor_pyodide.FILES, DIGEST)
    assert vendor_pyodide.read_lock() == dict.fromkeys(vendor_pyodide.FILES, DIGEST)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_vendor_pyodide.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.vendor_pyodide'`

- [ ] **Step 3: Write the module**

Create `scripts/vendor_pyodide.py`:

```python
"""
Fetch the Pyodide runtime the browser demo needs, verified against a lock file.

    python3 scripts/vendor_pyodide.py            # fill and check the cache
    python3 scripts/vendor_pyodide.py --update   # upgrading: rewrite the lock

`build_pages.py` calls `vendor()` to copy the cached files into `site/pyodide/`,
which is what makes the published page reach no host but the one serving it.

Kept out of that script because the failure modes have nothing in common. This
one deals with the network, a truncated download and a digest that does not
match; that one cannot fail except on a missing or stale wheel.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

PYODIDE_VERSION = '0.28.3'
BASE = f'https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/'

FILES = ('pyodide.mjs', 'pyodide.asm.js', 'pyodide.asm.wasm', 'python_stdlib.zip', 'pyodide-lock.json')
"""
What a pure-Python payload needs, and nothing more.

No package wheels. The page installs one wheel of this library, which is pure
Python and declares no dependencies, so `micropip` and the resolution behind it
buy nothing that `pyodide.unpackArchive` does not — see `demo/static/browser.js`.
"""

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.pyodide' / PYODIDE_VERSION
"""Gitignored. The version is in the path so a bump cannot read the last one's bytes."""
LOCK = Path(__file__).resolve().parent / 'pyodide.lock'

Downloader = Callable[[str], bytes]
"""Passed in only so the tests can supply one and never reach the network."""


def _fetch(url: str) -> bytes:
    """Read a URL whole. The only network access in this repository."""
    with urllib.request.urlopen(url) as response:
        return bytes(response.read())


def read_lock() -> dict[str, str]:
    """The expected digests. `sha256sum` format, so `sha256sum -c` works by hand."""
    entries: dict[str, str] = {}
    for line in LOCK.read_text().splitlines():
        if line.strip():
            digest, name = line.split()
            entries[name] = digest
    return entries


def _write_lock(digests: dict[str, str]) -> None:
    """Rewrite the lock in `FILES` order, so an upgrade reads as a diff."""
    LOCK.write_text(''.join(f'{digests[name]}  {name}\n' for name in FILES))


def _cached(name: str, expected: str) -> bytes | None:
    """The cached bytes when they match `expected`, else None."""
    path = CACHE / name
    if not path.exists():
        return None
    data = path.read_bytes()
    return data if hashlib.sha256(data).hexdigest() == expected else None


def vendor(destination: Path, *, download: Downloader = _fetch) -> None:
    """
    Copy the verified runtime into `destination`, fetching whatever the cache lacks.

    Raises SystemExit rather than returning a status, because every caller is a
    build step whose only sensible response is to stop.
    """
    expected = read_lock()
    missing = [name for name in FILES if name not in expected]
    if missing:
        raise SystemExit(f'{LOCK.name} has no entry for {", ".join(missing)} — run with --update')

    CACHE.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        data = _cached(name, expected[name])
        if data is None:
            data = download(BASE + name)
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected[name]:
                raise SystemExit(
                    f'{name} does not match {LOCK.name}\n'
                    f'  expected {expected[name]}\n'
                    f'  received {digest}',
                )
            (CACHE / name).write_bytes(data)
        (destination / name).write_bytes(data)


def update(*, download: Downloader = _fetch) -> None:
    """
    Download without checking and rewrite the lock. The one path that trusts the network.

    Run deliberately when upgrading Pyodide. Its output is a five-line diff, which
    is the whole point: what gets served under our name changes only on purpose.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for name in FILES:
        data = download(BASE + name)
        (CACHE / name).write_bytes(data)
        digests[name] = hashlib.sha256(data).hexdigest()
        print(f'  {name}: {len(data) // 1024} kB')
    _write_lock(digests)
    print(f'wrote {LOCK}')


def main(argv: Sequence[str]) -> int:
    """Fill and verify the cache. Returns a process exit status."""
    if '--update' in argv:
        update()
        return 0
    vendor(CACHE)
    print(f'{len(FILES)} files verified in {CACHE}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Bootstrap the real lock**

This is the one step that needs network.

```bash
uv run python scripts/vendor_pyodide.py --update
cat scripts/pyodide.lock
```

Expected: five lines, and the cache populated at `.pyodide/0.28.3/`. Sanity-check the sizes it prints — `pyodide.asm.wasm` should be about 8.2 MiB and `python_stdlib.zip` about 2.3 MiB. Anything wildly different means the CDN served something unexpected and the lock should not be committed.

- [ ] **Step 5: Verify the lock by hand**

```bash
(cd .pyodide/0.28.3 && sha256sum -c ../../scripts/pyodide.lock)
```

Expected: five `OK` lines. This proves the format claim in the docstring rather than assuming it.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_vendor_pyodide.py -v && uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/vendor_pyodide.py scripts/pyodide.lock tests/test_vendor_pyodide.py
git commit -m "feat: the Pyodide runtime, fetched once and pinned by digest"
```

---

## Task 2: Vendor into the site, and point the page at it

**Files:**
- Modify: `scripts/build_pages.py:1-15` (docstring), `:46-100` (main), `demo/static/browser.js:11` and `:27-31`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `vendor(destination, *, download)`, `PYODIDE_VERSION` from Task 1.
- Produces: `site/pyodide/` containing the five files; a page that boots from it.

These land together because neither works alone: `browser.js` pointing at `./pyodide/` needs the files there, and vendoring without repointing the page changes nothing.

- [ ] **Step 1: Ignore the cache**

Add to `.gitignore`, under the existing "Built by" comment block:

```
# Fetched by scripts/vendor_pyodide.py
.pyodide/
```

- [ ] **Step 2: Rewrite the build's docstring**

In `scripts/build_pages.py`, replace the first paragraph after the usage block. It currently reads:

> Pages serves files and nothing else, so the site carries everything the page
> needs: the wheel and the three demo modules Pyodide imports, `demo/schema.py` among
> them. Pyodide itself comes from a CDN — it is about ten megabytes and
> versioned, so vendoring it would dwarf everything else here.

with:

```
Pages serves files and nothing else, so the site carries everything the page
needs: the wheel, the three demo modules Pyodide imports — `demo/schema.py`
among them — and Pyodide itself.

The runtime is 11.7 MiB against a demo payload of 135 kB, which is why it used
to come from a CDN. It is carried now because the page's whole argument is that
this library needs nothing at run time, and a page that cannot start without
reaching somebody else's host is a poor way to make it. `vendor_pyodide.py`
fetches it once, against a pinned digest.
```

Also update the usage block in that docstring to the invocation Task 4 documents:

```
    uv build --wheel
    uv run python -m scripts.build_pages
    python3 -m http.server -d site 8001     # to check it locally
```

- [ ] **Step 3: Call the vendor step**

Add the import beside the others in `scripts/build_pages.py`:

```python
from scripts.vendor_pyodide import PYODIDE_VERSION, vendor
```

and call it after the wheel is copied, before the `index.html` rewrite:

```python
    vendor(SITE / 'pyodide')
```

- [ ] **Step 4: Split the size report**

Replace the closing report (currently the last three lines before `return 0`):

```python
    demo = [f for f in SITE.iterdir() if f.is_file()]
    payload = sum(f.stat().st_size for f in demo) // 1024
    runtime = sum(f.stat().st_size for f in (SITE / 'pyodide').iterdir()) / 1024 / 1024
    # Reported apart so a jump in our own payload stays visible next to a
    # constant 11.7 MiB. Added together, the demo's size would never move again.
    print(f'site/ built: {len(demo)} files, {payload} kB + {runtime:.1f} MiB Pyodide {PYODIDE_VERSION}')
    return 0
```

- [ ] **Step 5: Point the page at the vendored copy**

In `demo/static/browser.js`, replace line 11:

```js
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
```

with:

```js
// Vendored beside this file by scripts/vendor_pyodide.py. The page reaches no
// host but the one serving it, which is the claim the demo exists to make.
const PYODIDE = new URL('./pyodide/', import.meta.url).href;
```

- [ ] **Step 6: Install the wheel without micropip**

In the same file, replace these four lines:

```js
  say('installing pysqlsuggestions…');
  await py.loadPackage('micropip');
  const micropip = py.pyimport('micropip');
  // Wheel and demo sources sit beside this file; the build step puts them there.
  await micropip.install(new URL('./pysqlsuggestions-0.1.1-py3-none-any.whl', import.meta.url).href);
```

with:

```js
  say('installing pysqlsuggestions…');
  // A wheel is a zip and this one is pure Python with no dependencies, so
  // unpacking it onto sys.path is the whole install. micropip would add a
  // package download and a resolver to reach the same place — and did, until it
  // failed to fetch one morning and left the page booted with a dead editor.
  //
  // Unpacked into a directory we name rather than site-packages, whose real path
  // carries the interpreter version and would break silently on an upgrade.
  const wheel = await fetch(new URL('./pysqlsuggestions-0.1.1-py3-none-any.whl', import.meta.url));
  py.unpackArchive(await wheel.arrayBuffer(), 'zip', { extractDir: '/wheel' });
  py.runPython('import sys; sys.path.insert(0, "/wheel")');
```

**Do not change the wheel filename** — `build_pages.py` rewrites it on every build and fails when it finds none to rewrite, so the literal must stay in the shape its regex matches (`pysqlsuggestions-<version>-py3-none-any.whl`).

- [ ] **Step 7: Build and confirm the runtime is there**

```bash
uv build --wheel
uv run python -m scripts.build_pages
ls -la site/pyodide/
du -sh site/
```

Expected: the report names both sizes; `site/pyodide/` holds five files; `site/` totals about 12 MiB.

- [ ] **Step 8: Confirm nothing external remains**

```bash
grep -rE 'https?://' site/ --include='*.html' --include='*.js' --include='*.py' || echo 'no external references'
```

Expected: `no external references`. `site/pyodide/` is excluded by the `--include` filters, and deliberately: `pyodide-lock.json` names package URLs we never fetch.

- [ ] **Step 9: Run the checks**

Run: `uv run pytest -m 'not integration' -q && uv run mypy && uv run ruff check .`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add .gitignore scripts/build_pages.py demo/static/browser.js
git commit -m "feat: the demo carries its own runtime, and installs without micropip"
```

---

## Task 3: The gate

**Files:**
- Modify: `scripts/build_pages.py`
- Test: `tests/test_build_pages.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `external_references(directory: Path) -> list[str]` in `scripts/build_pages.py`, returning `'<name>: <url>'` strings.

Ordering matters: this comes after Task 2. Added before it, the gate would fail on the CDN constant `browser.js` still contains.

- [ ] **Step 1: Write the failing test**

Create `tests/test_build_pages.py`:

```python
"""The gate that keeps the published page self-contained."""

from __future__ import annotations

from pathlib import Path

from scripts.build_pages import external_references


def test_an_absolute_url_is_reported(tmp_path: Path) -> None:
    """The one thing this feature exists to prevent, in the file type most likely to carry it."""
    (tmp_path / 'browser.js').write_text("const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';\n")
    found = external_references(tmp_path)
    assert found == ['browser.js: https://cdn.jsdelivr.net/pyodide/v0.28.3/full/']


def test_a_self_contained_directory_is_clean(tmp_path: Path) -> None:
    """Relative URLs are the whole point and must not be mistaken for external ones."""
    (tmp_path / 'browser.js').write_text("const PYODIDE = new URL('./pyodide/', import.meta.url).href;\n")
    (tmp_path / 'index.html').write_text('<script type="module" src="./browser.js"></script>\n')
    (tmp_path / 'schema.py').write_text("SCHEMA = {('public', 'flight'): []}\n")
    assert external_references(tmp_path) == []


def test_the_demo_sources_name_no_external_host() -> None:
    """
    Asserted against the sources, not only the build output.

    The gate runs at build time, which is the last moment before publishing and a
    long way from the edit that would trip it. This fails in the ordinary test run
    instead, so a CDN reintroduced during development is caught the same afternoon.
    """
    assert external_references(Path(__file__).resolve().parents[1] / 'demo' / 'static') == []


def test_plain_http_counts_too(tmp_path: Path) -> None:
    """`https` is the likely mistake; `http` is the same mistake and easier to miss."""
    (tmp_path / 'index.html').write_text('<img src="http://example.invalid/logo.png">\n')
    assert external_references(tmp_path) == ['index.html: http://example.invalid/logo.png']


def test_the_runtime_directory_is_not_scanned(tmp_path: Path) -> None:
    """
    `pyodide-lock.json` names package URLs the page never fetches.

    Scanning it would fail every build over strings nothing reads.
    """
    runtime = tmp_path / 'pyodide'
    runtime.mkdir()
    (runtime / 'pyodide-lock.json').write_text('{"packages": {"x": {"file_name": "https://example.invalid/x.whl"}}}')
    assert external_references(tmp_path) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_build_pages.py -v`
Expected: FAIL — `ImportError: cannot import name 'external_references'`

- [ ] **Step 3: Implement the gate**

Add to `scripts/build_pages.py`, above `main()`:

```python
SCANNED = ('.html', '.js', '.py')
"""
Extensions a browser executes or imports from this site.

Not the runtime directory: `pyodide-lock.json` names a URL per package, none of
which this page fetches, and scanning it would fail every build over strings
nothing reads.
"""

EXTERNAL = re.compile(r'https?://[^\s\'"()]+')


def external_references(directory: Path) -> list[str]:
    """
    Absolute URLs in the files this site executes, as `name: url`.

    Empty is the invariant. A page that reaches another host to start is a page
    whose availability is somebody else's, and this demo's whole argument is that
    the library needs nothing at run time.
    """
    found: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix in SCANNED:
            found += [f'{path.name}: {url}' for url in EXTERNAL.findall(path.read_text())]
    return found
```

`re` is already imported at the top of the file.

- [ ] **Step 4: Wire it into the build**

In `main()`, immediately before the size report:

```python
    reaching = external_references(SITE)
    if reaching:
        print('site/ would reach another host:', file=sys.stderr)
        for reference in reaching:
            print(f'  {reference}', file=sys.stderr)
        return 1
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_build_pages.py -v && uv run python -m scripts.build_pages`
Expected: tests PASS, build exits zero.

- [ ] **Step 6: Prove the gate actually bites**

A gate nobody has watched fail is a gate nobody knows works. The URL goes in the
*source*, because the build rewrites `site/` from source on every run and editing
the output would prove nothing.

```bash
cp demo/static/browser.js /tmp/browser.js.bak
echo "const CDN = 'https://cdn.jsdelivr.net/x';" >> demo/static/browser.js
uv run python -m scripts.build_pages; echo "exit=$?"
cp /tmp/browser.js.bak demo/static/browser.js
uv run python -m scripts.build_pages; echo "restored exit=$?"
```

Expected: `exit=1` with a message naming `browser.js` and the URL, then
`restored exit=0`. Confirm `git diff --stat demo/static/browser.js` is empty
before moving on.

- [ ] **Step 7: Rebuild clean and run the checks**

Run: `uv run python -m scripts.build_pages && uv run pytest -m 'not integration' -q && uv run mypy && uv run ruff check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add scripts/build_pages.py tests/test_build_pages.py
git commit -m "feat: the build refuses to publish a page that reaches another host"
```

---

## Task 4: CI and documentation

**Files:**
- Modify: `.github/workflows/pages.yml`, `README.md`

**Interfaces:**
- Consumes: `scripts/pyodide.lock` (cache key), the `-m` invocation from Task 2.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Cache the runtime in CI**

In `.github/workflows/pages.yml`, insert before the `Assemble site/` step:

```yaml
      # The runtime is 11.7 MiB fetched from a CDN, and the lock is what makes
      # reusing it safe: a cache hit is only a cache hit for digests we pinned.
      - name: Cache the Pyodide runtime
        uses: actions/cache@v4
        with:
          path: .pyodide
          key: pyodide-${{ hashFiles('scripts/pyodide.lock') }}
```

- [ ] **Step 2: Update the build invocation**

In the same file, change the `Assemble site/` step's command:

```yaml
        run: uv run --no-project python -m scripts.build_pages
```

`-m` is required: `build_pages.py` now imports `scripts.vendor_pyodide`, which only resolves when the repository root is on `sys.path`, and running a script by path puts `scripts/` there instead.

- [ ] **Step 3: Update the README**

In `README.md`, replace the browser-demo command block:

```bash
uv build --wheel
uv run python -m scripts.build_pages
python3 -m http.server -d site 8001
```

and add, after the paragraph beginning "The same page, with no server and no database":

```markdown
The page reaches nothing. Pyodide is carried in `site/` rather than fetched from
a CDN, pinned by digest in `scripts/pyodide.lock`, and the build refuses to
assemble a site whose files name any absolute URL. That costs 11.7 MiB against a
demo payload of 135 kB, and buys a page that works on an air-gapped laptop and
cannot be broken by somebody else's outage — which is the claim the demo exists
to make.
```

- [ ] **Step 4: Check the README's own commands**

```bash
grep -n 'python -m http.server\|python scripts/build_pages' README.md scripts/build_pages.py
```

Expected: no matches. There is no `python` on a Debian-family machine without `python-is-python3`; the bare form fails with exit 127, which is how this was noticed.

- [ ] **Step 5: Validate the workflow file parses**

```bash
python3 -c "import sys,pathlib; print('yaml ok' if pathlib.Path('.github/workflows/pages.yml').read_text() else '')"
uv run python -c "
import re, pathlib
text = pathlib.Path('.github/workflows/pages.yml').read_text()
assert 'actions/cache@v4' in text, 'cache step missing'
assert '-m scripts.build_pages' in text, 'invocation not updated'
print('workflow updated')
"
```

Expected: `workflow updated`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/pages.yml README.md
git commit -m "docs: the demo reaches nothing, and says so"
```

---

## Task 5: Prove it offline

**Files:** none modified — this is the acceptance step.

**Interfaces:**
- Consumes: everything above.

The gate proves no file *names* another host. This proves the page *runs* without one, which is the property a visitor actually experiences.

- [ ] **Step 1: Build and serve**

```bash
uv build --wheel
uv run python -m scripts.build_pages
python3 -m http.server -d site 8001 &
sleep 1 && curl -sf -o /dev/null -w 'site: %{http_code}\n' http://localhost:8001/index.html
```

Expected: `site: 200`.

- [ ] **Step 2: Load the page with every external host unreachable**

`--host-resolver-rules` makes Chrome fail DNS for everything except localhost, which is a truer test than unplugging the network — the page cannot reach a CDN even if one is cached.

```bash
google-chrome --headless=new --no-sandbox --disable-gpu \
  --user-data-dir=/tmp/pyodide-offline-profile \
  --host-resolver-rules='MAP * ~NOTFOUND, EXCLUDE localhost' \
  --virtual-time-budget=120000 --dump-dom http://localhost:8001/index.html > /tmp/offline-dom.html
grep -o 'class="boot"[^>]*' /tmp/offline-dom.html
```

Expected: `class="boot" id="boot" data-done="yes"` — Pyodide booted with no external host reachable. Anything else means something is still being fetched.

- [ ] **Step 3: Drive it to a suggestion**

A boot that reaches `done` proves the runtime loaded. This proves the wheel installed and the engine answers — the part `unpackArchive` replaced.

```bash
google-chrome --headless=new --no-sandbox --disable-gpu \
  --user-data-dir=/tmp/pyodide-offline-profile \
  --host-resolver-rules='MAP * ~NOTFOUND, EXCLUDE localhost' \
  --remote-debugging-port=9223 http://localhost:8001/index.html &
sleep 25
```

Then, with Node 24 (its `WebSocket` is global, so this needs no packages):

```javascript
// /tmp/drive-offline.mjs
const list = await (await fetch('http://127.0.0.1:9223/json/list')).json();
const page = list.find(t => t.type === 'page' && t.url.includes('8001'));
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = e => { const d = JSON.parse(e.data); if (pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const evaluate = expr => new Promise(res => { const n = ++id; pending.set(n, d => res(d.result?.result?.value));
  ws.send(JSON.stringify({ id: n, method: 'Runtime.evaluate', params: { expression: expr, awaitPromise: true, returnByValue: true } })); });

console.log(await evaluate(`(async () => {
  const end = Date.now() + 120000;
  while (Date.now() < end && document.getElementById('boot')?.dataset.done !== 'yes')
    await new Promise(r => setTimeout(r, 300));
  const ed = document.getElementById('sql');
  ed.value = 'SELECT * FROM booking b JOIN ';
  ed.selectionStart = ed.selectionEnd = ed.value.length;
  ed.dispatchEvent(new Event('input', { bubbles: true }));
  await new Promise(r => setTimeout(r, 3000));
  return document.getElementById('stat').textContent + ' | first: ' +
    (document.querySelector('#pop .item .txt')?.textContent ?? 'NOTHING');
})()`));
ws.close();
```

```bash
node /tmp/drive-offline.mjs
```

Expected: a suggestion count and a first item — the engine answering with no external host reachable.

- [ ] **Step 4: Stop the servers**

```bash
pkill -f 'remote-debugging-port=9223'; pkill -f 'http.server -d site 8001'
```

- [ ] **Step 5: Full check**

Run: `./scripts/check.sh`
Expected: green.

- [ ] **Step 6: Commit any incidental fixes**

If steps 2 or 3 exposed a defect, fix it and commit. If they passed clean, there is nothing to commit and that is the expected outcome.

---

## Verification

```bash
./scripts/check.sh
uv build --wheel && uv run python -m scripts.build_pages
grep -rE 'https?://' site/ --include='*.html' --include='*.js' --include='*.py' || echo 'self-contained'
```

Done when: the build reports both sizes and exits zero; `site/` names no absolute URL in any file a browser executes; the page boots and completes suggestions in Chrome with every host but localhost unresolvable; and a second build performs no download at all.
