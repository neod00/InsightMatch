"""
News Risk Scanner for InsightMatch AI Analysis
==============================================
Scrapes news headlines from Naver News to detect corporate risk signals.
Uses Playwright with stealth mode for bot detection bypass.

Usage:
    from news_scanner import NewsRiskScanner
    scanner = NewsRiskScanner()
    results = await scanner.scan_company("아진산업")
"""

import asyncio
import random
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import quote_plus

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError as e:
    PLAYWRIGHT_AVAILABLE = False
    print(f"[NewsScanner] Warning: playwright not installed. Error: {e}")


async def apply_stealth(page):
    """
    Manual stealth implementation - removes webdriver detection flags.
    Based on techniques from BOT_BYPASS_STRATEGY.md
    """
    await page.add_init_script("""
        // Remove webdriver property
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // Add languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['ko-KR', 'ko', 'en-US', 'en']
        });
        
        // Fake plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        
        // Prevent automation detection
        window.chrome = { runtime: {} };
    """)


# =============================================================================
# 확장된 리스크 키워드 체계 (ISO 인증별 분류)
# =============================================================================
RISK_KEYWORDS = {
    "quality": {  # ISO 9001 관련
        "keywords": ["하자", "리콜", "품질 결함", "불량", "납기 지연", "허위 표시", "소비자 불만"],
        "weight": 1.0,
        "related_iso": "ISO 9001"
    },
    "environment": {  # ISO 14001 관련
        "keywords": ["폐수 방류", "환경 오염", "유해 물질", "탄소 배출", "환경부 과징금", "ESG 등급"],
        "weight": 1.2,
        "related_iso": "ISO 14001"
    },
    "safety": {  # ISO 45001 관련
        "keywords": ["산재", "사망 사고", "추락", "질식", "중대재해", "산업안전", "근로감독", "안전사고"],
        "weight": 1.5,  # 안전 이슈는 가중치 높음
        "related_iso": "ISO 45001"
    },
    "ethics": {  # ISO 37001/27001 관련
        "keywords": ["뇌물", "리베이트", "횡령", "배임", "정보 유출", "해킹", "갑질", "직장 내 괴롭힘"],
        "weight": 1.3,
        "related_iso": "ISO 37001/27001"
    },
    "regulatory": {  # 공통 규제 리스크
        "keywords": ["영업 정지", "허가 취소", "행정 처분", "과태료", "소송", "분쟁", "세무 조사", "파업"],
        "weight": 1.0,
        "related_iso": "Common"
    }
}


class NewsRiskScanner:
    """Google News 기반 기업 리스크 스캐너 (Naver는 JS 렌더링 차단)"""
    
    def __init__(self):
        # Google News Korea search
        self.base_url = "https://www.google.com/search"
        self.results_limit = 10  # 카테고리당 최대 결과 수
        
    def _generate_queries(self, company_name: str) -> List[Dict]:
        """기업명과 리스크 키워드를 조합하여 검색 쿼리 생성"""
        queries = []
        for category, data in RISK_KEYWORDS.items():
            for keyword in data["keywords"]:
                queries.append({
                    "query": f'"{company_name}" {keyword}',
                    "category": category,
                    "keyword": keyword,
                    "weight": data["weight"],
                    "related_iso": data["related_iso"]
                })
        return queries
    
    async def scan_company(self, company_name: str) -> Dict:
        """
        특정 기업에 대한 뉴스 리스크 스캔 수행
        
        Args:
            company_name: 검색할 기업명
            
        Returns:
            Dict containing risk signals, headlines, and summary
        """
        if not PLAYWRIGHT_AVAILABLE:
            return self._mock_scan(company_name)
        
        all_signals = []
        queries = self._generate_queries(company_name)
        
        async with async_playwright() as p:
            # Browserless 원격 브라우저 또는 로컬 브라우저 선택
            browserless_url = os.environ.get('BROWSERLESS_URL')
            
            if browserless_url:
                # Browserless.io 원격 브라우저 연결
                print(f"[NewsScanner] Connecting to Browserless remote browser...")
                browser = await p.chromium.connect_over_cdp(browserless_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=self._random_user_agent(),
                    locale='ko-KR'
                )
            else:
                # 로컬 브라우저 (개발 환경용)
                print(f"[NewsScanner] Launching local browser...")
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox'
                    ]
                )
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=self._random_user_agent(),
                    locale='ko-KR'
                )
            
            page = await context.new_page()
            await apply_stealth(page)
            
            # 샘플링: 전체 쿼리 중 카테고리별 대표 키워드만 검색 (속도 최적화)
            sampled_queries = self._sample_queries(queries, max_per_category=2)
            
            for q in sampled_queries:
                try:
                    headlines = await self._search_news(page, q["query"])
                    if headlines:
                        for headline in headlines[:3]:  # 카테고리당 상위 3개
                            all_signals.append({
                                "headline": headline["title"],
                                "url": headline["url"],
                                "date": headline.get("date", ""),
                                "category": q["category"],
                                "keyword": q["keyword"],
                                "weight": q["weight"],
                                "related_iso": q["related_iso"]
                            })
                    
                    # 자연스러운 딜레이
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    
                except Exception as e:
                    print(f"[NewsScanner] Query failed: {q['query']} - {e}")
                    continue
            
            await browser.close()
        
        return self._compile_results(company_name, all_signals)
    
    async def _search_news(self, page, query: str) -> List[Dict]:
        """Google News 검색 수행 (Korean results, last 1 year)"""
        # Google News search with Korea region and 1 year filter
        search_url = f"{self.base_url}?q={quote_plus(query)}&tbm=nws&hl=ko&gl=kr&tbs=qdr:y"
        
        await page.goto(search_url, wait_until='networkidle')
        await page.wait_for_timeout(random.randint(2000, 3000))
        
        # 뉴스 목록 파싱 (Google News selectors)
        headlines = []
        
        # Google News uses div[data-hveid] or div.SoaBEf for news items
        news_items = await page.query_selector_all('div.SoaBEf')
        if not news_items:
            # Fallback selector
            news_items = await page.query_selector_all('div[data-hveid]')
        
        for item in news_items[:self.results_limit]:
            try:
                # Title is in div.n0jPhd or a > div.BNeawe
                title_el = await item.query_selector('div.n0jPhd')
                if not title_el:
                    title_el = await item.query_selector('div.BNeawe')
                if not title_el:
                    title_el = await item.query_selector('a div')
                    
                link_el = await item.query_selector('a')
                
                if title_el and link_el:
                    title = await title_el.inner_text()
                    url = await link_el.get_attribute('href')
                    
                    # Date is typically in span.WG9SHc or span containing date patterns
                    date_el = await item.query_selector('span.WG9SHc')
                    if not date_el:
                        date_el = await item.query_selector('span.LEwnzc')
                    date_text = await date_el.inner_text() if date_el else ""
                    
                    if title:
                        headlines.append({
                            "title": title.strip()[:200],
                            "url": url if url else "",
                            "date": date_text.strip()
                        })
            except Exception as e:
                print(f"[NewsScanner] Parse error: {e}")
                continue
        
        return headlines
    
    def _sample_queries(self, queries: List[Dict], max_per_category: int = 2) -> List[Dict]:
        """카테고리별로 대표 쿼리만 샘플링 (속도 최적화)"""
        sampled = []
        categories = {}
        
        for q in queries:
            cat = q["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(q)
        
        for cat, cat_queries in categories.items():
            sampled.extend(random.sample(cat_queries, min(max_per_category, len(cat_queries))))
        
        return sampled
    
    def _compile_results(self, company_name: str, signals: List[Dict]) -> Dict:
        """수집된 시그널을 분석 결과로 컴파일"""
        # 카테고리별 집계
        category_counts = {}
        weighted_score = 0
        
        for signal in signals:
            cat = signal["category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1
            weighted_score += signal["weight"]
        
        # 상위 리스크 시그널 (가중치 순 정렬)
        top_signals = sorted(signals, key=lambda x: x["weight"], reverse=True)[:5]
        
        # 리스크 레벨 판정
        if weighted_score >= 10:
            risk_level = "HIGH"
        elif weighted_score >= 5:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"
        
        return {
            "company_name": company_name,
            "scan_date": datetime.now().isoformat(),
            "total_signals": len(signals),
            "weighted_score": round(weighted_score, 2),
            "risk_level": risk_level,
            "category_breakdown": category_counts,
            "top_signals": top_signals,
            "all_signals": signals
        }
    
    def _random_user_agent(self) -> str:
        """랜덤 User-Agent 생성"""
        agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        return random.choice(agents)
    
    def _mock_scan(self, company_name: str) -> Dict:
        """Playwright 미설치 시 Mock 결과 반환"""
        return {
            "company_name": company_name,
            "scan_date": datetime.now().isoformat(),
            "total_signals": 0,
            "weighted_score": 0,
            "risk_level": "UNKNOWN",
            "category_breakdown": {},
            "top_signals": [],
            "all_signals": [],
            "error": "Playwright not installed. Run: pip install playwright playwright-stealth && playwright install chromium"
        }


# =============================================================================
# Standalone Test
# =============================================================================
async def main():
    scanner = NewsRiskScanner()
    result = await scanner.scan_company("아진산업")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
