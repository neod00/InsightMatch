"""기본 협력계약 문서와 가입 화면 동의 전문이 같은 버전·같은 핵심 조항을 담는지 확인한다.

부칙 제3항(버전 관리): 새 버전은 별도 문서로 게시하고 종전 버전은 지우지 않고 보존한다.
"""
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', f.read()))


class TestPartnerAgreementPages(unittest.TestCase):
    def test_current_version_states_default_rate_and_orders_without_acceptance(self):
        current = read('partner-agreement.html')
        register = read('consultant_register.html')
        for text in (current, register):
            self.assertIn('기본 지급률은 대금의 80%', text)
            self.assertIn('제8조 (건별 발주)', text)
            self.assertIn('컨설턴트는 별도로 수락하지 않습니다', text)
            self.assertIn('기본보다 낮추는 경우에는 그 발주서에 대하여 컨설턴트의 동의를 받습니다', text)
        self.assertIn('버전 1.1', current)
        self.assertIn('기본 협력계약(버전 1.1)', register)

        from_index = open(os.path.join(ROOT, 'api', 'index.py'), encoding='utf-8').read()
        self.assertIn("PARTNER_AGREEMENT_VERSION = '1.1'", from_index)

    def test_former_version_is_preserved_unchanged_in_substance(self):
        former = read('partner-agreement-v1.0.html')
        self.assertIn('종전 버전(1.0)의 원문입니다', former)
        self.assertIn('구체적 요율은 건별 발주서에 명시합니다', former)  # 1.0 의 제3조 그대로
        self.assertNotIn('제8조 (건별 발주)', former)
        self.assertIn('partner-agreement-v1.0.html', open(os.path.join(ROOT, 'partner-agreement.html'), encoding='utf-8').read())


if __name__ == '__main__':
    unittest.main()
