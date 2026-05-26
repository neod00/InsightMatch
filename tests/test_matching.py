import unittest
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

from index import app, db
from models import Consultant
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
    # 9. 예산 필터 (구현 후 활성화)
    # ------------------------------------------------------------------
    # def test_budget_filter(self):
    #     """예산 범위가 맞는 컨설턴트가 우선 추천돼야 한다."""
    #     pass


if __name__ == '__main__':
    unittest.main()
