#!/bin/sh
set -eu
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$BERVEL_DB_PASSWORD" --set=test_password="$BERVEL_TEST_DB_PASSWORD" \
  --set=n8n_password="$N8N_DB_PASSWORD" <<'SQL'
CREATE ROLE bervel_core_stage31 LOGIN PASSWORD :'app_password';
CREATE ROLE bervel_core_stage31_test LOGIN PASSWORD :'test_password';
CREATE ROLE bervel_n8n LOGIN PASSWORD :'n8n_password';
CREATE DATABASE bervel_core_stage31 OWNER bervel_core_stage31;
CREATE DATABASE bervel_core_stage31_test OWNER bervel_core_stage31_test;
CREATE DATABASE bervel_n8n OWNER bervel_n8n;
REVOKE ALL ON DATABASE bervel_core_stage31 FROM PUBLIC;
REVOKE ALL ON DATABASE bervel_core_stage31_test FROM PUBLIC;
REVOKE ALL ON DATABASE bervel_n8n FROM PUBLIC;
SQL
