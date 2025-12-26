"""Quick test for news scanner with Samsung Electronics"""
import asyncio
import sys
sys.path.insert(0, 'server/scripts')
from news_scanner import NewsRiskScanner

async def test():
    scanner = NewsRiskScanner()
    result = await scanner.scan_company("삼성전자")
    print(f"Total Signals: {result['total_signals']}")
    print(f"Risk Level: {result['risk_level']}")
    if result['top_signals']:
        print("Top Signals:")
        for s in result['top_signals'][:3]:
            print(f"  - {s['keyword']}: {s['headline'][:50]}...")

asyncio.run(test())
