-- The analytics tables report_service's ClickHouse reports read.
--
-- Sorting and partition keys are set deliberately: they are what physical layout
-- awareness ranks to the top of a WHERE clause, and they are readable from
-- system.tables without touching user data. The Enum8 and LowCardinality columns
-- are the two places ClickHouse hands us candidate values for free.

CREATE TABLE IF NOT EXISTS analytics.report_executions
(
    started_at    DateTime,
    report_id     UInt64,
    database_id   UInt64,
    user_login    LowCardinality(String),
    status        Enum8('ok' = 1, 'error' = 2, 'timeout' = 3, 'cancelled' = 4),
    duration_ms   UInt32,
    row_count     UInt32,
    error_message String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(started_at)
ORDER BY (report_id, started_at)
COMMENT 'Каждое выполнение отчёта';

CREATE TABLE IF NOT EXISTS analytics.report_events
(
    event_time DateTime,
    report_id  UInt64,
    user_login LowCardinality(String),
    event_type Enum8('view' = 1, 'export' = 2, 'favorite' = 3, 'share' = 4),
    payload    String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (event_time, report_id);

CREATE TABLE IF NOT EXISTS analytics.report_dim
(
    report_id   UInt64,
    report_name String,
    group_name  LowCardinality(String),
    db_type     Enum8('PostgreSQL' = 1, 'ClickHouse' = 2, 'Trino' = 3)
)
ENGINE = ReplacingMergeTree
ORDER BY report_id;

-- A second database, so `<database>.<caret>` has more than one answer.
CREATE DATABASE IF NOT EXISTS staging;

CREATE TABLE IF NOT EXISTS staging.report_executions
(
    started_at DateTime,
    report_id  UInt64,
    status     Enum8('ok' = 1, 'error' = 2),
    duration_ms UInt32
)
ENGINE = MergeTree
ORDER BY (report_id, started_at);

INSERT INTO analytics.report_dim VALUES
    (1, 'Активные отчёты за период', 'Операционные', 'PostgreSQL'),
    (2, 'Отчёты по базе',            'Технические',  'PostgreSQL'),
    (3, 'Выполнения за сутки',       'Операционные', 'ClickHouse'),
    (4, 'Счета за месяц',            'Финансовые',   'PostgreSQL'),
    (5, 'Федеративный отчёт',        'Технические',  'Trino');

INSERT INTO analytics.report_executions
SELECT
    now() - toIntervalMinute(number * 7)                  AS started_at,
    1 + (number % 5)                                      AS report_id,
    1 + (number % 3)                                      AS database_id,
    ['abespalov', 'analyst', 'editor', 'viewer'][1 + number % 4] AS user_login,
    CAST(1 + intDiv(number, 250) % 4 AS UInt8)            AS status,
    30 + (number * 17) % 4000                             AS duration_ms,
    (number * 13) % 500                                   AS row_count,
    if(number % 250 = 249, 'relation does not exist', '') AS error_message
FROM numbers(1000);

INSERT INTO analytics.report_events
SELECT
    now() - toIntervalMinute(number * 11)                 AS event_time,
    1 + (number % 5)                                      AS report_id,
    ['abespalov', 'analyst', 'editor', 'viewer'][1 + number % 4] AS user_login,
    CAST(1 + number % 4 AS UInt8)                         AS event_type,
    ''                                                    AS payload
FROM numbers(400);

INSERT INTO staging.report_executions
SELECT
    now() - toIntervalHour(number)  AS started_at,
    1 + (number % 5)                AS report_id,
    CAST(1 + number % 2 AS UInt8)   AS status,
    50 + (number * 23) % 2000       AS duration_ms
FROM numbers(120);
