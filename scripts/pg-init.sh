#!/usr/bin/env bash
# Create one Postgres role + one database per SpendWise service (safe to re-run).
# Usage: scripts/pg-init.sh [postgres-container]
set -euo pipefail
PG="${1:-pg-lab}"
ENVFILE="$HOME/spendwise-pg.env"
umask 077
touch "$ENVFILE"
DBS=(auth transaction budget notification recurring savings import)

for db in "${DBS[@]}"; do
  role="${db}_svc"
  var="$(echo "$db" | tr a-z A-Z)_DB_PASSWORD"
  if ! grep -q "^${var}=" "$ENVFILE"; then
    echo "${var}=$(python3 -c 'import secrets; print(secrets.token_hex(16))')" >> "$ENVFILE"
  fi
  pw=$(grep "^${var}=" "$ENVFILE" | cut -d= -f2)
  docker exec -i "$PG" psql -U postgres -q -v ON_ERROR_STOP=1 \
    -v role="$role" -v db="$db" -v pw="$pw" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'role', :'pw') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'db') \gexec
SQL
  echo "ok  database=$db  role=$role"
done
