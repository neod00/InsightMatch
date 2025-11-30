import requests
import json

def test_consultants():
    # Test with a non-matching industry to trigger fallback
    url = "http://localhost:5000/api/consultants"
    params = {
        "industry": "Space Travel", 
        "iso": ["ISO 99999"] # Non-existent ISO
    }
    
    print("Testing Consultant Matching (Fallback)...")
    try:
        response = requests.get(url, params=params)
        consultants = response.json()
        
        print(f"Found {len(consultants)} consultants")
        
        if len(consultants) > 0:
            print("Top Consultant:")
            print(json.dumps(consultants[0], indent=2, ensure_ascii=False))
            
            if consultants[0].get('matchScore') == 95:
                print("\nSUCCESS: Fallback logic triggered (Match Score 95)")
            else:
                print(f"\nNOTE: Normal matching (Score {consultants[0].get('matchScore')})")
        else:
            print("\nFAILURE: No consultants found")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_consultants()
