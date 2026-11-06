"""예비 주소·발신 주소 기본값 — 환경 변수가 빠져도 링크가 남의 도메인이나 없는 주소로 가지 않는다.

예전 기본값 www.insightmatch.com·noreply@insightmatch.com 은 남이 가진 도메인이었고,
사이트맵 기본값 insight-match.vercel.app 은 없는 주소였다.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'api'))
os.environ.setdefault('SQLALCHEMY_DATABASE_URI', 'sqlite:///:memory:')

import index  # noqa: E402
from services.email_layout import DEFAULT_SITE_URL, site_url  # noqa: E402
from services.email_service import EmailService  # noqa: E402


class FallbackSiteUrlTest(unittest.TestCase):
    def test_no_foreign_or_dead_domains_in_server_code(self):
        for dirpath, _, files in os.walk(os.path.join(ROOT, 'api')):
            for name in files:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding='utf-8') as fh:
                    text = fh.read()
                for bad in ('www.insightmatch.com', 'noreply@insightmatch.com'):
                    self.assertFalse(bad in text, f'{path} 에 {bad}')
                self.assertFalse('https://insight-match.vercel.app' in text, path)

    def test_defaults_point_to_live_site(self):
        self.assertEqual(DEFAULT_SITE_URL, 'https://insight-match-ashen.vercel.app')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('BASE_URL', None)
            self.assertEqual(site_url(), DEFAULT_SITE_URL)
            self.assertEqual(index._site_base_url(), DEFAULT_SITE_URL)
            # 요청 밖(배치)에서 불려도 운영 주소로
            self.assertEqual(index.frontend_base_url(), DEFAULT_SITE_URL)

    def test_sitemap_uses_default_when_base_url_missing(self):
        index.app.config['TESTING'] = True
        with index.app.app_context():
            index.db.create_all()
            try:
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop('BASE_URL', None)
                    body = index.app.test_client().get('/sitemap.xml').get_data(as_text=True)
            finally:
                index.db.session.remove()
                index.db.drop_all()
        self.assertIn(DEFAULT_SITE_URL, body)
        self.assertNotIn('insight-match.vercel.app', body)

    def test_sender_falls_back_to_smtp_account(self):
        env = {'SMTP_USER': 'sender@example.com', 'SMTP_PASSWORD': 'x'}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop('FROM_EMAIL', None)
            self.assertEqual(EmailService().from_email, 'sender@example.com')
        with mock.patch.dict(os.environ, dict(env, FROM_EMAIL='noreply@example.org'), clear=False):
            self.assertEqual(EmailService().from_email, 'noreply@example.org')


if __name__ == '__main__':
    unittest.main()
