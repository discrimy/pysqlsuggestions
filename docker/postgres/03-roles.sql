-- A restricted role, so the Availability work has something real to detect.
--
-- `analyst` can read every report table except reports_database.password.
-- pg_attribute still lists that column for this role, which is the whole point:
-- it exists as metadata and must be shown greyed rather than silently omitted.
--
--   psql "postgresql://analyst:analyst@localhost:57432/report_service"

CREATE ROLE analyst LOGIN PASSWORD 'analyst';

GRANT CONNECT ON DATABASE report_service TO analyst;
GRANT USAGE ON SCHEMA public, billing TO analyst;

GRANT SELECT ON ALL TABLES IN SCHEMA public, billing TO analyst;

-- A column-level REVOKE cannot subtract from a table-level GRANT: table SELECT
-- implies every column. The only way to withhold one column is to drop the
-- table-level grant and enumerate the rest.
REVOKE SELECT ON reports_database FROM analyst;
GRANT SELECT (
    id, dt_created, dt_modified, title, type, host, port, name, "user",
    trino_catalog, hostname, hostname_stage, use_secure_connection,
    ca_certificate_path, relevance_query, alternative_database_id
) ON reports_database TO analyst;

-- Metadata visible, data not: has_any_column_privilege is true, has_table_privilege
-- is false, so `SELECT *` errors while naming columns explicitly works.
REVOKE SELECT ON mattermost_mattermostchannel FROM analyst;
GRANT SELECT (id, name) ON mattermost_mattermostchannel TO analyst;

-- No grant at all, so has_any_column_privilege is false and the relation half of
-- Availability has something real to detect. Personal data an analyst has no
-- business reading, which is the shape this case takes in practice: not a column
-- withheld from a table they use, but a table they may not open.
REVOKE SELECT ON reports_phonenumber FROM analyst;

ALTER DEFAULT PRIVILEGES IN SCHEMA public, billing GRANT SELECT ON TABLES TO analyst;
