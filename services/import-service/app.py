"""Validated CSV previews and explicit, retry-safe imports."""
import csv
import hashlib
import io
import json
import os
import threading
from datetime import datetime, timezone
from uuid import uuid4

import requests
from flask import Flask, g, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.api import authenticated, configure, label, money, utc_date

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['IMPORT_DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 1100000
db = SQLAlchemy(app)
configure(app)
TRANSACTION_URL = os.getenv('TRANSACTION_SERVICE_URL', 'http://127.0.0.1:5002').rstrip('/')
TIMEOUT = float(os.getenv('SERVICE_TIMEOUT_SECONDS', '5'))
COMMIT_LOCK = threading.Lock()


class Preview(db.Model):
    __tablename__ = 'previews'
    id = db.Column(db.String(32), primary_key=True, default=lambda: uuid4().hex)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    rows_json = db.Column(db.Text, nullable=False)
    errors_json = db.Column(db.Text, nullable=False)
    confirmed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class ImportedRow(db.Model):
    __tablename__ = 'imported_rows'
    user_id = db.Column(db.Integer, primary_key=True)
    fingerprint = db.Column(db.String(64), primary_key=True)
    transaction_id = db.Column(db.Integer, nullable=False)


def initialize_database():
    with app.app_context():
        db.create_all()


def fingerprint(row):
    canonical = dict(date=utc_date(row['created_at']).date().isoformat(), title=row['title'].strip().casefold(),
                     type=row['type'], category=row['category'].strip().casefold(), amount=format(money(row['amount']), '.2f'))
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def serialize(item):
    return dict(id=item.id, rows=json.loads(item.rows_json), errors=json.loads(item.errors_json), confirmed=item.confirmed)


def backend_headers():
    return {'Authorization': request.headers['Authorization'], 'X-Request-ID': g.request_id}


@app.get('/health')
def health():
    return jsonify(status='ok', service='import-service')


@app.get('/ready')
def ready():
    try:
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ready', service='import-service')
    except Exception:
        db.session.rollback()
        return jsonify(status='not_ready'), 503


@app.post('/imports/preview')
@authenticated
def preview_csv():
    file = request.files.get('file')
    if file is None:
        raise ValueError('Choose a CSV file.')
    raw = file.read(1000001)
    if len(raw) > 1000000:
        raise ValueError('CSV must be at most 1 MB.')
    try:
        content = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('CSV must use UTF-8 encoding.') from None
    if '\x00' in content:
        raise ValueError('CSV contains invalid characters.')
    reader = csv.DictReader(io.StringIO(content), strict=True)
    try:
        fields = reader.fieldnames
    except csv.Error:
        raise ValueError("CSV quoting is malformed.") from None
    if not fields:
        raise ValueError('CSV is empty.')
    aliases = {'date (utc)': 'date', 'created_at': 'date'}
    normalized = [aliases.get(field.strip().lower(), field.strip().lower()) for field in fields]
    if len(set(normalized)) != len(normalized) or set(normalized) != {'date', 'title', 'type', 'category', 'amount'}:
        raise ValueError('CSV must contain exactly: date,title,type,category,amount. SpendWise exports are supported.')
    reader.fieldnames = normalized
    try:
        response = requests.get(f'{TRANSACTION_URL}/transactions', headers=backend_headers(), timeout=TIMEOUT)
        response.raise_for_status()
        seen = {fingerprint(row) for row in response.json()['transactions']}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return jsonify(error='Existing transactions could not be checked. Please try again.'), 503
    seen.update(item.fingerprint for item in ImportedRow.query.filter_by(user_id=g.user_id).all())
    rows, errors = [], []
    try:
        for number, row in enumerate(reader, start=2):
            if number > 1001:
                raise ValueError('CSV may contain at most 1000 data rows.')
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError('Row has the wrong number of columns.')
                # Reverse our CSV export's formula-protection apostrophe only for dangerous prefixes.
                for key in ('title', 'category'):
                    value = row[key]
                    if value.startswith("'") and (value[1:].lstrip().startswith(('=', '+', '-', '@')) or value[1:].startswith(('\t', '\r', '\n'))):
                        row[key] = value[1:]
                payload = dict(title=label(row['title'], 'Title'), category=label(row['category'], 'Category', 50),
                               amount=float(money(row['amount'])), type=row['type'].strip().lower(),
                               created_at=utc_date(row['date'].strip()).isoformat())
                if payload['type'] not in ('income', 'expense'):
                    raise ValueError('Type must be income or expense.')
                key = fingerprint(payload)
                duplicate = key in seen
                seen.add(key)
                rows.append(dict(line=number, payload=payload, fingerprint=key, duplicate=duplicate))
            except ValueError as error:
                errors.append(dict(line=number, error=str(error)))
    except csv.Error:
        raise ValueError('CSV quoting is malformed.') from None
    if not rows and not errors:
        raise ValueError('CSV has no data rows.')
    item = Preview(user_id=g.user_id, rows_json=json.dumps(rows), errors_json=json.dumps(errors))
    db.session.add(item)
    db.session.commit()
    return jsonify(preview=serialize(item)), 201


@app.get('/imports/<preview_id>')
@authenticated
def get_preview(preview_id):
    item = Preview.query.filter_by(id=preview_id, user_id=g.user_id).first()
    if item is None:
        return jsonify(error='Preview not found.'), 404
    return jsonify(preview=serialize(item))


@app.post('/imports/<preview_id>/confirm')
@authenticated
def confirm_import(preview_id):
    with COMMIT_LOCK:
        item = Preview.query.filter_by(id=preview_id, user_id=g.user_id).first()
        if item is None:
            return jsonify(error='Preview not found.'), 404
        if json.loads(item.errors_json):
            return jsonify(error='Fix the invalid rows and upload the file again before confirming.'), 400
        if item.confirmed:
            return jsonify(imported=0, skipped=len(json.loads(item.rows_json)), confirmed=True)
        try:
            response = requests.get(f'{TRANSACTION_URL}/transactions', headers=backend_headers(), timeout=TIMEOUT)
            response.raise_for_status()
            existing = {fingerprint(row) for row in response.json()['transactions']}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return jsonify(error='Existing transactions could not be checked. Retry this preview safely.'), 503
        imported = skipped = 0
        for row in json.loads(item.rows_json):
            key = row['fingerprint']
            if row['duplicate'] or key in existing or db.session.get(ImportedRow, (g.user_id, key)):
                skipped += 1
                continue
            try:
                response = requests.post(f'{TRANSACTION_URL}/transactions', headers=dict(backend_headers(),
                                         **{'Idempotency-Key': 'import:' + key}), json=row['payload'], timeout=TIMEOUT)
                response.raise_for_status()
                transaction_id = response.json()['transaction']['id']
            except (requests.RequestException, ValueError, KeyError, TypeError):
                return jsonify(error='Import paused. Some rows may already be saved. Retry this preview safely.',
                               imported=imported, skipped=skipped, confirmed=False), 503
            db.session.add(ImportedRow(user_id=g.user_id, fingerprint=key, transaction_id=transaction_id))
            db.session.commit()
            imported += 1
        item.confirmed = True
        db.session.commit()
        return jsonify(imported=imported, skipped=skipped, confirmed=True)
