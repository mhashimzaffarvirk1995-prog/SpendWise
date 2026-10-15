"""Persistent schedules with an automatic local worker and retry-safe transactions."""
import calendar
import os
import threading
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import jwt
import requests
from flask import Flask, g, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.api import authenticated, configure, iso_date, json_body, label, money

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['RECURRING_DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
configure(app)
TRANSACTION_URL = os.getenv('TRANSACTION_SERVICE_URL', 'http://127.0.0.1:5002').rstrip('/')
TIMEOUT = float(os.getenv('SERVICE_TIMEOUT_SECONDS', '5'))
RUN_LOCK = threading.Lock()


class Schedule(db.Model):
    __tablename__ = 'schedules'
    id = db.Column(db.String(32), primary_key=True, default=lambda: uuid4().hex)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    title = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    frequency = db.Column(db.String(10), nullable=False)
    next_due = db.Column(db.Date, nullable=False)
    anchor_day = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    last_error = db.Column(db.String(200))


def initialize_database():
    with app.app_context():
        db.create_all()


def serialize(item):
    return dict(id=item.id, title=item.title, category=item.category, amount=float(item.amount), type=item.type,
                frequency=item.frequency, next_due=item.next_due.isoformat(), active=item.active, last_error=item.last_error)


def next_date(current, frequency, anchor):
    if frequency == 'daily':
        return current + timedelta(days=1)
    if frequency == 'weekly':
        return current + timedelta(days=7)
    year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
    return date(year, month, min(anchor, calendar.monthrange(year, month)[1]))


def run_due(user_id=None):
    if not RUN_LOCK.acquire(blocking=False):
        return dict(created=0, failed=0, busy=True)
    try:
        today = datetime.now(timezone.utc).date()
        query = Schedule.query.filter(Schedule.active.is_(True), Schedule.next_due <= today)
        if user_id is not None:
            query = query.filter_by(user_id=user_id)
        schedules = query.order_by(Schedule.next_due, Schedule.id).limit(100).all()
        created = failed = attempts = 0
        for item in schedules:
            while item.active and item.next_due <= today and attempts < 100:
                due = item.next_due
                attempts += 1
                token = jwt.encode({'sub': str(item.user_id), 'iss': 'spendwise-auth-service',
                                    'exp': datetime.now(timezone.utc) + timedelta(minutes=2)},
                                   os.environ['JWT_SECRET'], algorithm='HS256')
                try:
                    response = requests.post(f'{TRANSACTION_URL}/transactions', timeout=TIMEOUT,
                                             headers={'Authorization': 'Bearer ' + token,
                                                      'Idempotency-Key': f'recurring:{item.id}:{due.isoformat()}'},
                                             json=dict(title=item.title, category=item.category, amount=float(item.amount),
                                                       type=item.type, created_at=due.isoformat() + 'T00:00:00'))
                    response.raise_for_status()
                    item.next_due = next_date(due, item.frequency, item.anchor_day)
                    item.last_error = None
                    db.session.commit()
                    created += 1
                except (requests.RequestException, ValueError, OverflowError):
                    item.last_error = 'Transaction could not be posted. It will retry without creating a duplicate.'
                    db.session.commit()
                    failed += 1
                    break
        return dict(created=created, failed=failed, busy=False)
    finally:
        RUN_LOCK.release()


def start_background_worker(stop_event):
    def work():
        while not stop_event.is_set():
            try:
                with app.app_context():
                    run_due()
            except Exception:
                app.logger.exception('Recurring worker failed; retrying next cycle')
            if stop_event.wait(60):
                break
    thread = threading.Thread(target=work, name='recurring-worker', daemon=True)
    thread.start()
    return thread


@app.get('/health')
def health():
    return jsonify(status='ok', service='recurring-service')


@app.get('/ready')
def ready():
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ready', service='recurring-service')
    except Exception:
        db.session.rollback()
        return jsonify(status='not_ready'), 503


@app.get('/schedules')
@authenticated
def list_schedules():
    return jsonify(schedules=[serialize(item) for item in Schedule.query.filter_by(user_id=g.user_id)
                             .order_by(Schedule.next_due).all()])


@app.post('/schedules')
@authenticated
def create_schedule():
    data = json_body()
    title = label(data.get('title'), 'Title')
    category = label(data.get('category'), 'Category', 50)
    amount = money(data.get('amount'))
    if data.get('type') not in ('income', 'expense') or data.get('frequency') not in ('daily', 'weekly', 'monthly'):
        raise ValueError('Choose income/expense and daily/weekly/monthly frequency.')
    due = iso_date(data.get('start_date'))
    if due.year >= 9999:
        raise ValueError('Start date must be before year 9999.')
    item = Schedule(user_id=g.user_id, title=title, category=category, amount=amount, type=data['type'],
                    frequency=data['frequency'], next_due=due, anchor_day=due.day)
    db.session.add(item)
    db.session.commit()
    return jsonify(schedule=serialize(item)), 201


@app.post('/schedules/run-due')
@authenticated
def process_due():
    return jsonify(run_due(g.user_id))


@app.route('/schedules/<schedule_id>', methods=['PATCH', 'DELETE'])
@authenticated
def change_schedule(schedule_id):
    with RUN_LOCK:
        item = Schedule.query.filter_by(id=schedule_id, user_id=g.user_id).first()
        if item is None:
            return jsonify(error='Schedule not found.'), 404
        if request.method == 'DELETE':
            db.session.delete(item)
            db.session.commit()
            return jsonify(message='Schedule deleted. Existing transactions are retained.')
        active = json_body().get('active')
        if not isinstance(active, bool):
            raise ValueError('Active must be true or false.')
        item.active = active
        db.session.commit()
        return jsonify(schedule=serialize(item))
