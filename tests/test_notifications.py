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


class NotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(__file__).resolve().parents[1] / 'services/notification-service/app.py'
        spec = importlib.util.spec_from_file_location('notification_test_app', path)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        with patch.dict(os.environ, {'NOTIFICATION_DATABASE_URL': 'sqlite:///' + cls.temp.name + '/test.db',
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
            self.module.Notification.query.delete()
            self.module.db.session.commit()
        self.headers = self.token(1)
        self.response = Mock(status_code=200)
        self.response.json.return_value = {'budgets': [dict(category='food', status='near', remaining=20, percent=80)]}

    def token(self, user, expired=False):
        return {'Authorization': 'Bearer ' + jwt.encode({
            'sub': str(user), 'iss': 'spendwise-auth-service',
            'exp': datetime.now(timezone.utc) + timedelta(minutes=-1 if expired else 10)},
            self.module.JWT_SECRET, algorithm='HS256')}

    def sync(self, headers=None):
        with patch.object(self.module.requests, 'get', return_value=self.response) as get:
            result = self.client.post('/notifications/sync', json={'month': '2026-10'},
                                      headers=headers or self.headers)
            self.assertEqual(get.call_args.kwargs['headers']['Authorization'],
                             (headers or self.headers)['Authorization'])
            return result

    def inbox(self, headers=None):
        return self.client.get('/notifications', headers=headers or self.headers).json

    def test_deduplication_and_read_state_survive_sync(self):
        self.assertEqual(self.sync().json['created'], 1)
        self.assertEqual(self.sync().json['created'], 0)
        data = self.inbox()
        self.assertEqual(data['unread_count'], 1)
        notification_id = data['notifications'][0]['id']
        for _ in range(2):
            self.assertEqual(self.client.post(f'/notifications/{notification_id}/read',
                                             headers=self.headers).status_code, 200)
        self.assertEqual(self.sync().json['created'], 0)
        self.assertEqual(self.inbox()['unread_count'], 0)
        self.response.json.return_value['budgets'][0].update(status='over', remaining=-10, percent=110)
        self.assertEqual(self.sync().json['created'], 1)
        self.assertEqual(self.sync().json['created'], 0)
        data = self.inbox()
        self.assertEqual(len(data['notifications']), 2)
        self.assertEqual(data['unread_count'], 1)
        self.assertIn('exceeded by 10.00', data['notifications'][0]['message'])
        self.assertEqual(self.client.post('/notifications/read-all', headers=self.headers).status_code, 200)
        self.assertEqual(self.inbox()['unread_count'], 0)
        # A new month gets a fresh alert, and saved alerts survive a fresh DB session.
        with patch.object(self.module.requests, 'get', return_value=self.response):
            self.assertEqual(self.client.post('/notifications/sync', json={'month': '2026-11'},
                                             headers=self.headers).json['created'], 1)
        with self.module.app.app_context():
            self.module.db.session.remove()
        self.assertEqual(len(self.inbox()['notifications']), 3)

    def test_auth_and_user_isolation(self):
        for path in ('/notifications',):
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.token(1, expired=True)).status_code, 401)
        self.assertEqual(self.client.post('/notifications/sync', json={}).status_code, 401)
        self.sync()
        item_id = self.inbox()['notifications'][0]['id']
        other = self.token(2)
        self.assertEqual(self.inbox(other)['notifications'], [])
        self.assertEqual(self.client.post(f'/notifications/{item_id}/read', headers=other).status_code, 404)
        self.client.post('/notifications/read-all', headers=other)
        self.assertEqual(self.inbox()['unread_count'], 1)
        self.assertEqual(self.sync(other).json['created'], 1)
        self.assertEqual(self.inbox(other)['unread_count'], 1)

    def test_dependency_failure_and_retry(self):
        self.sync()
        with patch.object(self.module.requests, 'get', side_effect=requests.ConnectionError()):
            self.assertEqual(self.client.post('/notifications/sync', json={'month': '2026-10'},
                                             headers=self.headers).status_code, 503)
        self.assertEqual(len(self.inbox()['notifications']), 1)
        self.assertEqual(self.sync().json['created'], 0)

    def test_validation_and_no_alert_below_threshold(self):
        for data in (None, [], {'month': 10}, {'month': '2026-1'}):
            self.assertEqual(self.client.post('/notifications/sync', json=data,
                                             headers=self.headers).status_code, 400)
        self.response.status_code = 400
        self.assertEqual(self.sync().status_code, 400)
        self.response.status_code = 200
        self.response.json.return_value['budgets'][0]['status'] = 'ok'
        self.assertEqual(self.sync().json['created'], 0)
        self.assertEqual(self.inbox()['unread_count'], 0)


if __name__ == '__main__':
    unittest.main()
