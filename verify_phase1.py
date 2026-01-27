
import sys
import os

# Add api directory to path
api_dir = os.path.join(os.getcwd(), 'api')
if api_dir not in sys.path:
    sys.path.insert(0, api_dir)

from constants import normalize_iso_code
from services.matching_service import MatchingService

def test_iso_normalization():
    print("--- Testing ISO Normalization ---")
    codes = ["ISO 9001:2015", "iso9001", "9001:2015", "ISO 14001", "45001:2018"]
    for code in codes:
        normalized = normalize_iso_code(code)
        print(f"Original: {code:15} -> Normalized: {normalized}")
        
    assert normalize_iso_code("ISO 9001:2015") == "9001"
    assert normalize_iso_code("iso14001") == "14001"
    print("✅ Normalization tests passed!")

def test_matching_logic_mock():
    print("\n--- Testing Matching Logic (Mock) ---")
    # Note: Full matching requires DB context, here we test the logic integration
    service = MatchingService()
    
    # Check if _is_industry_match handles case sensitivity and keywords
    industries = ["Manufacturing", "IT Services"]
    assert service._is_industry_match(industries, "manufacturing") == True
    assert service._is_industry_match(industries, "IT") == True
    assert service._is_industry_match(industries, "Food") == False
    
    print("✅ Industry match helper tests passed!")

if __name__ == "__main__":
    try:
        test_iso_normalization()
        test_matching_logic_mock()
        print("\n🚀 All Phase 1 logic verifications passed!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
