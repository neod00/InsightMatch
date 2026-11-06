"""ISO 규격 판(연도) 표기 — 2026-11-06 ISO 공식 카탈로그 기준.

새 판이 나와 바꾼 규격:
  ISO 37001:2016 → 2025, ISO 30414:2018 → 2025, ISO/IEC 27701:2019 → 2025,
  ISO/IEC 27017:2015 → 2026, ISO/IEC 27018:2019 → 2025,
  ISO 14068-1:2023 → ISO 14068:2026 (번호에서 '-1'이 빠짐)
매칭은 연도·파트 번호를 떼고 비교하므로, 옛 판으로 등록한 컨설턴트도 새 판 요청과 맞물려야 한다.
"""
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'api'))

from services import matching_service as matching_module  # noqa: E402

USER_FACING = ['index.html', 'consultant_register.html', 'iso-catalog.js']
OUTDATED = ['ISO 37001:2016', 'ISO 30414:2018', 'ISO/IEC 27701:2019',
            'ISO/IEC 27017:2015', 'ISO/IEC 27018:2019', 'ISO 14068-1']
CURRENT = ['ISO 37001:2025', 'ISO 30414:2025', 'ISO/IEC 27701:2025',
           'ISO/IEC 27017:2026', 'ISO 14068:2026']


def _read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as fh:
        return fh.read()


class IsoEditionsTest(unittest.TestCase):
    def test_no_outdated_editions_on_screens(self):
        for name in USER_FACING:
            text = _read(name)
            for old in OUTDATED:
                self.assertNotIn(old, text, f'{name} 에 옛 판 {old} 가 남아 있다')

    def test_current_editions_offered(self):
        survey = _read('index.html')
        register = _read('consultant_register.html')
        catalog = _read('iso-catalog.js')
        for code in CURRENT:
            self.assertIn(f'value="{code}"', survey, f'설문에 {code} 선택지가 없다')
            self.assertIn(f"fullCode: '{code}'", register, f'등록 폼에 {code} 가 없다')
        self.assertIn('ISO/IEC 27018:2025', survey)  # 클라우드 보안 고민의 연관 규격
        for code in ['ISO 37001:2025', 'ISO 30414:2025', 'ISO/IEC 27701:2025', 'ISO 14068:2026']:
            self.assertIn(f"num: '{code}'", catalog, f'규격 소개에 {code} 가 없다')

    def test_catalog_text_matches_new_editions(self):
        catalog = _read('iso-catalog.js')
        # 27701:2025 는 단독 인증 규격, 30414:2025 는 가이드라인이 아니라 표준
        self.assertNotIn('ISO 27001 선행 취득', catalog)
        entry = re.search(r"\{ cat: 'hr', num: 'ISO 30414:2025'.*?\} \}", catalog).group(0)
        self.assertNotIn('가이드라인', entry)
        self.assertNotIn('11개 영역', entry)

    def test_old_registrations_still_match_new_editions(self):
        normalize = matching_module._normalize_iso
        pairs = [('ISO 37001:2016', 'ISO 37001:2025'), ('37001', 'ISO 37001:2025'),
                 ('ISO 30414:2018', 'ISO 30414:2025'), ('ISO/IEC 27701:2019', 'ISO/IEC 27701:2025'),
                 ('ISO/IEC 27017:2015', 'ISO/IEC 27017:2026'), ('ISO/IEC 27018:2019', 'ISO/IEC 27018:2025'),
                 ('ISO 14068-1:2023', 'ISO 14068:2026'), ('14068', 'ISO 14068:2026')]
        for old, new in pairs:
            self.assertEqual(normalize(old), normalize(new), (old, new))


if __name__ == '__main__':
    unittest.main()
