import requests
import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load env from parent directory if needed
load_dotenv()

SERVICE_KEY = os.environ.get('DATA_GO_KR_API_KEY')
BASE_URL = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"

# Operations for different bid types
OPERATIONS = {
    "물품": "getBidPblancListInfoThngPPSSrch",
    "용역": "getBidPblancListInfoServcPPSSrch",
    "공사": "getBidPblancListInfoCnstwkPPSSrch"
}

def fetch_bids_by_type(bid_type, keyword="ISO"):
    operation = OPERATIONS.get(bid_type)
    if not operation:
        return []
    
    url = f"{BASE_URL}/{operation}"
    
    # Query for announcements from the last 7 days
    today = datetime.now()
    start_date = (today - timedelta(days=7)).strftime('%Y%m%d0000')
    end_date = today.strftime('%Y%m%d2359')
    
    params = {
        'ServiceKey': SERVICE_KEY,
        'numOfRows': '30',
        'pageNo': '1',
        'inqryDiv': '1',  # 1: 공고게시일시
        'inqryBgnDt': start_date,
        'inqryEndDt': end_date,
        'bidNtceNm': keyword,
        'type': 'json'
    }
    
    try:
        print(f"Fetching {bid_type} bids from {start_date} to {end_date}...")
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            items = data.get('response', {}).get('body', {}).get('items', [])
            
            # API can return a single dict instead of a list if only one item found
            if isinstance(items, dict):
                return [items]
            return items if isinstance(items, list) else []
        else:
            print(f"Error {response.status_code} for {bid_type}")
            return []
    except Exception as e:
        print(f"Exception fetching {bid_type}: {e}")
        return []

def main():
    if not SERVICE_KEY:
        print("Error: DATA_GO_KR_API_KEY not found in environment.")
        return

    all_bids = []
    # Fetch from all sectors
    for b_type in OPERATIONS.keys():
        bids = fetch_bids_by_type(b_type)
        for b in bids:
            if isinstance(b, dict):
                b['bid_type'] = b_type
                all_bids.append(b)
    
    # Simple deduplication by bidNtceNo
    unique_bids = {b.get('bidNtceNo'): b for b in all_bids if b.get('bidNtceNo')}.values()
    
    print(f"Total unique ISO-related bids found: {len(unique_bids)}")
    if unique_bids:
        print(json.dumps(list(unique_bids), ensure_ascii=False, indent=2))
    else:
        print("No ISO-related bids found in the specified period.")

if __name__ == "__main__":
    main()
