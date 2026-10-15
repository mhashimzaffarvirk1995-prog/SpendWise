# API Reference

The browser app runs at http://localhost:8000, authentication at http://127.0.0.1:5001, and transactions at http://127.0.0.1:5002.

## Conventions

- Request and response bodies are JSON for service APIs.
- Browser routes use HTML forms and Flask sessions.
- Transaction routes require `Authorization: Bearer <token>`.
- Services add `X-Request-ID` to responses.
- Clients may send `X-Request-ID`; otherwise a new UUID is generated.
- Amounts are numeric values and are rounded to two decimals in summary responses.

## Browser Routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`, `POST` | `/` | Same handler as `/login`; login page, login submit, or redirect to dashboard when already logged in |
| `GET` | `/login` | Login page |
| `POST` | `/login` | Submit login form |
| `GET` | `/signup` | Signup page |
| `POST` | `/signup` | Submit signup form |
| `GET` | `/dashboard` | Dashboard with optional `filter`, `search`, and `edit_id` query params |
| `POST` | `/dashboard` | Create or update a transaction |
| `POST` | `/delete/<transaction_id>` | Delete a transaction |
| `GET` | `/logout` | Clear session and redirect to login |
| `GET` | `/health` | Web app liveness |
| `GET` | `/ready` | Web app readiness and backend dependency checks |

Browser POST routes require the hidden `csrf_token` field rendered by the templates.

### `GET /ready`

The web app checks both backend readiness endpoints.

Success:

```json
{
  "status": "ready",
  "service": "web-app",
  "check": "ready",
  "checks": {
    "auth_service": "ok",
    "transaction_service": "ok"
  }
}
```

Failure returns `503` with `status: "not_ready"` and one or both checks set to `error`.

## Auth Service

Local base URL:

```text
http://127.0.0.1:5001
```

### `GET /health`

Returns process liveness.

```json
{
  "status": "ok",
  "service": "auth-service",
  "check": "health"
}
```

### `GET /ready`

Checks database connectivity.

Success:

```json
{
  "status": "ready",
  "service": "auth-service",
  "check": "ready",
  "checks": {
    "database": "ok"
  }
}
```

Failure returns `503` with `status: "not_ready"`.

### `POST /signup`

Creates a user.

Request:

```json
{
  "name": "Sam Lee",
  "email": "sam@example.com",
  "password": "secret123"
}
```

Validation:

- `name` is required.
- `email` is required and normalized to lowercase.
- `password` must be at least 6 characters.
- `email` must be unique.

Success response: `201`

```json
{
  "user": {
    "id": 1,
    "name": "Sam Lee",
    "email": "sam@example.com",
    "created_at": "2026-06-14T12:00:00"
  }
}
```

Common errors:

| Status | Meaning |
| ---: | --- |
| `400` | Missing name/email or password shorter than 6 characters |
| `409` | Email already exists |

### `POST /login`

Authenticates a user and returns a JWT.

Request:

```json
{
  "email": "sam@example.com",
  "password": "secret123"
}
```

Success response: `200`

```json
{
  "access_token": "<jwt>",
  "token_type": "Bearer",
  "expires_in": 7200,
  "user": {
    "id": 1,
    "name": "Sam Lee",
    "email": "sam@example.com",
    "created_at": "2026-06-14T12:00:00"
  }
}
```

Error:

| Status | Meaning |
| ---: | --- |
| `401` | Invalid email or password |

### `GET /me`

Returns the current user for a valid bearer token.

Headers:

```text
Authorization: Bearer <jwt>
```

Success response: `200`

```json
{
  "user": {
    "id": 1,
    "name": "Sam Lee",
    "email": "sam@example.com",
    "created_at": "2026-06-14T12:00:00"
  }
}
```

Common errors:

| Status | Meaning |
| ---: | --- |
| `401` | Missing, invalid, or expired token |
| `404` | Token subject no longer maps to a user |

## Transaction Service

Local base URL:

```text
http://127.0.0.1:5002
```

All transaction routes require:

```text
Authorization: Bearer <jwt>
```

The JWT must be signed with `JWT_SECRET` and have issuer `spendwise-auth-service`.

### `GET /health`

Returns process liveness.

```json
{
  "status": "ok",
  "service": "transaction-service",
  "check": "health"
}
```

### `GET /ready`

Checks database connectivity.

Success:

```json
{
  "status": "ready",
  "service": "transaction-service",
  "check": "ready",
  "checks": {
    "database": "ok"
  }
}
```

Failure returns `503` with `status: "not_ready"`.

### `GET /transactions`

Returns dashboard data for the current user.

Query parameters:

| Name | Values | Purpose |
| --- | --- | --- |
| `filter` | `all`, `income`, `expense` | Optional transaction type filter. Invalid values fall back to `all`. |
| `search` | text | Optional case-insensitive search against title and category. |

Success response: `200`

```json
{
  "transactions": [
    {
      "id": 10,
      "user_id": 1,
      "title": "Groceries",
      "amount": 45.5,
      "type": "expense",
      "category": "Food",
      "created_at": "2026-06-14T12:00:00"
    }
  ],
  "summary": {
    "total_income": 5000.0,
    "total_expense": 45.5,
    "balance": 4954.5
  },
  "charts": {
    "income_expense": [
      {
        "label": "Income",
        "amount": 5000.0,
        "percent": 100.0
      },
      {
        "label": "Expense",
        "amount": 45.5,
        "percent": 0.91
      }
    ],
    "expense_by_category": [
      {
        "label": "Food",
        "amount": 45.5,
        "percent": 100.0
      }
    ],
    "recent_expense_trend": [
      {
        "label": "Sun",
        "date": "2026-06-14",
        "amount": 45.5,
        "percent": 100.0
      }
    ],
    "has_income_expense": true,
    "has_expense_by_category": true,
    "has_recent_expense_trend": true
  },
  "filter": "all",
  "search": ""
}
```

Notes:

- `summary` and `charts` are based on all transactions for the current user, not the filtered result set.
- `transactions` respects `filter` and `search`.
- `expense_by_category` returns the top five categories plus `Other` when more categories exist.
- `recent_expense_trend` returns seven entries for the last seven calendar days, even when some days have zero spending.
- Chart percentages are display helpers. Income versus expense is scaled against the larger of total income and total expense; category and trend percentages are scaled against their own chart totals.

### `GET /transactions/<transaction_id>`

Returns one transaction owned by the current user.

Success response: `200`

```json
{
  "transaction": {
    "id": 10,
    "user_id": 1,
    "title": "Groceries",
    "amount": 45.5,
    "type": "expense",
    "category": "Food",
    "created_at": "2026-06-14T12:00:00"
  }
}
```

Errors:

| Status | Meaning |
| ---: | --- |
| `401` | Missing or invalid bearer token |
| `404` | Transaction does not exist for current user |

### `POST /transactions`

Creates a transaction for the current user.

Request:

```json
{
  "title": "Salary",
  "amount": 5000,
  "type": "income",
  "category": "Job"
}
```

Validation:

- `title` is required.
- `category` is required.
- `amount` must be a number greater than zero.
- `type` must be `income` or `expense`.

Success response: `201`

```json
{
  "transaction": {
    "id": 11,
    "user_id": 1,
    "title": "Salary",
    "amount": 5000.0,
    "type": "income",
    "category": "Job",
    "created_at": "2026-06-14T12:00:00"
  }
}
```

Errors:

| Status | Meaning |
| ---: | --- |
| `400` | Invalid transaction payload |
| `401` | Missing or invalid bearer token |

### `PUT /transactions/<transaction_id>`

Updates a transaction owned by the current user.

Request body matches `POST /transactions`.

Success response: `200`

```json
{
  "transaction": {
    "id": 11,
    "user_id": 1,
    "title": "Salary",
    "amount": 5100.0,
    "type": "income",
    "category": "Job",
    "created_at": "2026-06-14T12:00:00"
  }
}
```

Errors:

| Status | Meaning |
| ---: | --- |
| `400` | Invalid transaction payload |
| `401` | Missing or invalid bearer token |
| `404` | Transaction does not exist for current user |

### `DELETE /transactions/<transaction_id>`

Deletes a transaction owned by the current user.

Success response: `200`

```json
{
  "message": "Transaction deleted."
}
```

Errors:

| Status | Meaning |
| ---: | --- |
| `401` | Missing or invalid bearer token |
| `404` | Transaction does not exist for current user |

## Budget Service

Local base URL: `http://127.0.0.1:5003`. Budget endpoints require a bearer token issued by the auth service and isolate records by user.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process health |
| GET | `/ready` | Database and transaction service readiness |
| GET | `/budgets?month=2026-10` | Limits with actual monthly expense totals |
| POST | `/budgets` | Create a monthly category limit |
| PUT | `/budgets/<id>` | Update a limit owned by the current user |
| DELETE | `/budgets/<id>` | Delete a limit owned by the current user |

Create/update JSON: `{"month":"2026-10","category":"Food","amount":15000}`. Month must be YYYY-MM and amount must be positive with at most two decimal places. Category names are trimmed and casefolded; duplicates per user/month return 409. Missing/invalid authentication returns 401, inaccessible records return 404, invalid input returns 400, and unavailable spending returns 503.

List returns `month` and `budgets`. Each budget includes `id`, `month`, `category`, `amount`, `spent`, `remaining`, `percent`, `progress` (capped at 100), and `status` (`ok`, `near` at 80% or above, `over` above the limit). Expenses are matched by creation month in UTC; income and other months are excluded.

Browser POST routes `/budgets/save` and `/budgets/<id>/delete` require CSRF tokens and a logged-in session.

## Notification Service

Local base URL: `http://127.0.0.1:5004`. All notification endpoints require an auth-service bearer token and restrict records to its user.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process health |
| GET | `/ready` | Notification database readiness |
| POST | `/notifications/sync` | Fetch budget alerts for `{"month":"2026-10"}`; defaults to current UTC month |
| GET | `/notifications` | Latest 50 notifications and total `unread_count` |
| POST | `/notifications/<id>/read` | Mark the user's notification read; repeat calls are safe |
| POST | `/notifications/read-all` | Mark all the user's notifications read |

Sync returns `created` and deduplicates by user, month, category, and warning level. It never accepts client-supplied alert text or amounts. Missing/expired auth returns 401, inaccessible records 404, invalid month 400, and unavailable budget data 503. Existing alerts are retained if synchronization fails.

Browser read routes use the same paths and require session authentication plus CSRF protection. Dashboard visits synchronize the selected and current UTC month before loading notifications.

## Reporting Service

Local base URL: `http://127.0.0.1:5005`. Report endpoints require an auth-service bearer token and forward it to source APIs, so reports include only that user's data. No user ID parameter is accepted for selecting another user's records.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process health |
| GET | `/ready` | Transaction API readiness |
| GET | `/reports/monthly?month=2026-10` | Monthly summary, previous-month comparison, category expenses, and budgets |
| GET | `/reports/transactions.csv?month=2026-10` | Download the user's selected-month transactions |

Month defaults to the current UTC month and must be YYYY-MM with a valid year/month. Monthly JSON includes `month`, `summary` (`income`, `expenses`, `balance`, `transaction_count`), `previous_month`, `previous_summary`, `expense_change`, `expense_change_percent`, `categories`, `highest_spending_category`, `budgets`, and `budget_error`. Percentage change is null when the earlier month's expenses were zero. Category matching ignores case and surrounding whitespace. Amounts are rounded to cents before totaling.

Unavailable transaction data returns 503 instead of incorrect zero totals. Unavailable budgets leave transaction reports available with `budget_error`. Invalid month returns 400; missing, invalid, or expired authentication returns 401. Responses disable caching. CSV includes a UTF-8 BOM, UTC dates, title, type, category, and two-decimal amount; potentially executable text cells receive a leading apostrophe.

Browser routes: `/reports` shows the monthly report and `/reports/download` proxies the CSV download using the signed-in session. Both require login.

## Recurring Service

Local base URL: `http://127.0.0.1:5006`. All data endpoints require a bearer token. `/health` and `/ready` expose process/database status.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/schedules` | Current user's schedules |
| POST | `/schedules` | Create a schedule |
| PATCH | `/schedules/<id>` | Pause/resume with `{"active":false}` or `true` |
| DELETE | `/schedules/<id>` | Delete schedule, retaining transactions |
| POST | `/schedules/run-due` | Process the user's due entries now |

Create JSON: `{"title":"Rent","category":"Home","amount":50000,"type":"expense","frequency":"monthly","start_date":"2026-10-01"}`. Frequency accepts daily/weekly/monthly. Dates use UTC, monthly cadence keeps its original day, and resuming includes missed dates. The launcher starts the automatic 60-second worker. Processing returns `created`, `failed`, and `busy`; at most 100 entries are processed per cycle. Retry errors appear as `last_error` on schedules.

## Savings Service

Local base URL: `http://127.0.0.1:5007`. All data endpoints require a bearer token. `/health` and `/ready` expose process/database status.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/goals` | Current user's goals, totals and contributions |
| POST | `/goals` | Create goal |
| PUT | `/goals/<id>` | Edit goal |
| DELETE | `/goals/<id>` | Delete goal and its contributions |
| POST | `/goals/<id>/contributions` | Add a contribution |
| DELETE | `/goals/<id>/contributions/<id>` | Remove mistaken contribution |

Goal JSON: `{"name":"Laptop","target":200000,"target_date":"2027-02-01"}`. Target date is optional. Contribution JSON: `{"amount":5000,"request_key":"unique-request-id"}`. Repeating a key/amount returns the existing result; reusing it with a different amount returns 409. Goal output includes saved, remaining, capped percent, completed, and contributions. Contribution records are separate from financial transactions.

## Import Service

Local base URL: `http://127.0.0.1:5008`. All data endpoints require a bearer token. `/health` and `/ready` expose process/database status.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/imports/preview` | Multipart `file` upload; validate without posting transactions |
| GET | `/imports/<id>` | Current user's persisted preview |
| POST | `/imports/<id>/confirm` | Explicitly import valid non-duplicate rows |

UTF-8 CSV headers: `date,title,type,category,amount`, or the equivalent SpendWise export headers. At most 1 MB/1000 data rows. Preview includes `rows` (payload, line, fingerprint, duplicate), `errors`, and `confirmed`. Validation errors block confirmation. Duplicate identity uses UTC day, normalized title/category, type and amount. Confirmation returns imported/skipped/confirmed; partial failure returns 503 with progress and can be retried safely. Inaccessible previews return 404. Every row is sent through Transaction API, preserving its date.

## Transaction creation extensions

`POST /transactions` accepts optional `created_at` as a date/ISO timestamp (UTC normalized) and optional `Idempotency-Key` header (1–200 characters). The receipt and transaction commit atomically. Same key/payload returns 200 with `replayed:true`; same key with different data returns 409. Keys are scoped to the authenticated user and receipts remain after transaction deletion. Existing transactions are preserved; the launcher creates the separate receipt table without changing existing rows. Amounts must be finite, positive, below one trillion, and have at most two decimal places.

Browser routes `/recurring`, `/savings`, and `/imports` require login. All browser mutation forms require CSRF protection. Each new API restricts records to the authenticated user; invalid input returns 400 and missing/expired authentication returns 401.
