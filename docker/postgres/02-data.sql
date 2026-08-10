-- Deterministic fixture data. Small on purpose: the demo needs plausible values
-- in every column, not volume. Row counts are stable so tests can assert on them.

INSERT INTO auth_group (name) VALUES ('analysts'), ('editors'), ('admins');

INSERT INTO auth_user (username, password, first_name, last_name, email, is_staff, is_superuser)
VALUES
    ('abespalov', '!', 'Александр', 'Беспалов', 'bespalov@medrocket.ru', true, true),
    ('analyst',   '!', 'Ирина',     'Волкова',  'volkova@medrocket.ru',  false, false),
    ('editor',    '!', 'Пётр',      'Смирнов',  'smirnov@medrocket.ru',  true,  false),
    ('viewer',    '!', 'Ольга',     'Кузнецова', 'kuznetsova@medrocket.ru', false, false);

INSERT INTO auth_user_groups (user_id, group_id)
SELECT u.id, g.id
FROM auth_user u
JOIN auth_group g ON g.name = CASE u.username
    WHEN 'abespalov' THEN 'admins'
    WHEN 'analyst'   THEN 'analysts'
    WHEN 'editor'    THEN 'editors'
    ELSE 'analysts'
END;

INSERT INTO reports_database (title, type, host, port, name, "user", trino_catalog, password, relevance_query)
VALUES
    ('Основная PostgreSQL', 'PostgreSQL', 'postgres',   5432, 'report_service', 'report', NULL,           'report', 'SELECT now()'),
    ('Аналитика ClickHouse', 'ClickHouse', 'clickhouse', 8123, 'analytics',      'report', NULL,           'report', 'SELECT 1'),
    ('Федерация Trino',      'Trino',      'trino',      8080, 'default',        'report', 'postgresql',   NULL,     'SELECT 1');

INSERT INTO reports_reportgroup (name, is_recommended)
VALUES ('Операционные', true), ('Финансовые', false), ('Технические', false);

INSERT INTO reports_alertconfig (add_count_row_for_mattermost, alert_changed_strategy, repeat_period, add_table)
VALUES (true, 'any', 3600, true), (false, 'increase', 86400, false);

INSERT INTO reports_report (author_id, type, name, database_id, text, group_id, comment, executions, last_execution_time)
VALUES
    (1, 0, 'Активные отчёты за период',
     1,
     'SELECT r.name, r.executions FROM reports_report r WHERE r.dt_created >= %с_даты|date|2024-01-01% ORDER BY r.executions DESC',
     1, 'Использует макрос даты', 1284, 0.42),
    (2, 0, 'Отчёты по базе',
     1,
     'SELECT d.title, count(*) AS total FROM reports_report r JOIN reports_database d ON d.id = r.database_id GROUP BY d.title',
     3, '', 317, 0.11),
    (2, 0, 'Выполнения за сутки',
     2,
     'SELECT report_id, count() AS runs FROM analytics.report_executions WHERE started_at >= now() - INTERVAL 1 DAY GROUP BY report_id',
     1, 'ClickHouse, фильтр по ключу сортировки', 8901, 0.03),
    (3, 0, 'Счета за месяц',
     1,
     'SELECT i.period, sum(i.amount) FROM billing.invoices i GROUP BY i.period ORDER BY i.period DESC',
     2, '', 42, 1.87),
    (1, 0, 'Федеративный отчёт',
     3,
     'SELECT p.name, c.runs FROM postgresql.public.reports_report p JOIN clickhouse.analytics.report_executions c ON c.report_id = p.id',
     3, 'Проверяет трёхуровневое пространство имён Trino', 5, 3.20);

UPDATE reports_report SET alert_config_id = 1 WHERE id = 1;
UPDATE reports_report SET alert_config_id = 2 WHERE id = 3;
UPDATE reports_report SET is_archived = true WHERE id = 4;
UPDATE reports_report SET broken = true WHERE id = 5;

INSERT INTO reports_report_users (report_id, user_id) VALUES (1, 2), (1, 3), (2, 2), (3, 4);
INSERT INTO reports_report_favorited_by (report_id, user_id) VALUES (1, 1), (3, 2);

INSERT INTO reports_databaseaccess (user_created_id, user_id, database_id, access_level)
VALUES (1, 2, 1, 'read'), (1, 3, 1, 'edit'), (1, 4, 2, 'read');

INSERT INTO reports_queryfilter (title, query, description)
VALUES ('Только активные', 'NOT is_archived', 'Исключает архивные отчёты');

INSERT INTO reports_queryfilter_databases (queryfilter_id, database_id) VALUES (1, 1);

INSERT INTO reports_phonenumber (user_id, number) VALUES (1, '+79001234567'), (3, '+79007654321');

INSERT INTO mattermost_mattermostchannel (name, hook_url)
VALUES ('#reports-alerts', 'https://mattermost.example/hooks/abc'),
       ('#reports-errors', 'https://mattermost.example/hooks/def');

INSERT INTO billing.invoices (database_id, period, amount, paid)
VALUES (1, '2026-06-01', 12500.00, true),
       (1, '2026-07-01', 12500.00, true),
       (2, '2026-07-01',  4300.50, false);

INSERT INTO billing."MonthlyTotals" ("Period", "Amount")
VALUES ('2026-06-01', 12500.00), ('2026-07-01', 16800.50);

-- Repeated on purpose: `most_common_vals` records a value only once it recurs,
-- so a handful of distinct rows would leave this column with no statistics.
INSERT INTO reports_runlog (report_id, status, environment)
SELECT 1 + (n % 3), (ARRAY['queued', 'running', 'succeeded', 'failed']::run_status[])[1 + (n % 4)],
       (ARRAY['production', 'staging'])[1 + (n % 2)]
FROM generate_series(1, 120) AS n;

ANALYZE;
