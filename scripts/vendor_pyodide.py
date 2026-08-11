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
                    f'{name} does not match {LOCK.name}\n  expected {expected[name]}\n  received {digest}',
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
