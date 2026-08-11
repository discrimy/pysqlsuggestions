"""The vendoring step, offline. No test here reaches the network."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import vendor_pyodide

RUNTIME = b'not really a wasm runtime, but bytes are bytes'
DIGEST = hashlib.sha256(RUNTIME).hexdigest()


def lock_for(names: tuple[str, ...], digest: str) -> str:
    """A lock file naming every file with the same digest, in `sha256sum` format."""
    return ''.join(f'{digest}  {name}\n' for name in names)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the module's cache and lock at a temporary directory."""
    lock = tmp_path / 'pyodide.lock'
    lock.write_text(lock_for(vendor_pyodide.FILES, DIGEST))
    monkeypatch.setattr(vendor_pyodide, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(vendor_pyodide, 'LOCK', lock)
    return tmp_path


def test_a_digest_that_does_not_match_stops_the_build(sandbox: Path) -> None:
    """
    The whole point of the lock is that nobody has to notice.

    A silent overwrite would publish whatever arrived, under our name.
    """
    with pytest.raises(SystemExit) as raised:
        vendor_pyodide.vendor(sandbox / 'out', download=lambda url: b'something else entirely')
    message = str(raised.value)
    assert 'pyodide.mjs' in message
    assert DIGEST in message


def test_a_matching_cached_file_is_never_downloaded(sandbox: Path) -> None:
    """The property that keeps a second build, and an offline build, working."""
    cache = sandbox / 'cache'
    cache.mkdir()
    for name in vendor_pyodide.FILES:
        (cache / name).write_bytes(RUNTIME)

    def refuse(url: str) -> bytes:
        raise AssertionError(f'downloaded {url} when the cache was good')

    vendor_pyodide.vendor(sandbox / 'out', download=refuse)
    assert sorted(p.name for p in (sandbox / 'out').iterdir()) == sorted(vendor_pyodide.FILES)


def test_a_download_fills_the_cache_and_the_destination(sandbox: Path) -> None:
    """A cold build fetches once; the copy in the cache is what makes the next one free."""
    calls: list[str] = []

    def record(url: str) -> bytes:
        calls.append(url)
        return RUNTIME

    vendor_pyodide.vendor(sandbox / 'out', download=record)
    assert len(calls) == len(vendor_pyodide.FILES)
    assert (sandbox / 'cache' / 'pyodide.asm.wasm').read_bytes() == RUNTIME
    assert (sandbox / 'out' / 'pyodide.asm.wasm').read_bytes() == RUNTIME


def test_a_lock_missing_an_entry_says_to_update(sandbox: Path) -> None:
    """A file added to FILES without a digest must not be fetched unverified."""
    (sandbox / 'pyodide.lock').write_text(lock_for(vendor_pyodide.FILES[:2], DIGEST))
    with pytest.raises(SystemExit) as raised:
        vendor_pyodide.vendor(sandbox / 'out', download=lambda url: RUNTIME)
    assert '--update' in str(raised.value)


def test_update_rewrites_the_lock_in_file_order(sandbox: Path) -> None:
    """A version bump should read as a diff, which means a stable order."""
    (sandbox / 'pyodide.lock').write_text('')
    vendor_pyodide.update(download=lambda url: RUNTIME)
    written = (sandbox / 'pyodide.lock').read_text()
    assert written == lock_for(vendor_pyodide.FILES, DIGEST)
    assert vendor_pyodide.read_lock() == dict.fromkeys(vendor_pyodide.FILES, DIGEST)
