# SpendWise

A personal finance tracker built with Flask. Create an account, track income and expenses, search transactions, and review balances and charts.

## Run locally

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open http://localhost:8000. Press Ctrl+C to stop all nine services.

Accounts and transactions persist in SQLite files under `instance/`. Local signing keys are generated automatically and saved there. Optional settings can be copied from `.env.example` to `.env`. Ports default to 8000 (browser), 5001 (authentication), 5002 (transactions), 5003 (budgets), 5004 (notifications), 5005 (reports), 5006 (recurring), 5007 (savings), and 5008 (imports); all bind to localhost.

The app includes password hashing, JWT authentication, and CSRF protection. Backend services and the browser interface retain their existing routes. See [API reference](docs/API.md).

## Monthly budgets

The dashboard supports category budgets for any selected month. Add, edit, or delete limits, and see spending, remaining amounts, and warnings at 80% usage or when over budget. Spending uses transaction creation dates in UTC; income is excluded. Category matching ignores case and surrounding whitespace. Each user can have one budget per category per month.

`budget-service` stores limits separately in `instance/budgets.sqlite3`. It reads spending through the authenticated transaction API, without accessing the transaction database. If spending is unavailable, the dashboard shows an error rather than an incorrect zero.

Run service API checks with `.venv/bin/python -m unittest discover -s tests`.

## Notifications

The dashboard displays persistent in-app budget alerts, an unread count, and buttons to mark one or all alerts as read. Notification Service stores its own data in `instance/notifications.sqlite3` and obtains trusted spending through the authenticated Budget API.

Alerts synchronize when the dashboard opens, including after browser transaction or budget changes. API-only changes appear on the next dashboard visit or an explicit sync call. Failed synchronization is reported and retried on the next visit; saved alerts remain available. This version does not send emails or run a background queue.

Each user receives at most one near-limit and one over-limit alert per category/month. Refreshes, retries, and marking alerts read do not generate duplicates. Alerts preserve the amount at creation, even if spending later changes or the budget is removed. The list shows the latest 50 alerts; unread count includes all saved unread alerts.

## Monthly reports and CSV export

Open **Monthly Reports** from the dashboard or visit http://localhost:8000/reports. Choose a month to view income, expenses, monthly net balance, transaction count, category spending, highest spending category, and the previous month's expense change. Budget comparison is also included; if Budget Service is unavailable, transaction reporting still works with an explicit warning.

Reporting Service is stateless: it reads authenticated Transaction and Budget APIs and does not access their databases. Dates are evaluated in UTC. A percentage comparison is unavailable when prior-month expenses were zero. Monthly balance is that month's income minus expenses, rather than a carried-forward account balance.

**Download Transactions CSV** exports only the selected month's transactions for the signed-in user, with UTC dates and two-decimal amounts. Empty months export column headers. User-entered spreadsheet formula prefixes are escaped. Reports are generated on request; exports are not saved on the server. Updates in the source services appear on the next report request.

## Recurring transactions

Open **Recurring** to schedule daily, weekly, or monthly income/expenses. The launcher runs a worker every 60 seconds while the app is running, with a **Process Due Entries Now** button for immediate processing. No user access tokens are stored; the trusted worker uses the existing shared signing secret to issue short-lived user-scoped tokens for Transaction Service.

Due dates use UTC and are preserved as transaction dates. Monthly schedules retain the original day, clamping to the last day of shorter months. Paused schedules are not processed; resuming catches up missed dates. Restarting the app also catches up missed dates, with a maximum of 100 posts per cycle. Failures retain the due date for retry. Schedule UUID/date idempotency keys prevent duplicate transaction creation on retries or restarts. Deleting schedules does not delete already-posted transactions.

## Savings goals

Open **Savings Goals** to create/edit a target and optional target date. Record explicit contributions, see progress, remove mistaken contributions, or delete a goal with its contribution records. Contributions do not move money or change income/expenses; this is a tracking feature, not an automatic deduction from account balance. Request keys prevent duplicate contributions from repeated form submissions.

## CSV import

Open **Import CSV**, upload a UTF-8 file, inspect the preview, then explicitly confirm. Required columns are `date,title,type,category,amount`; SpendWise report export headers are also supported. Dates accept YYYY-MM-DD or ISO timestamps and normalize to UTC. Files are limited to 1 MB and 1000 data rows.

Every row is validated before import. If any row is invalid, fix the CSV and upload it again. Duplicate rows are skipped using UTC calendar date, case-insensitive trimmed title/category, type and amount. Existing transactions, prior imports, and repeats within a file are checked. Intentionally identical transactions on the same day are treated as duplicates by CSV import; use the normal transaction form for such entries.

Confirmation is retry-safe, including after a partial network failure. Transaction Service atomically saves an idempotency receipt with each imported/recurring transaction; receipts remain even after transaction deletion to prevent accidental recreation. Imported rows keep their original dates for monthly budgets/reports. Previews and import receipts persist in `instance/imports.sqlite3`; raw uploaded files are not stored separately. New services own their databases and use APIs to access other services' records.

## Interface

All screens share a responsive workspace layout with active navigation, accessible form labels, visible keyboard focus, and readable financial amounts. Desktop navigation uses a sidebar; smaller screens use a collapsible menu. Transactions open in a native dialog, and destructive actions prompt for confirmation. Savings and recurring editors sit alongside their records; CSV import follows upload, preview, and confirmation steps.

Design tokens and responsive components live in `services/web-app/static/style.css`. Shared Jinja layouts are in `app_base.html`, `auth_base.html`, and `ui.html`. Small enhancements in `app.js` handle dialogs, navigation focus, password visibility, message dismissal, and duplicate-submit prevention. Icons are inline SVG and no external fonts or frontend build tools are required.
