-- report_service's own schema, as Django creates it.
--
-- Table and column names are the real ones: default Django naming
-- (reports_<lowercasemodel>), AutoDateMixin's dt_created/dt_modified, and the
-- auth_* tables from django.contrib.auth. Types follow Django's field mapping.
--
-- A second schema and a restricted column exist on purpose: `billing` gives the
-- schema-qualifier path something to find, and reports_database.password is the
-- natural example of a column that is visible as metadata but must not be read.

-- --------------------------------------------------------------------------- --
-- django.contrib.auth
-- --------------------------------------------------------------------------- --

CREATE TABLE auth_group (
    id       bigserial PRIMARY KEY,
    name     varchar(150) NOT NULL UNIQUE
);

CREATE TABLE auth_user (
    id            bigserial PRIMARY KEY,
    password      varchar(128) NOT NULL,
    last_login    timestamptz,
    is_superuser  boolean NOT NULL DEFAULT false,
    username      varchar(150) NOT NULL UNIQUE,
    first_name    varchar(150) NOT NULL DEFAULT '',
    last_name     varchar(150) NOT NULL DEFAULT '',
    email         varchar(254) NOT NULL DEFAULT '',
    is_staff      boolean NOT NULL DEFAULT false,
    is_active     boolean NOT NULL DEFAULT true,
    date_joined   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_user_groups (
    id        bigserial PRIMARY KEY,
    user_id   bigint NOT NULL REFERENCES auth_user (id) ON DELETE CASCADE,
    group_id  bigint NOT NULL REFERENCES auth_group (id) ON DELETE CASCADE,
    UNIQUE (user_id, group_id)
);

-- --------------------------------------------------------------------------- --
-- reports
-- --------------------------------------------------------------------------- --

CREATE TABLE reports_database (
    id                       bigserial PRIMARY KEY,
    dt_created               timestamptz NOT NULL DEFAULT now(),
    dt_modified              timestamptz NOT NULL DEFAULT now(),
    title                    varchar(256) NOT NULL,
    type                     varchar(256) NOT NULL,
    host                     varchar(256) NOT NULL,
    port                     integer NOT NULL,
    name                     varchar(256) NOT NULL,
    "user"                   varchar(256) NOT NULL,
    trino_catalog            varchar(256),
    password                 varchar(256),
    hostname                 varchar(256) NOT NULL DEFAULT '',
    hostname_stage           varchar(256) NOT NULL DEFAULT '',
    use_secure_connection    boolean NOT NULL DEFAULT false,
    ca_certificate_path      varchar(256) NOT NULL DEFAULT '',
    relevance_query          text NOT NULL DEFAULT '',
    alternative_database_id  bigint REFERENCES reports_database (id) ON DELETE SET NULL
);

COMMENT ON TABLE reports_database IS 'Базы данных, к которым выполняются отчёты';
COMMENT ON COLUMN reports_database.trino_catalog IS 'Каталог Trino; NULL для остальных типов';
COMMENT ON COLUMN reports_database.password IS 'Пароль подключения — читаем только сервисной ролью';

CREATE TABLE reports_reportgroup (
    id               bigserial PRIMARY KEY,
    dt_created       timestamptz NOT NULL DEFAULT now(),
    dt_modified      timestamptz NOT NULL DEFAULT now(),
    name             varchar(100) NOT NULL UNIQUE,
    is_recommended   boolean NOT NULL DEFAULT false,
    parent_id        bigint REFERENCES reports_reportgroup (id) ON DELETE CASCADE
);

CREATE TABLE reports_alertconfig (
    id                            bigserial PRIMARY KEY,
    add_count_row_for_mattermost  boolean NOT NULL DEFAULT false,
    ignore_alert_for_time         interval,
    alert_changed_strategy        varchar(32) NOT NULL DEFAULT 'any',
    repeat_period                 integer,
    run_on_alternative_databases  boolean NOT NULL DEFAULT false,
    add_table                     boolean NOT NULL DEFAULT false,
    add_table_file                boolean NOT NULL DEFAULT false
);

CREATE TABLE reports_report (
    id                   bigserial PRIMARY KEY,
    dt_created           timestamptz NOT NULL DEFAULT now(),
    dt_modified          timestamptz NOT NULL DEFAULT now(),
    author_id            bigint REFERENCES auth_user (id) ON DELETE SET NULL,
    type                 smallint NOT NULL DEFAULT 0,
    name                 varchar(100) NOT NULL,
    database_id          bigint NOT NULL REFERENCES reports_database (id) ON DELETE CASCADE,
    text                 text NOT NULL,
    group_id             bigint NOT NULL REFERENCES reports_reportgroup (id) ON DELETE CASCADE,
    comment              text NOT NULL DEFAULT '',
    page_size            smallint NOT NULL DEFAULT 100,
    show_none            boolean NOT NULL DEFAULT false,
    show_in_alerts       boolean NOT NULL DEFAULT false,
    show_in_mistakes     boolean NOT NULL DEFAULT false,
    show_old_display     boolean NOT NULL DEFAULT false,
    alert_config_id      bigint UNIQUE REFERENCES reports_alertconfig (id) ON DELETE SET NULL,
    external_id          integer,
    external_project_id  varchar(64),
    align_by_left_side   boolean NOT NULL DEFAULT false,
    broken               boolean NOT NULL DEFAULT false,
    is_archived          boolean NOT NULL DEFAULT false,
    dt_last_check        timestamptz,
    date_last_used       date,
    expiration_date      date,
    executions           bigint NOT NULL DEFAULT 0,
    last_execution_time  double precision
);

COMMENT ON TABLE reports_report IS 'SQL-отчёт';
COMMENT ON COLUMN reports_report.text IS 'Текст запроса, включая макросы %имя|тип|умолчание%';
COMMENT ON COLUMN reports_report.executions IS 'Сколько раз отчёт был выполнен';

CREATE TABLE reports_report_users (
    id         bigserial PRIMARY KEY,
    report_id  bigint NOT NULL REFERENCES reports_report (id) ON DELETE CASCADE,
    user_id    bigint NOT NULL REFERENCES auth_user (id) ON DELETE CASCADE,
    UNIQUE (report_id, user_id)
);

CREATE TABLE reports_report_favorited_by (
    id         bigserial PRIMARY KEY,
    report_id  bigint NOT NULL REFERENCES reports_report (id) ON DELETE CASCADE,
    user_id    bigint NOT NULL REFERENCES auth_user (id) ON DELETE CASCADE,
    UNIQUE (report_id, user_id)
);

CREATE TABLE reports_databaseaccess (
    id               bigserial PRIMARY KEY,
    dt_created       timestamptz NOT NULL DEFAULT now(),
    dt_modified      timestamptz NOT NULL DEFAULT now(),
    user_created_id  bigint NOT NULL REFERENCES auth_user (id) ON DELETE RESTRICT,
    user_id          bigint NOT NULL REFERENCES auth_user (id) ON DELETE CASCADE,
    database_id      bigint NOT NULL REFERENCES reports_database (id) ON DELETE CASCADE,
    access_level     varchar(16) NOT NULL DEFAULT 'read'
);

CREATE TABLE reports_queryfilter (
    id           bigserial PRIMARY KEY,
    dt_created   timestamptz NOT NULL DEFAULT now(),
    dt_modified  timestamptz NOT NULL DEFAULT now(),
    title        varchar(255) NOT NULL,
    query        text NOT NULL,
    description  varchar(3000) NOT NULL DEFAULT ''
);

CREATE TABLE reports_queryfilter_databases (
    id              bigserial PRIMARY KEY,
    queryfilter_id  bigint NOT NULL REFERENCES reports_queryfilter (id) ON DELETE CASCADE,
    database_id     bigint NOT NULL REFERENCES reports_database (id) ON DELETE CASCADE,
    UNIQUE (queryfilter_id, database_id)
);

-- The only composite foreign key here, and it exists for the introspection
-- query: conkey and confkey correspond position by position, and a single-column
-- key cannot tell a query that preserves that order from one that does not.
CREATE TABLE reports_queryfilter_usage (
    id              bigserial PRIMARY KEY,
    queryfilter_id  bigint NOT NULL,
    database_id     bigint NOT NULL,
    used_at         timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (queryfilter_id, database_id)
        REFERENCES reports_queryfilter_databases (queryfilter_id, database_id) ON DELETE CASCADE
);

CREATE TABLE reports_phonenumber (
    id       bigserial PRIMARY KEY,
    user_id  bigint NOT NULL UNIQUE REFERENCES auth_user (id) ON DELETE CASCADE,
    number   varchar(20) NOT NULL
);

CREATE TABLE mattermost_mattermostchannel (
    id       bigserial PRIMARY KEY,
    name     varchar(255) NOT NULL,
    hook_url varchar(512) NOT NULL
);

-- --------------------------------------------------------------------------- --
-- A second schema, so `analytics.<caret>` and `billing.<caret>` have somewhere to go
-- --------------------------------------------------------------------------- --

CREATE SCHEMA billing;

CREATE TABLE billing.invoices (
    id          bigserial PRIMARY KEY,
    database_id bigint NOT NULL REFERENCES reports_database (id) ON DELETE CASCADE,
    period      date NOT NULL,
    amount      numeric(12, 2) NOT NULL,
    paid        boolean NOT NULL DEFAULT false
);

-- Mixed-case, quoted identifiers: the case-folding path needs a real example.
CREATE TABLE billing."MonthlyTotals" (
    id       bigserial PRIMARY KEY,
    "Period" date NOT NULL,
    "Amount" numeric(12, 2) NOT NULL
);

CREATE VIEW public.reports_active AS
SELECT r.id, r.name, r.database_id, r.executions
FROM reports_report r
WHERE NOT r.is_archived AND NOT r.broken;

CREATE INDEX reports_report_database_id_idx ON reports_report (database_id);
CREATE INDEX reports_report_group_id_idx ON reports_report (group_id);
CREATE INDEX reports_report_dt_created_idx ON reports_report (dt_created DESC);

-- A column whose type enumerates itself, and one whose values repeat: between
-- them they exercise both sources of value suggestions. `reports_database.type`
-- deliberately does not, since three rows with three distinct values leave
-- Postgres nothing to record in `pg_stats.most_common_vals`.
CREATE TYPE run_status AS ENUM ('queued', 'running', 'succeeded', 'failed');

CREATE TABLE reports_runlog (
    id          bigserial PRIMARY KEY,
    report_id   bigint NOT NULL REFERENCES reports_report(id),
    status      run_status NOT NULL,
    environment varchar(32) NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now()
);
