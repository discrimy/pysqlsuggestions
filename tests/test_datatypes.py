"""Type families, the classifier behind comparison narrowing."""

from __future__ import annotations

import pytest

from pysqlsuggestions.engine import datatypes
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
        ('timestamp without time zone', 'temporal'),
        ('timestamptz', 'temporal'),
        ('date', 'temporal'),
        ('time without time zone', 'clock'),
        ('smallint', 'numeric'),
        ('Nullable(Int64)', 'numeric'),
        ('interval', 'interval'),
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
    """One table covers all three backends, because it matches on the words of a type name."""
    assert family(type_text) == expected


def test_interval_is_its_own_family_and_certainly_not_numeric() -> None:
    """
    `interval` contains `int`, which is why recognised names beat substrings.

    Its own family rather than temporal: Postgres has no `date > interval`, so
    one bucket for everything time-shaped offered a duration where a date
    belonged.
    """
    assert family('interval') == 'interval'


def test_datetime_is_temporal_not_two_matches() -> None:
    """So does `datetime` contain `date` and `time`; the first hit wins and is right."""
    assert family('DateTime64(3)') == 'temporal'


def test_an_array_is_not_its_element_type() -> None:
    """
    Postgres compares `bigint[]` with another array, never with a `bigint`.

    Reporting the element family offered exactly the comparison that does not
    exist. Unknown instead, so an array column is still offered rather than
    hidden — silence about a type is not evidence against it.
    """
    assert family('bigint[]') == UNKNOWN
    assert family('Array(String)') == UNKNOWN


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


@pytest.mark.parametrize(
    'type_text',
    [
        'endpoint_kind',
        'checkpoint',
        'nameplate',
        'timeline',
        'point',
        'internal',
        'int8range',
        'daterange',
        'oid',
        'Map(String, UInt8)',
        'Tuple(Int64, String)',
        'bigint[]',
    ],
)
def test_a_type_that_is_not_recognised_says_so(type_text: str) -> None:
    """
    Matching family markers anywhere in the text reads a name for a type.

    `endpoint_kind` is a user enum, and `int` inside it made it numeric — so a
    status column got compared against bigints and the `text` column that
    belonged there was dropped. A container or a range is not its element type
    either: `Map(String, UInt8)` has no comparison with a scalar.
    """
    assert datatypes.family(type_text) == datatypes.UNKNOWN


def test_an_interval_is_not_a_date() -> None:
    """
    Postgres has no `date > interval` and no `date > time`.

    One temporal bucket put all three together, so `WHERE day > ` offered an
    interval column and a time-of-day column.
    """
    assert not datatypes.comparable(datatypes.family('date'), datatypes.family('interval'))
    assert not datatypes.comparable(datatypes.family('date'), datatypes.family('time without time zone'))
    assert datatypes.comparable(datatypes.family('date'), datatypes.family('timestamp with time zone'))
