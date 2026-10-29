"""Read-only monthly reports generated from authenticated service APIs."""
import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from uuid import uuid4

import jwt
import redis
import requests
from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, request

load_dotenv()
app = Flask(__name__)
JWT_SECRET = os.environ['JWT_SECRET']
TRANSACTION_SERVICE_URL = os.getenv('TRANSACTION_SERVICE_URL', 'http://127.0.0.1:5002').rstrip('/')
BUDGET_SERVICE_URL = os.getenv('BUDGET_SERVICE_URL', 'http://127.0.0.1:5003').rstrip('/')
TIMEOUT = float(os.getenv('SERVICE_TIMEOUT_SECONDS', '5'))
CENT = Decimal('0.01')
REDIS_URL = os.getenv('REDIS_URL')  # e.g. redis://redis:6379/0 ; unset = cache off
REPORT_CACHE_TTL = int(os.getenv('REPORT_CACHE_TTL_SECONDS', '300'))
cache = redis.Redis.from_url(REDIS_URL, socket_timeout=0.2, socket_connect_timeout=0.2) if REDIS_URL else None


def cache_get(key):
    if cache is None:
        return None
    try:
        raw = cache.get(key)
        return json.loads(raw) if raw else None
    except (redis.RedisError, ValueError) as error:
        app.logger.warning('Report cache read failed: %s', type(error).__name__)
        return None


def cache_set(key, value):
    if cache is None:
        return
    try:
        cache.set(key, json.dumps(value), ex=REPORT_CACHE_TTL)
    except redis.RedisError as error:
        app.logger.warning('Report cache write failed: %s', type(error).__name__)


def report_generation(user_id):
    if cache is None:
        return 0
    try:
        return int(cache.get(f'report-gen:{user_id}') or 0)
    except (redis.RedisError, ValueError):
        return 0


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


@app.after_request
def response_context(response):
    response.headers['X-Request-ID'] = g.request_id
    response.headers['Cache-Control'] = 'no-store'
    if g.get('cache'):
        response.headers['X-Cache'] = g.cache
    return response




def selected_month():
    month = request.args.get('month', datetime.now(timezone.utc).strftime('%Y-%m'))
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}', month):
        raise ValueError('Month must be YYYY-MM.')
    try:
        datetime.strptime(month, '%Y-%m')
    except ValueError:
        raise ValueError('Month must be YYYY-MM.') from None
    return month


def previous_month(month):
    year, number = map(int, month.split('-'))
    if number == 1:
        return f'{year - 1:04d}-12' if year > 1 else None
    return f'{year:04d}-{number - 1:02d}'


def backend_headers():
    return {'Authorization': request.headers['Authorization'], 'X-Request-ID': g.request_id}


def load_transactions():
    response = requests.get(f'{TRANSACTION_SERVICE_URL}/transactions', headers=backend_headers(), timeout=TIMEOUT)
    response.raise_for_status()
    rows = response.json()['transactions']
    if not isinstance(rows, list):
        raise ValueError('Invalid transaction data.')
    result = []
    for row in rows:
        date = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        date = date.astimezone(timezone.utc)
        amount = Decimal(str(row['amount']))
        if not amount.is_finite() or amount <= 0 or row['type'] not in ('income', 'expense'):
            raise ValueError('Invalid transaction data.')
        if not isinstance(row['title'], str) or not isinstance(row['category'], str):
            raise ValueError('Invalid transaction data.')
        result.append(dict(row, amount=amount.quantize(CENT, rounding=ROUND_HALF_UP),
                           month=f'{date.year:04d}-{date.month:02d}',
                           created_at=date.isoformat().replace('+00:00', 'Z')))
    return result


def summarize(rows):
    income = sum((row['amount'] for row in rows if row['type'] == 'income'), Decimal(0))
    expense = sum((row['amount'] for row in rows if row['type'] == 'expense'), Decimal(0))
    return dict(income=float(income), expenses=float(expense), balance=float(income - expense),
                transaction_count=len(rows))


def dependency_error(error):
    response = getattr(error, 'response', None)
    if response is not None and response.status_code == 401:
        return jsonify(error='Please login again.'), 401
    app.logger.warning('Report data unavailable: %s', type(error).__name__)
    return jsonify(error='Report data is unavailable. Please try again.'), 503


@app.get('/health')
def health():
    return jsonify(status='ok', service='reporting-service')


@app.get('/ready')
def ready():
    try:
        response = requests.get(f'{TRANSACTION_SERVICE_URL}/ready', timeout=TIMEOUT)
        response.raise_for_status()
        return jsonify(status='ready', service='reporting-service')
    except requests.RequestException:
        return jsonify(status='not_ready', service='reporting-service'), 503


@app.get('/reports/monthly')
@authenticated
def monthly_report():
    try:
        month = selected_month()
    except ValueError as error:
        return jsonify(error=str(error)), 400
    cache_key = f'report:{g.user_id}:{month}:g{report_generation(g.user_id)}'
    cached = cache_get(cache_key)
    if cached is not None:
        g.cache = 'HIT'
        return jsonify(cached)
    g.cache = 'MISS' if cache is not None else None
    try:
        transactions = load_transactions()
        rows = [row for row in transactions if row['month'] == month]
        previous = previous_month(month)
        previous_rows = [row for row in transactions if row['month'] == previous]
        summary = summarize(rows)
        earlier = summarize(previous_rows)
        categories = {}
        for row in rows:
            if row['type'] == 'expense':
                category = row['category'].strip().casefold() or 'uncategorized'
                categories[category] = categories.get(category, Decimal(0)) + row['amount']
        breakdown = [dict(category=category, amount=float(amount))
                     for category, amount in sorted(categories.items(), key=lambda item: (-item[1], item[0]))]
        delta = Decimal(str(summary['expenses'])) - Decimal(str(earlier['expenses']))
        change = round(float(delta / Decimal(str(earlier['expenses'])) * 100), 1) if earlier['expenses'] else None
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError, InvalidOperation) as error:
        return dependency_error(error)
    budgets = []
    budget_error = None
    try:
        response = requests.get(f'{BUDGET_SERVICE_URL}/budgets', headers=backend_headers(),
                                params={'month': month}, timeout=TIMEOUT)
        response.raise_for_status()
        budgets = response.json()['budgets']
        if not isinstance(budgets, list):
            raise ValueError()
    except (requests.RequestException, ValueError, KeyError, TypeError):
        budget_error = 'Budget comparison is unavailable. Transaction totals are still shown.'
    report = dict(month=month, summary=summary, previous_month=previous, previous_summary=earlier,
                  expense_change=float(delta), expense_change_percent=change,
                  categories=breakdown, highest_spending_category=breakdown[0]['category'] if breakdown else None,
                  budgets=budgets, budget_error=budget_error)
    if budget_error is None:  # never cache a partial report
        cache_set(cache_key, report)
    return jsonify(report)


def csv_text(value):
    # Spreadsheet tools can interpret user-entered text as formulas, even in quoted CSV cells.
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


@app.get('/reports/transactions.csv')
@authenticated
def export_csv():
    try:
        month = selected_month()
    except ValueError as error:
        return jsonify(error=str(error)), 400
    try:
        rows = sorted((row for row in load_transactions() if row['month'] == month),
                      key=lambda row: (row['created_at'], row['id']))
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['Date (UTC)', 'Title', 'Type', 'Category', 'Amount'])
        for row in rows:
            writer.writerow([row['created_at'], csv_text(row['title']), row['type'],
                             csv_text(row['category']), format(row['amount'], '.2f')])
        return Response('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="spendwise-{month}.csv"',
                                 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError, InvalidOperation) as error:
        return dependency_error(error)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.getenv('REPORTING_SERVICE_PORT', '5005')))
