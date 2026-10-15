import json
import logging
import os
import secrets
from datetime import datetime, timezone
from uuid import uuid4

import requests
from dotenv import load_dotenv
from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for

load_dotenv()

VALID_APP_ENVS = {"development", "testing", "production"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_SAMESITE_VALUES = {"Lax", "Strict", "None"}


def parse_bool_env(name, default=False):
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise RuntimeError("WEB_APP_PORT must be a valid integer.")

    if port < 1 or port > 65535:
        raise RuntimeError("WEB_APP_PORT must be between 1 and 65535.")

    return port


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


APP_ENV = os.getenv("APP_ENV", "development").lower()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SECRET_KEY = os.getenv("SECRET_KEY")
WEB_APP_PORT = parse_port(os.getenv("WEB_APP_PORT", "5000"))
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:5001").rstrip("/")
TRANSACTION_SERVICE_URL = os.getenv("TRANSACTION_SERVICE_URL", "http://127.0.0.1:5002").rstrip("/")
BUDGET_SERVICE_URL = os.getenv("BUDGET_SERVICE_URL", "http://127.0.0.1:5003").rstrip("/")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:5004").rstrip("/")
REPORTING_SERVICE_URL = os.getenv("REPORTING_SERVICE_URL", "http://127.0.0.1:5005").rstrip("/")
RECURRING_SERVICE_URL = os.getenv("RECURRING_SERVICE_URL", "http://127.0.0.1:5006").rstrip("/")
SAVINGS_SERVICE_URL = os.getenv("SAVINGS_SERVICE_URL", "http://127.0.0.1:5007").rstrip("/")
IMPORT_SERVICE_URL = os.getenv("IMPORT_SERVICE_URL", "http://127.0.0.1:5008").rstrip("/")
SESSION_COOKIE_SECURE = parse_bool_env("SESSION_COOKIE_SECURE", APP_ENV == "production")
SESSION_COOKIE_HTTPONLY = parse_bool_env("SESSION_COOKIE_HTTPONLY", True)
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
SERVICE_TIMEOUT_SECONDS = float(os.getenv("SERVICE_TIMEOUT_SECONDS", "5"))

if APP_ENV not in VALID_APP_ENVS:
    raise RuntimeError("APP_ENV must be one of: development, testing, production.")

if LOG_LEVEL not in VALID_LOG_LEVELS:
    raise RuntimeError("LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL.")

if SESSION_COOKIE_SAMESITE not in VALID_SAMESITE_VALUES:
    raise RuntimeError("SESSION_COOKIE_SAMESITE must be one of: Lax, Strict, None.")

if SESSION_COOKIE_SAMESITE == "None" and not SESSION_COOKIE_SECURE:
    raise RuntimeError("SESSION_COOKIE_SECURE must be true when SESSION_COOKIE_SAMESITE is None.")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is required.")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "timestamp": utc_timestamp(),
            "service": "web-app",
            "request_id": getattr(record, "request_id", None),
        }

        for key in ["method", "path", "status_code", "user_id", "duration_ms"]:
            value = getattr(record, key, None)
            if value is not None:
                log_data[key] = value

        return json.dumps(log_data)


logger = logging.getLogger("web-app")
logger.setLevel(getattr(logging, LOG_LEVEL))
logger.handlers.clear()

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.propagate = False

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
app.config["SESSION_COOKIE_HTTPONLY"] = SESSION_COOKIE_HTTPONLY
app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE


@app.context_processor
def navigation_notifications():
    if request.endpoint == "dashboard" or not session.get("access_token"):
        return {}
    try:
        response, data = service_request("GET", f"{NOTIFICATION_SERVICE_URL}/notifications", headers=auth_headers())
        return {"unread_count": data.get("unread_count", 0)} if response.ok else {}
    except requests.RequestException:
        return {}


@app.template_filter("short_date")
def format_short_date(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d %b %Y")
    except (ValueError, TypeError, AttributeError):
        return "—"


@app.template_filter("money")
def format_money(value):
    return format(float(value or 0), ",.2f")


def auth_headers():
    token = session.get("access_token")

    if not token:
        return {}

    return {
        "Authorization": f"Bearer {token}",
        "X-Request-ID": g.get("request_id", ""),
    }


def service_request(method, url, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.setdefault("X-Request-ID", g.get("request_id", ""))

    response = requests.request(
        method,
        url,
        headers=headers,
        timeout=SERVICE_TIMEOUT_SECONDS,
        **kwargs,
    )

    try:
        data = response.json()
    except ValueError:
        data = {}

    return response, data


def current_user():
    return session.get("user")


def require_login():
    if not session.get("access_token") or not current_user():
        flash("Please login to access SpendWise.", "error")
        return redirect(url_for("login"))

    return None


def build_empty_dashboard_state():
    return {
        "transactions": [],
        "summary": {
            "total_income": 0,
            "total_expense": 0,
            "balance": 0,
        },
        "charts": {
            "income_expense": [
                {"label": "Income", "amount": 0, "percent": 0},
                {"label": "Expense", "amount": 0, "percent": 0},
            ],
            "expense_by_category": [],
            "recent_expense_trend": [],
            "has_income_expense": False,
            "has_expense_by_category": False,
            "has_recent_expense_trend": False,
        },
        "filter": "all",
        "search": "",
    }


@app.before_request
def start_request_context():
    g.request_id = request.headers.get("X-Request-ID", str(uuid4()))
    g.request_started_at = datetime.now(timezone.utc)

    if request.method == "POST":
        form_token = request.form.get("csrf_token")
        session_token = session.get("csrf_token")

        if not form_token or not session_token or not secrets.compare_digest(form_token, session_token):
            logger.warning(
                "CSRF validation failed",
                extra={
                    "request_id": g.request_id,
                    "method": request.method,
                    "path": request.path,
                },
            )
            abort(400)


@app.context_processor
def inject_csrf_token():
    def csrf_token():
        token = session.get("csrf_token")

        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token

        return token

    return {"csrf_token": csrf_token}


@app.after_request
def log_request(response):
    response.headers["X-Request-ID"] = g.get("request_id", "")

    started_at = g.get("request_started_at")
    duration_ms = None

    if started_at:
        duration = datetime.now(timezone.utc) - started_at
        duration_ms = round(duration.total_seconds() * 1000, 2)

    logger.info(
        "Request completed",
        extra={
            "request_id": g.get("request_id"),
            "method": request.method,
            "path": request.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )

    return response


@app.route("/health")
def health():
    return {"status": "ok", "service": "web-app", "check": "health"}, 200


@app.route("/ready")
def ready():
    checks = {
        "auth_service": "unknown",
        "transaction_service": "unknown",
        "budget_service": "unknown",
        "notification_service": "unknown",
        "reporting_service": "unknown",
        "recurring_service": "unknown",
        "savings_service": "unknown",
        "import_service": "unknown",
    }
    status_code = 200

    for key, service_url in {
        "auth_service": AUTH_SERVICE_URL,
        "transaction_service": TRANSACTION_SERVICE_URL,
        "budget_service": BUDGET_SERVICE_URL,
        "notification_service": NOTIFICATION_SERVICE_URL,
        "reporting_service": REPORTING_SERVICE_URL,
        "recurring_service": RECURRING_SERVICE_URL,
        "savings_service": SAVINGS_SERVICE_URL,
        "import_service": IMPORT_SERVICE_URL,
    }.items():
        try:
            response, _ = service_request("GET", f"{service_url}/ready")
            checks[key] = "ok" if response.ok else "error"
            if not response.ok:
                status_code = 503
        except requests.RequestException as error:
            checks[key] = "error"
            status_code = 503
            logger.error(
                f"Readiness check failed for {key}: {error}",
                extra={"request_id": g.get("request_id")},
            )

    return {
        "status": "ready" if status_code == 200 else "not_ready",
        "service": "web-app",
        "check": "ready",
        "checks": checks,
    }, status_code


@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("access_token"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        payload = {
            "email": request.form.get("email", "").strip().lower(),
            "password": request.form.get("password", ""),
        }

        try:
            response, data = service_request("POST", f"{AUTH_SERVICE_URL}/login", json=payload)
        except requests.RequestException:
            flash("Auth service is not available right now.", "error")
            return redirect(url_for("login"))

        if not response.ok:
            flash(data.get("error", "Invalid email or password."), "error")
            return redirect(url_for("login"))

        session["access_token"] = data["access_token"]
        session["user"] = data["user"]
        flash("Logged in successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("signup"))

        payload = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip().lower(),
            "password": password,
        }

        try:
            response, data = service_request("POST", f"{AUTH_SERVICE_URL}/signup", json=payload)
        except requests.RequestException:
            flash("Auth service is not available right now.", "error")
            return redirect(url_for("signup"))

        if not response.ok:
            flash(data.get("error", "Could not create account."), "error")
            return redirect(url_for("signup"))

        flash("Account created successfully. Please login now.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    login_redirect = require_login()

    if login_redirect:
        return login_redirect

    edit_id = request.args.get("edit_id")
    transaction_to_edit = None

    if request.method == "POST":
        transaction_id = request.form.get("transaction_id")
        payload = {
            "title": request.form.get("title", "").strip(),
            "amount": request.form.get("amount", ""),
            "type": request.form.get("type", "").strip().lower(),
            "category": request.form.get("category", "").strip(),
        }

        try:
            if transaction_id:
                response, data = service_request(
                    "PUT",
                    f"{TRANSACTION_SERVICE_URL}/transactions/{transaction_id}",
                    headers=auth_headers(),
                    json=payload,
                )
                success_message = "Transaction updated successfully."
            else:
                response, data = service_request(
                    "POST",
                    f"{TRANSACTION_SERVICE_URL}/transactions",
                    headers=auth_headers(),
                    json=payload,
                )
                success_message = "Transaction added successfully."
        except requests.RequestException:
            flash("Transaction service is not available right now.", "error")
            return redirect(url_for("dashboard"))

        if not response.ok:
            flash(data.get("error", "Could not save transaction."), "error")
            return redirect(url_for("dashboard"))

        flash(success_message, "success")
        return redirect(url_for("dashboard"))

    selected_filter = request.args.get("filter", "all")
    search_text = request.args.get("search", "").strip()
    params = {"filter": selected_filter, "search": search_text}

    try:
        response, dashboard_data = service_request(
            "GET",
            f"{TRANSACTION_SERVICE_URL}/transactions",
            headers=auth_headers(),
            params=params,
        )
    except requests.RequestException:
        flash("Transaction service is not available right now.", "error")
        dashboard_data = build_empty_dashboard_state()
    else:
        if not response.ok:
            session.clear()
            flash("Please login again.", "error")
            return redirect(url_for("login"))

    if edit_id:
        try:
            response, data = service_request(
                "GET",
                f"{TRANSACTION_SERVICE_URL}/transactions/{edit_id}",
                headers=auth_headers(),
            )
            if response.ok:
                transaction_to_edit = data["transaction"]
        except requests.RequestException:
            flash("Could not load transaction for editing.", "error")

    budget_month = request.args.get("month", datetime.now(timezone.utc).strftime("%Y-%m"))
    budgets = []
    budget_error = None
    budget_to_edit = None
    try:
        response, budget_data = service_request(
            "GET", f"{BUDGET_SERVICE_URL}/budgets", headers=auth_headers(), params={"month": budget_month},
        )
        if response.ok:
            budgets = budget_data.get("budgets", [])
            edit_budget_id = request.args.get("edit_budget_id", type=int)
            budget_to_edit = next((item for item in budgets if item["id"] == edit_budget_id), None)
        else:
            budget_error = budget_data.get("error", "Budgets are unavailable. Please try again.")
    except requests.RequestException:
        budget_error = "Budgets are unavailable. Please try again."

    notifications = []
    unread_count = 0
    notification_error = None
    try:
        # Refresh the current month even when viewing a historical budget month.
        for month in sorted({budget_month, datetime.now(timezone.utc).strftime("%Y-%m")}):
            sync_response, sync_data = service_request(
                "POST", f"{NOTIFICATION_SERVICE_URL}/notifications/sync",
                headers=auth_headers(), json={"month": month},
            )
            if not sync_response.ok:
                notification_error = sync_data.get("error", "Alerts could not be refreshed.")
        response, data = service_request("GET", f"{NOTIFICATION_SERVICE_URL}/notifications", headers=auth_headers())
        if response.ok:
            notifications = data.get("notifications", [])
            unread_count = data.get("unread_count", 0)
        else:
            notification_error = "Notifications are unavailable. Please try again."
    except requests.RequestException:
        notification_error = "Notifications are unavailable. Please try again."

    summary = dashboard_data.get("summary", {})

    return render_template(
        "index.html",
        transactions=dashboard_data.get("transactions", []),
        total_income=summary.get("total_income", 0),
        total_expense=summary.get("total_expense", 0),
        balance=summary.get("balance", 0),
        charts=dashboard_data.get("charts", build_empty_dashboard_state()["charts"]),
        transaction_to_edit=transaction_to_edit,
        selected_filter=dashboard_data.get("filter", selected_filter),
        search_text=dashboard_data.get("search", search_text),
        current_user=current_user(),
        budgets=budgets, budget_month=budget_month, budget_error=budget_error, budget_to_edit=budget_to_edit,
        notifications=notifications, unread_count=unread_count, notification_error=notification_error,
    )


@app.route("/delete/<int:transaction_id>", methods=["POST"])
def delete_transaction(transaction_id):
    login_redirect = require_login()

    if login_redirect:
        return login_redirect

    try:
        response, data = service_request(
            "DELETE",
            f"{TRANSACTION_SERVICE_URL}/transactions/{transaction_id}",
            headers=auth_headers(),
        )
    except requests.RequestException:
        flash("Transaction service is not available right now.", "error")
        return redirect(url_for("dashboard"))

    if not response.ok:
        flash(data.get("error", "Could not delete transaction."), "error")
        return redirect(url_for("dashboard"))

    flash("Transaction deleted successfully.", "success")
    return redirect(url_for("dashboard"))


@app.post("/budgets/save")
def save_budget():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    budget_id = request.form.get("budget_id", type=int)
    payload = {key: request.form.get(key, "").strip() for key in ("month", "category", "amount")}
    try:
        response, data = service_request(
            "PUT" if budget_id else "POST",
            f"{BUDGET_SERVICE_URL}/budgets" + (f"/{budget_id}" if budget_id else ""),
            headers=auth_headers(), json=payload,
        )
        flash("Budget saved successfully." if response.ok else data.get("error", "Could not save budget."),
              "success" if response.ok else "error")
    except requests.RequestException:
        flash("Budget service is unavailable. Please try again.", "error")
    return redirect(url_for("dashboard", month=payload["month"]))


@app.post("/budgets/<int:budget_id>/delete")
def delete_budget(budget_id):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    try:
        response, data = service_request("DELETE", f"{BUDGET_SERVICE_URL}/budgets/{budget_id}",
                                         headers=auth_headers())
        flash("Budget deleted successfully." if response.ok else data.get("error", "Could not delete budget."),
              "success" if response.ok else "error")
    except requests.RequestException:
        flash("Budget service is unavailable. Please try again.", "error")
    return redirect(url_for("dashboard", month=request.form.get("month", "")))


@app.post("/notifications/<int:notification_id>/read")
@app.post("/notifications/read-all", defaults={"notification_id": None})
def read_notifications(notification_id):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    endpoint = f"{notification_id}/read" if notification_id is not None else "read-all"
    try:
        response, data = service_request("POST", f"{NOTIFICATION_SERVICE_URL}/notifications/{endpoint}",
                                         headers=auth_headers())
        if not response.ok:
            flash(data.get("error", "Could not mark notification as read."), "error")
    except requests.RequestException:
        flash("Notification service is unavailable. Please try again.", "error")
    return redirect(url_for("dashboard", month=request.form.get("month") or datetime.now(timezone.utc).strftime("%Y-%m")))


@app.get("/reports")
def reports():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    month = request.args.get("month", datetime.now(timezone.utc).strftime("%Y-%m"))
    report = None
    report_error = None
    status = 200
    try:
        response, data = service_request("GET", f"{REPORTING_SERVICE_URL}/reports/monthly",
                                         headers=auth_headers(), params={"month": month})
        if response.status_code == 401:
            session.clear()
            flash("Please login again.", "error")
            return redirect(url_for("login"))
        if response.ok:
            report = data
        else:
            report_error = data.get("error", "Reports are unavailable. Please try again.")
            status = response.status_code if response.status_code in (400, 503) else 503
    except requests.RequestException:
        report_error = "Reports are unavailable. Please try again."
        status = 503
    response = app.make_response((render_template("reports.html", report=report, report_month=month,
                                                  report_error=report_error, current_user=current_user()), status))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/reports/download")
def download_report():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    month = request.args.get("month", datetime.now(timezone.utc).strftime("%Y-%m"))
    try:
        response = requests.get(f"{REPORTING_SERVICE_URL}/reports/transactions.csv", headers=auth_headers(),
                                params={"month": month}, timeout=SERVICE_TIMEOUT_SECONDS)
        if response.status_code == 401:
            session.clear()
            flash("Please login again.", "error")
            return redirect(url_for("login"))
        if response.ok:
            return Response(response.content, content_type="text/csv; charset=utf-8", headers={
                "Content-Disposition": response.headers.get("Content-Disposition", "attachment; filename=spendwise.csv"),
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            })
        flash("Report download failed. Check the month and try again.", "error")
    except requests.RequestException:
        flash("Reporting service is unavailable. Please try again.", "error")
    return redirect(url_for("reports", month=month))


def feature_request(method, url, **kwargs):
    try:
        response, data = service_request(method, url, headers=auth_headers(), **kwargs)
        if not response.ok:
            flash(data.get("error", "Service is unavailable. Please try again."), "error")
            return None
        return data
    except requests.RequestException:
        flash("Service is unavailable. Please try again.", "error")
        return None


@app.get("/recurring")
def recurring():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    data = feature_request("GET", f"{RECURRING_SERVICE_URL}/schedules")
    return render_template("recurring.html", schedules=data.get("schedules", []) if data else [],
                           available=data is not None, today=datetime.now(timezone.utc).date().isoformat(),
                           current_user=current_user())


@app.post("/recurring/create")
def create_recurring():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    payload = {key: request.form.get(key, "") for key in ("title", "category", "amount", "type", "frequency", "start_date")}
    if feature_request("POST", f"{RECURRING_SERVICE_URL}/schedules", json=payload):
        flash("Schedule saved. Due transactions are processed automatically while the app is running.", "success")
    return redirect(url_for("recurring"))


@app.post("/recurring/run-due")
def run_recurring():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    data = feature_request("POST", f"{RECURRING_SERVICE_URL}/schedules/run-due")
    if data:
        flash(f"Processed {data['created']} due entries; {data['failed']} failed. " +
              ("Worker is busy. Try again shortly." if data.get("busy") else "Check your dashboard for transactions."), "success")
    return redirect(url_for("recurring"))


@app.post("/recurring/<schedule_id>/<action>")
def change_recurring(schedule_id, action):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    if action not in ("pause", "resume", "delete"):
        abort(404)
    data = feature_request("DELETE" if action == "delete" else "PATCH",
                           f"{RECURRING_SERVICE_URL}/schedules/{schedule_id}",
                           **({} if action == "delete" else {"json": {"active": action == "resume"}}))
    if data:
        flash("Schedule updated. Existing transactions are retained.", "success")
    return redirect(url_for("recurring"))


@app.get("/savings")
def savings():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    data = feature_request("GET", f"{SAVINGS_SERVICE_URL}/goals")
    goals = data.get("goals", []) if data else []
    edit_goal = next((goal for goal in goals if goal["id"] == request.args.get("edit")), None)
    return render_template("savings.html", goals=goals, edit_goal=edit_goal, available=data is not None,
                           contribution_key=uuid4().hex, current_user=current_user())


@app.post("/savings/save")
def save_goal():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    goal_id = request.form.get("goal_id")
    payload = {key: request.form.get(key, "") for key in ("name", "target", "target_date")}
    data = feature_request("PUT" if goal_id else "POST", f"{SAVINGS_SERVICE_URL}/goals" +
                           (f"/{goal_id}" if goal_id else ""), json=payload)
    if data:
        flash("Savings goal saved.", "success")
    return redirect(url_for("savings"))


@app.post("/savings/<goal_id>/contribute")
def contribute_goal(goal_id):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    data = feature_request("POST", f"{SAVINGS_SERVICE_URL}/goals/{goal_id}/contributions",
                           json={key: request.form.get(key, "") for key in ("amount", "request_key")})
    if data:
        flash("Contribution recorded. Your transaction balance is unchanged.", "success")
    return redirect(url_for("savings"))


@app.post("/savings/<goal_id>/delete")
@app.post("/savings/<goal_id>/contributions/<contribution_id>/delete")
def delete_saving(goal_id, contribution_id=None):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    url = f"{SAVINGS_SERVICE_URL}/goals/{goal_id}"
    if contribution_id:
        url += f"/contributions/{contribution_id}"
    if feature_request("DELETE", url):
        flash("Savings record removed. Your transaction balance is unchanged.", "success")
    return redirect(url_for("savings"))


@app.get("/imports")
def imports():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    preview = None
    preview_id = request.args.get("preview")
    if preview_id:
        data = feature_request("GET", f"{IMPORT_SERVICE_URL}/imports/{preview_id}")
        if data:
            preview = data["preview"]
    return render_template("imports.html", preview=preview, current_user=current_user())


@app.post("/imports/preview")
def preview_import():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    file = request.files.get("file")
    if file is None:
        flash("Choose a CSV file.", "error")
        return redirect(url_for("imports"))
    content = file.read(1000001)
    if len(content) > 1000000:
        flash("CSV must be at most 1 MB.", "error")
        return redirect(url_for("imports"))
    data = feature_request("POST", f"{IMPORT_SERVICE_URL}/imports/preview",
                           files={"file": ("transactions.csv", content, "text/csv")})
    return redirect(url_for("imports", preview=data["preview"]["id"]) if data else url_for("imports"))


@app.post("/imports/<preview_id>/confirm")
def confirm_import(preview_id):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    data = feature_request("POST", f"{IMPORT_SERVICE_URL}/imports/{preview_id}/confirm")
    if data:
        flash(f"Imported {data['imported']} transactions; skipped {data['skipped']} duplicate rows.", "success")
    return redirect(url_for("imports", preview=preview_id))


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEB_APP_PORT, debug=APP_ENV == "development")
