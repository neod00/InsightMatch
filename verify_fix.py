import os
import sys
from dotenv import load_dotenv
import json

# Setup path
sys.path.append(os.path.join(os.getcwd(), 'server'))

from services.ai_service import AIService

# Load env including API keys
load_dotenv(os.path.join('server', '.env'))

def verify_ajin_analysis():
    print("Initializing AIService...")
    try:
        ai = AIService()
    except Exception as e:
        print(f"FAILED to initialize AIService: {e}")
        return

    # User provided test data
    test_data = {
        "companyName": "아진산업(주)", # Or just 아진산업
        "bzno": "515-81-07635",      # Provided specific BZNO
        "industry": "자동차 부품 제조",
        "employees": "Unknown",      # Let API find it
        "standards": ["ISO 9001", "ISO 14001"],
        "certStatus": "Unknown",
        "readiness": "Medium"
    }

    print(f"\nAnalyzing: {test_data['companyName']} (BZNO: {test_data['bzno']})...")
    
    try:
        result = ai.analyze(test_data)
        
        print("\n--- Verification Result ---")
        
        # 1. Check Public Data Integration
        if result.get('verified_data'):
            print(f"[SUCCESS] Public Data Found!")
            gov_data = result.get('gov_data', {})
            print(f"  - Company: {gov_data.get('company_name')}")
            print(f"  - Rep: {gov_data.get('representative')}")
            print(f"  - Employees: {gov_data.get('employee_count')}")
        else:
            print(f"[FAILURE] Public Data NOT Found.")
            
        # 2. Check Grounding/Search Logic
        links = result.get('evidence_links', [])
        print(f"\n[INFO] Evidence Links (Grounding): {len(links)} found")
        for link in links:
            print(f"  - {link}")
            
        # 3. Check Summary for Specifics
        summary = result.get('summary', '')
        print("\n[INFO] Summary Snippet:")
        print(summary[:300] + "...")
        
    except Exception as e:
        print(f"\n[ERROR] Analysis Exception: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_ajin_analysis()
