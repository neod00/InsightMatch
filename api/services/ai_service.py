import os
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import json

# 기업정보 API 서비스 임포트
try:
    from .corp_info_service import CorpInfoService
except ImportError:
    from corp_info_service import CorpInfoService


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
        Analyzes a company using Google Gemini based on intake data.
        Enhanced with DATA.go.kr 금융위원회 기업기본정보 API.
        """
        company_name = intake_data.get('companyName', 'Unknown Company')
        url = intake_data.get('companyUrl', '')
        crno = intake_data.get('crno', '').strip().replace('-', '')  # 법인등록번호 (옵션, 하이픈 제거)
        bzno = intake_data.get('bzno', '').strip().replace('-', '')  # 사업자등록번호 (옵션, 하이픈 제거)
        industry = intake_data.get('industry', '')
        employees = intake_data.get('employees', '')
        standards = intake_data.get('standards', [])
        cert_status = intake_data.get('certStatus', '')
        readiness = intake_data.get('readiness', '')
        
        # 0. 공공데이터 API로 기업 정보 조회 (신뢰성 있는 외부 데이터)
        # 법인등록번호 또는 사업자등록번호가 있으면 우선 사용 (더 정확함)
        gov_corp_data = None
        gov_data_summary = ""
        try:
            if crno:
                # 법인등록번호로 우선 조회
                gov_corp_data = self.corp_info_service.get_enhanced_company_info(
                    company_name=company_name, 
                    crno=crno
                )
            elif bzno:
                # 사업자등록번호로 조회
                gov_corp_data = self.corp_info_service.get_enhanced_company_info(
                    company_name=company_name, 
                    bzno=bzno
                )
            else:
                # 회사명만으로 조회
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
                - 지배구조수준: {risk_indicators.get('governance_level', 'unknown')}
                
                [계열회사]: {len(gov_corp_data.get('affiliates', []))}개
                [종속기업]: {len(gov_corp_data.get('subsidiaries', []))}개
                """
                print(f"✓ 공공데이터 API에서 '{company_name}' 기업정보 조회 성공")
            else:
                gov_data_summary = f"[공공데이터 API] 해당 기업 정보를 찾을 수 없습니다. (회사명: {company_name}, 법인등록번호: {crno or '없음'}, 사업자등록번호: {bzno or '없음'}) 사용자 입력 데이터만 활용합니다."
                print(f"✗ 공공데이터 API에서 '{company_name}' 기업정보를 찾지 못함 (crno: {crno}, bzno: {bzno})")
        except Exception as e:
            import traceback
            gov_data_summary = f"[공공데이터 API] 조회 실패: {str(e)}"
            print(f"✗ 공공데이터 API 오류: {e}")
            print(f"  상세 오류: {traceback.format_exc()}")
        
        # 1. Scrape Website Content (if URL provided)
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

        # 2. ISO Knowledge Base (직접 포함)
        ISO_KNOWLEDGE = """
        ISO Standards Knowledge Base:
        
        ISO 9001 (품질경영시스템):
        - 핵심 원칙: 고객 중심, 리더십, 사람 참여, 프로세스 접근, 개선, 증거 기반 의사결정, 관계 관리
        - 적용 대상: 모든 조직 (제조, 서비스, 공공기관 등)
        - 주요 요구사항: 품질 방침, 품질 목표, 문서화된 정보, 내부 심사, 경영 검토
        
        ISO 14001 (환경경영시스템):
        - 핵심 원칙: 조직의 맥락, 리더십, 기획, 지원, 운영, 성과 평가, 개선
        - 특별 고려사항: 생애주기 관점, 환경 측면 관리, 준수 의무
        - 주요 요구사항: 환경 방침, 환경 목표, 환경 측면 식별, 비상사태 대비
        
        ISO 45001 (안전보건경영시스템):
        - 핵심 원칙: 위험 식별, 위험 평가, 근로자 참여, 위험 통제 계층, 비상 대비
        - 법적 요구사항: 중대재해처벌법 대응 필수
        - 주요 요구사항: 안전보건 방침, 위험성 평가, 근로자 협의 및 참여
        
        ISO 27001 (정보보안경영시스템):
        - 핵심 원칙: 기밀성, 무결성, 가용성 (CIA 삼각형)
        - 주요 영역: 위험 평가 및 처리, 접근 통제, 암호화, 물리적 보안
        - 주요 요구사항: 정보보안 방침, 위험 평가, 적용성 보고서, 보안 통제
        
        ISO 13485 (의료기기 품질경영):
        - 초점: 규제 요구사항, 위험 관리, QMS 효과성 유지
        - 적용 대상: 의료기기 제조, 설계, 유통 조직
        - 특별 요구사항: 추적성, 멸균 관리, 임상 평가
        
        ESG 경영:
        - Environmental (환경): 탄소 발자국, 자원 효율성, 환경 영향
        - Social (사회): 노동권, 다양성, 지역사회 기여
        - Governance (지배구조): 이사회 다양성, 윤리 경영, 투명성
        """

        # 3. Construct Prompt (Fact-Based: Public Data + Google Search)
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
        
        ===== Verified Company Data (금융위원회 공공데이터 API) =====
        {gov_data_summary}
        
        ===== Analysis Instructions =====
        
        **CRITICAL: You MUST use the Verified Company Data from the government API in your analysis.**
        
        ISO 인증 현황에 대한 현실적 가정:
        - 상장기업(유가증권시장/코스닥): ISO 9001은 거의 100% 보유, ISO 14001도 대부분 보유
        - 외부감사 대상 기업: ISO 9001 보유 가능성 매우 높음 (80% 이상)
        - 설립 10년 이상 + 직원 100명 이상: 기본 ISO 인증 보유 추정
        - 자동차/화학/제조업: ISO 9001, ISO 14001 필수 보유 (거래처 요구사항)
        - 따라서 "인증 부재"보다는 "갱신", "통합 인증 전환", "고도화", "ESG 연계 강화" 등의 표현 사용
        
        분석 시 고려사항:
        - 기업연령: 오래된 기업 = 안정적이나 레거시 시스템 개선 필요
        - 상장여부: 상장기업은 더 엄격한 컴플라이언스 요구
        - 외부감사: 감사받는 기업은 지배구조가 우수
        - 직원규모: 대규모 = 구현 복잡성 증가하나 자원도 풍부
        - 계열사/종속기업: 그룹사 통합 인증 전략 필요
        
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
           - Use specific numbers from Public Data (e.g., "Since 2006", "817 employees").
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
            "risk_factors": ["대규모 조직(817명, 10개 계열사) 통합 관리 복잡성", "자동차 산업 특성상 품질/환경 이중 요구사항", "상장기업으로서 ESG 공시 의무 강화 대응 필요"],
            "recommended_standards": ["ISO 9001", "ISO 14001"],
            "industry": "자동차/자동차부품",
            "summary": "2006년 설립되어 19년의 업력을 보유한 평화산업(주)는 817명의 직원과 10개 계열사를 거느린 유가증권시장 상장기업입니다. 삼일회계법인의 외부감사를 받고 있어 재무 투명성이 확보되어 있습니다.\\n\\nGoogle 검색 결과, 현재 ISO 9001 인증을 보유하고 있는 것으로 확인됩니다. (출처: KAB). 현 시점에서는 기존 인증의 갱신 및 ISO 14001 환경경영시스템과의 통합인증(IMS) 전환이 효과적입니다.\\n\\nESG 경영 강화 추세에 따라 ISO 14001을 중심으로 탄소중립 로드맵과 연계한 환경경영 고도화를 권장합니다.",
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
                result['company_name'] = company_name
                
                # Format summary with proper paragraph breaks
                if 'summary' in result and result['summary']:
                    summary = result['summary']
                    # Handle both escaped (\n) and actual newlines
                    # Replace escaped newlines with actual newlines first
                    summary = summary.replace('\\n', '\n')
                    # Split by double newlines for paragraphs
                    paragraphs = summary.split('\n\n')
                    # Wrap each paragraph in <p> tags, convert single newlines within paragraph to <br>
                    formatted_summary = ''.join([f'<p>{p.strip().replace("\n", "<br>")}</p>' for p in paragraphs if p.strip()])
                    result['summary'] = formatted_summary
                
                # Ensure risk_level exists
                if 'risk_level' not in result and 'risk_score' in result:
                    score = result['risk_score']
                    if score >= 80:
                        result['risk_level'] = "안전 (Low Risk)"
                    elif score >= 60:
                        result['risk_level'] = "주의 (Moderate Risk)"
                    else:
                        result['risk_level'] = "위험 (High Risk)"
                
                # 공공데이터 API 조회 결과 추가
                if gov_corp_data and gov_corp_data.get('found'):
                    result['verified_data'] = True
                    result['gov_data'] = gov_corp_data.get('basic_info', {})
                    result['risk_indicators'] = gov_corp_data.get('risk_indicators', {})
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
        
        # Calculate Risk Level
        if risk_score >= 80:
            risk_level = "안전 (Low Risk)"
        elif risk_score >= 60:
            risk_level = "주의 (Moderate Risk)"
        else:
            risk_level = "위험 (High Risk)"

        return {
            'company_name': company_name,
            'industry': industry,
            'risk_score': risk_score,
            'risk_level': risk_level,
            'risk_factors': ['초기 경영시스템 부재', '법적 요구사항 파악 미흡', '문서화 체계 부족'],
            'recommended_standards': ['ISO 9001', 'ISO 14001'],
            'summary': f"<strong>{company_name}</strong>은(는) {industry} 분야에서 성장 중이나, 체계적인 품질/환경 관리 시스템 도입이 시급합니다. 특히 초기 단계에서의 리스크 관리가 중요합니다."
        }

