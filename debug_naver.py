"""Debug with longer wait and networkidle to capture JS-rendered content"""
import asyncio
from playwright.async_api import async_playwright

async def debug_naver():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Search for Samsung + 리콜
        url = "https://search.naver.com/search.naver?where=news&query=%22%EC%82%BC%EC%84%B1%EC%A0%84%EC%9E%90%22%20%EB%A6%AC%EC%BD%9C&sm=tab_opt&sort=1"
        
        # Wait for networkidle to ensure JS rendering is complete
        await page.goto(url, wait_until='networkidle')
        await page.wait_for_timeout(5000)  # Extra 5 sec wait
        
        # Save page HTML for debugging
        html = await page.content()
        with open("naver_debug2.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved page to naver_debug2.html")
        
        # Count elements
        selectors = {
            'ul.list_news': await page.query_selector_all('ul.list_news'),
            'ul.list_news > li': await page.query_selector_all('ul.list_news > li'),
            'div.news_area': await page.query_selector_all('div.news_area'),
            'a[title]': await page.query_selector_all('a[title]'),
            'a.news_tit': await page.query_selector_all('a.news_tit'),
            'div.news_contents': await page.query_selector_all('div.news_contents'),
        }
        
        for sel, els in selectors.items():
            print(f"Selector '{sel}': {len(els)} elements")
            
        # Try to get first title
        first_title = await page.query_selector('a.news_tit')
        if first_title:
            title_text = await first_title.get_attribute('title')
            print(f"\nFirst title found: {title_text}")
        else:
            # Alternative: find any link with long title attribute
            all_links = await page.query_selector_all('a[title]')
            print(f"\nAll links with title attr: {len(all_links)}")
            for link in all_links[:5]:
                t = await link.get_attribute('title')
                print(f"  - {t[:60] if t else 'no title'}...")
        
        await browser.close()

asyncio.run(debug_naver())
