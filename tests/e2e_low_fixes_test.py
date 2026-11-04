"""2026-11-04 E2E 점검(T12) '낮음' 항목 회귀 테스트.

회사명 표시 / 계약서 조사 / 옛 요청 글 한국어 / 로그인 제한(계정·IP) / 초대 메일 한국 시간 /
일정 처리 / API 404 JSON / 중복 컨설턴트 선택 / favicon / 로그인 응답의 휴대폰 번호.
"""
import datetime
import json
import os
import sys
import unittest

import jwt
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

# index 를 import 하기 전에 인메모리 DB 를 지정해야 한다(conftest.py 와 같은 이유).
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

import index  # noqa: E402
from index import app, db, format_kst, josa_euro, localize_request_description  # noqa: E402
from models import Consultant, Milestone, Notification, Project, User  # noqa: E402


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256',
    )
    return {'Authorization': f'Bearer {token}'}


class LowSeverityFixesTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        self.company = User(email='buyer@example.com', password_hash=generate_password_hash('Passw0rd!'),
                            role='company', name='김담당', company_name='바이어제조', phone='010-1111-2222',
                            biz_reg_no='1234567891', biz_status='verified')
        self.consultant_user = User(email='expert@example.com', password_hash='x', role='consultant', name='전문가')
        db.session.add_all([self.company, self.consultant_user])
        db.session.flush()
        self.consultant = Consultant(user_id=self.consultant_user.id, name='전문가', verified=True, status='verified',
                                     iso_experience='{"9001": true}')
        db.session.add(self.consultant)
        db.session.flush()
        self.project = Project(company_id=self.company.id, consultant_id=self.consultant.id,
                               title='ISO 9001:2026 인증 프로젝트', status='proposal_submitted',
                               proposal_price=8500000, proposal_duration='3개월',
                               description='업종: Manufacturing\n희망 일정: 유연함\n예산: unknown\n'
                                           '인증 사유: customer_demand, export\n추가 요청: 직접 쓴 글')
        db.session.add(self.project)
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ---------- 회사명 ----------
    def test_consultant_sees_company_name_not_contact_person(self):
        rows = self.client.get('/api/projects', headers=auth_headers(self.consultant_user)).get_json()
        self.assertEqual(rows[0]['company_name'], '바이어제조')

        self.client.post(f'/api/projects/{self.project.id}/negotiate', headers=auth_headers(self.company),
                         json={'requested_price': 8000000})
        note = Notification.query.filter_by(user_id=self.consultant_user.id, type='negotiation_requested').first()
        self.assertTrue(note.title.startswith('바이어제조에서'))

    # ---------- 계약서 조사 ----------
    def test_josa_euro(self):
        cases = {'ISO 9001:2026 인증 프로젝트': '로', '3개월': '로', '8,500,000원': '으로', '6주': '로',
                 '12주일': '로', '1년': '으로', '10': '으로', '7': '로', '「통합 인증」': '으로', 'ISO': '로'}
        for word, expected in cases.items():
            self.assertEqual(josa_euro(word), expected, word)

    def test_contract_text_uses_correct_particles(self):
        self.assertEqual(self.client.post(f'/api/projects/{self.project.id}/contract/draft',
                                          headers=auth_headers(self.company), json={}).status_code, 200)
        text = json.dumps(self.client.get(f'/api/projects/{self.project.id}/contract/preview',
                                          headers=auth_headers(self.company)).get_json(), ensure_ascii=False)
        self.assertIn('「ISO 9001:2026 인증 프로젝트」로 한다', text)
        self.assertIn('8,500,000원으로 한다', text)
        self.assertIn('3개월로 한다', text)
        self.assertNotIn('」으로', text)
        self.assertIn('0.3-draft', text)

    # ---------- 옛 요청 글 ----------
    def test_old_descriptions_are_shown_in_korean(self):
        text = localize_request_description(self.project.description)
        self.assertIn('업종: 제조업', text)
        self.assertIn('예산: 미정', text)
        self.assertIn('인증 사유: 고객사·거래처 요구, 해외 수출·바이어 요구', text)
        self.assertIn('추가 요청: 직접 쓴 글', text)
        rows = self.client.get('/api/projects', headers=auth_headers(self.consultant_user)).get_json()
        self.assertNotIn('Manufacturing', rows[0]['description'])
        self.assertEqual(Project.query.get(self.project.id).description.split('\n')[0], '업종: Manufacturing')

    # ---------- 로그인 제한 ----------
    def test_login_limit_is_per_account_with_wider_ip_limit(self):
        # 같은 IP 에서 서로 다른 계정 15번 실패해도 IP 제한(50)에 걸리지 않는다
        for i in range(15):
            code = self.client.post('/api/auth/login',
                                    json={'email': f'user{i}@example.com', 'password': 'x'}).status_code
            self.assertEqual(code, 401)
        # 한 계정을 계속 틀리면 11번째에 막힌다
        codes = [self.client.post('/api/auth/login', json={'email': 'buyer@example.com', 'password': 'bad'}).status_code
                 for _ in range(11)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10], 429)
        self.assertEqual(index.LOGIN_IP_LIMIT, 50)

    def test_login_response_includes_own_phone(self):
        body = self.client.post('/api/auth/login', json={'email': 'buyer@example.com', 'password': 'Passw0rd!'}).get_json()
        self.assertEqual(body['user']['phone'], '010-1111-2222')

    # ---------- 초대 메일 시각 ----------
    def test_format_kst(self):
        self.assertEqual(format_kst(datetime.datetime(2026, 11, 3, 23, 30)), '2026-11-04 08:30')
        self.assertEqual(format_kst(None), '')

    # ---------- 일정 ----------
    def _contracted_with_milestones(self, n=3):
        self.project.status = 'contracted'
        for i in range(n):
            db.session.add(Milestone(project_id=self.project.id, title=f'단계 {i + 1}'))
        db.session.commit()
        return Milestone.query.filter_by(project_id=self.project.id).order_by(Milestone.id).all()

    def test_schedule_must_cover_every_milestone_once(self):
        ms = self._contracted_with_milestones()
        base = datetime.date.today() + datetime.timedelta(days=7)
        def day(k):
            return (base + datetime.timedelta(days=k)).isoformat()
        url = f'/api/projects/{self.project.id}/propose-schedule'
        partial = {'schedule': [{'milestone_id': ms[0].id, 'proposed_date': day(0)}]}
        dup = {'schedule': [{'milestone_id': ms[0].id, 'proposed_date': day(0)},
                            {'milestone_id': ms[0].id, 'proposed_date': day(1)},
                            {'milestone_id': ms[1].id, 'proposed_date': day(2)}]}
        full = {'schedule': [{'milestone_id': m.id, 'proposed_date': day(i)} for i, m in enumerate(ms)]}
        self.assertEqual(self.client.post(url, headers=auth_headers(self.consultant_user), json=partial).status_code, 400)
        self.assertEqual(self.client.post(url, headers=auth_headers(self.consultant_user), json=dup).status_code, 400)
        self.assertEqual(self.client.post(url, headers=auth_headers(self.consultant_user), json=full).status_code, 200)

        # 거절하면 제안됐던 날짜를 비운다
        res = self.client.post(f'/api/projects/{self.project.id}/reject-schedule', headers=auth_headers(self.company),
                               json={'reason': '일정 조정'})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(all(m.due_date is None for m in Milestone.query.filter_by(project_id=self.project.id)))

    # ---------- 오류 응답 ----------
    def test_unknown_api_returns_json_not_html(self):
        # 없는 API 를 POST 로 부르면 예전엔 405 HTML 페이지가 나갔다
        res = self.client.post('/api/projects/1/negotiate/does-not-exist')
        self.assertEqual(res.status_code, 404)
        self.assertTrue(res.is_json)
        self.assertIn('API', res.get_json()['message'])
        # 있는 API 에 지원하지 않는 방식이면 405 를 유지하되 JSON 으로
        res = self.client.delete('/api/health')
        self.assertEqual(res.status_code, 405)
        self.assertTrue(res.is_json)
        res = self.client.get('/api/this-does-not-exist')
        self.assertEqual(res.status_code, 404)
        self.assertTrue(res.is_json)

    def test_duplicate_consultant_ids_are_counted_once(self):
        res = self.client.post('/api/quotes/request', headers=auth_headers(self.company),
                               json={'consultant_ids': [self.consultant.id, self.consultant.id],
                                     'analysis_context': {'selected_standards': ['ISO 14001:2026']}})
        self.assertEqual(res.status_code, 201, res.get_json())

    def test_upload_without_storage_is_503(self):
        # 저장소 키를 확실히 비우고, 혹시라도 외부 업로드 요청이 나가면 테스트가 실패하게 한다
        # (conftest 도 키를 비우지만 이 파일만 따로 돌릴 때를 대비)
        from unittest.mock import patch
        with patch.dict(os.environ, {'SUPABASE_URL': '', 'SUPABASE_ANON_KEY': ''}), \
                patch('requests.post', side_effect=AssertionError('외부 업로드 호출 금지')):
            res = self.client.post('/api/upload/profile-image', headers=auth_headers(self.consultant_user),
                                   data={'file': (__import__('io').BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 50),
                                                  'a.png', 'image/png')},
                                   content_type='multipart/form-data')
        self.assertEqual(res.status_code, 503)
        self.assertIn('message', res.get_json())

    # ---------- favicon ----------
    def test_favicon_is_served(self):
        res = self.client.get('/favicon.ico')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, 'image/x-icon')
        res.close()


if __name__ == '__main__':
    unittest.main()
