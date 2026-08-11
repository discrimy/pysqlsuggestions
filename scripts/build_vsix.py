"""
Assemble the extension's wheel bundle, then package the VSIX.

The extension installs with no network, so every wheel it will ever need has to
be inside the VSIX. Two come from this tree and the rest from PyPI, and all of
them must be `none-any`: a compiled wheel would mean one VSIX per platform,
which is the cost that choosing pg8000 over psycopg2 exists to avoid.

    uv run --with pip python -m scripts.build_vsix

`--with pip` because the project venv has none: uv installs packages without it,
and `pip download` is the only way to resolve a dependency tree to files.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Iterable
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'editors' / 'vscode'
WHEELS = EXTENSION / 'bundled' / 'wheels'

PURE_SUFFIX = '-none-any.whl'
"""The wheel tag meaning any interpreter, any platform."""

REQUIRED = frozenset(
    {
        'pysqlsuggestions',
        'pysqlsuggestions_lsp',
        'pygls',
        'lsprotocol',
        'attrs',
        'cattrs',
        'typing_extensions',
        'pg8000',
        'scramp',
        'asn1crypto',
        'python_dateutil',
        'six',
    }
)
"""
Every distribution the server needs at runtime, Postgres included.

Measured by resolving `pygls` and `pg8000` rather than guessed. Trino is absent
deliberately: it hard-requires lz4, orjson and zstandard — plain Requires-Dist,
not extras — and all three ship compiled.
"""


def distribution(filename: str) -> str:
    """The distribution name from a wheel filename, as the filename spells it."""
    return filename.split('-')[0]


def verify(filenames: Iterable[str]) -> list[str]:
    """
    What is wrong with this bundle, as a list of sentences. Empty means nothing.

    Split out from the build so the guard is testable without producing a
    bundle: both failures it catches produce a directory that looks plausibly
    full of wheels, so neither is visible by inspection.
    """
    names = list(filenames)
    problems = [
        f'{name} is compiled, which would need one VSIX per platform'
        for name in names
        if not name.endswith(PURE_SUFFIX)
    ]
    missing = REQUIRED - {distribution(name) for name in names}
    problems.extend(f'{name} is missing from the bundle' for name in sorted(missing))
    return problems


def build_wheels(destination: Path = WHEELS) -> list[Path]:
    """
    Fill `destination` with every wheel the extension installs. Returns them.

    Raises when the bundle is incomplete or carries anything compiled. Both are
    build bugs, and both would otherwise surface as an extension that fails to
    start on somebody else's machine.
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(destination)], cwd=ROOT, check=True)
    subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(destination)], cwd=ROOT / 'lsp', check=True)
    subprocess.run(
        [sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '--dest', str(destination), 'pygls', 'pg8000'],
        check=True,
    )

    wheels = sorted(destination.glob('*.whl'))
    if problems := verify(wheel.name for wheel in wheels):
        message = 'the bundle is not shippable:\n  ' + '\n  '.join(problems)
        raise SystemExit(message)

    print(f'{len(wheels)} wheels, all platform-independent')
    return wheels


def main() -> int:
    """Build the bundle and package the VSIX."""
    # Checked rather than imported: this project's venv genuinely has no pip —
    # uv installs without one — so the failure is expected and the message has
    # to name the fix rather than let pip's own error surface from a subprocess.
    if find_spec('pip') is None:
        message = 'no pip here; run: uv run --with pip python -m scripts.build_vsix'
        raise SystemExit(message)

    build_wheels()
    subprocess.run(['npm', 'run', 'build'], cwd=EXTENSION, check=True, shell=sys.platform == 'win32')
    subprocess.run(['npx', 'vsce', 'package'], cwd=EXTENSION, check=True, shell=sys.platform == 'win32')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
