"""The harness a third-party ByteCache is measured against, measured itself."""

from __future__ import annotations

from pysqlsuggestions.testing import CacheConformance, InMemoryByteCache


def test_the_reference_implementation_conforms() -> None:
    """If the shipped one fails its own harness, the harness is wrong."""
    assert CacheConformance.check(InMemoryByteCache()) == []


def test_a_cache_conflating_a_miss_with_empty_bytes_is_caught() -> None:
    """`b''` is a value somebody can store; returning it for a miss turns hits into re-reads."""

    class _Conflating(InMemoryByteCache):
        def get_bytes(self, key: str) -> bytes | None:
            """Answer a miss with empty bytes, the way a careless wrapper does."""
            return super().get_bytes(key) or b''

    assert CacheConformance.check(_Conflating()) != []


def test_a_cache_that_mangles_binary_is_caught() -> None:
    """A store that round-trips through text loses exactly the bytes JSON puts in it."""

    class _Lossy(InMemoryByteCache):
        def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
            """Drop everything above ASCII, the way a text column would."""
            super().set_bytes(key, bytes(byte for byte in value if byte < 128), ttl)  # noqa: PLR2004

    assert CacheConformance.check(_Lossy()) != []


def test_a_cache_that_never_overwrites_is_caught() -> None:
    """A warm entry re-read after DDL must replace the old one, not sit behind it."""

    class _WriteOnce(InMemoryByteCache):
        def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
            """Keep the first value forever."""
            if self.get_bytes(key) is None:
                super().set_bytes(key, value, ttl)

    assert CacheConformance.check(_WriteOnce()) != []


def test_a_cache_that_reinterprets_keys_is_caught() -> None:
    """Keys are opaque. A store lowercasing or truncating them merges distinct reads."""

    class _Folding(InMemoryByteCache):
        def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None:
            """Fold case, the way a case-insensitive store does."""
            super().set_bytes(key.lower(), value, ttl)

        def get_bytes(self, key: str) -> bytes | None:
            """Fold case on the way out too, which is what makes it merge."""
            return super().get_bytes(key.lower())

    assert CacheConformance.check(_Folding()) != []
