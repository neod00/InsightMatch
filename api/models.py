from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'company', 'consultant', 'admin'
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Link to User
    name = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(200))
    industry = db.Column(db.String(100))
    employees = db.Column(db.String(50))
    email = db.Column(db.String(120)) # Keep for contact info even if in User
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Consultant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Link to User
    name = db.Column(db.String(100), nullable=False)
    avatar = db.Column(db.String(10)) # Initials or URL
    specialty = db.Column(db.String(100))
    experience = db.Column(db.String(50))
    rating = db.Column(db.Float)
    reviews = db.Column(db.Integer)
    match_reason = db.Column(db.String(200)) # Default/Tag
    regions = db.Column(db.String(200)) # Comma separated
    certifications = db.Column(db.Text) # JSON string
    
    # New Trust-Centric Fields
    iso_experience = db.Column(db.Text) # JSON: {"9001": "Lead Auditor", ...}
    industry_experience = db.Column(db.Text) # JSON: ["Automotive", "Chemical"]
    project_types = db.Column(db.Text) # JSON: ["New", "Transition"]
    org_size_experience = db.Column(db.Text) # JSON: ["Small", "Medium"]
    roles = db.Column(db.Text) # JSON: ["Audit", "Training"]
    detailed_certifications = db.Column(db.Text) # JSON: Detailed cert info
    verified = db.Column(db.Boolean, default=False)
    trust_score = db.Column(db.Float, default=0.0)
    recent_projects = db.Column(db.Text) # JSON: List of recent projects
    
    # ① Profile Enhancement Fields
    profile_image_url = db.Column(db.String(500))  # 프로필 사진 URL
    bio = db.Column(db.Text)  # 자기소개
    introduction_video_url = db.Column(db.String(500))  # 소개 영상 링크
    portfolio_files = db.Column(db.Text)  # JSON: [{"name": "...", "url": "..."}]
    phone = db.Column(db.String(20))  # 연락처
    email = db.Column(db.String(120))  # 이메일 (User와 별도로 공개용)
    company_name = db.Column(db.String(100))  # 소속 회사/기관
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'avatar': self.avatar,
            'specialty': self.specialty,
            'experience': self.experience,
            'rating': self.rating,
            'reviews': self.reviews,
            'matchReason': self.match_reason,
            'regions': self.regions,
            'verified': self.verified,
            'trustScore': self.trust_score,
            'isoExperience': json.loads(self.iso_experience) if self.iso_experience else {},
            'industryExperience': json.loads(self.industry_experience) if self.industry_experience else [],
            'projectTypes': json.loads(self.project_types) if self.project_types else [],
            'roles': json.loads(self.roles) if self.roles else [],
            # Profile fields
            'profileImageUrl': self.profile_image_url,
            'bio': self.bio,
            'introductionVideoUrl': self.introduction_video_url,
            'portfolioFiles': json.loads(self.portfolio_files) if self.portfolio_files else [],
            'phone': self.phone,
            'email': self.email,
            'companyName': self.company_name
        }

# ② Notification Model
class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # quote_request, proposal_received, contract_signed, schedule_proposed, etc.
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text)
    link = db.Column(db.String(500))  # 클릭 시 이동할 URL
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'title': self.title,
            'message': self.message,
            'link': self.link,
            'isRead': self.is_read,
            'createdAt': self.created_at.isoformat() if self.created_at else None
        }

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Using User ID for simplicity in MVP
    consultant_id = db.Column(db.Integer, db.ForeignKey('consultant.id'))
    title = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), default='proposal_pending') # proposal_pending, proposal_submitted, contracted, in_progress, completed
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Consultant Proposal Fields (② 컨설턴트 직접 견적)
    proposal_price = db.Column(db.Integer)  # 제안 금액 (원)
    proposal_duration = db.Column(db.String(50))  # 예상 소요 기간 (예: "3개월")
    proposal_message = db.Column(db.Text)  # 제안 메시지
    proposal_file_url = db.Column(db.String(500))  # 제안서 파일 URL
    proposal_submitted_at = db.Column(db.DateTime)  # 제안 제출 시간
    
    # Schedule Fields (③ 일정 확정 워크플로우)
    schedule_data = db.Column(db.Text)  # JSON: 마일스톤별 예정일
    schedule_status = db.Column(db.String(50), default='pending')  # pending, proposed, confirmed
    schedule_proposed_at = db.Column(db.DateTime)
    schedule_confirmed_at = db.Column(db.DateTime)
    
    milestones = db.relationship('Milestone', backref='project', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'consultant_id': self.consultant_id,
            'title': self.title,
            'status': self.status,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'proposal_price': self.proposal_price,
            'proposal_duration': self.proposal_duration,
            'proposal_message': self.proposal_message,
            'proposal_file_url': self.proposal_file_url,
            'proposal_submitted_at': self.proposal_submitted_at.isoformat() if self.proposal_submitted_at else None,
            'schedule_status': self.schedule_status,
            'milestones': [m.to_dict() for m in self.milestones]
        }

class Milestone(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), default='pending') # pending, in_progress, completed
    due_date = db.Column(db.DateTime)
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'status': self.status,
            'due_date': self.due_date.isoformat() if self.due_date else None
        }

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(100), default='Admin')
    tags = db.Column(db.String(200)) # Comma separated tags
    image_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'author': self.author,
            'tags': self.tags.split(',') if self.tags else [],
            'image_url': self.image_url,
            'created_at': self.created_at.strftime('%Y-%m-%d')
        }

class AnalysisJob(db.Model):
    id = db.Column(db.String(36), primary_key=True) # UUID
    company_name = db.Column(db.String(100))
    url = db.Column(db.String(200))
    status = db.Column(db.String(20), default='processing') # processing, completed, failed, deleted
    result = db.Column(db.Text) # JSON string
    intake_data = db.Column(db.Text) # JSON string for raw input
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True) # Soft delete timestamp

    def set_result(self, result_dict):
        self.result = json.dumps(result_dict)

    def get_result(self):
        return json.loads(self.result) if self.result else None

    def set_intake_data(self, data_dict):
        self.intake_data = json.dumps(data_dict)

    def get_intake_data(self):
        return json.loads(self.intake_data) if self.intake_data else {}

