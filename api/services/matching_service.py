import sys
import os

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from models import Consultant
import json
import re

def _display_iso_label(label):
    """매칭 사유에 노출할 ISO 규격 표기를 다듬는다.

    컨설턴트가 등록한 원본 표기를 그대로 쓰면 '27001 경험', '9001, 14001 경험'
    처럼 내부 코드값이 그대로 화면에 나온다. 숫자로 시작하는 표기에만
    'ISO ' 접두어를 붙이고, 이미 'ISO 9001' / 'IATF 16949' 처럼 규격명이 붙은
    표기는 건드리지 않는다(접두어가 두 번 붙는 것을 막는다).
    """
    text = (label or '').strip()
    if text and text[0].isdigit():
        return f'ISO {text}'
    return text


def _load_json(raw, default):
    """DB 의 JSON 문자열 컬럼을 안전하게 파싱한다.

    저장 형식이 깨진 행 하나 때문에 매칭 결과 전체가 500 으로 죽으면 안 된다.
    기대한 타입(dict/list)이 아니면 기본값으로 되돌린다.
    """
    if not raw:
        return default
    if isinstance(raw, type(default)):
        return raw
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


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

# ============================================================
# 신뢰도 가중 평점 (베이지안 수축) — L1-C2
# ============================================================
# 단순 평균을 쓰면 **리뷰 1건짜리 5.0 이 리뷰 20건짜리 4.8 을 이긴다.**
# 표본이 1이면 그 5.0 은 거의 정보가 없는데도 만점으로 취급되기 때문이다.
# 그 결과 매칭 상위가 신규 1건짜리로 뒤덮이고, 지인 리뷰 1건으로 상위 노출을
# 살 수 있게 된다. 그래서 중립값(사전값)을 섞어 표본이 적을수록 중립 쪽으로
# 끌어당긴다:
#
#     가중평점 = (사전가중치 × 중립값 + Σ평점) / (사전가중치 + 리뷰수)
#
# ── 사전가중치를 5 로 정한 근거 ──
# 단위가 '리뷰 건수' 다. W=5 는 "리뷰 5건이 쌓여야 자기 평균이 중립값과
# 반반이 된다" 는 뜻이다.
#   · 1~2 로 두면 1건짜리 5.0 이 거의 그대로 반영되어 수축의 의미가 없다.
#   · 20 이상으로 두면, 컨설턴트 1인당 연간 프로젝트가 한 자릿수인 이 시장에서는
#     아무도 사전값을 벗어나지 못한다. 평점 항목 17점이 사실상 전원 동점이 되어
#     리뷰를 모으는 의미가 사라진다.
#   · 5 는 컨설턴트가 대략 1년치 실적을 쌓으면 자기 평균이 사전값을 넘어서는
#     지점이다. 리뷰 수집 속도가 관측되면 이 값 하나만 다시 조정하면 된다.
RATING_PRIOR_WEIGHT = 5

# ── 사전값(중립값)을 4.0 고정으로 둔 근거 ──
# 배치 4(84dd46b)가 남긴 TODO 는 "풀 평균으로 수축" 이었다. 그러나 지금
# **플랫폼 전체 리뷰는 0건**이라 풀 평균이 아예 정의되지 않는다. 게다가 초기
# 몇 건 구간에서는 풀 평균 자체가 극도로 불안정하다 — 첫 리뷰가 5.0 이면 풀
# 평균이 5.0 이 되고, 그러면 사전값 = 최고점이 되어 수축이 전원을 만점 쪽으로
# 밀어 올린다. 수축이 방어하려던 바로 그 조작을 수축이 도와주는 꼴이 된다.
# 그래서 데이터가 쌓이기 전까지는 **고정 사전값**을 쓴다.
#
# 4.0 은 아래 평가 척도 [3.0, 5.0] 의 정확한 중점이고, 그 결과 리뷰 0건인
# 컨설턴트의 평점 비율이 정확히 0.5(무정보 중립)가 된다. 배치 4가 특수 분기로
# 박아 두었던 "리뷰 0건이면 WEIGHT_RATING 의 절반" 과 **같은 중립을 수식 하나로
# 표현한 것**이다 (아래 NO_REVIEW_NEUTRAL_RATIO 참조).
#
# TODO(운영): 플랫폼 누적 리뷰가 50건을 넘으면 이 고정값을 풀 평균으로 바꾸는
#   것을 재검토한다. 그때는 풀 평균이 "이 플랫폼의 보통 컨설턴트" 를 실제로
#   대표하게 된다.
RATING_PRIOR_VALUE = 4.0

# 평점을 0~1 비율로 옮길 때의 척도.
# 1.0~5.0 전체를 선형으로 펴지 않는 이유: B2B 용역 평가는 4~5점에 몰린다.
# 1.0 을 바닥으로 잡으면 실제 변별이 일어나는 4.0~5.0 구간이 전체 폭의 25%로
# 압축되어, 4.2 와 4.9 의 차이가 점수에 거의 반영되지 않는다.
# 3.0 이하는 "매칭상 최하" 로 동일 취급한다.
RATING_SCALE_FLOOR   = 3.0
RATING_SCALE_CEILING = 5.0

# 평점 블록(WEIGHT_RATING) 내부 배분 — 합 1.0
#
# v3 까지는 0.6 / 0.4 였다. 수축을 도입하면서 3:1 로 옮긴다.
# 리뷰 수는 이제 **수축 계수로 이미 반영**된다(리뷰가 많을수록 자기 평균이
# 그대로 살아난다). 여기서 리뷰 수에 다시 0.4 를 주면 같은 신호를 두 번 세는
# 것이고, 그러면 '리뷰 20건 · 평균 3.0' 이 '리뷰 0건' 보다 높은 점수를 받는
# 역전이 생긴다(실측으로 확인했다 — 아래 배분에서는 4.11 < 6.38 로 뒤집히지 않는다).
# 남은 0.25 는 "검증 가능한 실적의 두께" 라는, 품질과는 다른 별개의 신호다.
RATING_SUB_SCORE   = 0.75  # 신뢰도 가중 평점 (수축 적용)
RATING_SUB_REVIEWS = 0.25  # 리뷰 수 자체 (실적의 두께)

# 리뷰가 0건일 때 평점 블록에 주는 비율.
# **상수로 박은 값이 아니라 위 사전값에서 유도된 값이다.** 리뷰 0건은 수축
# 공식에서 n=0 인 경우이고, 그때 가중평점은 정확히 RATING_PRIOR_VALUE 가 된다.
# 즉 "중립" 의 정의가 집계 쪽과 매칭 쪽에 각각 존재하지 않고 한 곳뿐이다.
# (이 항등식은 tests/review_loop_test.py 가 지킨다)
NO_REVIEW_NEUTRAL_RATIO = None  # 아래 _rating_ratio 정의 후 계산한다

# ── 보너스 (기본 100점과 별개로 가산) ──
BONUS_VERIFIED     = 10   # 플랫폼이 신원·자격을 확인한 컨설턴트

# ⚠️ BONUS_PROJECT_TYPE 은 메인 매칭 퍼널에서 한 번도 지급된 적이 없다.
#    criteria['project_type'] 을 채워 주는 프론트엔드가 **하나도 없기 때문**이다:
#      · direct_match() (/api/match, 공개 설문의 본선 경로) 는 criteria 에
#        project_type 을 아예 넣지 않는다.
#      · /api/consultants 는 ?project_type= 쿼리를 읽지만 어떤 화면도 보내지 않는다.
#      · /api/consultants/recommend 는 intake_data['projectType'] 을 읽지만
#        공개 설문(index.html)이 프로젝트 유형을 묻지 않아 항상 빈 문자열이다.
#    (admin.html 의 projectTypes 사용은 컨설턴트가 등록한 값을 '표시'하는 것이지
#     매칭 입력이 아니다.)
#
#    죽은 '예산 15pt' 와 다른 점이 둘 있어서 같은 처방을 쓰지 않았다:
#      1) 예산은 기본 100점 **안에** 있던 가중치라, 빼면 실질 만점이 85점이 되는
#         구멍이 생겨 반드시 재분배해야 했다. 이 5점은 100점 **밖의 보너스**라
#         구멍이 생기지 않는다. 오히려 기본 가중치에 5점을 얹으면 합이 105가 되어
#         test_weights_sum_to_max_base_score 항등식이 깨진다.
#      2) 예산은 존재하지 않는 컬럼(fee_range)을 보는 되살릴 수 없는 코드였다.
#         이쪽은 컨설턴트 등록 폼이 project_types 를 이미 수집하고 채점 로직도
#         정상이라, 설문에 질문 하나만 추가하면 그대로 살아난다.
#    그래서 지우지 않고 남겨 둔다. 지금 상태의 점수 영향은 정확히 0 이다.
#
#    TODO(제품 결정 필요): 이 5점을 실제로 쓰려면 공개 설문(index.html)에
#      '신규 인증 / 전환 / 갱신' 질문을 추가하고 direct_match() 의 criteria 에
#      project_type 을 실어야 한다. UI 변경이라 별도 범위로 둔다.
BONUS_PROJECT_TYPE = 5    # 신규/전환 등 프로젝트 유형 경험 일치 (위 주석 참조)

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


def bayesian_rating(rating, reviews):
    """신뢰도 가중 평점 (베이지안 수축).

        (사전가중치 × 사전값 + Σ평점) / (사전가중치 + 리뷰수)

    Σ평점을 `rating * reviews` 로 복원하는 이유: Consultant.rating 에는
    **가공하지 않은 산술평균**이 저장되어 있다(Review 테이블에서 재계산한 값).
    수축은 매칭 정책이지 표시값이 아니므로 저장 단계가 아니라 여기서 건다.
    그래야 프로필에는 "4.8 (20건)" 이라는 실제 평균이 그대로 보이고,
    사전가중치를 조정할 때 컨설턴트 행을 전부 다시 쓰지 않아도 된다.

    reviews == 0 이면 Σ평점 = 0, 분모 = 사전가중치 이므로 결과는 정확히
    사전값이다. 리뷰 0건에 대한 특수 분기가 필요 없다는 뜻이다.
    """
    reviews = max(int(reviews or 0), 0)
    total = (rating or 0) * reviews
    return (RATING_PRIOR_WEIGHT * RATING_PRIOR_VALUE + total) / (RATING_PRIOR_WEIGHT + reviews)


def _rating_ratio(rating):
    """평점 → 0.0~1.0 비율 (척도 [3.0, 5.0] 선형, 바깥은 클램프).

    v3 까지는 4.8/4.5/4.0 경계의 계단 함수(1.0 / 2/3 / 1/3 / 0)였다.
    수축을 도입하면 입력이 연속값이 되는데, 계단을 그대로 두면 4.79 와 4.50 이
    동점이고 4.80 에서만 갑자기 뛴다. 리뷰 한 건이 경계를 넘느냐로 3.4점(17×1/3)이
    움직이는 것은 데이터가 뒷받침하지 않는 변별이다.

    선형으로 바꾸면 덤으로 중립이 정확히 표현된다:
    _rating_ratio(RATING_PRIOR_VALUE=4.0) == 0.5.
    """
    span = RATING_SCALE_CEILING - RATING_SCALE_FLOOR
    ratio = ((rating or 0) - RATING_SCALE_FLOOR) / span
    return max(0.0, min(1.0, ratio))


# 리뷰 0건의 중립 비율 — 수축 공식의 n=0 극한에서 유도한다.
# (사전값이 척도의 중점이므로 0.5 × RATING_SUB_SCORE 가 된다)
NO_REVIEW_NEUTRAL_RATIO = RATING_SUB_SCORE * _rating_ratio(RATING_PRIOR_VALUE)


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

    **리뷰 0건에 대한 특수 분기가 없다.** 배치 4(84dd46b)는 `if reviews <= 0`
    으로 중립값을 따로 반환했는데, 그 방식에는 두 가지 문제가 있었다.

      1) 중립의 정의가 두 곳에 생긴다. 집계(리뷰 평균)와 매칭(중립 분기)이 각각
         "평가 없음" 을 다르게 정의하면 두 값이 어긋난다.
      2) **리뷰를 모을수록 손해가 되는 구간이 있었다.** 구 배분(0.6/0.4 +
         계단 함수) 실측:
             리뷰 0건        8.50
             4.0 × 5건       5.67   ← 보통 평가를 5건 모으면 0건보다 낮아진다
             3.0 × 5건       2.27
         평점 수집 기능을 붙이면서 "평가를 받으면 손해" 인 구조를 남겨둘 수는 없다.
         지금 배분에서는 4.0 × 5건 = 7.79 로 중립(6.38)보다 높다.

    수축을 쓰면 리뷰 0건이 n=0 인 한 경우로 자연히 흡수된다.
    (bayesian_rating(x, 0) == RATING_PRIOR_VALUE 이므로 rating 시드값이
     0.0 이든 5.0 이든 결과가 같다 — 가입 경로별 시드 불일치 버그의 재발 방지)

    수축이 실제로 막는 역전 (구 배분 → 현 배분):
        5.0 × 1건  vs  4.5 × 10건 :  12.47 > 11.33  →  8.85 < 11.33
        5.0 × 1건  vs  4.6 × 5건  :  12.47 >  9.07  →  8.85 <  9.70
    표본 1건짜리 만점이 표본 5~10건의 실적을 이기던 것이 뒤집힌다.

    현재 배분에서의 실측값 (WEIGHT_RATING=17):
        리뷰 0건            →  6.38   ← 중립
        5.0 × 1건           →  8.85
        4.8 × 20건          → 13.29
        5.0 × 50건          → 16.42
        3.0 × 5건           →  4.60   ← 중립보다 낮다
        3.0 × 20건          →  4.11   ← 리뷰가 많아도 나쁘면 낮다
    """
    shrunk = bayesian_rating(rating, reviews)
    return WEIGHT_RATING * (
        RATING_SUB_SCORE * _rating_ratio(shrunk)
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
            c_iso_raw = _load_json(c.iso_experience, {})

            # 저장된 표기가 무엇이든 정규형으로 접어서 비교한다(위 _normalize_iso 주석).
            c_iso = {_normalize_iso(key): key for key in c_iso_raw}

            matched_iso = [target for target in target_iso if target in c_iso]

            if target_iso:
                score += (len(matched_iso) / len(target_iso)) * WEIGHT_ISO
                if matched_iso:
                    # 화면에는 컨설턴트가 실제로 등록한 원래 표기를 보여준다
                    # (정규화는 비교용이지 표시용이 아니다).
                    labels = [_display_iso_label(c_iso[code]) for code in matched_iso]
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
            c_industries = _load_json(c.industry_experience, [])

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
            c_projects = _load_json(c.project_types, [])

            if target_project and target_project in c_projects:
                score += BONUS_PROJECT_TYPE
                match_details.append(f"{target_project} 프로젝트 경험")

            # ── 타임라인 긴급 페널티 ───────────────────────────────────
            # 리뷰가 있는 컨설턴트에게만 적용한다. 이 페널티의 목적은 '실적상
            # 평가가 낮은' 사람을 긴급 건에서 내리는 것이지, 아직 평가가 없는
            # 사람을 내리는 것이 아니다. (rating 시드를 0.0 으로 통일한 뒤에는
            # 조건을 걸지 않으면 신규 컨설턴트가 전부 -5 를 맞는다.)
            #
            # ⚠️ 여기만 수축값이 아니라 **원 평균(c.rating)** 을 본다. 의도적이다.
            #    수축값으로 바꾸면 URGENT_RATING_FLOOR(4.5)를 넘기 위해 필요한
            #    리뷰 수가 (P=4.0, W=5 기준) 전부 5점이어도 5건이다. 플랫폼 누적
            #    리뷰가 0건인 지금은 리뷰를 가진 **거의 전원**이 -5 를 맞는다.
            #    이 페널티는 "이 사람의 실제 평가가 낮은가" 를 묻는 하한 검사이지
            #    순위 산출식이 아니므로 원 평균이 맞다.
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
            # ── 매칭 결과 화면이 직접 읽는 필드 ──────────────────────────
            # 이 다섯 개가 빠져 있으면 화면이 '조용히' 잘못 동작한다:
            #   · script.js 의 ISO/지역 필터는 c.isoExperience / c.regions 가
            #     없으면 모든 후보를 탈락시켜, 필터를 걸기만 하면 결과가 항상
            #     0명이 됐다 (BUG-E2E-003).
            #   · 전문성 태그(심사원 자격·산업 전문)는 c.roles /
            #     c.industryExperience 를 읽으므로 통째로 사라졌다 (BUG-E2E-002).
            # 키 이름과 형태는 consultant_public_dict() / GET /api/consultants/<id>
            # 와 일치시킨다 — isoExperience 는 객체(Object.keys 로 순회),
            # 나머지는 리스트, regions 는 콤마로 이어 붙인 문자열이다.
            'isoExperience':      _load_json(c.iso_experience, {}),
            'industryExperience': _load_json(c.industry_experience, []),
            'projectTypes':       _load_json(c.project_types, []),
            'roles':              _load_json(c.roles, []),
            'regions':            c.regions or '',
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
