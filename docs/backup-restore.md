# Postgres backup and restore (Week 4)

## Backup
```bash
umask 077; STAMP=$(date -u +%Y%m%d-%H%M%S)
dc exec -T postgres pg_dumpall -U postgres --globals-only > ~/pgbackup/globals-$STAMP.sql   # roles
for db in auth transaction budget notification recurring savings import; do
  dc exec -T postgres pg_dump -U postgres -Fc -d "$db" > ~/pgbackup/$db-$STAMP.dump         # one file per service
done
```
`dc` = `TAG=week-04 docker compose --env-file ~/spendwise-pg.env`. Backups live outside the repo, mode 600.

## Restore one database
```bash
dc stop transaction-service                                   # Postgres refuses to drop a database in use
dc exec -T postgres psql -U postgres -c 'DROP DATABASE transaction;'
dc run --rm db-init                                           # recreates the empty database + owner (idempotent)
dc exec -T postgres pg_restore -U postgres -d transaction --exit-on-error < ~/pgbackup/transaction-<STAMP>.dump
dc start transaction-service
```

## Measured baseline (lab, tiny data)
| Measure | Result |
|---|---|
| Backup, all 7 databases + roles | 1.3 s, ~30 KB |
| **RTO** (drop → service healthy again) | 25 s, 10 s, 11 s |
| **RPO** | Everything written after the last dump is lost (proved: "after backup" row gone after restore) |
| Side effect | The lost row's id (4) was reused by the next new row — anything outside the database that remembered id 4 now points at a different record |

## What would improve it
- Lower RPO: dump more often, or continuous WAL archiving / a replica; on AWS, RDS automated backups with point-in-time recovery (Week 6+).
- Lower RTO: keep restore scripted and rehearsed; restore into a new database and switch over instead of drop-then-restore.
- Store backups off the server (S3) — today they sit on the same disk as the database.
