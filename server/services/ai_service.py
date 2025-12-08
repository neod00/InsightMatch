import os
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import json

class AIService:
    def __init__(self):
        # API 키는 환경변수에서 가져오기
        api_key = os.environ.get('GOOGLE_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-2.5-flash')
        else:
            self.model = None
            print("Warning: GOOGLE_API_KEY not found. AI Service will use mock data.")

    def analyze(self, intake_data):
        """
        Analyzes a company using Google Gemini based on intake data.
        """
        company_name = intake_data.get('companyName', 'Unknown Company')
        url = intake_data.get('companyUrl', '')
        industry = intake_data.get('industry', '')
        employees = intake_data.get('employees', '')
        standards = intake_data.get('standards', [])
        cert_status = intake_data.get('certStatus', '')
        readiness = intake_data.get('readiness', '')
        
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

        # 3. Construct Prompt
        prompt = f"""
        You are an expert ISO consultant. Analyze the following company profile and provide a risk assessment and certification strategy.
        
        {ISO_KNOWLEDGE}
        
        Company Profile:
        - Name: {company_name}
        - Industry: {industry}
        - Employees: {employees}
        - Interested Standards: {', '.join(standards) if standards else 'Not specified'}
        - Current Status: {cert_status}
        - Readiness Level: {readiness}
        - Website Data: {site_content}
        
        Task:
        1. Assess the company's Risk Score (0-100, where 100 is safe, 0 is critical risk).
        2. Identify 3-5 key Risk Factors in KOREAN (한국어) based on industry and size.
           - Each risk factor should be a clear, concise sentence in Korean
           - Examples: "규모와 구조로 인한 구현 복잡성", "초기 준비 수준으로 인한 문화 및 절차적 변화 필요"
        3. Recommend the best ISO standards strategy (Single vs Integrated).
        4. Write a professional summary in KOREAN (한국어) explaining why these standards are needed.
           - Use proper paragraph breaks (\\n\\n) to separate different topics
           - Format: First paragraph about company overview, second about ISO 9001, third about ISO 14001, etc.
           - Cite specific ISO principles from the context
           - Each paragraph should be 2-4 sentences
        
        Output Format (JSON only):
        {{
            "risk_score": 75,
            "risk_factors": ["한국어 리스크 요인 1", "한국어 리스크 요인 2", "한국어 리스크 요인 3"],
            "recommended_standards": ["ISO 9001", "ISO 14001"],
            "industry": "Refined Industry Name",
            "summary": "첫 번째 문단 내용...\\n\\n두 번째 문단 내용...\\n\\n세 번째 문단 내용..."
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
