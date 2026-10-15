import os
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from uuid import uuid4

import jwt
from flask import g, jsonify, request


def configure(app):
    @app.before_request
    def context():
        g.request_id = request.headers.get('X-Request-ID') or str(uuid4())

    @app.after_request
    def headers(response):
        response.headers['X-Request-ID'] = g.request_id
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(ValueError)
    def validation(error):
        return jsonify(error=str(error)), 400


def authenticated(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        try:
            if not header.startswith('Bearer '):
                raise ValueError()
            payload = jwt.decode(header[7:], os.environ['JWT_SECRET'], algorithms=['HS256'],
                                 issuer='spendwise-auth-service', options={'require': ['sub', 'exp']})
            g.user_id = int(payload['sub'])
            if g.user_id <= 0:
                raise ValueError()
        except (jwt.PyJWTError, ValueError, TypeError):
            return jsonify(error='Valid bearer token is required.'), 401
        return view(*args, **kwargs)
    return wrapped


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not Decimal('0') < amount < Decimal('1000000000000'):
            raise ValueError()
        if amount != amount.quantize(Decimal('0.01')):
            raise ValueError()
        return amount
    except (ValueError, InvalidOperation):
        raise ValueError('Amount must be positive, below 1 trillion, with at most two decimal places.') from None


def label(value, name, limit=100):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise ValueError(f'{name} must contain 1 to {limit} characters.')
    return value.strip()


def iso_date(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise ValueError('Date must be YYYY-MM-DD.') from None


def utc_date(value):
    if not isinstance(value, str):
        raise ValueError('Date must be YYYY-MM-DD or an ISO timestamp.')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        raise ValueError('Date must be YYYY-MM-DD or an ISO timestamp.') from None


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('A JSON object is required.')
    return data
