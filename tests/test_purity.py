"""Structural guards: the core must stay dependency-free and the engine must stay pure."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pysqlsuggestions

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'src' / 'pysqlsuggestions' / 'engine'
FORBIDDEN_FOR_ENGINE = {'pysqlsuggestions.ports', 'pysqlsuggestions.resolve'}
DRIVERS = {'psycopg2', 'psycopg', 'trino', 'clickhouse_connect', 'clickhouse_driver', 'sqlalchemy', 'sqlglot'}


def test_version_is_declared_once_in_effect() -> None:
    """
    The version is written in two files, and nothing but this makes them agree.

    `pyproject.toml` is what an install records; `__version__` is what callers
    read and what a bug report quotes. A release that bumps one and forgets the
    other produces a package that misreports itself, and no other test notices
    because every one of them passes either way.

    Read from the file rather than from `importlib.metadata`: the installed
    metadata is written at install time, so comparing against it would fail on a
    correct bump until someone reinstalled.
    """
    declared = re.search(r"^version = '([^']+)'", (ROOT / 'pyproject.toml').read_text(), re.M)
    assert declared is not None, 'pyproject.toml declares no version'
    assert pysqlsuggestions.__version__ == declared.group(1)


def test_import_pulls_in_no_drivers() -> None:
    """Importing the package must not import any database driver."""
    code = 'import sys, pysqlsuggestions; print(" ".join(sorted(sys.modules)))'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    assert not (DRIVERS & loaded), f'drivers leaked into import: {sorted(DRIVERS & loaded)}'


READERS = {'pysqlsuggestions.catalogs.trino_http', 'pysqlsuggestions.catalogs.clickhouse_http'}


def test_import_pulls_in_no_catalog_readers() -> None:
    """
    The stdlib readers are adapters, and no adapter is imported by the package root.

    `test_import_pulls_in_no_drivers` guards the same property against
    third-party drivers and cannot see these: they take no dependency, so a
    reader reaching `sys.modules` on a bare import would leak past every check
    the project has. Two backends now have their transport inside this library,
    which is why the guard needs restating rather than assuming.
    """
    code = 'import sys, pysqlsuggestions; print(" ".join(sorted(sys.modules)))'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    loaded = set(result.stdout.split())
    assert not (READERS & loaded), f'a catalog reader leaked into import: {sorted(READERS & loaded)}'


def _imported_modules(path: Path) -> set[str]:
    """Fully-qualified module names imported by `path`, resolving relative imports."""
    package_parts = path.relative_to(ENGINE.parents[1]).with_suffix('').parts
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                names.add(node.module or '')
            else:
                base = package_parts[: len(package_parts) - node.level]
                names.add('.'.join((*base, node.module)) if node.module else '.'.join(base))
    return names


def test_engine_never_imports_the_io_layer() -> None:
    """Nothing under engine/ may import ports or resolve — purity is structural, not aspirational."""
    offenders = {
        str(path.relative_to(ENGINE.parent)): sorted(FORBIDDEN_FOR_ENGINE & _imported_modules(path))
        for path in sorted(ENGINE.rglob('*.py'))
        if FORBIDDEN_FOR_ENGINE & _imported_modules(path)
    }
    assert not offenders, f'engine imported the I/O layer: {offenders}'


def test_lsp_version_matches_the_library() -> None:
    """
    The server and the library are released together, so their versions agree.

    The extension bundles wheels built from this tree. A server wheel claiming a
    version the library wheel does not is a bug report nobody can reproduce,
    because the two numbers in it describe different code.
    """
    root = re.search(r"^version = '([^']+)'", (ROOT / 'pyproject.toml').read_text(), re.M)
    server = re.search(r"^version = '([^']+)'", (ROOT / 'lsp' / 'pyproject.toml').read_text(), re.M)
    assert root is not None, 'pyproject.toml declares no version'
    assert server is not None, 'lsp/pyproject.toml declares no version'
    assert root.group(1) == server.group(1)


def test_the_server_module_reports_the_version_it_ships_as() -> None:
    """
    `pysqlsuggestions_lsp.__version__` is what the server tells a client it is.

    The same split the library has: `lsp/pyproject.toml` is what an install
    records, `__version__` is what `initialize` reports and what a bug report
    quotes. Nothing else compares them, so a release that bumps the manifest and
    forgets the module produces a server that misdescribes itself to every
    client it handshakes with — and the tests kept passing, because the one that
    looked like it covered this reads the manifest on both sides.

    Read from the file rather than imported, so this guard does not need pygls
    installed to run.
    """
    manifest = re.search(r"^version = '([^']+)'", (ROOT / 'lsp' / 'pyproject.toml').read_text(), re.M)
    module = re.search(
        r"^__version__ = '([^']+)'", (ROOT / 'lsp' / 'pysqlsuggestions_lsp' / '__init__.py').read_text(), re.M
    )
    assert manifest is not None, 'lsp/pyproject.toml declares no version'
    assert module is not None, 'pysqlsuggestions_lsp declares no __version__'
    assert module.group(1) == manifest.group(1)


def test_the_server_pins_the_library_release_it_belongs_to() -> None:
    """
    The `pysqlsuggestions==` pin is what a released server wheel carries.

    A checkout resolves it through the workspace and never notices the number,
    which is exactly why it goes stale: the pin is only read by someone
    installing the server from PyPI, and by then the wrong one is published. A
    server pinned to a library two releases back installs cleanly and offers the
    behaviour of neither.
    """
    root = re.search(r"^version = '([^']+)'", (ROOT / 'pyproject.toml').read_text(), re.M)
    pinned = re.search(r"'pysqlsuggestions==([^']+)'", (ROOT / 'lsp' / 'pyproject.toml').read_text())
    assert root is not None, 'pyproject.toml declares no version'
    assert pinned is not None, 'lsp/pyproject.toml pins no library version'
    assert pinned.group(1) == root.group(1)


def test_the_library_does_not_import_the_server() -> None:
    """
    The dependency runs one way: the server imports the library, never the reverse.

    `lsp/` may import drivers and pygls, which is exactly why the library must
    not reach into it. An import added in the wrong direction would drag both
    into `import pysqlsuggestions` and break the zero-dependency claim from a
    file that looks unrelated to it.
    """
    for path in (ROOT / 'src' / 'pysqlsuggestions').rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        assert 'pysqlsuggestions_lsp' not in source, f'{path} names the server package'


def _manifest() -> dict[str, Any]:
    """The extension's package.json."""
    text = (ROOT / 'editors' / 'vscode' / 'package.json').read_text(encoding='utf-8')
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def test_the_extension_version_matches_the_library() -> None:
    """
    The VSIX bundles wheels built from this tree, so the numbers must agree.

    An extension reporting 0.3.0 while carrying a 0.2.1 server is a bug report
    whose version line is a lie, and no other test would notice.
    """
    declared = re.search(r"^version = '([^']+)'", (ROOT / 'pyproject.toml').read_text(), re.M)
    assert declared is not None, 'pyproject.toml declares no version'
    assert _manifest()['version'] == declared.group(1)


def test_the_settings_schema_has_nowhere_to_put_a_password() -> None:
    """
    A password field in settings is a password in someone's git history.

    Passwords live in SecretStorage. This asserts the schema offers nowhere to
    put one, because a helpful-looking field is all it takes — and
    `additionalProperties: false` is what stops one being invented.
    """
    properties = _manifest()['contributes']['configuration']['properties']
    profile = properties['pysqlsuggestions.connections']['items']
    assert 'password' not in profile['properties']
    assert profile['additionalProperties'] is False


def test_the_extension_declares_no_python_requirement_it_no_longer_has() -> None:
    """
    The VSIX carries its interpreter, so nothing may still tell a user to install one.

    Left behind, the README and the settings schema keep describing an extension
    that stopped existing — and a requirement a user cannot satisfy is worse than
    no documentation, because they will go and satisfy it.
    """
    package = json.loads((ROOT / 'editors' / 'vscode' / 'package.json').read_text(encoding='utf-8'))
    settings = package['contributes']['configuration']['properties']
    assert 'pysqlsuggestions.pythonPath' not in settings
    readme = (ROOT / 'editors' / 'vscode' / 'README.md').read_text(encoding='utf-8')
    assert 'on your PATH' not in readme
    assert 'pythonPath' not in readme


def test_the_lock_names_a_runtime_for_every_target_the_build_packages() -> None:
    """
    Two lists that must agree, in two files, neither of which reads the other.

    `TARGETS` decides what `vsce package --target` is invoked for and the lock
    decides what can be fetched, so a target in one and not the other is a build
    that fails eight-ninths of the way through — after twenty minutes of
    downloads.
    """
    from scripts.runtime import LOCK, TARGETS, read_lock, verify_lock

    assets = read_lock(LOCK.read_text(encoding='utf-8'))
    assert set(assets) == set(TARGETS)
    assert verify_lock(assets) == []


def test_the_bundle_ships_a_runtime_and_not_the_wheels_that_built_it() -> None:
    """
    `bundled/wheels` feeds the install that produced the runtime and has nothing to do at run time.

    Shipping it would add a megabyte of already-installed packages to each of
    the nine builds, and would give a future reader two plausible places to look
    for the code that actually runs.
    """
    ignored = (ROOT / 'editors' / 'vscode' / '.vscodeignore').read_text(encoding='utf-8')
    assert 'bundled/wheels/**' in ignored
