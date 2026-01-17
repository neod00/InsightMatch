# core/handlers.py
"""
Shared business logic handlers for API endpoints.
These functions contain the core business logic, independent of Flask routing.
"""

import uuid
import json
import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import jwt

from .responses import (
    success_response, error_response, not_found, 
    unauthorized, bad_request, server_error, validation_error
)


class AuthHandler:
    """Handles authentication business logic."""
    
    def __init__(self, db, User, Company, Consultant, secret_key):
        self.db = db
        self.User = User
        self.Company = Company
        self.Consultant = Consultant
        self.secret_key = secret_key
    
    def signup(self, data):
        """
        Register a new user.
        
        Args:
            data: Dict containing email, password, name, role
        
        Returns:
            Tuple of (response_dict, status_code)
        """
        email = data.get('email')
        password = data.get('password')
        name = data.get('name')
        role = data.get('role', 'company')
        
        # Validation
        if not email or not password or not name:
            return bad_request("Email, password, and name are required", error_code="VAL_001")
        
        # Check existing user
        if self.User.query.filter_by(email=email).first():
            return error_response("Email already exists", error_code="AUTH_001", status_code=400)
        
        # Create user
        new_user = self.User(
            email=email,
            password_hash=generate_password_hash(password),
            name=name,
            role=role
        )
        self.db.session.add(new_user)
        self.db.session.commit()
        
        # Create role-specific profile
        if role == 'company':
            new_company = self.Company(user_id=new_user.id, name=name, industry='Unknown')
            self.db.session.add(new_company)
        elif role == 'consultant':
            new_consultant = self.Consultant(
                user_id=new_user.id,
                name=name,
                specialty='General',
                experience='0년',
                rating=0.0,
                reviews=0,
                match_reason="New Joiner"
            )
            self.db.session.add(new_consultant)
        
        self.db.session.commit()
        
        return success_response(message="User created successfully", status_code=201)
    
    def login(self, data):
        """
        Authenticate a user and return JWT token.
        
        Args:
            data: Dict containing email, password
        
        Returns:
            Tuple of (response_dict, status_code)
        """
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return bad_request("Email and password are required", error_code="VAL_001")
        
        user = self.User.query.filter_by(email=email).first()
        
        if not user or not check_password_hash(user.password_hash, password):
            return unauthorized("Invalid credentials", error_code="AUTH_002")
        
        token = jwt.encode({
            'user_id': user.id,
            'role': user.role,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, self.secret_key, algorithm="HS256")
        
        return success_response(data={
            'token': token,
            'user': {
                'id': user.id,
                'name': user.name,
                'email': user.email,
                'role': user.role
            }
        })


class ConsultantHandler:
    """Handles consultant-related business logic."""
    
    def __init__(self, db, Consultant, matching_service):
        self.db = db
        self.Consultant = Consultant
        self.matching_service = matching_service
    
    def get_all(self, criteria=None):
        """
        Get list of consultants, optionally filtered by criteria.
        
        Args:
            criteria: Optional dict with filter parameters
        
        Returns:
            List of consultant dicts
        """
        if criteria:
            matches = self.matching_service.match_consultants(criteria)
            return matches
        
        consultants = self.Consultant.query.all()
        return [c.to_dict() for c in consultants]
    
    def get_by_id(self, consultant_id):
        """
        Get detailed consultant information.
        
        Args:
            consultant_id: ID of the consultant
        
        Returns:
            Consultant dict or None
        """
        consultant = self.Consultant.query.get(consultant_id)
        if not consultant:
            return None
        
        return {
            'id': consultant.id,
            'name': consultant.name,
            'avatar': consultant.avatar,
            'specialty': consultant.specialty,
            'experience': consultant.experience,
            'rating': consultant.rating,
            'reviews': consultant.reviews,
            'matchReason': consultant.match_reason,
            'regions': consultant.regions,
            'verified': consultant.verified,
            'trustScore': consultant.trust_score,
            'isoExperience': json.loads(consultant.iso_experience) if consultant.iso_experience else {},
            'industryExperience': json.loads(consultant.industry_experience) if consultant.industry_experience else [],
            'projectTypes': json.loads(consultant.project_types) if consultant.project_types else [],
            'roles': json.loads(consultant.roles) if consultant.roles else [],
            'detailedCertifications': json.loads(consultant.detailed_certifications) if consultant.detailed_certifications else [],
            'recentProjects': json.loads(consultant.recent_projects) if consultant.recent_projects else []
        }
    
    def register(self, data):
        """
        Register a new consultant.
        
        Args:
            data: Dict with consultant registration data
        
        Returns:
            Tuple of (response_dict, status_code)
        """
        new_consultant = self.Consultant(
            name=data.get('name'),
            avatar=data.get('avatar', 'N'),
            specialty=data.get('specialty'),
            experience=f"{data.get('experience')}년",
            rating=5.0,
            reviews=0,
            match_reason=data.get('match_reason'),
            certifications=data.get('certifications'),
            iso_experience=json.dumps(data.get('iso_experience', {})),
            industry_experience=json.dumps(data.get('industry_experience', [])),
            project_types=json.dumps(data.get('project_types', [])),
            org_size_experience=json.dumps(data.get('org_size_experience', [])),
            roles=json.dumps(data.get('roles', [])),
            detailed_certifications=json.dumps(data.get('detailed_certifications', [])),
            verified=False,
            trust_score=50.0
        )
        self.db.session.add(new_consultant)
        self.db.session.commit()
        
        return success_response(
            data={'id': new_consultant.id},
            message="Consultant registered successfully",
            status_code=201
        )
    
    def approve(self, consultant_id):
        """Approve a consultant (admin action)."""
        consultant = self.Consultant.query.get(consultant_id)
        if not consultant:
            return not_found("Consultant not found")
        
        consultant.verified = True
        consultant.trust_score = max(consultant.trust_score or 50, 70)
        self.db.session.commit()
        
        return success_response(
            data={'verified': True},
            message="Consultant approved successfully"
        )
    
    def reject(self, consultant_id, reason=None):
        """Reject a consultant registration (admin action)."""
        consultant = self.Consultant.query.get(consultant_id)
        if not consultant:
            return not_found("Consultant not found")
        
        self.db.session.delete(consultant)
        self.db.session.commit()
        
        return success_response(message=f"Consultant rejected: {reason or 'No reason provided'}")
    
    def revoke(self, consultant_id):
        """Revoke consultant verification (admin action)."""
        consultant = self.Consultant.query.get(consultant_id)
        if not consultant:
            return not_found("Consultant not found")
        
        consultant.verified = False
        consultant.trust_score = min(consultant.trust_score or 50, 50)
        self.db.session.commit()
        
        return success_response(
            data={'verified': False},
            message="Consultant verification revoked"
        )


class MatchingHandler:
    """Handles consultant matching logic."""
    
    # Issue ID to Korean name mapping
    ISSUE_NAMES = {
        'quality_defect': '품질 불량',
        'customer_complaint': '고객 클레임',
        'process_inefficiency': '프로세스 비효율',
        'supplier_quality': '공급업체 품질',
        'safety_incident': '안전사고',
        'env_regulation': '환경 규제',
        'energy_cost': '에너지 비용',
        'work_condition': '작업환경',
        'esg_demand': 'ESG 요구',
        'carbon_report': '탄소 보고',
        'carbon_neutral': '탄소중립',
        'esg_disclosure': 'ESG 공시',
        'security_incident': '정보보안',
        'privacy_need': '개인정보',
        'cloud_security': '클라우드 보안',
        'ai_risk': 'AI 리스크',
        'supply_unstable': '공급망 불안정',
        'crisis_response': '위기 대응',
        'compliance_risk': '컴플라이언스',
        'corruption_prevent': '부패 방지',
        'turnover': '이직률',
        'burnout': '번아웃',
        'knowledge_loss': '지식 유실'
    }
    
    def __init__(self, matching_service):
        self.matching_service = matching_service
    
    def direct_match(self, data):
        """
        Perform direct consultant matching based on survey data.
        
        Args:
            data: Dict with survey/matching parameters
        
        Returns:
            Dict with matching results
        """
        company_name = data.get('companyName', '기업')
        contact_email = data.get('contactEmail', '')
        industry = data.get('industry', '')
        employees = data.get('employees', '')
        region = data.get('region', '')
        selected_standards = data.get('standards', [])
        issues = data.get('issues', [])
        reasons = data.get('reasons', [])
        cert_status = data.get('certStatus', 'None')
        timeline = data.get('timeline', 'flexible')
        budget = data.get('budget', 'unknown')
        additional_notes = data.get('additionalNotes', '')
        
        # Extract recommended ISO from issues
        recommended_iso_set = set()
        for issue in issues:
            related = issue.get('relatedISO', [])
            for iso in related:
                if iso and iso not in selected_standards:
                    recommended_iso_set.add(iso)
        
        recommended_standards = list(recommended_iso_set)
        all_standards = list(set(selected_standards + recommended_standards))
        
        # Build criteria for matching
        criteria = {
            'industry': industry,
            'recommended_iso': [{'code': std} for std in all_standards],
            'region': region,
            'budget': budget,
            'timeline': timeline
        }
        
        # Get matched consultants
        matched_consultants = self.matching_service.match_consultants(criteria)
        
        # Build issues summary
        issues_summary = ', '.join([
            self.ISSUE_NAMES.get(issue.get('id'), issue.get('id', '')) 
            for issue in issues[:5]
        ])
        
        return {
            'company_name': company_name,
            'contact_email': contact_email,
            'industry': industry,
            'selected_standards': selected_standards,
            'recommended_standards': recommended_standards,
            'all_standards': all_standards,
            'issues_summary': issues_summary if issues_summary else None,
            'reasons': reasons,
            'cert_status': cert_status,
            'timeline': timeline,
            'budget': budget,
            'consultants': matched_consultants
        }


class ProjectHandler:
    """Handles project-related business logic."""
    
    def __init__(self, db, Project, Milestone, Consultant, User):
        self.db = db
        self.Project = Project
        self.Milestone = Milestone
        self.Consultant = Consultant
        self.User = User
    
    def get_user_projects(self, user_id):
        """Get all projects for a user (as company or consultant)."""
        if not user_id:
            return bad_request("User ID required")
        
        projects = self.Project.query.filter(
            (self.Project.company_id == user_id) | 
            (self.Project.consultant_id == user_id)
        ).all()
        
        results = []
        for p in projects:
            consultant = self.Consultant.query.get(p.consultant_id)
            results.append({
                'id': p.id,
                'title': p.title,
                'session_id': getattr(p, 'session_id', None),
                'status': p.status,
                'proposal_status': getattr(p, 'proposal_status', 'pending'),
                'consultant_id': p.consultant_id,
                'consultant_name': consultant.name if consultant else 'Unknown',
                'start_date': p.start_date.isoformat() if p.start_date else None,
                'milestones': [m.to_dict() for m in p.milestones]
            })
        
        return results
    
    def create_project(self, data):
        """Create a new project."""
        new_project = self.Project(
            company_id=data.get('company_id'),
            consultant_id=data.get('consultant_id'),
            title=data.get('title'),
            session_id=data.get('session_id'),
            status='planning',
            start_date=datetime.datetime.utcnow()
        )
        self.db.session.add(new_project)
        self.db.session.commit()
        
        # Create default milestones
        defaults = ["Kick-off Meeting", "Gap Analysis", "Documentation", "Internal Audit", "Final Certification"]
        for title in defaults:
            m = self.Milestone(project_id=new_project.id, title=title)
            self.db.session.add(m)
        self.db.session.commit()
        
        return success_response(data={'id': new_project.id}, message="Project created", status_code=201)
    
    def delete_project(self, project_id):
        """Delete a project (only if not contracted)."""
        project = self.Project.query.get(project_id)
        if not project:
            return not_found("Project not found")
        
        if project.status in ['contracted', 'in_progress', 'completed']:
            return bad_request("계약된 프로젝트는 삭제할 수 없습니다.", error_code="BIZ_001")
        
        self.Milestone.query.filter_by(project_id=project_id).delete()
        self.db.session.delete(project)
        self.db.session.commit()
        
        return success_response(message="프로젝트가 삭제되었습니다.")
    
    def sign_contract(self, project_id):
        """Sign contract for a project."""
        project = self.Project.query.get(project_id)
        if not project:
            return not_found("Project not found")
        
        project.status = 'contracted'
        project.proposal_status = 'accepted'
        project.start_date = datetime.datetime.utcnow()
        
        # Create milestones if not exist
        if not project.milestones:
            defaults = ["Kick-off Meeting", "Gap Analysis", "Documentation", "Internal Audit", "Final Certification"]
            for title in defaults:
                m = self.Milestone(project_id=project.id, title=title)
                self.db.session.add(m)
        
        self.db.session.commit()
        
        return success_response(
            data={'status': project.status},
            message="Contract signed successfully"
        )
