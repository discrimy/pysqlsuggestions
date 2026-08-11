"""The gate that keeps the published page self-contained."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.build_pages import RUNTIME_BYTES, external_references


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


def test_the_demo_sources_name_no_external_host() -> None:
    """
    Asserted against the sources, not only the build output.

    The gate runs at build time, which is the last moment before publishing and a
    long way from the edit that would trip it. This fails in the ordinary test run
    instead, so a CDN reintroduced during development is caught the same afternoon.
    """
    assert external_references(Path(__file__).resolve().parents[1] / 'demo' / 'static') == []


def test_the_runtime_total_is_injected_into_the_built_transport() -> None:
    """
    The page cannot measure the download without knowing what it is measuring against.

    Injected rather than fetched: a `sizes.json` would cost a round trip before
    the bar could appear. Injected rather than hand-written: a constant nobody
    updates produces a bar that stops at 84% after a Pyodide upgrade, which is
    worse than no bar at all.
    """
    site = Path(__file__).resolve().parents[1] / 'site'
    if not (site / 'browser.js').exists():
        pytest.skip('run `uv run python -m scripts.build_pages` first')

    written = re.search(r'const RUNTIME_BYTES = (\d+);', (site / 'browser.js').read_text())
    assert written is not None, 'the build left no RUNTIME_BYTES in site/browser.js'
    assert int(written.group(1)) == sum(f.stat().st_size for f in (site / 'pyodide').iterdir())


def test_a_transport_without_the_placeholder_fails_the_build() -> None:
    """
    Renaming the constant must break the build, not the bar.

    The failure it guards is silent: the page would ship with whatever number was
    last hard-coded, and nothing downstream would notice.
    """
    assert RUNTIME_BYTES.search('const RUNTIME_BYTES = 0;') is not None
    assert RUNTIME_BYTES.search('const RUNTIME_TOTAL = 0;') is None
