"""AI 매뉴얼 생성기 T8 — 프론트(ai-manual.html) 정적 계약 테스트.

브라우저를 띄우지 않고 문자열 수준으로 서버 계약과의 정합성을 지킨다:
  - 규격 칩/edition 영역에 필요한 id 가 있다 (규격별 체크박스, edition 패널, IMS 안내, 일정 영역, 커버리지 패널)
  - 세션·내보내기 요청이 target_isos(배열)를 보내고, 옛 단일 select 를 더는 읽지 않는다
  - SSE named event `coverage` 를 addEventListener 로 받고, 서버 문자열은 escapeHtml 을 거쳐 삽입한다
  - 다운로드 파일명은 Content-Disposition(filename*) 우선, 서버 규칙(…_ISO_9001-2026_45001-2018_매뉴얼)과 동일한 폴백
  - 전환 일정 날짜가 코드에 박혀 있지 않다 (FR-75)
  - <script> 문법 (node 가 있으면 node --check)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
HTML_PATH = os.path.join(ROOT, 'ai-manual.html')


def _read_html():
    with open(HTML_PATH, encoding='utf-8') as fh:
        return fh.read()


def _main_script(html):
    scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
    return scripts[-1] if scripts else ''


class ManualFrontendContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read_html()
        cls.js = _main_script(cls.html)

    def test_required_ids_exist(self):
        for element_id in (
            'target-iso-group', 'iso-edition-panel', 'iso-ims-notice', 'iso-mixed-notice',
            'iso-edition-hint', 'iso-schedule-area', 'iso-empty-notice', 'coverage-panel',
            'stat-retry', 'iso-form', 'btn-generate', 'md-viewer', 'download-group', 'reason-group', 'issue-group',
        ):
            self.assertRegex(self.html, r'id="%s"' % re.escape(element_id), element_id)

    def test_three_standard_chips(self):
        for key in ('iso9001', 'iso14001', 'iso45001'):
            self.assertRegex(self.html, r'<input type="checkbox" value="%s"' % key)

    def test_legacy_single_select_is_gone(self):
        self.assertNotIn('inp-target-iso', self.html)

    def test_session_and_export_send_target_isos(self):
        self.assertRegex(self.js, r'target_isos:\s*targetIsos')
        # 세션·내보내기 본문에 옛 target_iso 키를 더는 보내지 않는다 (URL 프리셋 파싱은 제외)
        self.assertNotRegex(self.js, r'\n\s+target_iso:\s')
        self.assertRegex(self.js, r'manual_id:\s*currentManualId')

    def test_options_api_and_fallback(self):
        self.assertIn('/api/iso-manual/options', self.js)
        self.assertIn('ISO_FALLBACK_OPTIONS', self.js)
        # 옵션 실패는 토스트/알림이 아니라 로그
        m = re.search(r'async function loadIsoOptions\(\).*?\n        }\n', self.js, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn('alert(', m.group(0))
        self.assertIn('console.warn', m.group(0))

    def test_url_preset_formats(self):
        self.assertIn("params.get('target_isos')", self.js)
        self.assertIn("params.get('target_iso')", self.js)

    def test_coverage_named_event_and_escaping(self):
        self.assertIn("addEventListener('coverage'", self.js)
        body = re.search(r'function renderCoveragePanel\(\).*?\n        }\n', self.js, re.S)
        self.assertIsNotNone(body)
        body = body.group(0)
        # 서버 값(번호·제목·관련 조항·키워드)은 모두 escapeHtml 을 거쳐 innerHTML 에 들어간다
        self.assertRegex(body, r'itemLabel = \(phase, no, title\) => .*escapeHtml\(no.*escapeHtml\(title')
        self.assertIn('escapeHtml(m.refs.join', body)
        self.assertIn('escapeHtml(k.keywords.join', body)
        self.assertIn('report.error', self.js)  # error 필드가 있으면 표시하지 않는다

    def test_schedule_is_data_driven(self):
        # 전환 일정 날짜는 옵션 API 응답에서만 온다 — 소스에 2026-10-31 같은 날짜가 없어야 한다
        self.assertNotRegex(self.js, r'20\d\d-\d\d-\d\d')
        self.assertIn('schedule_labels', self.js)
        self.assertIn('다음 심사가 2015 기준이면 2015를 선택하세요', self.html)

    def test_server_strings_in_schedule_are_escaped(self):
        body = re.search(r'function renderIsoSchedule\(keys\).*?\n        }\n', self.js, re.S)
        self.assertIsNotNone(body)
        body = body.group(0)
        self.assertRegex(body, r'escapeHtml\(labels\[k\]\)')
        self.assertRegex(body, r'escapeHtml\(std\.schedule\[k\]\)')
        self.assertRegex(body, r'escapeHtml\(std\.display\)')

    def test_download_filename_follows_server_rule(self):
        self.assertIn("response.headers.get('Content-Disposition')", self.js)
        self.assertIn('filename\\*', self.js)
        self.assertIn('_매뉴얼.', self.js)
        self.assertNotIn("iso.replace(", self.js)  # 옛 클라이언트 파일명 조립 제거

    def test_phase_seam_is_double_newline(self):
        self.assertIn("rawMarkdown += '\\n\\n'", self.js)

    def test_resume_error_codes_have_messages(self):
        for code in ('RETRY_LIMIT_EXCEEDED', 'RETRY_WINDOW_EXPIRED', 'GENERATION_IN_PROGRESS', 'MANUAL_ALREADY_COMPLETE'):
            self.assertIn(code, self.js)
        self.assertIn('retries_remaining', self.js)

    @unittest.skipUnless(shutil.which('node'), 'node 가 없으면 건너뜀')
    def test_script_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'ai-manual.js')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(self.js)
            result = subprocess.run(['node', '--check', path], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
