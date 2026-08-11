"""
The demo's own schema: a small flight-booking database, invented for this.

Deliberately unrelated to anything real. The browser build publishes whatever it
carries, and value suggestions come from statistics — which are literal values
out of the rows — so a demo schema has to be one nobody has data in. It is
written as data rather than exported from a server, which means there is no
step that could be pointed at a database by mistake.

Shaped to exercise the engine rather than to be realistic: enums and booleans so
values come from the type, columns with skewed frequencies so they come from
statistics, relations three orders of magnitude apart in size, two schemas for
the namespace, a view, and joins that are obvious from the column names.
"""

from __future__ import annotations

from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.types import ColumnValue, Function

_STATUS = "Enum8('scheduled' = 1, 'boarding' = 2, 'departed' = 3, 'landed' = 4, 'cancelled' = 5)"

POSTGRES_TABLES: dict[tuple[str, str], list[tuple[str, str]]] = {
    ('public', 'airport'): [
        ('code', 'character(3)'),
        ('name', 'character varying(120)'),
        ('city', 'character varying(80)'),
        ('country', 'character varying(2)'),
        ('timezone', 'character varying(40)'),
        ('elevation_m', 'integer'),
        ('is_hub', 'boolean'),
    ],
    ('public', 'airline'): [
        ('id', 'bigint'),
        ('iata', 'character(2)'),
        ('name', 'character varying(120)'),
        ('country', 'character varying(2)'),
        ('founded', 'date'),
        ('is_active', 'boolean'),
    ],
    ('public', 'aircraft'): [
        ('id', 'bigint'),
        ('registration', 'character varying(10)'),
        ('model', 'character varying(40)'),
        ('manufacturer', 'character varying(40)'),
        ('seats', 'integer'),
        ('airline_id', 'bigint'),
        ('in_service_since', 'date'),
    ],
    ('public', 'flight'): [
        ('id', 'bigint'),
        ('number', 'character varying(8)'),
        ('airline_id', 'bigint'),
        ('aircraft_id', 'bigint'),
        ('origin', 'character(3)'),
        ('destination', 'character(3)'),
        ('departs_at', 'timestamp with time zone'),
        ('arrives_at', 'timestamp with time zone'),
        ('status', 'flight_status'),
        ('gate', 'character varying(6)'),
        ('delay_minutes', 'integer'),
    ],
    ('public', 'passenger'): [
        ('id', 'bigint'),
        ('full_name', 'character varying(160)'),
        ('email', 'character varying(254)'),
        ('loyalty_tier', 'loyalty_tier'),
        ('joined_at', 'timestamp with time zone'),
        ('is_verified', 'boolean'),
    ],
    ('public', 'booking'): [
        ('id', 'bigint'),
        ('reference', 'character(6)'),
        ('passenger_id', 'bigint'),
        ('flight_id', 'bigint'),
        ('cabin', 'cabin_class'),
        ('seat', 'character varying(4)'),
        ('price', 'numeric(10,2)'),
        ('currency', 'character(3)'),
        ('booked_at', 'timestamp with time zone'),
        ('checked_in', 'boolean'),
        ('extras', 'jsonb'),
    ],
    ('public', 'baggage'): [
        ('id', 'bigint'),
        ('booking_id', 'bigint'),
        ('weight_kg', 'numeric(5,2)'),
        ('is_cabin', 'boolean'),
        ('tag', 'character varying(12)'),
    ],
    ('revenue', 'invoice'): [
        ('id', 'bigint'),
        ('airline_id', 'bigint'),
        ('period', 'date'),
        ('amount', 'numeric(12,2)'),
        ('currency', 'character(3)'),
        ('is_paid', 'boolean'),
    ],
    ('revenue', 'refund'): [
        ('id', 'bigint'),
        ('booking_id', 'bigint'),
        ('amount', 'numeric(10,2)'),
        ('reason', 'character varying(120)'),
        ('issued_at', 'timestamp with time zone'),
    ],
    ('revenue', 'DailyTotals'): [
        ('Day', 'date'),
        ('Route', 'character varying(7)'),
        ('Amount', 'numeric(14,2)'),
    ],
}
"""`DailyTotals` is mixed case on purpose: Postgres needs it quoted, ClickHouse does not."""

POSTGRES_ROWS = {
    ('public', 'airport'): 7_412,
    ('public', 'airline'): 483,
    ('public', 'aircraft'): 12_940,
    ('public', 'flight'): 48_301_775,
    ('public', 'passenger'): 9_120_446,
    ('public', 'booking'): 121_884_002,
    ('public', 'baggage'): 96_402_119,
    ('revenue', 'invoice'): 5_796,
    ('revenue', 'refund'): 812_407,
    ('revenue', 'DailyTotals'): 264_190,
}
"""Three orders of magnitude apart, so the size shown next to a relation earns its place."""

POSTGRES_VALUES: dict[tuple[str, str, str], list[ColumnValue]] = {
    ('public', 'flight', 'status'): [
        ColumnValue('landed', 0.71),
        ColumnValue('scheduled', 0.19),
        ColumnValue('departed', 0.058),
        ColumnValue('cancelled', 0.031),
        ColumnValue('boarding', 0.011),
    ],
    ('public', 'booking', 'cabin'): [
        ColumnValue('economy', 0.842),
        ColumnValue('premium', 0.094),
        ColumnValue('business', 0.058),
        ColumnValue('first', 0.006),
    ],
    ('public', 'passenger', 'loyalty_tier'): [
        ColumnValue('none', 0.63),
        ColumnValue('silver', 0.221),
        ColumnValue('gold', 0.108),
        ColumnValue('platinum', 0.041),
    ],
    ('public', 'booking', 'currency'): [
        ColumnValue('EUR', 0.41),
        ColumnValue('USD', 0.36),
        ColumnValue('GBP', 0.14),
        ColumnValue('CHF', 0.052),
        ColumnValue('SEK', 0.0038),
    ],
    ('public', 'airport', 'country'): [
        ColumnValue('US', 0.22),
        ColumnValue('DE', 0.061),
        ColumnValue('FR', 0.055),
        ColumnValue('GB', 0.049),
        ColumnValue('ES', 0.037),
    ],
    ('public', 'aircraft', 'manufacturer'): [
        ColumnValue('Airbus', 0.512),
        ColumnValue('Boeing', 0.436),
        ColumnValue('Embraer', 0.041),
        ColumnValue('ATR', 0.011),
    ],
    ('public', 'flight', 'gate'): [
        ColumnValue('A12', 0.004),
        ColumnValue('B7', 0.0037),
        ColumnValue('C3', 0.0031),
    ],
}
"""
Skewed on purpose. A status column where one value covers seven tenths of the
table is what makes the share worth showing, and `gate` is the counter-example:
hundreds of values, none of them common.
"""

CLICKHOUSE_TABLES: dict[tuple[str, str], list[tuple[str, str]]] = {
    ('analytics', 'flight_event'): [
        ('event_time', 'DateTime'),
        ('flight_id', 'UInt64'),
        ('event_type', "Enum8('gate_change' = 1, 'delay' = 2, 'boarding' = 3, 'takeoff' = 4, 'landing' = 5)"),
        ('gate', 'LowCardinality(String)'),
        ('delay_minutes', 'Int32'),
        ('airport', 'LowCardinality(String)'),
    ],
    ('analytics', 'booking_daily'): [
        ('day', 'Date'),
        ('route', 'LowCardinality(String)'),
        ('cabin', "Enum8('economy' = 1, 'premium' = 2, 'business' = 3, 'first' = 4)"),
        ('bookings', 'UInt64'),
        ('revenue', 'Decimal(18, 2)'),
        ('refunds', 'UInt32'),
    ],
    ('analytics', 'search_log'): [
        ('ts', 'DateTime64(3)'),
        ('session_id', 'UUID'),
        ('origin', 'LowCardinality(String)'),
        ('destination', 'LowCardinality(String)'),
        ('passengers', 'UInt8'),
        ('results', 'UInt32'),
        ('booked', 'Bool'),
    ],
    ('staging', 'flight_raw'): [
        ('ingested_at', 'DateTime'),
        ('payload', 'String'),
        ('source', 'LowCardinality(String)'),
        ('status', _STATUS),
    ],
}

CLICKHOUSE_ROWS = {
    ('analytics', 'flight_event'): 2_318_004_912,
    ('analytics', 'booking_daily'): 1_204_880,
    ('analytics', 'search_log'): 884_301_557,
    ('staging', 'flight_raw'): 48_301_775,
}

CLICKHOUSE_VALUES: dict[tuple[str, str, str], list[ColumnValue]] = {
    ('analytics', 'flight_event', 'airport'): [
        ColumnValue('FRA', 0.041),
        ColumnValue('LHR', 0.038),
        ColumnValue('CDG', 0.034),
        ColumnValue('AMS', 0.031),
    ],
    ('analytics', 'booking_daily', 'route'): [
        ColumnValue('LHR-JFK', 0.008),
        ColumnValue('FRA-JFK', 0.006),
        ColumnValue('CDG-LHR', 0.005),
    ],
    ('analytics', 'search_log', 'origin'): [
        ColumnValue('LHR', 0.052),
        ColumnValue('FRA', 0.047),
        ColumnValue('JFK', 0.039),
    ],
}

_FUNCTION_NAMES = """
    abs avg cardinality ceil coalesce concat count cume_dist current_date current_time
    current_timestamp date_part date_trunc dense_rank extract first_value greatest
    initcap json_build_object json_extract_path lag last_value lead least length lower
    lpad ltrim max min mod nullif now ntile percentile_cont position power rank regexp_replace
    repeat replace round row_number rpad rtrim split_part sqrt string_agg strpos substring
    sum to_char to_date to_timestamp trim trunc upper width_bucket
""".split()

_SIGNATURES = {
    'now': ('', 'timestamp with time zone'),
    'current_date': ('', 'date'),
    'current_time': ('', 'time with time zone'),
    'current_timestamp': ('', 'timestamp with time zone'),
    'count': ('"any"', 'bigint'),
    'sum': ('numeric', 'numeric'),
    'avg': ('numeric', 'numeric'),
    'min': ('anyelement', 'anyelement'),
    'max': ('anyelement', 'anyelement'),
    'date_trunc': ('text, timestamp with time zone', 'timestamp with time zone'),
    'row_number': ('', 'bigint'),
    'rank': ('', 'bigint'),
    'dense_rank': ('', 'bigint'),
    'lower': ('text', 'text'),
    'upper': ('text', 'text'),
    'length': ('text', 'integer'),
    'coalesce': ('"any"', '"any"'),
    'string_agg': ('text, text', 'text'),
}
"""
Spelled out where the shape matters. A zero-argument function leaves the caret
after its parentheses rather than inside them, so `now` and `count` have to
differ here for that to be visible.
"""

FUNCTIONS = tuple(
    Function(
        schema='pg_catalog',
        name=name,
        args=_SIGNATURES.get(name, ('anyelement', 'anyelement'))[0],
        result=_SIGNATURES.get(name, ('anyelement', 'anyelement'))[1],
    )
    for name in sorted(_FUNCTION_NAMES)
)


def postgres() -> MemoryCatalog:
    """The flight-booking schema as Postgres would report it."""
    return MemoryCatalog(
        POSTGRES_TABLES,
        functions=FUNCTIONS,
        table_kinds={('revenue', 'DailyTotals'): 'materialized view'},
        table_rows=POSTGRES_ROWS,
        values=POSTGRES_VALUES,
    )


def clickhouse() -> MemoryCatalog:
    """The same domain as ClickHouse would report it: events, and types that name their values."""
    return MemoryCatalog(
        CLICKHOUSE_TABLES,
        functions=FUNCTIONS,
        table_kinds=dict.fromkeys(CLICKHOUSE_TABLES, 'mergetree'),
        table_rows=CLICKHOUSE_ROWS,
        values=CLICKHOUSE_VALUES,
    )
