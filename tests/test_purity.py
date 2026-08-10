"""Structural guards: the core must stay dependency-free and the engine must stay pure."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / 'src' / 'pysqlsuggestions' / 'engine'
FORBIDDEN_FOR_ENGINE = {'pysqlsuggestions.ports', 'pysqlsuggestions.resolve'}
DRIVERS = {'psycopg2', 'psycopg', 'trino', 'clickhouse_connect', 'clickhouse_driver', 'sqlalchemy', 'sqlglot'}


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
