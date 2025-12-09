import os
import sys
from dotenv import load_dotenv

# Setup path to import 'server' module
sys.path.append(os.path.join(os.getcwd(), 'server'))

from services.ai_service import AIService

# Load env
load_dotenv(os.path.join('server', '.env'))

def test_ai_grounding():
    print("Initializing AIService...")
    ai = AIService()
    
    if not ai.model:
        print("ERROR: AI Model not initialized (Check API Key)")
        return

    # Use a real company for testing grounding
    # Samsung Electronics is a safe bet for finding verified info
    test_data = {
        "companyName": "Samsung Electronics",
        "companyUrl": "www.samsung.com",
        "industry": "Electronics",
        "employees": "100000+",
        "standards": ["ISO 9001", "ISO 14001"],
        "certStatus": "Unknown",
        "readiness": "High"
    }

    print(f"Analyzing {test_data['companyName']}...")
    try:
        result = ai.analyze(test_data)
        print("\n--- Analysis Result ---")
        print(f"Risk Score: {result.get('risk_score')}")
        print(f"Summary: {result.get('summary')[:100]}...") # Print first 100 chars
        print(f"Evidence Links: {result.get('evidence_links')}")
        
        if result.get('evidence_links'):
            print("\n[SUCCESS] Grounding returned evidence links!")
        else:
            print("\n[WARNING] No evidence links found. Grounding might not have triggered or found info.")
            
    except Exception as e:
        print(f"\n[ERROR] Analysis failed: {str(e)}")

if __name__ == "__main__":
    test_ai_grounding()
