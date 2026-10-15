import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

import jwt
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services'))


class DatabaseCase(unittest.TestCase):
    service = None

    @classmethod
    def setUpClass(cls):
        if cls.service is None:
            return
        cls.temp = tempfile.TemporaryDirectory()
        path = ROOT / f'services/{cls.service}-service/app.py'
        spec = importlib.util.spec_from_file_location(cls.service + '_new_tests', path)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        with patch.dict(os.environ, {cls.env_key: 'sqlite:///' + cls.temp.name + '/test.db',
                                     'JWT_SECRET': 'test-key-' * 8}):
            spec.loader.exec_module(cls.module)
        cls.module.initialize_database()
        cls.secret = 'test-key-' * 8

    @classmethod
    def tearDownClass(cls):
        if cls.service is None:
            return
        with cls.module.app.app_context():
            cls.module.db.session.remove()
            cls.module.db.engine.dispose()
        cls.temp.cleanup()

    def setUp(self):
        self.client = self.module.app.test_client()
        self.env = patch.dict(os.environ, {'JWT_SECRET': self.secret})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.headers = self.token(1)
        with self.module.app.app_context():
            for table in reversed(self.module.db.metadata.sorted_tables):
                self.module.db.session.execute(table.delete())
            self.module.db.session.commit()

    def token(self, user, expired=False):
        return {'Authorization': 'Bearer ' + jwt.encode({
            'sub': str(user), 'iss': 'spendwise-auth-service',
            'exp': datetime.now(timezone.utc) + timedelta(minutes=-1 if expired else 10)},
            self.secret, algorithm='HS256')}


class TransactionSafetyTests(DatabaseCase):
    service = 'transaction'
    env_key = 'TRANSACTION_DATABASE_URL'

    def test_idempotent_retry_conflict_and_deleted_transaction(self):
        data = dict(title='Rent', category='Home', amount='20.50', type='expense', created_at='2026-01-01')
        headers = dict(self.headers, **{'Idempotency-Key': 'test-1'})
        response = self.client.post('/transactions', json=data, headers=headers)
        self.assertEqual(response.status_code, 201)
        id = response.json['transaction']['id']
        retry = self.client.post('/transactions', json=data, headers=headers)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json['transaction']['id'], id)
        self.assertEqual(self.client.post('/transactions', json=dict(data, amount=30), headers=headers).status_code, 409)
        self.client.delete(f'/transactions/{id}', headers=self.headers)
        self.assertEqual(self.client.post('/transactions', json=data, headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/transactions', headers=self.headers).json['transactions'], [])
        other = dict(self.token(2), **{'Idempotency-Key': 'test-1'})
        self.assertEqual(self.client.post('/transactions', json=data, headers=other).status_code, 201)

    def test_validation_and_utc_dates(self):
        data = dict(title='Item', category='Food', amount=10, type='expense')
        for value in ('NaN', 'Infinity', 0, -1, '0.001', 10**12):
            self.assertEqual(self.client.post('/transactions', json=dict(data, amount=value),
                                             headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/transactions', json=dict(data, created_at='invalid'),
                                         headers=self.headers).status_code, 400)
        response = self.client.post('/transactions', json=dict(data, created_at='2026-02-01T00:30:00+02:00'),
                                    headers=self.headers)
        self.assertEqual(response.json['transaction']['created_at'], '2026-01-31T22:30:00')


class RecurringTests(DatabaseCase):
    service = 'recurring'
    env_key = 'RECURRING_DATABASE_URL'

    def create(self, **kwargs):
        data = dict(title='Rent', category='Home', amount=100, type='expense', frequency='monthly',
                    start_date=datetime.now(timezone.utc).date().isoformat())
        data.update(kwargs)
        response = self.client.post('/schedules', json=data, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        return response.json['schedule']['id']

    def test_month_end_anchor_and_leap_year(self):
        self.assertEqual(self.module.next_date(date(2026, 1, 31), 'monthly', 31), date(2026, 2, 28))
        self.assertEqual(self.module.next_date(date(2026, 2, 28), 'monthly', 31), date(2026, 3, 31))
        self.assertEqual(self.module.next_date(date(2028, 1, 31), 'monthly', 31), date(2028, 2, 29))
        self.assertEqual(self.module.next_date(date(2026, 12, 31), 'weekly', 31), date(2027, 1, 7))

    def test_failure_retries_same_key_and_advances_once(self):
        id = self.create()
        with patch.object(self.module.requests, 'post', side_effect=requests.Timeout()) as post:
            response = self.client.post('/schedules/run-due', headers=self.headers)
            self.assertEqual(response.json['failed'], 1)
            original_key = post.call_args.kwargs['headers']['Idempotency-Key']
        success = Mock(status_code=201)
        with patch.object(self.module.requests, 'post', return_value=success) as post:
            self.assertEqual(self.client.post('/schedules/run-due', headers=self.headers).json['created'], 1)
            self.assertEqual(post.call_args.kwargs['headers']['Idempotency-Key'], original_key)
            self.assertEqual(self.client.post('/schedules/run-due', headers=self.headers).json['created'], 0)
            self.assertEqual(post.call_count, 1)
        self.assertIsNone(self.client.get('/schedules', headers=self.headers).json['schedules'][0]['last_error'])
        self.assertEqual(self.client.delete('/schedules/' + id, headers=self.token(2)).status_code, 404)

    def test_worker_pause_and_user_isolation(self):
        id = self.create()
        self.client.patch('/schedules/' + id, json={'active': False}, headers=self.headers)
        with patch.object(self.module.requests, 'post') as post:
            self.client.post('/schedules/run-due', headers=self.headers)
            post.assert_not_called()
        self.assertEqual(self.client.get('/schedules', headers=self.token(2)).json['schedules'], [])
        self.assertEqual(self.client.get('/schedules').status_code, 401)
        self.client.patch('/schedules/' + id, json={'active': True}, headers=self.headers)
        stop = threading.Event()
        called = threading.Event()
        def post(*args, **kwargs):
            called.set()
            stop.set()
            return Mock(status_code=201)
        with patch.object(self.module.requests, 'post', side_effect=post):
            thread = self.module.start_background_worker(stop)
            self.assertTrue(called.wait(3))
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_invalid_fields(self):
        response = self.client.post('/schedules', json={'title': 'Rent'}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        id = self.create()
        self.assertEqual(self.client.patch('/schedules/' + id, json={'active': 'yes'}, headers=self.headers).status_code, 400)


class SavingsTests(DatabaseCase):
    service = 'savings'
    env_key = 'SAVINGS_DATABASE_URL'

    def test_contributions_completion_retry_edit_remove_and_isolation(self):
        response = self.client.post('/goals', json={'name': 'Laptop', 'target': 100}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        id = response.json['goal']['id']
        path = f'/goals/{id}/contributions'
        data = {'amount': '80.10', 'request_key': 'same-request'}
        self.assertEqual(self.client.post(path, json=data, headers=self.headers).status_code, 201)
        response = self.client.post(path, json=data, headers=self.headers)
        self.assertEqual(response.json['goal']['saved'], 80.1)
        self.assertEqual(self.client.post(path, json=dict(data, amount=50), headers=self.headers).status_code, 409)
        response = self.client.post(path, json={'amount': 20, 'request_key': 'next-request'}, headers=self.headers)
        self.assertTrue(response.json['goal']['completed'])
        self.assertEqual(response.json['goal']['percent'], 100)
        self.assertEqual(self.client.post(path, json=data, headers=self.token(2)).status_code, 404)
        self.assertEqual(self.client.get('/goals', headers=self.token(2)).json['goals'], [])
        contribution_id = response.json['goal']['contributions'][0]['id']
        response = self.client.delete(path + '/' + contribution_id, headers=self.headers)
        self.assertEqual(response.json['goal']['saved'], 80.1)
        response = self.client.put('/goals/' + id, json={'name': 'Laptop updated', 'target': 200,
                                                       'target_date': '2027-02-01'}, headers=self.headers)
        self.assertEqual(response.json['goal']['remaining'], 119.9)
        self.client.delete('/goals/' + id, headers=self.headers)
        with self.module.app.app_context():
            self.assertEqual(self.module.Contribution.query.count(), 0)

    def test_validation_and_auth(self):
        for data in ({'name': 'Laptop', 'target': 'NaN'}, {'name': '', 'target': 100},
                     {'name': 'Laptop', 'target': 100, 'target_date': '2026-02-30'}):
            self.assertEqual(self.client.post('/goals', json=data, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get('/goals', headers=self.token(1, True)).status_code, 401)


class ImportTests(DatabaseCase):
    service = 'import'
    env_key = 'IMPORT_DATABASE_URL'
    csv = 'date,title,type,category,amount\n2026-01-01,Food,expense,Food,10.50\n'

    def preview(self, content=None):
        response = Mock(status_code=200)
        response.json.return_value = {'transactions': []}
        with patch.object(self.module.requests, 'get', return_value=response):
            return self.client.post('/imports/preview', data={'file': (io.BytesIO((content or self.csv).encode()), 'test.csv')},
                                    headers=self.headers)

    def backend(self, url, **kwargs):
        response = Mock(status_code=200)
        response.json.return_value = {'transactions': []}
        return response

    def test_preview_confirmation_duplicate_reupload_and_retry(self):
        content = self.csv + '2026-01-01,FOOD,expense,food,10.50\n'
        preview = self.preview(content).json['preview']
        self.assertTrue(preview['rows'][1]['duplicate'])
        with self.module.app.app_context():
            self.assertEqual(self.module.ImportedRow.query.count(), 0)
        path = '/imports/' + preview['id'] + '/confirm'
        success = Mock(status_code=201)
        success.json.return_value = {'transaction': {'id': 50}}
        with patch.object(self.module.requests, 'get', side_effect=self.backend), \
             patch.object(self.module.requests, 'post', side_effect=requests.Timeout()) as post:
            self.assertEqual(self.client.post(path, headers=self.headers).status_code, 503)
            original_key = post.call_args.kwargs['headers']['Idempotency-Key']
        with patch.object(self.module.requests, 'get', side_effect=self.backend), \
             patch.object(self.module.requests, 'post', return_value=success) as post:
            response = self.client.post(path, headers=self.headers)
            self.assertEqual(response.json['imported'], 1)
            self.assertEqual(response.json['skipped'], 1)
            self.assertEqual(post.call_args.kwargs['headers']['Idempotency-Key'], original_key)
            self.assertEqual(self.client.post(path, headers=self.headers).json['imported'], 0)
            self.assertEqual(post.call_count, 1)
        self.assertTrue(self.preview().json['preview']['rows'][0]['duplicate'])
        self.assertEqual(self.client.get('/imports/' + preview['id'], headers=self.token(2)).status_code, 404)
        self.assertEqual(self.client.post(path, headers=self.token(2)).status_code, 404)

    def test_invalid_preview_blocks_confirm_and_size_limit(self):
        preview = self.preview(self.csv + '2026-01-02,Bad,expense,Food,NaN\n').json['preview']
        self.assertEqual(len(preview['errors']), 1)
        self.assertEqual(self.client.post('/imports/' + preview['id'] + '/confirm', headers=self.headers).status_code, 400)
        for content in ('wrong,columns\n1,2\n', 'date,title,type,category,amount\n', 'x' * 1000001):
            self.assertEqual(self.preview(content).status_code, 400)
        self.assertEqual(self.client.post('/imports/preview').status_code, 401)

    def test_export_headers_and_existing_transaction_matching(self):
        content = 'Date (UTC),Title,Type,Category,Amount\n2026-01-01T00:00:00Z,"\'=1+1",expense,Food,10.50\n'
        response = self.preview(content)
        self.assertEqual(response.status_code, 201)
        row = response.json['preview']['rows'][0]
        self.assertEqual(row['payload']['title'], '=1+1')
        backend = Mock(status_code=200)
        backend.json.return_value = {'transactions': [dict(row['payload'], id=1)]}
        with patch.object(self.module.requests, 'get', return_value=backend), patch.object(self.module.requests, 'post') as post:
            response = self.client.post('/imports/' + response.json['preview']['id'] + '/confirm', headers=self.headers)
            self.assertEqual(response.json['skipped'], 1)
            post.assert_not_called()
