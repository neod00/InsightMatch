"""조건 협의 즉시 메일 · 제안서 첨부 파일 누락 회귀 테스트 (2026-11-06).

1) 기업이 조건 협의를 요청해도 컨설턴트에게 메일이 가지 않았다. 인앱 알림만 만들고,
   메일은 다음 날 아침 미열람 묶음 배치를 기다려야 했다(그 전에 알림을 읽으면 메일은 아예 없음).
   협의 요청·응답(수락·역제안·거절)·역제안 수락은 상대가 답해야 하므로 바로 메일을 보낸다.
2) 컨설턴트가 제안서 파일을 첨부했는데 기업 화면에는 기본 제안서만 보였다. 업로드 중이거나
   실패해도 제출이 그대로 됐고, 화면은 10MB 까지 받는다면서 Vercel 은 4.5MB 넘는 요청을 막았다.
"""
import datetime
import io
import os
import sys
import unittest
from unittest.mock import patch

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

import index  # noqa: E402
from index import app, db, email_service  # noqa: E402
from models import Consultant, ErrorLog, Notification, Project, User  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256',
    )
    return {'Authorization': f'Bearer {token}'}


class NegotiationMailTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        self.company = User(email='buyer@example.com', password_hash='x', role='company',
                            name='김담당', company_name='바이어제조')
        self.consultant_user = User(email='expert@example.com', password_hash='x', role='consultant', name='정전문')
        db.session.add_all([self.company, self.consultant_user])
        db.session.flush()
        self.consultant = Consultant(user_id=self.consultant_user.id, name='정전문', verified=True, status='verified')
        db.session.add(self.consultant)
        db.session.flush()
        self.project = Project(company_id=self.company.id, consultant_id=self.consultant.id,
                               title='ISO 9001 인증 프로젝트', status='proposal_submitted',
                               proposal_price=10000000, proposal_duration='3개월')
        db.session.add(self.project)
        db.session.commit()
        self.client = app.test_client()
        self.sent = []

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _capture(self, to_email, subject, html_content, text_content=None, attachments=None):
        self.sent.append({'to': to_email, 'subject': subject, 'html': html_content, 'text': text_content})
        return {'success': True}

    def _negotiate(self, **body):
        body = body or {'requested_price': 9000000, 'message': '<b>예산</b>이 빠듯합니다'}
        return self.client.post(f'/api/projects/{self.project.id}/negotiate',
                                headers=auth_headers(self.company), json=body)

    def _respond(self, **body):
        return self.client.post(f'/api/projects/{self.project.id}/negotiate/respond',
                                headers=auth_headers(self.consultant_user), json=body)

    def test_negotiation_request_emails_consultant_immediately(self):
        with patch.object(email_service, 'send_email', side_effect=self._capture):
            res = self._negotiate()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.sent), 1)
        mail = self.sent[0]
        self.assertEqual(mail['to'], 'expert@example.com')
        self.assertIn('바이어제조에서 조건 협의를 요청했습니다', mail['subject'])
        self.assertIn('9,000,000원', mail['html'])       # 희망 금액
        self.assertIn('10,000,000원', mail['html'])      # 기존 제안 금액
        self.assertIn('&lt;b&gt;예산&lt;/b&gt;', mail['html'])   # 기업 메시지는 이스케이프
        self.assertNotIn('<b>예산</b>', mail['html'])
        self.assertTrue(mail['text'])
        # 이미 메일로 나갔으니 다음 날 묶음 메일이 다시 보내지 않는다.
        note = Notification.query.filter_by(user_id=self.consultant_user.id, type='negotiation_requested').one()
        self.assertIsNotNone(note.emailed_at)

    def test_counter_offer_emails_company_without_fee_terms(self):
        with patch.object(email_service, 'send_email', side_effect=self._capture):
            self._negotiate()
            res = self._respond(action='counter', counter_price=9500000, message='범위를 줄이면 가능합니다')
        self.assertEqual(res.status_code, 200)
        mail = self.sent[-1]
        self.assertEqual(mail['to'], 'buyer@example.com')
        self.assertIn('정전문님이 역제안을 보냈습니다', mail['subject'])
        self.assertIn('9,500,000원', mail['html'])
        self.assertIn('범위를 줄이면 가능합니다', mail['html'])
        for word in ('수수료', '협력계약', '협력 계약', 'partner-agreement'):
            self.assertNotIn(word, mail['html'])
            self.assertNotIn(word, mail['text'])
        note = Notification.query.filter_by(user_id=self.company.id, type='negotiation_response').one()
        self.assertEqual(note.title, '정전문님이 역제안을 보냈습니다')   # "조건 협의에 역제안했습니다" 비문 수정
        self.assertIsNotNone(note.emailed_at)

    def test_accept_and_reject_email_company(self):
        with patch.object(email_service, 'send_email', side_effect=self._capture):
            self._negotiate()
            self._respond(action='accept')
        self.assertIn('조건 협의를 수락했습니다', self.sent[-1]['subject'])
        self.assertIn('9,000,000원', self.sent[-1]['html'])   # 확정 금액

        with patch.object(email_service, 'send_email', side_effect=self._capture):
            self._negotiate(requested_price=8000000)
            self._respond(action='reject', message='금액 조정은 어렵습니다')
        self.assertIn('조건 협의를 거절했습니다', self.sent[-1]['subject'])
        self.assertIn('금액 조정은 어렵습니다', self.sent[-1]['html'])

    def test_company_accepting_counter_emails_consultant(self):
        with patch.object(email_service, 'send_email', side_effect=self._capture):
            self._negotiate()
            self._respond(action='counter', counter_price=9500000)
            res = self.client.post(f'/api/projects/{self.project.id}/negotiate/accept-counter',
                                   headers=auth_headers(self.company))
        self.assertEqual(res.status_code, 200)
        mail = self.sent[-1]
        self.assertEqual(mail['to'], 'expert@example.com')
        self.assertIn('역제안을 수락했습니다', mail['subject'])
        self.assertIn('9,500,000원', mail['html'])

    def test_mail_failure_does_not_break_negotiation_and_is_logged(self):
        with patch.object(email_service, 'send_email', return_value={'success': False, 'message': 'smtp down'}):
            res = self._negotiate()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(db.session.get(Project, self.project.id).status, 'negotiating')
        note = Notification.query.filter_by(user_id=self.consultant_user.id, type='negotiation_requested').one()
        self.assertIsNone(note.emailed_at)    # 묶음 메일이 대신 보낼 수 있게 남겨 둔다
        self.assertEqual(ErrorLog.query.filter_by(level='warning').count(), 1)

    def test_deleted_account_is_not_emailed(self):
        self.consultant_user.email = f'deleted_{self.consultant_user.id}@deleted.invalid'
        db.session.commit()
        with patch.object(email_service, 'send_email', side_effect=self._capture):
            self._negotiate()
        self.assertEqual(self.sent, [])


class ProposalFileUploadTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        self.company = User(email='buyer2@example.com', password_hash='x', role='company', name='김담당')
        self.consultant_user = User(email='expert2@example.com', password_hash='x', role='consultant', name='정전문')
        db.session.add_all([self.company, self.consultant_user])
        db.session.flush()
        self.consultant = Consultant(user_id=self.consultant_user.id, name='정전문', verified=True, status='verified')
        db.session.add(self.consultant)
        db.session.flush()
        self.project = Project(company_id=self.company.id, consultant_id=self.consultant.id,
                               title='ISO 9001 인증 프로젝트', status='proposal_pending')
        db.session.add(self.project)
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_oversized_proposal_file_gets_clear_json_error(self):
        big = io.BytesIO(b'%PDF' + b'0' * (index.PROPOSAL_FILE_MAX_BYTES + 1))
        env = {'SUPABASE_URL': 'https://example.supabase.co', 'SUPABASE_ANON_KEY': 'test-anon'}
        with patch.dict(os.environ, env), patch('requests.post') as storage_post:
            res = self.client.post('/api/upload/proposal-file', headers=auth_headers(self.consultant_user),
                                   data={'project_id': str(self.project.id), 'file': (big, 'proposal.pdf')},
                                   content_type='multipart/form-data')
        self.assertEqual(res.status_code, 413)
        self.assertIn('4MB', res.get_json()['message'])
        storage_post.assert_not_called()

    def test_limit_is_below_vercel_request_body_limit(self):
        self.assertLess(index.PROPOSAL_FILE_MAX_BYTES, int(4.5 * 1024 * 1024))

    def _upload_small(self, env):
        class Ok:
            status_code = 200
            text = '{}'

            def json(self):
                return {}

        small = io.BytesIO(b'%PDF-1.4 small')
        with patch.dict(os.environ, env), patch('requests.post', return_value=Ok()) as storage_post:
            res = self.client.post('/api/upload/proposal-file', headers=auth_headers(self.consultant_user),
                                   data={'project_id': str(self.project.id), 'file': (small, 'proposal.pdf')},
                                   content_type='multipart/form-data')
        self.assertEqual(res.status_code, 200, res.get_json())
        storage_post.assert_called_once()
        return storage_post.call_args.kwargs['headers']

    def test_upload_uses_service_role_key_when_available(self):
        """비공개 버킷의 anon 권한 규칙을 지울 수 있도록 서버 전용 키로 올린다 (2026-11-11)."""
        headers = self._upload_small({'SUPABASE_URL': 'https://example.supabase.co',
                                      'SUPABASE_ANON_KEY': 'test-anon',
                                      'SUPABASE_SERVICE_ROLE_KEY': 'test-service'})
        self.assertEqual(headers['Authorization'], 'Bearer test-service')
        self.assertEqual(headers['apikey'], 'test-service')

    def test_upload_falls_back_to_anon_key_without_service_key(self):
        """서비스 키가 없는 로컬 개발 환경에서는 예전처럼 anon 키로 올린다."""
        headers = self._upload_small({'SUPABASE_URL': 'https://example.supabase.co',
                                      'SUPABASE_ANON_KEY': 'test-anon',
                                      'SUPABASE_SERVICE_ROLE_KEY': ''})
        self.assertEqual(headers['Authorization'], 'Bearer test-anon')


class ProposalFileFrontendContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'dashboard.html'), encoding='utf-8') as f:
            cls.html = f.read()

    def test_frontend_limit_matches_server(self):
        self.assertIn('const PROPOSAL_FILE_MAX_BYTES = 4 * 1024 * 1024;', self.html)
        self.assertNotIn('10 * 1024 * 1024', self.html)
        self.assertIn('제안서 파일 (PDF·Word, 4MB 이하)', self.html)

    def test_submit_is_blocked_while_uploading_or_after_failure(self):
        start = self.html.index('window.submitProposal = async function')
        body = self.html[start:start + 2500]
        self.assertIn("proposalUploadState === 'uploading'", body)
        self.assertIn("proposalUploadState === 'failed'", body)
        # 막는 검사가 실제 제출 요청보다 앞에 있어야 한다.
        self.assertLess(body.index("proposalUploadState === 'failed'"), body.index('/submit-proposal'))

    def test_modal_resets_previous_file(self):
        start = self.html.index('window.openProposalModal = function')
        self.assertIn('resetProposalFile();', self.html[start:start + 800])

    def test_company_can_download_proposal_while_negotiating(self):
        self.assertIn('${isSubmitted || isContracted || isNegotiating ? `\n'
                      '                        <button class="btn btn-secondary btn-sm" onclick="downloadProposal(',
                      self.html)


if __name__ == '__main__':
    unittest.main()
