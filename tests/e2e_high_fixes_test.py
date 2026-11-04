"""2026-11-04 E2E 점검(T12) '높음' 항목 회귀 테스트.

QA-01 미승인 컨설턴트 견적 차단 / QA-02 역제안 수락·협의 중 계약 금지 /
QA-03 희망 금액 검증 / QA-04 완료 프로젝트 회귀 차단 / QA-09 상세 화면 타 요청 설문 노출 /
UI-02 응답 시각에 UTC 표시.
"""
import datetime
import json
import os
import sys
import unittest

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

# index 를 import 하기 전에 인메모리 DB 를 지정해야 한다(conftest.py 와 같은 이유).
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

from index import app, db  # noqa: E402
from models import AnalysisJob, Consultant, Milestone, Notification, Project, User, iso_utc  # noqa: E402


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256',
    )
    return {'Authorization': f'Bearer {token}'}


class HighSeverityFixesTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()

        self.company = User(email='buyer@example.com', password_hash='x', role='company',
                            name='김담당', company_name='바이어제조',
                            biz_reg_no='1234567891', biz_status='verified')
        self.consultant_user = User(email='expert@example.com', password_hash='x', role='consultant', name='전문가')
        self.pending_user = User(email='pending@example.com', password_hash='x', role='consultant', name='심사중')
        db.session.add_all([self.company, self.consultant_user, self.pending_user])
        db.session.flush()

        self.consultant = Consultant(user_id=self.consultant_user.id, name='전문가',
                                     verified=True, status='verified')
        self.pending = Consultant(user_id=self.pending_user.id, name='심사중',
                                  verified=False, status='pending')
        db.session.add_all([self.consultant, self.pending])
        db.session.flush()

        self.project = Project(company_id=self.company.id, consultant_id=self.consultant.id,
                               title='ISO 9001 인증 프로젝트', status='proposal_submitted',
                               proposal_price=8000000, proposal_duration='3개월')
        db.session.add(self.project)
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ---------- QA-01 ----------
    def test_quote_request_rejects_unapproved_consultant(self):
        res = self.client.post('/api/quotes/request', headers=auth_headers(self.company),
                               json={'consultant_ids': [self.pending.id], 'analysis_context': {}})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()['code'], 'CONSULTANT_NOT_AVAILABLE')
        self.assertEqual(Project.query.filter_by(consultant_id=self.pending.id).count(), 0)

        for status in ('rejected', 'revoked', 'withdrawn'):
            self.consultant.status = status
            db.session.commit()
            res = self.client.post('/api/quotes/request', headers=auth_headers(self.company),
                                   json={'consultant_ids': [self.consultant.id], 'analysis_context': {}})
            self.assertEqual(res.status_code, 400, status)

    def test_quote_request_to_approved_consultant_still_works(self):
        res = self.client.post('/api/quotes/request', headers=auth_headers(self.company),
                               json={'consultant_ids': [self.consultant.id],
                                     'analysis_context': {'selected_standards': ['ISO 14001:2026']}})
        self.assertEqual(res.status_code, 201, res.get_json())

    def test_add_consultant_rejects_unapproved_consultant(self):
        res = self.client.post('/api/projects/add-consultant', headers=auth_headers(self.company),
                               json={'consultant_id': self.pending.id, 'title': self.project.title})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Project.query.filter_by(consultant_id=self.pending.id).count(), 0)

    # ---------- QA-03 ----------
    def test_negotiation_price_is_validated(self):
        for bad in ('abc', -5, 0, 99999, 1000000001, '7,000,000원'):
            res = self.client.post(f'/api/projects/{self.project.id}/negotiate',
                                   headers=auth_headers(self.company), json={'requested_price': bad})
            self.assertEqual(res.status_code, 400, bad)
        self.assertEqual(Project.query.get(self.project.id).status, 'proposal_submitted')

        res = self.client.post(f'/api/projects/{self.project.id}/negotiate',
                               headers=auth_headers(self.company), json={'requested_price': '7000000'})
        self.assertEqual(res.status_code, 200)
        data = json.loads(Project.query.get(self.project.id).negotiation_data)
        self.assertEqual(data['requested_price'], 7000000)  # 숫자로 정규화돼 저장

    def test_null_body_on_negotiate_is_400_not_500(self):
        res = self.client.post(f'/api/projects/{self.project.id}/negotiate',
                               headers={**auth_headers(self.company), 'Content-Type': 'application/json'},
                               data='null')
        self.assertEqual(res.status_code, 400)

    def test_accepting_legacy_invalid_price_is_400_not_500(self):
        self.project.status = 'negotiating'
        self.project.negotiation_status = 'pending'
        self.project.negotiation_data = json.dumps({'requested_price': 'abc'})
        db.session.commit()
        res = self.client.post(f'/api/projects/{self.project.id}/negotiate/respond',
                               headers=auth_headers(self.consultant_user), json={'action': 'accept'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Project.query.get(self.project.id).proposal_price, 8000000)

    # ---------- QA-02 ----------
    def _counter_offer(self):
        self.assertEqual(self.client.post(
            f'/api/projects/{self.project.id}/negotiate', headers=auth_headers(self.company),
            json={'requested_price': 7000000}).status_code, 200)
        self.assertEqual(self.client.post(
            f'/api/projects/{self.project.id}/negotiate/respond', headers=auth_headers(self.consultant_user),
            json={'action': 'counter', 'counter_price': 7500000, 'counter_duration': '3개월'}).status_code, 200)

    def test_contract_cannot_be_drafted_while_negotiating(self):
        self._counter_offer()
        res = self.client.post(f'/api/projects/{self.project.id}/contract/draft', headers=auth_headers(self.company),
                               json={})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Project.query.get(self.project.id).status, 'negotiating')

    def test_consultant_cannot_respond_again_after_counter(self):
        self._counter_offer()
        res = self.client.post(f'/api/projects/{self.project.id}/negotiate/respond',
                               headers=auth_headers(self.consultant_user), json={'action': 'accept'})
        self.assertEqual(res.status_code, 400)

    def test_company_accepts_counter_and_contract_uses_counter_price(self):
        self._counter_offer()
        # 컨설턴트는 자기 역제안을 수락할 수 없다
        self.assertEqual(self.client.post(
            f'/api/projects/{self.project.id}/negotiate/accept-counter',
            headers=auth_headers(self.consultant_user)).status_code, 403)

        res = self.client.post(f'/api/projects/{self.project.id}/negotiate/accept-counter',
                               headers=auth_headers(self.company))
        self.assertEqual(res.status_code, 200, res.get_json())
        project = Project.query.get(self.project.id)
        self.assertEqual(project.status, 'proposal_submitted')
        self.assertEqual(project.proposal_price, 7500000)
        self.assertEqual(project.negotiation_status, 'counter_accepted')
        self.assertTrue(Notification.query.filter_by(user_id=self.consultant_user.id,
                                                     type='negotiation_response').count() >= 1)

        # 두 번 수락은 안 된다
        self.assertEqual(self.client.post(
            f'/api/projects/{self.project.id}/negotiate/accept-counter',
            headers=auth_headers(self.company)).status_code, 400)

        self.assertEqual(self.client.post(
            f'/api/projects/{self.project.id}/contract/draft', headers=auth_headers(self.company),
            json={}).status_code, 200)
        preview = self.client.get(f'/api/projects/{self.project.id}/contract/preview',
                                  headers=auth_headers(self.company))
        self.assertEqual(preview.status_code, 200)
        self.assertIn('7,500,000', json.dumps(preview.get_json(), ensure_ascii=False))

    def test_company_can_renegotiate_after_counter(self):
        self._counter_offer()
        res = self.client.post(f'/api/projects/{self.project.id}/negotiate',
                               headers=auth_headers(self.company), json={'requested_price': 7200000})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Project.query.get(self.project.id).negotiation_status, 'pending')

    # ---------- QA-04 ----------
    def test_completed_project_cannot_be_reopened_by_schedule_or_milestone(self):
        self.project.status = 'completed'
        self.project.schedule_status = 'proposed'
        db.session.add(Milestone(project_id=self.project.id, title='진단·갭 분석', status='completed'))
        db.session.commit()
        milestone = Milestone.query.filter_by(project_id=self.project.id).first()

        for path in ('confirm-schedule', 'reject-schedule'):
            res = self.client.post(f'/api/projects/{self.project.id}/{path}',
                                   headers=auth_headers(self.company), json={'reason': 'x'})
            self.assertEqual(res.status_code, 400, path)
        res = self.client.post(f'/api/projects/{self.project.id}/milestones/{milestone.id}/status',
                               headers=auth_headers(self.consultant_user), json={'status': 'pending'})
        self.assertEqual(res.status_code, 400)

        project = Project.query.get(self.project.id)
        self.assertEqual(project.status, 'completed')
        self.assertEqual(Milestone.query.get(milestone.id).status, 'completed')

    def test_schedule_confirm_still_starts_a_contracted_project(self):
        self.project.status = 'contracted'
        self.project.schedule_status = 'proposed'
        db.session.commit()
        res = self.client.post(f'/api/projects/{self.project.id}/confirm-schedule', headers=auth_headers(self.company))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Project.query.get(self.project.id).status, 'in_progress')

    # ---------- QA-09 ----------
    def test_detail_does_not_borrow_other_surveys_by_email_or_name(self):
        # 세션 없는 프로젝트. 같은 이메일·담당자 이름이 들어간 남의 공개 설문이 있어도 끌어오면 안 된다.
        db.session.add(AnalysisJob(id='someone-elses-session', company_name='남의회사', status='completed',
                                   intake_data=json.dumps({'contactEmail': 'buyer@example.com',
                                                           'companyName': '김담당',
                                                           'standards': ['ISO/IEC 27001:2022'],
                                                           'region': '부산'})))
        db.session.commit()
        res = self.client.get(f'/api/projects/{self.project.id}/detail', headers=auth_headers(self.consultant_user))
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body['standards'], [])
        self.assertIsNone(body['region'])

    def test_detail_uses_exact_session_survey(self):
        db.session.add(AnalysisJob(id='session-abc-123', company_name='바이어제조', status='completed',
                                   intake_data=json.dumps({'standards': ['ISO 9001:2026'], 'region': '경기'})))
        db.session.add(AnalysisJob(id='session-abc-999', company_name='다른', status='completed',
                                   intake_data=json.dumps({'standards': ['ISO 45001:2018'], 'region': '부산'})))
        self.project.session_id = 'session-abc-123'
        db.session.commit()
        body = self.client.get(f'/api/projects/{self.project.id}/detail',
                               headers=auth_headers(self.consultant_user)).get_json()
        self.assertEqual(body['standards'], ['ISO 9001:2026'])
        self.assertEqual(body['region'], '경기')

    # ---------- QA-13: session_id 를 그대로 믿지 않는다 ----------
    def test_quote_request_does_not_reuse_another_companys_session(self):
        other = User(email='rival@example.com', password_hash='x', role='company', name='경쟁사',
                     company_name='경쟁사', biz_reg_no='2208112341', biz_status='verified')
        db.session.add(other)
        db.session.flush()
        db.session.add(Project(company_id=other.id, consultant_id=self.consultant.id,
                               title='남의 요청', session_id='rival-session', status='proposal_pending'))
        db.session.commit()

        res = self.client.post('/api/quotes/request', headers=auth_headers(self.company),
                               json={'consultant_ids': [self.consultant.id], 'session_id': 'rival-session',
                                     'analysis_context': {'selected_standards': ['ISO 45001:2018']}})
        self.assertEqual(res.status_code, 201, res.get_json())
        mine = Project.query.filter_by(company_id=self.company.id, title='ISO 45001:2018 인증 프로젝트').first()
        self.assertIsNotNone(mine)
        self.assertNotEqual(mine.session_id, 'rival-session')

    def test_quote_request_keeps_own_session(self):
        res = self.client.post('/api/quotes/request', headers=auth_headers(self.company),
                               json={'consultant_ids': [self.consultant.id], 'session_id': 'my-fresh-session',
                                     'analysis_context': {'selected_standards': ['ISO 45001:2018']}})
        self.assertEqual(res.status_code, 201)
        mine = Project.query.filter_by(company_id=self.company.id, title='ISO 45001:2018 인증 프로젝트').first()
        self.assertEqual(mine.session_id, 'my-fresh-session')

    def test_add_consultant_requires_existing_own_request_group(self):
        extra_user = User(email='extra@example.com', password_hash='x', role='consultant', name='추가')
        db.session.add(extra_user)
        db.session.flush()
        extra = Consultant(user_id=extra_user.id, name='추가', verified=True, status='verified')
        db.session.add(extra)
        db.session.commit()

        res = self.client.post('/api/projects/add-consultant', headers=auth_headers(self.company),
                               json={'consultant_id': extra.id, 'title': '아무 제목'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Project.query.filter_by(consultant_id=extra.id).count(), 0)

        res = self.client.post('/api/projects/add-consultant', headers=auth_headers(self.company),
                               json={'consultant_id': extra.id, 'title': self.project.title})
        self.assertEqual(res.status_code, 201, res.get_json())

    # ---------- UI-02 ----------
    def test_iso_utc_marks_naive_values_as_utc(self):
        self.assertEqual(iso_utc(datetime.datetime(2026, 11, 3, 23, 47, 32)), '2026-11-03T23:47:32+00:00')
        aware = datetime.datetime(2026, 11, 3, 23, 47, tzinfo=datetime.timezone.utc)
        self.assertEqual(iso_utc(aware), '2026-11-03T23:47:00+00:00')
        self.assertEqual(iso_utc(datetime.date(2026, 11, 10)), '2026-11-10')
        self.assertIsNone(iso_utc(None))

    def test_api_timestamps_carry_utc_offset(self):
        db.session.add(Notification(user_id=self.company.id, type='t', title='t', message='m'))
        db.session.commit()
        notes = self.client.get('/api/notifications', headers=auth_headers(self.company)).get_json()
        self.assertTrue(notes['notifications'][0]['createdAt'].endswith('+00:00'))
        rows = self.client.get('/api/projects', headers=auth_headers(self.company)).get_json()
        self.assertTrue(rows[0]['created_at'].endswith('+00:00'))


if __name__ == '__main__':
    unittest.main()
