"""2026-11-04 E2E 점검(T12) '중간' 항목 회귀 테스트.

QA-05 서명 시작 후 단독 취소 금지·병행 계약 금지 / QA-06 상대방 연락처 / QA-07 잘못된 입력 400 /
QA-08·UI-09 요청 내용·한국어 라벨 / QA-10 정산 정보 / QA-11 알림 / QA-12 계약 전 메시지 /
QA-15 거절 기록 보존 / QA-17 이메일·링크 검증 / UI-08 빈 프로필 승인 차단.
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
from models import AnalysisJob, Consultant, Message, Milestone, Notification, ProfileChangeLog, Project, User  # noqa: E402


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256',
    )
    return {'Authorization': f'Bearer {token}'}


class MediumSeverityFixesTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()

        self.company = User(email='buyer@example.com', password_hash='x', role='company', name='김담당',
                            company_name='바이어제조', phone='010-1111-2222',
                            biz_reg_no='1234567891', biz_status='verified')
        self.consultant_user = User(email='expert@example.com', password_hash='x', role='consultant', name='전문가')
        self.admin = User(email='admin@example.com', password_hash='x', role='admin', name='관리자')
        db.session.add_all([self.company, self.consultant_user, self.admin])
        db.session.flush()
        self.consultant = Consultant(user_id=self.consultant_user.id, name='전문가', email='expert@example.com',
                                     phone='010-9999-8888', verified=True, status='verified',
                                     iso_experience='{"9001": true}', regions='서울',
                                     business_type='business', biz_reg_no='1234567891', biz_name='전문가컨설팅',
                                     biz_ceo_name='전문가', bank_name='국민은행', account_number='123456789012',
                                     account_holder='전문가', partner_agreement_version='1.1',
                                     partner_agreed_at=datetime.datetime(2026, 11, 1, 3, 0))
        db.session.add(self.consultant)
        db.session.flush()
        self.project = Project(company_id=self.company.id, consultant_id=self.consultant.id,
                               title='ISO 9001 인증 프로젝트', session_id='session-1', status='proposal_submitted',
                               proposal_price=8000000, proposal_duration='3개월')
        db.session.add(self.project)
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _notes(self, user, notif_type):
        return Notification.query.filter_by(user_id=user.id, type=notif_type).count()

    # ---------- QA-05 ----------
    def test_company_cannot_cancel_alone_after_signing_started(self):
        self.project.status = 'awaiting_signature'
        self.project.consultant_signed_at = datetime.datetime(2026, 11, 4)
        db.session.commit()
        res = self.client.post(f'/api/projects/{self.project.id}/cancel', headers=auth_headers(self.company),
                               json={'reason': '변심'})
        self.assertEqual(res.status_code, 409)
        res = self.client.post('/api/projects/groups/cancel', headers=auth_headers(self.company),
                               json={'session_id': 'session-1'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(Project.query.get(self.project.id).status, 'awaiting_signature')

    def test_cancel_before_any_signature_still_works(self):
        self.project.status = 'pending_contract'
        db.session.commit()
        res = self.client.post(f'/api/projects/{self.project.id}/cancel', headers=auth_headers(self.company), json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Project.query.get(self.project.id).status, 'cancelled_by_company')

    def test_second_parallel_contract_in_same_session_is_blocked(self):
        other = Project(company_id=self.company.id, consultant_id=self.consultant.id, title=self.project.title,
                        session_id='session-1', status='awaiting_signature')
        db.session.add(other)
        db.session.commit()
        res = self.client.post(f'/api/projects/{self.project.id}/contract/draft', headers=auth_headers(self.company),
                               json={})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()['code'], 'CONTRACT_ALREADY_IN_PROGRESS')

    # ---------- QA-15 ----------
    def test_cancel_does_not_overwrite_consultant_decline(self):
        self.project.status = 'declined_by_consultant'
        db.session.commit()
        res = self.client.post(f'/api/projects/{self.project.id}/cancel', headers=auth_headers(self.company), json={})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Project.query.get(self.project.id).status, 'declined_by_consultant')

    # ---------- QA-06 ----------
    def test_contact_info_returns_the_counterpart(self):
        self.project.status = 'contracted'
        db.session.commit()
        as_company = self.client.get(f'/api/projects/{self.project.id}/contact-info',
                                     headers=auth_headers(self.company)).get_json()
        self.assertEqual(as_company['role'], 'consultant')
        self.assertEqual(as_company['email'], 'expert@example.com')
        as_consultant = self.client.get(f'/api/projects/{self.project.id}/contact-info',
                                        headers=auth_headers(self.consultant_user)).get_json()
        self.assertEqual(as_consultant['role'], 'company')
        self.assertEqual(as_consultant['email'], 'buyer@example.com')
        self.assertEqual(as_consultant['companyName'], '바이어제조')

    # ---------- QA-12 ----------
    def test_messages_only_after_contract(self):
        res = self.client.post(f'/api/projects/{self.project.id}/messages', headers=auth_headers(self.company),
                               json={'content': '010-1234-5678 로 연락주세요'})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Message.query.count(), 0)

        self.project.status = 'contracted'
        db.session.commit()
        res = self.client.post(f'/api/projects/{self.project.id}/messages', headers=auth_headers(self.company),
                               json={'content': '착수 회의 일정 잡겠습니다'})
        self.assertEqual(res.status_code, 200)
        res = self.client.post(f'/api/projects/{self.project.id}/messages', headers=auth_headers(self.company),
                               json={'content': 123})
        self.assertEqual(res.status_code, 400)

    # ---------- QA-07 ----------
    def test_bad_bodies_return_400_not_500(self):
        null_json = {'Content-Type': 'application/json'}
        cases = [
            ('/api/auth/signup', {}, 'null'),
            ('/api/auth/signup', {}, '[1, 2]'),
            ('/api/quotes/request', auth_headers(self.company), 'null'),
            (f'/api/projects/{self.project.id}/submit-proposal', auth_headers(self.consultant_user), 'null'),
            (f'/api/projects/{self.project.id}/reject-schedule', auth_headers(self.company), '"x"'),
        ]
        for path, headers, body in cases:
            res = self.client.post(path, headers={**headers, **null_json}, data=body)
            self.assertEqual(res.status_code, 400, (path, body))

        res = self.client.post('/api/auth/signup', json={'email': 123, 'password': ['x'], 'name': 'a'})
        self.assertEqual(res.status_code, 400)

    def test_public_match_tolerates_wrong_types(self):
        for payload in ({'standards': 'ISO 9001:2026'}, {'issues': ['quality_defect']},
                        {'issues': 'x', 'reasons': 5}, {'standards': [1, None, 'ISO 9001:2026']}):
            res = self.client.post('/api/match', json=payload)
            self.assertEqual(res.status_code, 200, payload)

    def test_schedule_with_numeric_date_is_400(self):
        self.project.status = 'contracted'
        db.session.add(Milestone(project_id=self.project.id, title='진단·갭 분석'))
        db.session.commit()
        milestone = Milestone.query.filter_by(project_id=self.project.id).first()
        res = self.client.post(f'/api/projects/{self.project.id}/propose-schedule',
                               headers=auth_headers(self.consultant_user),
                               json={'schedule': [{'milestone_id': milestone.id, 'proposed_date': 20261201}]})
        self.assertEqual(res.status_code, 400)
        res = self.client.post(f'/api/projects/{self.project.id}/propose-schedule',
                               headers=auth_headers(self.consultant_user), json={'schedule': 'soon'})
        self.assertEqual(res.status_code, 400)

    # ---------- QA-08 · UI-09 ----------
    def test_quote_description_uses_survey_values_in_korean(self):
        db.session.add(AnalysisJob(id='session-new', company_name='바이어제조', status='completed',
                                   intake_data=json.dumps({'region': '경기', 'employees': '51-200',
                                                           'certStatus': 'Expired',
                                                           'additionalNotes': '내년 3월 심사 희망'})))
        db.session.commit()
        context = {'industry': 'Manufacturing', 'selected_standards': ['ISO 14001:2026'], 'cert_status': 'Expired',
                   'timeline': '3months', 'budget': 'unknown', 'reasons': ['customer_demand', 'export'],
                   'issues_summary': '품질 불량'}
        res = self.client.post('/api/quotes/request', headers=auth_headers(self.company),
                               json={'consultant_ids': [self.consultant.id], 'session_id': 'session-new',
                                     'analysis_context': context})
        self.assertEqual(res.status_code, 201, res.get_json())
        desc = Project.query.filter_by(title='ISO 14001:2026 인증 프로젝트').first().description
        for expected in ('업종: 제조업', '직원 수: 51~200명', '지역: 경기', '인증 상태: 만료됨 (갱신 필요)',
                         '희망 일정: 3개월 이내', '예산: 미정', '인증 사유: 고객사·거래처 요구, 해외 수출·바이어 요구',
                         '추가 요청: 내년 3월 심사 희망'):
            self.assertIn(expected, desc)
        for code in ('Manufacturing', 'unknown', 'customer_demand', 'Expired'):
            self.assertNotIn(code, desc)

    def test_match_response_carries_region_and_notes(self):
        res = self.client.post('/api/match', json={'region': '부산', 'employees': '11-50',
                                                   'additionalNotes': '빠른 착수'}).get_json()
        self.assertEqual(res['region'], '부산')
        self.assertEqual(res['employees'], '11-50')
        self.assertEqual(res['additional_notes'], '빠른 착수')

    def test_detail_returns_korean_labels(self):
        db.session.add(AnalysisJob(id='session-1', company_name='바이어제조', status='completed',
                                   intake_data=json.dumps({'industry': 'Manufacturing', 'employees': '51-200',
                                                           'certStatus': 'None', 'budget': '2000+',
                                                           'timeline': '1month', 'reasons': ['public_bid']})))
        db.session.commit()
        body = self.client.get(f'/api/projects/{self.project.id}/detail',
                               headers=auth_headers(self.consultant_user)).get_json()
        self.assertEqual(body['industry_label'], '제조업')
        self.assertEqual(body['employees_label'], '51~200명')
        self.assertEqual(body['cert_status_label'], '없음 (신규 인증)')
        self.assertEqual(body['budget_label'], '2,000만 원 이상')
        self.assertEqual(body['timeline_text'], '1개월 이내 (긴급)')
        self.assertEqual(body['reasons'], ['공공입찰·조달 대응'])

    # ---------- QA-11 ----------
    def test_signature_requests_and_deletion_and_milestone_notify_counterpart(self):
        res = self.client.post(f'/api/projects/{self.project.id}/contract/draft', headers=auth_headers(self.company),
                               json={})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._notes(self.consultant_user, 'signature_requested'), 1)

        preview = self.client.get(f'/api/projects/{self.project.id}/contract/preview',
                                  headers=auth_headers(self.company)).get_json()
        contract_hash = preview.get('contract_hash') or preview.get('contractHash')
        res = self.client.post(f'/api/projects/{self.project.id}/contract/sign', headers=auth_headers(self.company),
                               json={'contract_hash': contract_hash})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(self._notes(self.consultant_user, 'signature_requested'), 2)

        # 진행 단계 변경 → 상대(기업) 알림
        contracted = Project(company_id=self.company.id, consultant_id=self.consultant.id, title='진행 건',
                             status='in_progress')
        db.session.add(contracted)
        db.session.flush()
        db.session.add(Milestone(project_id=contracted.id, title='진단·갭 분석'))
        db.session.commit()
        milestone = Milestone.query.filter_by(project_id=contracted.id).first()
        res = self.client.post(f'/api/projects/{contracted.id}/milestones/{milestone.id}/status',
                               headers=auth_headers(self.consultant_user), json={'status': 'completed'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._notes(self.company, 'milestone_updated'), 1)

        # 기업이 대기 중 요청 삭제 → 컨설턴트 알림
        pending = Project(company_id=self.company.id, consultant_id=self.consultant.id, title='삭제될 요청',
                          status='proposal_pending')
        db.session.add(pending)
        db.session.commit()
        before = self._notes(self.consultant_user, 'request_cancelled')
        res = self.client.delete(f'/api/projects/{pending.id}', headers=auth_headers(self.company))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self._notes(self.consultant_user, 'request_cancelled'), before + 1)

    # ---------- QA-10 ----------
    def test_consultant_sees_masked_settlement_and_agreement_record(self):
        body = self.client.get('/api/consultants/me', headers=auth_headers(self.consultant_user)).get_json()
        settlement = body['settlement']
        self.assertEqual(settlement['accountMasked'], '********9012')
        self.assertEqual(settlement['partnerAgreementVersion'], '1.1')
        self.assertTrue(settlement['partnerAgreedAt'].endswith('+00:00'))
        self.assertNotIn('123456789012', json.dumps(body))

    def test_consultant_can_change_settlement_and_admin_is_told(self):
        res = self.client.put('/api/consultants/me/settlement', headers=auth_headers(self.consultant_user),
                              json={'business_type': 'individual', 'bank_name': '신한은행',
                                    'account_number': '110-222-333444', 'account_holder': '전문가'})
        self.assertEqual(res.status_code, 200, res.get_json())
        consultant = Consultant.query.get(self.consultant.id)
        self.assertEqual(consultant.bank_name, '신한은행')
        self.assertEqual(consultant.business_type, 'individual')
        self.assertEqual(self._notes(self.admin, 'settlement_changed'), 1)
        log = ProfileChangeLog.query.filter_by(consultant_id=self.consultant.id, field_name='settlement').first()
        self.assertIsNotNone(log)
        self.assertNotIn('333444', log.new_value)  # 이력에 계좌 전체를 남기지 않는다

        res = self.client.put('/api/consultants/me/settlement', headers=auth_headers(self.consultant_user),
                              json={'business_type': 'business', 'bank_name': '신한은행',
                                    'account_number': '1', 'account_holder': '전문가', 'biz_reg_no': '123'})
        self.assertEqual(res.status_code, 400)
        res = self.client.put('/api/consultants/me/settlement', headers=auth_headers(self.company), json={})
        self.assertEqual(res.status_code, 404)

    # ---------- QA-17 ----------
    def test_profile_email_and_links_are_validated(self):
        url = f'/api/consultants/{self.consultant.id}/profile'
        for payload in ({'email': 'not-an-email'}, {'introductionVideoUrl': 'javascript:alert(1)'}, {'phone': 123}):
            res = self.client.put(url, headers=auth_headers(self.consultant_user), json=payload)
            self.assertEqual(res.status_code, 400, payload)
        res = self.client.post(f'/api/consultants/{self.consultant.id}/portfolio',
                               headers=auth_headers(self.consultant_user),
                               json={'name': 'x', 'url': 'javascript:alert(document.cookie)'})
        self.assertEqual(res.status_code, 400)
        res = self.client.put(url, headers=auth_headers(self.consultant_user), json={'email': 'new@example.com'})
        self.assertEqual(res.status_code, 200)

    # ---------- UI-08 ----------
    def test_admin_cannot_approve_unsubmitted_signup_placeholder(self):
        placeholder_user = User(email='newbie@example.com', password_hash='x', role='consultant', name='신규')
        db.session.add(placeholder_user)
        db.session.flush()
        placeholder = Consultant(user_id=placeholder_user.id, name='신규', specialty='General', status='pending')
        db.session.add(placeholder)
        db.session.commit()
        checklist = {'checklist': {'identity_verified': True, 'iso_credentials_verified': True,
                                   'project_history_verified': True, 'contact_verified': True}}
        res = self.client.post(f'/api/admin/consultants/{placeholder.id}/approve',
                               headers=auth_headers(self.admin), json=checklist)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()['code'], 'PROFILE_NOT_SUBMITTED')

        rows = self.client.get('/api/consultants', headers=auth_headers(self.admin)).get_json()
        by_id = {r['id']: r for r in rows}
        self.assertFalse(by_id[placeholder.id]['profileSubmitted'])
        self.assertTrue(by_id[self.consultant.id]['profileSubmitted'])


if __name__ == '__main__':
    unittest.main()
