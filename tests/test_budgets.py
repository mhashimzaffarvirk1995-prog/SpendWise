import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import jwt
import requests


class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(__file__).resolve().parents[1] / 'services/budget-service/app.py'
        spec = importlib.util.spec_from_file_location('budget_test_app', path)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        with patch.dict(os.environ, {'BUDGET_DATABASE_URL': 'sqlite:///' + cls.temp.name + '/test.db',
                                     'JWT_SECRET': 'test-secret-' * 5}):
            spec.loader.exec_module(cls.module)
        cls.module.initialize_database()

    @classmethod
    def tearDownClass(cls):
        with cls.module.app.app_context():
            cls.module.db.session.remove()
            cls.module.db.engine.dispose()
        cls.temp.cleanup()

    def setUp(self):
        self.client = self.module.app.test_client()
        with self.module.app.app_context():
            self.module.Budget.query.delete()
            self.module.db.session.commit()
        self.headers = self.token(1)
        self.payload = {'category': ' Food ', 'month': '2026-10', 'amount': 100}

    def token(self, user, expired=False):
        token = jwt.encode({'sub': str(user), 'iss': 'spendwise-auth-service',
                            'exp': datetime.now(timezone.utc) + timedelta(minutes=-1 if expired else 10)},
                           self.module.JWT_SECRET, algorithm='HS256')
        return {'Authorization': 'Bearer ' + token}

    def create(self):
        response = self.client.post('/budgets', json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        return response.json['budget']['id']

    def test_validation_and_duplicate_normalization(self):
        for value in (0, -1, 'NaN', 'Infinity', '1.001', 10**12, None):
            response = self.client.post('/budgets', json=dict(self.payload, amount=value), headers=self.headers)
            self.assertEqual(response.status_code, 400, value)
        for month in ('2026-13', '2026-1', '0000-01'):
            self.assertEqual(self.client.post('/budgets', json=dict(self.payload, month=month),
                                             headers=self.headers).status_code, 400)
        self.create()
        self.assertEqual(self.client.post('/budgets', json=dict(self.payload, category='FOOD'),
                                         headers=self.headers).status_code, 409)

    def test_auth_and_user_isolation(self):
        self.assertEqual(self.client.get('/budgets').status_code, 401)
        self.assertEqual(self.client.get('/budgets', headers=self.token(1, expired=True)).status_code, 401)
        budget_id = self.create()
        other = self.token(2)
        self.assertEqual(self.client.put(f'/budgets/{budget_id}', json=self.payload, headers=other).status_code, 404)
        self.assertEqual(self.client.delete(f'/budgets/{budget_id}', headers=other).status_code, 404)
        response = Mock(status_code=200)
        response.json.return_value = {'transactions': []}
        with patch.object(self.module.requests, 'get', return_value=response):
            self.assertEqual(self.client.get('/budgets?month=2026-10', headers=other).json['budgets'], [])
        self.assertEqual(self.client.post('/budgets', json=self.payload, headers=other).status_code, 201)

    def test_spending_month_case_and_thresholds(self):
        budget_id = self.create()
        response = Mock(status_code=200)
        response.json.return_value = {'transactions': [
            {'type': 'expense', 'category': 'FOOD', 'amount': 80, 'created_at': '2026-10-14T12:00:00'},
            {'type': 'income', 'category': 'food', 'amount': 999, 'created_at': '2026-10-14T12:00:00'},
            {'type': 'expense', 'category': 'food', 'amount': 500, 'created_at': '2026-09-30T23:59:59'},
        ]}
        with patch.object(self.module.requests, 'get', return_value=response) as get:
            budget = self.client.get('/budgets?month=2026-10', headers=self.headers).json['budgets'][0]
            self.assertEqual((budget['spent'], budget['remaining'], budget['status']), (80, 20, 'near'))
            self.assertEqual(get.call_args.kwargs['headers']['Authorization'], self.headers['Authorization'])
            self.client.put(f'/budgets/{budget_id}', json=dict(self.payload, amount=50), headers=self.headers)
            budget = self.client.get('/budgets?month=2026-10', headers=self.headers).json['budgets'][0]
            self.assertEqual((budget['remaining'], budget['status'], budget['progress']), (-30, 'over', 100))
            self.assertEqual(self.client.get('/budgets?month=2026-11', headers=self.headers).json['budgets'], [])
        self.assertEqual(self.client.delete(f'/budgets/{budget_id}', headers=self.headers).status_code, 200)

    def test_dependency_failure_does_not_report_zero(self):
        self.create()
        with patch.object(self.module.requests, 'get', side_effect=requests.ConnectionError()):
            response = self.client.get('/budgets?month=2026-10', headers=self.headers)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('budgets', response.json)
            self.assertEqual(self.client.get('/ready').status_code, 503)


if __name__ == '__main__':
    unittest.main()
