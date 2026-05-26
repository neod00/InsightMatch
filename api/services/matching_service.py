import sys
import os

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from models import Consultant
import json

class MatchingService:
    def match_consultants(self, criteria):
        """
        Matches consultants based on multi-dimensional criteria.
        Algorithm:
        1. Filter (Region, Budget - not fully impl in MVP)
        2. Score (Weighted Sum)
           - ISO Match (30%)
           - Industry Match (20%)
           - Project Type Match (15%)
           - Trust Score (20%)
           - Region Match (5%)
           - Timeline/Availability (5%)
           - Reviews/Rating (5%)
        """
        
        target_industry = criteria.get('industry', '')
        target_iso = [iso['code'] for iso in criteria.get('recommended_iso', [])]
        target_project_type = criteria.get('project_type', '')
        target_region = criteria.get('region', '')
        target_timeline = criteria.get('timeline', 'flexible')
        
        # BUG-007 Fix: 검증된 컨설턴트만 매칭 (상태가 'verified'이거나 verified=True)
        all_consultants = Consultant.query.filter(
            (Consultant.verified == True) | (Consultant.status == 'verified')
        ).all()
        
        # Fallback: 검증된 컨설턴트가 없으면 전체 포함
        if not all_consultants:
            all_consultants = Consultant.query.all()
        
        scored_consultants = []
        
        for consultant in all_consultants:
            score = 0
            match_details = []
            
            # BUG-008 Fix: JSON 파싱 에러 핸들링
            try:
                consultant_iso = json.loads(consultant.iso_experience) if consultant.iso_experience else {}
            except (json.JSONDecodeError, TypeError):
                consultant_iso = {}
            iso_score = 0
            matched_iso = []
            for iso in target_iso:
                if iso in consultant_iso:
                    iso_score += 1
                    matched_iso.append(iso)
            
            if target_iso:
                iso_points = (iso_score / len(target_iso)) * 30
                score += iso_points
                if matched_iso:
                    match_details.append(f"{', '.join(matched_iso)} 경험")

            # 2. Industry Match (20 points - reduced from 25)
            try:
                consultant_industries = json.loads(consultant.industry_experience) if consultant.industry_experience else []
            except (json.JSONDecodeError, TypeError):
                consultant_industries = []
            if self._is_industry_match(consultant_industries, target_industry):
                score += 20
                match_details.append(f"{target_industry} 분야 전문")
            elif consultant.specialty and target_industry in consultant.specialty: # Fallback
                score += 12
                match_details.append(f"{target_industry} 관련 경험")

            # 3. Project Type Match (15 points)
            try:
                consultant_projects = json.loads(consultant.project_types) if consultant.project_types else []
            except (json.JSONDecodeError, TypeError):
                consultant_projects = []
            if target_project_type and target_project_type in consultant_projects:
                score += 15
                match_details.append(f"{target_project_type} 프로젝트 경험")

            # 4. Trust Score (20 points)
            # Base trust score (0-100) -> 0-10 points
            # Verified badge -> +10 points
            trust_points = (consultant.trust_score or 0) * 0.1
            if consultant.verified:
                trust_points += 10
            score += min(trust_points, 20)
            
            # 5. Region Match (5 points - NEW)
            if target_region and consultant.regions:
                consultant_regions = [r.strip() for r in consultant.regions.split(',')]
                if target_region in consultant_regions:
                    score += 5
                    match_details.append(f"{target_region} 지역 활동")
            
            # 6. Timeline/Availability Fit (5 points - NEW)
            # Bonus for consultants with lower project load (higher availability)
            # For MVP: give points based on trust_score as proxy for availability
            if target_timeline in ['urgent', '1month']:
                # Urgent timeline - prefer highly rated consultants who can deliver fast
                if (consultant.rating or 0) >= 4.5:
                    score += 5
                    match_details.append("긴급 대응 가능")
            else:
                # Flexible timeline - small bonus for all
                score += 2
            
            # 7. Reviews/Rating (5 points - reduced from 10)
            if (consultant.reviews or 0) > 10:
                score += 2.5
            if (consultant.rating or 0) >= 4.5:
                score += 2.5

            scored_consultants.append({
                'consultant': consultant,
                'score': score,
                'match_details': match_details
            })
            
        # Sort by score desc
        scored_consultants.sort(key=lambda x: x['score'], reverse=True)
        
        # Fallback: If no good matches (score < 10), pick top rated consultants
        if not scored_consultants or scored_consultants[0]['score'] < 10:
             # Get top 3 by trust score
             top_consultants = sorted(all_consultants, key=lambda x: x.trust_score or 0, reverse=True)[:3]
             results = []
             for c in top_consultants:
                 results.append({
                    'id': c.id,
                    'name': c.name,
                    'avatar': c.avatar,
                    'specialty': c.specialty,
                    'experience': c.experience,
                    'rating': c.rating,
                    'reviews': c.reviews,
                    'matchReason': "분야별 최우수 전문가 (강력 추천)",
                    'matchScore': min(int((c.trust_score or 0) * 0.6 + 30), 80),
                    'verified': c.verified,
                    'trustScore': c.trust_score,
                    # BUG-009 Fix: 프로필 정보 추가
                    'profileImageUrl': c.profile_image_url,
                    'bio': c.bio,
                    'companyName': c.company_name
                })
             return results

        # Return top matches
        results = []
        for item in scored_consultants[:20]: # Return top 20 for pagination
            c = item['consultant']
            
            results.append({
                'id': c.id,
                'name': c.name,
                'avatar': c.avatar,
                'specialty': c.specialty,
                'experience': c.experience,
                'rating': c.rating,
                'reviews': c.reviews,
                'matchReason': item['match_details'][0] if item['match_details'] else c.match_reason,
                'matchScore': round(item['score']),
                'verified': c.verified,
                'trustScore': c.trust_score,
                # BUG-009 Fix: 프로필 정보 추가
                'profileImageUrl': c.profile_image_url,
                'bio': c.bio,
                'companyName': c.company_name
            })
            
        return results

    def _is_industry_match(self, consultant_industries, target_industry):
        if not target_industry:
            return False
        
        # Direct match
        if target_industry in consultant_industries:
            return True
            
        # Keyword match (Simple version)
        for ind in consultant_industries:
            if target_industry in ind or ind in target_industry:
                return True
                
        return False

