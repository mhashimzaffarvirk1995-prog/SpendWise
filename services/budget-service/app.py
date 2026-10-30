"""Monthly category budgets; spending is read through the transaction API."""
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from uuid import uuid4

import jwt
import redis
import requests
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

load_dotenv()
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['BUDGET_DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
JWT_SECRET = os.environ['JWT_SECRET']
TRANSACTION_SERVICE_URL = os.getenv('TRANSACTION_SERVICE_URL', 'http://127.0.0.1:5002').rstrip('/')
TIMEOUT = float(os.getenv('SERVICE_TIMEOUT_SECONDS', '5'))


class Budget(db.Model):
    __tablename__ = 'budgets'
    __table_args__ = (db.UniqueConstraint('user_id', 'month', 'category'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)


def initialize_database():
    with app.app_context():
        db.create_all()


def valid_month(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}', value):
        return False
    try:
        datetime.strptime(value, '%Y-%m')
        return True
    except ValueError:
        return False


def authenticated(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        try:
            if not header.startswith('Bearer '):
                raise ValueError()
            payload = jwt.decode(header[7:], JWT_SECRET, algorithms=['HS256'],
                                 issuer='spendwise-auth-service', options={'require': ['sub', 'exp']})
            g.user_id = int(payload['sub'])
            if g.user_id <= 0:
                raise ValueError()
        except (jwt.PyJWTError, ValueError, TypeError):
            return jsonify(error='Valid bearer token is required.'), 401
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def request_context():
    g.request_id = request.headers.get('X-Request-ID') or str(uuid4())


REDIS_URL = os.getenv("REDIS_URL")
report_cache = redis.Redis.from_url(REDIS_URL, socket_timeout=0.2, socket_connect_timeout=0.2) if REDIS_URL else None


@app.after_request
def invalidate_reports(response):
    """A successful write changes this user's numbers: bump their report generation."""
    if report_cache is not None and request.method in ("POST", "PUT", "DELETE") and response.status_code < 400:
        user_id = g.get('user_id')
        if user_id:
            try:
                report_cache.incr(f"report-gen:{user_id}")
            except redis.RedisError as error:
                app.logger.warning("Report cache invalidation failed: %s", type(error).__name__)
    return response


@app.after_request
def response_context(response):
    response.headers['X-Request-ID'] = g.request_id
    return response


def serialize(budget):
    return {'id': budget.id, 'month': budget.month, 'category': budget.category,
            'amount': float(budget.amount)}


@app.get('/health')
def health():
    return jsonify(status='ok', service='budget-service')


@app.get('/ready')
def ready():
    try:
        db.session.execute(text('SELECT 1'))
        response = requests.get(f'{TRANSACTION_SERVICE_URL}/ready', timeout=TIMEOUT)
        response.raise_for_status()
        return jsonify(status='ready', service='budget-service')
    except Exception as error:
        db.session.rollback()
        app.logger.warning('Budget readiness failed: %s', error)
        return jsonify(status='not_ready', service='budget-service'), 503


@app.get('/budgets')
@authenticated
def list_budgets():
    month = request.args.get('month', datetime.now(timezone.utc).strftime('%Y-%m'))
    if not valid_month(month):
        return jsonify(error='Month must be YYYY-MM.'), 400
    budgets = Budget.query.filter_by(user_id=g.user_id, month=month).order_by(Budget.category).all()
    try:
        response = requests.get(f'{TRANSACTION_SERVICE_URL}/transactions',
                                headers={'Authorization': request.headers['Authorization'],
                                         'X-Request-ID': g.request_id},
                                params={'filter': 'expense'}, timeout=TIMEOUT)
        if response.status_code == 401:
            return jsonify(error='Please login again.'), 401
        response.raise_for_status()
        transactions = response.json()['transactions']
        totals = {}
        for item in transactions:
            if item['type'] == 'expense' and item['created_at'][:7] == month:
                category = item['category'].strip().casefold()
                totals[category] = totals.get(category, Decimal(0)) + Decimal(str(item['amount']))
        result = []
        for budget in budgets:
            spent = totals.get(budget.category, Decimal(0)).quantize(Decimal('0.01'))
            percent = float(spent / budget.amount * 100)
            result.append(dict(serialize(budget), spent=float(spent),
                               remaining=float(budget.amount - spent), percent=round(percent, 1),
                               progress=min(100, max(0, percent)),
                               status='over' if spent > budget.amount else 'near' if percent >= 80 else 'ok'))
        return jsonify(month=month, budgets=result)
    except (requests.RequestException, ValueError, KeyError, TypeError, InvalidOperation):
        return jsonify(error='Spending data is unavailable. Please try again.'), 503


@app.route('/budgets', methods=['POST'])
@app.route('/budgets/<int:budget_id>', methods=['PUT'])
@authenticated
def save_budget(budget_id=None):
    budget = None
    if budget_id is not None:
        budget = Budget.query.filter_by(id=budget_id, user_id=g.user_id).first()
        if budget is None:
            return jsonify(error='Budget not found.'), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='A JSON object is required.'), 400
    category = data.get('category')
    month = data.get('month')
    if not isinstance(category, str) or not 1 <= len(category.strip()) <= 50:
        return jsonify(error='Category must contain 1 to 50 characters.'), 400
    if not valid_month(month):
        return jsonify(error='Month must be YYYY-MM.'), 400
    try:
        amount = Decimal(str(data.get('amount')))
        if not amount.is_finite() or amount <= 0 or amount >= Decimal('1000000000000'):
            raise ValueError()
        if amount != amount.quantize(Decimal('0.01')):
            raise ValueError()
    except (InvalidOperation, ValueError):
        return jsonify(error='Amount must be positive with at most two decimal places.'), 400
    if budget is None:
        budget = Budget(user_id=g.user_id)
        db.session.add(budget)
    budget.category = category.strip().casefold()
    budget.month = month
    budget.amount = amount
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error='A budget already exists for this category and month. Edit it instead.'), 409
    return jsonify(budget=serialize(budget)), 200 if budget_id is not None else 201


@app.delete('/budgets/<int:budget_id>')
@authenticated
def delete_budget(budget_id):
    budget = Budget.query.filter_by(id=budget_id, user_id=g.user_id).first()
    if budget is None:
        return jsonify(error='Budget not found.'), 404
    db.session.delete(budget)
    db.session.commit()
    return jsonify(message='Budget deleted.')


if __name__ == '__main__':
    initialize_database()
    app.run(host='127.0.0.1', port=int(os.getenv('BUDGET_SERVICE_PORT', '5003')))
