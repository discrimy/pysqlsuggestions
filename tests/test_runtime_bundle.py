"""
The runtime bundle's target table, checked without downloading 238 MB.

Every failure this guards produces a build that succeeds and a VSIX that does
not start: a target whose triple names another platform's interpreter, an asset
that is the unstripped variant, a lock file that lost an entry in a rebase.
None of them is visible by looking at the directory afterwards.
"""

from __future__ import annotations

from pathlib import Path

from scripts.runtime import (
    ARCHIVE_SUFFIX,
    PBS_RELEASE,
    PYTHON_VERSION,
    TARGETS,
    Asset,
    download_url,
    python_platform,
    read_lock,
    site_packages,
    verify_lock,
)

LOCK = Path(__file__).resolve().parents[1] / 'scripts' / 'runtime.lock'


def test_every_vs_code_target_is_named() -> None:
    """
    Nine, and exactly the nine VS Code publishes.

    A tenth would be a target the marketplace rejects; a missing one is a
    platform that silently gets no build, which nobody notices until an issue
    arrives from an Alpine container.
    """
    assert set(TARGETS) == {
        'win32-x64',
        'win32-arm64',
        'linux-x64',
        'linux-arm64',
        'linux-armhf',
        'alpine-x64',
        'alpine-arm64',
        'darwin-x64',
        'darwin-arm64',
    }


def test_alpine_targets_use_musl_and_linux_targets_do_not() -> None:
    """The one pair that looks interchangeable and is not."""
    assert TARGETS['alpine-x64'].endswith('-musl')
    assert TARGETS['alpine-arm64'].endswith('-musl')
    assert TARGETS['linux-x64'].endswith('-gnu')
    assert TARGETS['linux-arm64'].endswith('-gnu')


def test_the_lock_covers_every_target() -> None:
    """A lock entry lost in a rebase is a target that stops building."""
    assets = read_lock(LOCK.read_text(encoding='utf-8'))
    assert set(assets) == set(TARGETS)


def test_the_lock_is_internally_consistent() -> None:
    """Each filename must name its own target's triple, the pinned version and the stripped variant."""
    assert verify_lock(read_lock(LOCK.read_text(encoding='utf-8'))) == []


def test_verification_catches_a_filename_from_another_platform() -> None:
    """Copy-paste across nine near-identical lines is the failure this exists for."""
    assets = read_lock(LOCK.read_text(encoding='utf-8'))
    wrong = dict(assets)
    wrong['darwin-arm64'] = Asset(
        target='darwin-arm64',
        filename=assets['linux-x64'].filename,
        digest=assets['darwin-arm64'].digest,
    )
    problems = verify_lock(wrong)
    assert any('darwin-arm64' in problem for problem in problems)


def test_verification_catches_the_unstripped_variant() -> None:
    """`install_only` is the same interpreter and about 40% larger. Nine of those is 100 MB of nothing."""
    assets = read_lock(LOCK.read_text(encoding='utf-8'))
    fat = dict(assets)
    original = assets['linux-x64']
    fat['linux-x64'] = Asset(
        target='linux-x64',
        filename=original.filename.replace(ARCHIVE_SUFFIX, '-install_only.tar.gz'),
        digest=original.digest,
    )
    assert any('linux-x64' in problem for problem in verify_lock(fat))


def test_verification_catches_a_truncated_digest() -> None:
    """A hand-edited lock line loses characters more often than it loses whole entries."""
    assets = read_lock(LOCK.read_text(encoding='utf-8'))
    short = dict(assets)
    short['linux-x64'] = Asset(
        target='linux-x64',
        filename=assets['linux-x64'].filename,
        digest=assets['linux-x64'].digest[:32],
    )
    assert any('no sha256' in problem for problem in verify_lock(short))


def test_the_url_points_at_the_pinned_release() -> None:
    """A floating release would make two builds of the same commit differ."""
    asset = read_lock(LOCK.read_text(encoding='utf-8'))['linux-x64']
    url = download_url(asset)
    assert url.startswith('https://github.com/astral-sh/python-build-standalone/releases/download/')
    assert PBS_RELEASE in url
    assert url.endswith(asset.filename)


def test_site_packages_follows_the_platforms_own_layout() -> None:
    """Windows keeps them under `Lib`; everything else under `lib/pythonX.Y`."""
    root = Path('/tmp/x')
    assert site_packages(root, 'win32-x64') == root / 'python' / 'Lib' / 'site-packages'
    assert site_packages(root, 'linux-x64') == root / 'python' / 'lib' / f'python{PYTHON_VERSION}' / 'site-packages'


def test_the_python_platform_is_one_uv_understands() -> None:
    """uv installs into an interpreter it cannot execute, and this is how it is told which."""
    assert python_platform('win32-x64') == 'x86_64-pc-windows-msvc'
    assert python_platform('alpine-arm64') == 'aarch64-unknown-linux-musl'
