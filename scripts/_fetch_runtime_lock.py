"""
Regenerate `scripts/runtime.lock` by fetching every archive once and hashing it.

Not part of the build. Run when the pinned release or CPython series moves:

    uv run python -m scripts._fetch_runtime_lock

Archives land in the same cache the build uses, so a regeneration warms it
rather than spending 238 MB of download on nothing. An archive already there is
hashed where it lies — the whole point of a digest is that it tells you whether
the bytes are the ones you meant.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import urllib.request

from scripts.runtime import ARCHIVE_SUFFIX, CACHE, LOCK, PBS_RELEASE, PYTHON_VERSION, ROOT, TARGETS

API = f'https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{PBS_RELEASE}'


def _release_assets() -> dict[str, str]:
    """Asset name to download URL, for the pinned release."""
    request = urllib.request.Request(API, headers={'User-Agent': 'pysqlsuggestions-build'})  # noqa: S310
    with urllib.request.urlopen(request) as answer:  # noqa: S310
        release = json.load(answer)
    return {asset['name']: asset['browser_download_url'] for asset in release['assets']}


def _archive_name(assets: dict[str, str], target: str, triple: str) -> str:
    """The one asset matching this target, or a failure naming what was looked for."""
    pattern = re.compile(
        rf'^cpython-{re.escape(PYTHON_VERSION)}\.\d+\+{PBS_RELEASE}-{re.escape(triple)}{re.escape(ARCHIVE_SUFFIX)}$'
    )
    matched = sorted(name for name in assets if pattern.match(name))
    if not matched:
        message = f'no archive for {target} ({triple}) in release {PBS_RELEASE}'
        raise SystemExit(message)
    # Sorted, last: several patch releases of the same series can be present and
    # the newest is the one to pin.
    return matched[-1]


def main() -> int:
    """Fetch every archive, hash it, and write the lock."""
    CACHE.mkdir(parents=True, exist_ok=True)
    assets = _release_assets()
    lines = [
        '# python-build-standalone, one archive per VS Code target.',
        '# Regenerate with: uv run python -m scripts._fetch_runtime_lock',
        f'# release {PBS_RELEASE}, CPython {PYTHON_VERSION}, stripped.',
    ]
    for target, triple in TARGETS.items():
        name = _archive_name(assets, target, triple)
        archive = CACHE / name
        if not archive.exists():
            print(f'{target:14} fetching {name}', flush=True)
            with urllib.request.urlopen(assets[name]) as answer, archive.open('wb') as handle:  # noqa: S310
                shutil.copyfileobj(answer, handle)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        print(f'{target:14} {archive.stat().st_size / 1_048_576:6.1f} MiB  {digest[:12]}…', flush=True)
        lines.append(f'{digest}  {target}  {name}')
    LOCK.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'wrote {LOCK.relative_to(ROOT)}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
