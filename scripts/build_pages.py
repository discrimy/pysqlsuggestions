"""
Assemble the static demo into `site/`, ready for GitHub Pages.

    uv build --wheel
    uv run python -m scripts.build_pages
    python3 -m http.server -d site 8001     # to check it locally

Run as a module, not as a path: this imports `scripts.vendor_pyodide`, which
resolves only with the repository root on `sys.path`, and running a script by
path puts `scripts/` there instead.

Pages serves files and nothing else, so the site carries everything the page
needs: the wheel, the three demo modules Pyodide imports — `demo/schema.py`
among them — and Pyodide itself.

The runtime is 11.7 MiB against a demo payload of 135 kB, which is why it used
to come from a CDN. It is carried now because the page's whole argument is that
this library needs nothing at run time, and a page that cannot start without
reaching somebody else's host is a poor way to make it. `vendor_pyodide.py`
fetches it once, against a pinned digest.

The page is the same `index.html` the server serves. Its only concession to
this build is a pluggable transport, and `browser.js` is what fills it in.
"""

from __future__ import annotations

import re
import shutil
import sys
from itertools import takewhile
from pathlib import Path

from scripts.vendor_pyodide import PYODIDE_VERSION, vendor

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / 'site'
STATIC = ROOT / 'demo' / 'static'

# The checked-in page names a wheel that does not exist yet, and the build
# points it at the one it just copied. Matching the shape rather than a literal
# version is what keeps a release from turning this into a silent no-op: the
# page would still name last version's wheel, and the failure arrives as a
# 404 inside Pyodide's installer rather than from anything here.
WHEEL_NAME = re.compile(r'pysqlsuggestions-[^/\\\'"]+?-py3-none-any\.whl')

WHEEL_VERSION = re.compile(r'^pysqlsuggestions-(.+?)-py3-none-any\.whl$')
"""The version out of a wheel name, for ordering two of them by it."""

RUNTIME_BYTES = re.compile(r'const RUNTIME_BYTES = (\d+);')
"""
The decoded size of the vendored runtime, which the boot reports progress against.

Matched as a shape rather than a literal for the same reason as `WHEEL_NAME`: the
number moves with every Pyodide upgrade, and a build that quietly failed to
replace it would ship a bar that stops short of the end.
"""

COPIED = (
    (STATIC / 'index.html', 'index.html'),
    (STATIC / 'browser.js', 'browser.js'),
    (ROOT / 'demo' / 'payload.py', 'payload.py'),
    (ROOT / 'demo' / 'schema.py', 'schema.py'),
    (ROOT / 'demo' / 'browser.py', 'browser.py'),
)

BOOTSTRAP = '<script type="module" src="./browser.js"></script>'

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


def newest(wheels: list[Path]) -> Path:
    """
    The highest-versioned wheel, by version rather than by spelling.

    `sorted()` over the filenames put `0.10.0` before `0.9.0`, because `1` sorts
    before `9` — so the first two-digit minor would have copied the previous
    release into `site/`. The staleness check below would not have caught it
    either: an older wheel built after the last source edit is newer by mtime
    and older by version, so the page would have run the wrong library with
    nothing anywhere to say so.

    Numeric where the parts are numeric and a string otherwise, which orders a
    release ahead of its own release candidate and never raises on a version
    this does not recognise. Ordering something odd imperfectly is the job here;
    refusing to build over it is not.
    """
    return max(wheels, key=lambda path: _version_key(WHEEL_VERSION.sub(r'\1', path.name)))


def _version_key(version: str) -> tuple[bool, tuple[tuple[int, bool, str], ...]]:
    """
    `0.10.0` above `0.9.0`, and `0.10.0` above `0.10.0rc1`, without a parser.

    The leading flag is whether this looks like a version at all, so anything
    unrecognisable sorts *below* everything that does rather than above it —
    which is what a bare `max` over the parts did, and it would have picked the
    one wheel nobody meant.

    Within a part, an empty suffix outranks a non-empty one, because `rc1` is
    what a release candidate carries and the release itself carries nothing.
    """
    parts = []
    for part in version.split('.'):
        digits = ''.join(takewhile(str.isdigit, part))
        suffix = part[len(digits) :]
        parts.append((int(digits) if digits else 0, not suffix, suffix))
    return version[:1].isdigit(), tuple(parts)


def main() -> int:
    """Build `site/`. Returns a process exit status."""
    found = list((ROOT / 'dist').glob('pysqlsuggestions-*.whl'))
    if not found:
        print('no wheel in dist/ — run `uv build --wheel` first', file=sys.stderr)  # noqa: T201
        return 1
    wheel = newest(found)

    # The site is only as fresh as dist/. A wheel older than the library it was
    # built from produces a page that runs yesterday's code, and the symptom is
    # a TypeError from inside Pyodide rather than anything pointing here.
    built = wheel.stat().st_mtime
    stale = [f for f in (ROOT / 'src').rglob('*.py') if f.stat().st_mtime > built]
    if stale:
        names = ', '.join(sorted(f.name for f in stale)[:3])
        print(f'wheel is older than {len(stale)} source file(s) ({names}…)', file=sys.stderr)  # noqa: T201
        print('run `uv build --wheel` first', file=sys.stderr)  # noqa: T201
        return 1

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()

    for source, name in COPIED:
        if not source.exists():
            print(f'missing {source}', file=sys.stderr)  # noqa: T201
            return 1
        shutil.copy2(source, SITE / name)
    shutil.copy2(wheel, SITE / wheel.name)
    vendor(SITE / 'pyodide')

    # Both are modules, so document order decides: the transport is installed
    # before the page's own script reads it.
    page = (SITE / 'index.html').read_text()
    marker = '<script type="module">'
    if BOOTSTRAP not in page:
        if marker not in page:
            print('index.html has no module script to precede', file=sys.stderr)  # noqa: T201
            return 1
        page = page.replace(marker, f'{BOOTSTRAP}\n{marker}', 1)
    page = WHEEL_NAME.sub(wheel.name, page)
    (SITE / 'index.html').write_text(page)

    # browser.js names the wheel too, and the version moves.
    driver = (SITE / 'browser.js').read_text()
    named = WHEEL_NAME.subn(wheel.name, driver)
    if not named[1]:
        print('browser.js names no wheel to install', file=sys.stderr)  # noqa: T201
        return 1

    runtime_bytes = sum(f.stat().st_size for f in (SITE / 'pyodide').iterdir())
    sized = RUNTIME_BYTES.subn(f'const RUNTIME_BYTES = {runtime_bytes};', named[0])
    if not sized[1]:
        print('browser.js declares no RUNTIME_BYTES to fill in', file=sys.stderr)  # noqa: T201
        return 1
    (SITE / 'browser.js').write_text(sized[0])

    # Jekyll would otherwise swallow files it considers special.
    (SITE / '.nojekyll').write_text('')

    reaching = external_references(SITE)
    if reaching:
        print('site/ would reach another host:', file=sys.stderr)  # noqa: T201
        for reference in reaching:
            print(f'  {reference}', file=sys.stderr)  # noqa: T201
        return 1

    # Reported apart so a jump in our own payload stays visible next to a
    # constant 11.7 MiB. Added together, the demo's size would never move again.
    demo = [f for f in SITE.iterdir() if f.is_file()]
    payload = sum(f.stat().st_size for f in demo) // 1024
    runtime = sum(f.stat().st_size for f in (SITE / 'pyodide').iterdir()) / 1024 / 1024
    print(f'site/ built: {len(demo)} files, {payload} kB + {runtime:.1f} MiB Pyodide {PYODIDE_VERSION}')  # noqa: T201
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
