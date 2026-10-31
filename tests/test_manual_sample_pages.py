"""매뉴얼 작성 예시 페이지(iso-*-manual-sample.html)가 원본(tools/manual_samples)과 같고, 필수 안내를 담는지 확인한다.

예시는 사람이 쓴 것이라 'AI 생성 결과'로 오해되면 안 된다 — 안내 문구가 빠지지 않게 지킨다.
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'manual_samples'))

import build  # noqa: E402
from content import SAMPLES  # noqa: E402


class TestManualSamplePages(unittest.TestCase):
    def test_committed_pages_match_the_builder(self):
        for key in build.ORDER:
            path = os.path.join(ROOT, SAMPLES[key]['slug'] + '.html')
            with open(path, encoding='utf-8') as f:
                committed = f.read()
            self.assertEqual(committed, build.page(key),
                             f'{path} 이 원본과 다릅니다. python tools/manual_samples/build.py 로 다시 만드세요.')

    def test_pages_say_they_are_examples_not_ai_output(self):
        for key in build.ORDER:
            text = build.page(key)
            self.assertIn('AI 매뉴얼 생성기의 실제 결과물이 아닙니다', text)
            self.assertIn('(가상)', text)
            self.assertIn(SAMPLES[key]['standard'], text)
            self.assertNotIn('noindex', text)  # 검색 유입용 — 색인 허용
            self.assertIn('href="ai-manual.html"', text)

    def test_main_page_links_each_sample(self):
        with open(os.path.join(ROOT, 'index.html'), encoding='utf-8') as f:
            index = f.read()
        for key in build.ORDER:
            self.assertIn(f'href="{SAMPLES[key]["slug"]}.html"', index)


if __name__ == '__main__':
    unittest.main()
