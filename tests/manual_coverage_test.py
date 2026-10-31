"""AI 매뉴얼 생성기 T7 — 커버리지 점검 모듈, SSE `coverage` named event, 조회 API.

OpenAI·로컬 서버는 쓰지 않는다. 생성 스트림은 mock, DB 는 인메모리 SQLite.
"""
import datetime
import json
import os
import re
import sys
import time
import unittest
from unittest.mock import patch

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

# index 를 import 하기 전에 인메모리 DB 지정 (tests/conftest.py, manual_retry_test.py 와 같은 이유)
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

from index import app, db  # noqa: E402
from models import ManualGeneration, User  # noqa: E402
from services import manual_coverage as mc  # noqa: E402
from services import manual_outline as mo  # noqa: E402
from services import manual_prompt_builder as mpb  # noqa: E402
from services import standards_loader as sl  # noqa: E402


def outline_of(*codes):
    return mo.build_outline(sl.resolve_manual_standards(target_isos=list(codes), enforce_exposure=False))


COMBOS = (
    ('ISO 9001:2015',), ('ISO 9001:2026',), ('ISO 14001:2015',), ('ISO 14001:2026',), ('ISO 45001:2018',),
    ('ISO 9001:2015', 'ISO 14001:2015'), ('ISO 14001:2026', 'ISO 45001:2018'),
    ('ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018'),
    ('ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018'),
)


def heading_line(outline, s):
    """프롬프트 빌더가 지시하는 제목 형식 (FR-14)."""
    if s.level == 1:
        return '# %s. %s' % (s.manual_no, s.title)
    text = '%s %s %s' % ('#' * min(s.level, 3), s.manual_no, s.title)
    if s.related_tag and s.level >= 2:
        text += ' ' + s.related_tag
    return text


def make_markdown(outline, phase, drop=(), body=None, retitle=None):
    """phase 의 모든 섹션을 제목+본문으로 갖춘 정상 마크다운. drop: 제목 줄을 뺄 manual_no."""
    parts = []
    for s in outline.sections_for_phase(phase):
        text = body(s) if body else '%s 에 대하여 기술한다.' % ' '.join(s.coverage_keywords)
        if s.manual_no in drop:
            parts.append(text)           # 제목만 빠지고 본문은 남는다
            continue
        line = heading_line(outline, s)
        if retitle and s.manual_no in retitle:
            line = retitle[s.manual_no]
        parts.append(line + '\n\n' + text)
    return '\n\n'.join(parts)


def missing_numbers(report):
    return [m.manual_no for m in report.missing]


# ---------------------------------------------------------------------------
# 필수 섹션 (FR-50, AC-15, R8)
# ---------------------------------------------------------------------------


class SectionCoverageTest(unittest.TestCase):
    def test_complete_document_has_no_gaps_for_all_combos_and_phases(self):
        for codes in COMBOS:
            o = outline_of(*codes)
            for phase in (1, 2, 3):
                r = mc.check_phase_coverage(make_markdown(o, phase), o, phase)
                self.assertGreater(r.required, 0, (codes, phase))
                self.assertEqual(r.required, r.found, (codes, phase))
                self.assertEqual([], missing_numbers(r), (codes, phase))
                self.assertEqual((), r.keyword_missing, (codes, phase))
                self.assertEqual((), r.renumbered, (codes, phase))
                self.assertTrue(r.ok, (codes, phase))

    def test_required_counts_only_mandatory_sections_of_the_phase(self):
        o = outline_of('ISO 9001:2026')
        for phase in (1, 2, 3):
            expected = [s for s in o.sections if s.phase == phase and s.mandatory]
            r = mc.check_phase_coverage('', o, phase)
            self.assertEqual(len(expected), r.required)

    def test_ac15_removed_6_1_3_heading_is_reported(self):
        o = outline_of('ISO 9001:2026')
        self.assertIsNotNone(o.section('6.1.3'))
        r = mc.check_phase_coverage(make_markdown(o, 1, drop={'6.1.3'}), o, 1)
        self.assertEqual(['6.1.3'], missing_numbers(r))
        self.assertEqual(r.required - 1, r.found)
        self.assertEqual(('Q 6.1.3',), r.missing[0].refs)
        self.assertEqual(o.section('6.1.3').title, r.missing[0].title)
        self.assertFalse(r.ok)

    def test_removing_some_headings_in_each_phase(self):
        o = outline_of('ISO 9001:2015')
        for phase, drop in ((1, {'4.2', '6.3'}), (2, {'7.5', '8.4'}), (3, {'9.2', '10.2'})):
            r = mc.check_phase_coverage(make_markdown(o, phase, drop=drop), o, phase)
            self.assertEqual(sorted(drop), sorted(missing_numbers(r)), phase)
            self.assertEqual(r.required - len(drop), r.found)

    def test_english_gloss_and_whitespace_differences_are_tolerated(self):
        o = outline_of('ISO 9001:2015')
        s41 = o.section('4.1')
        retitle = {
            '4.1': '##   4.1    %s (Understanding the organization and its context)' % s41.title,
            '4.2': '## 4.2 %s' % o.section('4.2').title.replace(' ', ''),
            '5.2': '## **5.2** **%s**' % o.section('5.2').title,
            '4': '#  4.   %s  ' % o.section('4').title,
        }
        r = mc.check_phase_coverage(make_markdown(o, 1, retitle=retitle), o, 1)
        self.assertEqual([], missing_numbers(r))
        self.assertEqual(r.required, r.found)

    def test_title_match_with_wrong_or_missing_number_is_found_and_flagged(self):
        o = outline_of('ISO 9001:2015')
        s = o.section('4.1')
        for line, actual in (('## %s' % s.title, ''), ('## 3.1 %s' % s.title, '3.1')):
            r = mc.check_phase_coverage(make_markdown(o, 1, retitle={'4.1': line}), o, 1)
            self.assertEqual([], missing_numbers(r), line)
            self.assertEqual((('4.1', actual),), r.renumbered, line)

    def test_wrong_number_and_different_title_is_missing(self):
        o = outline_of('ISO 9001:2015')
        r = mc.check_phase_coverage(make_markdown(o, 1, retitle={'4.1': '## 3.1 전혀 다른 제목'}), o, 1)
        self.assertEqual(['4.1'], missing_numbers(r))

    def test_chapter_heading_is_not_rescued_by_longer_subsection_title(self):
        """'# 5. 리더십' 을 지워도 '## 5.1 리더십과 공약' 이 대신 인정되면 안 된다."""
        o = outline_of('ISO 9001:2015')
        r = mc.check_phase_coverage(make_markdown(o, 1, drop={'5'}), o, 1)
        self.assertEqual(['5'], missing_numbers(r))

    def test_headings_inside_code_fences_do_not_count(self):
        o = outline_of('ISO 9001:2015')
        md = make_markdown(o, 1, drop={'4.1'})
        md += '\n\n```\n%s\n```\n' % heading_line(o, o.section('4.1'))
        self.assertEqual(['4.1'], missing_numbers(mc.check_phase_coverage(md, o, 1)))

    def test_ims_alphabet_numbers(self):
        o = outline_of('ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018')
        self.assertIsNotNone(o.section('6.1.C'))
        r = mc.check_phase_coverage(make_markdown(o, 1, drop={'6.1.C', '6.1.F'}), o, 1)
        self.assertEqual(['6.1.C', '6.1.F'], missing_numbers(r))
        # 알파벳 번호 소문자 표기도 허용
        r = mc.check_phase_coverage(
            make_markdown(o, 1, retitle={'6.1.B': heading_line(o, o.section('6.1.B')).replace('6.1.B', '6.1.b')}), o, 1)
        self.assertEqual([], missing_numbers(r))
        # IMS 의 관련 조항 태그가 제목에 붙어 있어도 제목 비교에 영향 없음 (번호 없는 제목 줄)
        s = o.section('6.1.B')
        r = mc.check_phase_coverage(
            make_markdown(o, 1, retitle={'6.1.B': '### %s %s' % (s.title, s.related_tag)}), o, 1)
        self.assertEqual([], missing_numbers(r))
        self.assertEqual((('6.1.B', ''),), r.renumbered)

    def test_folded_clause_is_satisfied_by_parent_section_r8(self):
        # 14001:2026 6.1.1 은 14001+45001 IMS 에서 상위 6.1 에 접힌다 (ims_fold)
        o = outline_of('ISO 14001:2026', 'ISO 45001:2018')
        section = o.section_of_clause('E', '6.1.1')
        self.assertEqual('6.1', section.manual_no)
        role = next(r.role for r in section.refs if r.label == 'E 6.1.1')
        self.assertIn(role, (mo.ROLE_FOLD, mo.ROLE_IMS_FOLD))
        r = mc.check_phase_coverage(make_markdown(o, 1), o, 1)
        self.assertEqual([], missing_numbers(r))
        # 접힌 조항 자체는 별도 필수 항목이 아니므로 상위 섹션 제목이 없을 때만 누락으로 나온다
        r = mc.check_phase_coverage(make_markdown(o, 1, drop={'6.1'}), o, 1)
        self.assertEqual(['6.1'], missing_numbers(r))

    def test_single_fold_clauses_do_not_need_own_heading(self):
        o = outline_of('ISO 9001:2015')       # 5.1.1, 5.1.2 는 5.1 에 접힘
        self.assertEqual('5.1', o.section_of_clause('Q', '5.1.1').manual_no)
        r = mc.check_phase_coverage(make_markdown(o, 1), o, 1)
        self.assertEqual([], missing_numbers(r))

    def test_empty_and_invalid_input(self):
        o = outline_of('ISO 9001:2015')
        for md in ('', '   \n\n', None, 123):
            r = mc.check_phase_coverage(md, o, 1)
            self.assertEqual(0, r.found)
            self.assertEqual(r.required, len(r.missing))
            self.assertIsNone(r.error)

    def test_never_raises(self):
        o = outline_of('ISO 9001:2015')
        r = mc.check_phase_coverage('# 4. x', o, 9)
        self.assertEqual('ValueError', r.error)
        self.assertFalse(r.ok)
        r = mc.check_phase_coverage('# 4. x', None, 1)
        self.assertIsNotNone(r.error)
        self.assertNotIn(os.sep, r.error)

    def test_phase_scope(self):
        o = outline_of('ISO 9001:2015')
        phase1_doc = make_markdown(o, 1)
        r = mc.check_phase_coverage(phase1_doc, o, 2)
        self.assertEqual(0, r.found)
        self.assertEqual(r.required, len(r.missing))
        phase2_doc = make_markdown(o, 2)
        self.assertEqual(0, mc.check_phase_coverage(phase2_doc, o, 1).found)
        self.assertTrue(mc.check_phase_coverage(phase2_doc, o, 2).ok)

    def test_performance_is_light(self):
        o = outline_of('ISO 9001:2026', 'ISO 14001:2026', 'ISO 45001:2018')
        big = make_markdown(o, 1, body=lambda s: ('가나다라 ' * 2000) + ' '.join(s.coverage_keywords))
        started = time.perf_counter()
        r = mc.check_phase_coverage(big, o, 1)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(r.ok)

    def test_pathological_lines_are_linear(self):
        """F-08: 공백·괄호·탭이 긴 한 줄(16,000자)에서도 제목 파싱이 2차 시간으로 늘지 않는다."""
        o = outline_of('ISO 9001:2015')
        n = 16000
        cases = {
            '제목 뒤 긴 공백 + 문자': '# 4. 조직 상황' + ' ' * n + 'y',
            '제목 번호 뒤 긴 공백': '# 1' + ' ' * n + 'y',
            '탭': '## 4.1 가' + '\t' * n + 'y',
            '공백뿐인 제목': '# ' + ' ' * n,
            '괄호 반복': '# 4. ' + '가(' * n,
            '태그 반복': '# 4 ' + '[관련 조항 ' * (n // 8),
            '닫는 해시 반복': '# 4 ' + 'a #' * (n // 3),
            '공백 뒤 문자 반복': '# 4 ' + ' x' * (n // 2),
            '펜스 안 긴 줄': '```mermaid\nflowchart TB\n' + 'A["' + ' ' * n + '"]\n```',
        }
        for name, doc in cases.items():
            started = time.perf_counter()
            mc.check_phase_coverage(doc, o, 1)
            self.assertLess(time.perf_counter() - started, 1.0, name)

    def test_heading_forms_unchanged(self):
        """제목 줄 파싱을 정규식에서 선형 파싱으로 바꿔도 동작은 같다."""
        h = mc._match_heading
        self.assertEqual((1, '4. 조직 상황'), h('# 4. 조직 상황'))
        self.assertEqual((2, '4.1 이해'), h('  ## 4.1 이해 ##'))
        self.assertEqual((3, '6.1.A 결정'), h('### 6.1.A 결정   '))
        self.assertEqual((1, '#'), h('# #'))                  # 닫는 해시는 앞에 공백이 있어야 한다
        self.assertEqual((1, 'a#'), h('# a#'))
        self.assertIsNone(h('#제목'))
        self.assertIsNone(h('####### 일곱'))
        self.assertIsNone(h('    # 들여쓰기 4칸'))
        self.assertIsNone(h('# '))
        self.assertEqual((1, ''), h('#   '))
        self.assertEqual('x' * mc._MAX_HEADING_CHARS, mc._split_heading('x' * 5000)[1])


# ---------------------------------------------------------------------------
# 키워드 (FR-50)
# ---------------------------------------------------------------------------


class KeywordTest(unittest.TestCase):
    def test_keyword_missing_is_a_warning_not_a_missing_section(self):
        o = outline_of('ISO 9001:2015')
        s = o.section('5.2')
        self.assertEqual(('방침',), s.coverage_keywords)
        md = make_markdown(o, 1, retitle={'5.2': '## 5.2 Quality Policy'},
                           body=lambda x: '본문입니다.' if x.manual_no == '5.2' else ' '.join(x.coverage_keywords))
        r = mc.check_phase_coverage(md, o, 1)
        self.assertEqual([], missing_numbers(r))
        self.assertEqual(r.required, r.found)
        self.assertEqual(['5.2'], [k.manual_no for k in r.keyword_missing])
        self.assertEqual(('방침',), r.keyword_missing[0].keywords)
        self.assertTrue(r.ok)

    def test_keywords_are_or_matched(self):
        o = outline_of('ISO 9001:2015')
        s = o.section('4.1')
        self.assertGreater(len(s.coverage_keywords), 1)
        only_last = s.coverage_keywords[-1]
        md = make_markdown(o, 1, retitle={'4.1': '## 4.1 Context'},
                           body=lambda x: ('%s 만 있다.' % only_last) if x.manual_no == '4.1' else ' '.join(x.coverage_keywords))
        self.assertEqual((), mc.check_phase_coverage(md, o, 1).keyword_missing)

    def test_keyword_found_in_title_alone_and_ignoring_spaces(self):
        o = outline_of('ISO 14001:2015')
        s = o.section('6.1.2')
        self.assertIsNotNone(s)
        kw = s.coverage_keywords[0]                      # '환경측면' (공백 없이 정의)
        md = make_markdown(o, 1, retitle={'6.1.2': '## 6.1.2 %s' % (kw[:2] + ' ' + kw[2:])},
                           body=lambda x: '본문.' if x.manual_no == '6.1.2' else ' '.join(x.coverage_keywords))
        self.assertEqual((), mc.check_phase_coverage(md, o, 1).keyword_missing)

    def test_subsection_text_counts_for_parent(self):
        o = outline_of('ISO 14001:2026', 'ISO 45001:2018')
        # 6.1 본문은 키워드가 없지만 하위 6.1.A~E 가 다룬다
        md = make_markdown(o, 1, body=lambda x: '내용.' if x.manual_no == '6.1' else ' '.join(x.coverage_keywords))
        r = mc.check_phase_coverage(md, o, 1)
        self.assertNotIn('6.1', [k.manual_no for k in r.keyword_missing])


# ---------------------------------------------------------------------------
# 문서번호 (FR-53)
# ---------------------------------------------------------------------------


class DocNumberTest(unittest.TestCase):
    def check(self, o, text, phase=1):
        return mc.check_phase_coverage(make_markdown(o, phase) + '\n\n' + text, o, phase)

    def test_valid_numbers_pass_including_korean_particles(self):
        o = outline_of('ISO 9001:2015')
        r = self.check(o, '「SMS-QP01 영업 절차서」에 따라 처리하며 SMS-CP03에 정한다. SMS-QP06, SMS-CP00.')
        self.assertEqual((), r.doc_no_issues)

    def test_unknown_number(self):
        o = outline_of('ISO 9001:2015')
        r = self.check(o, 'SMS-QP99 와 SMS-EP01 을 참조한다. SMS-QP99 다시.')
        by = {d.doc_no: d for d in r.doc_no_issues}
        self.assertEqual({'SMS-QP99', 'SMS-EP01'}, set(by))
        self.assertEqual('unknown', by['SMS-QP99'].kind)
        self.assertEqual(2, by['SMS-QP99'].count)
        self.assertIsNone(by['SMS-QP99'].replaced_by)
        self.assertGreater(by['SMS-QP99'].line, 0)

    def test_replaced_number_in_ims(self):
        o = outline_of('ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018')
        r = self.check(o, 'SMS-EP02 와 SMS-SP02 및 SMS-EP03, SMS-CP08 참조.')
        by = {d.doc_no: d for d in r.doc_no_issues}
        self.assertEqual({'SMS-EP02', 'SMS-SP02', 'SMS-EP03'}, set(by))
        self.assertEqual('replaced', by['SMS-EP02'].kind)
        self.assertEqual('SMS-CP08', by['SMS-EP02'].replaced_by)
        self.assertEqual('SMS-CP08', by['SMS-SP02'].replaced_by)
        self.assertEqual('SMS-CP09', by['SMS-EP03'].replaced_by)

    def test_same_number_is_valid_when_not_replaced(self):
        # 14001 단독에서는 EP02 가 그대로 유효하다 (IMS 에서만 CP08 로 대체)
        o = outline_of('ISO 14001:2015')
        self.assertIn('SMS-EP02', [p.doc_no for p in o.procedures])
        self.assertEqual((), self.check(o, 'SMS-EP02 참조').doc_no_issues)

    def test_other_document_kinds_are_not_checked(self):
        o = outline_of('ISO 9001:2015')
        r = self.check(o, 'SMS-M01, SMS-WI01, SMS-F-QP0101, SMS-EP0234')
        self.assertEqual((), r.doc_no_issues)

    def test_numbers_inside_code_blocks_are_checked(self):
        o = outline_of('ISO 14001:2015')
        r = self.check(o, '```mermaid\nflowchart TB\n    A["x<br/>SMS-QP01"]\n```')
        self.assertEqual(['SMS-QP01'], [d.doc_no for d in r.doc_no_issues])


# ---------------------------------------------------------------------------
# Mermaid (FR-53)
# ---------------------------------------------------------------------------


class MermaidTest(unittest.TestCase):
    def kinds(self, block, closed=True):
        o = outline_of('ISO 9001:2015')
        text = '```mermaid\n%s\n%s' % (block, '```' if closed else '')
        r = mc.check_phase_coverage(make_markdown(o, 1) + '\n\n' + text, o, 1)
        return r, [w.kind for w in r.mermaid_warnings]

    def test_prompt_template_passes_for_all_combos(self):
        for codes in COMBOS:
            o = outline_of(*codes)
            md = make_markdown(o, 1) + '\n\n' + mpb.build_process_map_template(o)
            r = mc.check_phase_coverage(md, o, 1)
            self.assertEqual((), r.mermaid_warnings, codes)
            self.assertEqual(1, r.mermaid_blocks, codes)

    def test_clean_block(self):
        r, kinds = self.kinds('flowchart TB\n    A["요구 접수"]\n    A --> B\n    A -.policy.-> B')
        self.assertEqual([], kinds)
        self.assertEqual(1, r.mermaid_blocks)

    def test_unclosed_fence(self):
        _, kinds = self.kinds('flowchart TB\n    A["x"]', closed=False)
        self.assertIn('unclosed_fence', kinds)

    def test_unquoted_label(self):
        _, kinds = self.kinds('flowchart TB\n    A[요구 접수]')
        self.assertIn('unquoted_label', kinds)

    def test_multiple_edges_on_one_line(self):
        _, kinds = self.kinds('flowchart TB\n    A --> B B --> C')
        self.assertIn('multiple_edges', kinds)

    def test_edge_label_forbidden_chars(self):
        _, kinds = self.kinds('flowchart TB\n    A -.방침·정책.-> B')
        self.assertIn('edge_label', kinds)
        _, kinds = self.kinds('flowchart TB\n    A -.policy(x).-> B')
        self.assertIn('edge_label', kinds)

    def test_smart_quote_and_em_dash(self):
        _, kinds = self.kinds('flowchart TB\n    A["\u201c고객\u201d"]')
        self.assertIn('forbidden_char', kinds)
        _, kinds = self.kinds('flowchart TB\n    A["고객 \u2014 접수"]')
        self.assertIn('forbidden_char', kinds)

    def test_non_ascii_node_id(self):
        _, kinds = self.kinds('flowchart TB\n    고객["접수"]')
        self.assertIn('bad_node_id', kinds)

    def test_missing_declaration_and_empty_block(self):
        _, kinds = self.kinds('A --> B')
        self.assertIn('no_declaration', kinds)
        _, kinds = self.kinds('')
        self.assertIn('empty_block', kinds)

    def test_br_outside_label(self):
        _, kinds = self.kinds('flowchart TB\n    A["x<br/>y"]\n    A --> B<br/>')
        self.assertEqual(['br_outside_label'], kinds)

    def test_no_mermaid_no_warning(self):
        o = outline_of('ISO 9001:2015')
        r = mc.check_phase_coverage(make_markdown(o, 1), o, 1)
        self.assertEqual(0, r.mermaid_blocks)
        self.assertEqual((), r.mermaid_warnings)


# ---------------------------------------------------------------------------
# 직렬화·form_data
# ---------------------------------------------------------------------------


class SerializationTest(unittest.TestCase):
    def test_report_json_is_single_line_and_has_fr51_shape(self):
        o = outline_of('ISO 9001:2026')
        r = mc.check_phase_coverage(make_markdown(o, 1, drop={'6.1.3'}), o, 1)
        text = mc.report_to_json(r)
        self.assertNotIn('\n', text)
        data = json.loads(text)
        self.assertEqual(1, data['phase'])
        self.assertEqual(r.required, data['required'])
        self.assertEqual(r.required - 1, data['found'])
        self.assertEqual([{'manual_no': '6.1.3', 'title': o.section('6.1.3').title, 'refs': ['Q 6.1.3']}],
                         data['missing'])
        for key in ('keyword_missing', 'renumbered', 'doc_no_issues', 'mermaid_warnings', 'ok', 'error'):
            self.assertIn(key, data)
        self.assertFalse(data['ok'])

    def test_issue_lists_are_capped(self):
        o = outline_of('ISO 9001:2015')
        spam = ' '.join('SMS-QP%02d' % i for i in range(60, 100))
        r = mc.check_phase_coverage(make_markdown(o, 1) + '\n' + spam, o, 1)
        self.assertEqual(mc.MAX_LISTED_ISSUES, len(r.doc_no_issues))

    def test_manual_totals(self):
        o = outline_of('ISO 9001:2015')
        c = mc.check_manual_coverage({1: make_markdown(o, 1, drop={'4.1'}), 2: '', 3: make_markdown(o, 3)}, o)
        self.assertEqual([1, 3], [r.phase for r in c.phases])          # 빈 phase 는 점검 대상 아님
        d = mc.manual_coverage_to_dict(c, manual_id='abc')
        self.assertEqual('abc', d['manual_id'])
        self.assertEqual(['ISO 9001:2015'], d['standards'])
        self.assertEqual([1, 3], d['completed_phases'])
        self.assertEqual(d['totals']['required'] - 1, d['totals']['found'])
        self.assertEqual(1, d['totals']['missing'])

    def test_outline_from_form_data(self):
        a = mc.outline_from_form_data({'target_isos': ['ISO 9001:2026', 'ISO 45001:2018']})
        self.assertEqual(('ISO 9001:2026', 'ISO 45001:2018'), a.meta.standard_codes)
        b = mc.outline_from_form_data({'target_iso': 'ISO 14001:2015'})          # 옛 행
        self.assertEqual(('ISO 14001:2015',), b.meta.standard_codes)
        c = mc.outline_from_form_data({})                                        # 둘 다 없음 -> 현행 기본값
        self.assertEqual(('ISO 9001:2015',), c.meta.standard_codes)
        # 노출 게이트가 닫혀 있어도 이미 생성된 문서의 점검은 가능
        with patch.dict(os.environ):
            os.environ.pop('ENABLE_EDITION_2026', None)
            self.assertEqual(('ISO 9001:2026',), mc.outline_from_form_data({'target_isos': ['ISO 9001:2026']}).meta.standard_codes)


# ---------------------------------------------------------------------------
# API: 조회 엔드포인트, SSE coverage 이벤트
# ---------------------------------------------------------------------------


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256')
    return {'Authorization': f'Bearer {token}'}


def parse_sse(body):
    """SSE 본문을 이벤트 목록 [(event_name or None, data)] 로 나눈다 (브라우저 EventSource 규칙)."""
    events = []
    for block in body.split('\n\n'):
        if not block:
            continue
        name, data_lines = None, []
        for line in block.split('\n'):
            if line.startswith('event: '):
                name = line[len('event: '):]
            elif line.startswith('data: '):
                data_lines.append(line[len('data: '):])
            elif line.startswith('data:'):
                data_lines.append(line[len('data:'):])
        events.append((name, '\n'.join(data_lines)))
    return events


class CoverageApiTestBase(unittest.TestCase):
    def setUp(self):
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        if hasattr(app, '_tables_created'):
            delattr(app, '_tables_created')
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        # 전체 받기(2·3단계)는 사업자등록번호 확인 기업만(main #22) — 커버리지 동작만 보기 위해 등록해 둔다
        self.owner = User(email='c1@example.com', password_hash='x', role='company', name='C1', company_name='Buyer Co',
                          biz_reg_no='2208112341', biz_status='verified')
        self.other = User(email='c2@example.com', password_hash='x', role='company', name='C2', company_name='Other Co',
                          biz_reg_no='1018200009', biz_status='verified')
        self.consultant = User(email='k@example.com', password_hash='x', role='consultant', name='K')
        self.admin = User(email='a@example.com', password_hash='x', role='admin', name='A')
        db.session.add_all([self.owner, self.other, self.consultant, self.admin])
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def form(self, **extra):
        return dict({'company_name': 'Buyer Co', 'industry': '제조업', 'main_product': '자동차 부품',
                     'employees': '50', 'target_isos': ['ISO 9001:2015'], 'target_iso': 'ISO 9001:2015',
                     'issues': [], 'reasons': []}, **extra)

    def make_manual(self, user=None, form=None, **phases):
        m = ManualGeneration(
            id='m-%d' % (ManualGeneration.query.count() + 1), user_id=(user or self.owner).id,
            token_hash='h', token_expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
            form_data='{}', status='phase_1_completed')
        m.set_form_data(form or self.form())
        for key, value in phases.items():
            setattr(m, key, value)
        db.session.add(m)
        db.session.commit()
        return m

    def coverage(self, manual_id, user=None):
        headers = auth_headers(user) if user else {}
        return self.client.get('/api/iso-manual/%s/coverage' % manual_id, headers=headers)


class CoverageEndpointTest(CoverageApiTestBase):
    def test_permissions(self):
        m = self.make_manual()
        self.assertEqual(401, self.coverage(m.id).status_code)
        self.assertEqual(200, self.coverage(m.id, self.owner).status_code)
        self.assertEqual(200, self.coverage(m.id, self.admin).status_code)
        self.assertEqual(404, self.coverage(m.id, self.other).status_code)          # 타인은 존재 여부를 숨김
        self.assertEqual(403, self.coverage(m.id, self.consultant).status_code)     # resume 와 같은 역할 규칙
        self.assertEqual(404, self.coverage('no-such-id', self.owner).status_code)
        self.assertEqual('NOT_FOUND', self.coverage(m.id, self.other).get_json()['code'])

    def test_nothing_generated_yet(self):
        m = self.make_manual()
        body = self.coverage(m.id, self.owner).get_json()
        self.assertEqual([], body['completed_phases'])
        self.assertEqual([], body['phases'])
        self.assertEqual({'required': 0, 'found': 0, 'missing': 0}, body['totals'])

    def test_recomputes_from_saved_phases_and_form_data(self):
        o = outline_of('ISO 9001:2015')
        m = self.make_manual(phase1_markdown=make_markdown(o, 1, drop={'4.2'}),
                             phase2_markdown=make_markdown(o, 2))
        body = self.coverage(m.id, self.owner).get_json()
        self.assertEqual(m.id, body['manual_id'])
        self.assertEqual(['ISO 9001:2015'], body['standards'])
        self.assertEqual([1, 2], body['completed_phases'])
        p1, p2 = body['phases']
        self.assertEqual(1, p1['phase'])
        self.assertEqual(['4.2'], [x['manual_no'] for x in p1['missing']])
        self.assertEqual(p1['required'] - 1, p1['found'])
        self.assertEqual([], p2['missing'])
        t = body['totals']
        self.assertEqual(p1['required'] + p2['required'], t['required'])
        self.assertEqual(1, t['missing'])

    def test_legacy_row_with_only_target_iso_and_ims(self):
        o14 = outline_of('ISO 14001:2015')
        legacy = self.form()
        legacy.pop('target_isos')
        legacy['target_iso'] = 'ISO 14001:2015'
        m = self.make_manual(form=legacy, phase1_markdown=make_markdown(o14, 1))
        body = self.coverage(m.id, self.owner).get_json()
        self.assertEqual(['ISO 14001:2015'], body['standards'])
        self.assertEqual([], body['phases'][0]['missing'])
        oi = outline_of('ISO 9001:2026', 'ISO 45001:2018')
        m2 = self.make_manual(form=self.form(target_isos=['ISO 9001:2026', 'ISO 45001:2018']),
                              phase1_markdown=make_markdown(oi, 1, drop={'5.4'}))
        body = self.coverage(m2.id, self.owner).get_json()
        self.assertEqual(['5.4'], [x['manual_no'] for x in body['phases'][0]['missing']])

    def test_no_internal_information_and_generic_failure(self):
        m = self.make_manual(phase1_markdown='# 4. 조직 상황')
        text = self.coverage(m.id, self.owner).get_data(as_text=True)
        for word in ('.json', 'api/standards', 'Traceback', 'configured_default'):
            self.assertNotIn(word, text, word)
        self.assertNotIn(os.sep + 'api' + os.sep, text)
        with patch('index.manual_coverage.outline_from_form_data', side_effect=RuntimeError('C:\\secret\\path.json')):
            r = self.coverage(m.id, self.owner)
        self.assertEqual(503, r.status_code)
        self.assertEqual('COVERAGE_UNAVAILABLE', r.get_json()['code'])
        self.assertNotIn('secret', r.get_data(as_text=True))

    def test_does_not_change_state_or_schema(self):
        m = self.make_manual(phase1_markdown='# 4. 조직 상황')
        before = (m.status, m.updated_at, m.retry_count)
        self.coverage(m.id, self.owner)
        db.session.expire_all()
        m = ManualGeneration.query.get(m.id)
        self.assertEqual(before, (m.status, m.updated_at, m.retry_count))


class CoverageSseTest(CoverageApiTestBase):
    def new_session(self, user=None, **extra):
        body = {'company_name': 'Buyer Co', 'industry': '제조업', 'main_product': '자동차 부품', 'employees': '50',
                'target_iso': 'ISO 9001:2015', 'issues': ['quality_defect']}
        body.update(extra)
        r = self.client.post('/api/iso-manual/session', json=body, headers=auth_headers(user or self.owner))
        self.assertEqual(201, r.status_code)
        return r.get_json()

    def generate(self, session, stream_fn, continue_from=None):
        url = '/api/generate-iso?manual_id=%s&stream_token=%s' % (session['manual_id'], session['stream_token'])
        if continue_from:
            url += '&continue_from=%s' % continue_from
        with patch('index.generate_iso_manual_stream', stream_fn) as mocked:
            resp = self.client.get(url)
            body = resp.get_data(as_text=True)
        return resp, body, mocked

    @staticmethod
    def stream_of(markdown, phase):
        def stream(form_data):
            # 토큰 단위로 나누고 개행은 SSE 규약대로 이스케이프
            for i in range(0, len(markdown), 40):
                yield 'data: ' + markdown[i:i + 40].replace('\n', '\\n') + '\n\n'
            yield 'data: [PHASE_COMPLETE:%d]\n\n' % phase
            yield 'data: [DONE]\n\n'
        return stream

    def manual(self, session):
        db.session.expire_all()
        return ManualGeneration.query.get(session['manual_id'])

    def test_event_order_after_phase_complete_before_done(self):
        o = outline_of('ISO 9001:2015')
        session = self.new_session()
        resp, body, _ = self.generate(session, self.stream_of(make_markdown(o, 1, drop={'4.3'}), 1))
        events = parse_sse(body)
        names = [n for n, _ in events]
        data = [d for _, d in events]
        i_complete = data.index('[PHASE_COMPLETE:1]')
        i_cov = names.index('coverage')
        i_done = data.index('[DONE]')
        self.assertLess(i_complete, i_cov)
        self.assertLess(i_cov, i_done)
        self.assertEqual(len(events) - 1, i_done)                      # DONE 이 마지막
        self.assertEqual(1, names.count('coverage'))
        cov = json.loads(data[i_cov])
        self.assertEqual(1, cov['phase'])
        self.assertEqual(['4.3'], [m['manual_no'] for m in cov['missing']])
        self.assertEqual(cov['required'] - 1, cov['found'])
        self.assertIn('event: coverage\ndata: {', body)
        self.assertEqual('phase_1_completed', self.manual(session).status)

    def test_coverage_event_is_invisible_to_onmessage(self):
        """named event 는 onmessage 로 전달되지 않는다: 기본(이름 없는) 이벤트만 이어 붙이면 저장본과 같다."""
        o = outline_of('ISO 9001:2015')
        md = make_markdown(o, 1)
        session = self.new_session()
        _, body, _ = self.generate(session, self.stream_of(md, 1))
        messages = [d for n, d in parse_sse(body) if n is None]
        content = ''.join(d for d in messages if not d.startswith('[PHASE_COMPLETE:') and d != '[DONE]')
        self.assertEqual(md, content.replace('\\n', '\n'))
        self.assertNotIn('"required"', ''.join(messages))
        self.assertEqual(md, self.manual(session).phase1_markdown)

    def test_complete_markdown_reports_no_missing(self):
        o = outline_of('ISO 9001:2015')
        session = self.new_session()
        _, body, _ = self.generate(session, self.stream_of(make_markdown(o, 1), 1))
        cov = json.loads(next(d for n, d in parse_sse(body) if n == 'coverage'))
        self.assertEqual([], cov['missing'])
        self.assertEqual(cov['required'], cov['found'])
        self.assertTrue(cov['ok'])

    def test_later_phase_uses_its_own_range(self):
        o = outline_of('ISO 9001:2015')
        session = self.new_session()
        self.generate(session, self.stream_of(make_markdown(o, 1), 1))
        resp, body, _ = self.generate(session, self.stream_of(make_markdown(o, 2, drop={'7.5'}), 2), continue_from=7)
        cov = json.loads(next(d for n, d in parse_sse(body) if n == 'coverage'))
        self.assertEqual(2, cov['phase'])
        self.assertEqual(['7.5'], [m['manual_no'] for m in cov['missing']])

    def test_no_coverage_event_on_failure(self):
        session = self.new_session()

        def failing(form_data):
            yield 'data: 일부\n\n'
            yield 'data: [ERROR] OpenAI API 오류 (500)\n\n'
            yield 'data: [DONE]\n\n'
        _, body, _ = self.generate(session, failing)
        self.assertNotIn('event: coverage', body)
        self.assertEqual('phase_1_failed', self.manual(session).status)

    def test_check_failure_does_not_break_generation(self):
        session = self.new_session()
        with patch('index._manual_coverage_outline', side_effect=RuntimeError('boom')):
            _, body, _ = self.generate(session, self.stream_of('# 4. 조직 상황\n\n본문', 1))
        events = parse_sse(body)
        self.assertNotIn('coverage', [n for n, _ in events])
        data = [d for _, d in events]
        self.assertLess(data.index('[PHASE_COMPLETE:1]'), data.index('[DONE]'))
        m = self.manual(session)
        self.assertEqual('phase_1_completed', m.status)
        self.assertEqual('# 4. 조직 상황\n\n본문', m.phase1_markdown)

    def test_replay_of_saved_phase_also_sends_coverage_without_llm(self):
        o = outline_of('ISO 9001:2015')
        session = self.new_session()
        self.generate(session, self.stream_of(make_markdown(o, 1, drop={'6.2'}), 1))

        def must_not_run(form_data):
            raise AssertionError('LLM 이 다시 호출되면 안 된다')
            yield  # pragma: no cover
        _, body, _ = self.generate(session, must_not_run)
        events = parse_sse(body)
        names = [n for n, _ in events]
        data = [d for _, d in events]
        self.assertLess(data.index('[PHASE_COMPLETE:1]'), names.index('coverage'))
        self.assertLess(names.index('coverage'), data.index('[DONE]'))
        cov = json.loads(data[names.index('coverage')])
        self.assertEqual(['6.2'], [m['manual_no'] for m in cov['missing']])

    def test_sse_and_api_agree(self):
        o = outline_of('ISO 9001:2015')
        session = self.new_session()
        _, body, _ = self.generate(session, self.stream_of(make_markdown(o, 1, drop={'4.4', '6.3'}), 1))
        sse = json.loads(next(d for n, d in parse_sse(body) if n == 'coverage'))
        api = self.coverage(session['manual_id'], self.owner).get_json()['phases'][0]
        for key in ('phase', 'required', 'found', 'missing', 'keyword_missing', 'doc_no_issues', 'mermaid_warnings'):
            self.assertEqual(sse[key], api[key], key)


if __name__ == '__main__':
    unittest.main()
