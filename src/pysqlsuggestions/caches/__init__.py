"""
Caches, and the key every one of them is addressed by.

Mirrors `catalogs/`: `memory` is the dependency-free implementation, and a
module named after a backend adapts that one and takes its imports lazily.
"""

from __future__ import annotations

from pysqlsuggestions.caches import codec
from pysqlsuggestions.caches.keys import CACHED_TYPES, FINGERPRINT, KEY_VERSION, ReadKind, cache_key
from pysqlsuggestions.caches.memory import MemoryCache

__all__ = ['CACHED_TYPES', 'FINGERPRINT', 'KEY_VERSION', 'MemoryCache', 'ReadKind', 'cache_key', 'codec']
