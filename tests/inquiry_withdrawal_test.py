"""L1-C1: 문의 접수 + 회원 탈퇴 테스트.

여기서 지키려는 것은 두 가지다.

1) 문의 접수는 **무인증 공개 경로**다. 누구나 부를 수 있으므로 호출량 제한이
   실제로 걸리는지, 관리자 목록이 관리자에게만 열리는지 확인한다.

2) 회원 탈퇴는 **소프트 삭제**다. 행이 남아 있다는 뜻이므로,
   "탈퇴했는데 계속 쓸 수 있다" 는 사고가 나기 가장 쉬운 지점이다.
   기존 JWT 로 API 가 막히는지를 반드시 확인한다.
"""
import datetime
import os
import sys
import unittest

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

# index 를 import 하기 전에 인메모리 DB 를 지정해야 한다.
# (conftest.py 와 같은 이유 — 안 하면 drop_all() 이 개발용 insightmatch.db 를 지운다)
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

from werkzeug.security import generate_password_hash

from index import app, db
from models import Consultant, Inquiry, Message, Project, RateLimitEntry, User


def make_token(user):
    """로그인 응답과 같은 형태의 토큰. tv 를 포함해야 탈퇴 후 무효화를 검증할 수 있다."""
    payload = {
        'user_id': user.id,
        'role': user.role,
        'tv': user.token_version or 0,
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')


def auth_headers(user):
    return {'Authorization': f'Bearer {make_token(user)}'}


PASSWORD = 'test-password-1234'


class InquiryWithdrawalTestBase(unittest.TestCase):
    def setUp(self):
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        if hasattr(app, '_tables_created'):
            delattr(app, '_tables_created')

        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()

        self.company = User(
            email='company@example.com',
            password_hash=generate_password_hash(PASSWORD),
            role='company',
            name='Company User',
            company_name='Buyer Co',
            phone='010-1111-2222',
        )
        self.consultant_user = User(
            email='consultant@example.com',
            password_hash=generate_password_hash(PASSWORD),
            role='consultant',
            name='Consultant User',
        )
        self.admin_user = User(
            email='admin@example.com',
            password_hash=generate_password_hash(PASSWORD),
            role='admin',
            name='Admin User',
        )
        db.session.add_all([self.company, self.consultant_user, self.admin_user])
        db.session.flush()

        self.consultant = Consultant(
            user_id=self.consultant_user.id,
            name='ISO Expert',
            verified=True,
            status='verified',
            email='expert@example.com',
            phone='010-3333-4444',
            account_number='1234567890',
            account_holder='홍길동',
        )
        db.session.add(self.consultant)
        db.session.commit()

        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── 헬퍼 ────────────────────────────────────────────────
    def post_inquiry(self, headers=None, **overrides):
        payload = {
            'name': '문의자',
            'email': 'guest@example.com',
            'category': 'service',
            'subject': '견적 요청이 안 됩니다',
            'content': '견적 요청 버튼을 눌러도 아무 반응이 없습니다.',
        }
        payload.update(overrides)
        return self.client.post('/api/inquiries', json=payload, headers=headers or {})

    def add_project(self, status, title='ISO 9001 Project'):
        project = Project(
            company_id=self.company.id,
            consultant_id=self.consultant.id,
            title=title,
            status=status,
        )
        db.session.add(project)
        db.session.commit()
        return project


class TestInquiry(InquiryWithdrawalTestBase):
    """(a) 문의 무인증 접수 + rate limit / (b) 목록은 관리자 전용"""

    def test_inquiry_can_be_created_without_authentication(self):
        response = self.post_inquiry()

        self.assertEqual(response.status_code, 201)
        inquiry = Inquiry.query.one()
        self.assertIsNone(inquiry.user_id)          # 비로그인 접수
        self.assertEqual(inquiry.status, 'received')
        self.assertEqual(inquiry.email, 'guest@example.com')

    def test_inquiry_links_account_when_logged_in(self):
        # 로그인 상태면 폼에 무엇을 적었든 계정 이메일이 쓰인다(사칭 방지).
        response = self.post_inquiry(
            headers=auth_headers(self.company),
            email='someone-else@example.com',
        )

        self.assertEqual(response.status_code, 201)
        inquiry = Inquiry.query.one()
        self.assertEqual(inquiry.user_id, self.company.id)
        self.assertEqual(inquiry.email, 'company@example.com')

    def test_inquiry_rate_limit_blocks_flooding(self):
        # 3회/시간 상한. 4번째부터 429.
        for _ in range(3):
            self.assertEqual(self.post_inquiry().status_code, 201)

        blocked = self.post_inquiry()

        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.get_json().get('code'), 'RATE_LIMITED')
        self.assertEqual(Inquiry.query.count(), 3)

    def test_inquiry_rejects_invalid_input(self):
        self.assertEqual(self.post_inquiry(email='not-an-email').status_code, 400)
        self.assertEqual(self.post_inquiry(category='hacking').status_code, 400)
        self.assertEqual(self.post_inquiry(content='짧음').status_code, 400)
        self.assertEqual(Inquiry.query.count(), 0)

    def test_inquiry_list_requires_admin(self):
        self.post_inquiry()

        anonymous = self.client.get('/api/admin/inquiries')
        company = self.client.get('/api/admin/inquiries', headers=auth_headers(self.company))
        consultant = self.client.get('/api/admin/inquiries', headers=auth_headers(self.consultant_user))
        admin = self.client.get('/api/admin/inquiries', headers=auth_headers(self.admin_user))

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(company.status_code, 403)
        self.assertEqual(consultant.status_code, 403)
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(len(admin.get_json()['inquiries']), 1)

    def test_admin_can_update_inquiry_status_and_memo(self):
        self.post_inquiry()
        inquiry_id = Inquiry.query.one().id

        response = self.client.post(
            f'/api/admin/inquiries/{inquiry_id}',
            json={'status': 'done', 'memo': '전화로 안내 완료'},
            headers=auth_headers(self.admin_user),
        )

        self.assertEqual(response.status_code, 200)
        updated = Inquiry.query.get(inquiry_id)
        self.assertEqual(updated.status, 'done')
        self.assertEqual(updated.admin_memo, '전화로 안내 완료')

    def test_admin_inquiry_update_rejects_unknown_status(self):
        self.post_inquiry()
        inquiry_id = Inquiry.query.one().id

        response = self.client.post(
            f'/api/admin/inquiries/{inquiry_id}',
            json={'status': 'deleted'},
            headers=auth_headers(self.admin_user),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Inquiry.query.get(inquiry_id).status, 'received')

    def test_inquiry_notifies_admins(self):
        self.post_inquiry()

        from models import Notification
        notifications = Notification.query.filter_by(type='new_inquiry').all()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].user_id, self.admin_user.id)


class TestWithdrawal(InquiryWithdrawalTestBase):
    """(c) 탈퇴 후 JWT 차단 / (d) 진행 중 프로젝트면 거부 /
       (e) 같은 이메일 재가입 / (f) 프로젝트·메시지 행 보존"""

    def withdraw(self, user, password=PASSWORD, token_headers=None):
        return self.client.post(
            '/api/auth/withdraw',
            json={'password': password},
            headers=token_headers or auth_headers(user),
        )

    def test_withdrawal_requires_password(self):
        response = self.withdraw(self.company, password='wrong-password')

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(User.query.get(self.company.id).deleted_at)

    def test_withdrawal_anonymizes_personal_data(self):
        user_id = self.company.id

        response = self.withdraw(self.company)

        self.assertEqual(response.status_code, 200)
        user = User.query.get(user_id)
        self.assertIsNotNone(user.deleted_at)
        self.assertEqual(user.email, f'deleted_{user_id}@deleted.invalid')
        self.assertEqual(user.name, '탈퇴한 회원')
        self.assertIsNone(user.phone)
        # 회사명은 상대방 거래기록의 일부라 남긴다.
        self.assertEqual(user.company_name, 'Buyer Co')

    def test_existing_jwt_is_rejected_after_withdrawal(self):
        # 탈퇴 '전에' 발급된 토큰 — 이걸로 계속 API 가 열리면 탈퇴가 무의미하다.
        stale_headers = auth_headers(self.company)

        before = self.client.get('/api/notifications', headers=stale_headers)
        self.assertEqual(before.status_code, 200)

        self.assertEqual(self.withdraw(self.company, token_headers=stale_headers).status_code, 200)

        after = self.client.get('/api/notifications', headers=stale_headers)
        self.assertEqual(after.status_code, 401)

    def test_withdrawn_user_cannot_log_in(self):
        self.withdraw(self.company)

        response = self.client.post(
            '/api/auth/login',
            json={'email': 'company@example.com', 'password': PASSWORD},
        )

        self.assertEqual(response.status_code, 401)

    def test_withdrawal_blocked_by_active_project(self):
        self.add_project('in_progress')

        response = self.withdraw(self.company)

        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual(body['code'], 'WITHDRAWAL_BLOCKED')
        self.assertEqual(len(body['blockers']), 1)
        self.assertEqual(body['blockers'][0]['status'], 'in_progress')
        self.assertIsNone(User.query.get(self.company.id).deleted_at)

    def test_withdrawal_blocked_for_consultant_with_contracted_project(self):
        self.add_project('contracted')

        response = self.withdraw(self.consultant_user)

        self.assertEqual(response.status_code, 409)
        self.assertIsNone(User.query.get(self.consultant_user.id).deleted_at)

    def test_pre_contract_projects_are_closed_not_blocked(self):
        project = self.add_project('proposal_pending')

        response = self.withdraw(self.company)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Project.query.get(project.id).status, 'cancelled_by_company')

    def test_withdrawal_preserves_project_and_message_rows(self):
        # (f) FK 무결성: 하드 삭제였다면 여기서 행이 사라지거나 FK 가 깨진다.
        project = self.add_project('completed')
        message = Message(
            project_id=project.id,
            sender_id=self.company.id,
            content='진행 상황 문의드립니다.',
        )
        db.session.add(message)
        db.session.commit()
        project_id, message_id = project.id, message.id

        self.assertEqual(self.withdraw(self.company).status_code, 200)

        surviving_project = Project.query.get(project_id)
        surviving_message = Message.query.get(message_id)
        self.assertIsNotNone(surviving_project)
        self.assertIsNotNone(surviving_message)
        self.assertEqual(surviving_project.company_id, self.company.id)
        self.assertEqual(surviving_message.sender_id, self.company.id)
        self.assertEqual(surviving_message.content, '진행 상황 문의드립니다.')

    def test_same_email_can_sign_up_again_after_withdrawal(self):
        # (e) 정책: 이메일을 익명화하므로 재가입이 가능하다.
        self.assertEqual(self.withdraw(self.company).status_code, 200)

        response = self.client.post(
            '/api/auth/signup',
            json={
                'email': 'company@example.com',
                'password': 'another-password-1234',
                'name': '새 담당자',
                'company_name': 'Buyer Co',
                'role': 'company',
            },
        )

        self.assertEqual(response.status_code, 201)
        # 기존 행은 그대로 남아 있고, 새 계정이 별도로 생성된다.
        self.assertEqual(User.query.filter_by(email='company@example.com').count(), 1)
        self.assertEqual(User.query.count(), 4)

    def test_consultant_withdrawal_removes_from_matching_and_wipes_settlement(self):
        self.assertEqual(self.withdraw(self.consultant_user).status_code, 200)

        consultant = Consultant.query.get(self.consultant.id)
        self.assertFalse(consultant.verified)
        self.assertEqual(consultant.status, 'withdrawn')
        self.assertIsNone(consultant.account_number)
        self.assertIsNone(consultant.account_holder)
        self.assertIsNone(consultant.phone)

        # 공개 목록에서도 빠져야 한다 (verified / status='verified' 둘 다 꺼야 한다).
        public_list = self.client.get('/api/consultants').get_json()
        self.assertEqual(public_list, [])
        self.assertEqual(self.client.get(f'/api/consultants/{self.consultant.id}').status_code, 404)

    def test_admin_cannot_withdraw_from_ui(self):
        response = self.withdraw(self.admin_user)

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(User.query.get(self.admin_user.id).deleted_at)

    def test_password_reset_is_not_issued_for_withdrawn_account(self):
        self.withdraw(self.company)
        RateLimitEntry.query.delete()   # 탈퇴 요청과 무관하게 재설정 한도를 비운다
        db.session.commit()

        from models import PasswordResetToken
        before = PasswordResetToken.query.filter_by(used=False).count()

        response = self.client.post(
            '/api/auth/request-reset',
            json={'email': 'company@example.com'},
        )

        # 열거 방지를 위해 응답은 성공이지만 토큰이 새로 생겨서는 안 된다.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PasswordResetToken.query.filter_by(used=False).count(), before)

    def test_find_email_excludes_withdrawn_account(self):
        self.withdraw(self.company)

        response = self.client.post(
            '/api/auth/find-email',
            json={'name': 'Company User', 'phone': '010-1111-2222'},
        )

        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
