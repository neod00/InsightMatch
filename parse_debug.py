"""Find actual news item containers in Naver's div-based structure"""
from bs4 import BeautifulSoup

with open("naver_debug.html", "r", encoding="utf-8") as f:
    soup = BeautifulSoup(f.read(), "html.parser")

# Find all links that look like news titles (have title attribute and contain keywords)
all_links = soup.find_all("a", href=True)
news_links = []
for a in all_links:
    title = a.get("title", "")
    if title and len(title) > 15:  # Titles are typically longer
        news_links.append({
            "title": title[:80],
            "href": a.get("href", "")[:60],
            "class": a.get("class", [])
        })

print(f"Found {len(news_links)} potential news title links")
for n in news_links[:10]:
    print(f"  class={n['class']}, title='{n['title']}'")
    
# Now find parent structure of these links
if news_links:
    print("\n--- Analyzing parent structure of first news link ---")
    sample = soup.find("a", title=news_links[0]["title"])
    if sample:
        for i, parent in enumerate(sample.parents):
            if i > 5: break
            print(f"  Parent {i}: {parent.name}, class={parent.get('class')}")
