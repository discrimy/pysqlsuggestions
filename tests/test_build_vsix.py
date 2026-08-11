"""
The bundle carries everything, and nothing compiled.

The extension installs with no network, so a wheel missing from the VSIX is not
a slow first run — it is an extension that never starts, on a machine the
developer does not have. And a compiled wheel in there is a VSIX that works on
the machine that built it and fails on every other, which is exactly what
choosing pg8000 over psycopg2 was for.

Neither failure is visible by looking at the directory: both produce a bundle
that is plausibly full of wheels.
"""

from __future__ import annotations

from scripts.build_vsix import PURE_SUFFIX, REQUIRED, distribution, verify


def test_a_wheel_filename_yields_its_distribution() -> None:
    """Names come back as the filename spells them, underscores and all."""
    assert distribution('pg8000-1.31.5-py3-none-any.whl') == 'pg8000'
    assert distribution('python_dateutil-2.9.0.post0-py2.py3-none-any.whl') == 'python_dateutil'
    assert distribution('typing_extensions-4.16.0-py3-none-any.whl') == 'typing_extensions'


def test_the_required_set_names_both_local_distributions() -> None:
    """The library and the server are built from this tree, not downloaded."""
    assert 'pysqlsuggestions' in REQUIRED
    assert 'pysqlsuggestions_lsp' in REQUIRED


def test_the_required_set_names_nothing_that_ships_compiled() -> None:
    """
    Trino hard-requires lz4, orjson and zstandard; ClickHouse's driver is worse.

    Bundling either means one VSIX per platform, which is the cost this whole
    packaging choice exists to avoid.
    """
    for compiled in ('trino', 'lz4', 'orjson', 'zstandard', 'clickhouse_driver', 'psycopg2', 'psycopg2_binary'):
        assert compiled not in REQUIRED


def test_the_pure_suffix_is_what_a_universal_wheel_ends_with() -> None:
    """`none-any` is the tag meaning any interpreter, any platform."""
    assert PURE_SUFFIX == '-none-any.whl'
    assert 'pg8000-1.31.5-py3-none-any.whl'.endswith(PURE_SUFFIX)
    assert not 'lz4-4.4.5-cp312-cp312-win_amd64.whl'.endswith(PURE_SUFFIX)


def test_a_complete_pure_bundle_verifies() -> None:
    """The happy case, so the two below are known to be measuring something."""
    assert verify([f'{name}-1.0-py3-none-any.whl' for name in REQUIRED]) == []


def test_a_compiled_wheel_is_reported() -> None:
    """It would produce a VSIX that works only on the machine that built it."""
    names = [f'{name}-1.0-py3-none-any.whl' for name in REQUIRED]
    problems = verify([*names, 'lz4-4.4.5-cp312-cp312-win_amd64.whl'])
    assert any('lz4' in problem for problem in problems)


def test_a_missing_distribution_is_reported() -> None:
    """Without it the extension has no server to install and no way to fetch one."""
    names = [f'{name}-1.0-py3-none-any.whl' for name in REQUIRED if name != 'pg8000']
    problems = verify(names)
    assert any('pg8000' in problem for problem in problems)
