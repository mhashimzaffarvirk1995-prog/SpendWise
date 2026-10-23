#!/usr/bin/env bash
# Build all SpendWise service images: scripts/docker-build.sh [tag]
set -euo pipefail
TAG="${1:-dev}"
SERVICES=(web-app auth-service transaction-service budget-service notification-service
          reporting-service recurring-service savings-service import-service)
cd "$(dirname "$0")/../services"
for s in "${SERVICES[@]}"; do
  echo "==> $s"
  docker build -q --build-arg SERVICE="$s" -t "spendwise/$s:$TAG" . >/dev/null
done
docker images --filter "reference=spendwise/*:$TAG" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
