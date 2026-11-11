"""견적 요청 알림이 다음 날 묶음 메일로 한 번 더 나가던 문제 (2026-11-11).

견적 요청 메일은 요청 시점에 바로 나가는데, 함께 만든 인앱 알림에 emailed_at 을 채우지 않아
다음 날 아침 미열람 알림 묶음 메일(_cron_job_unread_digest)이 같은 견적 요청을 다시 보냈다.
운영 DB 에서 컨설턴트 알림(quote_request)이 emailed_at 없이 남아 있는 것을 확인했다.
"""
import datetime
import os
import sys
import unittest
from unittest.mock import patch

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

import index  # noqa: E402
from index import app, db, email_service  # noqa: E402
from models import Consultant, Notification, User  # noqa: E402


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256',
    )
    return {'Authorization': f'Bearer {token}'}


class QuoteRequestDigestDuplicateTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        self.company = User(email='buyer-q@example.com', password_hash='x', role='company', name='김담당',
                            company_name='바이어제조', biz_reg_no='1234567891', biz_status='verified')
        self.consultant_user = User(email='expert-q@example.com', password_hash='x', role='consultant', name='정전문')
        db.session.add_all([self.company, self.consultant_user])
        db.session.flush()
        self.consultant = Consultant(user_id=self.consultant_user.id, name='정전문', verified=True, status='verified')
        db.session.add(self.consultant)
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _request_quote(self, mail_result):
        with patch.object(email_service, 'send_quote_request_to_consultant', autospec=True,
                          return_value=mail_result), \
                patch.object(email_service, 'send_quote_confirmation_to_company', autospec=True,
                             return_value={'success': True}):
            res = self.client.post('/api/quotes/request',
                                   json={'consultant_ids': [self.consultant.id], 'analysis_context': {}},
                                   headers=auth_headers(self.company))
        self.assertEqual(res.status_code, 201, res.get_json())
        return Notification.query.filter_by(user_id=self.consultant_user.id, type='quote_request').one()

    def _run_digest_seven_hours_later(self, note):
        # 묶음 메일은 6시간 넘게 안 읽힌 알림만 고른다.
        note.created_at = index._naive_utc_now() - datetime.timedelta(hours=7)
        db.session.commit()
        with patch.object(email_service, 'send_notification_digest', autospec=True,
                          return_value={'success': True}) as digest:
            index._reset_cron_email_budget()
            index._cron_job_unread_digest()
        return digest

    def test_sent_quote_request_is_not_mailed_again_in_digest(self):
        note = self._request_quote({'success': True})
        self.assertIsNotNone(note.emailed_at)
        digest = self._run_digest_seven_hours_later(note)
        digest.assert_not_called()

    def test_failed_quote_request_mail_is_left_for_digest(self):
        note = self._request_quote({'success': False, 'message': 'smtp down'})
        self.assertIsNone(note.emailed_at)
        digest = self._run_digest_seven_hours_later(note)
        digest.assert_called_once()   # 바로 못 보냈으면 묶음 메일이 대신 보낸다


if __name__ == '__main__':
    unittest.main()
