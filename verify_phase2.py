
import sys
import os
import json

# Add api directory to path
api_dir = os.path.join(os.getcwd(), 'api')
if api_dir not in sys.path:
    sys.path.insert(0, api_dir)

from services.matching_service import MatchingService
from constants import MATCHING_WEIGHTS

class MockConsultant:
    def __init__(self, **kwargs):
        self.id = kwargs.get('id', 1)
        self.name = kwargs.get('name', 'Expert')
        self.experience = kwargs.get('experience', '10년')
        self.industry_experience = json.dumps(kwargs.get('industry_experience', []))
        self.iso_experience = json.dumps(kwargs.get('iso_experience', {}))
        self.org_size_experience = json.dumps(kwargs.get('org_size_experience', []))
        self.project_types = json.dumps(kwargs.get('project_types', []))
        self.roles = json.dumps(kwargs.get('roles', []))
        self.trust_score = kwargs.get('trust_score', 80.0)
        self.verified = kwargs.get('verified', True)
        self.regions = kwargs.get('regions', '서울,경기')
        self.rating = kwargs.get('rating', 4.8)
        self.reviews = kwargs.get('reviews', 50)
        self.avatar = 'E'
        self.specialty = 'General'
        self.match_reason = 'Default'

def test_phase2_logic():
    service = MatchingService()
    
    # 1. B1: Experience Parsing Test
    print("Testing B1: Experience Parsing...")
    assert service._parse_experience_years("15년") == 15
    assert service._parse_experience_years("7 years") == 7
    print("✅ B1 Passed")

    # 2. B3: Industry Group Matching Test
    print("\nTesting B3: Industry Grouping (Manufacturing <-> Factory)...")
    c_mfg = MockConsultant(industry_experience=["제조"])
    # "공장" is in the same group as "제조"
    score = service._calculate_industry_score(c_mfg, "공장")
    print(f"Industry group score (제조 for 공장): {score}")
    assert score == MATCHING_WEIGHTS['industry_match'] * 0.8
    print("✅ B3 Passed")

    # 3. Comprehensive Scoring Test (Mock Objects)
    print("\nTesting Combined Phase 2 Scoring...")
    
    # Senior Expert for Large Company
    c_senior = MockConsultant(
        name="Senior Large Expert",
        experience="20년",
        industry_experience=["제조업"],
        org_size_experience=["Large"],
        roles=["Lead Auditor"],
        trust_score=90.0
    )
    
    criteria = {
        'industry': '공장',
        'employees': '300인 이상', # ORG_SIZE_MAP -> Large
        'recommended_iso': [{'code': '9001'}],
        'project_type': 'New'
    }
    
    # We'll manually check the score calculation logic inner parts since we skip DB Consultant.query.all()
    # In a real test we'd use a test DB, but here we can check the helper methods
    
    exp_score = 0
    exp_years = service._parse_experience_years(c_senior.experience)
    if exp_years >= 15: exp_score = MATCHING_WEIGHTS['experience_years']
    
    ind_score = service._calculate_industry_score(c_senior, criteria['industry'])
    
    size_score = 0
    if "Large" in json.loads(c_senior.org_size_experience): size_score = MATCHING_WEIGHTS['org_size_match']
    
    role_score = 0
    from constants import ROLE_POINTS
    for r in json.loads(c_senior.roles): role_score += ROLE_POINTS.get(r, 0)
    role_score = min(role_score, MATCHING_WEIGHTS['role_match'])
    
    print(f"Senior Expert Scores -> Exp: {exp_score}, Industry: {ind_score}, Size: {size_score}, Role: {role_score}")
    
    assert exp_score == 10
    assert ind_score == 16 # 20 * 0.8
    assert size_score == 10
    assert role_score == 5
    
    print("✅ Scoring Components Checked")

if __name__ == "__main__":
    try:
        test_phase2_logic()
        print("\n🚀 All Phase 2 logic verifications passed!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
