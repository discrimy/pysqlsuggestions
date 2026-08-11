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


def main() -> int:
    """Build `site/`. Returns a process exit status."""
    wheels = sorted((ROOT / 'dist').glob('pysqlsuggestions-*.whl'))
    if not wheels:
        print('no wheel in dist/ — run `uv build --wheel` first', file=sys.stderr)  # noqa: T201
        return 1

    # The site is only as fresh as dist/. A wheel older than the library it was
    # built from produces a page that runs yesterday's code, and the symptom is
    # a TypeError from inside Pyodide rather than anything pointing here.
    built = wheels[-1].stat().st_mtime
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
    shutil.copy2(wheels[-1], SITE / wheels[-1].name)
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
    page = WHEEL_NAME.sub(wheels[-1].name, page)
    (SITE / 'index.html').write_text(page)

    # browser.js names the wheel too, and the version moves.
    driver = (SITE / 'browser.js').read_text()
    named = WHEEL_NAME.subn(wheels[-1].name, driver)
    if not named[1]:
        print('browser.js names no wheel to install', file=sys.stderr)  # noqa: T201
        return 1
    (SITE / 'browser.js').write_text(named[0])

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
