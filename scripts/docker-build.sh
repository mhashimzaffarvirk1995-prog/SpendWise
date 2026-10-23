#!/usr/bin/env bash
# Build all SpendWise service images: scripts/docker-build.sh [tag]
set -euo pipefail
TAG="${1:-dev}"
SHA=$(git -C "$(dirname "$0")/.." rev-parse --short HEAD)
DIRTY=$(git -C "$(dirname "$0")/.." status --porcelain --untracked-files=no | grep -q . && echo "-dirty" || true)
SERVICES=(web-app auth-service transaction-service budget-service notification-service
          reporting-service recurring-service savings-service import-service)
cd "$(dirname "$0")/../services"
for s in "${SERVICES[@]}"; do
  echo "==> $s"
  docker build -q --build-arg SERVICE="$s" \
    --label org.opencontainers.image.revision="$SHA$DIRTY" \
    -t "spendwise/$s:$TAG" -t "spendwise/$s:$SHA$DIRTY" . >/dev/null
done
docker images --filter "reference=spendwise/*" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
