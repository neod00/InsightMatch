"""알림 메일 공용 레이아웃 테스트.

모든 알림 메일이 email_layout.render_email 하나로 만들어지는지, 예전 메일의 문제
(이모지, 가운데 뜬 '→' 링크, 영어 코드값, '© 2025' 고정 연도)가 다시 생기지 않는지 본다.
실제 SMTP 는 부르지 않는다 — send_email 을 바꿔 끼워 본문만 받는다.
"""
import os
import re
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'api')))

from services import email_layout as layout  # noqa: E402
from services.email_service import EmailService  # noqa: E402

EMOJI_RE = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌚⌛⏩-⏺️]')
URL = 'https://example.com'


def _all_emails(svc):
    """메일 종류마다 한 번씩 보내 (이름, subject, html, text) 목록을 돌려준다."""
    sent = []

    def fake_send(to_email, subject, html_content, text_content=None, attachments=None):
        sent.append((subject, html_content, text_content))
        return {'success': True}

    calls = [
        ('approved', svc.send_consultant_review_result, dict(
            consultant_email='c@example.com', consultant_name='정전문', notification_type='consultant_approved')),
        ('rejected', svc.send_consultant_review_result, dict(
            consultant_email='c@example.com', consultant_name='정전문', notification_type='consultant_rejected',
            reason='자격증 확인 불가', register_url=f'{URL}/consultant_register.html')),
        ('revoked', svc.send_consultant_review_result, dict(
            consultant_email='c@example.com', consultant_name='정전문',
            notification_type='consultant_verification_revoked', reason='자격 만료')),
        ('restored', svc.send_consultant_review_result, dict(
            consultant_email='c@example.com', consultant_name='정전문', notification_type='consultant_restored')),
        ('quote_request', svc.send_quote_request_to_consultant, dict(
            consultant_email='c@example.com', consultant_name='정전문', company_name='한빛정밀',
            industry='Nuclear', standards=['ISO 9001:2026', 'ISO 45001:2018'],
            issues_summary='품질 불량', timeline='6months', budget='500-1000',
            additional_notes='잘 부탁드립니다', project_id=1, dashboard_url=f'{URL}/dashboard.html')),
        ('quote_confirmation', svc.send_quote_confirmation_to_company, dict(
            company_email='b@example.com', company_name='한빛정밀', consultant_names=['정전문', '김전문'],
            standards=['ISO 9001:2026'], dashboard_url=f'{URL}/dashboard.html')),
        ('proposal', svc.send_proposal_notification, dict(
            company_email='b@example.com', company_name='한빛정밀', consultant_name='정전문',
            project_title='ISO 9001 인증', proposal_price=8500000, proposal_duration='12주',
            dashboard_url=f'{URL}/dashboard.html')),
        ('password_reset', svc.send_password_reset_email, dict(
            to_email='u@example.com', user_name='김회원', reset_link=f'{URL}/reset-password.html?token=t')),
        ('digest', svc.send_notification_digest, dict(
            to_email='u@example.com', user_name='김회원',
            items=[{'title': '계약서 서명이 완료되었습니다', 'message': '양측 서명이 끝났습니다.', 'link': f'{URL}/d'}],
            total_count=2, dashboard_url=f'{URL}/dashboard.html')),
        ('reminder', svc.send_reminder_notice, dict(
            to_email='u@example.com', user_name='김회원', title='서명하지 않은 계약서가 있습니다',
            message='계약서 서명을 기다리고 있습니다.', action_url=f'{URL}/dashboard.html')),
        ('invite', svc.send_consultant_invite, dict(
            to_email='i@example.com', invite_name='홍길동', invite_url=f'{URL}/consultant_register.html?invite=t',
            expires_at_text='2026-11-20 00:00')),
        ('admin_alert', svc.send_admin_alert, dict(
            to_email='a@example.com', admin_name='운영자', subject_label='새 전문가 등록 신청',
            heading='새 전문가 등록 신청', summary='심사를 기다리고 있습니다.', rows=[('이름', '정전문')],
            action_url=f'{URL}/admin.html', cta_label='관리자 화면에서 심사하기 →')),
        ('error_digest', svc.send_error_digest, dict(
            to_email='a@example.com', admin_name='운영자', hours=24, error_count=1, warning_count=0,
            error_groups=[{'excType': 'ValueError', 'message': 'bad', 'path': '/api/x', 'count': 1}],
            warning_groups=[], admin_url=f'{URL}/admin.html')),
    ]
    out = []
    with patch.object(svc, 'send_email', side_effect=fake_send):
        for name, fn, kwargs in calls:
            fn(**kwargs)
            out.append((name,) + sent[-1])
    return out


class EmailLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = EmailService()
        cls.emails = _all_emails(cls.svc)

    def test_every_email_uses_shared_layout(self):
        self.assertEqual(len(self.emails), 13)
        for name, subject, body, text in self.emails:
            with self.subTest(name=name):
                self.assertTrue(body.startswith('<!DOCTYPE html>'))
                self.assertIn('class="im-card', body)            # 공용 카드
                self.assertIn('Insight<span', body)              # 워드마크 헤더
                self.assertIn('class="im-btn"', body)            # 버튼형 CTA
                self.assertIn('role="presentation"', body)       # 테이블 레이아웃
                self.assertIn('prefers-color-scheme: dark', body)
                self.assertIn('name="color-scheme"', body)
                # 레이아웃이 <style> 의 클래스(.container 등)에 기대지 않는다
                self.assertNotIn('class="container"', body)

    def test_no_emoji_arrows_or_fixed_year(self):
        year = datetime.now().year
        for name, subject, body, text in self.emails:
            with self.subTest(name=name):
                self.assertIsNone(EMOJI_RE.search(body), EMOJI_RE.findall(body))
                self.assertIsNone(EMOJI_RE.search(subject))
                self.assertNotIn('→', body)
                self.assertNotIn('© 2025', body)
                self.assertIn(f'&copy; {year} OpenBrain Limited', body)
                # 링크가 가운데 떠 있던 예전 구조
                self.assertNotIn('text-align: center;"', body)

    def test_quote_request_shows_korean_labels_not_codes(self):
        body = dict((n, b) for n, _, b, _ in self.emails)['quote_request']
        self.assertIn('원자력', body)
        self.assertIn('6개월 이내', body)
        self.assertIn('500만~1,000만 원', body)
        for code in ('Nuclear', '6months', '500-1000'):
            self.assertNotIn(f'>{code}<', body)
        # 항목·값 2열 표
        self.assertIn('>업종</td>', body)

    def test_quote_request_escapes_user_input_and_marks_urgent(self):
        sent = []
        with patch.object(self.svc, 'send_email', side_effect=lambda *a, **k: sent.append(a) or {'success': True}):
            self.svc.send_quote_request_to_consultant(
                consultant_email='c@example.com', consultant_name='<b>x</b>', company_name='<script>a()</script>',
                industry='<img src=x>', standards=[], issues_summary=None, timeline='1month', budget='unknown',
                additional_notes='"<i>메모</i>"', project_id=1)
        body = sent[0][2]
        self.assertNotIn('<script>', body)
        self.assertNotIn('<img src=x>', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertIn('일정이 급한 요청입니다', body)
        self.assertIn('미정', body)          # 예산 unknown → 미정
        self.assertNotIn('>주요 경영 이슈<', body)   # 비어 있는 선택 항목은 표에서 뺀다

    def test_company_emails_never_mention_fee_or_partner_contract(self):
        for name, subject, body, text in self.emails:
            if name not in ('quote_confirmation', 'proposal'):
                continue
            with self.subTest(name=name):
                for word in ('수수료', '협력계약', '협력 계약', 'partner-agreement'):
                    self.assertNotIn(word, body)
                    self.assertNotIn(word, text)

    def test_text_alternative_is_sent(self):
        for name, subject, body, text in self.emails:
            with self.subTest(name=name):
                self.assertTrue(text)
                self.assertNotIn("<p", text)
                self.assertIn('OpenBrain Limited', text)

    def test_support_email_is_configurable(self):
        with patch.dict(os.environ, {'SUPPORT_EMAIL': 'help@example.org'}):
            html_part, text = layout.render_email(subject='s', heading='h', body=[layout.paragraph('p')])
        self.assertIn('mailto:help@example.org', html_part)
        self.assertIn('help@example.org', text)

    def test_cta_label_trailing_arrow_is_removed(self):
        self.assertEqual(layout.clean_label('관리자 화면에서 보기 →'), '관리자 화면에서 보기')
        self.assertEqual(layout.clean_label('제안서 보기'), '제안서 보기')

    def test_index_reuses_shared_intake_labels(self):
        import index
        from services import intake_labels
        self.assertIs(index.INTAKE_LABELS, intake_labels.INTAKE_LABELS)
        self.assertEqual(index.intake_label('industry', 'Nuclear'), '원자력')
        self.assertEqual(index.localize_request_description('업종: Nuclear'), '업종: 원자력')


if __name__ == '__main__':
    unittest.main()
