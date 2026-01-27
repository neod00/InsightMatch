import asyncio
import os
import sys
import json
from datetime import datetime

# Add api/scripts to sys.path
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'api', 'scripts'))

try:
    from news_scanner import NewsRiskScanner
except ImportError:
    print("Error: news_scanner.py not found in api/scripts")
    sys.exit(1)

async def fetch_general_iso_news():
    scanner = NewsRiskScanner()
    # Modify RISK_KEYWORDS temporarily for general news discovery
    import news_scanner
    news_scanner.RISK_KEYWORDS = {
        "iso_general": {
            "keywords": ["ISO 9001 인증", "ISO 14001 환경", "ISO 45001 안전", "ESG 경영 ISO", "ISO 인증 혜택"],
            "weight": 1.0,
            "related_iso": "General"
        }
    }
    
    # We use a dummy company name or just search for the keywords
    # The news_scanner search query is: f'"{company_name}" {keyword}'
    # If we pass "ISO 인증", it will search for "ISO 인증" + keywords
    print("Fetching ISO-related news...")
    result = await scanner.scan_company("ISO 인증")
    
    if result.get("total_signals", 0) > 0:
        return result["all_signals"]
    return []

if __name__ == "__main__":
    news = asyncio.run(fetch_general_iso_news())
    print(json.dumps(news, ensure_ascii=False, indent=2))
