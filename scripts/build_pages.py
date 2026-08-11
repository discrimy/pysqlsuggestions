"""
Assemble the static demo into `site/`, ready for GitHub Pages.

    uv build --wheel
    uv run python scripts/build_pages.py
    python -m http.server -d site 8001     # to check it locally

Pages serves files and nothing else, so the site carries everything the page
needs: the wheel, the two demo modules Pyodide imports, and the schema snapshot
exported from the docker fixtures. Pyodide itself comes from a CDN — it is
about ten megabytes and versioned, so vendoring it would dwarf everything else
here.

The page is the same `index.html` the server serves. Its only concession to
this build is a pluggable transport, and `browser.js` is what fills it in.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / 'site'
STATIC = ROOT / 'demo' / 'static'

COPIED = (
    (STATIC / 'index.html', 'index.html'),
    (STATIC / 'browser.js', 'browser.js'),
    (ROOT / 'demo' / 'snapshot.json', 'snapshot.json'),
    (ROOT / 'demo' / 'payload.py', 'payload.py'),
    (ROOT / 'demo' / 'browser.py', 'browser.py'),
)

BOOTSTRAP = '<script type="module" src="./browser.js"></script>'


def main() -> int:
    """Build `site/`. Returns a process exit status."""
    wheels = sorted((ROOT / 'dist').glob('pysqlsuggestions-*.whl'))
    if not wheels:
        print('no wheel in dist/ — run `uv build --wheel` first', file=sys.stderr)  # noqa: T201
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

    # Both are modules, so document order decides: the transport is installed
    # before the page's own script reads it.
    page = (SITE / 'index.html').read_text()
    marker = '<script type="module">'
    if BOOTSTRAP not in page:
        if marker not in page:
            print('index.html has no module script to precede', file=sys.stderr)  # noqa: T201
            return 1
        page = page.replace(marker, f'{BOOTSTRAP}\n{marker}', 1)
    page = page.replace('pysqlsuggestions-0.1.0.dev0-py3-none-any.whl', wheels[-1].name)
    (SITE / 'index.html').write_text(page)

    # browser.js names the wheel too, and the version moves.
    driver = (SITE / 'browser.js').read_text()
    (SITE / 'browser.js').write_text(
        driver.replace('pysqlsuggestions-0.1.0.dev0-py3-none-any.whl', wheels[-1].name),
    )

    # Jekyll would otherwise swallow files it considers special.
    (SITE / '.nojekyll').write_text('')

    total = sum(f.stat().st_size for f in SITE.iterdir()) // 1024
    print(f'site/ built: {len(list(SITE.iterdir()))} files, {total} kB')  # noqa: T201
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
