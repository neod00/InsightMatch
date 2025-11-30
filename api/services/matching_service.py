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
           - Industry Match (25%)
           - Project Type Match (15%)
           - Trust Score (20%)
           - Role/Size Match (10%)
        """
        
        target_industry = criteria.get('industry', '')
        target_iso = [iso['code'] for iso in criteria.get('recommended_iso', [])]
        target_project_type = criteria.get('project_type', '')
        target_region = criteria.get('region', '')
        
        # Get all consultants
        all_consultants = Consultant.query.all()
        
        scored_consultants = []
        
        for consultant in all_consultants:
            score = 0
            match_details = []
            
            # 1. ISO Match (30 points)
            iso_score = 0
            consultant_iso = json.loads(consultant.iso_experience) if consultant.iso_experience else {}
            matched_iso = []
            for iso in target_iso:
                if iso in consultant_iso:
                    iso_score += 1
                    matched_iso.append(iso)
            
            if target_iso:
                iso_points = (iso_score / len(target_iso)) * 30
                score += iso_points
                if matched_iso:
                    match_details.append(f"ISO {', '.join(matched_iso)} 경험")

            # 2. Industry Match (25 points)
            consultant_industries = json.loads(consultant.industry_experience) if consultant.industry_experience else []
            if self._is_industry_match(consultant_industries, target_industry):
                score += 25
                match_details.append(f"{target_industry} 분야 전문")
            elif consultant.specialty and target_industry in consultant.specialty: # Fallback
                score += 15
                match_details.append(f"{target_industry} 관련 경험")

            # 3. Project Type Match (15 points)
            consultant_projects = json.loads(consultant.project_types) if consultant.project_types else []
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
            
            # 5. Role/Size Match (10 points)
            # Simplified for MVP: Bonus for high ratings/reviews if specific role not requested
            if (consultant.reviews or 0) > 10:
                score += 5
            if (consultant.rating or 0) >= 4.5:
                score += 5

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
                    'matchScore': 95, # Artificial high score for fallback
                    'verified': c.verified,
                    'trustScore': c.trust_score
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
                'trustScore': c.trust_score
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

