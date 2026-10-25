"""T3: 매뉴얼 목차(outline) 병합 엔진 테스트 (PRD 7.3 R1~R11, 7.3.1, FR-30~36·73~76·80, AC-1~5·10·22~25·29·30).

골든 파일
- 위치: tests/golden/manual_outline/<규격_edition>__<규격_edition>.json
- 사람이 리뷰할 수 있게 키 정렬·들여쓰기(2칸) JSON 으로 저장한다. refs 는 'Q 6.1.2' 같은 짧은 표기다.
- 갱신 방법 (의도한 변경일 때만, diff 를 리뷰한 뒤 커밋):
      UPDATE_GOLDEN=1 python -m pytest tests/manual_outline_test.py -q
  (PowerShell: $env:UPDATE_GOLDEN='1'; python -m pytest tests/manual_outline_test.py -q; Remove-Item Env:UPDATE_GOLDEN)
  갱신 모드에서는 골든을 다시 쓰고 해당 비교 테스트는 통과로 처리한다.
- 2015/2018 조합 골든(단일 3 + IMS 4)은 AC-22 회귀 기준이다. 2026 데이터·hls 의 2026 전용 항목이 이 결과를
  바꾸지 않는다는 것은 `Ac22EditionIsolationTest` 가 따로 증명한다.

PRD 예시와 데이터가 다른 곳은 데이터를 따랐고, 해당 테스트에 주석으로 적었다.
외부 호출·DB·서버를 쓰지 않는다. 실제 `api/standards/*.json` 은 읽기만 한다.
"""
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

from services import manual_outline as mo  # noqa: E402
from services import standards_loader as sl  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), 'golden', 'manual_outline')
UPDATE_GOLDEN = os.environ.get('UPDATE_GOLDEN', '').strip().lower() in ('1', 'true', 'yes')

LOADER = sl.StandardsLoader()

# AC-10 기대값: 개편 전 `iso_manual_service.SYSTEM_PROMPT_FULL` 의 '### 본문 (ISO 조항 순서)' 목차를
# (번호, 제목)으로 옮긴 것 (T4 에서 고정 프롬프트를 지우면서 테스트 상수로 이동, 커밋 84e43bd 기준).
# 파싱 규칙: 절 줄 '**4. 조직 상황 (Context ...)**' 의 한글 제목, 조항 줄에서 ' — '/' → ' 앞 부분,
# 7.1 처럼 '/' 로 나열한 괄호 설명은 제목에서 뺐다. 9001:2015 단일 매뉴얼 목차는 이것과 같아야 한다.
LEGACY_9001_2015_TOC = (
    ('4', '조직 상황'), ('4.1', '조직과 조직 상황의 이해'), ('4.2', '이해관계자의 니즈와 기대 이해'),
    ('4.3', '경영시스템의 적용범위 결정'), ('4.4', '경영시스템과 그 프로세스'),
    ('5', '리더십'), ('5.1', '리더십과 의지표명'), ('5.2', '방침'), ('5.3', '조직의 역할, 책임 및 권한'),
    ('6', '기획'), ('6.1', '리스크와 기회를 다루는 조치'), ('6.2', '목표 및 달성 기획'), ('6.3', '변경의 기획'),
    ('7', '지원'), ('7.1', '자원'), ('7.2', '역량 (적격성)'), ('7.3', '인식'), ('7.4', '의사소통'),
    ('7.5', '문서화된 정보'),
    ('8', '운용'), ('8.1', '운용 기획 및 관리'), ('8.2', '제품 및 서비스 요구사항'),
    ('8.3', '제품 및 서비스의 설계와 개발'), ('8.4', '외부에서 제공되는 프로세스, 제품 및 서비스의 관리'),
    ('8.5', '생산 및 서비스 제공'), ('8.6', '제품 및 서비스의 불출(Release)'), ('8.7', '부적합 출력(Output)의 관리'),
    ('9', '성과 평가'), ('9.1', '모니터링, 측정, 분석 및 평가'), ('9.2', '내부심사'),
    ('9.3', '경영검토(Management Review)'),
    ('10', '개선'), ('10.1', '일반사항'), ('10.2', '부적합 및 시정조치'), ('10.3', '지속적 개선'),
)


def sel(*codes, loader=None):
    return (loader or LOADER).normalize_target_isos(list(codes), enforce_exposure=False)


def outline(*codes, loader=None):
    loader = loader or LOADER
    return mo.build_outline(sel(*codes, loader=loader), loader)


def all_combos():
    codes = sl.allowed_standard_codes()
    out = []
    for r in (1, 2, 3):
        for combo in itertools.combinations(codes, r):
            if len({c.split(':')[0] for c in combo}) == r:
                out.append(combo)
    return out


def tags(o, manual_no):
    s = o.section(manual_no)
    assert s is not None, manual_no
    return [r.label for r in s.tagged_refs]


def golden_name(codes):
    return '__'.join(c.replace('ISO ', 'iso').replace(':', '_') for c in codes) + '.json'


def golden_projection(o):
    """골든용 요약 사본. 리뷰하기 쉽게 refs 를 짧은 문자열로 줄인다."""
    def folded_label(r):
        return r.label if r.role == mo.ROLE_FOLD else '%s (%s)' % (r.label, r.role)
    m = o.meta
    return {
        'selections': [s.standard_code for s in o.selections],
        'meta': {'is_ims': m.is_ims, 'cover_standards': m.cover_standards, 'doc_title': m.doc_title,
                 'system_name': m.system_name, 'policy_name': m.policy_name,
                 'objective_name': m.objective_name, 'filename_numbers': m.filename_numbers},
        'sections': [{
            'manual_no': s.manual_no, 'level': s.level, 'parent': s.parent, 'title': s.title,
            'title_source': s.title_source, 'merge_key': s.merge_key, 'phase': s.phase,
            'mandatory': s.mandatory, 'related_tag': s.related_tag,
            'refs': [r.label for r in s.refs if r.role == mo.ROLE_HEADING],
            'absorbed': [r.label for r in s.refs if r.role == mo.ROLE_ABSORBED],
            'folded': [folded_label(r) for r in s.folded_refs],
            'procedures': list(s.procedures), 'coverage_keywords': list(s.coverage_keywords),
        } for s in o.sections],
        'procedures': [{
            'doc_no': p.doc_no, 'name': p.name, 'merge_key': p.merge_key,
            'process_category': p.process_category, 'department': p.department, 'input': p.input,
            'output': p.output, 'records': p.records, 'retention': p.retention, 'media': p.media,
            'iso_clauses': p.iso_clause_label, 'manual_sections': list(p.manual_sections),
            'sources': ['%s %s' % x for x in p.sources], 'replaces': list(p.replaces),
        } for p in o.procedures],
        'appendix_c': mo.render_appendix_c_markdown(o).split('\n'),
    }


GOLDEN_COMBOS = [
    # 단일 5종
    ('ISO 9001:2015',), ('ISO 9001:2026',), ('ISO 14001:2015',), ('ISO 14001:2026',), ('ISO 45001:2018',),
    # 2015/2018 IMS 4종 (AC-22 회귀 기준)
    ('ISO 9001:2015', 'ISO 14001:2015'), ('ISO 9001:2015', 'ISO 45001:2018'),
    ('ISO 14001:2015', 'ISO 45001:2018'), ('ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018'),
    # 2026 / 혼합 IMS
    ('ISO 9001:2026', 'ISO 14001:2026'), ('ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018'),
    ('ISO 9001:2015', 'ISO 14001:2026'), ('ISO 9001:2026', 'ISO 45001:2018'),
]


class GoldenTest(unittest.TestCase):
    def test_golden_files(self):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        for codes in GOLDEN_COMBOS:
            with self.subTest(codes=codes):
                actual = golden_projection(outline(*codes))
                text = json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
                path = os.path.join(GOLDEN_DIR, golden_name(codes))
                if UPDATE_GOLDEN:
                    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
                        fh.write(text)
                    continue
                self.assertTrue(os.path.exists(path),
                                '골든 없음: %s (UPDATE_GOLDEN=1 로 생성 후 리뷰)' % path)
                with open(path, encoding='utf-8') as fh:
                    expected = json.load(fh)
                self.assertEqual(expected, json.loads(text), '골든 불일치: %s' % golden_name(codes))

    def test_no_stray_golden_files(self):
        expected = {golden_name(c) for c in GOLDEN_COMBOS}
        present = {n for n in os.listdir(GOLDEN_DIR) if n.endswith('.json')} if os.path.isdir(GOLDEN_DIR) else set()
        self.assertEqual(set(), present - expected)


class PropertyTest(unittest.TestCase):
    """17조합 전수 (FR-73, FR-80 (c), AC-4·25)."""

    def test_combo_count(self):
        self.assertEqual(17, len(all_combos()))

    def test_properties_all_combos(self):
        for combo in all_combos():
            with self.subTest(combo=combo):
                self._check(combo)

    def _check(self, combo):
        o = outline(*combo)
        stds = [LOADER.get_standard(s.standard_key, s.edition) for s in o.selections]

        # 결정성: 같은 입력 → 같은 출력, 입력 순서 무관
        base = mo.outline_to_dict(o)
        self.assertEqual(base, mo.outline_to_dict(outline(*combo)))
        for perm in itertools.permutations(combo):
            self.assertEqual(base, mo.outline_to_dict(outline(*perm)))

        # 번호 중복 0, 부모가 앞에 있음, level 일관
        nos = [s.manual_no for s in o.sections]
        self.assertEqual(len(nos), len(set(nos)), '번호 중복')
        seen = set()
        for s in o.sections:
            if s.parent is not None:
                self.assertIn(s.parent, seen)
                self.assertEqual(o.section(s.parent).level + 1, s.level)
            else:
                self.assertEqual(1, s.level)
            seen.add(s.manual_no)
            self.assertEqual(s.level, s.manual_no.count('.') + 1)

        # phase 1~3, 장 기준(R9), 포함 조항의 phase 와 일치
        for s in o.sections:
            self.assertIn(s.phase, (1, 2, 3))
            self.assertEqual(sl.PHASE_BY_CHAPTER[s.manual_no.split('.')[0]], s.phase)
            for r in s.refs:
                std = LOADER.get_standard(r.standard_key, r.edition)
                self.assertEqual(std.clause(r.clause_id).phase, s.phase)

        # 모든 원 조항이 정확히 한 섹션에 (누락 0, 중복 0) → 필수 조항 누락 0
        placed = [(r.tag, r.clause_id) for s in o.sections for r in s.refs]
        self.assertEqual(len(placed), len(set(placed)), '한 조항이 두 섹션에 들어감')
        expected = {(std.tag, c.id) for std in stds for c in std.clauses}
        self.assertEqual(expected, set(placed))
        for std in stds:
            for c in std.clauses:
                if c.mandatory:
                    self.assertIsNotNone(o.section_of_clause(std.tag, c.id))

        # 같은 merge_key 의 heading 조항은 1섹션 (AC-4)
        key_sections = {}
        for s in o.sections:
            for r in s.refs:
                if r.role == mo.ROLE_HEADING:
                    key_sections.setdefault(r.merge_key, set()).add(s.manual_no)
                    self.assertEqual(s.merge_key, r.merge_key)
        for k, secs in key_sections.items():
            self.assertEqual(1, len(secs), '%s 가 여러 섹션: %s' % (k, secs))

        # 섹션마다 heading 조항이 있고 mandatory 는 포함 조항에서 파생.
        # 단, IMS 에서 제목으로 펼쳐진 섹션은 항상 mandatory(사용자 결정 2026-10-25)
        for s in o.sections:
            self.assertTrue([r for r in s.refs if r.role == mo.ROLE_HEADING])
            expected = any(r.mandatory for r in s.refs) or s.related_tag is not None  # related_tag 는 IMS 에만 있음
            self.assertEqual(expected, s.mandatory)
            self.assertTrue(s.coverage_keywords)

        # 태그: IMS 만, 형식 고정
        for s in o.sections:
            if o.is_ims:
                self.assertEqual('[관련 조항: %s]' % ' · '.join(r.label for r in s.tagged_refs), s.related_tag)
            else:
                self.assertIsNone(s.related_tag)
                self.assertEqual([r.clause_id for r in s.refs if r.role == mo.ROLE_HEADING], [s.manual_no])

        # 단일은 ims_* 규칙을 쓰지 않는다
        if not o.is_ims:
            roles = {r.role for s in o.sections for r in s.refs}
            self.assertTrue(roles <= {mo.ROLE_HEADING, mo.ROLE_FOLD})

        # 절차서: 섹션이 가리키는 id 가 목록에 있고, 원 절차서는 정확히 한 번 대응
        proc_ids = [p.id for p in o.procedures]
        self.assertEqual(len(proc_ids), len(set(proc_ids)))
        for s in o.sections:
            for pid in s.procedures:
                self.assertIn(pid, proc_ids)
        sources = [src for p in o.procedures for src in p.sources]
        self.assertEqual(len(sources), len(set(sources)))
        self.assertEqual({(std.tag, p.id) for std in stds for p in std.procedures}, set(sources))
        for p in o.procedures:
            self.assertIn(p.process_category, sl.PROCESS_CATEGORIES)
            for sec in p.manual_sections:
                self.assertIsNotNone(o.section(sec))

        # 부록 C: 섹션마다 1행, 모든 원 조항이 셀에 한 번씩
        ac = mo.build_appendix_c(o)
        self.assertEqual(o.meta.standard_codes, ac.columns)
        self.assertEqual([s.manual_no for s in o.sections], [r[0] for r in ac.rows])
        for col, std in enumerate(stds):
            ids = []
            for _, _, cells in ac.rows:
                if cells[col] != '—':
                    ids.extend(re.findall(r'[0-9]+(?:\.[0-9]+)*', cells[col]))
            self.assertEqual(sorted(c.id for c in std.clauses), sorted(ids))


class SingleOutlineTest(unittest.TestCase):
    """R1: 단일 규격 = 그 edition 의 조항번호."""

    def numbers(self, o):
        return [s.manual_no for s in o.sections]

    def test_ac10_9001_2015_matches_current_prompt_toc(self):
        """AC-10: 9001:2015 단일 outline 의 절·조항(번호·제목) = 개편 전 SYSTEM_PROMPT_FULL 목차."""
        self.assertEqual(35, len(LEGACY_9001_2015_TOC))     # 7개 절 + 28개 조항
        o = outline('ISO 9001:2015')
        self.assertEqual(list(LEGACY_9001_2015_TOC), [(s.manual_no, s.title) for s in o.sections])

    def test_9001_2015_all_subclauses_folded(self):
        o = outline('ISO 9001:2015')
        self.assertFalse([s for s in o.sections if s.level == 3])
        self.assertEqual(['Q 8.7.1', 'Q 8.7.2'], [r.label for r in o.section('8.7').folded_refs])

    def test_ac1_14001_2015(self):
        o = outline('ISO 14001:2015')
        for no, key in [('6.1.2', 'planning.env_aspects'), ('6.1.3', 'planning.compliance_obligations'),
                        ('6.1.4', 'planning.action_planning'), ('8.2', 'ops.emergency'),
                        ('9.1.2', 'perf.compliance_evaluation')]:
            self.assertEqual(key, o.section(no).merge_key)
        # PRD AC-1 은 "6.1.3 준수의무 / 6.1.4 조치의 기획" 이지만 데이터는 KS 목차 제목으로 교정됨
        # (REVIEW_NOTES I.4, A5). 데이터를 따른다.
        self.assertEqual('준수의무사항', o.section('6.1.3').title)
        self.assertEqual('조치 계획', o.section('6.1.4').title)
        self.assertEqual('환경측면', o.section('6.1.2').title)
        self.assertEqual('비상사태 대비 및 대응', o.section('8.2').title)
        for no in ('8.3', '8.4', '8.5', '8.6', '8.7'):
            self.assertIsNone(o.section(no))
        self.assertFalse([s for s in o.sections if '고객만족' in s.title])
        self.assertEqual('환경방침', o.meta.policy_name)

    def test_ac2_45001_2018(self):
        o = outline('ISO 45001:2018')
        for no in ('5.4', '6.1.2', '6.1.3', '6.1.4', '8.1.2', '8.1.3', '8.1.4', '8.2', '9.1.2', '10.2'):
            self.assertIsNotNone(o.section(no), no)
        self.assertIn('사건', o.section('10.2').title)

    def test_ac23_9001_2026(self):
        o = outline('ISO 9001:2026')
        for no, key in [('6.1.1', 'planning.risks_opportunities'), ('6.1.2', 'planning.action_planning'),
                        ('6.1.3', 'planning.opportunity_actions')]:
            self.assertEqual(key, o.section(no).merge_key)
            self.assertEqual('6.1', o.section(no).parent)
        for no in ('8.7.1', '8.7.2', '10.2.1', '10.2.2', '10.3'):
            self.assertIsNone(o.section(no))
        self.assertEqual(['Q 8.7.1', 'Q 8.7.2'], [r.label for r in o.section('8.7').folded_refs])
        self.assertEqual(['Q 10.2.1', 'Q 10.2.2'], [r.label for r in o.section('10.2').folded_refs])
        self.assertEqual('지속적 개선', o.section('10.1').title)
        self.assertEqual('improvement.continual', o.section('10.1').merge_key)
        self.assertEqual('부적합 및 시정조치', o.section('10.2').title)
        self.assertFalse([s for s in o.sections if s.title == '일반사항' and s.manual_no == '10.1'])
        mr = o.section('9.3')
        self.assertEqual(['perf.management_review.general', 'perf.management_review.inputs',
                          'perf.management_review.outputs'], [r.merge_key for r in mr.folded_refs])
        self.assertEqual('경영검토 결과', mr.folded_refs[2].title_ko)

    def test_ac24_14001_2026(self):
        o = outline('ISO 14001:2026')
        expect = [('6.1.1', '일반사항'), ('6.1.2', '환경측면'), ('6.1.3', '준수의무'), ('6.1.4', '리스크와 기회'),
                  ('6.1.5', '조치의 기획'), ('6.3', '변경의 기획'), ('10.1', '지속적 개선'),
                  ('10.2', '부적합 및 시정조치')]
        for no, title in expect:
            self.assertEqual(title, o.section(no).title, no)
        self.assertIsNotNone(o.section('6.2'))
        self.assertIsNone(o.section('10.3'))
        # 9.3.1~9.3.3 은 fold (7.3.1 대응 원칙 5) 이고 부록 C 에는 대응
        self.assertEqual(['E 9.3.1', 'E 9.3.2', 'E 9.3.3'], [r.label for r in o.section('9.3').folded_refs])
        self.assertIn('9.3 (9.3.1, 9.3.2, 9.3.3)', mo.render_appendix_c_markdown(o))
        self.assertFalse([s for s in o.sections if s.manual_no == '6.1.4' and s.title == '조치의 기획'])

    def test_ac30_14001_2026_6_1_1_single_section(self):
        o = outline('ISO 14001:2026')
        s = o.section('6.1.1')
        self.assertEqual('planning.risk_opportunity.general', s.merge_key)
        self.assertTrue(s.mandatory)


class ImsExampleTest(unittest.TestCase):
    """PRD 7.3 예시와의 일치."""

    def test_example_a_2015(self):
        o = outline('ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018')
        expect = [
            ('6.1.A', '리스크와 기회의 결정', ['Q 6.1.1', 'E 6.1.1', 'S 6.1.1']),
            ('6.1.B', '조치의 기획', ['Q 6.1.2', 'E 6.1.4', 'S 6.1.4']),
            ('6.1.C', '준수의무(법적 및 기타 요구사항)', ['E 6.1.3', 'S 6.1.3']),
            ('6.1.D', '환경측면', ['E 6.1.2']),
            ('6.1.E', '위험요인 파악 및 리스크·기회 평가', ['S 6.1.2']),
        ]
        self.assertEqual(expect, [(s.manual_no, s.title, tags(o, s.manual_no)) for s in o.children('6.1')])

    def test_example_a_9001_45001(self):
        o = outline('ISO 9001:2015', 'ISO 45001:2018')
        self.assertEqual([('6.1.A', ['Q 6.1.1', 'S 6.1.1']), ('6.1.B', ['Q 6.1.2', 'S 6.1.4']),
                          ('6.1.C', ['S 6.1.3']), ('6.1.D', ['S 6.1.2'])],
                         [(s.manual_no, tags(o, s.manual_no)) for s in o.children('6.1')])

    def test_example_b_2026(self):
        o = outline('ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018')
        expect = [
            ('6.1.A', '리스크와 기회의 결정', ['Q 6.1.1', 'E 6.1.4', 'S 6.1.1']),
            ('6.1.B', '조치의 기획', ['Q 6.1.2', 'E 6.1.5', 'S 6.1.4']),
            ('6.1.C', '기회에 대한 조치', ['Q 6.1.3']),
            ('6.1.D', '준수의무(법적 및 기타 요구사항)', ['E 6.1.3', 'S 6.1.3']),
            ('6.1.E', '환경측면', ['E 6.1.2']),
            ('6.1.F', '위험요인 파악 및 리스크·기회 평가', ['S 6.1.2']),
        ]
        self.assertEqual(expect, [(s.manual_no, s.title, tags(o, s.manual_no)) for s in o.children('6.1')])
        # 14001:2026 6.1.1 일반은 IMS 에서 6.1 상위 섹션에 접힌다 (G2, AC-30)
        sec = o.section('6.1')
        self.assertEqual([('E 6.1.1', mo.ROLE_IMS_FOLD)], [(r.label, r.role) for r in sec.folded_refs])
        self.assertNotIn('E 6.1.1', sec.related_tag)

    def test_example_b_pairs(self):
        o = outline('ISO 9001:2026', 'ISO 45001:2018')
        self.assertEqual([['Q 6.1.1', 'S 6.1.1'], ['Q 6.1.2', 'S 6.1.4'], ['Q 6.1.3'], ['S 6.1.3'], ['S 6.1.2']],
                         [tags(o, s.manual_no) for s in o.children('6.1')])
        o = outline('ISO 14001:2026', 'ISO 45001:2018')
        self.assertEqual([['E 6.1.4', 'S 6.1.1'], ['E 6.1.5', 'S 6.1.4'], ['E 6.1.3', 'S 6.1.3'],
                          ['E 6.1.2'], ['S 6.1.2']],
                         [tags(o, s.manual_no) for s in o.children('6.1')])
        self.assertEqual(['E 6.1.1'], [r.label for r in o.section('6.1').folded_refs])

    def test_14001_2026_6_4_and_45001_6_4_not_merged(self):
        o = outline('ISO 14001:2026', 'ISO 45001:2018')
        self.assertNotEqual(o.section_of_clause('E', '6.1.4').manual_no, o.section_of_clause('S', '6.1.4').manual_no)

    def test_r4_emergency_8_8(self):
        for codes, expect in [
            (('ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018'), ['E 8.2', 'S 8.2']),
            (('ISO 9001:2015', 'ISO 45001:2018'), ['S 8.2']),     # AC-3
            (('ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018'), ['E 8.2', 'S 8.2']),
        ]:
            with self.subTest(codes=codes):
                o = outline(*codes)
                self.assertEqual('제품 및 서비스 요구사항', o.section('8.2').title)
                self.assertEqual(['Q 8.2'], tags(o, '8.2'))
                # AC-3 은 "8.8 = 비상시 대비 및 대응(S 8.2)" 이지만 IMS 제목은 hls ims_subclause_titles
                # ("비상사태 대비 및 대응")를 따른다 (R4 본문 표기와 같음).
                self.assertEqual('비상사태 대비 및 대응', o.section('8.8').title)
                self.assertEqual(expect, tags(o, '8.8'))
                self.assertEqual(2, o.section('8.8').phase)
                self.assertIsNone(o.section('8.9'))

    def test_r4_without_9001(self):
        o = outline('ISO 14001:2015', 'ISO 45001:2018')
        self.assertEqual('비상사태 대비 및 대응', o.section('8.2').title)
        self.assertEqual(['E 8.2', 'S 8.2'], tags(o, '8.2'))
        self.assertIsNone(o.section('8.3'))

    def test_r3_unique_numbers(self):
        o = outline('ISO 9001:2015', 'ISO 45001:2018')
        self.assertEqual(['S 5.4'], tags(o, '5.4'))
        self.assertEqual(['Q 6.3'], tags(o, '6.3'))
        self.assertEqual('[관련 조항: S 5.4]', o.section('5.4').related_tag)

    def test_6_3_changes_merged_2026(self):
        o = outline('ISO 9001:2026', 'ISO 14001:2026')
        self.assertEqual(['Q 6.3', 'E 6.3'], tags(o, '6.3'))

    def test_r6_change_management_separate(self):
        o = outline('ISO 9001:2015', 'ISO 45001:2018')
        self.assertEqual(['Q 6.3'], tags(o, '6.3'))
        self.assertEqual(['S 8.1.3'], tags(o, '8.1.C'))
        self.assertEqual('8.5', o.section_of_clause('Q', '8.5.6').manual_no)

    def test_s3_scenario_9001_45001(self):
        o = outline('ISO 9001:2015', 'ISO 45001:2018')
        self.assertEqual('통합방침(품질·안전보건)', o.meta.policy_name)
        self.assertEqual('통합경영매뉴얼 (ISO 9001·45001)', o.meta.doc_title)
        self.assertEqual('9001_45001', o.meta.filename_numbers)
        self.assertEqual(['S 8.1.2', 'S 8.1.3', 'S 8.1.4'], [tags(o, n)[0] for n in ('8.1.B', '8.1.C', '8.1.D')])
        self.assertEqual(['Q 9.1.2'], tags(o, '9.1.C'))
        self.assertEqual('고객만족', o.section('9.1.C').title)
        self.assertEqual(['S 9.1.2'], tags(o, '9.1.B'))
        self.assertEqual('사건, 부적합 및 시정조치', o.section('10.2').title)   # R7 title_variants
        self.assertEqual('리더십과 근로자 참여', o.section('5').title)

    def test_ims_expanded_subsections_are_mandatory(self):
        # IMS 에서 독립 제목으로 펼쳐진 9001 9.1.2/9.1.3 은 단일에서 fold 였어도 커버리지 대상(사용자 결정 2026-10-25)
        for combo in (('ISO 9001:2015', 'ISO 14001:2015'), ('ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018')):
            o = outline(*combo)
            for no in ('9.1.C', '9.1.D'):
                self.assertTrue(o.section(no).mandatory, (combo, no))
                self.assertIn(o.section(no), o.coverage_targets())
            self.assertEqual([], [s.manual_no for s in o.sections if not s.mandatory])
        # 단일 9001 은 9.1.2/9.1.3 이 여전히 9.1 에 접힘(목차 불변, AC-10)
        single = outline('ISO 9001:2015')
        self.assertIsNone(single.section('9.1.C'))

    def test_r11_9001_2026_45001(self):
        o = outline('ISO 9001:2026', 'ISO 45001:2018')
        self.assertEqual('지속적 개선', o.section('10.1').title)
        self.assertEqual('[관련 조항: Q 10.1 · S 10.3 · S 10.1]', o.section('10.1').related_tag)
        self.assertEqual(mo.ROLE_ABSORBED, [r for r in o.section('10.1').refs if r.label == 'S 10.1'][0].role)
        self.assertEqual(['Q 10.2', 'S 10.2'], tags(o, '10.2'))
        self.assertIsNone(o.section('10.3'))

    def test_r11_9001_2015_14001_2026(self):
        o = outline('ISO 9001:2015', 'ISO 14001:2026')
        self.assertEqual(('일반사항', ['Q 10.1']), (o.section('10.1').title, tags(o, '10.1')))
        self.assertEqual(['Q 10.2', 'E 10.2'], tags(o, '10.2'))
        self.assertEqual(('지속적 개선', ['Q 10.3', 'E 10.1']), (o.section('10.3').title, tags(o, '10.3')))

    def test_r11_three_2026(self):
        o = outline('ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018')
        self.assertEqual(['10.1', '10.2'], [s.manual_no for s in o.children('10')])
        self.assertEqual(['Q 10.1', 'E 10.1', 'S 10.3', 'S 10.1'], tags(o, '10.1'))

    def test_absorption_not_applied_when_primary_has_key(self):
        for codes in [('ISO 9001:2015', 'ISO 45001:2018'), ('ISO 14001:2015', 'ISO 45001:2018'),
                      ('ISO 9001:2015', 'ISO 14001:2026', 'ISO 45001:2018')]:
            with self.subTest(codes=codes):
                o = outline(*codes)
                self.assertFalse([r for s in o.sections for r in s.refs if r.role == mo.ROLE_ABSORBED])

    def test_ims_titles_from_hls(self):
        o = outline('ISO 9001:2015', 'ISO 14001:2015')
        self.assertEqual('통합경영시스템 적용범위 결정', o.section('4.3').title)
        self.assertEqual('hls_common', o.section('4.3').title_source)
        self.assertEqual('리더십', o.section('5').title)       # S 미선택 → 기본 제목
        self.assertEqual('부적합 및 시정조치', o.section('10.2').title)


class ProcedureMergeTest(unittest.TestCase):
    """FR-04·FR-34·R10."""

    def by_id(self, o):
        return {p.id: p for p in o.procedures}

    def test_integrated_cp08_cp09(self):
        o = outline('ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018')
        p = self.by_id(o)
        for pid in ('EP02', 'SP02', 'EP03', 'SP04'):
            self.assertNotIn(pid, p)
        self.assertEqual(('EP02', 'SP02'), p['CP08'].replaces)
        self.assertEqual(('EP03', 'SP04'), p['CP09'].replaces)
        self.assertEqual('환경안전팀', p['CP09'].department)     # ims_procedures 명시값 우선
        self.assertEqual('CORE', p['CP09'].process_category)
        self.assertEqual('SMS-CP09', p['CP09'].doc_no)
        self.assertEqual(('8.8',), p['CP09'].manual_sections)
        self.assertEqual(('CP09',), o.section('8.8').procedures)
        self.assertIn('CP08', o.section('6.1.C').procedures)
        # R10 합집합: 괄호 안 쉼표는 나누지 않는다
        self.assertIn('비상 자원(소화설비, 응급장비)', p['CP09'].input)
        self.assertEqual(p['CP09'].input.count('비상연락망'), 1)

    def test_no_integration_with_single_member(self):
        p = self.by_id(outline('ISO 9001:2015', 'ISO 14001:2015'))
        self.assertIn('EP02', p)
        self.assertIn('EP03', p)
        self.assertNotIn('CP08', p)
        self.assertNotIn('CP09', p)

    def test_common_cp_department_first_standard(self):
        p = self.by_id(outline('ISO 14001:2015', 'ISO 45001:2018'))
        self.assertEqual('환경안전팀', p['CP00'].department)
        self.assertEqual((('E', 'CP00'), ('S', 'CP00')), p['CP00'].sources)
        p = self.by_id(outline('ISO 9001:2015', 'ISO 45001:2018'))
        self.assertEqual('품질경영팀', p['CP00'].department)

    def test_retention_and_order(self):
        o = outline('ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018')
        ids = [p.id for p in o.procedures]
        self.assertEqual(['CP00', 'CP01', 'CP02', 'CP03', 'CP04', 'CP05', 'CP06', 'CP07', 'CP08', 'CP09'], ids[:10])
        self.assertEqual(['QP00', 'QP01', 'QP02', 'QP03', 'QP04', 'QP05', 'QP06'], ids[10:17])
        self.assertTrue(all(i.startswith('EP') for i in ids[17:20]))
        self.assertTrue(all(i.startswith('SP') for i in ids[20:]))

    def test_retention_rule_unit(self):
        self.assertEqual('5년', mo._longest_retention(['3년', '5년']))
        self.assertEqual('3년', mo._longest_retention(['3년', '개정 후 3년']))
        self.assertEqual('5년 또는 법정 보존기간', mo._longest_retention(['5년', '3년 또는 법정 보존기간']))

    def test_single_procedures_unchanged(self):
        o = outline('ISO 14001:2015')
        std = LOADER.get_standard('iso14001', '2015')
        self.assertEqual([p.id for p in std.procedures], [p.id for p in o.procedures])
        for mine, src in zip(o.procedures, std.procedures):
            self.assertEqual((src.department, src.retention, src.input), (mine.department, mine.retention, mine.input))


class AppendixCTest(unittest.TestCase):
    def test_header_editions_mixed(self):
        o = outline('ISO 45001:2018', 'ISO 9001:2026')     # 입력 순서 무관
        md = mo.render_appendix_c_markdown(o)
        self.assertIn('| 매뉴얼 번호 | 제목 | ISO 9001:2026 | ISO 45001:2018 |', md)
        self.assertIn('| 10.1 | 지속적 개선 | 10.1 | 10.3, 10.1 |', md)
        self.assertIn('| 8.8 | 비상사태 대비 및 대응 | — | 8.2 |', md)
        self.assertEqual('ISO 9001:2026 · ISO 45001:2018', o.meta.cover_standards)   # AC-29

    def test_single(self):
        md = mo.render_appendix_c_markdown(outline('ISO 9001:2015'))
        self.assertIn('| 8.7 | 부적합 출력(Output)의 관리 | 8.7 (8.7.1, 8.7.2) |', md)


class Ac22EditionIsolationTest(unittest.TestCase):
    """AC-22: 2015/2018 조합 결과는 2026 파일·hls 의 2026 전용 항목이 없어도 같다."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='outline_ac22_')
        for name in ('hls_common.json', 'iso9001_2015.json', 'iso14001_2015.json', 'iso45001_2018.json'):
            shutil.copy(os.path.join(sl.STANDARDS_DIR, name), os.path.join(self.dir, name))
        path = os.path.join(self.dir, 'hls_common.json')
        with open(path, encoding='utf-8') as fh:
            hls = json.load(fh)
        # 2026 대응으로 추가된 hls 항목을 모두 제거한다
        hls['ims_subclause_order']['6.1'].remove('planning.opportunity_actions')
        del hls['ims_subclause_titles']['planning.opportunity_actions']
        hls['ims_fold_into_parent'] = []
        hls['ims_key_absorption'] = []
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(hls, fh, ensure_ascii=False)
        self.loader = sl.StandardsLoader(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_2015_combos_unchanged(self):
        codes = ['ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018']
        for r in (1, 2, 3):
            for combo in itertools.combinations(codes, r):
                with self.subTest(combo=combo):
                    a = outline(*combo)
                    b = outline(*combo, loader=self.loader)
                    self.assertEqual(mo.outline_to_dict(a), mo.outline_to_dict(b))
                    self.assertEqual(mo.render_appendix_c_markdown(a), mo.render_appendix_c_markdown(b))


class GuardTest(unittest.TestCase):
    def test_errors(self):
        with self.assertRaises(mo.OutlineError):
            mo.build_outline([], LOADER)
        s = sel('ISO 9001:2015')[0]
        with self.assertRaises(mo.OutlineError):
            mo.build_outline([s, s], LOADER)

    def test_no_edition_or_standard_branches_in_code(self):
        """FR-82: edition·규격 분기 금지. 엔진 코드에 연도·규격 키 리터럴이 없어야 한다."""
        with open(os.path.join(REPO_ROOT, 'api', 'services', 'manual_outline.py'), encoding='utf-8') as fh:
            code = fh.read()
        code_wo_doc = re.sub(r'"""[\s\S]*?"""', '', code)
        code_wo_doc = re.sub(r'#.*', '', code_wo_doc)
        for literal in ('2015', '2018', '2026', 'iso9001', 'iso14001', 'iso45001', '"Q"', '"E"', '"S"',
                        "'Q'", "'E'", "'S'", 'edition =='):
            self.assertNotIn(literal, code_wo_doc, literal)

    def test_outline_is_read_only(self):
        o = outline('ISO 9001:2015')
        with self.assertRaises(Exception):
            o.sections[0].title = 'x'


if __name__ == '__main__':
    unittest.main()
