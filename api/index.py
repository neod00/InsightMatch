import os
import sys

# Add current directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

import uuid
import json
import datetime
import re
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import jwt
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, AnalysisJob, Consultant, User, Project, Milestone, Post, Company, Notification, Message, ProfileChangeLog, PasswordResetToken
from services import AIService, MatchingService, ProposalService, EmailService

# Load environment variables
# Load from project root directory
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)
print(f"Loading .env from: {env_path}")
print(f"GOOGLE_API_KEY exists: {os.environ.get('GOOGLE_API_KEY') is not None}")
print(f"DATA_GO_KR_API_KEY exists: {os.environ.get('DATA_GO_KR_API_KEY') is not None}")

# Configure Flask
app = Flask(__name__)
CORS(app)

# Database Config - Use SQLite for local development, PostgreSQL for production
is_local_dev = not os.environ.get('VERCEL')  # Vercel sets this env var in production

if is_local_dev:
    # Use absolute path to project root
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(base_dir, 'insightmatch.db')
    database_url = f'sqlite:///{db_path}'
    print(f"[LOCAL DEV] Using SQLite: {db_path}")

else:
    # Production: use Supabase PostgreSQL
    database_url = os.environ.get('DATABASE_URL')
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    database_url = database_url or os.environ.get('SUPABASE_DB_URL', 'sqlite:///insightmatch.db')

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-123')

db.init_app(app)

# Initialize Services
ai_service = AIService()
matching_service = MatchingService()
proposal_service = ProposalService()
email_service = EmailService()

# Create tables on first request
@app.before_request
def create_tables():
    if not hasattr(app, '_tables_created'):
        db.create_all()
        app._tables_created = True

# --- Helper: Email Validation ---
def is_valid_email(email):
    """Unicode-aware email validation (RFC 5321 compliant with IDN support)"""
    if not email:
        return False
    pattern = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
    return re.match(pattern, email) is not None

# --- Auth Endpoints ---
@app.route('/api/auth/signup', methods=['POST'])
def signup():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    name = data.get('name')
    role = data.get('role', 'company')
    
    # Email validation
    if not is_valid_email(email):
        return jsonify({'message': '유효하지 않은 이메일 형식입니다.'}), 400
    
    # Password length validation
    if not password or len(password) < 8:
        return jsonify({'message': '비밀번호는 8자 이상이어야 합니다.'}), 400
    
    if User.query.filter_by(email=email).first():
        return jsonify({'message': 'Email already exists'}), 400
    
    phone = data.get('phone', '').strip()
        
    new_user = User(
        email=email,
        password_hash=generate_password_hash(password),
        name=name,
        role=role,
        phone=phone
    )
    db.session.add(new_user)
    db.session.commit()
    
    if role == 'company':
        new_company = Company(user_id=new_user.id, name=name, industry='Unknown')
        db.session.add(new_company)
        db.session.commit()
    elif role == 'consultant':
        new_consultant = Consultant(
            user_id=new_user.id,
            name=name,
            specialty='General',
            experience='0년',
            rating=0.0,
            reviews=0,
            match_reason="New Joiner"
        )
        db.session.add(new_consultant)
        db.session.commit()
        
    return jsonify({'message': 'User created successfully'}), 201

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    user = User.query.filter_by(email=email).first()
    
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'message': 'Invalid credentials'}), 401
        
    token = jwt.encode({
        'user_id': user.id,
        'role': user.role,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    }, app.config['SECRET_KEY'], algorithm="HS256")
    
    return jsonify({
        'token': token,
        'user': {
            'id': user.id,
            'name': user.name,
            'email': user.email,
            'role': user.role
        }
    })

# --- Password Reset Endpoints ---
@app.route('/api/auth/request-reset', methods=['POST'])
def request_password_reset():
    """Request a password reset link via email"""
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        
        if not email:
            return jsonify({'message': '이메일을 입력해주세요.'}), 400
        
        # Always return success to prevent email enumeration attacks
        success_message = '입력하신 이메일로 비밀번호 재설정 링크를 발송했습니다. 이메일을 확인해주세요.'
        
        user = User.query.filter_by(email=email).first()
        if not user:
            print(f"[Password Reset] Email not found in database: {email}")
            # Silently succeed to prevent enumeration
            return jsonify({'message': success_message})
        
        # Generate secure token
        token = str(uuid.uuid4())
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=30)
        
        # Invalidate any existing tokens for this user
        try:
            PasswordResetToken.query.filter_by(user_id=user.id, used=False).update({'used': True})
        except Exception as e:
            print(f"[Password Reset] Token invalidation error (continuing): {e}")
        
        # Create new token
        reset_token = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=expires_at
        )
        db.session.add(reset_token)
        db.session.commit()
        
        # Build reset link
        base_url = os.environ.get('BASE_URL')
        if not base_url or ('localhost' in base_url and 'localhost' not in request.host):
            base_url = request.host_url.rstrip('/')
        
        reset_link = f"{base_url}/reset-password.html?token={token}"
        print(f"[Password Reset] Link generated: {reset_link}")
        
        # Send email
        email_service = EmailService()
        result = email_service.send_password_reset_email(
            to_email=email,
            user_name=user.name or '사용자',
            reset_link=reset_link
        )
        
        if not result.get('success') and not result.get('simulated'):
            print(f"[Password Reset] Email send failed: {result.get('message')}")
        
        return jsonify({'message': success_message})
        
    except Exception as e:
        import traceback
        print(f"[Password Reset] Critical error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'message': '서버 오류가 발생했습니다.',
            'debug': str(e)
        }), 500

@app.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    """Reset password using token from email"""
    data = request.json
    token = data.get('token', '').strip()
    new_password = data.get('new_password', '')
    
    if not token:
        return jsonify({'message': '유효하지 않은 요청입니다.'}), 400
    
    if not new_password or len(new_password) < 8:
        return jsonify({'message': '비밀번호는 8자 이상이어야 합니다.'}), 400
    
    # Find token
    reset_token = PasswordResetToken.query.filter_by(token=token, used=False).first()
    
    if not reset_token:
        return jsonify({'message': '유효하지 않거나 이미 사용된 링크입니다.'}), 400
    
    # Check expiration
    if reset_token.expires_at < datetime.datetime.utcnow():
        return jsonify({'message': '링크가 만료되었습니다. 다시 요청해주세요.'}), 400
    
    # Update password
    user = User.query.get(reset_token.user_id)
    if not user:
        return jsonify({'message': '사용자를 찾을 수 없습니다.'}), 404
    
    user.password_hash = generate_password_hash(new_password)
    reset_token.used = True
    db.session.commit()
    
    return jsonify({'message': '비밀번호가 성공적으로 변경되었습니다. 로그인해주세요.'})

@app.route('/api/auth/find-email', methods=['POST'])
def find_email():
    """Find email (ID) by name and phone number"""
    data = request.json
    name = data.get('name', '').strip()
    phone = data.get('phone', '').strip()
    
    if not name or not phone:
        return jsonify({'message': '이름과 휴대폰 번호를 모두 입력해주세요.'}), 400
    
    # Normalize phone number (remove hyphens and spaces)
    phone_normalized = phone.replace('-', '').replace(' ', '')
    
    # Find user with matching name and phone
    users = User.query.filter_by(name=name).all()
    
    matched_user = None
    for user in users:
        if user.phone:
            user_phone_normalized = user.phone.replace('-', '').replace(' ', '')
            if user_phone_normalized == phone_normalized:
                matched_user = user
                break
    
    if not matched_user:
        return jsonify({'message': '일치하는 계정을 찾을 수 없습니다.'}), 404
    
    # Mask email for security (show first 3 chars + *** + domain)
    email = matched_user.email
    at_index = email.find('@')
    if at_index > 3:
        masked_email = email[:3] + '*' * (at_index - 3) + email[at_index:]
    else:
        masked_email = email[0] + '*' * (at_index - 1) + email[at_index:]
    
    return jsonify({
        'message': '이메일을 찾았습니다.',
        'email': masked_email
    })

# --- Analysis Endpoints ---
@app.route('/api/analyze', methods=['POST'])
def start_analysis():
    data = request.json
    company_url = data.get('companyUrl')
    company_name = data.get('companyName', '(주)인사이트매치')
    
    job_id = str(uuid.uuid4())
    
    job = AnalysisJob(
        id=job_id,
        company_name=company_name,
        url=company_url,
        status='processing'
    )
    job.set_intake_data(data)
    
    db.session.add(job)
    db.session.commit()
    
    return jsonify({'job_id': job_id, 'message': 'Analysis started'}), 202

@app.route('/api/analyze/<job_id>', methods=['GET'])
def get_analysis_status(job_id):
    job = AnalysisJob.query.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    if job.status == 'processing':
        job.status = 'analyzing'
        db.session.commit()
        
        try:
            intake_data = job.get_intake_data()
            result = ai_service.analyze(intake_data)
            
            job.set_result(result)
            job.status = 'completed'
            db.session.commit()
        except Exception as e:
            job.status = 'failed'
            db.session.commit()
            return jsonify({'status': 'failed', 'error': str(e)})
            
    elif job.status == 'analyzing':
        return jsonify({'status': 'processing'})

    return jsonify({
        'status': job.status,
        'result': job.get_result()
    })

# --- Direct Matching Endpoint (Survey-Based, No AI) ---
@app.route('/api/match', methods=['POST'])
def direct_match():
    """
    Direct consultant matching based on survey data.
    No AI analysis - just rule-based matching.
    """
    data = request.json
    
    company_name = data.get('companyName', '기업')
    contact_email = data.get('contactEmail', '')
    industry = data.get('industry', '')
    employees = data.get('employees', '')
    region = data.get('region', '')
    selected_standards = data.get('standards', [])
    issues = data.get('issues', [])  # [{id: 'safety_incident', relatedISO: ['ISO 45001:2018']}]
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
    
    # Combine selected + recommended for matching
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
    matched_consultants = matching_service.match_consultants(criteria)
    
    # Build issues summary
    issue_names = {
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
    
    issues_summary = ', '.join([issue_names.get(issue.get('id'), issue.get('id', '')) for issue in issues[:5]])
    
    # Build result
    result = {
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
    
    return jsonify(result)

# --- Consultant Endpoints ---
@app.route('/api/consultants', methods=['GET'])
def get_consultants():
    job_id = request.args.get('job_id')
    industry = request.args.get('industry')
    iso_codes = request.args.getlist('iso')
    project_type = request.args.get('project_type')
    region = request.args.get('region')
    
    criteria = {}
    
    if job_id:
        job = AnalysisJob.query.get(job_id)
        if job and job.result:
            analysis_result = job.get_result()
            criteria = analysis_result
            
    if industry:
        criteria['industry'] = industry
    if iso_codes:
        criteria['recommended_iso'] = [{'code': code} for code in iso_codes]
    if project_type:
        criteria['project_type'] = project_type
    if region:
        criteria['region'] = region

    if criteria:
        matches = matching_service.match_consultants(criteria)
        return jsonify(matches)
            
    consultants = Consultant.query.all()
    return jsonify([c.to_dict() for c in consultants])

@app.route('/api/consultants/register', methods=['POST'])
def register_consultant():
    data = request.json
    new_consultant = Consultant(
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
    db.session.add(new_consultant)
    db.session.commit()
    return jsonify({'message': 'Consultant registered successfully', 'id': new_consultant.id}), 201

# --- Project Endpoints ---
@app.route('/api/projects', methods=['GET', 'POST'])
def handle_projects():
    user_id = request.args.get('user_id')
    
    if request.method == 'GET':
        if not user_id:
            return jsonify({'message': 'User ID required'}), 400
        
        # 컨설턴트인 경우: user_id로 Consultant 테이블에서 consultant_id 조회
        consultant = Consultant.query.filter_by(user_id=user_id).first()
        consultant_id = consultant.id if consultant else None
        
        # 필터링: company_id가 user_id이거나, consultant_id가 조회된 consultant의 id인 프로젝트
        if consultant_id:
            projects = Project.query.filter(
                (Project.company_id == user_id) | (Project.consultant_id == consultant_id)
            ).all()
        else:
            projects = Project.query.filter(Project.company_id == user_id).all()
        
        results = []
        for p in projects:
            consultant_info = Consultant.query.get(p.consultant_id)
            company_user = User.query.get(p.company_id)
            
            results.append({
                'id': p.id,
                'title': p.title,
                'session_id': getattr(p, 'session_id', None),
                'status': p.status,
                'consultant_id': p.consultant_id,
                'consultant_name': consultant_info.name if consultant_info else 'Unknown',
                'profile_image_url': consultant_info.profile_image_url if consultant_info else None,
                'company_id': p.company_id,
                'company_name': company_user.name if company_user else 'Unknown Company',
                'start_date': p.start_date.isoformat() if p.start_date else None,
                'created_at': p.created_at.isoformat() if hasattr(p, 'created_at') and p.created_at else None,
                # 제안서 관련 필드 (① 견적 비교용)
                'proposal_price': getattr(p, 'proposal_price', None),
                'proposal_duration': getattr(p, 'proposal_duration', None),
                'proposal_message': getattr(p, 'proposal_message', None),
                'proposal_file_url': getattr(p, 'proposal_file_url', None),
                'proposal_submitted_at': p.proposal_submitted_at.isoformat() if hasattr(p, 'proposal_submitted_at') and p.proposal_submitted_at else None,
                # 일정 관련 필드 (③ 일정 워크플로우용)
                'schedule_status': getattr(p, 'schedule_status', 'pending'),
                # 취소 관련 필드 (④ 취소 이력)
                'cancelled_at': p.cancelled_at.isoformat() if hasattr(p, 'cancelled_at') and p.cancelled_at else None,
                'cancelled_reason': getattr(p, 'cancelled_reason', None),
                'milestones': [m.to_dict() for m in p.milestones]
            })
        return jsonify(results)
        
    elif request.method == 'POST':
        data = request.json
        
        # === Duplicate Prevention ===
        # Check if an ACTIVE project with same company+consultant+title already exists
        active_statuses = ['planning', 'proposal_submitted', 'contracted', 'in_progress']
        existing = Project.query.filter(
            Project.company_id == data.get('company_id'),
            Project.consultant_id == data.get('consultant_id'),
            Project.title == data.get('title'),
            Project.status.in_(active_statuses)
        ).first()
        
        if existing:
            return jsonify({
                'message': '이 컨설턴트에게 동일한 규격으로 이미 진행 중인 요청이 있습니다.',
                'existing_project_id': existing.id,
                'existing_status': existing.status
            }), 409
        
        new_project = Project(
            company_id=data.get('company_id'),
            consultant_id=data.get('consultant_id'),
            title=data.get('title'),
            status='planning',
            start_date=datetime.datetime.utcnow()
        )
        db.session.add(new_project)
        db.session.commit()
        
        defaults = ["Kick-off Meeting", "Gap Analysis", "Documentation", "Internal Audit", "Final Certification"]
        for title in defaults:
            m = Milestone(project_id=new_project.id, title=title)
            db.session.add(m)
        db.session.commit()
        
        return jsonify({'message': 'Project created', 'id': new_project.id}), 201

# --- Project Delete Endpoint ---
@app.route('/api/projects/<int:project_id>', methods=['DELETE'])
def delete_project(project_id):
    """Delete a project (only if not contracted)"""
    project = Project.query.get_or_404(project_id)
    
    # Cannot delete contracted projects
    if project.status in ['contracted', 'in_progress', 'completed']:
        return jsonify({'message': '계약된 프로젝트는 삭제할 수 없습니다.'}), 400
    
    # Delete related milestones
    Milestone.query.filter_by(project_id=project_id).delete()
    
    # Delete project
    db.session.delete(project)
    db.session.commit()
    
    return jsonify({'message': '프로젝트가 삭제되었습니다.'})

@app.route('/api/projects/<int:project_id>/proposal', methods=['GET'])
def download_proposal(project_id):
    project = Project.query.get_or_404(project_id)
    consultant = Consultant.query.get(project.consultant_id)
    company_user = User.query.get(project.company_id)
    company_name = company_user.name if company_user else "Client"
    
    pdf_buffer = proposal_service.generate_proposal(project, consultant, company_name)
    
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f"proposal_{project_id}.pdf",
        mimetype='application/pdf'
    )

@app.route('/api/projects/<int:project_id>/sign', methods=['POST'])
def sign_contract(project_id):
    project = Project.query.get_or_404(project_id)
    
    # 제안서가 제출되지 않은 경우 계약 불가
    if project.status != 'proposal_submitted':
        return jsonify({'message': '제안서가 제출된 프로젝트만 계약할 수 있습니다.'}), 400
    
    # 상태를 'contracted'로 변경
    project.status = 'contracted'
    project.start_date = datetime.datetime.utcnow()
    
    # 계약 후 마일스톤이 없으면 생성
    if not project.milestones:
        defaults = ["Kick-off Meeting", "Gap Analysis", "Documentation", "Internal Audit", "Final Certification"]
        for title in defaults:
            m = Milestone(project_id=project.id, title=title)
            db.session.add(m)
    
    db.session.commit()
    return jsonify({'message': 'Contract signed successfully', 'status': project.status})

# ========================================
# ② 컨설턴트 직접 견적 체계
# ========================================

@app.route('/api/projects/<int:project_id>/submit-proposal', methods=['POST'])
def submit_proposal(project_id):
    """컨설턴트가 제안서(금액, 기간, 메시지, 파일) 제출"""
    project = Project.query.get_or_404(project_id)
    
    # 이미 제출된 경우
    if project.status == 'proposal_submitted':
        return jsonify({'message': '이미 제안서가 제출되었습니다.'}), 400
    
    # 이미 계약된 경우
    if project.status in ['contracted', 'in_progress', 'completed']:
        return jsonify({'message': '이미 계약된 프로젝트입니다.'}), 400
    
    data = request.json
    
    # 필수 필드 검증
    proposal_price = data.get('proposal_price')
    if not proposal_price:
        return jsonify({'message': '제안 금액을 입력해주세요.'}), 400
    
    # 제안 정보 저장
    project.proposal_price = int(proposal_price)
    project.proposal_duration = data.get('proposal_duration', '')
    project.proposal_message = data.get('proposal_message', '')
    project.proposal_file_url = data.get('proposal_file_url', '')  # 파일 업로드는 별도 처리
    project.proposal_submitted_at = datetime.datetime.utcnow()
    project.status = 'proposal_submitted'
    
    db.session.commit()
    
    # ② 기업에게 알림 생성
    try:
        company_user = User.query.get(project.company_id)
        consultant = Consultant.query.get(project.consultant_id)
        if company_user and consultant:
            # 인앱 알림 생성
            notification = Notification(
                user_id=company_user.id,
                type='proposal_received',
                title=f'{consultant.name}님이 제안서를 보냈습니다',
                message=f'제안 금액: {project.proposal_price:,}원',
                link=f'/dashboard.html'
            )
            db.session.add(notification)
            db.session.commit()
            
            # 이메일 발송 (선택적)
            if email_service:
                try:
                    # email_service.send_proposal_notification(...)
                    pass
                except Exception as e:
                    print(f"[Email] Failed to send proposal notification: {e}")
    except Exception as e:
        print(f"[Notification] Failed to create notification: {e}")
    
    return jsonify({
        'message': '제안서가 성공적으로 제출되었습니다.',
        'project_id': project.id,
        'status': project.status,
        'proposal_price': project.proposal_price
    })

@app.route('/api/projects/<int:project_id>/proposal', methods=['GET'])
def get_proposal(project_id):
    """특정 프로젝트의 제안서 상세 조회"""
    project = Project.query.get_or_404(project_id)
    consultant = Consultant.query.get(project.consultant_id)
    
    return jsonify({
        'project_id': project.id,
        'consultant_id': project.consultant_id,
        'consultant_name': consultant.name if consultant else 'Unknown',
        'proposal_price': project.proposal_price,
        'proposal_duration': project.proposal_duration,
        'proposal_message': project.proposal_message,
        'proposal_file_url': project.proposal_file_url,
        'proposal_submitted_at': project.proposal_submitted_at.isoformat() if project.proposal_submitted_at else None,
        'status': project.status
    })

@app.route('/api/projects/<int:project_id>/detail', methods=['GET'])
def get_project_detail(project_id):
    """프로젝트 상세 정보 조회 (컨설턴트용)"""
    project = Project.query.get_or_404(project_id)
    
    # Company 정보 조회 (company_id가 있는 경우)
    company = None
    company_user = None
    if project.company_id:
        company = Company.query.get(project.company_id)
        company_user = User.query.get(project.company_id)
    
    # AnalysisJob에서 추가 정보 조회 (session_id가 있는 경우)
    analysis_job = None
    intake_data = {}
    
    # 1차: session_id로 직접 조회
    if hasattr(project, 'session_id') and project.session_id:
        analysis_job = AnalysisJob.query.filter_by(id=project.session_id).first()
        if not analysis_job:
            # session_id 부분 매칭 시도
            try:
                analysis_job = AnalysisJob.query.filter(AnalysisJob.id.like(f'%{project.session_id[:8]}%')).first()
            except:
                pass
    
    # 2차: session_id로 못 찾으면, company_user의 이메일로 AnalysisJob 검색
    if not analysis_job and company_user:
        # 가장 최근의 해당 사용자 AnalysisJob 검색
        all_jobs = AnalysisJob.query.filter(
            AnalysisJob.deleted_at.is_(None)
        ).order_by(AnalysisJob.created_at.desc()).all()
        
        for job in all_jobs:
            job_intake = job.get_intake_data() if job.intake_data else {}
            contact_email = job_intake.get('contactEmail', '')
            company_name_in_job = job_intake.get('companyName', '')
            
            # 이메일 매칭 또는 회사명 매칭
            if (contact_email and company_user.email and contact_email.lower() == company_user.email.lower()) or \
               (company_name_in_job and company_user.name and company_name_in_job == company_user.name):
                analysis_job = job
                break
    
    # intake_data에서 정보 파싱
    if analysis_job:
        intake_data = analysis_job.get_intake_data() if analysis_job.intake_data else {}
    
    # Consultant 정보
    consultant = Consultant.query.get(project.consultant_id)
    
    # AI 진단 결과 파싱
    ai_diagnosis = None
    analysis_result = None
    if analysis_job and analysis_job.result:
        try:
            if isinstance(analysis_job.result, str):
                result_data = json.loads(analysis_job.result)
            else:
                result_data = analysis_job.result
            
            ai_diagnosis = {
                'risk_level': result_data.get('risk_level'),
                'estimated_duration': result_data.get('estimated_duration'),
                'summary': result_data.get('summary', '')
            }
            analysis_result = result_data.get('detailed_analysis')
        except:
            pass
    
    # standards 파싱 (리스트 또는 문자열)
    standards = intake_data.get('standards', [])
    if isinstance(standards, str):
        standards = [s.strip() for s in standards.split(',') if s.strip()]
    
    # 기업명 결정 (우선순위: intake_data > company > company_user > title 파싱)
    company_name = intake_data.get('companyName') or \
                   (company.name if company else None) or \
                   (company_user.name if company_user else None) or \
                   (project.title.split(' - ')[0] if project.title and ' - ' in project.title else None)
    
    return jsonify({
        'id': project.id,
        'title': project.title,
        'description': project.description,
        'status': project.status,
        'created_at': project.created_at.isoformat() if hasattr(project, 'created_at') and project.created_at else (project.start_date.isoformat() if project.start_date else None),
        
        # 기업 정보
        'company_id': project.company_id,
        'company_name': company_name,
        'industry': intake_data.get('industry'),
        'employees': intake_data.get('employees'),
        
        # 인증 요구사항
        'standards': standards,
        'cert_status': intake_data.get('certStatus'),
        'readiness': intake_data.get('readiness'),
        'target_date': intake_data.get('targetDate'),
        'budget': intake_data.get('budget'),
        
        # AI 진단 결과
        'ai_diagnosis': ai_diagnosis,
        'analysis_result': analysis_result,
        
        # 컨설턴트 정보
        'consultant_id': project.consultant_id,
        'consultant_name': consultant.name if consultant else None,
        
        # 제안서 정보
        'proposal_price': project.proposal_price,
        'proposal_duration': project.proposal_duration,
        'proposal_message': project.proposal_message,
        'proposal_file_url': project.proposal_file_url,
        'proposal_submitted_at': project.proposal_submitted_at.isoformat() if project.proposal_submitted_at else None,
        
        # 일정 정보
        'schedule_status': project.schedule_status
    })

# ========================================
# ③ 계약 후 일정 확정 워크플로우
# ========================================

@app.route('/api/projects/<int:project_id>/propose-schedule', methods=['POST'])
def propose_schedule(project_id):
    """컨설턴트가 마일스톤별 일정 제안"""
    project = Project.query.get_or_404(project_id)
    
    # 계약된 프로젝트만 일정 제안 가능
    if project.status not in ['contracted', 'in_progress']:
        return jsonify({'message': '계약 완료된 프로젝트만 일정을 제안할 수 있습니다.'}), 400
    
    data = request.json
    schedule = data.get('schedule', [])  # [{milestone_id, proposed_date}, ...]
    
    if not schedule:
        return jsonify({'message': '일정 데이터가 필요합니다.'}), 400
    
    # 마일스톤 일정 업데이트
    for item in schedule:
        milestone = Milestone.query.get(item.get('milestone_id'))
        if milestone and milestone.project_id == project_id:
            if item.get('proposed_date'):
                milestone.due_date = datetime.datetime.fromisoformat(item['proposed_date'].replace('Z', '+00:00'))
    
    project.schedule_data = json.dumps(schedule)
    project.schedule_status = 'proposed'
    project.schedule_proposed_at = datetime.datetime.utcnow()
    
    db.session.commit()
    
    return jsonify({
        'message': '일정이 제안되었습니다. 기업의 확인을 기다려주세요.',
        'schedule_status': project.schedule_status
    })

@app.route('/api/projects/<int:project_id>/confirm-schedule', methods=['POST'])
def confirm_schedule(project_id):
    """기업이 제안된 일정 승인"""
    project = Project.query.get_or_404(project_id)
    
    if project.schedule_status != 'proposed':
        return jsonify({'message': '제안된 일정이 없습니다.'}), 400
    
    project.schedule_status = 'confirmed'
    project.schedule_confirmed_at = datetime.datetime.utcnow()
    project.status = 'in_progress'  # 일정 확정 시 프로젝트 시작
    
    db.session.commit()
    
    return jsonify({
        'message': '일정이 확정되었습니다. 프로젝트가 시작됩니다.',
        'schedule_status': project.schedule_status,
        'status': project.status
    })

@app.route('/api/projects/<int:project_id>/reject-schedule', methods=['POST'])
def reject_schedule(project_id):
    """기업이 제안된 일정 거절 (재조율 요청)"""
    project = Project.query.get_or_404(project_id)
    
    if project.schedule_status != 'proposed':
        return jsonify({'message': '제안된 일정이 없습니다.'}), 400
    
    data = request.json
    rejection_reason = data.get('reason', '')
    
    project.schedule_status = 'pending'  # 다시 대기 상태로
    project.schedule_data = None
    
    db.session.commit()
    
    return jsonify({
        'message': '일정 조율을 요청했습니다. 컨설턴트가 새로운 일정을 제안할 것입니다.',
        'schedule_status': project.schedule_status
    })

# --- Cancel Consultant Request ---
@app.route('/api/projects/<int:project_id>/cancel', methods=['POST'])
def cancel_consultant_request(project_id):
    """특정 컨설턴트에 대한 요청 취소 (Soft Delete + 알림)"""
    project = Project.query.get_or_404(project_id)
    
    # 이미 계약된 경우 취소 불가
    if project.status in ['contracted', 'in_progress', 'completed']:
        return jsonify({'message': '계약된 요청은 취소할 수 없습니다.'}), 400
    
    # 이미 취소된 경우
    if project.status == 'cancelled_by_company':
        return jsonify({'message': '이미 취소된 요청입니다.'}), 400
    
    # 취소 사유 받기
    data = request.json or {}
    cancelled_reason = data.get('reason', '사유 없음')
    
    # Soft Delete: 상태를 cancelled_by_company로 변경
    project.status = 'cancelled_by_company'
    project.cancelled_at = datetime.datetime.utcnow()
    project.cancelled_reason = cancelled_reason
    
    # 컨설턴트에게 알림 생성
    try:
        consultant = Consultant.query.get(project.consultant_id)
        if consultant and consultant.user_id:
            company_user = User.query.get(project.company_id)
            company_name = company_user.name if company_user else '기업'
            
            notification = Notification(
                user_id=consultant.user_id,
                type='request_cancelled',
                title=f'{company_name}의 요청이 취소되었습니다',
                message=f'취소 사유: {cancelled_reason}',
                link='/dashboard.html'
            )
            db.session.add(notification)
    except Exception as e:
        print(f"[Notification] Failed to create cancellation notification: {e}")
    
    db.session.commit()
    
    return jsonify({
        'message': '요청이 취소되었습니다.',
        'project_id': project_id,
        'status': project.status,
        'cancelled_at': project.cancelled_at.isoformat() if project.cancelled_at else None
    })

# --- Add Consultant to Existing Quote Request ---
@app.route('/api/projects/add-consultant', methods=['POST'])
def add_consultant_to_request():
    """기존 견적 요청 그룹에 컨설턴트 추가"""
    data = request.json
    user_id = data.get('user_id')
    consultant_id = data.get('consultant_id')
    title = data.get('title')  # 기존 프로젝트 제목 사용
    session_id = data.get('session_id') # 세션 ID 추가
    
    if not user_id or not consultant_id or not title:
        return jsonify({'message': 'user_id, consultant_id, title이 필요합니다.'}), 400
    
    # 컨설턴트 확인
    consultant = Consultant.query.get(consultant_id)
    if not consultant:
        return jsonify({'message': '컨설턴트를 찾을 수 없습니다.'}), 404
    
    # 이미 해당 컨설턴트에게 같은 제목으로 요청한 적 있는지 확인
    existing = Project.query.filter_by(
        company_id=user_id, 
        consultant_id=consultant_id,
        title=title
    ).first()
    
    if existing:
        return jsonify({'message': f'{consultant.name}에게 이미 요청된 프로젝트입니다.'}), 400
    
    # 새 프로젝트 생성
    new_project = Project(
        company_id=user_id,
        consultant_id=consultant_id,
        title=title,
        session_id=session_id, # 세션 ID 저장
        status='proposal_pending',
    )
    if hasattr(new_project, 'proposal_status'):
        new_project.proposal_status = 'pending'
    
    db.session.add(new_project)
    db.session.commit()
    
    # 이메일 발송
    try:
        company = User.query.get(user_id)
        consultant_user = User.query.get(consultant.user_id) if consultant.user_id else None
        if consultant_user and company and email_service:
            email_service.send_consultant_notification(
                consultant_email=consultant_user.email,
                consultant_name=consultant.name,
                company_name=company.name,
                request_details={'title': title}
            )
    except Exception as e:
        print(f"[Email] Failed to send notification: {e}")
    
    return jsonify({
        'message': f'{consultant.name}에게 견적 요청을 추가했습니다.',
        'project_id': new_project.id
    }), 201

# --- Get Available Consultants for Adding to Project ---
@app.route('/api/projects/<string:title>/available-consultants', methods=['GET'])
def get_available_consultants(title):
    """이미 요청되지 않은 컨설턴트 목록 조회 (선별 로직 적용)"""
    user_id = request.args.get('user_id')
    session_id = request.args.get('session_id')
    if not user_id:
        return jsonify({'message': 'user_id가 필요합니다.'}), 400
    
    # 1. 이미 요청된 컨설턴트 ID 목록
    existing_projects = Project.query.filter_by(company_id=user_id, title=title).all()
    existing_consultant_ids = [p.consultant_id for p in existing_projects]
    
    # 2. 추천 로직을 위한 기준 정보(criteria) 구축
    criteria = {}
    
    # session_id로 AnalysisJob 조회 시도
    job = None
    if session_id:
        job = AnalysisJob.query.get(session_id)
    
    if not job:
        # title 등에서 ISO 코드 추출 시도 (세션ID가 없는 경우 대비)
        iso_match = [iso for iso in ["9001", "14001", "45001", "50001", "27001", "22301"] if iso in title]
        if iso_match:
            criteria['recommended_iso'] = [{'code': code} for code in iso_match]
    else:
        # Job 정보가 있는 경우 criteria 추출
        intake_data = job.get_intake_data()
        criteria = {
            'industry': intake_data.get('industry', ''),
            'recommended_iso': [{'code': code} for code in intake_data.get('standards', [])] if isinstance(intake_data.get('standards'), list) else [],
            'project_type': intake_data.get('projectType', ''),
            'region': intake_data.get('region', '')
        }

    # 3. 매칭 서비스 실행 (선별 로직)
    matched = matching_service.match_consultants(criteria)
    
    # 4. 이미 요청한 컨설턴트 제외 및 정보 구성
    results = []
    for c_info in matched:
        if c_info['id'] not in existing_consultant_ids:
            results.append({
                'id': c_info['id'],
                'name': c_info['name'],
                'specialty': c_info['specialty'],
                'rating': c_info['rating'],
                'experience': c_info['experience'],
                'verified': c_info['verified'],
                'profileImageUrl': c_info.get('profileImageUrl') or c_info.get('avatar'),
                'matchReason': c_info.get('matchReason'),
                'matchScore': c_info.get('matchScore')
            })
            
    # 매칭 결과가 부족할 경우Fallback: 검증된 컨설턴트 중 미요청자 추가
    if len(results) < 5:
        existing_in_results = [r['id'] for r in results]
        if existing_consultant_ids:
            others = Consultant.query.filter(
                Consultant.verified == True,
                ~Consultant.id.in_(existing_consultant_ids),
                ~Consultant.id.in_(existing_in_results)
            ).limit(10).all()
        else:
            others = Consultant.query.filter(
                Consultant.verified == True,
                ~Consultant.id.in_(existing_in_results)
            ).limit(10).all()
            
        for c in others:
            results.append({
                'id': c.id,
                'name': c.name,
                'specialty': c.specialty,
                'rating': c.rating,
                'experience': c.experience,
                'verified': c.verified,
                'profileImageUrl': c.profile_image_url,
                'matchReason': "분야별 전문가 (추가 추천)",
                'matchScore': 60
            })

    return jsonify(results[:15]) # 상위 15명 반환

# --- Admin Endpoints ---
@app.route('/api/admin/jobs', methods=['GET'])
def get_admin_jobs():
    # Filter parameter for showing deleted items
    show_deleted = request.args.get('show_deleted', 'false').lower() == 'true'
    
    try:
        # Base query
        query = AnalysisJob.query
        
        # Filter out deleted items unless explicitly requested
        if not show_deleted:
            query = query.filter(AnalysisJob.deleted_at.is_(None))
        
        jobs = query.order_by(AnalysisJob.created_at.desc()).all()
        results = []
        
        for job in jobs:
            # Get intake data for matching info
            intake_data = job.get_intake_data() if job.intake_data else {}
            
            # Find related projects by company name or email
            company_name = job.company_name or intake_data.get('companyName', '')
            related_projects = []
            
            if company_name:
                # Try to find user by email first
                contact_email = intake_data.get('contactEmail', '')
                user = None
                if contact_email:
                    user = User.query.filter_by(email=contact_email).first()
                
                # If not found by email, try by name
                if not user and company_name:
                    user = User.query.filter_by(name=company_name).first()
                
                # Also search projects directly by title containing company_name or standards
                standards = intake_data.get('standards', [])
                if standards:
                    # Build project title pattern from standards
                    std_text = ', '.join(standards) if isinstance(standards, list) else str(standards)
                    project_title_pattern = f"%{std_text}%"
                    matching_projects = Project.query.filter(Project.title.like(project_title_pattern)).all()
                    
                    for p in matching_projects:
                        consultant = Consultant.query.get(p.consultant_id)
                        related_projects.append({
                            'project_id': p.id,
                            'title': p.title,
                            'status': p.status,
                            'consultant_id': p.consultant_id,
                            'consultant_name': consultant.name if consultant else 'Unknown'
                        })
                
                # Also add projects from user if found
                if user:
                    user_projects = Project.query.filter_by(company_id=user.id).all()
                    existing_ids = [p['project_id'] for p in related_projects]
                    for p in user_projects:
                        if p.id not in existing_ids:
                            consultant = Consultant.query.get(p.consultant_id)
                            related_projects.append({
                                'project_id': p.id,
                                'title': p.title,
                                'status': p.status,
                                'consultant_id': p.consultant_id,
                                'consultant_name': consultant.name if consultant else 'Unknown'
                            })
            
            results.append({
                'id': job.id,
                'company_name': job.company_name,
                'url': job.url,
                'status': job.status,
                'created_at': job.created_at.isoformat() if job.created_at else None,
                'deleted_at': job.deleted_at.isoformat() if job.deleted_at else None,
                # Matching request info (instead of AI result)
                'intake_data': {
                    'industry': intake_data.get('industry'),
                    'employees': intake_data.get('employees'),
                    'region': intake_data.get('region'),
                    'standards': intake_data.get('standards', []),
                    'issues': intake_data.get('issues', []),
                    'timeline': intake_data.get('timeline'),
                    'budget': intake_data.get('budget'),
                    'contact_email': intake_data.get('contactEmail')
                },
                'related_projects': related_projects,
                'project_count': len(related_projects)
            })
        return jsonify(results)
    except Exception as e:
        print(f"[Admin API] Error fetching jobs: {e}")
        return jsonify({'message': '데이터를 불러오는 중 오류가 발생했습니다.', 'error': str(e)}), 500


# --- Admin Job Delete Endpoint ---
@app.route('/api/admin/jobs/<string:job_id>', methods=['DELETE'])
def delete_admin_job(job_id):
    """Soft delete a matching request (AnalysisJob)"""
    job = AnalysisJob.query.get_or_404(job_id)
    
    # Check if there are any contracted projects related to this job
    intake_data = job.get_intake_data() if job.intake_data else {}
    contact_email = intake_data.get('contactEmail', '')
    
    if contact_email:
        user = User.query.filter_by(email=contact_email).first()
        if user:
            contracted_projects = Project.query.filter(
                Project.company_id == user.id,
                Project.status.in_(['contracted', 'in_progress', 'completed'])
            ).count()
            
            if contracted_projects > 0:
                return jsonify({
                    'message': '계약된 프로젝트가 있어 삭제할 수 없습니다.',
                    'contracted_count': contracted_projects
                }), 400
    
    # Soft delete
    job.deleted_at = datetime.datetime.utcnow()
    job.status = 'deleted'
    db.session.commit()
    
    return jsonify({
        'message': '매칭 요청이 삭제되었습니다.',
        'id': job_id,
        'deleted_at': job.deleted_at.isoformat()
    })

# --- Consultant Admin Endpoints ---
@app.route('/api/admin/consultants/<int:consultant_id>/approve', methods=['POST'])
def approve_consultant(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)
    consultant.verified = True
    consultant.trust_score = max(consultant.trust_score or 50, 70)
    db.session.commit()
    return jsonify({'message': 'Consultant approved successfully', 'verified': True})

@app.route('/api/admin/consultants/<int:consultant_id>/reject', methods=['POST'])
def reject_consultant(consultant_id):
    data = request.json
    reason = data.get('reason', 'No reason provided')
    consultant = Consultant.query.get_or_404(consultant_id)
    db.session.delete(consultant)
    db.session.commit()
    return jsonify({'message': f'Consultant rejected: {reason}'})

@app.route('/api/admin/consultants/<int:consultant_id>/revoke', methods=['POST'])
def revoke_consultant_verification(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)
    consultant.verified = False
    consultant.trust_score = min(consultant.trust_score or 50, 50)
    db.session.commit()
    return jsonify({'message': 'Consultant verification revoked', 'verified': False})

# --- Consultant Detail Endpoint ---
@app.route('/api/consultants/<int:consultant_id>', methods=['GET'])
def get_consultant_detail(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)
    
    return jsonify({
        'id': consultant.id,
        'name': consultant.name,
        'avatar': consultant.avatar,
        'profileImageUrl': consultant.profile_image_url,
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
    })

# --- Quote Request Endpoints ---
@app.route('/api/quotes/request', methods=['POST'])
def request_quotes():
    data = request.json
    consultant_ids = data.get('consultant_ids', [])
    analysis_context = data.get('analysis_context') or {}  # Ensure it's not None
    
    # Ensure analysis_context is a dict
    if not isinstance(analysis_context, dict):
        analysis_context = {}
    
    if not consultant_ids:
        return jsonify({'message': 'No consultants selected'}), 400
    
    if len(consultant_ids) > 5:
        return jsonify({'message': 'Maximum 5 consultants can be selected'}), 400
    
    # Verify all consultants exist
    consultants = Consultant.query.filter(Consultant.id.in_(consultant_ids)).all()
    if len(consultants) != len(consultant_ids):
        return jsonify({'message': 'Some consultants not found'}), 404
    
    # Get user info (if logged in)
    user_id = request.args.get('user_id') or data.get('user_id')
    
    if not user_id:
        return jsonify({'message': 'User ID required. Please log in first.'}), 401
    
    # Generate project title from analysis context
    company_name = analysis_context.get('company_name', '기업')
    
    # Check multiple possible keys for standards
    recommended_standards = (
        analysis_context.get('selected_standards') or 
        analysis_context.get('all_standards') or 
        analysis_context.get('recommended_standards') or 
        []
    )
    
    iso_codes = []
    if isinstance(recommended_standards, list) and len(recommended_standards) > 0:
        for std in recommended_standards:
            if isinstance(std, dict):
                code = std.get('code', '')
                if code:
                    iso_codes.append(code)
            elif isinstance(std, str) and std:
                iso_codes.append(std)
    
    # Build title without duplication
    if iso_codes:
        iso_text = ', '.join(iso_codes)
        project_title = f"{iso_text} 인증 프로젝트"
    else:
        project_title = "ISO 인증 프로젝트"
    
    # Get session_id from frontend (for grouping projects from same matching session)
    session_id = data.get('session_id') or str(uuid.uuid4())
    
    # Create quote requests and projects for each consultant
    quote_request_id = str(uuid.uuid4())
    created_requests = []
    created_projects = []
    
    for consultant in consultants:
        # === Duplicate Prevention ===
        # Check if an ACTIVE project with same company+consultant+title already exists
        active_statuses = ['planning', 'proposal_pending', 'proposal_submitted', 'contracted', 'in_progress']
        existing = Project.query.filter(
            Project.company_id == user_id,
            Project.consultant_id == consultant.id,
            Project.title == project_title,
            Project.status.in_(active_statuses)
        ).first()
        
        if existing:
            # Skip this consultant - already has active project
            created_requests.append({
                'consultant_id': consultant.id,
                'consultant_name': consultant.name,
                'skipped': True,
                'reason': '이미 진행 중인 동일 요청이 있습니다',
                'existing_project_id': existing.id
            })
            continue
        
        # Create a project for each consultant
        try:
            new_project = Project(
                company_id=user_id,
                consultant_id=consultant.id,
                title=project_title,
                status='proposal_pending',  # 계약 전 상태
            )
            # Set optional fields if they exist
            if hasattr(new_project, 'session_id'):
                new_project.session_id = session_id
            if hasattr(new_project, 'proposal_status'):
                new_project.proposal_status = 'pending'
            
            db.session.add(new_project)
            db.session.flush()  # Get the project ID
            
            # 마일스톤은 계약 후에 생성 (sign_contract에서 처리)
            
            created_projects.append({
                'project_id': new_project.id,
                'consultant_id': consultant.id,
                'consultant_name': consultant.name,
                'title': project_title
            })
            
            created_requests.append({
                'consultant_id': consultant.id,
                'consultant_name': consultant.name,
                'status': 'pending',
                'project_id': new_project.id
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({'message': f'Failed to create project: {str(e)}'}), 500
    
    # Commit all projects
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'Failed to save projects: {str(e)}'}), 500
    
    # --- 이메일 발송 ---
    email_results = []
    
    # 컨설턴트별로 알림 이메일 발송
    for consultant in consultants:
        # 컨설턴트 User의 이메일 가져오기
        consultant_user = User.query.get(consultant.user_id) if consultant.user_id else None
        consultant_email = consultant_user.email if consultant_user else None
        
        if consultant_email and consultant_email != 'dummy':
            # 프로젝트 ID 찾기
            project_id = next(
                (p['project_id'] for p in created_projects if p['consultant_id'] == consultant.id),
                None
            )
            
            try:
                result = email_service.send_quote_request_to_consultant(
                    consultant_email=consultant_email,
                    consultant_name=consultant.name,
                    company_name=company_name,
                    industry=analysis_context.get('industry', '미정'),
                    standards=recommended_standards if isinstance(recommended_standards, list) else [],
                    issues_summary=analysis_context.get('issues_summary'),
                    timeline=analysis_context.get('timeline', 'flexible'),
                    budget=analysis_context.get('budget', 'unknown'),
                    additional_notes=analysis_context.get('additional_notes'),
                    project_id=project_id
                )
                email_results.append({
                    'consultant_id': consultant.id,
                    'consultant_name': consultant.name,
                    'email_sent': result.get('success', False),
                    'simulated': result.get('simulated', False)
                })
            except Exception as e:
                print(f"[Email] Error sending to {consultant.name}: {e}")
                email_results.append({
                    'consultant_id': consultant.id,
                    'consultant_name': consultant.name,
                    'email_sent': False,
                    'error': str(e)
                })
    
    # 기업 사용자에게 확인 이메일 발송
    company_user = User.query.get(user_id)
    company_email = company_user.email if company_user else analysis_context.get('contact_email')
    
    if company_email:
        try:
            consultant_names = [c.name for c in consultants]
            email_service.send_quote_confirmation_to_company(
                company_email=company_email,
                company_name=company_name,
                consultant_names=consultant_names,
                standards=recommended_standards if isinstance(recommended_standards, list) else []
            )
        except Exception as e:
            print(f"[Email] Error sending confirmation to company: {e}")
    
    return jsonify({
        'message': f'Quote requested from {len(consultants)} consultants',
        'quote_request_id': quote_request_id,
        'requests': created_requests,
        'projects': created_projects,
        'email_notifications': email_results,
        'analysis_context': {
            'company_name': analysis_context.get('company_name'),
            'industry': analysis_context.get('industry'),
            'recommended_standards': analysis_context.get('recommended_standards', [])
        }
    }), 201

# --- Blog Endpoints ---
@app.route('/api/posts', methods=['GET', 'POST'])
def handle_posts():
    if request.method == 'GET':
        posts = Post.query.order_by(Post.created_at.desc()).all()
        return jsonify([p.to_dict() for p in posts])
    
    elif request.method == 'POST':
        data = request.json
        new_post = Post(
            title=data.get('title'),
            content=data.get('content'),
            author=data.get('author', 'InsightMatch Team'),
            tags=data.get('tags'),
            image_url=data.get('image_url')
        )
        db.session.add(new_post)
        db.session.commit()
        return jsonify({'message': 'Post created', 'id': new_post.id}), 201

@app.route('/api/posts/<int:post_id>', methods=['GET'])
def get_post(post_id):
    post = Post.query.get_or_404(post_id)
    return jsonify(post.to_dict())

# --- SEO Endpoints ---
@app.route('/api/sitemap.xml')
def sitemap():
    base_url = os.environ.get('BASE_URL', 'https://insight-match.vercel.app')
    pages = ['/', '/index.html', '/login.html', '/signup.html', '/blog.html']
    
    sitemap_xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    sitemap_xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    
    for page in pages:
        sitemap_xml.append('<url>')
        sitemap_xml.append(f'<loc>{base_url}{page}</loc>')
        sitemap_xml.append('<changefreq>daily</changefreq>')
        sitemap_xml.append('<priority>0.8</priority>')
        sitemap_xml.append('</url>')
        
    posts = Post.query.all()
    for post in posts:
        sitemap_xml.append('<url>')
        sitemap_xml.append(f'<loc>{base_url}/blog_detail.html?id={post.id}</loc>')
        sitemap_xml.append(f'<lastmod>{post.created_at.strftime("%Y-%m-%d")}</lastmod>')
        sitemap_xml.append('<changefreq>weekly</changefreq>')
        sitemap_xml.append('<priority>0.6</priority>')
        sitemap_xml.append('</url>')
        
    sitemap_xml.append('</urlset>')
    return Response('\n'.join(sitemap_xml), mimetype='text/xml')

@app.route('/api/robots.txt')
def robots():
    base_url = os.environ.get('BASE_URL', 'https://insight-match.vercel.app')
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {base_url}/api/sitemap.xml"
    ]
    return Response('\n'.join(lines), mimetype='text/plain')

# --- Health Check ---
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.datetime.utcnow().isoformat()})

# --- Seed Data Endpoint (Admin only) ---
@app.route('/api/admin/seed', methods=['POST'])
def seed_data():
    # Seed Posts
    if Post.query.count() == 0:
        posts = [
            {
                "title": "2025년 ISO 9001 개정 방향과 기업의 대응 전략",
                "content": "ISO 9001 품질경영시스템이 2025년 대대적인 개정을 앞두고 있습니다. 이번 개정에서는 AI 기술 도입에 따른 품질 관리 프로세스의 변화와 ESG 경영 요소의 통합이 주요 화두가 될 전망입니다...",
                "tags": "ISO 9001,품질경영,트렌드",
                "image_url": "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?auto=format&fit=crop&q=80&w=1000"
            },
            {
                "title": "중대재해처벌법 대응을 위한 ISO 45001 도입 가이드",
                "content": "중대재해처벌법 시행 이후 안전보건경영시스템(ISO 45001)에 대한 관심이 급증하고 있습니다. 체계적인 위험성 평가와 근로자 참여를 보장하는 ISO 45001 구축은 법적 리스크를 최소화하는 가장 확실한 방법입니다...",
                "tags": "ISO 45001,안전보건,중대재해처벌법",
                "image_url": "https://images.unsplash.com/photo-1581094794329-cd11965d158e?auto=format&fit=crop&q=80&w=1000"
            },
            {
                "title": "ESG 경영과 ISO 14001: 환경 리스크 관리의 핵심",
                "content": "글로벌 공급망에서 ESG 평가가 필수화되면서 환경경영시스템(ISO 14001) 인증은 선택이 아닌 필수가 되었습니다. 탄소 배출량 관리와 자원 순환 프로세스를 ISO 14001을 통해 어떻게 시스템화할 수 있는지 알아봅니다...",
                "tags": "ESG,ISO 14001,환경경영",
                "image_url": "https://images.unsplash.com/photo-1473341304170-971dccb5ac1e?auto=format&fit=crop&q=80&w=1000"
            }
        ]
        for p_data in posts:
            post = Post(**p_data)
            db.session.add(post)
        db.session.commit()
        
    # Seed Consultants
    consultant_count = Consultant.query.count()
    if consultant_count < 8:
        consultants = [
            {
                "name": "김철수",
                "specialty": "제조/화학",
                "experience": "15년",
                "rating": 4.9,
                "reviews": 24,
                "match_reason": "화학 업종 전문 심사원",
                "verified": True,
                "trust_score": 92.5,
                "iso_experience": json.dumps({"ISO 9001": "Lead Auditor", "ISO 14001": "Auditor"}),
                "industry_experience": json.dumps(["Chemical", "Manufacturing"]),
                "avatar": "K"
            },
            {
                "name": "이영희",
                "specialty": "IT/서비스",
                "experience": "8년",
                "rating": 4.8,
                "reviews": 15,
                "match_reason": "IT 보안 및 품질 통합 전문가",
                "verified": True,
                "trust_score": 88.0,
                "iso_experience": json.dumps({"ISO 9001": "Auditor", "ISO 27001": "Lead Auditor"}),
                "industry_experience": json.dumps(["IT", "Service"]),
                "avatar": "L"
            },
            {
                "name": "박민수",
                "specialty": "건설/안전",
                "experience": "20년",
                "rating": 5.0,
                "reviews": 42,
                "match_reason": "건설 안전 분야 최고 전문가",
                "verified": True,
                "trust_score": 98.0,
                "iso_experience": json.dumps({"ISO 45001": "Lead Auditor"}),
                "industry_experience": json.dumps(["Construction"]),
                "avatar": "P"
            },
            {
                "name": "최지은",
                "specialty": "IT/정보보호",
                "experience": "12년",
                "rating": 4.7,
                "reviews": 31,
                "match_reason": "정보보안 및 개인정보보호 전문가",
                "verified": True,
                "trust_score": 90.5,
                "iso_experience": json.dumps({"ISO 27001": "Lead Auditor", "ISO 9001": "Auditor"}),
                "industry_experience": json.dumps(["IT", "Finance", "Service"]),
                "avatar": "C"
            },
            {
                "name": "정대현",
                "specialty": "환경/ESG",
                "experience": "18년",
                "rating": 4.9,
                "reviews": 38,
                "match_reason": "ESG 경영 및 환경경영시스템 전문가",
                "verified": True,
                "trust_score": 94.0,
                "iso_experience": json.dumps({"ISO 14001": "Lead Auditor", "ISO 9001": "Auditor"}),
                "industry_experience": json.dumps(["Manufacturing", "Chemical", "Energy"]),
                "avatar": "J"
            },
            {
                "name": "한소영",
                "specialty": "의료/바이오",
                "experience": "10년",
                "rating": 4.8,
                "reviews": 22,
                "match_reason": "의료기기 및 바이오 품질관리 전문가",
                "verified": True,
                "trust_score": 89.0,
                "iso_experience": json.dumps({"ISO 9001": "Lead Auditor", "ISO 13485": "Auditor"}),
                "industry_experience": json.dumps(["Medical", "Biotech", "Pharmaceutical"]),
                "avatar": "H"
            },
            {
                "name": "윤태호",
                "specialty": "종합/통합",
                "experience": "25년",
                "rating": 5.0,
                "reviews": 56,
                "match_reason": "다중 ISO 통합 경영시스템 구축 전문가",
                "verified": True,
                "trust_score": 96.5,
                "iso_experience": json.dumps({
                    "ISO 9001": "Lead Auditor",
                    "ISO 14001": "Lead Auditor",
                    "ISO 45001": "Lead Auditor",
                    "ISO 27001": "Auditor"
                }),
                "industry_experience": json.dumps(["Manufacturing", "IT", "Service", "Construction"]),
                "avatar": "Y"
            },
            {
                "name": "강미라",
                "specialty": "자동차/부품",
                "experience": "14년",
                "rating": 4.9,
                "reviews": 28,
                "match_reason": "자동차 산업 IATF 16949 및 ISO 9001 전문가",
                "verified": True,
                "trust_score": 93.0,
                "iso_experience": json.dumps({"ISO 9001": "Lead Auditor", "IATF 16949": "Lead Auditor"}),
                "industry_experience": json.dumps(["Automotive", "Manufacturing", "Parts"]),
                "avatar": "G"
            }
        ]
        for c_data in consultants:
            # Check if consultant already exists
            existing = Consultant.query.filter_by(name=c_data['name']).first()
            if existing:
                continue
            
            u = User(email=f"{c_data['name']}@example.com", password_hash="dummy", role="consultant", name=c_data['name'])
            db.session.add(u)
            db.session.commit()
            
            c = Consultant(user_id=u.id, **c_data)
            db.session.add(c)
        db.session.commit()
    
    return jsonify({'message': 'Seed data created successfully'})

# ========================================
# ② Notification System APIs
# ========================================

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    """사용자 알림 목록 조회"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'message': 'User ID required'}), 400
    
    notifications = Notification.query.filter_by(user_id=user_id).order_by(Notification.created_at.desc()).limit(50).all()
    unread_count = Notification.query.filter_by(user_id=user_id, is_read=False).count()
    
    return jsonify({
        'notifications': [n.to_dict() for n in notifications],
        'unreadCount': unread_count
    })

@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):
    """알림 읽음 처리"""
    notification = Notification.query.get_or_404(notification_id)
    notification.is_read = True
    db.session.commit()
    return jsonify({'message': 'Marked as read'})

@app.route('/api/notifications/read-all', methods=['POST'])
def mark_all_notifications_read():
    """모든 알림 읽음 처리"""
    data = request.json or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'message': 'User ID required'}), 400
    
    Notification.query.filter_by(user_id=user_id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({'message': 'All notifications marked as read'})

def create_notification(user_id, type, title, message=None, link=None):
    """알림 생성 헬퍼 함수"""
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        link=link
    )
    db.session.add(notification)
    db.session.commit()
    return notification

# ========================================
# ① Consultant Profile APIs
# ========================================

@app.route('/api/consultants/<int:consultant_id>/profile', methods=['GET', 'PUT'])
def consultant_profile(consultant_id):
    """컨설턴트 프로필 조회/수정"""
    consultant = Consultant.query.get_or_404(consultant_id)
    
    if request.method == 'GET':
        return jsonify(consultant.to_dict())
    
    elif request.method == 'PUT':
        data = request.json
        user_id = data.get('user_id')  # 변경한 사용자 ID
        
        # 변경 이력 기록을 위한 필드 매핑
        field_mapping = {
            'bio': 'bio',
            'phone': 'phone',
            'email': 'email',
            'companyName': 'company_name',
            'profileImageUrl': 'profile_image_url',
            'introductionVideoUrl': 'introduction_video_url',
            'specialty': 'specialty',
            'regions': 'regions'
        }
        
        # 각 필드에 대해 변경 여부 확인 및 이력 기록
        for json_key, db_field in field_mapping.items():
            if json_key in data:
                old_value = getattr(consultant, db_field)
                new_value = data[json_key]
                
                # 값이 실제로 변경되었는지 확인
                if str(old_value or '') != str(new_value or ''):
                    # 변경 이력 기록
                    change_log = ProfileChangeLog(
                        consultant_id=consultant_id,
                        field_name=json_key,
                        old_value=str(old_value) if old_value else None,
                        new_value=str(new_value) if new_value else None,
                        changed_by=user_id
                    )
                    db.session.add(change_log)
                
                # 값 업데이트
                setattr(consultant, db_field, new_value)
        
        db.session.commit()
        return jsonify({'message': 'Profile updated', 'consultant': consultant.to_dict()})

@app.route('/api/consultants/<int:consultant_id>/portfolio', methods=['POST', 'DELETE'])
def manage_portfolio(consultant_id):
    """포트폴리오 파일 관리"""
    consultant = Consultant.query.get_or_404(consultant_id)
    
    current_files = json.loads(consultant.portfolio_files) if consultant.portfolio_files else []
    
    if request.method == 'POST':
        data = request.json
        new_file = {
            'name': data.get('name'),
            'url': data.get('url'),
            'uploaded_at': datetime.datetime.utcnow().isoformat()
        }
        current_files.append(new_file)
        consultant.portfolio_files = json.dumps(current_files)
        db.session.commit()
        return jsonify({'message': 'Portfolio file added', 'files': current_files})
    
    elif request.method == 'DELETE':
        data = request.json
        file_url = data.get('url')
        current_files = [f for f in current_files if f.get('url') != file_url]
        consultant.portfolio_files = json.dumps(current_files)
        db.session.commit()
        return jsonify({'message': 'Portfolio file removed', 'files': current_files})

# ========================================
# ③ File Upload API (Supabase Storage)
# ========================================

@app.route('/api/upload/signed-url', methods=['POST'])
def get_upload_signed_url():
    """Supabase Storage 업로드용 Signed URL 생성"""
    # NOTE: 실제 Supabase Storage 연동 시 supabase-py 사용
    # 현재는 클라이언트에서 직접 Supabase에 업로드하도록 URL만 반환
    data = request.json
    file_name = data.get('fileName')
    file_type = data.get('fileType')
    bucket = data.get('bucket', 'proposals')  # proposals, portfolios, profiles
    
    # 고유한 파일명 생성
    unique_name = f"{uuid.uuid4()}_{file_name}"
    
    # Supabase Storage URL (클라이언트에서 직접 사용)
    supabase_url = os.environ.get('SUPABASE_URL', '')
    
    return jsonify({
        'fileName': unique_name,
        'bucket': bucket,
        'uploadPath': f"{bucket}/{unique_name}",
        'publicUrl': f"{supabase_url}/storage/v1/object/public/{bucket}/{unique_name}" if supabase_url else None
    })

# ========================================
# 관리자용 프로필 변경 이력 조회 API
# ========================================

@app.route('/api/admin/profile-change-logs', methods=['GET'])
def get_profile_change_logs():
    """프로필 변경 이력 조회 (관리자용)"""
    consultant_id = request.args.get('consultant_id')
    limit = request.args.get('limit', 100, type=int)
    
    query = ProfileChangeLog.query.order_by(ProfileChangeLog.changed_at.desc())
    
    if consultant_id:
        query = query.filter_by(consultant_id=consultant_id)
    
    logs = query.limit(limit).all()
    
    # 컨설턴트 이름과 변경자 이름 추가
    result = []
    for log in logs:
        log_dict = log.to_dict()
        
        # 컨설턴트 이름
        consultant = Consultant.query.get(log.consultant_id)
        log_dict['consultantName'] = consultant.name if consultant else 'Unknown'
        
        # 변경자 이름
        if log.changed_by:
            changer = User.query.get(log.changed_by)
            log_dict['changedByName'] = changer.name if changer else 'Unknown'
        else:
            log_dict['changedByName'] = 'System'
        
        result.append(log_dict)
    
    return jsonify(result)

# ========================================
# 프로필 이미지 업로드 API
# ========================================

@app.route('/api/upload/profile-image', methods=['POST'])
def upload_profile_image():
    """프로필 이미지를 Supabase Storage에 업로드"""
    import requests
    import time as time_module
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    user_id = request.form.get('user_id', 'unknown')
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # 파일 크기 검증 (100KB)
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    
    if file_size > 100 * 1024:
        return jsonify({'error': '파일 크기는 100KB 이하여야 합니다.'}), 400
    
    # 파일 타입 검증
    allowed_types = ['image/jpeg', 'image/png', 'image/gif']
    if file.content_type not in allowed_types:
        return jsonify({'error': 'JPG, PNG, GIF 파일만 업로드 가능합니다.'}), 400
    
    # Supabase 설정
    supabase_url = os.environ.get('SUPABASE_URL', 'https://ghyioswdnfgtijowvpeo.supabase.co')
    supabase_key = os.environ.get('SUPABASE_ANON_KEY', '') # 사용자가 제공해야 함
    
    # 파일명 생성
    file_ext = file.filename.rsplit('.', 1)[-1].lower()
    unique_name = f"profile_{user_id}_{int(time_module.time())}.{file_ext}"
    
    # Supabase Storage에 업로드
    upload_url = f"{supabase_url}/storage/v1/object/profiles/{unique_name}"
    
    headers = {
        'Authorization': f'Bearer {supabase_key}',
        'apikey': supabase_key,
        'Content-Type': file.content_type
    }
    
    try:
        response = requests.post(upload_url, headers=headers, data=file.read())
        
        if response.status_code in [200, 201]:
            public_url = f"{supabase_url}/storage/v1/object/public/profiles/{unique_name}"
            return jsonify({
                'success': True,
                'url': public_url,
                'fileName': unique_name
            })
        else:
            error_detail = response.json() if response.text else {'message': 'Unknown error'}
            return jsonify({
                'error': f"업로드 실패: {error_detail.get('message', response.status_code)}"
            }), 500
            
    except Exception as e:
        return jsonify({'error': f'업로드 중 오류: {str(e)}'}), 500

# ========================================
# B. 인앱 메시지 API
# ========================================

@app.route('/api/projects/<int:project_id>/messages', methods=['GET', 'POST'])
def handle_messages(project_id):
    """프로젝트 메시지 조회/전송"""
    project = Project.query.get_or_404(project_id)
    
    if request.method == 'GET':
        # 메시지 목록 조회
        messages = Message.query.filter_by(project_id=project_id).order_by(Message.created_at.asc()).all()
        
        # 발신자 정보 추가
        result = []
        for msg in messages:
            sender = User.query.get(msg.sender_id)
            msg_dict = msg.to_dict()
            msg_dict['senderName'] = sender.name if sender else 'Unknown'
            msg_dict['senderRole'] = sender.role if sender else 'unknown'
            result.append(msg_dict)
        
        return jsonify(result)
    
    elif request.method == 'POST':
        data = request.json
        sender_id = data.get('sender_id')
        content = data.get('content', '').strip()
        
        if not content:
            return jsonify({'message': '메시지 내용을 입력해주세요.'}), 400
        
        # 메시지 생성
        message = Message(
            project_id=project_id,
            sender_id=sender_id,
            content=content
        )
        db.session.add(message)
        db.session.commit()
        
        # 상대방에게 알림 생성
        try:
            sender = User.query.get(sender_id)
            # 수신자 결정 (기업이면 컨설턴트에게, 컨설턴트면 기업에게)
            if sender and sender.role == 'company':
                consultant = Consultant.query.get(project.consultant_id)
                if consultant:
                    consultant_user = User.query.get(consultant.user_id)
                    if consultant_user:
                        notification = Notification(
                            user_id=consultant_user.id,
                            type='new_message',
                            title=f'{sender.name}님이 메시지를 보냈습니다',
                            message=content[:50] + ('...' if len(content) > 50 else ''),
                            link=f'/dashboard.html?project={project_id}'
                        )
                        db.session.add(notification)
            else:
                # 컨설턴트가 보낸 경우 기업에게 알림
                company_user = User.query.get(project.company_id)
                if company_user:
                    notification = Notification(
                        user_id=company_user.id,
                        type='new_message',
                        title=f'{sender.name if sender else "컨설턴트"}님이 메시지를 보냈습니다',
                        message=content[:50] + ('...' if len(content) > 50 else ''),
                        link=f'/dashboard.html?project={project_id}'
                    )
                    db.session.add(notification)
            db.session.commit()
        except Exception as e:
            print(f"[Notification] Failed to create message notification: {e}")
        
        return jsonify({'message': '메시지가 전송되었습니다.', 'id': message.id})

@app.route('/api/projects/<int:project_id>/messages/read', methods=['POST'])
def mark_messages_read(project_id):
    """해당 프로젝트의 메시지 읽음 처리"""
    data = request.json or {}
    user_id = data.get('user_id')
    
    if not user_id:
        return jsonify({'message': 'User ID required'}), 400
    
    # 본인이 보낸 메시지가 아닌 것들만 읽음 처리
    Message.query.filter(
        Message.project_id == project_id,
        Message.sender_id != user_id,
        Message.is_read == False
    ).update({'is_read': True})
    db.session.commit()
    
    return jsonify({'message': 'Messages marked as read'})

@app.route('/api/messages/unread-count', methods=['GET'])
def get_unread_message_count():
    """읽지 않은 메시지 수 조회"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'message': 'User ID required'}), 400
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'count': 0})
    
    # 사용자가 참여한 프로젝트의 읽지 않은 메시지 수
    if user.role == 'company':
        projects = Project.query.filter_by(company_id=user_id).all()
    else:
        consultant = Consultant.query.filter_by(user_id=user_id).first()
        if consultant:
            projects = Project.query.filter_by(consultant_id=consultant.id).all()
        else:
            projects = []
    
    project_ids = [p.id for p in projects]
    if not project_ids:
        return jsonify({'count': 0})
    
    count = Message.query.filter(
        Message.project_id.in_(project_ids),
        Message.sender_id != int(user_id),
        Message.is_read == False
    ).count()
    
    return jsonify({'count': count})

# C. 계약 후에만 연락처 공개
@app.route('/api/projects/<int:project_id>/contact-info', methods=['GET'])
def get_contact_info(project_id):
    """계약 완료된 프로젝트의 컨설턴트 연락처 조회"""
    project = Project.query.get_or_404(project_id)
    
    # 계약 완료 상태인지 확인
    if project.status not in ['contracted', 'in_progress', 'completed']:
        return jsonify({'message': '계약이 완료된 후에만 연락처를 확인할 수 있습니다.'}), 403
    
    consultant = Consultant.query.get(project.consultant_id)
    if not consultant:
        return jsonify({'message': '컨설턴트 정보를 찾을 수 없습니다.'}), 404
    
    return jsonify({
        'name': consultant.name,
        'phone': consultant.phone,
        'email': consultant.email,
        'companyName': consultant.company_name
    })


@app.route('/')
def index():
    return send_file('../index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files (HTML, CSS, JS, images)"""
    file_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename)
    if os.path.exists(file_path):
        return send_file(file_path)
    else:
        return jsonify({'error': 'File not found'}), 404

# Vercel automatically detects Flask app named 'app'

# For local development
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

