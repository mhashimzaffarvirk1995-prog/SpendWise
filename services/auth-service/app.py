import json
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def parse_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise RuntimeError("AUTH_SERVICE_PORT must be a valid integer.")

    if port < 1 or port > 65535:
        raise RuntimeError("AUTH_SERVICE_PORT must be between 1 and 65535.")

    return port


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


APP_ENV = os.getenv("APP_ENV", "development").lower()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
AUTH_DATABASE_URL = os.getenv("AUTH_DATABASE_URL")
JWT_SECRET = os.getenv("JWT_SECRET")
AUTH_SERVICE_PORT = parse_port(os.getenv("AUTH_SERVICE_PORT", "5001"))
JWT_EXPIRES_MINUTES = int(os.getenv("JWT_EXPIRES_MINUTES", "120"))

if LOG_LEVEL not in VALID_LOG_LEVELS:
    raise RuntimeError("LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL.")

if not AUTH_DATABASE_URL:
    raise RuntimeError("AUTH_DATABASE_URL is required.")

if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET is required.")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "timestamp": utc_timestamp(),
            "service": "auth-service",
            "request_id": getattr(record, "request_id", None),
        }

        for key in ["method", "path", "status_code", "user_id", "duration_ms"]:
            value = getattr(record, key, None)
            if value is not None:
                log_data[key] = value

        return json.dumps(log_data)


logger = logging.getLogger("auth-service")
logger.setLevel(getattr(logging, LOG_LEVEL))
logger.handlers.clear()

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.propagate = False

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = AUTH_DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def create_access_token(user):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "name": user.name,
        "email": user.email,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRES_MINUTES),
        "iss": "spendwise-auth-service",
    }

    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def get_bearer_token():
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None

    return auth_header.removeprefix("Bearer ").strip()


def decode_token(token):
    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=["HS256"],
        issuer="spendwise-auth-service",
    )


def serialize_user(user):
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def initialize_database():
    with app.app_context():
        db.create_all()


@app.before_request
def start_request_context():
    g.request_id = request.headers.get("X-Request-ID", str(uuid4()))
    g.request_started_at = datetime.now(timezone.utc)


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
    return jsonify({"status": "ok", "service": "auth-service", "check": "health"}), 200


@app.route("/ready")
def ready():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({
            "status": "ready",
            "service": "auth-service",
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
            "service": "auth-service",
            "check": "ready",
            "checks": {"database": "error"},
        }), 503


@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or len(password) < 6:
        return jsonify({"error": "Name, valid email, and 6 character password are required."}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already exists."}), 409

    user = User(
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
    )
    db.session.add(user)
    db.session.commit()

    logger.info(
        "User signup successful",
        extra={"request_id": g.get("request_id"), "user_id": user.id},
    )

    return jsonify({"user": serialize_user(user)}), 201


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    user = User.query.filter_by(email=email).first()

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Invalid email or password."}), 401

    token = create_access_token(user)

    logger.info(
        "User login successful",
        extra={"request_id": g.get("request_id"), "user_id": user.id},
    )

    return jsonify({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": JWT_EXPIRES_MINUTES * 60,
        "user": serialize_user(user),
    }), 200


@app.route("/me")
def me():
    token = get_bearer_token()

    if not token:
        return jsonify({"error": "Bearer token is required."}), 401

    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        return jsonify({"error": "Invalid or expired token."}), 401

    user = db.session.get(User, int(payload["sub"]))

    if not user:
        return jsonify({"error": "User not found."}), 404

    return jsonify({"user": serialize_user(user)}), 200


if __name__ == "__main__":
    if APP_ENV != "production":
        initialize_database()

    app.run(host="0.0.0.0", port=AUTH_SERVICE_PORT, debug=APP_ENV == "development")
