"""AI 매뉴얼 생성기 T5 — 세션 API의 edition 지원, 옵션 엔드포인트, 내보내기 표기.

OpenAI·실제 파일 변환은 호출하지 않는다 (내보내기 변환 함수는 mock).
"""
import datetime
import io
import os
import sys
import unittest
from unittest.mock import patch

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

# index 를 import 하기 전에 인메모리 DB 지정 (tests/conftest.py, manual_retry_test.py 와 같은 이유)
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

from index import app, db, ALLOWED_MANUAL_ISO_STANDARDS, validate_manual_form_data, build_export_filename  # noqa: E402
from models import ManualGeneration, User  # noqa: E402
from services import standards_loader as sl  # noqa: E402

GATE_ENV = 'ENABLE_EDITION_2026'


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256')
    return {'Authorization': f'Bearer {token}'}


BASE_FORM = {
    'company_name': '테스트기업',
    'industry': '금속 가공',
    'main_product': '자동차 부품',
    'employees': '50',
}


class EditionApiTestBase(unittest.TestCase):
    def setUp(self):
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        if hasattr(app, '_tables_created'):
            delattr(app, '_tables_created')
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        # 전체 받기(2·3단계)·다운로드는 사업자등록번호 확인 기업만(main #22) — 이 파일은 edition 동작만 본다
        self.company = User(email='c1@example.com', password_hash='x', role='company',
                            name='C1', company_name='Buyer Co',
                            biz_reg_no='2208112341', biz_status='verified')
        self.consultant = User(email='k@example.com', password_hash='x', role='consultant', name='K')
        self.admin = User(email='a@example.com', password_hash='x', role='admin', name='A')
        db.session.add_all([self.company, self.consultant, self.admin])
        db.session.commit()
        self.client = app.test_client()
        # 기본 환경: 2026 게이트 닫힘
        self.env = patch.dict(os.environ)
        self.env.start()
        os.environ.pop(GATE_ENV, None)

    def tearDown(self):
        self.env.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def open_gate(self):
        os.environ[GATE_ENV] = '1'

    def post_session(self, user=None, **extra):
        body = dict(BASE_FORM, **extra)
        return self.client.post('/api/iso-manual/session', json=body,
                                headers=auth_headers(user or self.admin))   # 관리자는 하루 한도 면제

    def stored_form(self, response):
        manual = ManualGeneration.query.get(response.get_json()['manual_id'])
        return manual.get_form_data()


class SessionBackwardCompatTest(EditionApiTestBase):
    def test_legacy_target_iso_strings(self):
        for iso in ('ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018'):
            r = self.post_session(target_iso=iso)
            self.assertEqual(201, r.status_code, iso)
            form = self.stored_form(r)
            self.assertEqual([iso], form['target_isos'])
            self.assertEqual(iso, form['target_iso'])

    def test_no_target_defaults_to_9001_2015(self):
        r = self.post_session()
        self.assertEqual(201, r.status_code)
        self.assertEqual(['ISO 9001:2015'], self.stored_form(r)['target_isos'])
        r = self.post_session(target_iso='')
        self.assertEqual(['ISO 9001:2015'], self.stored_form(r)['target_isos'])

    def test_legacy_unknown_value_rejected(self):
        r = self.post_session(target_iso='ISO 27001:2022')
        self.assertEqual(400, r.status_code)
        self.assertIn('target_isos', r.get_json()['errors'])
        self.assertEqual('unsupported_standard', r.get_json()['error_codes']['target_isos'])

    def test_allowed_constant_derived_from_loader(self):
        self.assertEqual(set(sl.allowed_standard_codes()), set(ALLOWED_MANUAL_ISO_STANDARDS))
        self.assertEqual(5, len(ALLOWED_MANUAL_ISO_STANDARDS))

    def test_target_isos_wins_over_target_iso(self):
        r = self.post_session(target_isos=['ISO 14001:2015'], target_iso='ISO 9001:2015')
        self.assertEqual(201, r.status_code)
        form = self.stored_form(r)
        self.assertEqual(['ISO 14001:2015'], form['target_isos'])
        self.assertEqual('ISO 14001:2015', form['target_iso'])


class SessionTargetIsosTest(EditionApiTestBase):
    def test_single_and_ims(self):
        cases = (
            (['ISO 9001:2015'], 'ISO 9001:2015'),
            (['ISO 14001:2015', 'ISO 9001:2015'], 'ISO 9001:2015 + ISO 14001:2015'),   # 9001->14001->45001 정렬
            (['ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018'],
             'ISO 9001:2015 + ISO 14001:2015 + ISO 45001:2018'),
        )
        for items, display in cases:
            r = self.post_session(target_isos=items)
            self.assertEqual(201, r.status_code, items)
            form = self.stored_form(r)
            self.assertEqual(display.split(' + '), form['target_isos'])
            self.assertEqual(display, form['target_iso'])

    def test_short_forms_store_resolved_editions(self):
        r = self.post_session(target_isos=['9001', '45001'])
        self.assertEqual(201, r.status_code)
        # 게이트가 닫힌 환경의 기본 edition 은 2015(공개된 판), 45001 은 2018
        self.assertEqual(['ISO 9001:2015', 'ISO 45001:2018'], self.stored_form(r)['target_isos'])

    def test_short_form_with_edition(self):
        r = self.post_session(target_isos=['9001:2015', '14001:2015'])
        self.assertEqual(201, r.status_code)
        self.assertEqual(['ISO 9001:2015', 'ISO 14001:2015'], self.stored_form(r)['target_isos'])

    def test_short_default_follows_gate(self):
        self.open_gate()
        r = self.post_session(target_isos=['9001', '14001', '45001'])
        self.assertEqual(201, r.status_code)
        self.assertEqual(['ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018'], self.stored_form(r)['target_isos'])

    def test_validation_errors_are_400_with_codes(self):
        cases = (
            ([], 'empty'),
            (['ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018', 'ISO 9001:2015'], 'too_many'),
            (['ISO 9001:2015', 'ISO 9001:2026'], 'duplicate_standard'),
            (['9001', 'ISO 9001:2015'], 'duplicate_standard'),
            (['ISO 45001:2026'], 'unsupported_edition'),
            (['ISO 9001:2019'], 'unsupported_edition'),
            (['ISO 27001:2022'], 'unsupported_standard'),
            (['ISO 9001'], 'invalid_format'),
            ([123], 'invalid_format'),
            ('ISO 9001:2015', 'invalid_format'),
        )
        for items, code in cases:
            r = self.post_session(target_isos=items)
            self.assertEqual(400, r.status_code, items)
            body = r.get_json()
            self.assertIn('target_isos', body['errors'], items)
            self.assertIsInstance(body['errors']['target_isos'], str)
            self.assertEqual(code, body['error_codes']['target_isos'], items)
            self.assertEqual('입력값을 확인해주세요.', body['message'])
        self.assertEqual(0, ManualGeneration.query.count())

    def test_other_field_errors_keep_existing_format(self):
        r = self.client.post('/api/iso-manual/session', json={'target_isos': ['ISO 9001:2015']},
                             headers=auth_headers(self.company))
        self.assertEqual(400, r.status_code)
        body = r.get_json()
        self.assertIn('company_name', body['errors'])
        self.assertNotIn('error_codes', body)

    def test_validate_function_returns_tuple(self):
        form, errors = validate_manual_form_data(dict(BASE_FORM, target_isos=['9001:2015']))
        self.assertIsNone(errors)
        self.assertEqual(['ISO 9001:2015'], form['target_isos'])
        form, errors = validate_manual_form_data(dict(BASE_FORM, target_isos=[]))
        self.assertIsNone(form)
        self.assertIn('target_isos', errors)


class EditionGateTest(EditionApiTestBase):
    def test_2026_rejected_when_gate_closed(self):
        for items in (['ISO 9001:2026'], ['ISO 14001:2026'], ['9001:2026'],
                      ['ISO 9001:2015', 'ISO 14001:2026']):
            r = self.post_session(target_isos=items)
            self.assertEqual(400, r.status_code, items)
            self.assertEqual('edition_not_available', r.get_json()['error_codes']['target_isos'], items)
        r = self.post_session(target_iso='ISO 9001:2026')
        self.assertEqual(400, r.status_code)
        self.assertEqual(0, ManualGeneration.query.count())

    def test_2015_and_2018_work_when_gate_closed(self):
        r = self.post_session(target_isos=['ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018'])
        self.assertEqual(201, r.status_code)

    def test_2026_allowed_when_gate_open(self):
        self.open_gate()
        r = self.post_session(target_isos=['ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018'])
        self.assertEqual(201, r.status_code)
        self.assertEqual(['ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018'], self.stored_form(r)['target_isos'])

    def test_mixed_editions_when_gate_open(self):
        self.open_gate()
        r = self.post_session(target_isos=['ISO 9001:2015', 'ISO 14001:2026'])
        self.assertEqual(201, r.status_code)
        self.assertEqual('ISO 9001:2015 + ISO 14001:2026', self.stored_form(r)['target_iso'])

    def test_still_blocked_45001_2026_when_gate_open(self):
        self.open_gate()
        r = self.post_session(target_isos=['ISO 45001:2026'])
        self.assertEqual(400, r.status_code)

    def test_stored_session_not_redefaulted_at_generation(self):
        """세션 저장 form_data 의 확정 edition 이 게이트 변화와 무관하게 유지된다 (T4 계약)."""
        r = self.post_session(target_isos=['9001'])
        form = self.stored_form(r)
        self.assertEqual(['ISO 9001:2015'], form['target_isos'])
        self.open_gate()
        self.assertEqual(['ISO 9001:2015'], form['target_isos'])


class OptionsEndpointTest(EditionApiTestBase):
    def get(self, user=None):
        headers = auth_headers(user) if user else {}
        return self.client.get('/api/iso-manual/options', headers=headers)

    def test_permissions(self):
        self.assertEqual(401, self.get().status_code)
        self.assertEqual(403, self.get(self.consultant).status_code)
        self.assertEqual(200, self.get(self.company).status_code)
        self.assertEqual(200, self.get(self.admin).status_code)

    def test_structure_gate_closed(self):
        body = self.get(self.company).get_json()
        by_key = {s['standard_key']: s for s in body['standards']}
        self.assertEqual(['iso9001', 'iso14001', 'iso45001'], [s['standard_key'] for s in body['standards']])
        self.assertEqual(3, body['max_standards'])
        q = by_key['iso9001']
        self.assertEqual('2015', q['default_edition'])
        self.assertEqual({'2015': True, '2026': False}, {e['edition']: e['exposed'] for e in q['editions']})
        self.assertEqual('ISO 9001:2026', q['editions'][1]['standard_code'])
        self.assertEqual({'2015': True, '2026': False}, {e['edition']: e['is_default'] for e in q['editions']})
        self.assertEqual('2026-10-31', by_key['iso14001']['schedule']['cb_prev_edition_application_stop'])
        self.assertEqual('2027-10-31', by_key['iso14001']['schedule']['new_cert_new_edition_only_after'])
        self.assertEqual(['2018'], [e['edition'] for e in by_key['iso45001']['editions']])
        self.assertIsNone(by_key['iso45001']['schedule'])
        self.assertIn('cb_prev_edition_application_stop', body['schedule_labels'])
        self.assertTrue(body['schedule_source'])

    def test_structure_gate_open(self):
        self.open_gate()
        body = self.get(self.company).get_json()
        by_key = {s['standard_key']: s for s in body['standards']}
        self.assertEqual('2026', by_key['iso9001']['default_edition'])
        self.assertEqual('2026', by_key['iso14001']['default_edition'])
        self.assertEqual('2018', by_key['iso45001']['default_edition'])
        self.assertTrue(all(e['exposed'] for s in body['standards'] for e in s['editions']))

    def test_no_internal_information(self):
        text = self.get(self.company).get_data(as_text=True)
        for word in ('.json', 'api/standards', 'api\\standards', 'reasons', 'data_ready', 'released',
                     'warning', 'Traceback', 'verification', 'toc_only', 'configured_default'):
            self.assertNotIn(word, text, word)
        self.assertNotIn(os.sep + 'api' + os.sep, text)

    def test_data_failure_is_generic_503(self):
        with patch('index.manual_standards.get_edition_options', side_effect=RuntimeError('C:\\secret\\path.json')):
            r = self.get(self.company)
        self.assertEqual(503, r.status_code)
        self.assertNotIn('secret', r.get_data(as_text=True))


class ExportFilenameHelperTest(unittest.TestCase):
    """파일명에 edition 포함 (사용자 결정): `{회사}_ISO_9001-2026_45001-2018_매뉴얼.{ext}`."""

    def test_single(self):
        self.assertEqual('테스트기업_ISO_9001-2026_매뉴얼.docx',
                         build_export_filename('테스트기업', ('ISO 9001:2026',), 'docx'))
        self.assertEqual('A사_ISO_45001-2018_매뉴얼.pdf', build_export_filename('A사', ['ISO 45001:2018'], 'pdf'))

    def test_ims_order_is_9001_14001_45001_regardless_of_input_order(self):
        codes = ('ISO 45001:2018', 'ISO 9001:2015', 'ISO 14001:2026')
        self.assertEqual('X_ISO_9001-2015_14001-2026_45001-2018_매뉴얼.pdf', build_export_filename('X', codes, 'pdf'))
        self.assertEqual('테스트기업_ISO_9001-2026_45001-2018_매뉴얼.pdf',
                         build_export_filename('테스트기업', ('ISO 9001:2026', 'ISO 45001:2018'), 'pdf'))

    def test_unsafe_characters_are_removed(self):
        name = build_export_filename(r'../a/b\c:*?"<>| 회사.', ('ISO 9001:2026',), 'pdf')
        self.assertEqual('abc 회사_ISO_9001-2026_매뉴얼.pdf', name)
        self.assertNotRegex(name, r'[/\:*?"<>|]')
        self.assertTrue(build_export_filename('', ('ISO 9001:2026',), 'docx').startswith('ISO매뉴얼_ISO_9001-2026'))
        self.assertTrue(build_export_filename(None, (), 'docx').startswith('ISO매뉴얼_ISO_매뉴얼'))
        self.assertTrue(build_export_filename('A', (), 'p/d?f').endswith('.pdf'))

    def test_no_or_unparseable_standards(self):
        self.assertEqual('A_ISO_매뉴얼.docx', build_export_filename('A', (), 'docx'))
        self.assertEqual('A_ISO_매뉴얼.docx', build_export_filename('A', ('garbage', None), 'docx'))
        self.assertEqual('A_ISO_9001-2015_매뉴얼.docx',
                         build_export_filename('A', ('ISO 9001:2015', 'ISO 9001:2026'), 'docx'))   # 같은 규격 중복은 첫 값


class ExportEditionTest(EditionApiTestBase):
    def export(self, fmt='docx', user=None, **extra):
        calls = {}

        def fake(name):
            def _fn(markdown, company_name='', target_iso=''):
                calls[name] = (company_name, target_iso)
                return io.BytesIO(b'FILE')
            return _fn

        body = {'markdown': '# 매뉴얼\n본문', 'format': fmt, 'company_name': '테스트기업'}
        body.update(extra)
        with patch('services.document_export_service.markdown_to_docx', fake('docx')), \
                patch('services.document_export_service.markdown_to_pdf', fake('pdf')):
            r = self.client.post('/api/export-iso', json=body, headers=auth_headers(user or self.company))
        return r, calls

    @staticmethod
    def filename(response):
        cd = response.headers.get('Content-Disposition', '')
        from urllib.parse import unquote
        if "filename*=UTF-8''" in cd:
            return unquote(cd.split("filename*=UTF-8''", 1)[1].split(';')[0])
        return cd

    def test_legacy_target_iso(self):
        r, calls = self.export('docx', target_iso='ISO 9001:2015')
        self.assertEqual(200, r.status_code)
        self.assertEqual(('테스트기업', 'ISO 9001:2015'), calls['docx'])
        self.assertEqual('테스트기업_ISO_9001-2015_매뉴얼.docx', self.filename(r))

    def test_target_isos_ims_cover_and_filename(self):
        r, calls = self.export('pdf', target_isos=['ISO 9001:2015', 'ISO 45001:2018'])
        self.assertEqual(200, r.status_code)
        self.assertEqual('ISO 9001:2015 · ISO 45001:2018', calls['pdf'][1])
        self.assertEqual('테스트기업_ISO_9001-2015_45001-2018_매뉴얼.pdf', self.filename(r))

    def test_edition_shown_for_2026_without_gate(self):
        """이미 생성된 문서의 내보내기에는 노출 게이트를 다시 적용하지 않는다."""
        r, calls = self.export('docx', target_isos=['9001:2026', '45001'])
        self.assertEqual(200, r.status_code)
        self.assertEqual('ISO 9001:2026 · ISO 45001:2018', calls['docx'][1])
        self.assertEqual('테스트기업_ISO_9001-2026_45001-2018_매뉴얼.docx', self.filename(r))

    def test_target_isos_wins_over_target_iso(self):
        r, calls = self.export('docx', target_isos=['ISO 14001:2015'], target_iso='ISO 9001:2015')
        self.assertEqual('ISO 14001:2015', calls['docx'][1])

    def test_invalid_selection_is_400(self):
        r, _ = self.export('docx', target_isos=['ISO 9001:2015', 'ISO 9001:2026'])
        self.assertEqual(400, r.status_code)
        self.assertEqual('duplicate_standard', r.get_json()['error_codes']['target_isos'])
        r, _ = self.export('docx', target_iso='ISO 99999:2015')
        self.assertEqual(400, r.status_code)

    def test_uses_stored_session_when_request_has_no_standard(self):
        self.open_gate()
        created = self.post_session(target_isos=['ISO 9001:2026', 'ISO 14001:2026'])
        manual_id = created.get_json()['manual_id']
        # T11 F-04 이후: manual_id 가 있으면 요청 markdown 대신 저장본만 내보낸다 → 저장본을 먼저 둔다.
        manual = ManualGeneration.query.get(manual_id)
        manual.phase1_markdown = '# 매뉴얼\n저장본'
        db.session.commit()
        os.environ.pop(GATE_ENV, None)    # 게이트가 닫혀도 내보내기는 가능
        r, calls = self.export('docx', user=self.admin, manual_id=manual_id)
        self.assertEqual(200, r.status_code)
        self.assertEqual('ISO 9001:2026 · ISO 14001:2026', calls['docx'][1])
        self.assertEqual('테스트기업_ISO_9001-2026_14001-2026_매뉴얼.docx', self.filename(r))

    def test_no_standard_info_still_works(self):
        r, calls = self.export('docx')
        self.assertEqual(200, r.status_code)
        self.assertEqual('', calls['docx'][1])
        self.assertEqual('테스트기업_ISO_매뉴얼.docx', self.filename(r))

    def test_permissions_unchanged(self):
        r, _ = self.export('docx', user=self.consultant, target_iso='ISO 9001:2015')
        self.assertEqual(403, r.status_code)


if __name__ == '__main__':
    unittest.main()
