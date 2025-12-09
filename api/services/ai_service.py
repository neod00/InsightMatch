import os
import re
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
<<<<<<< HEAD
            # Tools 설정: Google Search Retrieval 활성화 (Grounding)
            self.model = genai.GenerativeModel('gemini-2.0-flash', tools='google_search_retrieval')
=======
            self.model = genai.GenerativeModel('gemini-2.5-flash')
>>>>>>> 2f6828ad46ed154d704b0699522dd5f81f3708dd
        else:
            self.model = None
            print("Warning: GOOGLE_API_KEY not found. AI Service will use mock data.")

    def _scrape_iso_info(self, url: str, company_name: str) -> dict:
        """
        웹사이트에서 ISO 인증 관련 정보를 스크래핑합니다.
        """
        result = {
            'site_content': '',
            'iso_mentions': [],
            'certification_page_found': False
        }
        
        if not url:
            return result
            
        try:
            if not url.startswith('http'):
                url = 'https://' + url
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # 메인 페이지 스크래핑
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                body_text = soup.get_text(separator=' ', strip=True)
                result['site_content'] = body_text[:1500]
                
                # ISO 관련 키워드 검색
                iso_patterns = [
                    r'ISO\s*9001',
                    r'ISO\s*14001',
                    r'ISO\s*45001',
                    r'ISO\s*27001',
                    r'ISO\s*13485',
                    r'IATF\s*16949',
                    r'품질경영시스템',
                    r'환경경영시스템',
                    r'안전보건경영시스템',
                    r'정보보안경영시스템',
                ]
                
                for pattern in iso_patterns:
                    matches = re.findall(pattern, body_text, re.IGNORECASE)
                    if matches:
                        result['iso_mentions'].extend(matches)
                
                # 인증 관련 페이지 링크 찾기
                cert_keywords = ['인증', 'certification', 'iso', 'quality', '품질', '환경']
                for link in soup.find_all('a', href=True):
                    link_text = link.get_text().lower()
                    href = link['href'].lower()
                    if any(kw in link_text or kw in href for kw in cert_keywords):
                        result['certification_page_found'] = True
                        # 인증 페이지 스크래핑 시도
                        try:
                            cert_url = link['href']
                            if not cert_url.startswith('http'):
                                cert_url = url.rstrip('/') + '/' + cert_url.lstrip('/')
                            cert_response = requests.get(cert_url, headers=headers, timeout=5)
                            if cert_response.status_code == 200:
                                cert_soup = BeautifulSoup(cert_response.text, 'html.parser')
                                cert_text = cert_soup.get_text(separator=' ', strip=True)
                                for pattern in iso_patterns:
                                    matches = re.findall(pattern, cert_text, re.IGNORECASE)
                                    if matches:
                                        result['iso_mentions'].extend(matches)
                        except:
                            pass
                        break
                
                # 중복 제거
                result['iso_mentions'] = list(set(result['iso_mentions']))
                
        except Exception as e:
            print(f"웹사이트 스크래핑 실패: {e}")
            result['site_content'] = "Website not accessible."
        
        return result

    def analyze(self, intake_data):
        """
<<<<<<< HEAD
        Analyzes a company using Google Gemini with Search Grounding.
        Enhanced with DATA.go.kr 금융위원회 기업기본정보 API.
        STRICT MODE: Government Data > Search Results > User Input
        """
        company_name = intake_data.get('companyName', 'Unknown Company')
        url = intake_data.get('companyUrl', '')
        crno = intake_data.get('crno', '').strip().replace('-', '')
        bzno = intake_data.get('bzno', '').strip().replace('-', '')
        user_industry = intake_data.get('industry', '')  # 사용자 입력 (fallback)
        user_employees = intake_data.get('employees', '')  # 사용자 입력 (fallback)
=======
        Analyzes a company using Google Gemini based on intake data.
        """
        company_name = intake_data.get('companyName', 'Unknown Company')
        url = intake_data.get('companyUrl', '')
        industry = intake_data.get('industry', '')
        employees = intake_data.get('employees', '')
>>>>>>> 2f6828ad46ed154d704b0699522dd5f81f3708dd
        standards = intake_data.get('standards', [])
        cert_status = intake_data.get('certStatus', '')
        readiness = intake_data.get('readiness', '')
        
<<<<<<< HEAD
        # ==========================================
        # STEP 0: 공공데이터 API로 기업 정보 조회 (최우선 데이터)
        # ==========================================
        gov_corp_data = None
        verified_employee_count = None
        verified_industry = None
        verified_established = None
        verified_is_listed = False
        verified_has_audit = False
        
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
                
                # 검증된 데이터 추출
                verified_employee_count = basic_info.get('employee_count')
                verified_industry = basic_info.get('main_business') or basic_info.get('industry_code')
                verified_established = basic_info.get('established_date')
                verified_is_listed = risk_indicators.get('is_listed', False)
                verified_has_audit = risk_indicators.get('has_audit', False)
                
                print(f"✓ 공공데이터 API 조회 성공: {company_name}")
                print(f"  - 직원수: {verified_employee_count}명")
                print(f"  - 업종: {verified_industry}")
                print(f"  - 설립일: {verified_established}")
            else:
                print(f"✗ 공공데이터 API에서 '{company_name}' 기업정보를 찾지 못함")
        except Exception as e:
            print(f"✗ 공공데이터 API 오류: {e}")
        
        # ==========================================
        # STEP 1: 웹사이트 스크래핑 (ISO 인증 정보 추출)
        # ==========================================
        scrape_result = self._scrape_iso_info(url, company_name)
        site_content = scrape_result['site_content']
        iso_from_website = scrape_result['iso_mentions']
        
        if iso_from_website:
            print(f"✓ 웹사이트에서 ISO 인증 언급 발견: {iso_from_website}")
        
        # ==========================================
        # STEP 2: 최종 데이터 결정 (공공데이터 > 사용자 입력)
        # ==========================================
        final_employee_count = verified_employee_count if verified_employee_count else user_employees
        final_industry = verified_industry if verified_industry else user_industry
        final_established = verified_established if verified_established else "정보 없음"
        
        # 공공데이터 요약 생성
        gov_data_summary = ""
        if gov_corp_data and gov_corp_data.get('found'):
            basic_info = gov_corp_data.get('basic_info', {})
            risk_indicators = gov_corp_data.get('risk_indicators', {})
            
            gov_data_summary = f"""
            ★★★ 정부 공공데이터 (금융위원회 제공) - 이 정보를 최우선으로 사용하세요 ★★★
            - 회사명: {basic_info.get('corp_name', company_name)}
            - 법인등록번호: {basic_info.get('crno', 'N/A')}
            - 사업자등록번호: {basic_info.get('bzno', 'N/A')}
            - 대표자: {basic_info.get('representative', 'N/A')}
            - 설립일: {basic_info.get('established_date', 'N/A')} ({risk_indicators.get('company_age_years', 0)}년 업력)
            - ★ 종업원수: {basic_info.get('employee_count', 'N/A')}명 (정확한 수치, 사용자 입력값 무시)
            - ★ 주요사업/업종: {basic_info.get('main_business', 'N/A')} (정확한 업종, 사용자 입력값 무시)
            - 주소: {basic_info.get('address', 'N/A')}
            - 상장시장: {basic_info.get('market_type', 'N/A')}
            - 상장여부: {'상장기업' if risk_indicators.get('is_listed') else '비상장'}
            - 외부감사: {'있음 - ' + basic_info.get('auditor', '') if risk_indicators.get('has_audit') else '없음'}
            - 감사의견: {basic_info.get('audit_opinion', 'N/A')}
            - 계열회사: {len(gov_corp_data.get('affiliates', []))}개
            """
        else:
            gov_data_summary = f"""
            [공공데이터 조회 실패] - 사용자 입력 정보로 대체
            - 업종: {user_industry} (사용자 입력)
            - 직원수: {user_employees} (사용자 입력)
            """
        
        # 웹사이트 ISO 정보 요약
        website_iso_summary = ""
        if iso_from_website:
            website_iso_summary = f"""
            ★ 웹사이트에서 발견된 ISO 인증 관련 언급: {', '.join(iso_from_website)}
            (이 정보는 참고용이며, Google 검색으로 추가 검증 필요)
            """
        
        # ==========================================
        # STEP 3: 프롬프트 구성 (공공데이터 우선)
        # ==========================================
        prompt = f"""
        You are an expert ISO consultant analyzing "{company_name}".
        
        ============================================================
        ★★★ CRITICAL INSTRUCTION ★★★
        ============================================================
        
        1. **DATA PRIORITY (반드시 준수)**:
           - PRIORITY 1: Government Public Data (Below) - 직원수, 업종은 반드시 이 데이터 사용
           - PRIORITY 2: Website Scraping Results - ISO 인증 참고
           - PRIORITY 3: Google Search Results - ISO 인증현황 검증
           - PRIORITY 4: User Input - 위 데이터가 없을 때만 fallback으로 사용
        
        2. **ISO 인증 검증 (매우 중요)**:
           - Use Google Search to find: "{company_name} ISO 인증" or "{company_name} ISO 9001"
           - Search for official certification records from KAB (한국인정원) or certification bodies
           - If found: State "검색 결과 확인됨" with source
           - If NOT found: State "검색 결과 확인 불가 - 추가 확인 필요"
           - Do NOT assume certifications exist without evidence
        
        3. **STRICT RULES**:
           - 직원수: 반드시 공공데이터의 "{final_employee_count}"명 사용 (사용자 입력 "{user_employees}" 무시)
           - 업종: 반드시 공공데이터의 "{final_industry}" 사용 (사용자 입력 "{user_industry}" 무시)
           - 설립연도: 공공데이터의 "{final_established}" 사용
        
        ============================================================
        ★ VERIFIED GOVERNMENT DATA (최우선 데이터) ★
        ============================================================
        {gov_data_summary}
        
        ============================================================
        WEBSITE SCRAPING RESULTS
        ============================================================
        {website_iso_summary}
        Site Content Preview: {site_content[:500]}...
=======
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
        
        ISO 인증 현황에 대한 현실적 가정:
        - 대기업/중견기업: ISO 9001은 거의 100% 보유
        - 제조업: ISO 9001, ISO 14001 필수 보유 (거래처 요구사항)
        - 따라서 "인증 부재"보다는 "갱신", "통합 인증 전환", "고도화", "ESG 연계 강화" 등의 표현 사용
        
        Task:
        1. Risk Score (0-100, 100=안전, 0=위험)
           - 업종과 규모를 고려하여 현실적으로 산정
        
        2. Risk Factors (한국어로 3-5개)
           - "부재"보다는 "강화 필요", "고도화 필요", "통합 필요" 등의 표현 사용
           - 예시: "대규모 조직 대상 통합경영시스템 구축 복잡성", "업종 특성상 품질/환경 이중 요구사항"
        
        3. Recommended Standards
           - 업종과 규모에 맞는 ISO 인증 추천
        
        4. Summary (한국어, 문단 구분 필수)
           - 첫 문단: 기업 개요
           - 둘째 문단: 현재 보유 추정 인증과 개선/강화 포인트
           - 셋째 문단: 추천 ISO 전략
           - 문단 사이에 \\n\\n 사용
>>>>>>> 2f6828ad46ed154d704b0699522dd5f81f3708dd
        
        ============================================================
        USER INPUT (참고용, 공공데이터 없을 때만 사용)
        ============================================================
        - 사용자 선택 업종: {user_industry}
        - 사용자 선택 직원수: {user_employees}
        - 관심 인증: {', '.join(standards) if standards else 'Not specified'}
        - 현재 인증상태 (자가진단): {cert_status}
        - 준비수준: {readiness}
        
        ============================================================
        TASK
        ============================================================
        
        1. **ISO 인증현황 검색** (Google Search 사용):
           - "{company_name} ISO 인증", "{company_name} 품질경영", "{company_name} 인증서" 검색
           - 공식 인증 기록 확인 (KAB, 한국표준협회, 인증기관 등)
           - 검색 결과를 evidence_links에 포함
        
        2. **Risk Score (0-100)**: 
           - 공공데이터 기반 (상장여부, 감사여부, 업력, 규모)
           - ISO 인증 검색 결과 반영
        
        3. **Risk Factors (한국어, 3-5개)**:
           - 반드시 공공데이터 수치 인용: "{final_employee_count}명", "{final_established} 설립" 등
           - ISO 인증 검색 결과 기반 (확인됨/미확인 명시)
        
        4. **Summary (한국어, 3문단)**:
           - 문단1: 기업개요 (공공데이터 기반 - 설립일, 직원수, 업종, 상장여부)
           - 문단2: ISO 인증현황 (검색 결과 기반 - "검색 결과 ○○ 확인" 또는 "검색으로 확인 불가")
           - 문단3: 전략적 제안
           - ★ 직원수는 반드시 "{final_employee_count}명" 사용
           - ★ 업종은 반드시 "{final_industry}" 사용
        
        ============================================================
        OUTPUT FORMAT (JSON only, no markdown)
        ============================================================
        {{
            "risk_score": 75,
<<<<<<< HEAD
            "risk_factors": [
                "{final_established[:4]}년 설립, {final_employee_count}명 규모의 {final_industry} 기업으로...",
                "ISO 인증 현황: (검색 결과 기반 작성)",
                "..."
            ],
            "recommended_standards": ["ISO 9001", "ISO 14001"],
            "industry": "{final_industry}",
            "summary": "문단1: {final_established} 설립, {final_employee_count}명 규모...\\n\\n문단2: ISO 인증 검색 결과...\\n\\n문단3: 전략 제안...",
            "evidence_links": ["https://example.com/cert-info"],
            "iso_status": {{
                "verified_certs": ["ISO 9001 (검색 확인)"],
                "unverified_claims": [],
                "search_performed": true
            }}
=======
            "risk_factors": ["한국어 리스크 요인 1", "한국어 리스크 요인 2", "한국어 리스크 요인 3"],
            "recommended_standards": ["ISO 9001", "ISO 14001"],
            "industry": "Refined Industry Name",
            "summary": "첫 번째 문단 내용...\\n\\n두 번째 문단 내용...\\n\\n세 번째 문단 내용..."
>>>>>>> 2f6828ad46ed154d704b0699522dd5f81f3708dd
        }}
        """

        # ==========================================
        # STEP 4: Gemini API 호출
        # ==========================================
        if self.model:
            try:
                response = self.model.generate_content(prompt)
                text = response.text
                
                # JSON 추출
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                
                text = text.strip()
                result = json.loads(text)
                result['company_name'] = company_name
                
<<<<<<< HEAD
                # 회사명 추가
                result['company_name'] = company_name
                
                # Summary 포맷팅
=======
                # Format summary with proper paragraph breaks
>>>>>>> 2f6828ad46ed154d704b0699522dd5f81f3708dd
                if 'summary' in result and result['summary']:
                    summary = result['summary']
                    # Handle both escaped (\n) and actual newlines
                    # Replace escaped newlines with actual newlines first
                    summary = summary.replace('\\n', '\n')
                    # Split by double newlines for paragraphs
                    paragraphs = summary.split('\n\n')
<<<<<<< HEAD
                    formatted_summary = ''.join([f'<p>{p.strip().replace(chr(10), "<br>")}</p>' for p in paragraphs if p.strip()])
                    result['summary'] = formatted_summary
                
                # Risk Level 계산
=======
                    # Wrap each paragraph in <p> tags, convert single newlines within paragraph to <br>
                    formatted_summary = ''.join([f'<p>{p.strip().replace("\n", "<br>")}</p>' for p in paragraphs if p.strip()])
                    result['summary'] = formatted_summary
                
                # Ensure risk_level exists
>>>>>>> 2f6828ad46ed154d704b0699522dd5f81f3708dd
                if 'risk_level' not in result and 'risk_score' in result:
                    score = result['risk_score']
                    if score >= 80:
                        result['risk_level'] = "안전 (Low Risk)"
                    elif score >= 60:
                        result['risk_level'] = "주의 (Moderate Risk)"
                    else:
                        result['risk_level'] = "위험 (High Risk)"
<<<<<<< HEAD
                
                # 공공데이터 정보 추가
                if gov_corp_data and gov_corp_data.get('found'):
                    result['verified_data'] = True
                    result['gov_data'] = gov_corp_data.get('basic_info', {})
                    result['risk_indicators'] = gov_corp_data.get('risk_indicators', {})
                else:
                    result['verified_data'] = False
                
                # 업종 덮어쓰기 (공공데이터 우선)
                if final_industry:
                    result['industry'] = final_industry
                
=======
                        
>>>>>>> 2f6828ad46ed154d704b0699522dd5f81f3708dd
                return result
            except Exception as e:
<<<<<<< HEAD
                print(f"Gemini API Error: {e}")
                import traceback
                print(traceback.format_exc())
                
                # FAILOVER: 공공데이터만으로 부분 보고서 생성
                if gov_corp_data and gov_corp_data.get('found'):
                    info = gov_corp_data.get('basic_info', {})
                    return {
                        'company_name': company_name,
                        'industry': final_industry or user_industry,
                        'risk_score': 50,
                        'risk_level': "분석 지연 (API Error)",
                        'risk_factors': [
                            f"공공데이터 확인: {info.get('established_date', 'N/A')} 설립",
                            f"직원수: {info.get('employee_count', 'N/A')}명",
                            f"업종: {info.get('main_business', 'N/A')}",
                            "AI 분석 서비스 일시 장애"
                        ],
                        'recommended_standards': standards if standards else ["ISO 9001"],
                        'summary': f"<p><strong>[시스템 안내]</strong> AI 분석 서비스가 일시적으로 지연되고 있습니다.</p><p><strong>금융위원회 공공데이터</strong>를 통해 확인된 정보: {company_name}은(는) {info.get('established_date', 'N/A')} 설립, {info.get('employee_count', 'N/A')}명 규모의 기업입니다. 주요 사업은 {info.get('main_business', 'N/A')}입니다.</p><p>잠시 후 다시 시도하시면 상세 분석 결과를 확인하실 수 있습니다.</p>",
                        'evidence_links': ["https://www.data.go.kr"],
                        'verified_data': True,
                        'gov_data': info
                    }
                else:
                    return {
                        'company_name': company_name,
                        'industry': user_industry,
                        'risk_score': 0,
                        'risk_level': "분석 실패",
                        'risk_factors': ["AI 모델 응답 없음", "공공데이터 조회 실패"],
                        'recommended_standards': [],
                        'summary': f"<p>죄송합니다. 현재 분석 서비스를 이용할 수 없습니다.</p><p>오류: {str(e)}</p>",
                        'evidence_links': [],
                        'verified_data': False
                    }
        else:
            return {
                'company_name': company_name,
                'industry': user_industry,
                'risk_score': 0,
                'risk_level': "설정 오류",
                'risk_factors': ["API Key 미설정"],
                'recommended_standards': [],
                'summary': "<p>Google AI API Key가 설정되지 않았습니다.</p>",
                'evidence_links': [],
                'verified_data': False
            }
=======
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
        
        ISO 인증 현황에 대한 현실적 가정:
        - 대기업/중견기업: ISO 9001은 거의 100% 보유
        - 제조업: ISO 9001, ISO 14001 필수 보유 (거래처 요구사항)
        - 따라서 "인증 부재"보다는 "갱신", "통합 인증 전환", "고도화", "ESG 연계 강화" 등의 표현 사용
        
        Task:
        1. Risk Score (0-100, 100=안전, 0=위험)
           - 업종과 규모를 고려하여 현실적으로 산정
        
        2. Risk Factors (한국어로 3-5개)
           - "부재"보다는 "강화 필요", "고도화 필요", "통합 필요" 등의 표현 사용
           - 예시: "대규모 조직 대상 통합경영시스템 구축 복잡성", "업종 특성상 품질/환경 이중 요구사항"
        
        3. Recommended Standards
           - 업종과 규모에 맞는 ISO 인증 추천
        
        4. Summary (한국어, 문단 구분 필수)
           - 첫 문단: 기업 개요
           - 둘째 문단: 현재 보유 추정 인증과 개선/강화 포인트
           - 셋째 문단: 추천 ISO 전략
           - 문단 사이에 \\n\\n 사용
        
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
>>>>>>> 2f6828ad46ed154d704b0699522dd5f81f3708dd
