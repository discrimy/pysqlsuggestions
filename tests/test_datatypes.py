"""Type families, the classifier behind comparison narrowing."""

from __future__ import annotations

import pytest

from pysqlsuggestions.engine.datatypes import UNKNOWN, comparable, family


@pytest.mark.parametrize(
    ('type_text', 'expected'),
    [
        # Postgres
        ('bigint', 'numeric'),
        ('integer', 'numeric'),
        ('numeric(12,2)', 'numeric'),
        ('double precision', 'numeric'),
        ('character varying(150)', 'string'),
        ('text', 'string'),
        ('timestamp with time zone', 'temporal'),
        ('date', 'temporal'),
        ('interval', 'temporal'),
        ('boolean', 'boolean'),
        ('jsonb', 'json'),
        ('bytea', 'binary'),
        ('uuid', 'uuid'),
        ('inet', 'network'),
        # ClickHouse
        ('UInt64', 'numeric'),
        ('Float64', 'numeric'),
        ('LowCardinality(String)', 'string'),
        ("Enum8('ok' = 1, 'error' = 2)", 'string'),
        ('DateTime', 'temporal'),
        ('Nullable(Date)', 'temporal'),
        # Trino
        ('varchar(256)', 'string'),
        ('timestamp(6) with time zone', 'temporal'),
        ('decimal(10,2)', 'numeric'),
    ],
)
def test_families(type_text: str, expected: str) -> None:
    """One table covers all three backends, because it matches on substrings."""
    assert family(type_text) == expected


def test_interval_is_temporal_not_numeric() -> None:
    """`interval` contains `int`, so order in the table is load-bearing."""
    assert family('interval') == 'temporal'


def test_datetime_is_temporal_not_two_matches() -> None:
    """So does `datetime` contain `date` and `time`; the first hit wins and is right."""
    assert family('DateTime64(3)') == 'temporal'


def test_an_array_reports_its_element_family() -> None:
    """A comparison against `bigint[]` is still a numeric one."""
    assert family('bigint[]') == 'numeric'
    assert family('Array(String)') == 'string'


def test_an_unrecognised_type_is_unknown() -> None:
    """A type this table has never heard of must not be treated as incompatible."""
    assert family('tsvector') == UNKNOWN
    assert family('') == UNKNOWN
    assert family(None) == UNKNOWN


def test_comparable_within_a_family() -> None:
    """Same family compares; different families do not."""
    assert comparable('numeric', 'numeric')
    assert not comparable('numeric', 'temporal')
    assert not comparable('string', 'boolean')


def test_unknown_compares_with_anything() -> None:
    """Silence about a type is not evidence against it."""
    assert comparable(UNKNOWN, 'temporal')
    assert comparable('numeric', UNKNOWN)
