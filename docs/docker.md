# Week 3 — SpendWise in containers

## Run model
| Before (Weeks 1–2) | Now |
|---|---|
| `run.py` starts 9 Flask dev servers as threads in one process | 9 images, one container per service, run by **gunicorn** (`wsgi.py`, `--preload`, 2 workers) |
| recurring worker = a thread inside run.py | separate container `recurring-worker` (same image, `python worker.py`, heartbeat healthcheck) |
| services reach each other on 127.0.0.1:500x | custom bridge network `spendwise-net`, Docker DNS: `http://auth-service:8080` |
| SQLite files under /var/lib/spendwise | one named volume per service: `spendwise-<service>-data` → `/data` |

## Image
`services/Dockerfile` (one file, `--build-arg SERVICE=<name>`):
- stage 1 `python:3.12-slim` builds wheels from the service's `requirements.txt`
- stage 2 `python:3.12-slim`: installs wheels only, copies `common/` + service code **last** (layer caching), runs as non-root user `app` (UID 999), `/data` owned by `app`, code owned by root (read-only for the app)
- `HEALTHCHECK` on `/health` with Python `urllib` (no curl in slim)

| Image | Size | User | Healthcheck |
|---|---|---|---|
| naive (`python:3.12`, root) | 1.68 GB | root | none |
| slim multi-stage | 196–244 MB (−86%) | app | yes |

Rebuild after a code-only change: 11 s → 2.2 s (dependency layers CACHED).

## Tags
| Tag | Example | Use |
|---|---|---|
| git SHA | `spendwise/auth-service:a4a0f65` | deploy with this — immutable, maps to one commit (`-dirty` if uncommitted changes) |
| week | `spendwise/auth-service:week-03` | human-friendly name |
| latest / dev | local only | never deploy — you can't tell what's inside |
Every image also carries `org.opencontainers.image.revision=<sha>`.

## Security scan (Trivy)
| | naive | slim |
|---|---|---|
| OS CRITICAL / HIGH | 26 / 434 | 0 / 44 (no fix available upstream yet) |
| Python libs | PyJWT 2.13.0: 1 CRITICAL + 6 HIGH | same → **fixed by PyJWT 2.14.0** |
Gate: `scripts/docker-scan.sh <tag>` → exit 1 on any CRITICAL. Now: PASS on all 9.

## Scripts
| Script | Does |
|---|---|
| `scripts/docker-build.sh [tag]` | build all 9 images, tag `<tag>` + `<git sha>` |
| `scripts/docker-run-local.sh [tag]` | network + volumes + 10 containers; web-app on 127.0.0.1:9000 |
| `scripts/docker-smoke.sh [tag]` | start each image alone, require `healthy`, exit 1 otherwise |
| `scripts/docker-scan.sh [tag]` | Trivy, exit 1 on CRITICAL |
| `scripts/log-table.py` | JSON logs → table with request IDs |

## Proven
- Kill one container from the host → only that one restarts (`restarts=1`); others keep their start time; web-app stays 200. (Week 1 Drill 4 had no isolation.)
- Containers recreated from new images → signup account still there (`users 1 rows`) because data lives in volumes.
- Request ID crosses containers: web-app `POST /signup` and auth-service `POST /signup` share `rid=75482332`.
