import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import redis
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.api import money, utc_date

load_dotenv()

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
VALID_TRANSACTION_TYPES = {"income", "expense"}


def parse_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise RuntimeError("TRANSACTION_SERVICE_PORT must be a valid integer.")

    if port < 1 or port > 65535:
        raise RuntimeError("TRANSACTION_SERVICE_PORT must be between 1 and 65535.")

    return port


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


APP_ENV = os.getenv("APP_ENV", "development").lower()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
TRANSACTION_DATABASE_URL = os.getenv("TRANSACTION_DATABASE_URL")
JWT_SECRET = os.getenv("JWT_SECRET")
TRANSACTION_SERVICE_PORT = parse_port(os.getenv("TRANSACTION_SERVICE_PORT", "5002"))

if LOG_LEVEL not in VALID_LOG_LEVELS:
    raise RuntimeError("LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL.")

if not TRANSACTION_DATABASE_URL:
    raise RuntimeError("TRANSACTION_DATABASE_URL is required.")

if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET is required.")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "timestamp": utc_timestamp(),
            "service": "transaction-service",
            "request_id": getattr(record, "request_id", None),
        }

        for key in ["method", "path", "status_code", "user_id", "duration_ms"]:
            value = getattr(record, key, None)
            if value is not None:
                log_data[key] = value

        return json.dumps(log_data)


logger = logging.getLogger("transaction-service")
logger.setLevel(getattr(logging, LOG_LEVEL))
logger.handlers.clear()

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.propagate = False

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = TRANSACTION_DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    title = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TransactionReceipt(db.Model):
    __tablename__ = "transaction_receipts"
    user_id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(200), primary_key=True)
    payload_hash = db.Column(db.String(64), nullable=False)
    response_json = db.Column(db.Text, nullable=False)


def initialize_database():
    with app.app_context():
        db.create_all()


def get_bearer_token():
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None

    return auth_header.removeprefix("Bearer ").strip()


def get_current_user_id():
    token = get_bearer_token()

    if not token:
        return None

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], issuer="spendwise-auth-service",
                             options={"require": ["sub", "exp"]})
        user_id = int(payload["sub"])
        return user_id if user_id > 0 else None
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


def require_user_id():
    user_id = get_current_user_id()

    if not user_id:
        return None, (jsonify({"error": "Valid bearer token is required."}), 401)

    return user_id, None


def serialize_transaction(transaction):
    return {
        "id": transaction.id,
        "user_id": transaction.user_id,
        "title": transaction.title,
        "amount": transaction.amount,
        "type": transaction.type,
        "category": transaction.category,
        "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
    }


def validate_transaction_payload(data):
    if not isinstance(data, dict):
        return None, "A JSON object is required."
    title, category, kind = data.get("title"), data.get("category"), data.get("type")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        return None, "Title must contain 1 to 100 characters."
    if not isinstance(category, str) or not 1 <= len(category.strip()) <= 50:
        return None, "Category must contain 1 to 50 characters."
    if kind not in VALID_TRANSACTION_TYPES:
        return None, "Type must be income or expense."
    try:
        amount = money(data.get("amount"))
    except ValueError as error:
        return None, str(error)
    return {"title": title.strip(), "category": category.strip(), "amount": float(amount), "type": kind}, None


def build_summary(transactions):
    total_income = sum(item.amount for item in transactions if item.type == "income")
    total_expense = sum(item.amount for item in transactions if item.type == "expense")

    return {
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "balance": round(total_income - total_expense, 2),
    }


def chart_percent(amount, total):
    if total <= 0:
        return 0

    return round((amount / total) * 100, 2)


def build_charts(transactions):
    summary = build_summary(transactions)
    max_income_expense = max(summary["total_income"], summary["total_expense"])

    income_expense = [
        {
            "label": "Income",
            "amount": summary["total_income"],
            "percent": chart_percent(summary["total_income"], max_income_expense),
        },
        {
            "label": "Expense",
            "amount": summary["total_expense"],
            "percent": chart_percent(summary["total_expense"], max_income_expense),
        },
    ]

    expense_totals_by_category = {}

    for transaction in transactions:
        if transaction.type != "expense":
            continue

        category = transaction.category.strip().title() or "Uncategorized"
        expense_totals_by_category[category] = expense_totals_by_category.get(category, 0) + transaction.amount

    sorted_categories = sorted(
        expense_totals_by_category.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    top_categories = sorted_categories[:5]
    other_total = sum(amount for _, amount in sorted_categories[5:])

    if other_total > 0:
        top_categories.append(("Other", other_total))

    total_category_expense = sum(amount for _, amount in top_categories)
    expense_by_category = [
        {
            "label": category,
            "amount": round(amount, 2),
            "percent": chart_percent(amount, total_category_expense),
        }
        for category, amount in top_categories
    ]

    today = datetime.now(timezone.utc).date()
    trend_dates = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    expense_totals_by_date = {day: 0 for day in trend_dates}

    for transaction in transactions:
        if transaction.type != "expense" or not transaction.created_at:
            continue

        transaction_date = transaction.created_at.date()

        if transaction_date in expense_totals_by_date:
            expense_totals_by_date[transaction_date] += transaction.amount

    max_daily_expense = max(expense_totals_by_date.values()) if expense_totals_by_date else 0
    recent_expense_trend = [
        {
            "label": day.strftime("%a"),
            "date": day.isoformat(),
            "amount": round(expense_totals_by_date[day], 2),
            "percent": chart_percent(expense_totals_by_date[day], max_daily_expense),
        }
        for day in trend_dates
    ]

    return {
        "income_expense": income_expense,
        "expense_by_category": expense_by_category,
        "recent_expense_trend": recent_expense_trend,
        "has_income_expense": summary["total_income"] > 0 or summary["total_expense"] > 0,
        "has_expense_by_category": bool(expense_by_category),
        "has_recent_expense_trend": any(item["amount"] > 0 for item in recent_expense_trend),
    }


@app.before_request
def start_request_context():
    g.request_id = request.headers.get("X-Request-ID", str(uuid4()))
    g.request_started_at = datetime.now(timezone.utc)


REDIS_URL = os.getenv("REDIS_URL")
report_cache = redis.Redis.from_url(REDIS_URL, socket_timeout=0.2, socket_connect_timeout=0.2) if REDIS_URL else None


@app.after_request
def invalidate_reports(response):
    """A successful write changes this user's numbers: bump their report generation."""
    if report_cache is not None and request.method in ("POST", "PUT", "DELETE") and response.status_code < 400:
        user_id = get_current_user_id()
        if user_id:
            try:
                report_cache.incr(f"report-gen:{user_id}")
            except redis.RedisError as error:
                app.logger.warning("Report cache invalidation failed: %s", type(error).__name__)
    return response


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
    return jsonify({"status": "ok", "service": "transaction-service", "check": "health"}), 200


@app.route("/ready")
def ready():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({
            "status": "ready",
            "service": "transaction-service",
            "check": "ready",
            "checks": {"database": "ok"},
        }), 200
    except Exception as error:
        db.session.rollback()
        logger.error(
            f"Readiness database check failed: {error}",
            extra={"request_id": g.get("request_id")},
        )
        return jsonify({
            "status": "not_ready",
            "service": "transaction-service",
            "check": "ready",
            "checks": {"database": "error"},
        }), 503


@app.route("/transactions", methods=["GET"])
def list_transactions():
    user_id, error_response = require_user_id()

    if error_response:
        return error_response

    selected_filter = request.args.get("filter", "all")
    search_text = request.args.get("search", "").strip()

    query = Transaction.query.filter_by(user_id=user_id)

    if selected_filter in VALID_TRANSACTION_TYPES:
        query = query.filter_by(type=selected_filter)
    else:
        selected_filter = "all"

    if search_text:
        search_pattern = f"%{search_text}%"
        query = query.filter(
            or_(
                Transaction.title.ilike(search_pattern),
                Transaction.category.ilike(search_pattern),
            )
        )

    overview_transactions = Transaction.query.filter_by(user_id=user_id).all()
    transactions = query.order_by(Transaction.created_at.desc()).all()

    return jsonify({
        "transactions": [serialize_transaction(item) for item in transactions],
        "summary": build_summary(overview_transactions),
        "charts": build_charts(overview_transactions),
        "filter": selected_filter,
        "search": search_text,
    }), 200


@app.route("/transactions/<int:transaction_id>", methods=["GET"])
def get_transaction(transaction_id):
    user_id, error_response = require_user_id()

    if error_response:
        return error_response

    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()

    if not transaction:
        return jsonify({"error": "Transaction not found."}), 404

    return jsonify({"transaction": serialize_transaction(transaction)}), 200


@app.route("/transactions", methods=["POST"])
def create_transaction():
    user_id, error_response = require_user_id()

    if error_response:
        return error_response

    payload, error = validate_transaction_payload(request.get_json(silent=True) or {})

    if error:
        return jsonify({"error": error}), 400

    data = request.get_json(silent=True) or {}
    if "created_at" in data:
        try:
            created_at = utc_date(data["created_at"])
            payload["created_at"] = created_at.isoformat()
        except ValueError as error:
            return jsonify(error=str(error)), 400
    key = request.headers.get("Idempotency-Key")
    if key is not None and not 1 <= len(key) <= 200:
        return jsonify(error="Idempotency-Key must contain 1 to 200 characters."), 400
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    def replay(receipt):
        if receipt.payload_hash != fingerprint:
            return jsonify(error="This idempotency key was already used for different data."), 409
        return jsonify(transaction=json.loads(receipt.response_json), replayed=True), 200
    if key:
        existing = db.session.get(TransactionReceipt, (user_id, key))
        if existing:
            return replay(existing)
    if "created_at" in payload:
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
    transaction = Transaction(user_id=user_id, **payload)
    db.session.add(transaction)
    try:
        db.session.flush()
        result = serialize_transaction(transaction)
        if key:
            db.session.add(TransactionReceipt(user_id=user_id, key=key, payload_hash=fingerprint,
                                              response_json=json.dumps(result)))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = db.session.get(TransactionReceipt, (user_id, key)) if key else None
        if existing:
            return replay(existing)
        raise
    return jsonify(transaction=result), 201


@app.route("/transactions/<int:transaction_id>", methods=["PUT"])
def update_transaction(transaction_id):
    user_id, error_response = require_user_id()

    if error_response:
        return error_response

    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()

    if not transaction:
        return jsonify({"error": "Transaction not found."}), 404

    payload, error = validate_transaction_payload(request.get_json(silent=True) or {})

    if error:
        return jsonify({"error": error}), 400

    transaction.title = payload["title"]
    transaction.amount = payload["amount"]
    transaction.type = payload["type"]
    transaction.category = payload["category"]
    db.session.commit()

    logger.info(
        "Transaction updated",
        extra={"request_id": g.get("request_id"), "user_id": user_id},
    )

    return jsonify({"transaction": serialize_transaction(transaction)}), 200


@app.route("/transactions/<int:transaction_id>", methods=["DELETE"])
def delete_transaction(transaction_id):
    user_id, error_response = require_user_id()

    if error_response:
        return error_response

    transaction = Transaction.query.filter_by(id=transaction_id, user_id=user_id).first()

    if not transaction:
        return jsonify({"error": "Transaction not found."}), 404

    db.session.delete(transaction)
    db.session.commit()

    logger.info(
        "Transaction deleted",
        extra={"request_id": g.get("request_id"), "user_id": user_id},
    )

    return jsonify({"message": "Transaction deleted."}), 200


if __name__ == "__main__":
    if APP_ENV != "production":
        initialize_database()

    app.run(host="0.0.0.0", port=TRANSACTION_SERVICE_PORT, debug=APP_ENV == "development")
