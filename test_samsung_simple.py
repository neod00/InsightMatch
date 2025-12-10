import sys
import os
sys.path.insert(0, 'api/services')
from corp_info_service import CorpInfoService

service = CorpInfoService()
result = service.get_enhanced_company_info('삼성전자')

if result['found']:
    info = result['basic_info']
    print(f"✓ 찾은 회사: {info.get('corp_name')}")
    print(f"  설립일: {info.get('established_date', 'N/A')}")
    print(f"  종업원수: {info.get('employee_count', 'N/A')}명")
    print(f"  상장시장: {info.get('market_type', 'N/A')}")
else:
    print("✗ 찾지 못함")
