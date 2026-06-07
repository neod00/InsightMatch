import sys
import os

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from models import Consultant
import json

# 예산 문자열 → 숫자(만원) 파싱
_BUDGET_TABLE = {
    '1000만원 미만': (0, 1000),
    '1000~3000만원': (1000, 3000),
    '3000~5000만원': (3000, 5000),
    '5000만원 이상': (5000, 99999),
    'unknown': None,
}

def _parse_budget(s):
    """예산 문자열을 (min, max) 만원 튜플로 변환. 인식 불가면 None."""
    return _BUDGET_TABLE.get(s)


class MatchingService:
    def match_consultants(self, criteria):
        """
        기업-컨설턴트 매칭 알고리즘 (v2 — 명세 기반 가중치)

        가중치 (총 100점):
          ISO 자격   30pt  — 요청 ISO와 컨설턴트 전문 분야 일치율
          지역       20pt  — 기업-컨설턴트 지역 일치
          산업 경험  20pt  — 업종 일치 여부
          평점/리뷰  15pt  — 평점·리뷰 수 기반
          예산       15pt  — 예산 범위 적합성

        보너스 (점수 외 가산, 상한 없음):
          verified   +10pt — 플랫폼 검증 배지
          프로젝트 유형 +5pt — 신규/전환 등 유형 일치

        정렬: 총점 내림차순, 상위 20건 반환
        폴백: 최고점 < 10 → trust_score 상위 3명 반환
        """
        target_industry   = criteria.get('industry', '')
        target_iso        = [iso['code'] for iso in criteria.get('recommended_iso', [])]
        target_project    = criteria.get('project_type', '')
        target_region     = criteria.get('region', '')
        target_timeline   = criteria.get('timeline', 'flexible')
        target_budget     = _parse_budget(criteria.get('budget', 'unknown'))

        # 전체 컨설턴트 대상 (verified 우선이지만 미검증도 포함 — 점수로 자연 분리)
        all_consultants = Consultant.query.filter(
            (Consultant.verified == True) | (Consultant.status == 'verified')
        ).all()

        if not all_consultants:
            return []

        scored = []

        for c in all_consultants:
            score = 0
            match_details = []

            # ── 1. ISO 자격 (30pt) ─────────────────────────────────────
            try:
                c_iso = json.loads(c.iso_experience) if c.iso_experience else {}
            except (json.JSONDecodeError, TypeError):
                c_iso = {}

            iso_score = 0
            matched_iso = []
            for iso in target_iso:
                if iso in c_iso:
                    iso_score += 1
                    matched_iso.append(iso)

            if target_iso:
                score += (iso_score / len(target_iso)) * 30
                if matched_iso:
                    match_details.append(f"{', '.join(matched_iso)} 경험")

            # ── 2. 지역 (20pt) ─────────────────────────────────────────
            if target_region and c.regions:
                c_regions = [r.strip() for r in c.regions.split(',')]
                if target_region in c_regions:
                    score += 20
                    match_details.append(f"{target_region} 지역 활동")

            # ── 3. 산업 경험 (20pt) ────────────────────────────────────
            try:
                c_industries = json.loads(c.industry_experience) if c.industry_experience else []
            except (json.JSONDecodeError, TypeError):
                c_industries = []

            if self._industry_match(c_industries, target_industry):
                score += 20
                match_details.append(f"{target_industry} 분야 전문")
            elif c.specialty and target_industry and target_industry in c.specialty:
                score += 10
                match_details.append(f"{target_industry} 관련 경험")

            # ── 4. 평점·리뷰 (15pt) ────────────────────────────────────
            rating  = c.rating  or 0
            reviews = c.reviews or 0

            if rating >= 4.8:
                score += 9
            elif rating >= 4.5:
                score += 6
            elif rating >= 4.0:
                score += 3

            if reviews >= 30:
                score += 6
            elif reviews >= 10:
                score += 4
            elif reviews >= 1:
                score += 2

            # ── 5. 예산 (15pt) ─────────────────────────────────────────
            if target_budget:
                try:
                    c_fee = json.loads(c.fee_range) if hasattr(c, 'fee_range') and c.fee_range else None
                except (json.JSONDecodeError, TypeError):
                    c_fee = None

                if c_fee and isinstance(c_fee, dict):
                    c_min = c_fee.get('min', 0)
                    c_max = c_fee.get('max', 99999)
                    b_min, b_max = target_budget
                    # 예산 범위가 겹치면 비례 점수
                    overlap_min = max(c_min, b_min)
                    overlap_max = min(c_max, b_max)
                    if overlap_max >= overlap_min:
                        overlap_ratio = (overlap_max - overlap_min) / max((b_max - b_min), 1)
                        score += round(min(overlap_ratio, 1.0) * 15)
                        match_details.append("예산 범위 적합")
                else:
                    # fee_range 미등록 컨설턴트 — 중간 점수 부여
                    score += 7

            # ── 보너스: verified (+10pt) ───────────────────────────────
            if c.verified:
                score += 10

            # ── 보너스: 프로젝트 유형 (+5pt) ──────────────────────────
            try:
                c_projects = json.loads(c.project_types) if c.project_types else []
            except (json.JSONDecodeError, TypeError):
                c_projects = []

            if target_project and target_project in c_projects:
                score += 5
                match_details.append(f"{target_project} 프로젝트 경험")

            # ── 타임라인 긴급 페널티/보너스 ───────────────────────────
            if target_timeline in ('urgent', '1month') and rating < 4.5:
                score -= 5  # 긴급 요청에 낮은 평점 컨설턴트 하향

            scored.append({
                'consultant': c,
                'score': score,
                'match_details': match_details,
            })

        scored.sort(key=lambda x: x['score'], reverse=True)

        # 폴백: 최고 점수가 너무 낮으면 trust_score 상위 3명
        if not scored or scored[0]['score'] < 10:
            top = sorted(all_consultants, key=lambda x: x.trust_score or 0, reverse=True)[:3]
            return [self._format(c, [], min(int((c.trust_score or 0) * 0.6 + 30), 80)) for c in top]

        return [
            self._format(item['consultant'], item['match_details'], round(item['score']))
            for item in scored[:20]
        ]

    def _format(self, c, match_details, score):
        reason = match_details[0] if match_details else (c.match_reason or 'ISO 전문 컨설턴트')
        return {
            'id':              c.id,
            'name':            c.name,
            'avatar':          c.avatar,
            'specialty':       c.specialty,
            'experience':      c.experience,
            'rating':          c.rating,
            'reviews':         c.reviews,
            'matchReason':     reason,
            'matchScore':      score,
            'verified':        c.verified,
            'trustScore':      c.trust_score,
            'profileImageUrl': c.profile_image_url,
            'bio':             c.bio,
            'companyName':     c.company_name,
        }

    def _industry_match(self, consultant_industries, target):
        if not target:
            return False
        if target in consultant_industries:
            return True
        for ind in consultant_industries:
            if target in ind or ind in target:
                return True
        return False
