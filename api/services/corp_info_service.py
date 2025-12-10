"""
금융위원회 기업기본정보 API 서비스
DATA.go.kr 공공데이터 API를 활용하여 기업 정보를 조회합니다.

API 명세:
- 기업개요조회: getCorpOutline_V2
- 계열회사조회: getAffiliate_V2
- 연결대상종속기업조회: getConsSubsComp_V2
"""

import os
import requests
from urllib.parse import quote
import json


class CorpInfoService:
    """금융위원회 기업기본정보 API 서비스"""
    
    BASE_URL = "http://apis.data.go.kr/1160100/service/GetCorpBasicInfoService_V2"
    
    def __init__(self):
        # API 키는 환경변수에서 가져오기 (기본값은 제공된 인증키)
        self.api_key = os.environ.get(
            'DATA_GO_KR_API_KEY', 
            '3d5ffc75a14cccb5038feb87bbf1b03f36591801bd4469fbfaf1d39f90a62ff8'
        )
    
    def get_corp_outline(self, corp_name: str = None, crno: str = None, num_of_rows: int = 10, page_no: int = 1) -> dict:
        """
        기업개요 조회
        
        Args:
            corp_name: 법인명 (회사명)
            crno: 법인등록번호 (13자리)
            num_of_rows: 한 페이지 결과 수
            page_no: 페이지 번호
            
        Returns:
            기업 정보 딕셔너리 또는 None
        """
        url = f"{self.BASE_URL}/getCorpOutline_V2"
        
        params = {
            'serviceKey': self.api_key,
            'resultType': 'json',
            'numOfRows': num_of_rows,
            'pageNo': page_no
        }
        
        # 법인명 또는 법인등록번호로 검색
        if crno:
            # 하이픈 제거 및 공백 제거
            crno_clean = crno.replace('-', '').replace(' ', '')
            params['crno'] = crno_clean
            print(f"[API] 법인등록번호로 조회: {crno_clean}")
        if corp_name:
            params['corpNm'] = corp_name
            print(f"[API] 법인명으로 조회: {corp_name}")
            
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # API 에러 응답 확인
            if 'response' in data:
                header = data['response'].get('header', {})
                result_code = header.get('resultCode', '')
                result_msg = header.get('resultMsg', '')
                
                if result_code != '00' and result_code != '':
                    print(f"[API] 에러 응답: {result_code} - {result_msg}")
                    return {
                        'success': False,
                        'message': f"API Error: {result_msg} (Code: {result_code})",
                        'items': []
                    }
                
                body = data['response'].get('body', {})
                total_count = body.get('totalCount', 0)
                items = body.get('items', {})
                
                print(f"[API] 응답: totalCount={total_count}, items 타입={type(items)}")
                
                if items and 'item' in items:
                    item_list = items['item']
                    # 단일 항목이면 리스트로 변환
                    if isinstance(item_list, dict):
                        item_list = [item_list]
                    
                    print(f"[API] {len(item_list)}개 결과 발견")
                    return {
                        'success': True,
                        'total_count': total_count,
                        'items': item_list
                    }
                elif total_count == 0:
                    print(f"[API] 검색 결과 없음 (totalCount=0)")
                else:
                    print(f"[API] items 구조 이상: {items}")
            
            return {
                'success': False,
                'message': 'No data found in response',
                'items': []
            }
            
        except requests.exceptions.RequestException as e:
            print(f"API 요청 실패: {e}")
            return {
                'success': False,
                'message': str(e),
                'items': []
            }
        except json.JSONDecodeError as e:
            print(f"JSON 파싱 실패: {e}")
            return {
                'success': False,
                'message': 'Invalid JSON response',
                'items': []
            }
    
    def get_affiliate(self, crno: str, bas_dt: str = None, num_of_rows: int = 10, page_no: int = 1) -> dict:
        """
        계열회사 조회
        
        Args:
            crno: 법인등록번호
            bas_dt: 기준일자 (YYYYMMDD)
            num_of_rows: 한 페이지 결과 수
            page_no: 페이지 번호
            
        Returns:
            계열회사 정보 딕셔너리
        """
        url = f"{self.BASE_URL}/getAffiliate_V2"
        
        params = {
            'serviceKey': self.api_key,
            'resultType': 'json',
            'numOfRows': num_of_rows,
            'pageNo': page_no,
            'crno': crno
        }
        
        if bas_dt:
            params['basDt'] = bas_dt
            
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if 'response' in data:
                body = data['response'].get('body', {})
                items = body.get('items', {})
                
                if items and 'item' in items:
                    item_list = items['item']
                    if isinstance(item_list, dict):
                        item_list = [item_list]
                    
                    return {
                        'success': True,
                        'total_count': body.get('totalCount', 0),
                        'items': item_list
                    }
            
            return {
                'success': False,
                'message': 'No data found',
                'items': []
            }
            
        except Exception as e:
            print(f"계열회사 조회 실패: {e}")
            return {
                'success': False,
                'message': str(e),
                'items': []
            }
    
    def get_subsidiary(self, crno: str, bas_dt: str = None, num_of_rows: int = 10, page_no: int = 1) -> dict:
        """
        연결대상종속기업 조회
        
        Args:
            crno: 법인등록번호
            bas_dt: 기준일자 (YYYYMMDD)
            num_of_rows: 한 페이지 결과 수
            page_no: 페이지 번호
            
        Returns:
            종속기업 정보 딕셔너리
        """
        url = f"{self.BASE_URL}/getConsSubsComp_V2"
        
        params = {
            'serviceKey': self.api_key,
            'resultType': 'json',
            'numOfRows': num_of_rows,
            'pageNo': page_no,
            'crno': crno
        }
        
        if bas_dt:
            params['basDt'] = bas_dt
            
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if 'response' in data:
                body = data['response'].get('body', {})
                items = body.get('items', {})
                
                if items and 'item' in items:
                    item_list = items['item']
                    if isinstance(item_list, dict):
                        item_list = [item_list]
                    
                    return {
                        'success': True,
                        'total_count': body.get('totalCount', 0),
                        'items': item_list
                    }
            
            return {
                'success': False,
                'message': 'No data found',
                'items': []
            }
            
        except Exception as e:
            print(f"종속기업 조회 실패: {e}")
            return {
                'success': False,
                'message': str(e),
                'items': []
            }
    
    def get_enhanced_company_info(self, company_name: str = None, crno: str = None, bzno: str = None) -> dict:
        """
        종합 기업 정보 조회 (AI 분석 보강용)
        법인등록번호 또는 사업자등록번호가 있으면 우선 사용하여 더 정확한 결과 제공
        
        Args:
            company_name: 회사명 (옵션, crno/bzno가 없을 때 사용)
            crno: 법인등록번호 (13자리, 옵션)
            bzno: 사업자등록번호 (10자리, 옵션)
            
        Returns:
            종합 기업 정보 딕셔너리
        """
        result = {
            'found': False,
            'company_name': company_name or '',
            'basic_info': None,
            'affiliates': [],
            'subsidiaries': [],
            'risk_indicators': {}
        }
        
        # 1. 기업개요 조회 (법인등록번호 우선, 없으면 사업자등록번호, 없으면 회사명)
        if crno:
            # 법인등록번호로 조회 (가장 정확)
            corp_data = self.get_corp_outline(corp_name=company_name, crno=crno)
        elif bzno:
            # 사업자등록번호로 조회 (법인등록번호가 없을 때)
            # API는 사업자등록번호 직접 지원 안 함, 회사명과 함께 사용
            corp_data = self.get_corp_outline(corp_name=company_name)
            # 결과에서 사업자등록번호로 필터링
            if corp_data['success'] and corp_data['items']:
                matching_items = [item for item in corp_data['items'] 
                                 if item.get('bzno', '').replace('-', '') == bzno.replace('-', '')]
                if matching_items:
                    corp_data['items'] = matching_items
                else:
                    corp_data['success'] = False
                    corp_data['items'] = []
        else:
            # 회사명만으로 조회
            corp_data = self.get_corp_outline(corp_name=company_name)
        
        if corp_data['success'] and corp_data['items']:
            # 여러 결과 중 가장 정확한 항목 선택
            item = self._select_best_match(corp_data['items'], company_name)
            result['found'] = True
            
            # 기본 정보 추출
            result['basic_info'] = {
                'crno': item.get('crno', ''),  # 법인등록번호
                'corp_name': item.get('corpNm', ''),  # 법인명
                'corp_name_en': item.get('corpEnsnNm', ''),  # 영문명
                'representative': item.get('enpRprFnm', ''),  # 대표자
                'bzno': item.get('bzno', ''),  # 사업자등록번호
                'address': item.get('enpBsadr', ''),  # 주소
                'phone': item.get('enpTlno', ''),  # 전화번호
                'fax': item.get('enpFxno', ''),  # 팩스
                'homepage': item.get('enpHmpgUrl', ''),  # 홈페이지
                'industry_code': item.get('sicNm', ''),  # 표준산업분류
                'established_date': item.get('enpEstbDt', ''),  # 설립일
                'employee_count': item.get('enpEmpeCnt', 0),  # 종업원수
                'is_sme': item.get('smenpYn', '') == 'Y',  # 중소기업 여부
                'main_business': item.get('enpMainBizNm', ''),  # 주요사업
                'market_type': item.get('corpRegMrktDcdNm', ''),  # 상장시장
                'fiscal_month': item.get('enpStacMm', ''),  # 결산월
                'exchange_listed_date': item.get('enpXchgLstgDt', ''),  # 거래소상장일
                'kosdaq_listed_date': item.get('enpKosdaqLstgDt', ''),  # 코스닥상장일
                'main_bank': item.get('enpMntrBnkNm', ''),  # 주거래은행
                'avg_tenure': item.get('empeAvgCnwkTermCtt', ''),  # 평균근속기간
                'avg_salary': item.get('enpPn1AvgSlryAmt', 0),  # 1인평균급여
                'auditor': item.get('actnAudpnNm', ''),  # 회계감사인
                'audit_opinion': item.get('audtRptOpnnCtt', ''),  # 감사의견
            }
            
            # 2. 리스크 지표 산출
            result['risk_indicators'] = self._calculate_risk_indicators(result['basic_info'])
            
            # 3. 법인등록번호가 있으면 계열회사/종속기업 조회
            # 파라미터로 받은 crno 우선, 없으면 API 결과에서 가져온 crno 사용
            final_crno = crno or item.get('crno')
            if final_crno:
                # 계열회사 조회
                affiliate_data = self.get_affiliate(final_crno)
                if affiliate_data['success']:
                    result['affiliates'] = affiliate_data['items']
                
                # 종속기업 조회
                subsidiary_data = self.get_subsidiary(final_crno)
                if subsidiary_data['success']:
                    result['subsidiaries'] = subsidiary_data['items']
        
        return result
    
    def _select_best_match(self, items: list, company_name: str = None) -> dict:
        """
        여러 검색 결과 중에서 가장 정확한 항목을 선택합니다.
        
        우선순위:
        1. 회사명 정확 일치 (대소문자 무시)
        2. "(주)" 포함 항목
        3. 상장기업
        4. 종업원수가 많은 항목
        5. 설립일이 있는 항목
        
        Args:
            items: 검색 결과 항목 리스트
            company_name: 검색한 회사명
            
        Returns:
            선택된 항목 딕셔너리
        """
        if not items:
            return None
        
        if len(items) == 1:
            return items[0]
        
        # 회사명 정규화 (공백, 괄호 제거)
        def normalize_name(name):
            if not name:
                return ""
            name = name.replace(" ", "").replace("　", "")
            # (주), (유), (합) 등 제거
            import re
            name = re.sub(r'\([^)]*\)', '', name)
            return name.lower()
        
        normalized_search = normalize_name(company_name) if company_name else ""
        
        # 각 항목에 점수 부여
        scored_items = []
        for item in items:
            score = 0
            corp_name = item.get('corpNm', '')
            normalized_corp = normalize_name(corp_name)
            
            # 1. 정확 일치 (가장 높은 점수) - 원본 이름 기준
            if company_name and corp_name:
                # 원본 이름 정확 일치 (공백 제거 후 비교)
                if company_name.replace(' ', '') == corp_name.replace(' ', ''):
                    score += 2000
                # "(주)" 추가/제거 후 비교
                elif company_name.replace('(주)', '').replace(' ', '') == corp_name.replace('(주)', '').replace(' ', ''):
                    score += 1500
            
            # 2. 정규화된 이름 정확 일치
            if normalized_search and normalized_corp == normalized_search:
                score += 1000
            
            # 3. "(주)" 포함 (주식회사는 더 정확할 가능성) - 높은 가중치
            if '(주)' in corp_name:
                score += 200  # 가중치 증가
            elif '(유)' in corp_name:
                score += 150
            
            # 4. 부분 일치 (하지만 정확 일치가 아닐 때만)
            if normalized_search and normalized_search in normalized_corp:
                # 검색어가 회사명의 시작 부분인 경우 더 높은 점수
                if normalized_corp.startswith(normalized_search):
                    score += 50
                else:
                    score += 20  # 부분 일치 점수 낮춤
            
            # 5. 상장기업 (유가, 코스피, 코스닥 등)
            market_type = item.get('corpRegMrktDcdNm', '')
            if market_type and market_type not in ['', '기타']:
                score += 40  # 가중치 증가
            
            # 6. 종업원수 (많을수록 본사일 가능성)
            try:
                emp_count = int(item.get('enpEmpeCnt', 0) or 0)
                if emp_count > 0:
                    score += min(emp_count // 100, 30)  # 최대 30점으로 증가
            except:
                pass
            
            # 7. 설립일이 있는 항목
            if item.get('enpEstbDt'):
                score += 15  # 가중치 증가
            
            # 8. 감사인이 있는 항목 (대기업일 가능성)
            if item.get('actnAudpnNm'):
                score += 10  # 가중치 증가
            
            scored_items.append((score, item))
        
        # 점수 순으로 정렬 (높은 점수 우선)
        scored_items.sort(key=lambda x: x[0], reverse=True)
        
        # 가장 높은 점수의 항목 반환
        return scored_items[0][1]
    
    def _calculate_risk_indicators(self, basic_info: dict) -> dict:
        """
        기업 정보를 기반으로 리스크 지표 산출
        
        Args:
            basic_info: 기업 기본 정보
            
        Returns:
            리스크 지표 딕셔너리
        """
        indicators = {
            'company_age_years': 0,
            'is_listed': False,
            'has_audit': False,
            'audit_clean': False,
            'employee_scale': 'unknown',
            'governance_level': 'unknown'
        }
        
        # 설립연수 계산
        established_date = basic_info.get('established_date', '')
        if established_date and len(established_date) >= 4:
            try:
                from datetime import datetime
                est_year = int(established_date[:4])
                current_year = datetime.now().year
                indicators['company_age_years'] = current_year - est_year
            except:
                pass
        
        # 상장 여부
        market_type = basic_info.get('market_type', '')
        indicators['is_listed'] = market_type and market_type != '기타' and market_type != ''
        
        # 감사 여부 및 적정 여부
        auditor = basic_info.get('auditor', '')
        indicators['has_audit'] = bool(auditor)
        
        audit_opinion = basic_info.get('audit_opinion', '')
        indicators['audit_clean'] = '적정' in audit_opinion or '예외사항없음' in audit_opinion
        
        # 종업원 규모
        employee_count = basic_info.get('employee_count', 0)
        try:
            emp_count = int(employee_count) if employee_count else 0
            if emp_count == 0:
                indicators['employee_scale'] = 'unknown'
            elif emp_count < 10:
                indicators['employee_scale'] = 'micro'
            elif emp_count < 50:
                indicators['employee_scale'] = 'small'
            elif emp_count < 300:
                indicators['employee_scale'] = 'medium'
            else:
                indicators['employee_scale'] = 'large'
        except:
            indicators['employee_scale'] = 'unknown'
        
        # 지배구조 수준 (상장 + 감사 기준)
        if indicators['is_listed'] and indicators['has_audit']:
            indicators['governance_level'] = 'high'
        elif indicators['has_audit']:
            indicators['governance_level'] = 'medium'
        else:
            indicators['governance_level'] = 'low'
        
        return indicators


# 테스트용 코드
if __name__ == "__main__":
    service = CorpInfoService()
    
    # 테스트: 메리츠자산운용 검색
    result = service.get_enhanced_company_info("메리츠자산운용")
    print(json.dumps(result, indent=2, ensure_ascii=False))
