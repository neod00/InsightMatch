import requests
import json
import sys
import os

# Base URL for the API
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')

def publish_post(title, content, tags, image_url, author="InsightMatch AI"):
    url = f"{BASE_URL}/api/posts"
    
    payload = {
        "title": title,
        "content": content,
        "tags": tags, # Comma separated string expected by API
        "image_url": image_url,
        "author": author
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 201:
            print(f"Successfully published post: {title}")
            return response.json()
        else:
            print(f"Failed to publish post. Status: {response.status_code}")
            print(response.text)
            return None
    except Exception as e:
        print(f"Error connecting to API: {e}")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python publish_blog.py <title> <content> [tags] [image_url]")
        sys.exit(1)
        
    title = sys.argv[1]
    content = sys.argv[2]
    tags = sys.argv[3] if len(sys.argv) > 3 else "ISO,인증"
    image_url = sys.argv[4] if len(sys.argv) > 4 else ""
    
    publish_post(title, content, tags, image_url)
