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

ROOT = Path(__file__).resolve().parents[1]

CACHE = ROOT / '.cache' / 'runtimes'
"""
Where fetched interpreters live between builds.

Load-bearing rather than a convenience: nine archives is 238 MB, and a release
build that re-fetched them would spend longer downloading than packaging. Here
rather than in `build_vsix.py` so the lock generator warms the same directory
the build reads.
"""

LOCK = ROOT / 'scripts' / 'runtime.lock'

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


UV_CANNOT_EXPRESS = frozenset({'linux-armhf'})
"""
Targets uv has no `--python-platform` value for.

`armv7-unknown-linux-gnueabihf` is not in uv's list — measured against uv itself,
which rejects it outright rather than approximating. The nearest thing it offers
is the generic `linux`, which resolves as x86_64: for a compiled wheel that
would pick the *wrong* one silently, which is worse than not asking. So the flag
is omitted for this target and `verify()`'s rejection of anything not `none-any`
is what stands in its place — which is the guarantee the whole build already
rests on, applied one step earlier.
"""


def python_platform(target: str) -> str | None:
    """
    The `--python-platform` value uv wants for `target`, or None when it has none.

    uv's platform names are the same triples for eight of the nine, which is why
    `TARGETS` holds triples rather than something friendlier. See
    `UV_CANNOT_EXPRESS` for the one that is different and why it is not
    approximated.
    """
    return None if target in UV_CANNOT_EXPRESS else TARGETS[target]
