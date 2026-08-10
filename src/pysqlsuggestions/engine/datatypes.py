"""
Grouping backend type names into families, so a comparison can be checked.

`bigint > timestamp` is an error in every backend here, and offering a column
that cannot appear on the other side of the operator wastes the most valuable
row in the list. Deciding that needs no type system — only whether two types
belong to the same family.

The families are matched on substrings of the type text the catalog already
reports, which is what lets one table serve `character varying(150)`,
`LowCardinality(String)` and `varchar(256)` without three dialect tables. An
unrecognised type reports `unknown` and is never filtered out: silence about a
type is not evidence against it.
"""

from __future__ import annotations

UNKNOWN = 'unknown'

# Order matters. `interval` contains `int` and `datetime` contains `date`, so
# temporal has to be decided before numeric, and both before anything looser.
_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('temporal', ('timestamp', 'datetime', 'date', 'time', 'interval')),
    ('boolean', ('bool',)),
    ('numeric', ('int', 'numeric', 'decimal', 'float', 'double', 'real', 'money', 'serial', 'number')),
    ('json', ('json',)),
    ('binary', ('bytea', 'binary', 'blob')),
    ('uuid', ('uuid',)),
    ('network', ('inet', 'cidr', 'macaddr')),
    ('string', ('char', 'text', 'string', 'enum', 'name', 'clob')),
)


def family(type_text: str | None) -> str:
    """
    The family `type_text` belongs to, or `unknown`.

    An array reports the family of its element type: `bigint[]` is numeric, and
    a query comparing it will be doing so element-wise.
    """
    if not type_text:
        return UNKNOWN
    lowered = type_text.lower()
    for name, markers in _FAMILIES:
        if any(marker in lowered for marker in markers):
            return name
    return UNKNOWN


def comparable(left: str, right: str) -> bool:
    """
    Whether two families may face each other across a comparison operator.

    Unknown compares with anything, because the alternative is hiding a column
    over a type this table has never heard of.
    """
    return UNKNOWN in (left, right) or left == right
