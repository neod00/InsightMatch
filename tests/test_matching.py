import unittest
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

from index import app, db, NEW_CONSULTANT_RATING
from models import Consultant
from services import matching_service as matching_module
from services.matching_service import MatchingService


def make_consultant(**kwargs):
    defaults = dict(
        specialty='General',
        match_reason='',
        iso_experience=json.dumps({}),
        industry_experience=json.dumps([]),
        project_types=json.dumps([]),
        verified=True,
        trust_score=70.0,
        rating=4.0,
        reviews=0,
        regions='',
    )
    defaults.update(kwargs)
    return Consultant(**defaults)


class TestMatchingService(unittest.TestCase):

    def setUp(self):
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['TESTING'] = True
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        self.svc = MatchingService()

        # Expert A: ISO 9001/14001, 제조업, 서울, 검증, 우수 평점
        self.c_a = make_consultant(
            name='Expert A',
            iso_experience=json.dumps({'9001': True, '14001': True}),
            industry_experience=json.dumps(['Manufacturing', 'Chemical']),
            project_types=json.dumps(['New', 'Transition']),
            verified=True,
            trust_score=80.0,
            rating=4.8,
            reviews=50,
            regions='서울,경기',
        )
        # Expert B: ISO 27001, IT, 부산, 검증됨, 낮은 평점
        self.c_b = make_consultant(
            name='Expert B',
            iso_experience=json.dumps({'27001': True}),
            industry_experience=json.dumps(['IT/Software']),
            project_types=json.dumps(['New']),
            verified=True,
            trust_score=40.0,
            rating=4.0,
            reviews=5,
            regions='부산',
        )
        db.session.add_all([self.c_a, self.c_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ------------------------------------------------------------------
    # 1. 완전 매칭: ISO + 산업 + 프로젝트 모두 일치
    # ------------------------------------------------------------------
    def test_perfect_match_ranks_first(self):
        """ISO·산업·프로젝트 전부 일치하는 컨설턴트가 1순위여야 한다."""
        criteria = {
            'industry': 'Manufacturing',
            'recommended_iso': [{'code': '9001'}],
            'project_type': 'New',
            'timeline': 'flexible',
        }
        results = self.svc.match_consultants(criteria)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['name'], 'Expert A')

    def test_perfect_match_score_above_threshold(self):
        """완전 매칭 점수는 70점 이상이어야 한다."""
        criteria = {
            'industry': 'Manufacturing',
            'recommended_iso': [{'code': '9001'}],
            'project_type': 'New',
            'timeline': 'flexible',
        }
        results = self.svc.match_consultants(criteria)
        self.assertGreaterEqual(results[0]['matchScore'], 70)

    # ------------------------------------------------------------------
    # 2. 부분 매칭: 다른 ISO·산업으로 B가 1순위
    # ------------------------------------------------------------------
    def test_partial_match_correct_winner(self):
        """ISO 27001 + IT 조건에서는 Expert B가 1순위여야 한다."""
        criteria = {
            'industry': 'IT/Software',
            'recommended_iso': [{'code': '27001'}],
            'timeline': 'flexible',
        }
        results = self.svc.match_consultants(criteria)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['name'], 'Expert B')

    # ------------------------------------------------------------------
    # 3. 점수 정렬 보장
    # ------------------------------------------------------------------
    def test_results_sorted_by_score_descending(self):
        """결과 목록은 matchScore 내림차순으로 정렬돼야 한다."""
        criteria = {'industry': 'Manufacturing', 'timeline': 'flexible'}
        results = self.svc.match_consultants(criteria)
        scores = [r['matchScore'] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    # ------------------------------------------------------------------
    # 4. verified 필터
    # ------------------------------------------------------------------
    def test_high_rating_consultant_preferred(self):
        """평점·리뷰가 높은 Expert A가 조건 없을 때 Expert B보다 높은 순위여야 한다."""
        criteria = {'timeline': 'flexible'}
        results = self.svc.match_consultants(criteria)
        idx_a = next(i for i, r in enumerate(results) if r['name'] == 'Expert A')
        idx_b = next(i for i, r in enumerate(results) if r['name'] == 'Expert B')
        self.assertLess(idx_a, idx_b)

    # ------------------------------------------------------------------
    # 5. 지역 매칭
    # ------------------------------------------------------------------
    def test_region_match_adds_score(self):
        """지역이 일치하면 그렇지 않을 때보다 점수가 높아야 한다."""
        base = {'industry': 'Manufacturing', 'recommended_iso': [{'code': '9001'}], 'timeline': 'flexible'}

        without_region = self.svc.match_consultants(base)
        score_without = next(r['matchScore'] for r in without_region if r['name'] == 'Expert A')

        with_region = self.svc.match_consultants({**base, 'region': '서울'})
        score_with = next(r['matchScore'] for r in with_region if r['name'] == 'Expert A')

        self.assertGreater(score_with, score_without)

    # ------------------------------------------------------------------
    # 6. 응답 필드 완전성
    # ------------------------------------------------------------------
    def test_response_fields_present(self):
        """모든 필수 필드가 응답에 포함돼야 한다."""
        criteria = {'timeline': 'flexible'}
        results = self.svc.match_consultants(criteria)
        required = {'id', 'name', 'matchScore', 'matchReason', 'verified', 'trustScore'}
        for r in results:
            missing = required - r.keys()
            self.assertFalse(missing, f"필드 누락: {missing} in {r['name']}")

    # ------------------------------------------------------------------
    # 7. 빈 조건 — 폴백 동작
    # ------------------------------------------------------------------
    def test_empty_criteria_returns_results(self):
        """조건이 없어도 결과가 반환돼야 한다 (폴백)."""
        results = self.svc.match_consultants({'timeline': 'flexible'})
        self.assertGreater(len(results), 0)

    # ------------------------------------------------------------------
    # 8. matchScore 범위
    # ------------------------------------------------------------------
    def test_match_score_in_valid_range(self):
        """matchScore는 0~100 사이여야 한다."""
        criteria = {'industry': 'Manufacturing', 'recommended_iso': [{'code': '9001'}], 'timeline': 'flexible'}
        results = self.svc.match_consultants(criteria)
        for r in results:
            self.assertGreaterEqual(r['matchScore'], 0, f"{r['name']} 점수 음수")
            self.assertLessEqual(r['matchScore'], 100, f"{r['name']} 점수 100 초과")

    # ------------------------------------------------------------------
    # 9. 가중치 정합성 — 배분표의 합이 100인가
    # ------------------------------------------------------------------
    def test_weights_sum_to_max_base_score(self):
        """가중치 합은 정확히 100이어야 한다.

        이 검증이 없어서 '예산 15pt'가 죽은 채로 남아 실질 만점이 85점인데
        화면에는 100점 만점으로 표시되는 상태가 오래 유지됐다.
        가중치를 조정할 때 이 테스트가 합계를 지킨다.
        """
        total = (
            matching_module.WEIGHT_ISO
            + matching_module.WEIGHT_REGION
            + matching_module.WEIGHT_INDUSTRY
            + matching_module.WEIGHT_RATING
        )
        self.assertEqual(total, matching_module.MAX_BASE_SCORE)
        self.assertEqual(total, 100)

    def test_rating_subweights_sum_to_one(self):
        """평점 블록 내부 배분(평점:리뷰)의 합은 1.0이어야 한다."""
        self.assertAlmostEqual(
            matching_module.RATING_SUB_SCORE + matching_module.RATING_SUB_REVIEWS,
            1.0,
        )

    def test_no_dead_budget_criterion_remains(self):
        """존재하지 않는 컬럼(fee_range)을 참조하는 죽은 분기가 남아 있으면 안 된다."""
        self.assertFalse(hasattr(Consultant, 'fee_range'))
        source_path = os.path.join(
            os.path.dirname(__file__), '..', 'api', 'services', 'matching_service.py'
        )
        with open(source_path, encoding='utf-8') as fh:
            source = fh.read()
        # 주석(설명·이력)에는 남아 있어도 되지만 실행 코드에는 없어야 한다.
        code_lines = [
            line for line in source.splitlines()
            if 'fee_range' in line and not line.strip().startswith('#')
        ]
        self.assertEqual(code_lines, [], f"죽은 fee_range 분기가 남아 있다: {code_lines}")

    # ------------------------------------------------------------------
    # 10. ISO 표기 정규화 — 세 vocabulary 가 서로 일치해야 한다
    # ------------------------------------------------------------------
    def test_iso_code_normalization_covers_all_three_vocabularies(self):
        """설문 폼·등록 폼·시드 데이터가 같은 규격을 다르게 적어도 같게 취급해야 한다."""
        normalize = matching_module._normalize_iso
        # 기업 설문(index.html) / 컨설턴트 등록 폼 / 시드 데이터
        self.assertEqual(normalize('ISO 9001:2015'), normalize('9001'))
        self.assertEqual(normalize('ISO 9001:2015'), normalize('ISO 9001'))
        self.assertEqual(normalize('ISO/IEC 27001:2022'), normalize('27001'))
        self.assertEqual(normalize('IATF 16949:2016'), normalize('IATF16949'))
        self.assertEqual(normalize('ISO 14064-1:2018'), normalize('14064'))
        # 다른 규격끼리는 섞이면 안 된다
        self.assertNotEqual(normalize('ISO 27001:2022'), normalize('ISO 27017:2015'))
        self.assertNotEqual(normalize('ISO 9001:2015'), normalize('ISO 14001:2015'))

    def test_full_iso_code_from_survey_matches_bare_code_from_register_form(self):
        """설문이 'ISO 9001:2015'를 보내도 '9001'로 등록한 컨설턴트가 ISO 점수를 받아야 한다.

        이 정규화가 없으면 최대 가중치(ISO)가 실제 컨설턴트 전원에게 0점이었다.
        """
        criteria = {'recommended_iso': [{'code': 'ISO 9001:2015'}], 'timeline': 'flexible'}
        results = self.svc.match_consultants(criteria)
        score_a = next(r['matchScore'] for r in results if r['name'] == 'Expert A')

        no_iso = self.svc.match_consultants({'timeline': 'flexible'})
        base_a = next(r['matchScore'] for r in no_iso if r['name'] == 'Expert A')

        self.assertGreater(score_a, base_a, 'ISO 표기가 달라 점수가 0점으로 죽었다')

    # ------------------------------------------------------------------
    # 11. 지역 — '전국' 선택자는 어떤 지역 요청에도 걸려야 한다
    # ------------------------------------------------------------------
    def test_nationwide_consultant_matches_any_region(self):
        """'전국 가능'을 선택한 컨설턴트가 특정 지역 요청에서 0점이 되면 안 된다."""
        nationwide = make_consultant(name='Nationwide', regions='전국', rating=4.0, reviews=1)
        db.session.add(nationwide)
        db.session.commit()

        results = self.svc.match_consultants({'region': '제주', 'timeline': 'flexible'})
        scored = next(r for r in results if r['name'] == 'Nationwide')
        unmatched = next(r for r in results if r['name'] == 'Expert B')  # regions='부산'

        self.assertGreater(scored['matchScore'], unmatched['matchScore'])

    # ------------------------------------------------------------------
    # 12. 신규 컨설턴트 — "평가 없음"과 "낮은 평가"의 구분
    # ------------------------------------------------------------------
    def test_zero_review_consultant_is_not_penalised_by_rating_seed(self):
        """리뷰가 0건이면 rating 시드 값(0.0이든 5.0이든)이 점수에 영향을 주면 안 된다.

        가입 경로마다 rating 시드가 5.0/0.0으로 갈렸던 버그의 재발 방지.
        시드를 0.0으로 통일해도 신규 컨설턴트가 불리해지지 않는다는 근거이기도 하다.
        """
        seeded_zero = make_consultant(name='Zero Seed', rating=0.0, reviews=0, regions='')
        seeded_five = make_consultant(name='Five Seed', rating=5.0, reviews=0, regions='')
        db.session.add_all([seeded_zero, seeded_five])
        db.session.commit()

        results = self.svc.match_consultants({'timeline': 'flexible'})
        by_name = {r['name']: r['matchScore'] for r in results}
        self.assertEqual(by_name['Zero Seed'], by_name['Five Seed'])

    def test_unreviewed_consultant_scores_between_bad_and_good(self):
        """리뷰 0건은 중립 — 최하점도 만점도 아니어야 한다."""
        block = matching_module._rating_block
        neutral = block(NEW_CONSULTANT_RATING, 0)
        worst = block(3.0, 5)          # 리뷰는 있는데 평점이 낮은 경우
        best = block(5.0, 50)          # 평점·리뷰 모두 최상

        self.assertGreater(neutral, worst)
        self.assertLess(neutral, best)

    def test_urgent_penalty_does_not_apply_to_unreviewed_consultants(self):
        """긴급 요청 페널티는 '평가가 낮은' 사람에게만 — '평가가 없는' 사람에겐 아니다."""
        rookie = make_consultant(name='Rookie', rating=NEW_CONSULTANT_RATING, reviews=0, regions='')
        db.session.add(rookie)
        db.session.commit()

        flexible = self.svc.match_consultants({'timeline': 'flexible'})
        urgent = self.svc.match_consultants({'timeline': 'urgent'})

        score_flexible = next(r['matchScore'] for r in flexible if r['name'] == 'Rookie')
        score_urgent = next(r['matchScore'] for r in urgent if r['name'] == 'Rookie')
        self.assertEqual(score_flexible, score_urgent)

    # ------------------------------------------------------------------
    # 13. 표시 점수 상한 — "100점 만점"인데 100을 넘으면 안 된다
    # ------------------------------------------------------------------
    def test_display_score_never_exceeds_hundred_with_bonuses(self):
        """보너스까지 더해 원점수가 100을 넘어도 표시 점수는 100을 넘지 않는다."""
        criteria = {
            'industry': 'Manufacturing',
            'recommended_iso': [{'code': 'ISO 9001:2015'}, {'code': 'ISO 14001:2015'}],
            'region': '서울',
            'project_type': 'New',
            'timeline': 'flexible',
        }
        results = self.svc.match_consultants(criteria)
        top = next(r for r in results if r['name'] == 'Expert A')
        # 원점수는 100을 넘는 상황이어야 이 테스트가 의미가 있다
        raw = self.svc._display_score(999)
        self.assertEqual(raw, 100)
        self.assertLessEqual(top['matchScore'], 100)
        self.assertGreaterEqual(top['matchScore'], 0)

    # ------------------------------------------------------------------
    # 14. 매칭 결과가 화면이 실제로 읽는 필드를 담고 있어야 한다
    #     (BUG-E2E-002 전문성 태그 실종 / BUG-E2E-003 필터 결과 0명)
    # ------------------------------------------------------------------
    # script.js 가 읽는 키와 기대 형태:
    #   c.isoExperience      → 객체 (Object.keys(...).some(...)) — ISO 필터
    #   c.regions            → 문자열 ((c.regions||'').toLowerCase()) — 지역 필터
    #   c.industryExperience → 배열 (.some(...), [0] 로 태그 표시)
    #   c.roles              → 배열 (.includes('Lead Auditor')) — 심사원 태그
    FRONTEND_REQUIRED_FIELDS = {
        'isoExperience': dict,
        'industryExperience': list,
        'projectTypes': list,
        'roles': list,
        'regions': str,
    }

    def test_match_result_exposes_fields_the_result_page_reads(self):
        """매칭 결과 항목이 script.js 가 읽는 필드를 올바른 형태로 담아야 한다.

        이 필드들이 빠져 있으면 화면이 예외 없이 '조용히' 잘못 동작한다 —
        ISO/지역 필터는 전원을 탈락시켜 결과가 항상 0명이 되고(BUG-E2E-003),
        전문성 태그는 통째로 사라진다(BUG-E2E-002).
        """
        self.c_a.roles = json.dumps(['Lead Auditor', 'Trainer'])
        db.session.commit()

        results = self.svc.match_consultants({
            'industry': 'Manufacturing',
            'recommended_iso': [{'code': '9001'}],
            'region': '서울',
            'timeline': 'flexible',
        })
        top = next(r for r in results if r['name'] == 'Expert A')

        for field, expected_type in self.FRONTEND_REQUIRED_FIELDS.items():
            self.assertIn(field, top, f'{field} 가 매칭 결과에서 빠졌다')
            self.assertIsInstance(top[field], expected_type, f'{field} 형태가 다르다')

        # 값도 실제로 채워져야 한다 (빈 껍데기면 필터가 여전히 전원 탈락시킨다)
        self.assertIn('9001', top['isoExperience'])
        self.assertIn('Manufacturing', top['industryExperience'])
        self.assertIn('Lead Auditor', top['roles'])
        self.assertEqual(top['regions'], '서울,경기')

    def test_iso_and_region_filters_keep_matching_consultants(self):
        """script.js 의 ISO·지역 필터 로직을 그대로 재현해도 후보가 남아야 한다.

        필터 자체가 프론트에 있으므로, 서버 응답만으로 필터가 통과되는지를
        같은 규칙으로 흉내 내어 검증한다.
        """
        results = self.svc.match_consultants({
            'industry': 'Manufacturing',
            'recommended_iso': [{'code': '9001'}],
            'timeline': 'flexible',
        })

        # ISO 필터: Object.keys(c.isoExperience).some(k => k.includes('9001'))
        iso_survivors = [
            c for c in results
            if any('9001' in key for key in (c.get('isoExperience') or {}))
        ]
        self.assertTrue(iso_survivors, 'ISO 필터를 걸면 결과가 0명이 된다')

        # 지역 필터: (c.regions || '').toLowerCase().includes('서울')
        region_survivors = [
            c for c in results if '서울' in (c.get('regions') or '').lower()
        ]
        self.assertTrue(region_survivors, '지역 필터를 걸면 결과가 0명이 된다')

    def test_fallback_results_also_expose_frontend_fields(self):
        """폴백 경로(점수 미달 시 trust_score 상위)의 결과에도 같은 필드가 있어야 한다.

        폴백은 _format 을 따로 호출하므로, 여기만 빠뜨리면 '검색은 되는데
        필터만 걸면 0명' 이 특정 상황에서만 재현되는 형태로 남는다.
        """
        results = self.svc.match_consultants({'timeline': 'flexible'})
        self.assertTrue(results)
        for item in results:
            for field, expected_type in self.FRONTEND_REQUIRED_FIELDS.items():
                self.assertIn(field, item)
                self.assertIsInstance(item[field], expected_type)

    def test_broken_json_in_profile_does_not_kill_matching(self):
        """JSON 이 깨진 행이 있어도 매칭 전체가 죽으면 안 된다."""
        broken = make_consultant(name='Broken', iso_experience='{not json',
                                 industry_experience='oops', regions='서울')
        db.session.add(broken)
        db.session.commit()

        results = self.svc.match_consultants({
            'recommended_iso': [{'code': '9001'}], 'timeline': 'flexible'})
        item = next(r for r in results if r['name'] == 'Broken')
        self.assertEqual(item['isoExperience'], {})
        self.assertEqual(item['industryExperience'], [])

    # ------------------------------------------------------------------
    # 15. 매칭 사유 표기 (OBS-E2E-004)
    # ------------------------------------------------------------------
    def test_match_reason_shows_iso_prefix_instead_of_bare_code(self):
        """매칭 사유에 '27001 경험' 같은 내부 코드값이 그대로 나오면 안 된다."""
        results = self.svc.match_consultants({
            'recommended_iso': [{'code': '27001'}], 'timeline': 'flexible'})
        reason = next(r['matchReason'] for r in results if r['name'] == 'Expert B')
        self.assertEqual(reason, 'ISO 27001 경험')

    def test_match_reason_does_not_double_prefix_named_standards(self):
        """이미 'ISO 9001' / 'IATF 16949' 로 등록한 표기에 접두어가 또 붙으면 안 된다."""
        named = make_consultant(
            name='Named', iso_experience=json.dumps({'ISO 9001': True}), regions='서울')
        iatf = make_consultant(
            name='Iatf', iso_experience=json.dumps({'IATF 16949': True}), regions='서울')
        db.session.add_all([named, iatf])
        db.session.commit()

        results = self.svc.match_consultants({
            'recommended_iso': [{'code': '9001'}, {'code': 'IATF16949'}],
            'timeline': 'flexible'})
        by_name = {r['name']: r['matchReason'] for r in results}
        self.assertEqual(by_name['Named'], 'ISO 9001 경험')
        self.assertEqual(by_name['Iatf'], 'IATF 16949 경험')

    # ------------------------------------------------------------------
    # 16. 프로젝트 유형 보너스는 '살아 있는 채로' 남겨 둔다 (OBS-E2E-006)
    # ------------------------------------------------------------------
    def test_project_type_bonus_still_works_when_criteria_supplies_it(self):
        """criteria 에 project_type 이 실리면 보너스가 실제로 지급돼야 한다.

        메인 퍼널은 이 값을 채우지 않아 현재 점수 영향이 0 이지만, 채점 로직
        자체는 정상이라 지우지 않았다(설문에 질문 하나만 추가하면 되살아난다).
        여기서 죽어 버리면 '되살릴 수 있다'는 전제가 조용히 무너진다.
        """
        base = self.svc.match_consultants({
            'recommended_iso': [{'code': '9001'}], 'timeline': 'flexible'})
        with_type = self.svc.match_consultants({
            'recommended_iso': [{'code': '9001'}], 'project_type': 'New',
            'timeline': 'flexible'})

        score_base = next(r['matchScore'] for r in base if r['name'] == 'Expert A')
        score_bonus = next(r['matchScore'] for r in with_type if r['name'] == 'Expert A')
        self.assertEqual(score_bonus - score_base, matching_module.BONUS_PROJECT_TYPE)

    def test_project_type_bonus_is_outside_the_base_hundred(self):
        """보너스는 기본 100점 배분 밖에 있어야 한다.

        이 보너스를 WEIGHT_* 로 '재분배' 하면 기본 합이 105 가 되어
        test_weights_sum_to_max_base_score 항등식이 깨진다.
        """
        base_total = (
            matching_module.WEIGHT_ISO
            + matching_module.WEIGHT_REGION
            + matching_module.WEIGHT_INDUSTRY
            + matching_module.WEIGHT_RATING
        )
        self.assertEqual(base_total, matching_module.MAX_BASE_SCORE)
        self.assertNotIn(matching_module.BONUS_PROJECT_TYPE, [0])


if __name__ == '__main__':
    unittest.main()
