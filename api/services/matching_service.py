import sys
import os

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from models import Consultant
import json
import re

# ============================================================
# 매칭 가중치 (합 = MAX_BASE_SCORE = 100)
# ============================================================
# 숫자를 코드 곳곳에 흩어 두면 재조정할 때마다 전부 찾아다녀야 하고,
# 합이 100 이 아닌 채로 배포되는 사고(아래 "예산 15pt" 참조)를 눈치채지 못한다.
# 여기 한 곳만 고치면 전체 배분이 바뀌고, 합계는 테스트가 지킨다.
#
# ── 재분배 이력 ────────────────────────────────────────────────
# v2 배분: ISO 30 / 지역 20 / 산업 20 / 평점 15 / 예산 15 = 100
#   그런데 예산 15pt 는 한 번도 지급된 적이 없다. 계산식이
#   `hasattr(c, 'fee_range')` 를 보는데 Consultant 모델에 fee_range 컬럼이
#   존재하지 않아 항상 False 였다(=죽은 분기). 게다가 예산 문자열 파싱표는
#   '1000~3000만원' 같은 키를 기대하는데 설문 폼(index.html)이 실제로 보내는
#   값은 'under500' / '500-1000' / '1000-2000' / '2000+' 라 어느 것도 매칭되지
#   않는다. 즉 이중으로 죽어 있었다.
#   결과적으로 실질 만점이 85점인데 화면에는 "100점 만점"으로 표시되어,
#   기업 눈에는 모든 매칭이 실제보다 15% 나빠 보였다.
#
# v3 배분: 죽은 예산 항목을 제거하고 15pt 를 데이터가 실제로 존재하는
#   네 기준에 비례 재분배한다(각 × 100/85 후 정수 보정).
#     ISO   30 → 35   지역 20 → 24   산업 20 → 24   평점 15 → 17
#   순위에 영향을 주지 않던 상수 15점이 사라지고, 변별력 있는 기준들의
#   점수 차가 그만큼 증폭된다.
#
# TODO(제품 결정 필요): 예산 매칭을 되살리려면 컨설턴트에게 요금 범위를
#   받아야 한다. 이는 (1) 등록 폼에 요금 범위 필드 추가, (2) 기존 컨설턴트
#   백필, (3) "요금을 기업에 공개할 것인가" 라는 정책 결정을 동반하므로
#   코드만으로 끝나지 않는다. 결정이 나면 WEIGHT_BUDGET 을 새로 정의하고
#   위 네 값을 다시 줄이면 된다.
WEIGHT_ISO      = 35   # 요청 ISO 와 컨설턴트 보유 규격의 일치율
WEIGHT_REGION   = 24   # 기업 소재지 - 컨설턴트 활동 지역
WEIGHT_INDUSTRY = 24   # 업종 경험
WEIGHT_RATING   = 17   # 평점·리뷰 (실적 신호)

MAX_BASE_SCORE = 100   # 위 가중치의 합. tests 가 이 항등식을 검증한다.

# 산업이 '정확히' 일치하지 않고 specialty 문자열에만 걸릴 때의 부분 점수 비율
INDUSTRY_PARTIAL_RATIO = 0.5

# 평점 블록(WEIGHT_RATING) 내부 배분 — 합 1.0
RATING_SUB_SCORE   = 0.6   # 평점 값 자체
RATING_SUB_REVIEWS = 0.4   # 리뷰 수 (= 그 평점을 얼마나 믿을 수 있는가)

# 리뷰가 0건일 때 평점 블록에 주는 비율.
# "평가 없음"과 "낮은 평가"는 다르다 — 아래 _rating_block() 주석 참조.
NO_REVIEW_NEUTRAL_RATIO = 0.5

# ── 보너스 (기본 100점과 별개로 가산) ──
BONUS_VERIFIED     = 10   # 플랫폼이 신원·자격을 확인한 컨설턴트
BONUS_PROJECT_TYPE = 5    # 신규/전환 등 프로젝트 유형 경험 일치

# 긴급 요청에 '검증된 저평점' 컨설턴트를 올리지 않기 위한 페널티
PENALTY_URGENT_LOW_RATING = 5
URGENT_TIMELINES = ('urgent', '1month')
URGENT_RATING_FLOOR = 4.5

# 컨설턴트가 "전국 가능"을 선택한 경우의 값 (consultant_register.html)
REGION_NATIONWIDE = '전국'

# 폴백(최고점이 이 값 미만이면 trust_score 상위로 대체)
FALLBACK_SCORE_THRESHOLD = 10
FALLBACK_RESULT_LIMIT = 3
RESULT_LIMIT = 20


# ============================================================
# ISO 규격 표기 정규화
# ============================================================
# 같은 규격을 세 곳이 서로 다르게 적고 있어서, 문자열 그대로 비교하면
# 최대 가중치(35pt)가 통째로 0점이 된다. 실측 결과 12명 풀 / 대표 요청 3건
# 전부에서 ISO 점수가 0이었다.
#
#   기업 설문(index.html)          : 'ISO 9001:2015', 'ISO/IEC 27001:2022'
#   컨설턴트 등록 폼               : '9001', '27001', 'IATF16949'
#   시드 데이터(/api/admin/seed)   : 'ISO 9001', 'IATF 16949'
#
# 스키마를 바꾸거나 기존 데이터를 백필하지 않고, 조회 시점에 양쪽을 같은
# 정규형(숫자 코드)으로 접어서 비교한다. 이미 저장된 데이터에도 소급 적용된다.
_ISO_NUMBER_RE = re.compile(r'(\d{4,5})')


def _normalize_iso(code):
    """ISO/IATF 규격 표기를 비교용 정규형(숫자 코드 문자열)으로 변환.

    'ISO 9001:2015' / 'ISO 9001' / '9001'        → '9001'
    'ISO/IEC 27001:2022' / '27001'               → '27001'
    'ISO 14064-1:2018' / '14064'                 → '14064'  (파트 번호는 버린다)
    'IATF 16949:2016' / 'IATF16949'              → '16949'
    숫자를 못 찾으면 소문자 원문 (자기 자신끼리는 여전히 일치한다)
    """
    if not code:
        return ''
    # ':2015' 같은 발행 연도를 먼저 떼어낸다. 연도도 4자리라 숫자 추출보다 앞서야 한다.
    head = str(code).split(':')[0]
    match = _ISO_NUMBER_RE.search(head)
    return match.group(1) if match else head.strip().lower()


def _rating_ratio(rating):
    """평점 → 0.0~1.0 비율 (기존 9/6/3 단계 배분을 비율로 옮긴 것)."""
    if rating >= 4.8:
        return 1.0
    if rating >= 4.5:
        return 2 / 3
    if rating >= 4.0:
        return 1 / 3
    return 0.0


def _reviews_ratio(reviews):
    """리뷰 수 → 0.0~1.0 비율 (기존 6/4/2 단계 배분을 비율로 옮긴 것)."""
    if reviews >= 30:
        return 1.0
    if reviews >= 10:
        return 2 / 3
    if reviews >= 1:
        return 1 / 3
    return 0.0


def _rating_block(rating, reviews):
    """평점 블록 점수 (0 ~ WEIGHT_RATING).

    리뷰가 0건이면 평점 값을 쓰지 않고 중립값(WEIGHT_RATING 의 절반)을 준다.

    이유:
      1) 리뷰 0건은 "나쁘다"가 아니라 "아직 모른다"이다. 0점을 주는 것은
         '3.9점짜리 컨설턴트와 같다'고 단정하는 것인데, 데이터가 그렇게
         말한 적이 없다.
      2) 신규 컨설턴트를 영구히 하위에 두면 첫 프로젝트를 못 받고,
         프로젝트가 없으니 리뷰도 못 받는다(콜드 스타트 교착). 공급 측이
         굶으면 매칭 플랫폼 자체가 성립하지 않는다.
      3) 그렇다고 만점을 주면 리뷰 56건 5.0점 베테랑과 동점이 되어 기업을
         오해시킨다. 그래서 '절반'이다 — 무정보 상태의 중립 사전확률.
    TODO(L1): 리뷰가 쌓이면 절반 고정 대신 풀 평균으로 수축(shrinkage)시키는
      편이 정확하다. 지금은 플랫폼 전체 리뷰가 사실상 0건이라 평균이 무의미하다.
    """
    if reviews <= 0:
        return WEIGHT_RATING * NO_REVIEW_NEUTRAL_RATIO
    return WEIGHT_RATING * (
        RATING_SUB_SCORE * _rating_ratio(rating)
        + RATING_SUB_REVIEWS * _reviews_ratio(reviews)
    )


class MatchingService:
    def match_consultants(self, criteria):
        """
        기업-컨설턴트 매칭 알고리즘 (v3 — 죽은 예산 항목 제거 + 가중치 상수화)

        기본 점수 (합 100점, 상단 상수로 정의):
          ISO 자격   WEIGHT_ISO      — 요청 ISO 와 컨설턴트 보유 규격 일치율
          지역       WEIGHT_REGION   — 지역 일치 ('전국' 선택자는 어디든 일치)
          산업 경험  WEIGHT_INDUSTRY — 업종 일치 (부분 일치는 절반)
          평점/리뷰  WEIGHT_RATING   — 리뷰 0건이면 중립값

        보너스 (기본 점수 외 가산):
          verified      +BONUS_VERIFIED
          프로젝트 유형 +BONUS_PROJECT_TYPE

        표시 점수는 0~100 으로 자른다. 정렬은 자르기 전 원점수로 하므로
        상위권의 변별력은 유지된다. (115/100 같은 표시는 85점을 100점 만점으로
        보여주던 것과 같은 종류의 거짓말이다.)

        정렬: 총점 내림차순, 상위 RESULT_LIMIT 건 반환
        폴백: 최고점 < FALLBACK_SCORE_THRESHOLD → trust_score 상위 3명
        """
        target_industry   = criteria.get('industry', '')
        target_iso        = {
            _normalize_iso(iso.get('code'))
            for iso in criteria.get('recommended_iso', [])
            if iso and iso.get('code')
        }
        target_iso.discard('')
        target_project    = criteria.get('project_type', '')
        target_region     = criteria.get('region', '')
        target_timeline   = criteria.get('timeline', 'flexible')

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

            # ── 1. ISO 자격 (WEIGHT_ISO) ───────────────────────────────
            try:
                c_iso_raw = json.loads(c.iso_experience) if c.iso_experience else {}
            except (json.JSONDecodeError, TypeError):
                c_iso_raw = {}

            # 저장된 표기가 무엇이든 정규형으로 접어서 비교한다(위 _normalize_iso 주석).
            c_iso = {_normalize_iso(key): key for key in c_iso_raw}

            matched_iso = [target for target in target_iso if target in c_iso]

            if target_iso:
                score += (len(matched_iso) / len(target_iso)) * WEIGHT_ISO
                if matched_iso:
                    # 화면에는 컨설턴트가 실제로 등록한 원래 표기를 보여준다
                    # (정규화는 비교용이지 표시용이 아니다).
                    labels = [c_iso[code] for code in matched_iso]
                    match_details.append(f"{', '.join(labels)} 경험")

            # ── 2. 지역 (WEIGHT_REGION) ────────────────────────────────
            if target_region and c.regions:
                c_regions = {r.strip() for r in c.regions.split(',') if r.strip()}
                # '전국 가능'을 선택한 컨설턴트는 특정 지역 요청에도 일치한다.
                # (이 처리가 없으면 전국만 체크한 컨설턴트는 어떤 지역 요청에서도
                #  영구히 0점이었다 — 등록 폼의 첫 번째 선택지인데도)
                if target_region in c_regions:
                    score += WEIGHT_REGION
                    match_details.append(f"{target_region} 지역 활동")
                elif REGION_NATIONWIDE in c_regions:
                    score += WEIGHT_REGION
                    match_details.append("전국 활동 가능")

            # ── 3. 산업 경험 (WEIGHT_INDUSTRY) ─────────────────────────
            try:
                c_industries = json.loads(c.industry_experience) if c.industry_experience else []
            except (json.JSONDecodeError, TypeError):
                c_industries = []

            if self._industry_match(c_industries, target_industry):
                score += WEIGHT_INDUSTRY
                match_details.append(f"{target_industry} 분야 전문")
            elif c.specialty and target_industry and target_industry in c.specialty:
                score += WEIGHT_INDUSTRY * INDUSTRY_PARTIAL_RATIO
                match_details.append(f"{target_industry} 관련 경험")

            # ── 4. 평점·리뷰 (WEIGHT_RATING) ───────────────────────────
            rating  = c.rating  or 0
            reviews = c.reviews or 0
            score += _rating_block(rating, reviews)

            # ── 5. 예산 — 제거됨 ───────────────────────────────────────
            # Consultant 에 요금 범위 컬럼이 없어 이 분기는 단 한 번도 점수를
            # 준 적이 없다. 상단 "재분배 이력" 주석 참조.

            # ── 보너스: verified ───────────────────────────────────────
            if c.verified:
                score += BONUS_VERIFIED

            # ── 보너스: 프로젝트 유형 ──────────────────────────────────
            try:
                c_projects = json.loads(c.project_types) if c.project_types else []
            except (json.JSONDecodeError, TypeError):
                c_projects = []

            if target_project and target_project in c_projects:
                score += BONUS_PROJECT_TYPE
                match_details.append(f"{target_project} 프로젝트 경험")

            # ── 타임라인 긴급 페널티 ───────────────────────────────────
            # 리뷰가 있는 컨설턴트에게만 적용한다. 이 페널티의 목적은 '실적상
            # 평가가 낮은' 사람을 긴급 건에서 내리는 것이지, 아직 평가가 없는
            # 사람을 내리는 것이 아니다. (rating 시드를 0.0 으로 통일한 뒤에는
            # 조건을 걸지 않으면 신규 컨설턴트가 전부 -5 를 맞는다.)
            if target_timeline in URGENT_TIMELINES and reviews > 0 and rating < URGENT_RATING_FLOOR:
                score -= PENALTY_URGENT_LOW_RATING

            scored.append({
                'consultant': c,
                'score': score,
                'match_details': match_details,
            })

        scored.sort(key=lambda x: x['score'], reverse=True)

        # 폴백: 최고 점수가 너무 낮으면 trust_score 상위 3명
        if not scored or scored[0]['score'] < FALLBACK_SCORE_THRESHOLD:
            top = sorted(all_consultants, key=lambda x: x.trust_score or 0, reverse=True)[:FALLBACK_RESULT_LIMIT]
            return [self._format(c, [], min(int((c.trust_score or 0) * 0.6 + 30), 80)) for c in top]

        return [
            self._format(item['consultant'], item['match_details'], self._display_score(item['score']))
            for item in scored[:RESULT_LIMIT]
        ]

    def _display_score(self, raw_score):
        """표시용 점수 — 0~100 으로 자른다.

        보너스(verified/프로젝트 유형)까지 더하면 원점수가 100을 넘을 수 있는데,
        "100점 만점"이라 안내하면서 115점을 보여주면 점수 체계 자체를 못 믿게 된다.
        정렬은 자르기 전 원점수로 하므로 순위는 그대로다.
        """
        return max(0, min(round(raw_score), MAX_BASE_SCORE))

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
