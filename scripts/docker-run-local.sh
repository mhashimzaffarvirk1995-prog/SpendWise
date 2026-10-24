#!/usr/bin/env bash
# Run all SpendWise containers on one custom network (lab use): scripts/docker-run-local.sh [tag]
set -euo pipefail
TAG="${1:-dev}"
NET=spendwise-net
ENVFILE="$HOME/spendwise-docker.env"
declare -A DB=(
  [auth-service]=AUTH_DATABASE_URL:auth.sqlite3
  [transaction-service]=TRANSACTION_DATABASE_URL:transactions.sqlite3
  [budget-service]=BUDGET_DATABASE_URL:budgets.sqlite3
  [notification-service]=NOTIFICATION_DATABASE_URL:notifications.sqlite3
  [recurring-service]=RECURRING_DATABASE_URL:recurring.sqlite3
  [savings-service]=SAVINGS_DATABASE_URL:savings.sqlite3
  [import-service]=IMPORT_DATABASE_URL:imports.sqlite3
)
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

run() {  # run <name> <image-service> [extra docker args...] [-- command...]
  local name=$1 svc=$2; shift 2
  docker rm -f "$name" >/dev/null 2>&1 || true
  local args=(-d --name "$name" --network "$NET" --env-file "$ENVFILE" --restart unless-stopped)
  if [[ -n "${DB[$svc]:-}" ]]; then
    local var=${DB[$svc]%%:*} file=${DB[$svc]#*:}
    args+=(-e "$var=sqlite:////data/$file" -v "spendwise-$svc-data:/data")
  fi
  docker run "${args[@]}" "$@"
}

for s in auth-service transaction-service budget-service notification-service \
         reporting-service recurring-service savings-service import-service; do
  run "$s" "$s" "spendwise/$s:$TAG"
done
run recurring-worker recurring-service \
  --health-cmd "python -c 'import os,sys,time; sys.exit(0 if time.time() - os.path.getmtime(\"/tmp/worker-heartbeat\") < 60 else 1)'" \
  --health-interval 30s --health-start-period 20s \
  "spendwise/recurring-service:$TAG" python worker.py
run web-app web-app -p 127.0.0.1:9000:8080 "spendwise/web-app:$TAG"

sleep 15
docker ps --filter network="$NET" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
