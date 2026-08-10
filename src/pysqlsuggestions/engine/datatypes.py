"""
Grouping backend type names into families, so a comparison can be checked.

`bigint > timestamp` is an error in every backend here, and offering a column
that cannot appear on the other side of the operator wastes the most valuable
row in the list. Deciding that needs no type system — only whether two types
belong to the same family.

Families are matched against the *words* of the type text the catalog reports,
which is what lets one table serve `character varying(150)`,
`LowCardinality(String)` and `varchar(256)` without three dialect tables. An
unrecognised type reports `unknown` and is never filtered out: silence about a
type is not evidence against it.

Recognised names rather than substrings. Looking for `int` anywhere in the text
finds it in `endpoint_kind`, and a user's enum column was then compared against
bigints while the `text` column that belonged there was dropped.
"""

from __future__ import annotations

import re

UNKNOWN = 'unknown'

_WORDS = re.compile(r'[a-z][a-z0-9]*')

_CONTAINERS = frozenset({'array', 'map', 'tuple', 'nested', 'struct', 'row', 'set'})
"""
Constructors whose contents are not their own type.

`Map(String, UInt8)` names two types and is neither of them; nothing compares
it with a scalar. Reading the first element type found makes it numeric, which
is worse than admitting ignorance.
"""

_MODIFIERS = frozenset({'nullable', 'lowcardinality', 'simpleaggregatefunction', 'unsigned', 'signed'})
"""Wrappers that pass their argument's type straight through."""

_FAMILIES: tuple[tuple[str, frozenset[str]], ...] = (
    (
        'numeric',
        frozenset(
            """
            int int2 int4 int8 int16 int32 int64 int128 int256 integer smallint bigint tinyint mediumint
            uint8 uint16 uint32 uint64 uint128 uint256 dec decimal numeric fixed
            float float4 float8 float32 float64 real double precision money number
            serial smallserial bigserial serial2 serial4 serial8
            """.split(),
        ),
    ),
    (
        'temporal',
        frozenset('date timestamp timestamptz datetime datetime64 date32 smalldatetime'.split()),
    ),
    ('clock', frozenset('time timetz'.split())),
    ('interval', frozenset({'interval'})),
    ('boolean', frozenset('bool boolean'.split())),
    (
        'string',
        frozenset(
            """
            char varchar character nchar nvarchar text string citext clob name enum enum8 enum16
            fixedstring longtext mediumtext tinytext
            """.split(),
        ),
    ),
    ('json', frozenset('json jsonb'.split())),
    ('binary', frozenset('bytea binary varbinary blob bytes'.split())),
    ('uuid', frozenset({'uuid'})),
    ('network', frozenset('inet cidr macaddr macaddr8 ipv4 ipv6'.split())),
)

_OF_WORD = {word: name for name, words in _FAMILIES for word in words}


def family(type_text: str | None) -> str:
    """
    The family `type_text` belongs to, or `unknown`.

    The first recognised word wins, which is what reads `timestamp with time
    zone` as temporal rather than as a clock time and `character varying` as a
    string: SQL writes the head of a type name first. A modifier passes through
    to what it wraps, and a container reports nothing at all.

    An array is not its element type. Postgres compares `bigint[]` with another
    array, never with a `bigint`, so claiming the element's family would offer
    exactly the comparison that does not exist.
    """
    if not type_text:
        return UNKNOWN
    if type_text.rstrip().endswith(']'):
        return UNKNOWN
    words = _WORDS.findall(type_text.lower())
    for word in words:
        if word in _CONTAINERS:
            return UNKNOWN
        if word in _MODIFIERS:
            continue
        found = _OF_WORD.get(word)
        if found is not None:
            return found
    return UNKNOWN


_ENUM_LABELS = re.compile(r"'((?:[^']|'')*)'")

_BOOLEAN_LITERALS = ('true', 'false')


def literals(type_text: str | None) -> tuple[str, ...]:
    """
    Every value a column of this type can hold, when the type says so outright.

    A boolean has two, and an enum that writes its labels into its own type text
    — ClickHouse's `Enum8('ok' = 1, 'error' = 2)` — has exactly those. Both are
    exhaustive and free: no statistics, no query, and nothing left out. Postgres
    spells an enum column with the type's *name* instead, so there the labels
    are a catalog read like any other.

    Empty for every other type, which is most of them: a varchar has no value
    set to enumerate and only statistics can say what is in it.
    """
    if not type_text:
        return ()
    if family(type_text) == 'boolean':
        return _BOOLEAN_LITERALS
    lowered = type_text.lower()
    if lowered.startswith(('enum8(', 'enum16(', 'enum(')):
        return tuple(match.group(1).replace("''", "'") for match in _ENUM_LABELS.finditer(type_text))
    return ()


def comparable(left: str, right: str) -> bool:
    """
    Whether two families may face each other across a comparison operator.

    Unknown compares with anything, because the alternative is hiding a column
    over a type this table has never heard of.
    """
    return UNKNOWN in (left, right) or left == right
