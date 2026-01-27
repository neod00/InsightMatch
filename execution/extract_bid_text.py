import asyncio
import os
import re
import json
import sys
from playwright.async_api import async_playwright

async def extract_bid_content(bid_url):
    """
    Uses Playwright to render the G2B bid page and extract text.
    """
    async with async_playwright() as p:
        print(f"Launching browser to visit: {bid_url}")
        browser = await p.chromium.launch(headless=True)
        # Fix: correctly await context and new_page
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = await context.new_page()
        
        try:
            # G2B can be slow, increase timeout
            await page.goto(bid_url, wait_until="load", timeout=90000)
            
            # Wait for some typical content or iframes to load
            await asyncio.sleep(5) 
            
            # G2B detail pages often use iframes. Check both main frame and child frames.
            all_text = ""
            frames = page.frames
            print(f"Detected {len(frames)} frames. Extracting text...")
            for frame in frames:
                try:
                    # Use evaluate to get all text content from the frame
                    content = await frame.evaluate("() => document.body.innerText")
                    all_text += "\n" + content
                except Exception as e:
                    # print(f"Frame skip: {e}")
                    continue
            
            # Find attachment links
            attachment_links = []
            links = await page.query_selector_all("a")
            for link in links:
                try:
                    href = await link.get_attribute("href")
                    if href and any(ext in href.lower() for ext in ['.pdf', '.hwp', '.doc', '.docx']):
                        if href.startswith('/'):
                            attachment_links.append(f"https://www.g2b.go.kr{href}")
                        else:
                            attachment_links.append(href)
                except:
                    continue
            
            await browser.close()
            return {
                "text": all_text,
                "attachments": list(set(attachment_links))
            }
        except Exception as e:
            await browser.close()
            return {"error": str(e)}

def analyze_iso_context(text):
    """
    Simple keyword based context extractor.
    """
    keywords = ["ISO 9001", "ISO 14001", "ISO 45001", "품질경영", "환경경영", "안전보건", "가점", "필수조건", "인증보유"]
    findings = []
    
    # Clean text: remove extra spaces and newlines
    clean_text = " ".join(text.split())
    
    for kw in keywords:
        # Case-insensitive search using Regex
        matches = list(re.finditer(re.escape(kw), clean_text, re.IGNORECASE))
        for m in matches:
            start = max(0, m.start() - 150)
            end = min(len(clean_text), m.end() + 150)
            findings.append({
                "keyword": kw,
                "context": clean_text[start:end]
            })
    return findings

async def main():
    if len(sys.argv) < 2:
        # Test with the ISO 45001 bid found earlier
        url = "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=202601BK01292915&bidPbancOrd=000"
    else:
        url = sys.argv[1]
        
    result = await extract_bid_content(url)
    if "error" in result:
        print(f"Error: {result['error']}")
        return
    
    print(f"Extraction successful. Text length: {len(result['text'])}")
    print(f"Attachments found: {len(result['attachments'])}")
    
    analysis = analyze_iso_context(result['text'])
    print(f"ISO Signals detected in text: {len(analysis)}")
    
    # Deduplicate context (sometimes keywords appear close to each other)
    seen_contexts = set()
    for entry in analysis:
        # Use first 50 chars as key
        key = entry['context'][:50]
        if key not in seen_contexts:
            print(f"--- ISO Signal Match ---")
            print(f"Keyword: {entry['keyword']}")
            print(f"Context: ...{entry['context']}...")
            seen_contexts.add(key)
        if len(seen_contexts) >= 5: break # Show max 5

if __name__ == "__main__":
    asyncio.run(main())
