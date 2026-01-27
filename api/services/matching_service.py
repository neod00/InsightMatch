import sys
import os
import re
import json

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from models import Consultant
from constants import (
    normalize_iso_code, 
    MATCHING_WEIGHTS, 
    FALLBACK_BASE_SCORE,
    INDUSTRY_GROUPS,
    ORG_SIZE_MAP,
    ROLE_POINTS
)

class MatchingService:
    def match_consultants(self, criteria):
        """
        Matches consultants based on multi-dimensional criteria (Phase 2).
        
        New in Phase 2:
        - B1: Years of Experience scoring
        - B2: Organization Size matching
        - B3: Industry Groupings (Keyword expansion)
        - B4: Role-based importance points
        """
        
        target_industry = criteria.get('industry', '')
        target_employees = criteria.get('employees', '')
        target_iso = [normalize_iso_code(iso['code']) for iso in criteria.get('recommended_iso', [])]
        target_project_type = criteria.get('project_type', '')
        target_region = criteria.get('region', '')
        target_timeline = criteria.get('timeline', 'flexible')
        
        # B2: Normalize target company size
        target_size = ORG_SIZE_MAP.get(target_employees, 'Medium')
        
        # Get all consultants
        all_consultants = Consultant.query.all()
        
        scored_consultants = []
        
        for consultant in all_consultants:
            score = 0
            match_details = []
            
            # --- 1. ISO Match (25 points) ---
            iso_score = 0
            consultant_iso_raw = json.loads(consultant.iso_experience) if consultant.iso_experience else {}
            consultant_iso = {normalize_iso_code(k): v for k, v in consultant_iso_raw.items()}
            
            matched_iso = []
            for iso in target_iso:
                if iso in consultant_iso:
                    iso_score += 1
                    matched_iso.append(iso)
            
            if target_iso:
                iso_points = (iso_score / len(target_iso)) * MATCHING_WEIGHTS['iso_match']
                score += iso_points
                if matched_iso:
                    match_details.append(f"ISO {', '.join(matched_iso)} 전문가")

            # --- 2. Industry Match (20 points - B3: Enhanced) ---
            industry_match_score = self._calculate_industry_score(consultant, target_industry)
            score += industry_match_score
            if industry_match_score >= MATCHING_WEIGHTS['industry_match'] * 0.8:
                match_details.append(f"{target_industry} 분야 풍부한 경험")
            elif industry_match_score > 0:
                match_details.append(f"{target_industry} 관련 분야 수행")

            # --- 3. Experience Years (10 points - B1: NEW) ---
            exp_years = self._parse_experience_years(consultant.experience)
            if exp_years >= 15:
                score += MATCHING_WEIGHTS['experience_years'] # Max
                match_details.append(f"{exp_years}년차 베테랑")
            elif exp_years >= 10:
                score += MATCHING_WEIGHTS['experience_years'] * 0.8
                match_details.append(f"{exp_years}년 숙련 전문가")
            elif exp_years >= 5:
                score += MATCHING_WEIGHTS['experience_years'] * 0.5
            
            # --- 4. Org Size Match (10 points - B2: NEW) ---
            consultant_org_sizes = json.loads(consultant.org_size_experience) if consultant.org_size_experience else []
            if target_size in consultant_org_sizes:
                score += MATCHING_WEIGHTS['org_size_match']
                size_kr = {'Small':'소기업', 'Medium':'중소/중견', 'Large':'대기업'}.get(target_size, '')
                match_details.append(f"{size_kr} 맞춤형 컨설팅")

            # --- 5. Project Type Match (10 points) ---
            consultant_projects = json.loads(consultant.project_types) if consultant.project_types else []
            if target_project_type and target_project_type in consultant_projects:
                score += MATCHING_WEIGHTS['project_type']
                match_details.append(f"{target_project_type} 최적화")

            # --- 6. Trust & Roles (20 points - B4: Enhanced) ---
            # Trust score (max 15)
            trust_points = (consultant.trust_score or 0) * 0.1
            if consultant.verified:
                trust_points += 5 # Badge bonus
            score += min(trust_points, MATCHING_WEIGHTS['trust_verified'])
            
            # Role points (max 5 - B4)
            consultant_roles = json.loads(consultant.roles) if consultant.roles else []
            role_points = 0
            for r in consultant_roles:
                role_points += ROLE_POINTS.get(r, 0)
            score += min(role_points, MATCHING_WEIGHTS['role_match'])
            if 'Lead Auditor' in consultant_roles or '심사원' in consultant_roles:
                match_details.append("공인 심사 자격 보유")

            # --- 7. Region & Timeline (5 points total) ---
            # Region (3)
            if target_region and consultant.regions and target_region in consultant.regions:
                score += MATCHING_WEIGHTS['region_match']
            # Timeline (2)
            if target_timeline in ['urgent', '1month'] and (consultant.rating or 0) >= 4.5:
                score += MATCHING_WEIGHTS['timeline_fit']

            scored_consultants.append({
                'consultant': consultant,
                'score': score,
                'match_details': match_details
            })
            
        # Sort by score desc
        scored_consultants.sort(key=lambda x: x['score'], reverse=True)
        
        # Fallback (A2 Fixed in v1, refined in v2)
        if not scored_consultants or scored_consultants[0]['score'] < 10:
            return self._get_fallback_results(all_consultants)

        # Return top matches
        results = []
        for item in scored_consultants[:20]:
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
                'matchScore': round(min(item['score'], 99)), # Realistic cap
                'verified': c.verified,
                'trustScore': c.trust_score
            })
            
        return results

    def _parse_experience_years(self, exp_str):
        """Extract number from strings like '15년', '10 years', etc."""
        if not exp_str: return 0
        match = re.search(r'(\d+)', str(exp_str))
        return int(match.group(1)) if match else 0

    def _calculate_industry_score(self, consultant, target_industry):
        """B3: Enhanced industry matching using groups and keywords."""
        if not target_industry: return 0
        
        consultant_industries = json.loads(consultant.industry_experience) if consultant.industry_experience else []
        weight = MATCHING_WEIGHTS['industry_match']
        
        # 1. Direct match (Full points)
        if target_industry in consultant_industries:
            return weight
            
        # 2. Group match (80% points)
        target_group = None
        for group, keywords in INDUSTRY_GROUPS.items():
            if any(k.lower() in target_industry.lower() for k in keywords):
                target_group = group
                break
        
        if target_group:
            for ind in consultant_industries:
                if any(k.lower() in ind.lower() for k in INDUSTRY_GROUPS[target_group]):
                    return weight * 0.8
        
        # 3. Simple keyword overlap (40% points)
        for ind in consultant_industries:
            if target_industry.lower() in ind.lower() or ind.lower() in target_industry.lower():
                return weight * 0.4
                
        return 0

    def _get_fallback_results(self, all_consultants):
        """Provide trust-based recommendations when no direct matches exist."""
        top_consultants = sorted(all_consultants, key=lambda x: x.trust_score or 0, reverse=True)[:3]
        results = []
        for c in top_consultants:
            fallback_score = FALLBACK_BASE_SCORE + (c.trust_score or 0) * 0.2
            results.append({
                'id': c.id, 'name': c.name, 'avatar': c.avatar,
                'specialty': c.specialty, 'experience': c.experience,
                'rating': c.rating, 'reviews': c.reviews,
                'matchReason': "플랫폼 검증 우수 전문가",
                'matchScore': round(min(fallback_score, 85)),
                'verified': c.verified, 'trustScore': c.trust_score
            })
        return results

