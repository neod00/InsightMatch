import requests
import json
import time

def test_analysis():
    url = "http://localhost:5000/api/analyze"
    data = {
        "companyName": "Test Corp",
        "companyUrl": "",
        "industry": "IT/Software",
        "employees": "11-50",
        "standards": ["ISO 9001"],
        "certStatus": "None",
        "readiness": "Initial",
        "targetDate": "2025-12-31",
        "budget": "1000-2000"
    }
    
    print("1. Starting Analysis...")
    try:
        response = requests.post(url, json=data)
        if response.status_code != 202:
            print(f"Failed to start analysis: {response.status_code}")
            return
            
        job_id = response.json().get('job_id')
        print(f"Job ID: {job_id}")
        
        print("2. Polling for results...")
        for _ in range(10):
            time.sleep(2)
            status_res = requests.get(f"http://localhost:5000/api/analyze/{job_id}")
            status_data = status_res.json()
            
            print(f"Status: {status_data.get('status')}")
            
            if status_data.get('status') == 'completed':
                result = status_data.get('result')
                print("\n3. Analysis Result:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
                
                if 'risk_level' in result:
                    print(f"\nSUCCESS: risk_level found: {result['risk_level']}")
                else:
                    print("\nFAILURE: risk_level NOT found")
                return
                
        print("Timed out waiting for results")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_analysis()
