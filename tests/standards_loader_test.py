"""T2: 규격 조항 데이터 로더 테스트 (PRD FR-01~07, FR-70~75, FR-81~82, AC-20/22/26/28/30).

실제 `api/standards/*.json` 은 읽기만 한다. 잘못된 데이터가 필요한 테스트는 임시 디렉터리에
실제 파일을 복사한 뒤 그 사본을 망가뜨린다. 외부 호출·DB·서버를 쓰지 않는다.
"""
import copy
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

from services import standards_loader as sl  # noqa: E402

REAL_DIR = sl.STANDARDS_DIR
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

ALL_FILES = ['iso9001_2015.json', 'iso9001_2026.json', 'iso14001_2015.json',
             'iso14001_2026.json', 'iso45001_2018.json']
ALL_CODES = ['ISO 9001:2015', 'ISO 9001:2026', 'ISO 14001:2015', 'ISO 14001:2026', 'ISO 45001:2018']


def _read(name):
    with open(os.path.join(REAL_DIR, name), encoding='utf-8') as fh:
        return json.load(fh)


class TempStandards:
    """실제 JSON 6개의 사본을 담은 임시 디렉터리. `mutate(name, fn)` 으로 사본만 고친다."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='standards_test_')
        for name in ['hls_common.json'] + ALL_FILES:
            shutil.copy(os.path.join(REAL_DIR, name), os.path.join(self.dir, name))

    def path(self, name):
        return os.path.join(self.dir, name)

    def mutate(self, name, fn):
        with open(self.path(name), encoding='utf-8') as fh:
            data = json.load(fh)
        fn(data)
        with open(self.path(name), 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False)

    def write_text(self, name, text):
        with open(self.path(name), 'w', encoding='utf-8') as fh:
            fh.write(text)

    def loader(self, **kwargs):
        return sl.StandardsLoader(self.dir, **kwargs)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def clause(data, cid):
    return next(c for c in data['clauses'] if c['id'] == cid)


class _Base(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in [k for k in os.environ if k.startswith(sl.EDITION_FLAG_PREFIX)]:
            del os.environ[k]
        self.tmp = TempStandards()
        self.addCleanup(self.tmp.cleanup)
        sl.clear_cache()
        self.addCleanup(sl.clear_cache)

    def assertDataError(self, fn, *fragments, filename=None):
        with self.assertRaises(sl.StandardsDataError) as cm:
            fn()
        msg = str(cm.exception)
        for frag in fragments:
            self.assertIn(frag, msg)
        if filename:
            self.assertEqual(cm.exception.filename, filename)
            self.assertIn(filename, msg)
        return cm.exception


# ---------------------------------------------------------------------------
class RealDataTest(_Base):
    """실제 데이터 6개가 스키마 v1.2 검증을 통과한다 (AC-20, AC-30)."""

    def test_load_all_passes(self):
        catalog = sl.load_all()
        self.assertEqual(sorted(catalog.standards), sorted(sl.registry_pairs()))
        self.assertEqual(len(catalog.standards), 5)
        self.assertEqual(catalog.hls.schema_version, '1.2')

    def test_schema_version_and_title_status_everywhere(self):
        for (key, ed), data in sl.load_all().standards.items():
            self.assertEqual(data.schema_version, '1.2')
            for c in data.clauses:
                self.assertIn(c.title_ko_status, sl.TITLE_STATUSES, '%s %s' % (key, c.id))
                self.assertLessEqual(len(c.requirement_summary), 400)
            self.assertEqual(data.content_source, 'self_authored')

    def test_ac20_edition_files_match_allowed_list(self):
        files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(REAL_DIR, 'iso*.json')))
        self.assertEqual(files, sorted(ALL_FILES))
        self.assertEqual(sorted(sl.allowed_standard_codes()), sorted(ALL_CODES))
        for (key, ed), data in sl.load_all().standards.items():
            self.assertEqual(data.filename, '%s_%s.json' % (key, ed))
            self.assertEqual(data.edition, ed)
            self.assertIn(data.standard_code, ALL_CODES)
            self.assertEqual(data.standard_code, sl.standard_code_of(key, ed))

    def test_ac30_carried_from_resolves(self):
        cat = sl.load_all()
        carried_total = 0
        for (key, ed), data in cat.standards.items():
            for c in data.clauses:
                if c.verification == 'carried':
                    self.assertIsNotNone(c.carried_from, '%s %s' % (key, c.id))
                if c.carried_from:
                    carried_total += 1
                    src_key, rest = c.carried_from.split('_', 1)
                    src_ed, src_id = rest.split(':')
                    src = cat.standards[(src_key, src_ed)].clause(src_id)
                    self.assertEqual(src.merge_key, c.merge_key)
                    self.assertEqual(src.requirement_summary, c.requirement_summary)
        self.assertGreater(carried_total, 0)

    def test_terms_section_optional_field_is_read_not_rejected(self):
        d26 = sl.get_standard('iso9001', '2026')
        self.assertIsNotNone(d26.terms_section)
        self.assertIsNone(sl.get_standard('iso9001', '2015').terms_section)
        self.assertEqual(d26.warnings, ())     # 정의된 선택 필드는 경고도 아니다

    def test_returned_structures_are_read_only(self):
        d = sl.get_standard('iso14001', '2015')
        self.assertIsInstance(d.clauses, tuple)
        with self.assertRaises(FrozenInstanceError):
            d.edition = '2026'
        with self.assertRaises(FrozenInstanceError):
            d.clauses[0].title_ko = 'x'
        with self.assertRaises(TypeError):
            d.clauses[0].extra['x'] = 1
        c = d.clause('6.1.2')
        self.assertEqual(c.merge_key, 'planning.env_aspects')
        self.assertIs(d.clause_by_merge_key('planning.env_aspects'), c)
        self.assertEqual(d.procedure('EP01').process_category, d.procedures[8].process_category)
        self.assertIsInstance(c.documented_info[0], sl.DocumentedInfo)
        self.assertEqual({x.id for x in d.clauses_in_phase(1)} & {'7.1'}, set())
        with self.assertRaises(TypeError):
            sl.get_hls_common().ims_naming['x'] = 'y'

    def test_loads_regardless_of_cwd(self):
        old = os.getcwd()
        tmp = tempfile.mkdtemp()
        try:
            os.chdir(tmp)
            sl.clear_cache()
            self.assertEqual(len(sl.load_all().standards), 5)
        finally:
            os.chdir(old)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_standards_dir_is_relative_to_module(self):
        expected = os.path.normpath(os.path.join(os.path.dirname(sl.__file__), '..', 'standards'))
        self.assertEqual(sl.STANDARDS_DIR, expected)


# ---------------------------------------------------------------------------
class CacheTest(_Base):
    def test_parse_once_per_process(self):
        real_read = sl._read_json
        with mock.patch.object(sl, '_read_json', side_effect=real_read) as spy:
            a = sl.get_standard('iso9001', '2015')
            b = sl.get_standard('iso9001', '2015')
            sl.get_hls_common()
            sl.get_hls_common()
            self.assertIs(a, b)
            self.assertEqual(spy.call_count, 2)    # 9001:2015 1회 + hls 1회

    def test_lazy_loads_only_requested_edition(self):
        real_read = sl._read_json
        with mock.patch.object(sl, '_read_json', side_effect=real_read) as spy:
            sl.get_standard('iso45001', '2018')
            names = [c.args[1] for c in spy.call_args_list]
            self.assertEqual(names, ['iso45001_2018.json'])

    def test_clear_cache_reparses(self):
        a = sl.get_standard('iso9001', '2015')
        sl.clear_cache()
        b = sl.get_standard('iso9001', '2015')
        self.assertIsNot(a, b)
        self.assertEqual(a, b)

    def test_failure_is_not_cached(self):
        loader = self.tmp.loader()
        os.rename(self.tmp.path('iso9001_2015.json'), self.tmp.path('hidden.json'))
        self.assertDataError(lambda: loader.get_standard('iso9001', '2015'))
        os.rename(self.tmp.path('hidden.json'), self.tmp.path('iso9001_2015.json'))
        self.assertEqual(loader.get_standard('iso9001', '2015').edition, '2015')

    def test_loader_instances_are_isolated(self):
        loader = self.tmp.loader()
        a = loader.get_standard('iso9001', '2015')
        self.assertIsNot(a, sl.get_standard('iso9001', '2015'))


# ---------------------------------------------------------------------------
class FailureTest(_Base):
    """파일 없음·깨진 JSON·스키마 위반은 모두 명확한 예외 (FR-05, AC-7/20)."""

    def get(self, key='iso9001', ed='2015', **kw):
        return lambda: self.tmp.loader(**kw).get_standard(key, ed)

    def test_missing_file(self):
        os.remove(self.tmp.path('iso14001_2026.json'))
        self.assertDataError(self.get('iso14001', '2026'), '찾을 수 없', filename='iso14001_2026.json')

    def test_missing_hls(self):
        os.remove(self.tmp.path('hls_common.json'))
        self.assertDataError(lambda: self.tmp.loader().get_hls_common(), '찾을 수 없', filename='hls_common.json')

    def test_missing_file_fails_load_all(self):
        os.remove(self.tmp.path('iso45001_2018.json'))
        self.assertDataError(lambda: self.tmp.loader().load_all(), filename='iso45001_2018.json')

    def test_broken_json_reports_line(self):
        self.tmp.write_text('iso9001_2015.json', '{\n  "schema_version": "1.2",\n  oops\n}')
        e = self.assertDataError(self.get(), 'JSON 파싱', filename='iso9001_2015.json')
        self.assertIn('line 3', str(e))

    def test_empty_and_non_object_json(self):
        self.tmp.write_text('iso9001_2015.json', '')
        self.assertDataError(self.get(), 'JSON', filename='iso9001_2015.json')
        self.tmp.write_text('iso9001_2015.json', '[1, 2]')
        self.assertDataError(self.get(), '객체가 아닙니다', filename='iso9001_2015.json')

    def test_duplicate_json_key(self):
        self.tmp.write_text('iso9001_2015.json', '{"a": 1, "a": 2}')
        self.assertDataError(self.get(), '중복된 JSON 키', filename='iso9001_2015.json')

    def test_missing_required_field(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: d.pop('policy_noun'))
        self.assertDataError(self.get(), "'policy_noun'", filename='iso9001_2015.json')

    def test_missing_clause_field_names_clause_id(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '6.1.2').pop('merge_key'))
        self.assertDataError(self.get(), 'clauses[6.1.2]', 'merge_key', filename='iso9001_2015.json')

    def test_wrong_schema_version(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: d.update(schema_version='1.1'))
        self.assertDataError(self.get(), 'schema_version', "'1.2'")

    def test_content_source_must_be_self_authored(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: d.update(content_source='copied'))
        self.assertDataError(self.get(), 'content_source')

    def test_enum_violations(self):
        for field, bad in [('outline_role', 'hidden'), ('verification', 'maybe'),
                           ('title_ko_status', 'guess'), ('merge_key', 'Bad Key')]:
            sl.clear_cache()
            self.tmp = TempStandards()
            self.addCleanup(self.tmp.cleanup)
            self.tmp.mutate('iso14001_2015.json', lambda d, f=field, b=bad: clause(d, '6.1.2').update({f: b}))
            self.assertDataError(self.get('iso14001', '2015'), 'clauses[6.1.2]', field)

    def test_procedure_category_enum(self):
        self.tmp.mutate('iso45001_2018.json', lambda d: d['procedures'][0].update(process_category='XYZ'))
        self.assertDataError(self.get('iso45001', '2018'), 'process_category', 'procedures[CP00]')

    def test_mandatory_must_be_bool(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.1').update(mandatory='yes'))
        self.assertDataError(self.get(), 'clauses[4.1]', 'mandatory')

    def test_duplicate_clause_id(self):
        def mut(d):
            dup = copy.deepcopy(clause(d, '4.1'))
            dup['merge_key'] = 'context.issues_dup'
            d['clauses'].append(dup)
        self.tmp.mutate('iso9001_2015.json', mut)
        self.assertDataError(self.get(), 'clauses[4.1]', 'id 중복')

    def test_duplicate_merge_key(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.2').update(merge_key=clause(d, '4.1')['merge_key']))
        self.assertDataError(self.get(), 'clauses[4.2]', 'merge_key 중복')

    def test_duplicate_procedure_id(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: d['procedures'][1].update(id=d['procedures'][0]['id']))
        self.assertDataError(self.get(), '절차서 id 중복')

    def test_parent_level_phase_consistency(self):
        cases = [('parent', '5', 'parent 불일치'), ('level', 3, 'level 불일치'), ('phase', 2, 'phase 불일치')]
        for field, bad, text in cases:
            self.tmp = TempStandards()
            self.addCleanup(self.tmp.cleanup)
            self.tmp.mutate('iso9001_2015.json', lambda d, f=field, b=bad: clause(d, '4.1').update({f: b}))
            self.assertDataError(self.get(), 'clauses[4.1]', text)

    def test_parent_missing_in_file(self):
        def mut(d):
            d['clauses'] = [c for c in d['clauses'] if c['id'] != '6.1']
        self.tmp.mutate('iso9001_2015.json', mut)
        self.assertDataError(self.get(), "parent '6.1'")

    def test_missing_chapter(self):
        def mut(d):
            d['clauses'] = [c for c in d['clauses'] if c['id'] != '10' and not c['id'].startswith('10.')]
        self.tmp.mutate('iso9001_2015.json', mut)
        self.assertDataError(self.get(), '절(level 1) 누락')

    def test_linked_procedure_reference(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.1').update(linked_procedures=['ZZ99']))
        self.assertDataError(self.get(), 'clauses[4.1]', "'ZZ99'")

    def test_procedure_iso_clause_reference(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: d['procedures'][0].update(iso_clauses=['99.9']))
        self.assertDataError(self.get(), 'procedures[CP00]', "'99.9'")

    def test_edition_filename_mismatch(self):
        shutil.copy(self.tmp.path('iso9001_2015.json'), self.tmp.path('iso9001_2026.json'))
        e = self.assertDataError(self.get('iso9001', '2026'), 'edition 불일치', filename='iso9001_2026.json')
        self.assertIn('standard_code 불일치', str(e))

    def test_standard_key_mismatch(self):
        shutil.copy(self.tmp.path('iso14001_2015.json'), self.tmp.path('iso9001_2015.json'))
        self.assertDataError(self.get(), 'standard_key 불일치', filename='iso9001_2015.json')

    def test_standard_code_mismatch(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: d.update(standard_code='ISO 9001'))
        self.assertDataError(self.get(), 'standard_code 불일치')

    def test_summary_rules(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.1').update(requirement_summary='가' * 401))
        self.assertDataError(self.get(), 'clauses[4.1]', '401자')
        self.tmp = TempStandards()
        self.addCleanup(self.tmp.cleanup)
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.1').update(requirement_summary=''))
        self.assertDataError(self.get(), 'clauses[4.1]', '비어 있음')

    def test_toc_only_allows_empty_summary(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.1').update(requirement_summary='', verification='toc_only'))
        data = self.get()()
        self.assertEqual(data.clause('4.1').verification, 'toc_only')

    def test_coverage_keywords_required(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.1').update(coverage_keywords=[]))
        self.assertDataError(self.get(), 'clauses[4.1]', 'coverage_keywords')

    def test_documented_info_shape(self):
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.1').update(documented_info=[{'kind': 'keep', 'name': 'x'}]))
        self.assertDataError(self.get(), 'clauses[4.1]', 'documented_info')

    def test_carried_from_format(self):
        for bad in ('iso9001_2015', 'iso9001-2015:6.2', 'iso14001_2015:6.2', 'iso9001_2026:6.2', 'iso9001_1999:6.2'):
            self.tmp = TempStandards()
            self.addCleanup(self.tmp.cleanup)
            self.tmp.mutate('iso9001_2026.json', lambda d, b=bad: clause(d, '6.2').update(carried_from=b))
            self.assertDataError(self.get('iso9001', '2026'), 'clauses[6.2]', 'carried_from')

    def test_carried_without_carried_from(self):
        def mut(d):
            c = next(c for c in d['clauses'] if c['verification'] == 'carried')
            c.pop('carried_from')
        self.tmp.mutate('iso9001_2026.json', mut)
        self.assertDataError(self.get('iso9001', '2026'), 'carried 이면 carried_from')

    def test_carried_from_target_must_exist_and_match(self):
        def mut(d):
            c = next(c for c in d['clauses'] if c.get('carried_from'))
            c['requirement_summary'] += '!'
        self.tmp.mutate('iso9001_2026.json', mut)
        self.assertDataError(self.get('iso9001', '2026'), '글자 단위로 같지 않음', filename='iso9001_2026.json')
        self.tmp = TempStandards()
        self.addCleanup(self.tmp.cleanup)

        def mut2(d):
            c = next(c for c in d['clauses'] if c.get('carried_from'))
            c['carried_from'] = 'iso9001_2015:99.9'
        self.tmp.mutate('iso9001_2026.json', mut2)
        self.assertDataError(self.get('iso9001', '2026'), '99.9', '없음')

    def test_unexpected_json_file_fails_load_all(self):
        self.tmp.write_text('iso9001_2027.json', '{}')
        self.assertDataError(lambda: self.tmp.loader().load_all(), '레지스트리에 없는', filename='iso9001_2027.json')

    def test_unknown_field_policy(self):
        """정의 밖 필드: 기본은 거부하지 않고 경고+보존, strict 모드에서는 오류. 정의된 선택 필드는 경고 없음."""
        self.tmp.mutate('iso9001_2015.json', lambda d: clause(d, '4.1').update(brand_new_field=1))
        data = self.get()()
        self.assertTrue(any('brand_new_field' in w for w in data.warnings))
        self.assertEqual(data.clause('4.1').extra['brand_new_field'], 1)
        self.assertDataError(self.get(strict_unknown_fields=True), 'brand_new_field')

    def test_hls_validation(self):
        self.tmp.mutate('hls_common.json', lambda d: d['edition_info']['standards'].pop('iso45001'))
        self.assertDataError(lambda: self.tmp.loader().get_hls_common(), 'edition_info.standards', filename='hls_common.json')
        self.tmp = TempStandards()
        self.addCleanup(self.tmp.cleanup)
        self.tmp.mutate('hls_common.json', lambda d: d.update(schema_version='1.0'))
        self.assertDataError(lambda: self.tmp.loader().get_hls_common(), 'schema_version')
        self.tmp = TempStandards()
        self.addCleanup(self.tmp.cleanup)
        self.tmp.mutate('hls_common.json', lambda d: d['ims_subclause_order']['6.1'].append('no.such_key'))
        self.assertDataError(lambda: self.tmp.loader().get_hls_common(), 'no.such_key')

    def test_error_carries_all_issues(self):
        def mut(d):
            clause(d, '4.1').update(outline_role='x')
            clause(d, '4.2').update(verification='y')
        self.tmp.mutate('iso9001_2015.json', mut)
        e = self.assertDataError(self.get(), 'clauses[4.1]', 'clauses[4.2]')
        self.assertEqual(len(e.issues), 2)


# ---------------------------------------------------------------------------
class NormalizeTest(_Base):
    """입력 정규화 표 (FR-71~73, AC-21 중 로더 몫). 노출 게이트와 무관한 동작은 enforce_exposure=False."""

    def norm(self, items, **kw):
        kw.setdefault('enforce_exposure', False)
        return sl.normalize_target_isos(items, **kw)

    def codes(self, items, **kw):
        return [s.standard_code for s in self.norm(items, **kw)]

    def test_full_and_short_forms(self):
        table = [
            (['ISO 9001:2015'], ['ISO 9001:2015']),
            (['9001:2026'], ['ISO 9001:2026']),
            (['9001:2015'], ['ISO 9001:2015']),
            (['ISO 14001:2026'], ['ISO 14001:2026']),
            (['14001:2026'], ['ISO 14001:2026']),
            (['14001:2015'], ['ISO 14001:2015']),
            (['ISO 45001:2018'], ['ISO 45001:2018']),
            (['45001:2018'], ['ISO 45001:2018']),
        ]
        for items, expected in table:
            self.assertEqual(self.codes(items), expected, items)

    def test_case_and_whitespace(self):
        for raw in ['iso 9001:2015', 'ISO9001:2015', '  ISO 9001 : 2015  ', 'Iso  9001:2015', '\tiso 9001: 2015\n']:
            self.assertEqual(self.codes([raw]), ['ISO 9001:2015'], raw)
        self.assertEqual(self.codes([' 9001 : 2026 ']), ['ISO 9001:2026'])
        self.assertEqual(self.codes([' 45001 ']), ['ISO 45001:2018'])

    def test_edition_omitted_uses_configured_default(self):
        got = self.norm(['9001', '14001', '45001'])
        self.assertEqual([s.standard_code for s in got], ['ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018'])
        self.assertTrue(all(s.defaulted for s in got))
        self.assertFalse(self.norm(['9001:2015'])[0].defaulted)

    def test_explicit_edition_never_overridden_by_default(self):
        self.assertEqual(self.codes(['9001:2015', '14001']), ['ISO 9001:2015', 'ISO 14001:2026'])

    def test_sorted_by_standard_order(self):
        self.assertEqual(self.codes(['45001', '14001:2015', '9001:2026']),
                         ['ISO 9001:2026', 'ISO 14001:2015', 'ISO 45001:2018'])

    def test_edition_mixing_all_17_combinations(self):
        """FR-73: 5종 edition 으로 만들 수 있는 단일 5 + 2개 8 + 3개 4 = 17조합 모두 정규화된다."""
        by_std = {}
        for k, e in sl.registry_pairs():
            by_std.setdefault(k, []).append(sl.standard_code_of(k, e))
        from itertools import combinations, product
        total = 0
        for r in (1, 2, 3):
            for keys in combinations(by_std, r):
                for combo in product(*(by_std[k] for k in keys)):
                    total += 1
                    self.assertEqual(self.codes(list(combo)), list(combo))
        self.assertEqual(total, 17)

    def test_unsupported_edition_and_standard(self):
        cases = [(['ISO 45001:2026'], 'unsupported_edition'), (['45001:2015'], 'unsupported_edition'),
                 (['9001:1999'], 'unsupported_edition'), (['ISO 27001:2022'], 'unsupported_standard'),
                 (['12345'], 'unsupported_standard')]
        for items, code in cases:
            with self.assertRaises(ValueError) as cm:
                self.norm(items)
            self.assertIsInstance(cm.exception, sl.EditionSelectionError)
            self.assertEqual(cm.exception.code, code, items)
            self.assertEqual(cm.exception.field, 'target_isos')

    def test_invalid_formats(self):
        for bad in ['', ' ', 'ISO 9001', 'iso9001', 'ISO 9001:15', 'ISO 9001-2015', 'ISO 9001:2015:1',
                    'abc', '9001:', ':2015', 'ISO 9001:２０１５', '９００１', None, 9001, ['9001']]:
            with self.assertRaises(sl.EditionSelectionError, msg=repr(bad)) as cm:
                self.norm([bad])
            self.assertEqual(cm.exception.code, 'invalid_format', repr(bad))

    def test_duplicate_standard_rejected(self):
        for items in (['ISO 9001:2015', 'ISO 9001:2026'], ['9001', '9001'], ['9001:2015', '14001', '9001:2015'],
                      ['iso 14001:2015', '14001:2026']):
            with self.assertRaises(sl.EditionSelectionError) as cm:
                self.norm(items)
            self.assertEqual(cm.exception.code, 'duplicate_standard', items)

    def test_count_limits_and_container(self):
        with self.assertRaises(sl.EditionSelectionError) as cm:
            self.norm([])
        self.assertEqual(cm.exception.code, 'empty')
        with self.assertRaises(sl.EditionSelectionError) as cm:
            self.norm(['9001', '14001', '45001', '9001'])
        self.assertEqual(cm.exception.code, 'too_many')
        for bad in ('ISO 9001:2015', None, {'a': 1}, 5):
            with self.assertRaises(sl.EditionSelectionError) as cm:
                self.norm(bad)
            self.assertEqual(cm.exception.code, 'invalid_format')

    def test_legacy_target_iso_and_priority(self):
        got = sl.resolve_manual_standards(target_iso='ISO 9001:2015', enforce_exposure=False)
        self.assertEqual([s.standard_code for s in got], ['ISO 9001:2015'])
        got = sl.resolve_manual_standards(target_isos=['14001:2015'], target_iso='ISO 9001:2015', enforce_exposure=False)
        self.assertEqual([s.standard_code for s in got], ['ISO 14001:2015'])
        got = sl.resolve_manual_standards(target_iso='ISO 14001:2015')    # 게이트가 열려 있는 edition
        self.assertEqual(got[0].standard_code, 'ISO 14001:2015')
        with self.assertRaises(sl.EditionSelectionError):
            sl.resolve_manual_standards()
        with self.assertRaises(sl.EditionSelectionError):
            sl.resolve_manual_standards(target_iso=['ISO 9001:2015'])

    def test_format_target_iso(self):
        sel = self.norm(['45001', '9001:2026'])
        self.assertEqual(sl.format_target_iso(sel), 'ISO 9001:2026 + ISO 45001:2018')

    def test_normalizing_does_not_mutate_input(self):
        items = ['45001', '9001']
        self.norm(items)
        self.assertEqual(items, ['45001', '9001'])


# ---------------------------------------------------------------------------
class DefaultsAndGateTest(_Base):
    """기본 edition(FR-72)과 노출 게이트(FR-81, AC-26)."""

    def test_configured_defaults_are_user_decision(self):
        self.assertEqual({k: v['default_edition'] for k, v in sl.STANDARD_REGISTRY.items()},
                         {'iso9001': '2026', 'iso14001': '2026', 'iso45001': '2018'})
        for k in sl.STANDARD_REGISTRY:
            self.assertEqual(sl.get_default_edition(k, respect_gate=False),
                             sl.STANDARD_REGISTRY[k]['default_edition'])

    def test_default_2026_gate_closed_by_default(self):
        """공개 시점 미결정(Q8/Q14): 기본은 2015/2018 만 노출. 2026 기본값은 게이트가 열려야 켜진다."""
        for key in ('iso9001', 'iso14001'):
            self.assertFalse(sl.edition_exposure(key, '2026').allowed)
            self.assertTrue(sl.edition_exposure(key, '2015').allowed)
        self.assertTrue(sl.edition_exposure('iso45001', '2018').allowed)
        self.assertEqual(sl.get_default_edition('iso9001'), '2015')      # 게이트 닫힘 -> 노출 가능한 최신
        self.assertEqual(sl.get_default_edition('iso45001'), '2018')

    def test_gate_closed_rejects_2026_in_normalize(self):
        with self.assertRaises(sl.EditionSelectionError) as cm:
            sl.normalize_target_isos(['ISO 9001:2026'])
        self.assertEqual(cm.exception.code, 'edition_not_available')
        got = sl.normalize_target_isos(['9001', '45001'])               # 생략 -> 노출 가능한 edition
        self.assertEqual([s.standard_code for s in got], ['ISO 9001:2015', 'ISO 45001:2018'])
        # 게이트를 보지 않는 내부 사용
        got = sl.normalize_target_isos(['ISO 9001:2026'], enforce_exposure=False)
        self.assertEqual(got[0].standard_code, 'ISO 9001:2026')

    def test_env_flag_opens_2026(self):
        os.environ['ENABLE_EDITION_2026'] = 'true'
        d = sl.edition_exposure('iso9001', '2026')
        self.assertTrue(d.allowed and d.released and d.data_ready)
        self.assertEqual(sl.get_default_edition('iso9001'), '2026')
        self.assertEqual([s.standard_code for s in sl.normalize_target_isos(['9001', '14001', '45001'])],
                         ['ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018'])
        os.environ['ENABLE_EDITION_2026'] = '0'
        self.assertFalse(sl.edition_exposure('iso9001', '2026').allowed)

    def test_flags_argument_overrides_env(self):
        self.assertTrue(sl.edition_exposure('iso14001', '2026', flags={'2026'}).allowed)
        os.environ['ENABLE_EDITION_2026'] = '1'
        self.assertFalse(sl.edition_exposure('iso14001', '2026', flags=set()).allowed)

    def test_unsigned_edition_closed_even_if_flag_on(self):
        for field in ('reviewed_by', 'reviewed_at'):
            for blank in ('', '   '):
                tmp = TempStandards()
                self.addCleanup(tmp.cleanup)
                tmp.mutate('iso9001_2026.json', lambda d, f=field, b=blank: d.update({f: b}))
                loader = tmp.loader()
                d = loader.edition_exposure('iso9001', '2026', flags={'2026'})
                self.assertFalse(d.allowed, (field, blank))
                self.assertTrue(d.released)
                self.assertFalse(d.data_ready)
                self.assertTrue(any('reviewed' in r for r in d.reasons))
                # 서명이 비어도 데이터 자체는 읽힌다(내부·테스트용)
                self.assertEqual(getattr(loader.get_standard('iso9001', '2026'), field).strip(), '')

    def test_toc_only_mandatory_closes_gate(self):
        tmp = TempStandards()
        self.addCleanup(tmp.cleanup)

        def mut(d):
            c = clause(d, '6.3')
            c.update(verification='toc_only', requirement_summary='')
            c.pop('carried_from', None)
        tmp.mutate('iso14001_2026.json', mut)
        loader = tmp.loader()
        data = loader.get_standard('iso14001', '2026')
        gate = sl.evaluate_data_gate(data)
        self.assertFalse(gate.ready)
        self.assertEqual(gate.toc_only_mandatory, ('6.3',))
        d = loader.edition_exposure('iso14001', '2026', flags={'2026'})
        self.assertFalse(d.allowed)
        self.assertIn('toc_only', ' '.join(d.reasons))
        with self.assertRaises(sl.EditionSelectionError) as cm:
            loader.normalize_target_isos(['ISO 14001:2026'], flags={'2026'})
        self.assertEqual(cm.exception.code, 'edition_not_available')
        self.assertEqual(sl.find_unverified_clauses(data)['toc_only'], ('6.3',))

    def test_toc_only_non_mandatory_does_not_close_gate(self):
        tmp = TempStandards()
        self.addCleanup(tmp.cleanup)

        def mut(d):
            c = clause(d, '6.3')
            c.update(verification='toc_only', requirement_summary='', mandatory=False)
            c.pop('carried_from', None)
        tmp.mutate('iso14001_2026.json', mut)
        gate = sl.evaluate_data_gate(tmp.loader().get_standard('iso14001', '2026'))
        self.assertTrue(gate.ready)

    def test_signed_and_clean_passes_gate(self):
        for pair in sl.registry_pairs():
            gate = sl.evaluate_data_gate(sl.get_standard(*pair))
            self.assertTrue(gate.ready, pair)
            self.assertFalse(gate.unsigned)

    def test_exposure_surfaces_broken_data(self):
        tmp = TempStandards()
        self.addCleanup(tmp.cleanup)
        os.remove(tmp.path('iso9001_2026.json'))
        with self.assertRaises(sl.StandardsDataError):
            tmp.loader().edition_exposure('iso9001', '2026')

    def test_edition_options_for_ui(self):
        opts = {o['standard_key']: o for o in sl.get_edition_options()}
        self.assertEqual(list(opts), ['iso9001', 'iso14001', 'iso45001'])
        o9 = opts['iso9001']
        self.assertEqual(o9['default_edition'], '2015')               # 게이트 닫힘
        self.assertEqual(o9['configured_default_edition'], '2026')
        self.assertEqual([(e['edition'], e['exposed']) for e in o9['editions']], [('2015', True), ('2026', False)])
        self.assertEqual(o9['editions'][1]['published_at'], '2026-09')
        opened = {o['standard_key']: o for o in sl.get_edition_options(flags={'2026'})}
        self.assertEqual(opened['iso14001']['default_edition'], '2026')
        self.assertEqual(opened['iso14001']['schedule']['cb_prev_edition_application_stop'], '2026-10-31')
        json.dumps(opened)      # JSON 직렬화 가능

    def test_no_edition_specific_branches_in_loader_source(self):
        """FR-82: `if edition == ...` 같은 edition 하드코딩 분기가 없다."""
        with open(sl.__file__, encoding='utf-8') as fh:
            src = fh.read()
        import re
        self.assertIsNone(re.search(r"edition\s*(==|!=)\s*['\"]\d{4}['\"]", src))


# ---------------------------------------------------------------------------
class EditionInfoTest(_Base):
    """전환 일정 데이터 접근 (FR-75)."""

    def test_edition_info_returned(self):
        info = sl.get_edition_info()
        self.assertEqual(info['source'], 'KAB 전환지침 공고')
        self.assertEqual(set(info['schedule_labels']), set(sl.SCHEDULE_KEYS))
        s14 = info['standards']['iso14001']['schedule']
        self.assertEqual(s14['cb_prev_edition_application_stop'], '2026-10-31')
        self.assertEqual(s14['new_cert_new_edition_only_after'], '2027-10-31')
        self.assertIsNone(info['standards']['iso45001']['schedule'])
        self.assertEqual([e['edition'] for e in info['standards']['iso9001']['editions']], ['2015', '2026'])

    def test_edition_info_is_a_copy(self):
        info = sl.get_edition_info()
        info['standards']['iso14001']['schedule']['cb_prev_edition_application_stop'] = 'tampered'
        self.assertEqual(sl.get_edition_info()['standards']['iso14001']['schedule']
                         ['cb_prev_edition_application_stop'], '2026-10-31')
        json.dumps(info)

    def test_reflects_json_change_without_code_change(self):
        """일정이 바뀌면 JSON 갱신만으로 반영된다 (FR-75)."""
        tmp = TempStandards()
        self.addCleanup(tmp.cleanup)
        tmp.mutate('hls_common.json', lambda d: d['edition_info']['standards']['iso14001']['schedule']
                   .update(cb_prev_edition_application_stop='2026-12-31'))
        got = tmp.loader().get_edition_info()
        self.assertEqual(got['standards']['iso14001']['schedule']['cb_prev_edition_application_stop'], '2026-12-31')


# ---------------------------------------------------------------------------
class RepositoryHygieneTest(_Base):
    """AC-28(원문 비반입)과 .gitignore 영향."""

    def test_ac28_no_purchased_pdf_or_source_text_in_standards_dir(self):
        names = os.listdir(REAL_DIR)
        for n in names:
            self.assertTrue(n.lower().endswith(('.json', '.md')), '예상 밖 파일: %s' % n)
        self.assertFalse([n for n in names if n.lower().endswith(('.pdf', '.txt'))])
        for root, dirs, files in os.walk(REAL_DIR):
            self.assertNotIn('iso_standards', dirs)
            self.assertNotIn('data', dirs)

    def test_ac28_no_pdf_under_api_and_no_iso_standards_dir(self):
        api_dir = os.path.join(REPO_ROOT, 'api')
        for root, dirs, files in os.walk(api_dir):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.pytest_cache')]
            self.assertNotIn('iso_standards', dirs, root)
            for f in files:
                self.assertFalse(f.lower().endswith('.pdf'), os.path.join(root, f))

    def test_ac28_source_notes_declare_self_authored(self):
        for pair in sl.registry_pairs():
            note = sl.get_standard(*pair).source_note
            self.assertIn('자체 작성', note)
        self.assertIn('자체 작성', sl.get_hls_common().source_note)

    def test_standards_dir_not_gitignored(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('git 없음')
        probe = subprocess.run([git, 'rev-parse', '--is-inside-work-tree'], cwd=REPO_ROOT,
                               capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest('git 저장소 아님')
        for name in ['hls_common.json'] + ALL_FILES:
            rel = 'api/standards/' + name
            r = subprocess.run([git, 'check-ignore', '-q', '--no-index', rel], cwd=REPO_ROOT)
            self.assertEqual(r.returncode, 1, '%s 가 .gitignore 에 걸림' % rel)
        # 비교 대상: 같은 방식으로 `data/` 아래는 실제로 무시된다(검사 자체가 작동하는지 확인)
        r = subprocess.run([git, 'check-ignore', '-q', '--no-index', 'api/data/standards/x.json'], cwd=REPO_ROOT)
        self.assertEqual(r.returncode, 0)


if __name__ == '__main__':
    unittest.main()
