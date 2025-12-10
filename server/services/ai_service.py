import os
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import json

# Import CorpInfoService
from .corp_info_service import CorpInfoService

class AIService:
    def __init__(self):
        # API 키는 환경변수에서 가져오기
        api_key = os.environ.get('GOOGLE_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
            # Tools 설정: Google Search Retrieval 활성화 (Grounding)
            # gemini-2.5-flash -> gemini-2.0-flash (Available & Supports Grounding)
            self.model = genai.GenerativeModel('gemini-2.5-flash', tools='google_search')
        else:
            self.model = None
            print("Warning: GOOGLE_API_KEY not found. AI Service will use mock data.")
        
        # 기업정보 API 서비스 초기화
        self.corp_info_service = CorpInfoService()

    def analyze(self, intake_data):
        """
        Analyzes a company using Google Gemini with Search Grounding.
        Enhanced with DATA.go.kr 금융위원회 기업기본정보 API.
        STRICT MODE: No Mock Data.
        """
        company_name = intake_data.get('companyName', 'Unknown Company')
        url = intake_data.get('companyUrl', '')
        crno = intake_data.get('crno', '').strip().replace('-', '')
        bzno = intake_data.get('bzno', '').strip().replace('-', '')
        industry = intake_data.get('industry', '')
        employees = intake_data.get('employees', '')
        standards = intake_data.get('standards', [])
        cert_status = intake_data.get('certStatus', '')
        readiness = intake_data.get('readiness', '')
        
        # 0. 공공데이터 API로 기업 정보 조회
        gov_corp_data = None
        gov_data_summary = ""
        try:
            if crno:
                gov_corp_data = self.corp_info_service.get_enhanced_company_info(company_name, crno=crno)
            elif bzno:
                gov_corp_data = self.corp_info_service.get_enhanced_company_info(company_name, bzno=bzno)
            else:
                gov_corp_data = self.corp_info_service.get_enhanced_company_info(company_name)
            
            if gov_corp_data.get('found'):
                basic_info = gov_corp_data.get('basic_info', {})
                risk_indicators = gov_corp_data.get('risk_indicators', {})
                
                gov_data_summary = f"""
                [공공데이터 기업정보 - 금융위원회 제공]
                - 법인등록번호: {basic_info.get('crno', 'N/A')}
                - 사업자등록번호: {basic_info.get('bzno', 'N/A')}
                - 대표자: {basic_info.get('representative', 'N/A')}
                - 설립일: {basic_info.get('established_date', 'N/A')}
                - 종업원수: {basic_info.get('employee_count', 'N/A')}명
                - 주요사업: {basic_info.get('main_business', 'N/A')}
                - 주소: {basic_info.get('address', 'N/A')}
                - 중소기업 여부: {'예' if basic_info.get('is_sme') else '아니오/미확인'}
                - 상장시장: {basic_info.get('market_type', 'N/A')}
                - 감사의견: {basic_info.get('audit_opinion', 'N/A')}
                
                [리스크 지표]
                - 기업연령: {risk_indicators.get('company_age_years', 0)}년
                - 상장여부: {'예' if risk_indicators.get('is_listed') else '아니오'}
                - 외부감사: {'있음' if risk_indicators.get('has_audit') else '없음'}
                
                [계열회사]: {len(gov_corp_data.get('affiliates', []))}개
                """
                print(f"✓ 공공데이터 API에서 '{company_name}' 기업정보 조회 성공")
            else:
                print(f"✗ 공공데이터 API에서 '{company_name}' 기업정보를 찾지 못함")
        except Exception as e:
            print(f"✗ 공공데이터 API 오류: {e}")
        
        # 1. Scrape Website Content
        site_content = ""
        if url:
            try:
                if not url.startswith('http'):
                    url = 'https://' + url
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(url, headers=headers, timeout=5)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    body_text = soup.get_text(separator=' ', strip=True)[:1000]
                    site_content = f"Content: {body_text}"
            except Exception:
                site_content = "Website not accessible."

        # 2. ISO Knowledge Base
        ISO_KNOWLEDGE = "ISO 9001, 14001, 45001, 27001, ESG context provided."

        # 3. Construct Prompt with Explicit "No Hallucination" instruction
        prompt = f"""
        You are an expert ISO consultant. 
        
        **SOURCE OF TRUTH**:
        1. Verified Government Data (Below) -> PRIORITY 1
        2. Google Search Results (Your Tool) -> PRIORITY 2
        
        **INSTRUCTION**:
        - Do NOT make up facts. If data is missing, state "Information not found".
        - Use the specific numbers from Government Data (Date, Employees).
        
        ===== Company Profile (User Input) =====
        - Name: {company_name}
        - Industry: {industry}
        - Employees: {employees}
        - Standards: {', '.join(standards) if standards else 'Not specified'}
        - Status: {cert_status}
        - Website Data: {site_content}
        
        ===== Verified Company Data (Financial Services Commission API) =====
        {gov_data_summary}
        
        Task:
        1. **Fact Check**: Verify ISO status via Google Search.
        2. **Risk Score**: Based on verified data.
        3. **Summary**: 3 paragraphs (Korean). Quote verified data.
        
        Output Format (JSON only):
        {{
            "risk_score": 80,
            "risk_factors": ["Fact 1", "Fact 2"],
            "recommended_standards": ["ISO 9001"],
            "industry": "Industry",
            "summary": "Para 1...\\n\\nPara 2...\\n\\nPara 3...",
            "evidence_links": ["URL 1"]
        }}
        """

        # 4. Call Gemini API
        if self.model:
            try:
                response = self.model.generate_content(prompt)
                text = response.text
                if text.startswith("```json"):
                    text = text[7:]
                if text.endswith("```"):
                    text = text[:-3]
                
                result = json.loads(text)
                
                # Format summary
                if 'summary' in result and result['summary']:
                    summary = result['summary']
                    summary = summary.replace('\\n', '\n')
                    paragraphs = summary.split('\n\n')
                    formatted_summary = ''.join([f'<p>{p.strip().replace("\n", "<br>")}</p>' for p in paragraphs if p.strip()])
                    result['summary'] = formatted_summary
                
                # Risk Level
                if 'risk_level' not in result and 'risk_score' in result:
                    score = result['risk_score']
                    if score >= 80:
                        result['risk_level'] = "안전 (Low Risk)"
                    elif score >= 60:
                        result['risk_level'] = "주의 (Moderate Risk)"
                    else:
                        result['risk_level'] = "위험 (High Risk)"
                        
                # Add Data Props
                if gov_corp_data and gov_corp_data.get('found'):
                    result['verified_data'] = True
                    result['gov_data'] = gov_corp_data.get('basic_info', {})
                else:
                    result['verified_data'] = False
                    
                return result

            except Exception as e:
                print(f"Gemini API Error: {e}")
                
                # FAILOVER: If API fails, return a "Partial Report" using ONLY Government Data if available.
                if gov_corp_data and gov_corp_data.get('found'):
                    info = gov_corp_data.get('basic_info', {})
                    return {
                        'company_name': company_name,
                        'industry': industry,
                        'risk_score': 50, # Neutral score
                        'risk_level': "분석 지연 (API Error)",
                        'risk_factors': [
                            f"공공데이터 확인됨: {info.get('established_date')} 설립",
                            f"기업규모: {info.get('employee_count')}명 (API 추정)",
                            "상세 AI 분석을 위한 Google 통신 장애 발생"
                        ],
                        'recommended_standards': standards if standards else ["ISO 9001"],
                        'summary': f"<p><strong>[시스템 안내]</strong> 현재 AI 서비스 사용량이 폭주하여 정밀 분석이 지연되고 있습니다.</p><p>하지만 <strong>금융위원회 공공데이터</strong>를 통해 '{company_name}'의 기본 정보(설립일: {info.get('established_date')}, 직원수: {info.get('employee_count')}명)는 정상적으로 확인되었습니다.</p><p>잠시 후 다시 시도해주시면 전체 분석 보고서를 확인하실 수 있습니다.</p>",
                        'evidence_links': ["https://www.data.go.kr"],
                        'verified_data': True,
                        'gov_data': info
                    }
                else:
                    # COMPLETE FAILURE (No AI, No Public Data)
                    return {
                        'company_name': company_name,
                        'industry': industry,
                        'risk_score': 0,
                        'risk_level': "분석 실패 (Service Error)",
                        'risk_factors': ["AI 모델 응답 없음", "공공데이터 조회 실패"],
                        'recommended_standards': [],
                        'summary': f"<p>죄송합니다. 현재 AI 분석 서비스와 공공데이터 서버에 연결할 수 없습니다.</p><p>({str(e)})</p><p>잠시 후 다시 시도해 주세요.</p>",
                        'evidence_links': []
                    }
        else:
             return {
                'company_name': company_name,
                'industry': industry,
                'risk_score': 0,
                'risk_level': "설정 오류 (No API Key)",
                'risk_factors': ["API Key Missing"],
                'recommended_standards': [],
                'summary': "<p>Google AI API Key가 설정되지 않았습니다. 관리자에게 문의하세요.</p>",
                'evidence_links': []
            }
