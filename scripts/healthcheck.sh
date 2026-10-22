#!/usr/bin/env bash
# SpendWise health check — exits non-zero if anything is unhealthy.
# Usage: sudo scripts/healthcheck.sh        (CERT_DOMAIN=example.com to check TLS)
set -uo pipefail

DATA_DIR="${DATA_DIR:-/var/lib/spendwise}"
VG="${VG:-spendwise-vg}"
DISK_WARN="${DISK_WARN:-80}"
CERT_DOMAIN="${CERT_DOMAIN:-52-65-48-147.sslip.io}"
CERT_WARN_DAYS="${CERT_WARN_DAYS:-14}"

FAILS=0; WARNS=0
ok()   { printf '  \033[32m[OK]\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33m[WARN]\033[0m %s\n' "$1"; WARNS=$((WARNS+1)); }
fail() { printf '  \033[31m[FAIL]\033[0m %s\n' "$1"; FAILS=$((FAILS+1)); }

declare -A SVC=(
  [8000]=web-app [5001]=auth [5002]=transaction [5003]=budget [5004]=notification
  [5005]=reporting [5006]=recurring [5007]=savings [5008]=import
)
PORTS=(8000 5001 5002 5003 5004 5005 5006 5007 5008)

[[ $EUID -eq 0 ]] || warn "not running as root - LVM and database checks may be skipped"

echo "== Services (/health)"
for p in "${PORTS[@]}"; do
  code=$(curl -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:$p/health" || true)
  if [[ "$code" == "200" ]]; then ok "${SVC[$p]} :$p -> 200"
  else fail "${SVC[$p]} :$p -> ${code:-no response}"; fi
done

echo "== Ports listening"
for p in "${PORTS[@]}"; do
  if ss -ltnH "sport = :$p" | grep -q .; then ok "port $p listening"
  else fail "port $p not listening"; fi
done

echo "== Disk usage (warn at ${DISK_WARN}%)"
for mp in / "$DATA_DIR"; do
  use=$(df --output=pcent "$mp" 2>/dev/null | tail -1 | tr -dc '0-9')
  if [[ -z "$use" ]]; then fail "$mp not found"
  elif (( use >= DISK_WARN )); then fail "$mp at ${use}%"
  else ok "$mp at ${use}%"; fi
done

echo "== Data directory writable (as spendwise)"
probe="$DATA_DIR/.healthcheck-write-test"
if sudo -u spendwise sh -c "echo ok > '$probe' && rm -f '$probe'" 2>/dev/null; then
  ok "spendwise can write to $DATA_DIR"
else
  fail "spendwise CANNOT write to $DATA_DIR (disk full / permissions / read-only?)"
fi

echo "== Ownership & permissions"
bad_owner=$(find "$DATA_DIR" -xdev ! -user spendwise 2>/dev/null)
if [[ -z "$bad_owner" ]]; then ok "all files under $DATA_DIR owned by spendwise"
else while read -r f; do fail "wrong owner: $f ($(stat -c %U:%G "$f"))"; done <<< "$bad_owner"; fi
world=$(find "$DATA_DIR/instance" -type f -perm /o+r 2>/dev/null)
if [[ -z "$world" ]]; then ok "no world-readable files in instance/"
else while read -r f; do warn "world-readable: $f ($(stat -c %a "$f"))"; done <<< "$world"; fi

writable=$(find "$DATA_DIR" -xdev -perm /o+w 2>/dev/null)
if [[ -z "$writable" ]]; then ok "nothing under $DATA_DIR is world-writable"
else while read -r f; do fail "world-writable: $f ($(stat -c %a "$f"))"; done <<< "$writable"; fi
keys="$DATA_DIR/instance/local-keys.json"
if [[ -f "$keys" ]]; then
  kp=$(stat -c %a "$keys")
  if [[ "$kp" == "600" ]]; then ok "local-keys.json is 600"
  else fail "local-keys.json is $kp (must be 600)"; fi
fi

echo "== LVM free space"
if [[ $EUID -eq 0 ]] && vfree=$(vgs --noheadings --units g -o vg_free "$VG" 2>/dev/null); then
  ok "$VG free: $(echo "$vfree" | xargs)"
else
  warn "could not read VG $VG"
fi

echo "== Databases"
shopt -s nullglob
dbs=("$DATA_DIR"/instance/*.sqlite3)
if (( ${#dbs[@]} == 0 )); then fail "no databases found in $DATA_DIR/instance"
else for db in "${dbs[@]}"; do ok "$(basename "$db") $(du -h "$db" | cut -f1)"; done; fi

echo "== TLS certificate"
if [[ -z "$CERT_DOMAIN" ]]; then
  warn "skipped - set CERT_DOMAIN (TLS arrives in Week 2)"
else
  end=$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$CERT_DOMAIN" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  if [[ -z "$end" ]]; then fail "could not read certificate for $CERT_DOMAIN"
  else
    days=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 ))
    if (( days < CERT_WARN_DAYS )); then fail "$CERT_DOMAIN expires in $days days"
    else ok "$CERT_DOMAIN valid for $days days"; fi
  fi
fi

echo
echo "Summary: $FAILS failed, $WARNS warnings"
(( FAILS == 0 ))
