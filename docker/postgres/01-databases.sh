#!/bin/sh
set -eu
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$BERVEL_DB_PASSWORD" --set=test_password="$BERVEL_TEST_DB_PASSWORD" --set=inspection_password="$BERVEL_INSPECTION_DB_PASSWORD" \
  <<'SQL'
CREATE ROLE bervel_core_stage31 LOGIN PASSWORD :'app_password';
CREATE ROLE bervel_core_stage31_test LOGIN PASSWORD :'test_password';
CREATE ROLE bervel_inspector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'inspection_password';
ALTER ROLE bervel_inspector SET default_transaction_read_only = on;
CREATE DATABASE bervel_core_stage31 OWNER bervel_core_stage31;
CREATE DATABASE bervel_core_stage31_test OWNER bervel_core_stage31_test;
REVOKE ALL ON DATABASE bervel_core_stage31 FROM PUBLIC;
REVOKE ALL ON DATABASE bervel_core_stage31_test FROM PUBLIC;
\connect bervel_core_stage31
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
GRANT CONNECT ON DATABASE bervel_core_stage31 TO bervel_inspector;
GRANT USAGE ON SCHEMA public TO bervel_inspector;
ALTER DEFAULT PRIVILEGES FOR ROLE bervel_core_stage31 IN SCHEMA public GRANT SELECT ON TABLES TO bervel_inspector;
\connect bervel_core_stage31_test
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
SQL
