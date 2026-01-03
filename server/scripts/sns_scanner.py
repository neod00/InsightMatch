"""
SNS Sentiment Scanner for InsightMatch AI Analysis
===================================================
Scrapes Naver Blog/Cafe to detect corporate sentiment signals.
Uses Playwright with Browserless for serverless compatibility.

Usage:
    from sns_scanner import SNSSentimentScanner
    scanner = SNSSentimentScanner()
    results = await scanner.scan_company("삼성전자")
"""

import asyncio
import random
import json
import os
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import quote_plus

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError as e:
    PLAYWRIGHT_AVAILABLE = False
    print(f"[SNSScanner] Warning: playwright not installed. Error: {e}")


# =============================================================================
# 감성 분석용 키워드 (부정/긍정)
# =============================================================================
NEGATIVE_KEYWORDS = [
    # 품질 및 서비스 문제
    "하자", "불량", "고장", "결함", "리콜", "불친절", "최악", "실망", "짜증",
    # 안전 및 사고 (S - Social)
    "사고", "위험", "부상", "폭발", "화재", "산업재해",
    # 노동 및 조직문화 (S - Social)
    "갑질", "야근", "퇴사", "해고", "부당", "괴롭힘", "꼰대", "블라인드", "잡플래닛",
    # 윤리 및 지배구조 (G - Governance)
    "논란", "횡령", "배임", "비리", "독과점", "담합", "수사"
]

POSITIVE_KEYWORDS = [
    "만족", "좋아요", "추천", "최고", "감사", "친절", "빠른", "훌륭", "대박"
]


async def apply_stealth(page):
    """Manual stealth implementation for bot detection bypass"""
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)


class SNSSentimentScanner:
    """네이버 블로그/카페 기반 기업 여론 스캐너"""
    
    def __init__(self):
        # Naver search base URL
        self.base_url = "https://search.naver.com/search.naver"
        self.results_limit = 10
        
    def _generate_queries(self, company_name: str) -> List[str]:
        """기업명 기반 ESG 및 평판 검색 쿼리 생성"""
        # 포괄적 여론 탐지를 위해 따옴표 제거 및 주제 확장
        queries = [
            f'{company_name} 조직문화',
            f'{company_name} 뉴스',
            f'{company_name} 논란',
            f'{company_name} 근무환경',
            f'{company_name} 품질',
            f'{company_name} ESG',
            f'{company_name} 서비스 불만'
        ]
        return queries
    
    async def scan_company(self, company_name: str) -> Dict:
        """
        기업 SNS 여론 스캔
        
        Args:
            company_name: 분석할 기업명
            
        Returns:
            Dict containing sentiment analysis results
        """
        if not PLAYWRIGHT_AVAILABLE:
            return self._mock_scan(company_name)
        
        all_mentions = []
        queries = self._generate_queries(company_name)
        
        async with async_playwright() as p:
            # Browserless 원격 또는 로컬 브라우저
            browserless_url = os.environ.get('BROWSERLESS_URL')
            
            if browserless_url:
                print(f"[SNSScanner] Connecting to Browserless...")
                browser = await p.chromium.connect_over_cdp(browserless_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=self._random_user_agent(),
                    locale='ko-KR'
                )
            else:
                print(f"[SNSScanner] Launching local browser...")
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=self._random_user_agent(),
                    locale='ko-KR'
                )
            
            page = await context.new_page()
            await apply_stealth(page)
            
            # Vercel 환경에서도 최소한의 다양성 확보
            if browserless_url:
                queries = queries[:4]
            
            for query in queries:
                try:
                    mentions = await self._search_blog_cafe(page, query)
                    all_mentions.extend(mentions)
                    
                    # 딜레이
                    delay = random.uniform(0.5, 1.0) if browserless_url else random.uniform(1.0, 2.0)
                    await asyncio.sleep(delay)
                    
                except Exception as e:
                    print(f"[SNSScanner] Query failed: {query} - {e}")
                    continue
            
            await browser.close()
        
        return self._analyze_sentiment(company_name, all_mentions)
    
    async def _search_blog_cafe(self, page, query: str) -> List[Dict]:
        """네이버 블로그/카페 검색"""
        # 블로그 탭 검색
        search_url = f"{self.base_url}?where=blog&query={quote_plus(query)}&sm=tab_opt&nso=so:r,p:3m"
        
        await page.goto(search_url, wait_until='domcontentloaded')
        await page.wait_for_timeout(random.randint(1500, 2500))
        
        mentions = []
        
        # 블로그 포스트 파싱
        posts = await page.query_selector_all('li.bx')
        if not posts:
            posts = await page.query_selector_all('div.total_wrap')
        
        for post in posts[:self.results_limit]:
            try:
                # 제목 추출
                title_el = await post.query_selector('a.title_link')
                if not title_el:
                    title_el = await post.query_selector('a.api_txt_lines')
                
                # 본문 미리보기 추출
                desc_el = await post.query_selector('div.dsc_wrap')
                if not desc_el:
                    desc_el = await post.query_selector('div.api_txt_lines')
                
                if title_el:
                    title = await title_el.inner_text()
                    url = await title_el.get_attribute('href')
                    desc = await desc_el.inner_text() if desc_el else ""
                    
                    # 텍스트 결합
                    full_text = f"{title} {desc}"
                    
                    # 감성 분석
                    sentiment = self._analyze_text_sentiment(full_text)
                    
                    mentions.append({
                        "title": title.strip()[:100],
                        "url": url or "",
                        "text_preview": desc.strip()[:200] if desc else "",
                        "sentiment": sentiment,
                        "source": "naver_blog"
                    })
                    
            except Exception as e:
                continue
        
        return mentions
    
    def _analyze_text_sentiment(self, text: str) -> str:
        """단순 키워드 기반 감성 분석"""
        text_lower = text.lower()
        
        neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)
        pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
        
        if neg_count > pos_count:
            return "negative"
        elif pos_count > neg_count:
            return "positive"
        else:
            return "neutral"
    
    def _analyze_sentiment(self, company_name: str, mentions: List[Dict]) -> Dict:
        """전체 여론 분석 결과 생성"""
        total = len(mentions)
        negative = sum(1 for m in mentions if m["sentiment"] == "negative")
        positive = sum(1 for m in mentions if m["sentiment"] == "positive")
        neutral = total - negative - positive
        
        # 부정 비율 계산
        neg_ratio = (negative / total * 100) if total > 0 else 0
        
        # 리스크 레벨 판정
        if neg_ratio >= 40:
            risk_level = "HIGH"
        elif neg_ratio >= 20:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"
        
        # 주요 부정 키워드 추출
        negative_mentions = [m for m in mentions if m["sentiment"] == "negative"]
        top_negative = negative_mentions[:3]
        
        return {
            "company_name": company_name,
            "scan_date": datetime.now().isoformat(),
            "total_mentions": total,
            "sentiment_breakdown": {
                "positive": positive,
                "neutral": neutral,
                "negative": negative
            },
            "negative_ratio": round(neg_ratio, 1),
            "risk_level": risk_level,
            "top_negative_mentions": top_negative,
            "all_mentions": mentions
        }
    
    def _random_user_agent(self) -> str:
        """랜덤 User-Agent 반환"""
        agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        ]
        return random.choice(agents)
    
    def _mock_scan(self, company_name: str) -> Dict:
        """Playwright 미설치 시 Mock 결과"""
        return {
            "company_name": company_name,
            "scan_date": datetime.now().isoformat(),
            "total_mentions": 0,
            "sentiment_breakdown": {"positive": 0, "neutral": 0, "negative": 0},
            "negative_ratio": 0,
            "risk_level": "UNKNOWN",
            "top_negative_mentions": [],
            "all_mentions": [],
            "error": "Playwright not installed"
        }


# =============================================================================
# Standalone Test
# =============================================================================
async def main():
    scanner = SNSSentimentScanner()
    result = await scanner.scan_company("삼성전자")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
