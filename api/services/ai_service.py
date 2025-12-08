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
            self.model = genai.GenerativeModel('gemini-pro')
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

        # 3. Construct Prompt (공공데이터 정보 포함)
        prompt = f"""
        You are an expert ISO consultant. Analyze the following company profile and provide a risk assessment and certification strategy.
        
        {ISO_KNOWLEDGE}
        
        ===== Company Profile (사용자 입력) =====
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
        IMPORTANT: If government verified data is available, use it to provide MORE ACCURATE risk assessment.
        Consider these factors from verified data:
        - Company age (older = more stable, but may have legacy issues)
        - Listed status (listed companies have stricter compliance requirements)
        - External audit status (audited companies have better governance)
        - Employee scale (affects complexity of ISO implementation)
        - Governance level (affects readiness for certification)
        
        Task:
        1. Assess the company's Risk Score (0-100, where 100 is safe, 0 is critical risk).
           - Use verified data when available to make score more accurate
           - Consider company age, audit status, and governance level
        2. Identify 3-5 key Risk Factors based on industry, size, and verified data.
        3. Recommend the best ISO standards strategy (Single vs Integrated).
        4. Write a professional summary (Korean) explaining why these standards are needed.
           - Reference specific company data when available (설립연도, 직원수, 감사여부 등)
           - Cite specific ISO principles from the context
        
        Output Format (JSON only):
        {{
            "risk_score": 75,
            "risk_factors": ["Risk 1", "Risk 2", "Risk 3"],
            "recommended_standards": ["ISO 9001", "ISO 14001"],
            "industry": "Refined Industry Name",
            "summary": "Professional summary in Korean with specific company details..."
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

