# SpendWise

A personal finance tracker built as **nine Flask microservices** — and a hands-on DevOps project that takes it from `python run.py` on a laptop to production-grade AWS, one week at a time.

![SpendWise dashboard](app-frontend.png)

[![CI](https://github.com/mhashimzaffarvirk1995-prog/SpendWise/actions/workflows/ci.yml/badge.svg)](https://github.com/mhashimzaffarvirk1995-prog/SpendWise/actions/workflows/ci.yml)

**What the app does:** sign up, track income and expenses, set monthly category budgets with alerts, see monthly reports and CSV exports, schedule recurring entries, track savings goals, and import transactions from CSV. Password hashing, JWT authentication and CSRF protection are built in.

**What the DevOps project adds:** an AWS workbench managed only through Session Manager, LVM storage, a hardened systemd service, nginx with Let's Encrypt TLS, rate limiting and request tracing, per-service Docker images with security and smoke gates, CI on every push, and a written troubleshooting log for every failure found along the way.

---

## Architecture

~~~
Browser ──HTTPS──▶ nginx (TLS 1.3, HTTP/2, rate limits, X-Request-ID)
                     │
                     ▼
                  web-app ──▶ auth · transaction · budget · notification · reporting
                                · recurring (+ worker) · savings · import
                     each service owns its own database; services talk only over HTTP APIs
~~~

| Port | Service | Database | Calls |
|---|---|---|---|
| 8000 | web-app (browser UI) | — | all services |
| 5001 | auth-service | `auth.sqlite3` | — |
| 5002 | transaction-service | `transactions.sqlite3` | — |
| 5003 | budget-service | `budgets.sqlite3` | transaction |
| 5004 | notification-service | `notifications.sqlite3` | budget |
| 5005 | reporting-service | — (stateless) | transaction, budget |
| 5006 | recurring-service + worker | `recurring.sqlite3` | transaction |
| 5007 | savings-service | `savings.sqlite3` | — |
| 5008 | import-service | `imports.sqlite3` | transaction |

- **Service-owned databases:** no service reads another service's database.
- **Auth:** auth-service issues a JWT at login; every backend verifies it locally with the shared `JWT_SECRET`.
- **Tracing:** one `X-Request-ID` follows a request from nginx through every service — see [request flow](docs/request-flow-local.md).

More: [architecture](docs/architecture.md) · [service-by-service request flow](docs/request-flow-local.md) · [API reference](docs/API.md)

---

## Run it

### 1. Local (Python)
Requires Python 3.10+.
~~~bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py            # http://localhost:8000 — Ctrl+C stops all nine services
~~~
Data and auto-generated signing keys live in `instance/`. Optional settings: copy `.env.example` to `.env`.

### 2. Docker (one container per service)
~~~bash
scripts/docker-build.sh dev        # build 9 images (tagged dev + git SHA)
scripts/docker-smoke.sh dev        # every image must become healthy
scripts/docker-scan.sh dev         # Trivy gate: fails on any CRITICAL CVE
scripts/docker-run-local.sh dev    # network + volumes + 10 containers → http://127.0.0.1:9000
~~~
The run script expects an env file at `~/spendwise-docker.env` with `SECRET_KEY`, `JWT_SECRET` and the `*_SERVICE_URL` values (`http://<service>:8080`). Details: [docs/docker.md](docs/docker.md).

### 3. Server (systemd + nginx)
The workbench runs SpendWise as a hardened systemd unit behind nginx with TLS:
`deploy/systemd/`, `deploy/nginx/`, `deploy/cron/`, `deploy/logrotate/`, `deploy/hardening/`. Health check: `scripts/healthcheck.sh` (exit 1 on any failure).

### Tests
~~~bash
.venv/bin/python -m unittest discover -s tests
~~~
GitHub Actions runs the same tests on every `week-*` push and every pull request to `main`.

---

## DevOps roadmap progress

A 26-week, AWS-first plan. Each week ships something real, is merged through a reviewed PR, and is tagged.

| Week | Theme | Highlights | Tag |
|---|---|---|---|
| 0 | AWS workbench | budgets & alerts, EC2 via Session Manager only (no SSH, no open ports), baseline architecture doc | `v0.1-baseline` |
| 1 | Linux administration | LVM data volume grown online, non-root hardened systemd unit (restart limits, memory caps), nightly SQLite backups + restore test, cron + logrotate, ufw / sshd / fail2ban, `healthcheck.sh`, 10 break-and-fix drills (32 automated checks) | `v0.2-week01` |
| 2 | Networking + Git | nginx reverse proxy, Elastic IP + Let's Encrypt (TLS 1.3, HTTP/2, HSTS), request IDs end to end, rate limiting, 502 vs 504 drills, network tools, VPC CIDR plan, first CI workflow | `v0.3-week02` |
| 3 | Docker | gunicorn + `wsgi.py` per service, separate recurring worker, multi-stage non-root images (1.68 GB → ~240 MB), custom network + volumes, Trivy gate (fixed a CRITICAL PyJWT CVE), smoke-test gate, fault-isolation and OOM drills | `v0.4-week03` |
| 4 | Postgres, Redis, Compose | *next* | |

Coming up: AWS core (IAM, VPC, RDS, ECS), Terraform, Ansible, CI/CD with Jenkins and GitHub Actions, Kubernetes (k3s → EKS), Rancher, GitOps (Argo CD, Flux), observability (Prometheus, Grafana, Loki, OpenTelemetry), DevSecOps and SRE.

---

## Documentation

| Doc | What's inside |
|---|---|
| [architecture.md](docs/architecture.md) | services, ports, databases, call graph |
| [week-01.md](docs/week-01.md) | Linux notes, e.g. why `local-keys.json` must be 0600 |
| [request-flow-local.md](docs/request-flow-local.md) | one transaction traced hop by hop |
| [network-tools.md](docs/network-tools.md) | curl, dig, traceroute, openssl, ss, tcpdump — one question each |
| [cidr-plan.md](docs/cidr-plan.md) | VPC subnet plan for AWS |
| [docker.md](docs/docker.md) | images, tags, scans, smoke tests, container drills |
| [troubleshooting.md](docs/troubleshooting.md) | every failure found so far: symptom → cause → fix |
| [API.md](docs/API.md) | backend API reference |

## Repository layout

~~~
services/<name>/        app.py, wsgi.py, requirements.txt (one folder per service)
services/common/        shared helpers
services/Dockerfile     one multi-stage Dockerfile for all services
deploy/                 systemd, nginx, cron, logrotate, hardening
scripts/                healthcheck, backup, docker build/run/smoke/scan, verify, log-table
.github/workflows/      CI
docs/                   notes, runbooks, troubleshooting log
tests/                  unit tests
run.py                  local launcher (all services in one process)
~~~

---

## Features in detail

<details>
<summary>Budgets, notifications, reports, recurring transactions, savings, CSV import and interface</summary>

### Monthly budgets

The dashboard supports category budgets for any selected month. Add, edit, or delete limits, and see spending, remaining amounts, and warnings at 80% usage or when over budget. Spending uses transaction creation dates in UTC; income is excluded. Category matching ignores case and surrounding whitespace. Each user can have one budget per category per month.

`budget-service` stores limits separately in `instance/budgets.sqlite3`. It reads spending through the authenticated transaction API, without accessing the transaction database. If spending is unavailable, the dashboard shows an error rather than an incorrect zero.

Run service API checks with `.venv/bin/python -m unittest discover -s tests`.

### Notifications

The dashboard displays persistent in-app budget alerts, an unread count, and buttons to mark one or all alerts as read. Notification Service stores its own data in `instance/notifications.sqlite3` and obtains trusted spending through the authenticated Budget API.

Alerts synchronize when the dashboard opens, including after browser transaction or budget changes. API-only changes appear on the next dashboard visit or an explicit sync call. Failed synchronization is reported and retried on the next visit; saved alerts remain available. This version does not send emails or run a background queue.

Each user receives at most one near-limit and one over-limit alert per category/month. Refreshes, retries, and marking alerts read do not generate duplicates. Alerts preserve the amount at creation, even if spending later changes or the budget is removed. The list shows the latest 50 alerts; unread count includes all saved unread alerts.

### Monthly reports and CSV export

Open **Monthly Reports** from the dashboard or visit http://localhost:8000/reports. Choose a month to view income, expenses, monthly net balance, transaction count, category spending, highest spending category, and the previous month's expense change. Budget comparison is also included; if Budget Service is unavailable, transaction reporting still works with an explicit warning.

Reporting Service is stateless: it reads authenticated Transaction and Budget APIs and does not access their databases. Dates are evaluated in UTC. A percentage comparison is unavailable when prior-month expenses were zero. Monthly balance is that month's income minus expenses, rather than a carried-forward account balance.

**Download Transactions CSV** exports only the selected month's transactions for the signed-in user, with UTC dates and two-decimal amounts. Empty months export column headers. User-entered spreadsheet formula prefixes are escaped. Reports are generated on request; exports are not saved on the server. Updates in the source services appear on the next report request.

### Recurring transactions

Open **Recurring** to schedule daily, weekly, or monthly income/expenses. The launcher runs a worker every 60 seconds while the app is running, with a **Process Due Entries Now** button for immediate processing. No user access tokens are stored; the trusted worker uses the existing shared signing secret to issue short-lived user-scoped tokens for Transaction Service.

Due dates use UTC and are preserved as transaction dates. Monthly schedules retain the original day, clamping to the last day of shorter months. Paused schedules are not processed; resuming catches up missed dates. Restarting the app also catches up missed dates, with a maximum of 100 posts per cycle. Failures retain the due date for retry. Schedule UUID/date idempotency keys prevent duplicate transaction creation on retries or restarts. Deleting schedules does not delete already-posted transactions.

### Savings goals

Open **Savings Goals** to create/edit a target and optional target date. Record explicit contributions, see progress, remove mistaken contributions, or delete a goal with its contribution records. Contributions do not move money or change income/expenses; this is a tracking feature, not an automatic deduction from account balance. Request keys prevent duplicate contributions from repeated form submissions.

### CSV import

Open **Import CSV**, upload a UTF-8 file, inspect the preview, then explicitly confirm. Required columns are `date,title,type,category,amount`; SpendWise report export headers are also supported. Dates accept YYYY-MM-DD or ISO timestamps and normalize to UTC. Files are limited to 1 MB and 1000 data rows.

Every row is validated before import. If any row is invalid, fix the CSV and upload it again. Duplicate rows are skipped using UTC calendar date, case-insensitive trimmed title/category, type and amount. Existing transactions, prior imports, and repeats within a file are checked. Intentionally identical transactions on the same day are treated as duplicates by CSV import; use the normal transaction form for such entries.

Confirmation is retry-safe, including after a partial network failure. Transaction Service atomically saves an idempotency receipt with each imported/recurring transaction; receipts remain even after transaction deletion to prevent accidental recreation. Imported rows keep their original dates for monthly budgets/reports. Previews and import receipts persist in `instance/imports.sqlite3`; raw uploaded files are not stored separately. New services own their databases and use APIs to access other services' records.

### Interface

All screens share a responsive workspace layout with active navigation, accessible form labels, visible keyboard focus, and readable financial amounts. Desktop navigation uses a sidebar; smaller screens use a collapsible menu. Transactions open in a native dialog, and destructive actions prompt for confirmation. Savings and recurring editors sit alongside their records; CSV import follows upload, preview, and confirmation steps.

Design tokens and responsive components live in `services/web-app/static/style.css`. Shared Jinja layouts are in `app_base.html`, `auth_base.html`, and `ui.html`. Small enhancements in `app.js` handle dialogs, navigation focus, password visibility, message dismissal, and duplicate-submit prevention. Icons are inline SVG and no external fonts or frontend build tools are required.

</details>
