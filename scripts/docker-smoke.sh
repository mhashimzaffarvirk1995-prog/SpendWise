#!/usr/bin/env bash
# Start every image alone with throw-away settings and require it to become healthy.
# Usage: scripts/docker-smoke.sh [tag]   -> exit 1 if any image fails
set -uo pipefail
TAG="${1:-dev}"
SERVICES=(web-app auth-service transaction-service budget-service notification-service
          reporting-service recurring-service savings-service import-service)
declare -A DBVAR=(
  [auth-service]=AUTH_DATABASE_URL [transaction-service]=TRANSACTION_DATABASE_URL
  [budget-service]=BUDGET_DATABASE_URL [notification-service]=NOTIFICATION_DATABASE_URL
  [recurring-service]=RECURRING_DATABASE_URL [savings-service]=SAVINGS_DATABASE_URL
  [import-service]=IMPORT_DATABASE_URL
)
cleanup() { for s in "${SERVICES[@]}"; do docker rm -f "smoke-$s" >/dev/null 2>&1; done; }
trap cleanup EXIT

for s in "${SERVICES[@]}"; do
  args=(-d --name "smoke-$s" -e JWT_SECRET=smoke-only -e SECRET_KEY=smoke-only)
  [[ -n "${DBVAR[$s]:-}" ]] && args+=(-e "${DBVAR[$s]}=sqlite:////tmp/smoke.sqlite3")
  docker rm -f "smoke-$s" >/dev/null 2>&1
  docker run "${args[@]}" "spendwise/$s:$TAG" >/dev/null
done

fail=0
for s in "${SERVICES[@]}"; do
  status="timeout"
  for _ in $(seq 1 30); do
    running=$(docker inspect -f '{{.State.Running}}' "smoke-$s" 2>/dev/null)
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "smoke-$s" 2>/dev/null)
    if [[ "$health" == "healthy" ]]; then status="healthy"; break; fi
    if [[ "$running" != "true" ]]; then status="exited($(docker inspect -f '{{.State.ExitCode}}' "smoke-$s"))"; break; fi
    sleep 2
  done
  printf "%-22s %s\n" "$s" "$status"
  if [[ "$status" != "healthy" ]]; then
    fail=1
    docker logs "smoke-$s" 2>&1 | tail -3 | sed 's/^/    /'
  fi
done
(( fail == 0 )) && echo "SMOKE: PASS" || echo "SMOKE: FAIL"
exit $fail
