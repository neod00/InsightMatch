"""
평화산업(주) 분석 테스트
사업자등록번호: 514-81-57898
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api', 'services'))

from corp_info_service import CorpInfoService
from ai_service import AIService
import json

def test_pyeonghwa_analysis():
    """평화산업(주) 분석 테스트"""
    print("=" * 60)
    print("평화산업(주) AI 분석 테스트")
    print("=" * 60)
    
    # 1. 기업정보 API 테스트
    print("\n[1단계] 공공데이터 API로 기업정보 조회")
    print("-" * 60)
    corp_service = CorpInfoService()
    
    # 사업자등록번호로 조회
    result = corp_service.get_enhanced_company_info(
        company_name="평화산업(주)",
        bzno="5148157898"  # 하이픈 제거
    )
    
    if result.get('found'):
        print("✓ 기업 정보 발견!")
        basic_info = result.get('basic_info', {})
        print(f"  - 법인명: {basic_info.get('corp_name', 'N/A')}")
        print(f"  - 사업자등록번호: {basic_info.get('bzno', 'N/A')}")
        print(f"  - 대표자: {basic_info.get('representative', 'N/A')}")
        print(f"  - 설립일: {basic_info.get('established_date', 'N/A')}")
        print(f"  - 종업원수: {basic_info.get('employee_count', 'N/A')}명")
        print(f"  - 주요사업: {basic_info.get('main_business', 'N/A')}")
        
        risk_indicators = result.get('risk_indicators', {})
        print(f"\n  [리스크 지표]")
        print(f"  - 기업연령: {risk_indicators.get('company_age_years', 0)}년")
        print(f"  - 상장여부: {'예' if risk_indicators.get('is_listed') else '아니오'}")
        print(f"  - 외부감사: {'있음' if risk_indicators.get('has_audit') else '없음'}")
        print(f"  - 기업규모: {risk_indicators.get('employee_scale', 'unknown')}")
    else:
        print("✗ 기업 정보를 찾을 수 없습니다.")
        print("  (회사명으로 재시도 중...)")
        result = corp_service.get_enhanced_company_info(company_name="평화산업")
        if result.get('found'):
            print("✓ 회사명으로 기업 정보 발견!")
        else:
            print("✗ 회사명으로도 찾을 수 없습니다.")
    
    # 2. AI 분석 테스트
    print("\n[2단계] AI 분석 실행")
    print("-" * 60)
    
    intake_data = {
        'companyName': '평화산업(주)',
        'companyUrl': '',
        'crno': '',  # 법인등록번호 없음
        'bzno': '514-81-57898',  # 사업자등록번호
        'industry': '제조업',
        'employees': '51-200',
        'standards': ['ISO 9001', 'ISO 14001'],
        'certStatus': 'None',
        'readiness': 'Initial'
    }
    
    ai_service = AIService()
    
    try:
        print("AI 분석 중... (약 10-30초 소요)")
        analysis_result = ai_service.analyze(intake_data)
        
        print("\n✓ AI 분석 완료!")
        print("\n[분석 결과]")
        print("-" * 60)
        print(f"회사명: {analysis_result.get('company_name', 'N/A')}")
        print(f"리스크 점수: {analysis_result.get('risk_score', 'N/A')}/100")
        print(f"리스크 수준: {analysis_result.get('risk_level', 'N/A')}")
        print(f"\n주요 리스크 요인:")
        for i, factor in enumerate(analysis_result.get('risk_factors', []), 1):
            print(f"  {i}. {factor}")
        print(f"\n추천 ISO 표준:")
        for standard in analysis_result.get('recommended_standards', []):
            print(f"  - {standard}")
        
        if analysis_result.get('verified_data'):
            print("\n✓ 공공데이터 검증 완료")
            gov_data = analysis_result.get('gov_data', {})
            print(f"  - 검증된 사업자등록번호: {gov_data.get('bzno', 'N/A')}")
        else:
            print("\n⚠ 공공데이터 검증 실패 (사용자 입력 데이터만 사용)")
        
        print(f"\n[AI 요약]")
        print("-" * 60)
        summary = analysis_result.get('summary', 'N/A')
        # HTML 태그 제거
        import re
        summary_text = re.sub(r'<[^>]+>', '', summary)
        print(summary_text)
        
        # 결과를 JSON 파일로 저장
        with open('pyeonghwa_analysis_result.json', 'w', encoding='utf-8') as f:
            json.dump(analysis_result, f, ensure_ascii=False, indent=2)
        print("\n✓ 분석 결과가 'pyeonghwa_analysis_result.json'에 저장되었습니다.")
        
    except Exception as e:
        print(f"\n✗ AI 분석 실패: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_pyeonghwa_analysis()
