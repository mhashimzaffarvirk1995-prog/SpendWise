import csv
import importlib.util
import io
import os
from pathlib import Path
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import jwt
import requests


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'services/reporting-service/app.py'
        spec = importlib.util.spec_from_file_location('report_test_app', path)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        with patch.dict(os.environ, {'JWT_SECRET': 'test-secret-' * 5}):
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.client = self.module.app.test_client()
        self.headers = self.token(1)
        self.rows = [
            self.row(1, 1000, 'income', 'Job', '2026-01-02T00:00:00'),
            self.row(2, '0.10', 'expense', ' Food ', '2026-01-03T00:00:00'),
            self.row(3, '0.20', 'expense', 'FOOD', '2026-01-04T00:00:00'),
            self.row(4, 40, 'expense', 'Rent', '2026-01-04T00:00:00'),
            self.row(5, 20, 'expense', 'Rent', '2025-12-15T00:00:00'),
            self.row(6, 999, 'expense', 'Other', '2026-02-01T00:00:00'),
        ]

    def row(self, id, amount, kind, category, date, title='Test'):
        return dict(id=id, amount=amount, type=kind, category=category, created_at=date, title=title)

    def token(self, user, expired=False):
        return {'Authorization': 'Bearer ' + jwt.encode({
            'sub': str(user), 'iss': 'spendwise-auth-service',
            'exp': datetime.now(timezone.utc) + timedelta(minutes=-1 if expired else 10)},
            self.module.JWT_SECRET, algorithm='HS256')}

    def backend(self, url, **kwargs):
        response = Mock(status_code=200)
        response.json.return_value = {'transactions': self.rows} if url.endswith('/transactions') else {'budgets': []}
        return response

    def report(self, month='2026-01'):
        with patch.object(self.module.requests, 'get', side_effect=self.backend) as get:
            response = self.client.get('/reports/monthly', query_string={'month': month}, headers=self.headers)
            for call in get.call_args_list:
                self.assertEqual(call.kwargs['headers']['Authorization'], self.headers['Authorization'])
            return response

    def test_totals_categories_and_year_boundary(self):
        response = self.report()
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(data['summary'], dict(income=1000, expenses=40.3, balance=959.7, transaction_count=4))
        self.assertEqual(data['previous_month'], '2025-12')
        self.assertEqual(data['previous_summary']['expenses'], 20)
        self.assertEqual(data['expense_change'], 20.3)
        self.assertEqual(data['expense_change_percent'], 101.5)
        self.assertEqual(data['categories'], [dict(category='rent', amount=40), dict(category='food', amount=0.3)])
        self.assertEqual(data['highest_spending_category'], 'rent')

    def test_empty_month_zero_baseline_and_utc_boundary(self):
        self.rows = [self.row(1, 10, 'expense', 'Food', '2026-02-01T00:30:00+02:00')]
        data = self.report().json
        self.assertEqual(data['summary']['expenses'], 10)
        self.assertIsNone(data['expense_change_percent'])
        self.assertEqual(self.report('2026-03').json['summary']['transaction_count'], 0)
        self.assertEqual(self.report('0001-01').status_code, 200)

    def test_invalid_month_and_auth_never_fetch(self):
        with patch.object(self.module.requests, 'get') as get:
            for month in ('2026-13', '2026-1', '0000-01', '2026-01\r\nInjected: yes'):
                for path in ('/reports/monthly', '/reports/transactions.csv'):
                    self.assertEqual(self.client.get(path, query_string={'month': month},
                                                     headers=self.headers).status_code, 400)
            self.assertEqual(self.client.get('/reports/monthly').status_code, 401)
            self.assertEqual(self.client.get('/reports/transactions.csv', headers=self.token(1, True)).status_code, 401)
            get.assert_not_called()

    def test_csv_escaping_formula_protection_and_month_filter(self):
        self.rows[1]['title'] = '=SUM(A1:A2)'
        self.rows[2]['title'] = 'Lunch, "friends"\nCafe'
        self.rows[2]['category'] = '\t=1+1'
        with patch.object(self.module.requests, 'get', side_effect=self.backend):
            response = self.client.get('/reports/transactions.csv?month=2026-01', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn('spendwise-2026-01.csv', response.headers['Content-Disposition'])
        rows = list(csv.reader(io.StringIO(response.data.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[2][1], "'=SUM(A1:A2)")
        self.assertEqual(rows[3][1], 'Lunch, "friends"\nCafe')
        self.assertEqual(rows[3][3], "'\t=1+1")
        self.assertEqual(rows[2][4], '0.10')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_dependency_failure_and_partial_budget_report(self):
        with patch.object(self.module.requests, 'get', side_effect=requests.ConnectionError()):
            self.assertEqual(self.client.get('/reports/monthly', headers=self.headers).status_code, 503)
            self.assertEqual(self.client.get('/reports/transactions.csv', headers=self.headers).status_code, 503)
            self.assertEqual(self.client.get('/ready').status_code, 503)
        def backend(url, **kwargs):
            if url.endswith('/budgets'):
                raise requests.ConnectionError()
            return self.backend(url, **kwargs)
        with patch.object(self.module.requests, 'get', side_effect=backend):
            data = self.client.get('/reports/monthly?month=2026-01', headers=self.headers).json
            self.assertEqual(data['summary']['expenses'], 40.3)
            self.assertTrue(data['budget_error'])
        self.rows[0]['amount'] = 'NaN'
        self.assertEqual(self.report().status_code, 503)

    def test_upstream_auth_failure(self):
        response = Mock(status_code=401)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with patch.object(self.module.requests, 'get', return_value=response):
            self.assertEqual(self.client.get('/reports/monthly', headers=self.headers).status_code, 401)


if __name__ == '__main__':
    unittest.main()
