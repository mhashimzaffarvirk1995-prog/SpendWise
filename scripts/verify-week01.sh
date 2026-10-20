#!/usr/bin/env bash
# Week 1 drill verification — read-only. Run: sudo scripts/verify-week01.sh
set -uo pipefail
D=/var/lib/spendwise; I=$D/instance; K=$I/local-keys.json
P=0; F=0
pass(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; P=$((P+1)); }
fail(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; F=$((F+1)); }
chk(){ if eval "$2" >/dev/null 2>&1; then pass "$1"; else fail "$1"; fi; }

echo "== Base"
chk "spendwise service active"                 "systemctl is-active --quiet spendwise"
chk "healthcheck exits 0"                      "/usr/local/bin/spendwise-healthcheck"

echo "== Drill 1 · port already in use"
chk "no stray http.server running"             "! pgrep -f 'http.server'"
chk "port 5002 owned by spendwise MainPID"     "ss -ltnpH 'sport = :5002' | grep -q \"pid=\$(systemctl show spendwise -p MainPID --value),\""

echo "== Drill 2 · disk full"
chk "junk.bin removed"                         "[ ! -e $D/junk.bin ]"
chk "data disk below 80%"                      "[ \$(df --output=pcent $D | tail -1 | tr -dc 0-9) -lt 80 ]"

echo "== Drill 3 · keys deleted / restored"
chk "local-keys.json exists"                   "[ -f $K ]"
chk "latest backup has current keys"           "f=\$(ls -t /var/backups/spendwise/spendwise-*.tar.gz | head -1); [ \"\$(tar -xOzf \$f ./local-keys.json | sha256sum)\" = \"\$(sha256sum < $K)\" ]"

echo "== Drill 4 · single process"
chk "all 9 ports share one PID (known design)" "[ \$(ss -ltnpH | grep -E ':(8000|500[1-8]) ' | grep -o 'pid=[0-9]*' | sort -u | wc -l) -eq 1 ]"

echo "== Drill 5 · wrong owner"
chk "every file under $D owned by spendwise"   "[ -z \"\$(find $D -xdev ! -user spendwise)\" ]"
chk "all databases are 600"                    "[ -z \"\$(find $I -name '*.sqlite3' ! -perm 600)\" ]"

echo "== Drill 6 · fstab"
chk "findmnt --verify clean"                   "findmnt --verify"
chk "$D mounted from LVM"                      "findmnt -n -o SOURCE $D | grep -q spendwise--vg-data"
chk "fstab entry has nofail"                   "grep -E '/var/lib/spendwise' /etc/fstab | grep -q nofail"
chk "unit has RequiresMountsFor"               "systemctl show spendwise -p RequiresMountsFor | grep -q $D"

echo "== Drill 7 · OOM"
chk "MemoryMax set (768M)"                     "[ \$(systemctl show spendwise -p MemoryMax --value) = 805306368 ]"
chk "MemoryHigh set (512M)"                    "[ \$(systemctl show spendwise -p MemoryHigh --value) = 536870912 ]"
chk "leaky test unit cleaned up"               "! systemctl list-units --all --no-legend | grep -q leaky"

echo "== Drill 8 · crash loop"
chk "Restart=always"                           "[ \$(systemctl show spendwise -p Restart --value) = always ]"
chk "StartLimitBurst=10"                       "[ \$(systemctl show spendwise -p StartLimitBurst --value) = 10 ]"
chk "StartLimitInterval=5min"                  "[ \$(systemctl show spendwise -p StartLimitIntervalUSec --value) = 5min ]"
chk "test units cleaned up"                    "! systemctl list-units --all --no-legend | grep -qE 'exit0|exit1|crashy'"

echo "== Drill 9 · silent cron"
chk "/etc/cron.d/spendwise exists"             "[ -f /etc/cron.d/spendwise ]"
chk "old broken cron files removed"            "[ ! -e /etc/cron.d/spendwise-healthcheck ] && [ ! -e /etc/cron.d/spendwise-backup ]"
chk "cron uses absolute paths + redirects"     "grep -q '/usr/local/bin/spendwise-healthcheck; } >>' /etc/cron.d/spendwise"
chk "healthcheck log written in last 10 min"   "[ -n \"\$(find /var/log/spendwise-healthcheck.log -mmin -10)\" ]"
chk "logrotate covers both logs"               "grep -q spendwise-healthcheck.log /etc/logrotate.d/spendwise"

echo "== Drill 10 · chmod 777"
chk "nothing world-writable under $D"          "[ -z \"\$(find $D -xdev -perm /o+w)\" ]"
chk "instance/ is 700"                         "[ \$(stat -c %a $I) = 700 ]"
chk "local-keys.json is 600"                   "[ \$(stat -c %a $K) = 600 ]"
chk "nobody cannot read keys"                  "! sudo -u nobody cat $K"
chk "evil.txt removed"                         "[ ! -e $I/evil.txt ]"

echo
echo "Result: $P passed, $F failed"
(( F == 0 ))
