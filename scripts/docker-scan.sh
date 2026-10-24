#!/usr/bin/env bash
# Scan all SpendWise images; exit 1 if any CRITICAL vulnerability is found.
set -uo pipefail
TAG="${1:-dev}"
SERVICES=(web-app auth-service transaction-service budget-service notification-service
          reporting-service recurring-service savings-service import-service)
fail=0
printf "%-24s %8s %6s\n" IMAGE CRITICAL HIGH
for s in "${SERVICES[@]}"; do
  json=$(trivy image --quiet --scanners vuln --severity CRITICAL,HIGH --format json "spendwise/$s:$TAG" 2>/dev/null)
  crit=$(echo "$json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sum(1 for r in d.get("Results",[]) for v in r.get("Vulnerabilities") or [] if v["Severity"]=="CRITICAL"))')
  high=$(echo "$json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sum(1 for r in d.get("Results",[]) for v in r.get("Vulnerabilities") or [] if v["Severity"]=="HIGH"))')
  printf "%-24s %8s %6s\n" "$s" "$crit" "$high"
  (( crit > 0 )) && fail=1
done
(( fail == 0 )) && echo "GATE: PASS (zero CRITICAL)" || echo "GATE: FAIL (CRITICAL found)"
exit $fail
