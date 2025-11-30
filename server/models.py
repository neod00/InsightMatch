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
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'avatar': self.avatar,
            'specialty': self.specialty,
            'experience': self.experience,
            'rating': self.rating,
            'reviews': self.reviews,
            'matchReason': self.match_reason,
            'verified': self.verified,
            'trustScore': self.trust_score,
            'isoExperience': json.loads(self.iso_experience) if self.iso_experience else {},
            'industryExperience': json.loads(self.industry_experience) if self.industry_experience else [],
            'projectTypes': json.loads(self.project_types) if self.project_types else [],
            'roles': json.loads(self.roles) if self.roles else []
        }

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Using User ID for simplicity in MVP
    consultant_id = db.Column(db.Integer, db.ForeignKey('consultant.id'))
    title = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), default='planning') # planning, in_progress, review, completed
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    milestones = db.relationship('Milestone', backref='project', lazy=True)

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
    status = db.Column(db.String(20), default='processing') # processing, completed, failed
    result = db.Column(db.Text) # JSON string
    intake_data = db.Column(db.Text) # JSON string for raw input
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_result(self, result_dict):
        self.result = json.dumps(result_dict)

    def get_result(self):
        return json.loads(self.result) if self.result else None

    def set_intake_data(self, data_dict):
        self.intake_data = json.dumps(data_dict)

    def get_intake_data(self):
        return json.loads(self.intake_data) if self.intake_data else {}
