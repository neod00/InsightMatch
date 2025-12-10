"""
삼성전자 공공데이터 API 테스트 스크립트
"""
import os
import sys
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# API 서비스 import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api', 'services'))
from corp_info_service import CorpInfoService
import json

def test_samsung():
    """삼성전자로 공공데이터 API 테스트"""
    print("=" * 60)
    print("삼성전자 공공데이터 API 테스트")
    print("=" * 60)
    
    service = CorpInfoService()
    
    # 테스트 케이스들
    test_cases = [
        {
            "name": "회사명만으로 검색",
            "params": {"company_name": "삼성전자"}
        },
        {
            "name": "정확한 회사명으로 검색",
            "params": {"company_name": "삼성전자(주)"}
        },
        {
            "name": "법인등록번호로 검색 (삼성전자: 12081176-0000396)",
            "params": {"company_name": "삼성전자", "crno": "12081176-0000396"}
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n[테스트 {i}] {test_case['name']}")
        print("-" * 60)
        
        params = test_case['params']
        result = service.get_enhanced_company_info(
            company_name=params.get('company_name'),
            crno=params.get('crno'),
            bzno=params.get('bzno')
        )
        
        if result['found']:
            print("✓ 기업 정보 조회 성공!")
            basic_info = result['basic_info']
            print(f"  - 회사명: {basic_info.get('corp_name', 'N/A')}")
            print(f"  - 법인등록번호: {basic_info.get('crno', 'N/A')}")
            print(f"  - 사업자등록번호: {basic_info.get('bzno', 'N/A')}")
            print(f"  - 설립일: {basic_info.get('established_date', 'N/A')}")
            print(f"  - 종업원수: {basic_info.get('employee_count', 'N/A')}명")
            print(f"  - 주요사업: {basic_info.get('main_business', 'N/A')}")
            print(f"  - 상장시장: {basic_info.get('market_type', 'N/A')}")
            
            risk_indicators = result['risk_indicators']
            print(f"  - 업력: {risk_indicators.get('company_age_years', 0)}년")
            print(f"  - 상장여부: {'상장' if risk_indicators.get('is_listed') else '비상장'}")
            print(f"  - 감사여부: {'있음' if risk_indicators.get('has_audit') else '없음'}")
            
            print(f"\n  [전체 결과 JSON]")
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("✗ 기업 정보를 찾지 못했습니다.")
            print(f"  검색한 회사명: {params.get('company_name', 'N/A')}")
            if params.get('crno'):
                print(f"  검색한 법인등록번호: {params.get('crno')}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)

if __name__ == "__main__":
    try:
        test_samsung()
    except Exception as e:
        print(f"에러 발생: {e}")
        import traceback
        traceback.print_exc()
