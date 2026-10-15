"""Explicit savings goals and contributions, independent of account balance."""
import os
from datetime import datetime, timezone
from uuid import uuid4

from flask import Flask, g, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.api import authenticated, configure, iso_date, json_body, label, money

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['SAVINGS_DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
configure(app)


class Goal(db.Model):
    __tablename__ = 'goals'
    id = db.Column(db.String(32), primary_key=True, default=lambda: uuid4().hex)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    target = db.Column(db.Numeric(14, 2), nullable=False)
    target_date = db.Column(db.Date)


class Contribution(db.Model):
    __tablename__ = 'contributions'
    __table_args__ = (db.UniqueConstraint('goal_id', 'request_key'),)
    id = db.Column(db.String(32), primary_key=True, default=lambda: uuid4().hex)
    goal_id = db.Column(db.String(32), nullable=False, index=True)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    request_key = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


def initialize_database():
    with app.app_context():
        db.create_all()


def serialize(goal):
    contributions = Contribution.query.filter_by(goal_id=goal.id).order_by(Contribution.created_at.desc()).all()
    saved = sum((item.amount for item in contributions), start=0)
    return dict(id=goal.id, name=goal.name, target=float(goal.target), saved=float(saved),
                remaining=float(max(0, goal.target - saved)), percent=min(100, round(float(saved / goal.target * 100), 1)),
                completed=saved >= goal.target, target_date=goal.target_date.isoformat() if goal.target_date else None,
                contributions=[dict(id=item.id, amount=float(item.amount), created_at=item.created_at.isoformat())
                               for item in contributions])


@app.get('/health')
def health():
    return jsonify(status='ok', service='savings-service')


@app.get('/ready')
def ready():
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ready', service='savings-service')
    except Exception:
        db.session.rollback()
        return jsonify(status='not_ready'), 503


@app.get('/goals')
@authenticated
def list_goals():
    return jsonify(goals=[serialize(goal) for goal in Goal.query.filter_by(user_id=g.user_id).order_by(Goal.name).all()])


@app.post('/goals')
@authenticated
def create_goal():
    data = json_body()
    name = label(data.get('name'), 'Name')
    target = money(data.get('target'))
    target_date = iso_date(data['target_date']) if data.get('target_date') else None
    item = Goal(user_id=g.user_id, name=name, target=target, target_date=target_date)
    db.session.add(item)
    db.session.commit()
    return jsonify(goal=serialize(item)), 201


@app.route('/goals/<goal_id>', methods=['PUT', 'DELETE'])
@authenticated
def change_goal(goal_id):
    item = Goal.query.filter_by(id=goal_id, user_id=g.user_id).first()
    if item is None:
        return jsonify(error='Goal not found.'), 404
    if request.method == 'DELETE':
        Contribution.query.filter_by(goal_id=item.id).delete()
        db.session.delete(item)
        db.session.commit()
        return jsonify(message='Goal deleted. Transactions are unchanged.')
    data = json_body()
    name = label(data.get('name'), 'Name')
    target = money(data.get('target'))
    target_date = iso_date(data['target_date']) if data.get('target_date') else None
    item.name, item.target, item.target_date = name, target, target_date
    db.session.commit()
    return jsonify(goal=serialize(item))


@app.post('/goals/<goal_id>/contributions')
@authenticated
def add_contribution(goal_id):
    goal = Goal.query.filter_by(id=goal_id, user_id=g.user_id).first()
    if goal is None:
        return jsonify(error='Goal not found.'), 404
    data = json_body()
    amount = money(data.get('amount'))
    key = label(data.get('request_key'), 'Request key', 100)
    def replay(item):
        if item.amount != amount:
            return jsonify(error='Request key already used for a different amount.'), 409
        return jsonify(goal=serialize(goal), replayed=True)
    existing = Contribution.query.filter_by(goal_id=goal.id, request_key=key).first()
    if existing:
        return replay(existing)
    item = Contribution(goal_id=goal.id, amount=amount, request_key=key)
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return replay(Contribution.query.filter_by(goal_id=goal.id, request_key=key).one())
    return jsonify(goal=serialize(goal)), 201


@app.delete('/goals/<goal_id>/contributions/<contribution_id>')
@authenticated
def remove_contribution(goal_id, contribution_id):
    goal = Goal.query.filter_by(id=goal_id, user_id=g.user_id).first()
    if goal is None:
        return jsonify(error='Goal not found.'), 404
    item = Contribution.query.filter_by(id=contribution_id, goal_id=goal.id).first()
    if item is None:
        return jsonify(error='Contribution not found.'), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify(goal=serialize(goal))
