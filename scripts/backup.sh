#!/usr/bin/env bash
set -euo pipefail

SRC=/var/lib/spendwise/instance
DEST=/var/backups/spendwise
KEEP_DAYS=7
STAMP=$(date +%Y%m%d-%H%M%S)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$DEST"
for db in "$SRC"/*.sqlite3; do
  sqlite3 "$db" ".backup '$TMP/$(basename "$db")'"
done
cp "$SRC"/local-keys.json "$TMP"/ 2>/dev/null || true

tar -czf "$DEST/spendwise-$STAMP.tar.gz" -C "$TMP" .
chmod 600 "$DEST/spendwise-$STAMP.tar.gz"
find "$DEST" -name 'spendwise-*.tar.gz' -mtime +$KEEP_DAYS -delete
echo "backup ok: $DEST/spendwise-$STAMP.tar.gz"
