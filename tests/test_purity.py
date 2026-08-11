"""Structural guards: the core must stay dependency-free and the engine must stay pure."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

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
