"""The key is one opaque string, and distinct reads must never collide."""

from __future__ import annotations

from pysqlsuggestions.caches import FINGERPRINT, KEY_VERSION, cache_key


def test_none_and_the_empty_string_are_different_keys() -> None:
    """
    The collision that cost a session its relation list, made impossible.

    `SELECT "".` is a quoted empty identifier, so `tables('')` — the relations in
    a namespace actually named that, which is none — and `tables(None)` —
    everywhere the search path reaches — are both reachable from ordinary text.
    Folding them together cached the empty answer over the real one, silently.
    """
    assert cache_key('analyst', 'postgres', 'tables', None) != cache_key('analyst', 'postgres', 'tables', '')


def test_a_relation_read_never_collides_with_a_column_read() -> None:
    """`columns(None, '')` used to compute the very key `tables(None)` occupied."""
    assert cache_key('analyst', 'postgres', 'tables', None) != cache_key('analyst', 'postgres', 'columns', None, '')


def test_a_literal_dash_is_not_the_none_marker() -> None:
    """`-` means None, so a namespace actually named `-` has to encode as something else."""
    assert cache_key('analyst', 'postgres', 'tables', '-') != cache_key('analyst', 'postgres', 'tables', None)


def test_the_role_leads_the_key() -> None:
    """Two roles must not share a cached read; this is the oldest rule in the port."""
    assert cache_key('alice', 'postgres', 'tables', None) != cache_key('bob', 'postgres', 'tables', None)


def test_an_unnamed_role_is_a_role_and_not_a_wildcard() -> None:
    """identity=None gets its own line in the key rather than matching every entry."""
    assert cache_key(None, 'postgres', 'tables', None) != cache_key('alice', 'postgres', 'tables', None)


def test_the_dialect_separates_two_backends() -> None:
    """One process may serve several, and a Trino schema is not a Postgres one."""
    assert cache_key('analyst', 'trino', 'tables', None) != cache_key('analyst', 'postgres', 'tables', None)


def test_keys_are_ascii_and_carry_no_separator_from_the_data() -> None:
    """A name containing the separator, a percent or a non-ASCII character stays one component."""
    key = cache_key('analyst', 'postgres', 'columns', 'we:ird', 'na%meé')
    assert key.isascii()
    assert len(key.split(':')) == len(cache_key('analyst', 'postgres', 'columns', 'a', 'b').split(':'))


def test_every_key_is_stamped_with_the_version_and_the_shape() -> None:
    """A library upgrade that changes a cached type must not read the old entries."""
    assert cache_key('analyst', 'postgres', 'tables', None).startswith(f'{KEY_VERSION}:{FINGERPRINT}:')


def test_the_fingerprint_is_pinned() -> None:
    """
    A change to what is cached should be a deliberate line in a diff.

    Not a test of the hash function: it is a tripwire. When this fails, confirm
    the shape change was intended and write the new value in. Deriving the
    fingerprint rather than hand-bumping it is what stops someone forgetting;
    pinning it is what stops it changing unnoticed.
    """
    assert FINGERPRINT == '6cfe2915'


def test_the_fingerprint_is_stable_across_processes() -> None:
    """`hashlib`, never the builtin `hash()`, which PYTHONHASHSEED randomises."""
    import os
    import subprocess
    import sys

    code = 'from pysqlsuggestions.caches import FINGERPRINT; print(FINGERPRINT)'
    seen = {
        subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, 'PYTHONHASHSEED': seed},
        ).stdout.strip()
        for seed in ('0', '1', '12345')
    }
    assert seen == {FINGERPRINT}
