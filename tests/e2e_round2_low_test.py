"""2026-11-04 E2E 2차 재점검(T12 6절) '낮음' 항목 회귀 테스트.

R2-05: 화면에 그대로 보이는 API 안내·오류 문구가 한국어
R2-08: 서명 알림 제목의 조사("기업이")
R2-11: 관리자 매칭 요청 목록이 2규격 요청의 규격을 모두 보이고, 업종·직원 수·희망 일정을 채운다
(R2-06 회사명 미리 채우기, R2-07 관리자 화면 표시, R2-09 역제안 창, R2-10 ESC 는 화면 코드라 브라우저로 확인)
"""
import datetime
import os
import sys
import unittest

import jwt
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

from index import app, db, standards_from_title  # noqa: E402
from models import Consultant, Notification, Project, User  # noqa: E402


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256',
    )
    return {'Authorization': f'Bearer {token}'}


class Round2LowTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        self.admin = User(email='admin-r2@example.com', password_hash='x', role='admin', name='관리자')
        self.company = User(email='buyer-r2@example.com', password_hash=generate_password_hash('Passw0rd!'),
                            role='company', name='담당', company_name='두규격제조',
                            biz_reg_no='1234567891', biz_status='verified')
        self.cu = User(email='expert-r2@example.com', password_hash='x', role='consultant', name='전문가')
        db.session.add_all([self.admin, self.company, self.cu])
        db.session.flush()
        self.consultant = Consultant(user_id=self.cu.id, name='전문가', verified=True, status='verified',
                                     iso_experience='{"9001": true, "14001": true}',
                                     industry_experience='["Manufacturing"]', regions='경기')
        db.session.add(self.consultant)
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ---------- R2-05 ----------
    def test_login_failure_message_is_korean(self):
        body = self.client.post('/api/auth/login', json={'email': 'buyer-r2@example.com', 'password': 'wrong'}).get_json()
        self.assertEqual(body['message'], '이메일 또는 비밀번호가 올바르지 않습니다.')

    def test_duplicate_signup_message_is_korean(self):
        res = self.client.post('/api/auth/signup', json={
            'email': 'buyer-r2@example.com', 'password': 'Passw0rd!x', 'name': '중복', 'role': 'company'})
        self.assertIn(res.status_code, (400, 409))
        self.assertNotRegex(res.get_json()['message'], r'^[A-Za-z]')

    def test_consultant_register_validation_is_korean(self):
        res = self.client.post('/api/consultants/register', headers=auth_headers(self.company), json={})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json()['message'], '컨설턴트 계정만 전문가 프로필을 등록할 수 있습니다.')

    # ---------- R2-08 ----------
    def test_signature_notice_uses_correct_particle(self):
        project = Project(company_id=self.company.id, consultant_id=self.consultant.id,
                          title='ISO 9001:2026 인증 프로젝트', status='proposal_submitted',
                          proposal_price=8500000, proposal_duration='3개월')
        db.session.add(project)
        db.session.commit()
        self.assertEqual(self.client.post(f'/api/projects/{project.id}/contract/draft',
                                          headers=auth_headers(self.company), json={}).status_code, 200)
        preview = self.client.get(f'/api/projects/{project.id}/contract/preview',
                                  headers=auth_headers(self.company)).get_json()
        contract_hash = preview.get('contract_hash') or preview.get('contractHash')
        res = self.client.post(f'/api/projects/{project.id}/contract/sign', headers=auth_headers(self.company),
                               json={'contract_hash': contract_hash})
        self.assertEqual(res.status_code, 200, res.get_json())
        titles = [n.title for n in Notification.query.filter_by(user_id=self.cu.id, type='signature_requested')]
        self.assertIn('기업이 계약서에 서명했습니다', titles)
        self.assertFalse(any('기업가' in t for t in titles))

    # ---------- R2-11 ----------
    def test_standards_from_title(self):
        self.assertEqual(standards_from_title('ISO 9001:2026, ISO 14001:2026 인증 프로젝트'),
                         ['ISO 9001:2026', 'ISO 14001:2026'])
        self.assertEqual(standards_from_title('ISO/IEC 27001:2022, ISO 14064-1:2018 인증 프로젝트'),
                         ['ISO/IEC 27001:2022', 'ISO 14064-1:2018'])
        self.assertEqual(standards_from_title('IATF 16949:2016 인증 프로젝트'), ['IATF 16949:2016'])
        self.assertEqual(standards_from_title(None), [])

    def test_admin_jobs_show_all_standards_and_intake(self):
        match = self.client.post('/api/match', json={
            'companyName': '두규격제조', 'contactEmail': 'buyer-r2@example.com',
            'industry': 'Manufacturing', 'employees': '51-200', 'region': '경기',
            'standards': ['ISO 9001:2026', 'ISO 14001:2026'], 'issues': [], 'reasons': [],
            'certStatus': 'None', 'timeline': '3months', 'budget': 'unknown', 'additionalNotes': '',
        }).get_json()
        res = self.client.post('/api/quotes/request', headers=auth_headers(self.company), json={
            'consultant_ids': [self.consultant.id], 'analysis_context': match, 'session_id': match['session_id']})
        self.assertEqual(res.status_code, 201, res.get_json())

        jobs = self.client.get('/api/admin/jobs', headers=auth_headers(self.admin)).get_json()
        job = next(j for j in jobs if j['company_name'] == '두규격제조')
        intake = job['intake_data']
        self.assertEqual(intake['standards'], ['ISO 9001:2026', 'ISO 14001:2026'])
        self.assertEqual(intake['industry'], '제조업')
        self.assertEqual(intake['employees'], '51~200명')
        self.assertEqual(intake['region'], '경기')
        self.assertEqual(intake['timeline'], '3개월 이내')
        self.assertEqual(job['related_projects'][0]['standards'], ['ISO 9001:2026', 'ISO 14001:2026'])


if __name__ == '__main__':
    unittest.main()
