# A browser demo that reaches nothing — design

Date: 2026-08-11
Status: **proposed**. Nothing built yet.

The published demo loads the Pyodide runtime from `cdn.jsdelivr.net`. This
vendors it into the site, so the page depends on no host but the one serving it.

---

## 1. Context

`demo/static/browser.js:11` names a CDN:

```js
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
```

`scripts/build_pages.py` records why, and the reasoning was sound when written:

> Pyodide itself comes from a CDN — it is about ten megabytes and versioned, so
> vendoring it would dwarf everything else here.

The measurement holds. The five runtime files this page needs total **11.7 MiB**,
against a `site/` that is otherwise 135 kB. What has changed is the weight given
to the other side.

### Why revisit it

- **It is a real failure mode, observed.** Driving the published page in a
  headless browser produced `Loading micropip` → `Failed to load micropip` →
  `Failed to fetch`, and a page that booted to a dead editor: no suggestions, an
  empty status line, no error a visitor could act on. A reload fixed it. The
  demo is the first thing a prospective user meets, and it currently fails in a
  way that looks like the library is broken.
- **The offline claim is the demo's whole argument.** The page exists to show
  that the library has no runtime dependencies and a pure core — that the entire
  pipeline runs in a page with no server and no database. A pipeline that cannot
  start without reaching a third party undercuts the thing it is demonstrating.
- **It removes a dependency nobody chose.** Availability, TLS, corporate
  proxies, and jsdelivr's own future all sit between a visitor and the demo.

### Prior art in this codebase to follow

- **The build refuses rather than warns.** `build_pages.py` already exits
  non-zero on a missing wheel and on a wheel older than `src/`;
  `.github/workflows/pages.yml` refuses to publish when the tag and
  `pyproject.toml` disagree about the version. Properties that matter are gated
  where they would otherwise be lost, not documented.
- **Zero runtime dependencies.** The library imports nothing outside the
  standard library, and the build should not be where `requests` sneaks in.
- **Comments carry the argument.** Where a decision is not obvious from the
  code, the file says why.

### Decisions taken during brainstorming

1. **Downloaded at build time**, not committed. `site/` and `dist/` are already
   gitignored; the runtime joins them. The repository keeps its size, and the
   *published* artifact is what becomes self-contained — which is what a visitor
   experiences. Committing 11.7 MiB, of which 8.2 MiB is a binary rewritten on
   every upgrade, would cost more in the repository than it buys.
2. **Integrity pinned by sha256** in a committed lock file, checked on every
   build. A Pyodide upgrade becomes a reviewable five-line diff rather than a
   silent change in what gets served under our name.
3. **`micropip` is dropped**, not vendored. See §3.
4. **A separate module owns the download.** See §2.

---

## 2. Scope

### In

`scripts/vendor_pyodide.py`; `scripts/pyodide.lock`; the vendor call, the
external-reference gate and the size report in `scripts/build_pages.py`; the
relative runtime URL and the micropip removal in `demo/static/browser.js`; the
`.pyodide/` cache entry in `.gitignore`; the cache step in `pages.yml`; tests;
README and docstring corrections.

### Out, deliberately

Vendoring any Pyodide *package* — the page installs one pure-Python wheel and
needs none. A service worker or offline manifest: the page is static and the
browser caches it already. Upgrading Pyodide: `0.28.3` is what ships today and
what will ship after this change.

### Not in this change, though found alongside it

The suggestion list renders `label` rather than the text that gets inserted, so
two join proposals to the same relation read identically. That is a library
defect in `engine/rank.py`'s conflation of "what to match against" with "what to
show", it predates this work in shape, and it deserves its own fix.

---

## 3. What the page actually needs

Five files, measured from `v0.28.3`:

| file | size |
| --- | --- |
| `pyodide.asm.wasm` | 8.2 MiB |
| `python_stdlib.zip` | 2.3 MiB |
| `pyodide.asm.js` | 1.0 MiB |
| `pyodide-lock.json` | 107 kiB |
| `pyodide.mjs` | 16 kiB |
| **total** | **11.7 MiB** (12,261,945 bytes) |

No package wheels appear in that list, and that is the second half of this
change. The page loads `micropip` for one purpose: to install a wheel that is
pure Python and declares no dependencies. Pyodide 0.28.3 exposes
`unpackArchive`, which unpacks a zip — and a wheel is a zip — directly into the
filesystem.

Dropping `micropip` removes a package download, the lock resolution behind it,
and the exact failure observed above. It is the rare simplification that also
deletes a bug.

---

## 4. `scripts/vendor_pyodide.py`

One job: **verified Pyodide bytes in a cache directory.** It is a separate
module because its failure modes — no network, a truncated download, a digest
that does not match — have nothing in common with `build_pages.py`'s, which
today cannot fail except on a missing or stale wheel.

```python
PYODIDE_VERSION = '0.28.3'
BASE = f'https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/'
FILES = ('pyodide.mjs', 'pyodide.asm.js', 'pyodide.asm.wasm',
         'python_stdlib.zip', 'pyodide-lock.json')
CACHE = ROOT / '.pyodide' / PYODIDE_VERSION
```

Downloads use `urllib.request` from the standard library. The version is in the
cache path, so a bump cannot read stale bytes from the previous one.

```python
def vendor(destination: Path, *, download: Downloader = _urlopen) -> None:
    """Copy the verified runtime into `destination`, fetching what the cache lacks."""
```

`download` is a parameter so the tests can supply their own and never touch the
network. That is the only reason it exists, and the signature says so.

**The rule.** For each file: if the cached copy's sha256 matches
`scripts/pyodide.lock`, use it. Otherwise download, hash, compare, and cache on
success. A mismatch raises, naming the file, the expected digest and the one
that arrived — never a silent overwrite, because the whole point of the lock is
that nobody has to notice.

**The lock** is `sha256sum` format, so `sha256sum -c scripts/pyodide.lock` works
by hand:

```
9f2c…  pyodide.mjs
77de…  pyodide.asm.wasm
```

**Bootstrapping.** `python3 scripts/vendor_pyodide.py --update` downloads without
checking and rewrites the lock. It is the only path that trusts the network, it
is run deliberately when upgrading Pyodide, and its output is a five-line diff.
Without it the lock could not be created and a version bump would be
unimplementable.

---

## 5. The build and the page

### 5.1 `scripts/build_pages.py`

One call — `vendor(SITE / 'pyodide')` — after the existing copies.

The docstring says vendoring "would dwarf everything else here", which will be
the opposite of what the build does. It gets rewritten to name the size and say
what it buys.

The closing report needs splitting. `site/ built: 7 files, 135 kB` becoming
`12 MB` reads as a bug and hides the number worth watching:

```
site/ built: 7 files, 135 kB + 11.7 MB Pyodide runtime
```

A jump in our own payload stays visible beside a constant.

### 5.2 The gate

Before reporting success, scan every text file the build wrote — `index.html`,
`browser.js`, `payload.py`, `schema.py`, `browser.py` — for `http://` or
`https://`. Any hit fails the build, naming the file and the URL.

No allowlist: after the CDN constant goes, the assembled site contains exactly
zero absolute URLs, which was checked rather than assumed. A rule with no
exceptions is one nobody has to maintain, and this is the property the whole
change exists to establish — so it is enforced where it would otherwise be
quietly lost by a future edit.

### 5.3 `demo/static/browser.js`

The constant becomes relative:

```js
const PYODIDE = new URL('./pyodide/', import.meta.url).href;
```

and the install loses micropip:

```js
const wheel = await fetch(new URL('./pysqlsuggestions-0.1.1-py3-none-any.whl', import.meta.url));
py.unpackArchive(await wheel.arrayBuffer(), 'zip', { extractDir: '/wheel' });
py.runPython('import sys; sys.path.insert(0, "/wheel")');
```

Unpacking into a directory we name rather than into `site-packages` keeps this
independent of Pyodide's interpreter version — the real path contains
`python3.13` today, and an upgrade would break it silently. It also matches how
the boot already puts `/demo` on the path, so the two now work the same way.

`build_pages.py` rewrites the wheel filename in `browser.js` on every build and
fails when it finds none to rewrite. That check must keep matching after the
edit, since the name moves with each release.

---

## 6. Testing

All offline. None of these reach the network.

- **The gate rejects an absolute URL** and accepts a directory without one — a
  pure function over a temporary directory.
- **A digest mismatch fails loudly**, naming the file and both digests. Uses an
  injected downloader returning wrong bytes, which is why `vendor` takes one.
- **A matching cached file is used without downloading**: the injected
  downloader is never called. This is the property that keeps repeat builds and
  offline builds working, and it is invisible if untested.
- **`browser.js` names no external host**, asserted against the source file
  rather than only the build output, so the CDN cannot creep back in during
  development and be caught only at publish time.

The existing `tests/test_demo_browser.py` is unaffected: it drives `Demo`
directly in CPython and never loads Pyodide.

---

## 7. CI and documentation

`pages.yml` caches `.pyodide/` keyed on the hash of `scripts/pyodide.lock`, so a
publish downloads the runtime once and reuses it until the lock changes. The
uploaded artifact grows to about 12 MB, well inside the limits for Pages.

`README.md`'s browser-demo section and `build_pages.py`'s docstring both say the
page reaches a CDN. Both become the opposite claim, with the size named plainly:
11.7 MiB is what a page costs that works on an air-gapped laptop and cannot be
broken by somebody else's outage.

Both also print `python -m http.server -d site 8001`. There is no `python` on a
Debian-family machine without the `python-is-python3` package — the command
fails with exit 127, which is how this was noticed. Corrected to `python3` in
both places.

---

## 8. Open questions carried forward

1. **Upgrading Pyodide** has no routine yet beyond `--update`. When 0.29 lands,
   somebody has to decide whether the five-file list is still the right set.
2. **Subresource integrity in the page.** The lock protects what the *build*
   fetches; nothing protects what the *browser* fetches from our own origin. On
   a single origin this buys little, and it is noted rather than proposed.
