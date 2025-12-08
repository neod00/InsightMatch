import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import json

class CorpInfoService:
    def __init__(self):
        # API Keys from environment variables
        self.dart_api_key = os.environ.get('DART_API_KEY')
        self.public_data_key = os.environ.get('DATA_GO_KR_API_KEY') # 공공데이터포털 Decoding Key
        
    def get_enhanced_company_info(self, company_name, crno=None, bzno=None):
        """
        Retrieves company information from multiple sources (Public Data Portal, DART).
        Prioritizes Public Data Portal (Financial Services Commission) for detailed financial/structure info.
        """
        result = {
            'found': False,
            'source': None,
            'basic_info': {},
            'risk_indicators': {},
            'affiliates': [],
            'subsidiaries': []
        }
        
        # 1. Try Public Data Portal (Financial Services Commission - Corporate Basic Info)
        # This is the most reliable source for 'overview' data (FSC API)
        if self.public_data_key:
            fsc_data = self._fetch_fsc_basic_info(company_name, crno)
            if fsc_data:
                result['found'] = True
                result['source'] = 'FSC_Public_Data'
                result['basic_info'] = fsc_data
                
                # Calculate basic risk indicators from FSC data
                result['risk_indicators'] = self._calculate_risk_indicators(fsc_data)
                return result
        else:
            print("[CorpInfoService] Warning: DATA_GO_KR_API_KEY not found in environment variables.")

        # 2. (Optional) Try DART if FSC failed (Implementation Reserved for Phase 2 strict)
        # For now, we focus on the FSC API as requested in the 'Public Data' requirement.
        
        return result

    def _fetch_fsc_basic_info(self, company_name, crno=None):
        """
        Fetches data from Financial Services Commission (FSC) API via Ministry of the Interior and Safety (Public Data Portal).
        API: getCorpOutline_V2 (Enterprise Basic Information)
        """
        # Endpoint for FSC Corporate Basic Info
        url = 'http://apis.data.go.kr/1160100/service/GetCorpBasicInfoService_V2/getCorpOutline_V2'
        
        params = {
            'serviceKey': self.public_data_key,
            'pageNo': '1',
            'numOfRows': '1',
            'resultType': 'json',
            'corpNm': company_name
        }
        
        if crno:
            params['crno'] = crno

        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
                
                if items:
                    # Taking the first match
                    item = items[0] if isinstance(items, list) else items
                    
                    # Parse interesting fields
                    return {
                        'crno': item.get('crno'),
                        'bzno': item.get('bzno'),
                        'company_name': item.get('corpNm'),
                        'representative': item.get('ceoNm'),
                        'established_date': item.get('enpEstbDt'), # YYYYMMDD
                        'employee_count': item.get('enpPn1AvgEmplCnt', '0'), # Average employee count
                        'main_business': item.get('sicNm'),
                        'address': item.get('enpBsadr'),
                        'is_sme': item.get('smeYn') == 'Y',
                        'market_type': item.get('corpCls'), # Y: Kospi, K: Kosdaq, N: Konex, E: Etc
                        'main_bank': item.get('mainBankNm'),
                        'auditor': item.get('audtInstNm'),
                        'audit_opinion': item.get('audtRptOpnnCtt')
                    }
        except Exception as e:
            print(f"FSC API Error: {e}")
            pass
            
        return None

    def _calculate_risk_indicators(self, data):
        """
        Calculates simple risk indicators based on basic info.
        """
        indicators = {
            'company_age_years': 0,
            'is_listed': False,
            'has_audit': False,
            'audit_clean': False,
            'employee_scale': 'unknown',
            'governance_level': 'unknown'
        }
        
        # 1. Age
        est_date = data.get('established_date')
        if est_date and len(str(est_date)) == 8:
            est_year = int(str(est_date)[:4])
            current_year = datetime.now().year
            indicators['company_age_years'] = current_year - est_year
            
        # 2. Listed Status
        market = data.get('market_type')
        if market in ['Y', 'K']: # Kospi or Kosdaq
            indicators['is_listed'] = True
            
        # 3. Audit Status
        if data.get('auditor'):
            indicators['has_audit'] = True
            
        op = data.get('audit_opinion', '')
        if '적정' in op:
            indicators['audit_clean'] = True
            
        # 4. Scale
        try:
            emp = int(data.get('employee_count', 0))
            if emp >= 300:
                indicators['employee_scale'] = 'Large'
            elif emp >= 50:
                indicators['employee_scale'] = 'Medium'
            else:
                indicators['employee_scale'] = 'Small'
        except:
            pass
            
        return indicators
