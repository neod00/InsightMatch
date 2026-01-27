import requests
import json

def test_match_api():
    url = "http://localhost:5000/api/match"
    payload = {
        "companyName": "Test Company",
        "contactEmail": "testcompany@test.com",
        "industry": "Manufacturing",
        "employees": "51-200",
        "standards": ["ISO 9001"],
        "issues": [
            {"id": "quality_defect", "relatedISO": ["ISO 9001"]},
            {"id": "safety_incident", "relatedISO": ["ISO 45001"]}
        ],
        "timeline": "flexible",
        "budget": "unknown"
    }
    
    try:
        response = requests.post(url, json=payload)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Success!")
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        else:
            print("Error Response:")
            print(response.text)
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    test_match_api()
