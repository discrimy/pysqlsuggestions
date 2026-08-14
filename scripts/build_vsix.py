"""
Assemble the extension's runtime, then package one VSIX per platform.

The extension carries its own interpreter, so nothing about a user's machine can
make it fail to start. Every wheel that goes into that interpreter is still
`none-any` — a compiled one would mean a per-target wheel set, platform tags,
and a verification step that can only really run on the target itself. Shipping
nine interpreters is cheap; shipping nine wheel sets is not.

    uv run --with pip python -m scripts.build_vsix
    uv run --with pip python -m scripts.build_vsix --target linux-x64

`--with pip` because the project venv has none: uv installs packages without it,
and `pip download` is the only way to resolve a dependency tree to files.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from collections.abc import Iterable, Sequence
from importlib.util import find_spec
from pathlib import Path

from scripts import runtime

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'editors' / 'vscode'
WHEELS = EXTENSION / 'bundled' / 'wheels'
RUNTIME = EXTENSION / 'bundled' / 'runtime.tar.gz'

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


def fetch(asset: runtime.Asset, cache: Path = runtime.CACHE) -> Path:
    """
    `asset` on disk, downloading it once and checking its digest every time.

    Verified on a cache hit too. A truncated download that got as far as the
    cache is indistinguishable from a good one by name, and would otherwise
    produce a VSIX with half an interpreter in it.
    """
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset.filename
    if not archive.exists():
        url = runtime.download_url(asset)
        print(f'fetching {asset.target}: {url}')
        with urllib.request.urlopen(url) as answer, archive.open('wb') as handle:  # noqa: S310
            shutil.copyfileobj(answer, handle)
    found = hashlib.sha256(archive.read_bytes()).hexdigest()
    if found != asset.digest:
        archive.unlink()
        message = f'{asset.filename} hashed {found}, not {asset.digest}; discarded, run again to refetch'
        raise SystemExit(message)
    return archive


def unpack(archive: Path, into: Path) -> None:
    """
    Extract `archive` into `into`, refusing anything that escapes it.

    `filter='tar'` rather than `'data'`: python-build-standalone relies on
    symlinks — `bin/python3` is one, and so is most of `lib/` — which the
    stricter filter rejects. `'tar'` keeps them and still refuses absolute paths
    and `..`, which is the property that matters. The archive is digest-pinned,
    which is exactly why dropping this guard would go unnoticed.
    """
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as tar:
        tar.extractall(into, filter='tar')  # noqa: S202


def install_into(root: Path, target: str, wheels: Path) -> None:
    """
    Install the bundle into an extracted interpreter's own site-packages.

    Not `uv pip install --python <root>`: eight of the nine interpreters cannot
    execute on the machine doing the building. `--target` installs into a
    directory without running anything, which works here precisely because every
    wheel is `none-any` — there is no platform-specific resolution to perform,
    so naming the platform is a guard rather than a lookup.

    `--no-index` because a build that could reach PyPI could ship something this
    tree did not produce.

    `--python-platform` is omitted for the one target uv cannot name; see
    `runtime.UV_CANNOT_EXPRESS`.

    `--no-cache` is not a precaution, it is a correction. uv's cache is keyed on
    distribution name and version, and both wheels built from this tree keep the
    same version between releases — so a rebuild produces different bytes under
    an identical identity and the cache serves the previous unpack. It did: a
    VSIX shipped a library three weeks older than the tree that built it, with
    no symptom but a completion list quietly missing whole clauses. Nothing is
    lost by disabling it, since every wheel is local and pure already.
    """
    platform_name = runtime.python_platform(target)
    command = [
        'uv',
        'pip',
        'install',
        '--no-cache',
        '--target',
        str(runtime.site_packages(root, target)),
        '--python-version',
        runtime.PYTHON_VERSION,
        *(('--python-platform', platform_name) if platform_name else ()),
        '--no-index',
        '--find-links',
        str(wheels),
        'pysqlsuggestions-lsp[pg8000]',
    ]
    subprocess.run(command, check=True)


def host_target() -> str | None:
    """
    Which of the nine targets this machine is, or None when it is none of them.

    Only used to decide whether the packed interpreter can be *run* as well as
    inspected. Getting it wrong in the cautious direction costs one check; in
    the other it would try to execute an ARM binary on x86 and report a build
    failure that is nothing of the kind.
    """
    machine = {'x86_64': 'x64', 'AMD64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
    if machine is None:
        return None
    if sys.platform == 'darwin':
        return f'darwin-{machine}'
    if sys.platform == 'win32':
        return f'win32-{machine}'
    if sys.platform.startswith('linux'):
        # musl reports no libc version at all, which is how Alpine is told from
        # a glibc distribution without shelling out to `ldd`.
        return f'linux-{machine}' if platform.libc_ver()[0] else f'alpine-{machine}'
    return None


HOST_TARGET = host_target()

EXPECTED = ('pysqlsuggestions', 'pysqlsuggestions_lsp', 'pygls', 'pg8000')
"""What must be installed for the server to start. Not the whole set — the
transitive dependencies are `verify()`'s job, and repeating them here would be a
second list to keep in step with the first."""


SOURCES = (
    (ROOT / 'src' / 'pysqlsuggestions', 'pysqlsuggestions'),
    (ROOT / 'lsp' / 'pysqlsuggestions_lsp', 'pysqlsuggestions_lsp'),
)
"""The two packages built from this tree, and the names they install under."""


def assert_installed_is_this_tree(target: str, staging: Path) -> None:
    """
    Every module installed must be byte-identical to the one in this checkout.

    A version number cannot answer this. Both distributions keep theirs between
    releases, so a rebuilt wheel is a different package under an identical
    identity — which is how a stale copy reached a VSIX through uv's cache once
    already. Comparing content is the only check that would have caught it, and
    it costs a few hundred file reads on a build that spends minutes on
    compression.

    Structural, so it holds for all nine targets rather than the one that can
    execute.
    """
    packages = runtime.site_packages(staging, target)
    for source, name in SOURCES:
        for module in sorted(source.rglob('*.py')):
            if '__pycache__' in module.parts:
                continue
            installed = packages / name / module.relative_to(source)
            if not installed.exists():
                message = f'{target}: {installed.relative_to(packages)} was not installed'
                raise SystemExit(message)
            if installed.read_bytes() != module.read_bytes():
                message = (
                    f'{target}: {installed.relative_to(packages)} is not the file in this tree — '
                    'a stale build was installed, which is what --no-cache exists to prevent'
                )
                raise SystemExit(message)


def smoke_test(target: str, staging: Path) -> None:
    """
    Prove the packed runtime holds what it claims, and run it where we can.

    Structural for eight of the nine: their interpreters cannot execute on the
    machine building them, so what is checkable is that the binary exists and
    that the distributions landed. A test that pretended to do more would be
    lying about eight of nine cases.

    For the host target it goes further and imports the server. The entry-point
    check matters more than the import: the dialect registry resolves through
    the `pysqlsuggestions.dialects` group, and a `.dist-info` lost in a
    `--target` install makes `named()` return None with no other symptom than a
    completion list that has quietly stopped knowing any keywords.
    """
    binary = staging / 'python' / ('python.exe' if target.startswith('win32-') else 'bin/python3')
    if not binary.exists():
        message = f'{target}: no interpreter at {binary}'
        raise SystemExit(message)

    packages = runtime.site_packages(staging, target)
    for expected in EXPECTED:
        if not any(packages.glob(f'{expected}-*.dist-info')):
            message = f'{target}: {expected} is not installed in {packages}'
            raise SystemExit(message)
    assert_installed_is_this_tree(target, staging)

    if target != HOST_TARGET:
        return
    subprocess.run(
        [
            str(binary),
            '-c',
            'import pysqlsuggestions_lsp;'
            'from pysqlsuggestions.dialects.registry import named;'
            "assert named('clickhouse') is not None, 'entry points did not survive the install'",
        ],
        check=True,
    )


def _discard_bytecode(staging: Path) -> None:
    """
    Remove every `__pycache__` before packing.

    The host target is the one whose smoke test actually runs the interpreter,
    and running it writes `.pyc` files. Left in place, the same target would
    pack differently depending on which of the nine machines built it — the
    host's archive carrying bytecode that the other eight lack. A build should
    not produce different bytes for the same input.

    Nothing is lost: CPython writes the cache itself on first import, into a
    directory the extension owns and can write to.
    """
    for cached in staging.rglob('__pycache__'):
        shutil.rmtree(cached, ignore_errors=True)


def pack_runtime(target: str, wheels: Path = WHEELS, cache: Path = runtime.CACHE) -> Path:
    """
    Build `bundled/runtime.tar.gz` for `target`. Returns its path.

    Packed rather than shipped as a tree: a VSIX is a zip, which compresses per
    file, and the extracted interpreter zipped comes to 69.8 MiB against 34.0
    for the same content as a solid tar.gz inside the zip — both measured on
    `linux-x64`. The cost is one extraction on first activation.
    """
    assets = runtime.read_lock(runtime.LOCK.read_text(encoding='utf-8'))
    if problems := runtime.verify_lock(assets):
        message = 'runtime.lock is not shippable:\n  ' + '\n  '.join(problems)
        raise SystemExit(message)

    staging = cache / f'staging-{target}'
    unpack(fetch(assets[target], cache), staging)
    install_into(staging, target, wheels)
    # Before packing, so a broken runtime costs a few seconds rather than a
    # compression pass — and so nothing lands in `bundled/` that failed.
    smoke_test(target, staging)
    _discard_bytecode(staging)

    RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    if RUNTIME.exists():
        RUNTIME.unlink()
    with tarfile.open(RUNTIME, 'w:gz') as tar:
        tar.add(staging / 'python', arcname='python')
    print(f'{target}: runtime.tar.gz is {RUNTIME.stat().st_size / 1_048_576:.1f} MiB')
    return RUNTIME


def main(argv: Sequence[str] | None = None) -> int:
    """Build the runtime and package a VSIX, once per target."""
    # Checked rather than imported: this project's venv genuinely has no pip —
    # uv installs without one — so the failure is expected and the message has
    # to name the fix rather than let pip's own error surface from a subprocess.
    if find_spec('pip') is None:
        message = 'no pip here; run: uv run --with pip python -m scripts.build_vsix'
        raise SystemExit(message)

    parser = argparse.ArgumentParser(description='Build one VSIX per platform target.')
    parser.add_argument(
        '--target',
        action='append',
        choices=sorted(runtime.TARGETS),
        help='package only this target; repeatable. Every target when omitted.',
    )
    arguments = parser.parse_args(argv)
    targets = arguments.target or sorted(runtime.TARGETS)

    # Once, before the loop: the wheels and the bundled TypeScript are identical
    # across targets, and rebuilding them nine times is nine chances to differ.
    build_wheels()
    subprocess.run(['npm', 'run', 'build'], cwd=EXTENSION, check=True, shell=sys.platform == 'win32')

    for target in targets:
        pack_runtime(target)
        subprocess.run(
            ['npx', 'vsce', 'package', '--target', target],
            cwd=EXTENSION,
            check=True,
            shell=sys.platform == 'win32',
        )
    print(f'{len(targets)} VSIX{"es" if len(targets) != 1 else ""}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
