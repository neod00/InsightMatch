import os
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import json
import asyncio

# Import CorpInfoService
from .corp_info_service import CorpInfoService

# Import NewsRiskScanner for external signal detection
try:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
    from news_scanner import NewsRiskScanner
    NEWS_SCANNER_AVAILABLE = True
except ImportError as e:
    NEWS_SCANNER_AVAILABLE = False
    print(f"[AIService] Warning: NewsRiskScanner not available: {e}")

# Import SNSSentimentScanner for social media sentiment analysis
try:
    from sns_scanner import SNSSentimentScanner
    SNS_SCANNER_AVAILABLE = True
except ImportError as e:
    SNS_SCANNER_AVAILABLE = False
    print(f"[AIService] Warning: SNSSentimentScanner not available: {e}")

class AIService:
    def __init__(self):
        # API 키는 환경변수에서 가져오기
        api_key = os.environ.get('GOOGLE_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
            # Priority order: 2.0-flash (primary) -> 1.5-flash -> 1.5-flash-8b
            self.model_names = ['gemini-2.0-flash', 'gemini-flash-latest', 'gemini-1.5-flash-8b']
            self.primary_model_name = self.model_names[0]
            self.model = genai.GenerativeModel(self.primary_model_name)
            print(f"[AIService] Initialized with model: {self.primary_model_name}")
        else:
            self.model = None
            self.model_names = []
            print("[AIService] Warning: GOOGLE_API_KEY not found. AI Service will use mock data.")
        
        # 기업정보 API 서비스 초기화
        self.corp_info_service = CorpInfoService()
        
        # 뉴스 리스크 스캐너 초기화 (외부 시그널 탐지용)
        if NEWS_SCANNER_AVAILABLE:
            self.news_scanner = NewsRiskScanner()
        else:
            self.news_scanner = None
        
        # SNS 여론 스캐너 초기화 (네이버 블로그/카페)
        if SNS_SCANNER_AVAILABLE:
            self.sns_scanner = SNSSentimentScanner()
        else:
            self.sns_scanner = None

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
        
        # 0.5 뉴스 리스크 스캔 (외부 시그널 탐지)
        news_data = None
        news_summary = ""
        if self.news_scanner:
            try:
                # Run async scanner in sync context
                news_data = asyncio.run(self.news_scanner.scan_company(company_name))
                
                if news_data and news_data.get('total_signals', 0) > 0:
                    top_headlines = news_data.get('top_signals', [])[:5]
                    headlines_text = "\n".join([
                        f"- [{sig['category']}] {sig['headline'][:80]}... (관련: {sig['related_iso']})"
                        for sig in top_headlines
                    ])
                    
                    news_summary = f"""
                    [외부 뉴스 시그널 분석 - Google News 기반]
                    - 탐지된 리스크 시그널: {news_data.get('total_signals', 0)}건
                    - 뉴스 기반 리스크 레벨: {news_data.get('risk_level', 'N/A')}
                    - 가중치 점수: {news_data.get('weighted_score', 0)}
                    
                    [주요 부정적 뉴스 헤드라인]
                    {headlines_text}
                    
                    [카테고리별 분포]
                    {json.dumps(news_data.get('category_breakdown', {}), ensure_ascii=False)}
                    """
                    print(f"✓ 뉴스 리스크 스캔 완료: {news_data.get('total_signals', 0)}건 탐지")
                else:
                    print(f"✓ 뉴스 리스크 스캔 완료: 부정적 시그널 없음")
            except Exception as e:
                print(f"✗ 뉴스 리스크 스캔 오류: {e}")
        
        # 0.6 SNS 여론 스캔 (네이버 블로그/카페)
        sns_data = None
        sns_summary = ""
        if self.sns_scanner:
            try:
                sns_data = asyncio.run(self.sns_scanner.scan_company(company_name))
                
                if sns_data and sns_data.get('total_mentions', 0) > 0:
                    top_negative = sns_data.get('top_negative_mentions', [])[:3]
                    negative_text = "\n".join([
                        f"- [{m.get('source', 'blog')}] {m.get('title', '')[:60]}..."
                        for m in top_negative
                    ]) if top_negative else "없음"
                    
                    sns_summary = f"""
                    [SNS/커뮤니티 여론 분석 - 네이버 블로그/카페]
                    - 총 언급량: {sns_data.get('total_mentions', 0)}건
                    - 부정 비율: {sns_data.get('negative_ratio', 0)}%
                    - 여론 리스크 레벨: {sns_data.get('risk_level', 'N/A')}
                    
                    [감성 분포]
                    - 긍정: {sns_data.get('sentiment_breakdown', {}).get('positive', 0)}건
                    - 중립: {sns_data.get('sentiment_breakdown', {}).get('neutral', 0)}건
                    - 부정: {sns_data.get('sentiment_breakdown', {}).get('negative', 0)}건
                    
                    [주요 부정적 언급]
                    {negative_text}
                    """
                    print(f"✓ SNS 여론 스캔 완료: {sns_data.get('total_mentions', 0)}건 분석")
                else:
                    print(f"✓ SNS 여론 스캔 완료: 언급 없음")
            except Exception as e:
                print(f"✗ SNS 여론 스캔 오류: {e}")
        
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

        # 3. Construct Prompt with Triangulated Verification (Government Data + News Signals)
        prompt = f"""
        You are an expert ISO consultant performing TRIANGULATED VERIFICATION.
        
        **SOURCE OF TRUTH (Priority Order)**:
        1. Verified Government Data (금융위원회 API) -> PRIORITY 1 (Factual)
        2. External News Signals (Google News) -> PRIORITY 2 (Risk Indicators)
        3. User Input -> PRIORITY 3 (Subjective Claims)
        
        **CRITICAL INSTRUCTION**:
        - CROSS-REFERENCE all sources. If NEWS shows safety incidents but USER claims "High Readiness", FLAG THIS DISCREPANCY.
        - If NEWS shows 산재(safety) issues, STRONGLY RECOMMEND ISO 45001.
        - Use specific numbers from Government Data (Date, Employees).
        - **If the "External News Risk Signals" section below is empty or insufficient, USE YOUR SEARCH TOOL** to find recent (last 1 year) corporate risk signals (safety, environment, ethics) for "{company_name}".
        - Do NOT make up facts. If data is missing after searching, state "Information not found".
        
        ===== Company Profile (User Input) =====
        - Name: {company_name}
        - Industry: {industry}
        - Employees: {employees}
        - Standards: {', '.join(standards) if standards else 'Not specified'}
        - Status: {cert_status}
        - Website Data: {site_content}
        
        ===== Verified Company Data (Financial Services Commission API) =====
        {gov_data_summary if gov_data_summary else "[No Government Data Available]"}
        
        ===== External News Risk Signals (Google News Analysis) =====
        {news_summary if news_summary else "[No Negative News Signals Detected]"}
        
        ===== SNS/Community Sentiment (Naver Blog/Cafe) =====
        {sns_summary if sns_summary else "[No SNS Sentiment Data Available]"}
        
        **Task**:
        1. **Cross-Reference Check**: Compare User claims vs Government Data vs News vs SNS Sentiment.
        2. **Risk Score**: Base on ALL sources. News incidents and high negative SNS ratio increase risk.
        3. **Recommendations**: If safety news found -> ISO 45001. If environment news -> ISO 14001.
        4. **SNS Insight**: If SNS shows high negative ratio, mention brand reputation risk.
        4. **Summary**: 3 paragraphs (Korean). Quote verified facts and news headlines.
        
        **IMPORTANT**: ALL OUTPUT MUST BE IN KOREAN except for standard names (ISO 9001, etc.)
        
        Output Format (JSON only):
        {{
            "risk_score": 80,
            "risk_factors": ["공공데이터: 종업원수 754명으로 사용자 주장(1-10명)과 큰 차이", "공공데이터: 코스닥 상장기업으로 규제 요구사항 높음", "뉴스: 안전사고 관련 기사 발견"],
            "recommended_standards": ["ISO 9001", "ISO 45001"],
            "industry": "업종명 (한글)",
            "summary": "1단락 (팩트)...\\n\\n2단락 (뉴스 발견)...\\n\\n3단락 (권고사항)...",
            "evidence_links": ["URL 1"],
            "news_risk_level": "HIGH/MEDIUM/LOW"
        }}
        """

        # 4. Call Gemini API with Fallback Logic
        if self.model:
            try:
                response_text = None
                used_model = self.primary_model_name
                
                for model_name in self.model_names:
                    try:
                        print(f"[AIService] Attempting analysis with {model_name}...")
                        temp_model = genai.GenerativeModel(model_name)
                        response = temp_model.generate_content(prompt)
                        response_text = response.text
                        used_model = model_name
                        print(f"✓ Analysis succeeded with {model_name}")
                        break
                    except Exception as api_err:
                        err_msg = str(api_err)
                        if "429" in err_msg or "quota" in err_msg.lower():
                            print(f"⚠ {model_name} quota exceeded (429). Trying next model...")
                            continue
                        else:
                            print(f"✗ {model_name} failed: {err_msg}")
                            raise api_err
                
                if not response_text:
                    raise Exception("All attempted models failed or exceeded quota.")

                text = response_text
                if text.startswith("```json"):
                    text = text[7:]
                if text.endswith("```"):
                    text = text[:-3]
                
                # Sanitize text - remove potential markdown or trailing garbage
                text = text.strip()
                if not text.startswith("{") and "{" in text:
                    text = text[text.find("{"):]
                if not text.endswith("}") and "}" in text:
                    text = text[:text.rfind("}")+1]

                result = json.loads(text)
                result['ai_model_used'] = used_model
                
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
                
                # Add News Data Props
                if news_data and news_data.get('total_signals', 0) > 0:
                    result['news_data'] = {
                        'total_signals': news_data.get('total_signals', 0),
                        'risk_level': news_data.get('risk_level', 'UNKNOWN'),
                        'weighted_score': news_data.get('weighted_score', 0),
                        'top_signals': news_data.get('top_signals', [])[:3]
                    }
                
                # Add SNS Sentiment Data Props
                if sns_data and sns_data.get('total_mentions', 0) > 0:
                    result['sns_data'] = {
                        'total_mentions': sns_data.get('total_mentions', 0),
                        'negative_ratio': sns_data.get('negative_ratio', 0),
                        'risk_level': sns_data.get('risk_level', 'UNKNOWN'),
                        'sentiment_breakdown': sns_data.get('sentiment_breakdown', {})
                    }
                    
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
