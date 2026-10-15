"""Persistent in-app budget alerts, synchronized from the budget API."""
import os
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

import jwt
import requests
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

load_dotenv()
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['NOTIFICATION_DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
JWT_SECRET = os.environ['JWT_SECRET']
BUDGET_SERVICE_URL = os.getenv('BUDGET_SERVICE_URL', 'http://127.0.0.1:5003').rstrip('/')
TIMEOUT = float(os.getenv('SERVICE_TIMEOUT_SECONDS', '5'))


class Notification(db.Model):
    __tablename__ = 'notifications'
    __table_args__ = (db.UniqueConstraint('user_id', 'month', 'category', 'level'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    level = db.Column(db.String(10), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


def initialize_database():
    with app.app_context():
        db.create_all()


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
    return response



def serialize(item):
    return dict(id=item.id, month=item.month, category=item.category, level=item.level,
                message=item.message, is_read=item.is_read, created_at=item.created_at.isoformat() + 'Z')


@app.get('/health')
def health():
    return jsonify(status='ok', service='notification-service')


@app.get('/ready')
def ready():
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ready', service='notification-service')
    except Exception:
        db.session.rollback()
        return jsonify(status='not_ready', service='notification-service'), 503


@app.post('/notifications/sync')
@authenticated
def sync_notifications():
    # Fetch trusted spending through the budget API, never accept client-written alerts.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='A JSON object is required.'), 400
    month = data.get('month', datetime.now(timezone.utc).strftime('%Y-%m'))
    if not isinstance(month, str) or len(month) != 7:
        return jsonify(error='Month must be YYYY-MM.'), 400
    try:
        response = requests.get(f'{BUDGET_SERVICE_URL}/budgets', params={'month': month},
                                headers={'Authorization': request.headers['Authorization'],
                                         'X-Request-ID': g.request_id}, timeout=TIMEOUT)
        if response.status_code in (400, 401):
            return jsonify(error='Invalid month or expired login.'), response.status_code
        response.raise_for_status()
        budgets = response.json()['budgets']
        alerts = []
        for budget in budgets:
            level = budget['status']
            if level not in ('near', 'over'):
                continue
            category = budget['category']
            if level == 'over':
                message = f"{category.title()} budget exceeded by {abs(budget['remaining']):.2f} for {month}."
            else:
                message = (f"{category.title()} budget is {budget['percent']:.1f}% used for {month}. "
                           f"{budget['remaining']:.2f} remaining.")
            alerts.append((category, level, message))
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return jsonify(error='Budget alerts could not be refreshed. Please try again.'), 503
    created = 0
    for category, level, message in alerts:
        if Notification.query.filter_by(user_id=g.user_id, month=month, category=category, level=level).first():
            continue
        db.session.add(Notification(user_id=g.user_id, month=month, category=category, level=level, message=message))
        try:
            db.session.commit()
            created += 1
        except IntegrityError:
            # A concurrent refresh already saved this same alert.
            db.session.rollback()
    return jsonify(created=created)


@app.get('/notifications')
@authenticated
def list_notifications():
    query = Notification.query.filter_by(user_id=g.user_id)
    unread_count = query.filter_by(is_read=False).count()
    items = query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(50).all()
    return jsonify(notifications=[serialize(item) for item in items], unread_count=unread_count)


@app.post('/notifications/<int:notification_id>/read')
@authenticated
def mark_read(notification_id):
    item = Notification.query.filter_by(id=notification_id, user_id=g.user_id).first()
    if item is None:
        return jsonify(error='Notification not found.'), 404
    item.is_read = True
    db.session.commit()
    return jsonify(notification=serialize(item))


@app.post('/notifications/read-all')
@authenticated
def mark_all_read():
    Notification.query.filter_by(user_id=g.user_id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify(message='All notifications marked as read.')


if __name__ == '__main__':
    initialize_database()
    app.run(host='127.0.0.1', port=int(os.getenv('NOTIFICATION_SERVICE_PORT', '5004')))
