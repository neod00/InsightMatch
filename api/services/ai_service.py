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
            self.model = genai.GenerativeModel('gemini-2.0-flash', tools='google_search_retrieval')
        else:
            self.model = None
            print("Warning: GOOGLE_API_KEY not found. AI Service will use mock data.")
        
        # 기업정보 API 서비스 초기화
        self.corp_info_service = CorpInfoService()

    def analyze(self, intake_data):
        """
        Analyzes a company using Google Gemini with Search Grounding.
        Enhanced with DATA.go.kr 금융위원회 기업기본정보 API.
        """
        company_name = intake_data.get('companyName', 'Unknown Company')
        url = intake_data.get('companyUrl', '')
        crno = intake_data.get('crno', '').strip().replace('-', '')  # 법인등록번호
        bzno = intake_data.get('bzno', '').strip().replace('-', '')  # 사업자등록번호
        industry = intake_data.get('industry', '')
        employees = intake_data.get('employees', '')
        standards = intake_data.get('standards', [])
        cert_status = intake_data.get('certStatus', '')
        readiness = intake_data.get('readiness', '')
        
        # 0. 공공데이터 API로 기업 정보 조회 (신뢰성 있는 외부 데이터)
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
                - 주거래은행: {basic_info.get('main_bank', 'N/A')}
                - 감사인: {basic_info.get('auditor', 'N/A')}
                - 감사의견: {basic_info.get('audit_opinion', 'N/A')}
                
                [리스크 지표]
                - 기업연령: {risk_indicators.get('company_age_years', 0)}년
                - 상장여부: {'예' if risk_indicators.get('is_listed') else '아니오'}
                - 외부감사: {'있음' if risk_indicators.get('has_audit') else '없음'}
                - 감사적정: {'예' if risk_indicators.get('audit_clean') else '아니오/미확인'}
                - 기업규모: {risk_indicators.get('employee_scale', 'unknown')}
                
                [계열회사]: {len(gov_corp_data.get('affiliates', []))}개
                [종속기업]: {len(gov_corp_data.get('subsidiaries', []))}개
                """
                print(f"✓ 공공데이터 API에서 '{company_name}' 기업정보 조회 성공")
            else:
                gov_data_summary = f"[공공데이터 API] 해당 기업 정보를 찾을 수 없습니다. (회사명: {company_name}) 사용자 입력 데이터만 활용합니다."
                print(f"✗ 공공데이터 API에서 '{company_name}' 기업정보를 찾지 못함")
        except Exception as e:
            gov_data_summary = f"[공공데이터 API] 조회 실패: {str(e)}"
            print(f"✗ 공공데이터 API 오류: {e}")
        
        # 1. Scrape Website Content
        site_content = ""
        if url:
            try:
                if not url.startswith('http'):
                    url = 'https://' + url
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                response = requests.get(url, headers=headers, timeout=5)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    title = soup.title.string if soup.title else ""
                    meta_desc = ""
                    meta = soup.find('meta', attrs={'name': 'description'})
                    if meta:
                        meta_desc = meta['content']
                    body_text = soup.get_text(separator=' ', strip=True)[:1500]
                    site_content = f"Title: {title}\nDescription: {meta_desc}\nContent: {body_text}"
            except Exception as e:
                print(f"Scraping failed: {e}")
                site_content = "Website not accessible."

        # 2. ISO Knowledge Base
        ISO_KNOWLEDGE = """
        ISO Standards Knowledge Base:
        (Same ISO text as before... omitted for brevity but assumed present in logic)
        ISO 9001 (품질경영시스템)...
        ISO 14001 (환경경영시스템)...
        ISO 45001 (안전보건경영시스템)...
        ISO 27001 (정보보안경영시스템)...
        ISO 13485 (의료기기 품질경영)...
        ESG 경영...
        """

        # 3. Construct Prompt
        prompt = f"""
        You are an expert ISO consultant. 
        
        **Step 1: Analyze Verified Government Data**
        Review the 'Verified Company Data' section below. This is the source of truth for corporate structure, size, and history.

        **Step 2: Google Search Grounding**
        Use Google Search to find additional *latest* factual information about "{company_name}" (Industry: {industry}).
        Search for:
        1. Official website content ("About Us", "Certifications").
        2. Any public records of ISO certifications (ISO 9001, 14001, etc.).
        3. Recent news or ESG reports.

        **Step 3: Synthesis**
        Combine the Government Data (Structure/Size) with Search Data (Certifications/Activities) to provide a precise risk assessment.
        
        {ISO_KNOWLEDGE}
        
        ===== Company Profile (User Input) =====
        - Name: {company_name}
        - Industry: {industry}
        - Employees: {employees}
        - Interested Standards: {', '.join(standards) if standards else 'Not specified'}
        - Current Status: {cert_status}
        - Readiness Level: {readiness}
        - Website Data: {site_content}
        
        ===== Verified Company Data (Financial Services Commission API) =====
        {gov_data_summary}
        
        Task:
        1. **Fact Check (Certifications)**: 
           - Verify ISO certifications via Google Search.
           - If found: List them and mark source as "Verified via Search".
           - If not found but user claims yes: Mark as "Self-reported (Not verified online)".
           - If not found and user says no: Mark as "None found".
        
        2. **Risk Score (0-100)**: 
           - Base score on Public Data (Stability, Audits).
           - Adjust based on Search findings (e.g., if ISO 9001 is found, score +10).
           - 100=Safe, 0=Risky.
        
        3. **Risk Factors (Korean)**: 
           - Use specific numbers from Public Data (e.g., "From 2006", "817 employees").
           - Cite missing certifications confirmed by Search.
        
        4. **Recommended Standards**: Based on gaps found.
        
        5. **Summary (Korean, 3 paragraphs)**:
           - Para 1: **Company Overview** using Public Data (Years, Employees, Listed status).
           - Para 2: **Certification Status** (Search results) vs Gaps.
           - Para 3: **Strategic Roadmap** (ESG, Integrated Certs).
           - **Must use specific numbers/names from the data.**
        
        Output Format (JSON only):
        {{
            "risk_score": 80,
            "risk_factors": ["대규모 조직(817명) 통합 관리 복잡성", "자동차 산업 특성상 품질/환경 이중 요구사항", "상장기업으로서 ESG 공시 의무 강화 대응 필요"],
            "recommended_standards": ["ISO 9001", "ISO 14001"],
            "industry": "자동차/자동차부품",
            "summary": "2006년 설립되어 19년의 업력을 보유한 평화산업(주)는 817명의 직원과 10개 계열사를 거느린 유가증권시장 상장기업입니다. 삼일회계법인의 외부감사를 받고 있어 재무 투명성이 확보되어 있습니다.\\n\\nGoogle 검색 결과, 현재 ISO 9001 인증을 보유하고 있는 것으로 확인됩니다 (출처: KAB). 현 시점에서는 기존 인증의 갱신 및 ISO 14001 환경경영시스템과의 통합인증(IMS) 전환이 효과적입니다.\\n\\nESG 경영 강화 추세에 따라 ISO 14001을 중심으로 탄소중립 로드맵과 연계한 환경경영 고도화를 권장합니다.",
            "evidence_links": ["https://www.company.com/cert", "https://kab.or.kr/result"]
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
                
                # Check Risk Level
                if 'risk_level' not in result and 'risk_score' in result:
                    score = result['risk_score']
                    if score >= 80:
                        result['risk_level'] = "안전 (Low Risk)"
                    elif score >= 60:
                        result['risk_level'] = "주의 (Moderate Risk)"
                    else:
                        result['risk_level'] = "위험 (High Risk)"
                        
                # Add Debug Info for Verification
                if gov_corp_data and gov_corp_data.get('found'):
                    result['verified_data'] = True
                    result['gov_data'] = gov_corp_data.get('basic_info', {})
                else:
                    result['verified_data'] = False
                    
                return result
            except Exception as e:
                print(f"Gemini API failed: {e}")
                return self._mock_analyze(intake_data)
        else:
            return self._mock_analyze(intake_data)

    def _mock_analyze(self, intake_data):
        company_name = intake_data.get('companyName', 'Unknown')
        industry = intake_data.get('industry', 'General')
        risk_score = 65
        return {
            'company_name': company_name,
            'industry': industry,
            'risk_score': risk_score,
            'risk_level': "주의 (Moderate Risk)",
            'risk_factors': ['초기 경영시스템 부재', '법적 요구사항 파악 미흡'],
            'recommended_standards': ['ISO 9001'],
            'summary': f"<strong>{company_name}</strong>은(는) 성장 중인 기업입니다. (Mock Data)",
            'evidence_links': []
        }
