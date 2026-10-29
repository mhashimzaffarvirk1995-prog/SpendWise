#!/bin/sh
# Compose one-shot job: one Postgres role + one database per service (safe to re-run).
# Connection comes from PGHOST/PGUSER/PGPASSWORD; service passwords from <NAME>_DB_PASSWORD.
set -eu
for db in auth transaction budget notification recurring savings import; do
  var="$(echo "$db" | tr a-z A-Z)_DB_PASSWORD"
  eval "pw=\${$var:?$var is not set}"
  psql -q -v ON_ERROR_STOP=1 -v role="${db}_svc" -v db="$db" -v pw="$pw" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'role', :'pw') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'db') \gexec
SQL
  echo "ok  database=$db  role=${db}_svc"
done
