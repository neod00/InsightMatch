# api/constants.py
"""
Shared constants for the InsightMatch API.
Central location for all mappings and configurations.
"""

# Issue ID to Korean name mapping
# Used by: index.py (direct_match), handlers.py (MatchingHandler)
ISSUE_NAMES = {
    'quality_defect': '품질 불량',
    'customer_complaint': '고객 클레임',
    'process_inefficiency': '프로세스 비효율',
    'supplier_quality': '공급업체 품질',
    'safety_incident': '안전사고',
    'env_regulation': '환경 규제',
    'energy_cost': '에너지 비용',
    'work_condition': '작업환경',
    'esg_demand': 'ESG 요구',
    'carbon_report': '탄소 보고',
    'carbon_neutral': '탄소중립',
    'esg_disclosure': 'ESG 공시',
    'security_incident': '정보보안',
    'privacy_need': '개인정보',
    'cloud_security': '클라우드 보안',
    'ai_risk': 'AI 리스크',
    'supply_unstable': '공급망 불안정',
    'crisis_response': '위기 대응',
    'compliance_risk': '컴플라이언스',
    'corruption_prevent': '부패 방지',
    'turnover': '이직률',
    'burnout': '번아웃',
    'knowledge_loss': '지식 유실'
}

# ISO code normalization mapping
# Converts various formats to standard short codes
ISO_CODE_ALIASES = {
    # ISO 9001 variants
    'iso 9001': '9001',
    'iso 9001:2015': '9001',
    'iso9001': '9001',
    'iso9001:2015': '9001',
    '9001:2015': '9001',
    
    # ISO 14001 variants
    'iso 14001': '14001',
    'iso 14001:2015': '14001',
    'iso14001': '14001',
    'iso14001:2015': '14001',
    '14001:2015': '14001',
    
    # ISO 45001 variants
    'iso 45001': '45001',
    'iso 45001:2018': '45001',
    'iso45001': '45001',
    'iso45001:2018': '45001',
    '45001:2018': '45001',
    
    # ISO 27001 variants
    'iso 27001': '27001',
    'iso 27001:2022': '27001',
    'iso27001': '27001',
    'iso27001:2022': '27001',
    '27001:2022': '27001',
    
    # ISO 22000 variants
    'iso 22000': '22000',
    'iso 22000:2018': '22000',
    'iso22000': '22000',
    
    # IATF 16949 (Automotive)
    'iatf 16949': '16949',
    'iatf 16949:2016': '16949',
    'iatf16949': '16949',
    
    # ISO 13485 (Medical)
    'iso 13485': '13485',
    'iso 13485:2016': '13485',
    'iso13485': '13485',
}

def normalize_iso_code(code):
    """
    Normalize ISO code to standard short format.
    
    Examples:
        'ISO 9001:2015' -> '9001'
        'iso 14001' -> '14001'
        '9001' -> '9001' (already normalized)
    
    Args:
        code: ISO code string in any format
        
    Returns:
        Normalized short code (e.g., '9001', '14001')
    """
    if not code:
        return ''
    
    # Convert to lowercase for matching
    code_lower = code.lower().strip()
    
    # Check alias mapping first
    if code_lower in ISO_CODE_ALIASES:
        return ISO_CODE_ALIASES[code_lower]
    
    # If already a short code (just digits), return as-is
    if code.isdigit():
        return code
    
    # Try to extract numeric part (e.g., 'ISO 9001:2015' -> '9001')
    import re
    match = re.search(r'(\d{4,5})', code)
    if match:
        return match.group(1)
    
    # Fallback: return original
    return code


# 매칭 가중치는 services/matching_service.py 상단에 단일 정의로 존재한다.
#
# 여기 있던 MATCHING_WEIGHTS 딕셔너리를 제거했다. 어디에서도 import 되지
# 않는 죽은 테이블이었는데(grep 결과 정의부가 유일한 등장), 실제 로직과
# 숫자가 전혀 달라서(iso 25 vs 40, region 3 vs 14) 읽는 사람이 이쪽을
# 실제 배분표로 오해하기 쉬웠다. 가중치를 조정할 때는
# matching_service.py 의 WEIGHT_* 상수만 고치면 되고, 합이 100인지는
# tests/test_matching.py 가 검증한다.

# Industry Categories for B3 (Keyword Expansion)
INDUSTRY_GROUPS = {
    'manufacturing': ['제조', '공장', '생산', '사출', '조립', '금형', 'Manufacturing', 'Factory'],
    'it_service': ['IT', '소프트웨어', '개발', 'SI', '클라우드', '플랫폼', 'Software', 'Information Technology'],
    'service': ['서비스', '교육', '컨설팅', '유통', '물류', 'Service', 'Logistics'],
    'construction': ['건설', '건축', '토목', '플랜트', 'Construction', 'Engineering'],
    'food': ['식품', '음식', '해썹', 'HACCP', 'Food', 'Beverage'],
    'medical': ['의료', '바이오', '제약', '병원', 'Medical', 'Bio', 'Pharma']
}

# Organization Size Mapping for B2
# Normalizes various inputs to standard sizes
ORG_SIZE_MAP = {
    '1-5인': 'Small',
    '5-10인': 'Small',
    '10-30인': 'Small',
    '30-50인': 'Medium',
    '50-100인': 'Medium',
    '100-300인': 'Medium',
    '300인 이상': 'Large',
    'SME': 'Medium',
    'Enterprise': 'Large'
}

# Role Points for B4
ROLE_POINTS = {
    'Lead Auditor': 5,
    'Auditor': 3,
    'Consultant': 2,
    'Trainer': 2,
    '팀장': 3,
    '심사원': 4
}

# Fallback score calculation
FALLBACK_BASE_SCORE = 50

