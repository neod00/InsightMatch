"""세션 만료 처리 — 프론트 정적 계약 + 공용 헬퍼(auth-session.js) 동작 테스트.

- 로그인 화면은 역할 힌트(?role=)를 정해진 값만 받아 해당 탭을 연다.
- 만료로 로그인에 보내는 페이지는 token·user 를 지우기 전에 역할을 읽어 넘긴다.
- 전문가 등록 폼은 401 이면 입력을 임시 저장하되 정산 정보·계약 동의는 저장하지 않는다.
- AI 매뉴얼은 생성 전 401 이면 입력을 저장하고 로그인으로, 생성 후 401 이면 화면을 지킨다(이동하지 않음).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as fh:
        return fh.read()


def _function_body(src, signature):
    """`function name(...) {` 부터 짝이 맞는 `}` 까지."""
    start = src.find(signature)
    if start < 0:
        return ''
    i = src.find('{', start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    return ''


SETTLEMENT_FIELDS = (
    'business_type', 'biz_reg_no', 'biz_name', 'biz_ceo_name',
    'bank_name', 'account_number', 'account_holder',
)


class LoginRoleHintTest(unittest.TestCase):
    def setUp(self):
        self.html = _read('login.html')

    def test_role_hint_is_whitelisted(self):
        self.assertIn("const ROLE_HINTS = ['company', 'consultant', 'admin'];", self.html)
        self.assertIn("ROLE_HINTS.includes(urlParams.get('role'))", self.html)

    def test_role_hint_reuses_existing_tab_functions(self):
        self.assertRegex(self.html, r"roleHint === 'consultant'[\s\S]{0,200}switchLoginTab\('consultant'\)")
        self.assertRegex(self.html, r"roleHint === 'admin'[\s\S]{0,200}showAdminLogin\(\)")


class RedirectPagesPassRoleTest(unittest.TestCase):
    def test_dashboard_reads_role_before_clearing(self):
        body = _function_body(_read('dashboard.html'), 'function redirectToLoginWithNotice(')
        self.assertTrue(body)
        self.assertLess(body.index('readRoleHint()'), body.index("localStorage.removeItem('user')"))
        self.assertIn('&role=${roleHint}', body)

    def test_admin_reads_role_before_clearing(self):
        body = _function_body(_read('admin.html'), 'function redirectToLoginWithNotice(')
        self.assertTrue(body)
        self.assertLess(body.index("localStorage.getItem('user')"), body.index("localStorage.removeItem('user')"))
        self.assertIn('&role=${roleHint}', body)


class ConsultantRegisterDraftTest(unittest.TestCase):
    def setUp(self):
        self.html = _read('consultant_register.html')

    def test_loads_helper(self):
        self.assertIn('<script src="auth-session.js?v=1"></script>', self.html)

    def test_draft_excludes_settlement_and_consent(self):
        lists = re.findall(r'const REGISTER_DRAFT_(?:INPUTS|TEXTAREAS|CHECK_GROUPS) = \[([^\]]*)\]', self.html)
        self.assertEqual(len(lists), 3)
        saved = ' '.join(lists)
        for name in SETTLEMENT_FIELDS + ('partner_agreement_agreed',):
            self.assertNotIn(f"'{name}'", saved, name)
        collect = _function_body(self.html, 'function collectRegisterDraft(')
        for name in SETTLEMENT_FIELDS:
            self.assertNotIn(name, collect, name)

    def test_upload_and_submit_handle_401(self):
        upload = _function_body(self.html, 'async function handlePhotoSelect(')
        self.assertRegex(upload, r'response\.status === 401\)[\s\S]{0,200}redirectForExpiredSession\(\{ photoPending: true \}\)')
        submit = self.html[self.html.index("addEventListener('submit'"):]
        self.assertRegex(submit, r'response\.status === 401\)[\s\S]{0,120}redirectForExpiredSession\(\)')
        self.assertIn('isTokenUsable(token)', submit)

    def test_load_time_expiry_check_before_form(self):
        body = _function_body(self.html, 'function checkLoginAndPrefill(')
        self.assertIn('isTokenUsable(token)', body)
        # 만료 확인 → 복원 → 거부 프리필 순서 (복원값이 프리필보다 우선)
        self.assertLess(body.index('isTokenUsable(token)'), body.index('restoreRegisterDraft(user)'))
        self.assertLess(body.index('restoreRegisterDraft(user)'), body.index('prefillRejectedProfile(token'))

    def test_restored_draft_wins_over_rejected_prefill(self):
        body = _function_body(self.html, 'async function prefillRejectedProfile(')
        self.assertRegex(body, r'if \(draftRestored\) \{[\s\S]*?\} else \{\s*applyProfileToForm\(profile\);')

    def test_restore_notice_mentions_settlement(self):
        body = _function_body(self.html, 'function showDraftRestoredNotice(')
        self.assertIn('정산 정보는', body)
        self.assertIn('다시 입력해주세요', body)

    def test_draft_saved_before_session_cleared(self):
        body = _function_body(self.html, 'function redirectForExpiredSession(')
        self.assertLess(body.index('saveRegisterDraft('), body.index('redirectToLogin('))


class AiManualExpiryTest(unittest.TestCase):
    def setUp(self):
        self.html = _read('ai-manual.html')

    def test_loads_helper(self):
        self.assertIn('<script src="auth-session.js?v=1"></script>', self.html)

    def test_session_create_401_saves_draft_and_redirects(self):
        body = _function_body(self.html, 'function redirectForExpiredManualSession(')
        self.assertLess(body.index("saveManualDraft('session_expired')"), body.index('redirectToLogin('))
        self.assertRegex(self.html, r'sessionResponse\.status === 401\) \{\s*redirectForExpiredManualSession\(\);')

    def test_after_generation_401_does_not_navigate(self):
        notice = _function_body(self.html, 'function showManualLoginExpired(')
        self.assertTrue(notice)
        self.assertNotIn('location.href', notice)
        self.assertNotIn('redirectToLogin', notice)
        export = _function_body(self.html, 'async function exportDocument(')
        self.assertIn("showManualLoginExpired('download')", export)
        self.assertNotIn('redirectToLogin', export)
        retry = _function_body(self.html, 'async function retryGeneration(')
        self.assertNotIn('redirectToLogin', retry)

    def test_restored_note_matches_reason(self):
        self.assertIn("restoredManualDraft.reason === 'session_expired'", self.html)


class BusinessNumberUnauthorizedTest(unittest.TestCase):
    def test_put_401_calls_callback(self):
        js = _read('business-number.js')
        self.assertRegex(js, r"res\.status === 401\)[\s\S]{0,200}opts\.onUnauthorized")

    def test_callers_pass_callback(self):
        for name in ('ai-manual.html', 'dashboard.html', 'consultant_profile.html', 'script.js'):
            self.assertIn('onUnauthorized', _read(name), name)


@unittest.skipUnless(shutil.which('node'), 'node 가 없으면 건너뜀')
class AuthSessionHelperBehaviorTest(unittest.TestCase):
    """auth-session.js 를 가짜 브라우저 객체로 실행해 동작을 확인한다."""

    HARNESS = r"""
const fs = require('fs');
const store = (init) => {
  const m = new Map(Object.entries(init || {}));
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
           removeItem: k => m.delete(k), _m: m };
};
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64').replace(/=+$/, '');
const now = Math.floor(Date.now() / 1000);
const results = {};
function run(user, path, search, opts) {
  const ls = store({ token: 'x', user: user ? JSON.stringify(user) : undefined });
  if (!user) ls.removeItem('user');
  const ss = store();
  const win = { location: { pathname: path, search: search || '', href: '' } };
  const ctx = { window: win, localStorage: ls, sessionStorage: ss, atob: s => Buffer.from(s, 'base64').toString('binary'),
                console, URLSearchParams };
  new Function(...Object.keys(ctx), fs.readFileSync(process.argv[2], 'utf8'))(...Object.values(ctx));
  const A = win.InsightAuth;
  const first = A.redirectToLogin(opts || {});
  const second = A.redirectToLogin(opts || {});
  return { href: win.location.href, notice: ss.getItem('auth_notice'), token: ls.getItem('token'),
           user: ls.getItem('user'), first, second, A };
}
let r = run({ id: 1, role: 'consultant' }, '/dashboard.html', '');
results.consultant = { href: r.href, notice: r.notice, token: r.token, user: r.user, first: r.first, second: r.second };
r = run({ id: 2, role: 'hacker' }, '/consultant_register.html', '?invite=a&b');
results.badRole = { href: r.href };
r = run(null, '/ai-manual.html', '', { role: 'company', message: 'M' });
results.roleOverride = { href: r.href, notice: r.notice };
let seen = null;
r = run({ id: 3, role: 'admin' }, '/admin.html', '', { beforeRedirect: (role) => { seen = role; } });
results.before = { seen, href: r.href };
const A = r.A;
results.tokens = {
  expired: A.isTokenUsable('h.' + b64({ exp: now - 10 }) + '.s'),
  valid: A.isTokenUsable('h.' + b64({ exp: now + 3600 }) + '.s'),
  noExp: A.isTokenUsable('h.' + b64({ user_id: 1 }) + '.s'),
  broken: A.isTokenUsable('garbage'),
};
results.loginUrl = A.loginUrl('a.html', 'evil');
console.log(JSON.stringify(results));
"""

    def test_helper_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = os.path.join(tmp, 'harness.js')
            with open(harness, 'w', encoding='utf-8') as fh:
                fh.write(self.HARNESS)
            proc = subprocess.run(['node', harness, os.path.join(ROOT, 'auth-session.js')],
                                  capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            res = json.loads(proc.stdout.strip().splitlines()[-1])

        c = res['consultant']
        self.assertEqual(c['href'], 'login.html?redirect=dashboard.html&role=consultant')
        self.assertEqual(c['notice'], '세션이 만료되었습니다. 다시 로그인해주세요.')
        self.assertIsNone(c['token'])
        self.assertIsNone(c['user'])
        self.assertTrue(c['first'])
        self.assertFalse(c['second'])  # 두 번째 호출은 무시 (중복 이동 방지)

        # 화이트리스트 밖 역할은 넘기지 않고, 쿼리 포함 경로는 인코딩된다
        self.assertEqual(res['badRole']['href'],
                         'login.html?redirect=consultant_register.html%3Finvite%3Da%26b')
        self.assertEqual(res['roleOverride']['href'], 'login.html?redirect=ai-manual.html&role=company')
        self.assertEqual(res['roleOverride']['notice'], 'M')
        # beforeRedirect 는 user 가 지워지기 전에, 역할과 함께 불린다
        self.assertEqual(res['before']['seen'], 'admin')
        self.assertEqual(res['before']['href'], 'login.html?redirect=admin.html&role=admin')

        self.assertEqual(res['tokens'], {'expired': False, 'valid': True, 'noExp': True, 'broken': False})
        self.assertEqual(res['loginUrl'], 'login.html?redirect=a.html')


if __name__ == '__main__':
    unittest.main()
