"""매칭 설문(index.html) 코드값 → 화면·메일에 보일 한국어.

견적 요청 설명·프로젝트 상세·알림 메일이 같은 표를 쓴다.
예전에는 곳곳에 서로 다른 표가 있었고 실제 폼 값(customer_demand, Expired, 2000+ 등)과 맞지 않아
"업종: Manufacturing", "예산: unknown", "인증 사유: customer_demand" 처럼 코드가 그대로 보였다.

api/index.py 에 있던 것을 메일 서비스도 쓸 수 있게 이 모듈로 옮겼다
(services 가 index 를 import 하면 순환 import 가 된다). index.py 는 여기서 다시 가져다 쓴다.
"""

INTAKE_LABELS = {
    'industry': {
        'IT/Software': 'IT/소프트웨어', 'Manufacturing': '제조업', 'Automotive': '자동차/부품',
        'Electronics': '전자/반도체', 'Chemical': '화학/소재', 'Medical': '의료/바이오',
        'Food': '식품/음료', 'Construction': '건설/토목', 'Energy': '에너지/발전',
        'Nuclear': '원자력', 'Logistics': '물류/유통', 'Finance': '금융/보험',
        'Service': '서비스업', 'Public': '공공기관', 'Other': '기타',
    },
    'employees': {
        '1-10': '1~10명', '11-50': '11~50명', '51-200': '51~200명',
        '201-500': '201~500명', '500+': '500명 이상',
    },
    'cert_status': {
        'None': '없음 (신규 인증)', 'Expired': '만료됨 (갱신 필요)', 'Active': '보유 중 (추가 인증)',
        # 예전 값
        'none': '미보유', 'expired': '만료됨', 'expiring': '갱신 예정', 'valid': '유효', 'initial': '신규 인증',
    },
    'timeline': {
        'flexible': '일정 여유 있음', '6months': '6개월 이내', '3months': '3개월 이내',
        '1month': '1개월 이내 (긴급)', 'urgent': '긴급 (1개월 이내)',
    },
    'budget': {
        'unknown': '미정', 'under500': '500만 원 미만', '500-1000': '500만~1,000만 원',
        '1000-2000': '1,000만~2,000만 원', '2000+': '2,000만 원 이상',
        # 예전 값
        '2000-3000': '2,000만~3,000만 원', 'over3000': '3,000만 원 이상', 'negotiable': '협의 가능',
    },
    'reasons': {
        'customer_demand': '고객사·거래처 요구', 'public_bid': '공공입찰·조달 대응',
        'export': '해외 수출·바이어 요구', 'investment': '투자 유치·상장 준비',
        'internal': '사내 경영 시스템 체계화', 'regulation': '법·규제 대응',
        'competition': '경쟁사 대비 차별화',
        # 예전 값
        'customer': '거래처 요구', 'improve': '프로세스 개선', 'competitive': '경쟁력 강화',
        'esg': 'ESG 대응', 'other': '기타',
    },
}


def intake_label(kind, value):
    """설문 코드값을 한국어로. 표에 없으면 원래 값(사람이 쓴 값일 수 있음)."""
    if value in (None, '', []):
        return None
    if isinstance(value, list):
        return [INTAKE_LABELS.get(kind, {}).get(v, v) for v in value if isinstance(v, str) and v]
    if not isinstance(value, str):
        return str(value)
    return INTAKE_LABELS.get(kind, {}).get(value, value)
