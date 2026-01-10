import os
import sys
import uuid
import json
import datetime
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import jwt
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, AnalysisJob, Consultant, User, Project, Milestone, Post, Company
from services import AIService, MatchingService, ProposalService, EmailService

# Windows 콘솔 인코딩 설정
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

# Load environment variables from .env file
load_dotenv()

# Configure Flask to serve static files from the project root (one level up)
basedir = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.abspath(os.path.join(basedir, '..'))

app = Flask(__name__, static_folder=project_root, static_url_path='')
CORS(app)

# Database Config
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///' + os.path.join(basedir, 'insightmatch.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-123')

db.init_app(app)

# Initialize Services
ai_service = AIService()
matching_service = MatchingService()
proposal_service = ProposalService()
email_service = EmailService()

@app.route('/')
def home():
    return send_file(os.path.join(project_root, 'index.html'))

# --- Auth Endpoints ---
@app.route('/api/auth/signup', methods=['POST'])
def signup():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    name = data.get('name')
    role = data.get('role', 'company') # company, consultant, admin
    
    if User.query.filter_by(email=email).first():
        return jsonify({'message': 'Email already exists'}), 400
        
    new_user = User(
        email=email,
        password_hash=generate_password_hash(password),
        name=name,
        role=role
    )
    db.session.add(new_user)
    db.session.commit()
    
    # Create associated profile based on role
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
    # Save full intake data
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
        # Mark as analyzing to prevent race conditions
        job.status = 'analyzing'
        db.session.commit()
        
        try:
            # Use saved intake data for analysis
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
        # Still working
        return jsonify({'status': 'processing'}) # Return processing to keep client polling

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
            # Merge analysis result with manual filters if any
            criteria = analysis_result
            
    # Manual overrides or direct search
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
        
        # New Fields
        iso_experience=json.dumps(data.get('iso_experience', {})),
        industry_experience=json.dumps(data.get('industry_experience', [])),
        project_types=json.dumps(data.get('project_types', [])),
        org_size_experience=json.dumps(data.get('org_size_experience', [])),
        roles=json.dumps(data.get('roles', [])),
        detailed_certifications=json.dumps(data.get('detailed_certifications', [])),
        verified=False, # Default to unverified
        trust_score=50.0 # Default starting score
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
            
        projects = Project.query.filter((Project.company_id == user_id) | (Project.consultant_id == user_id)).all()
        results = []
        for p in projects:
            consultant = Consultant.query.get(p.consultant_id)
            results.append({
                'id': p.id,
                'title': p.title,
                'status': p.status,
                'proposal_status': getattr(p, 'proposal_status', 'pending'),
                'proposal_data': p.proposal_data if hasattr(p, 'proposal_data') else None,
                'consultant_id': p.consultant_id,
                'consultant_name': consultant.name if consultant else 'Unknown',
                'start_date': p.start_date.isoformat() if p.start_date else None,
                'milestones': [m.to_dict() for m in p.milestones]
            })
        return jsonify(results)
        
    elif request.method == 'POST':
        data = request.json
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
    
    # 상태를 'contracted'로 변경
    project.status = 'contracted'
    project.proposal_status = 'accepted'
    project.start_date = datetime.datetime.utcnow()
    
    # 계약 후 마일스톤이 없으면 생성
    if not project.milestones:
        defaults = ["Kick-off Meeting", "Gap Analysis", "Documentation", "Internal Audit", "Final Certification"]
        for title in defaults:
            m = Milestone(project_id=project.id, title=title)
            db.session.add(m)
    
    db.session.commit()
    return jsonify({'message': 'Contract signed successfully', 'status': project.status})

# --- Phase 2: Project Delete/Cancel ---
@app.route('/api/projects/<int:project_id>', methods=['DELETE'])
def delete_project(project_id):
    """프로젝트 삭제 (제안서 대기 상태에서만 가능)"""
    project = Project.query.get_or_404(project_id)
    
    # 계약된 프로젝트는 삭제 불가
    if project.status in ['contracted', 'in_progress', 'completed']:
        return jsonify({'message': '계약된 프로젝트는 삭제할 수 없습니다.'}), 400
    
    # 관련 마일스톤 삭제
    Milestone.query.filter_by(project_id=project_id).delete()
    
    # 프로젝트 삭제
    db.session.delete(project)
    db.session.commit()
    
    return jsonify({'message': '프로젝트가 삭제되었습니다.'})

# --- Phase 3: Cancel specific consultant request ---
@app.route('/api/projects/<int:project_id>/cancel', methods=['POST'])
def cancel_consultant_request(project_id):
    """특정 컨설턴트에 대한 요청 취소"""
    project = Project.query.get_or_404(project_id)
    
    # 이미 계약된 경우 취소 불가
    if project.status in ['contracted', 'in_progress', 'completed']:
        return jsonify({'message': '계약된 요청은 취소할 수 없습니다.'}), 400
    
    # 이미 제안서가 제출된 경우
    if project.proposal_status == 'submitted':
        return jsonify({'message': '이미 제안서가 제출된 요청입니다. 삭제하시겠습니까?'}), 400
    
    # 프로젝트 삭제
    Milestone.query.filter_by(project_id=project_id).delete()
    db.session.delete(project)
    db.session.commit()
    
    return jsonify({'message': '요청이 취소되었습니다.'})

# --- Phase 3: Add consultant to existing quote request ---
@app.route('/api/projects/add-consultant', methods=['POST'])
def add_consultant_to_request():
    """기존 견적 요청 그룹에 컨설턴트 추가"""
    data = request.json
    user_id = data.get('user_id')
    consultant_id = data.get('consultant_id')
    title = data.get('title')  # 기존 프로젝트 제목 사용
    
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
        status='proposal_pending',
        proposal_status='pending'
    )
    db.session.add(new_project)
    db.session.commit()
    
    # 이메일 발송 (EmailService 사용)
    try:
        company = User.query.get(user_id)
        if company and email_service:
            email_service.send_consultant_notification(
                consultant_email=consultant.email,
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

# --- Get available consultants for adding to project ---
@app.route('/api/projects/<string:title>/available-consultants', methods=['GET'])
def get_available_consultants(title):
    """이미 요청되지 않은 컨설턴트 목록 조회"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'message': 'user_id가 필요합니다.'}), 400
    
    # 이미 요청된 컨설턴트 ID 목록
    existing_projects = Project.query.filter_by(company_id=user_id, title=title).all()
    existing_consultant_ids = [p.consultant_id for p in existing_projects]
    
    # 검증된 전체 컨설턴트 중 아직 요청하지 않은 컨설턴트
    available = Consultant.query.filter(
        Consultant.verified == True,
        ~Consultant.id.in_(existing_consultant_ids) if existing_consultant_ids else True
    ).all()
    
    results = []
    for c in available:
        results.append({
            'id': c.id,
            'name': c.name,
            'specialty': c.specialty,
            'rating': c.rating,
            'experience': c.experience,
            'verified': c.verified
        })
    
    return jsonify(results)

# --- Admin Endpoints ---
@app.route('/api/admin/jobs', methods=['GET'])
def get_admin_jobs():
    jobs = AnalysisJob.query.order_by(AnalysisJob.created_at.desc()).all()
    results = []
    for job in jobs:
        results.append({
            'id': job.id,
            'company_name': job.company_name,
            'url': job.url,
            'status': job.status,
            'created_at': job.created_at.isoformat(),
            'result': job.get_result()
        })
    return jsonify(results)

# --- Consultant Admin Endpoints ---
@app.route('/api/admin/consultants/<int:consultant_id>/approve', methods=['POST'])
def approve_consultant(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)
    consultant.verified = True
    consultant.trust_score = max(consultant.trust_score or 50, 70)  # Bump trust score on verification
    db.session.commit()
    return jsonify({'message': 'Consultant approved successfully', 'verified': True})

@app.route('/api/admin/consultants/<int:consultant_id>/reject', methods=['POST'])
def reject_consultant(consultant_id):
    data = request.json
    reason = data.get('reason', 'No reason provided')
    consultant = Consultant.query.get_or_404(consultant_id)
    # For now, we'll just delete the consultant. In production, you might want to soft-delete or notify.
    db.session.delete(consultant)
    db.session.commit()
    return jsonify({'message': f'Consultant rejected: {reason}'})

@app.route('/api/admin/consultants/<int:consultant_id>/revoke', methods=['POST'])
def revoke_consultant_verification(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)
    consultant.verified = False
    consultant.trust_score = min(consultant.trust_score or 50, 50)  # Lower trust score
    db.session.commit()
    return jsonify({'message': 'Consultant verification revoked', 'verified': False})

# --- Consultant Detail Endpoint ---
@app.route('/api/consultants/<int:consultant_id>', methods=['GET'])
def get_consultant_detail(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)
    
    # Return detailed information
    return jsonify({
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
    })

# --- Quote Request Endpoints ---
@app.route('/api/quotes/request', methods=['POST'])
def request_quotes():
    data = request.json
    consultant_ids = data.get('consultant_ids', [])
    analysis_context = data.get('analysis_context', {})
    
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
    recommended_standards = analysis_context.get('recommended_standards', [])
    if isinstance(recommended_standards, list) and len(recommended_standards) > 0:
        # Extract ISO codes from recommended standards
        iso_codes = []
        for std in recommended_standards:
            if isinstance(std, dict):
                iso_codes.append(std.get('code', ''))
            elif isinstance(std, str):
                iso_codes.append(std)
        iso_text = ', '.join(iso_codes) if iso_codes else 'ISO 인증'
    else:
        iso_text = 'ISO 인증'
    
    project_title = f"{iso_text} 인증 프로젝트"
    
    # Create quote requests and projects for each consultant
    quote_request_id = str(uuid.uuid4())
    created_requests = []
    created_projects = []
    
    for consultant in consultants:
        # Create a project for each consultant
        try:
            new_project = Project(
                company_id=user_id,
                consultant_id=consultant.id,
                title=project_title,
                status='proposal_pending',  # 계약 전 상태
                proposal_status='pending'   # 제안서 대기 중
            )
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
    
    # Commit all projects and milestones
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
    
    # 기업 사용자에게 확인 이메일 발송
    company_user = User.query.get(user_id)
    company_email = company_user.email if company_user else analysis_context.get('contact_email')
    
    if company_email:
        consultant_names = [c.name for c in consultants]
        email_service.send_quote_confirmation_to_company(
            company_email=company_email,
            company_name=company_name,
            consultant_names=consultant_names,
            standards=recommended_standards if isinstance(recommended_standards, list) else []
        )
    
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
@app.route('/sitemap.xml')
def sitemap():
    base_url = "https://www.insightmatch.com"
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

@app.route('/robots.txt')
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Sitemap: https://www.insightmatch.com/sitemap.xml"
    ]
    return Response('\n'.join(lines), mimetype='text/plain')

# --- Initialize Database and Seed Data function
def init_db():
    with app.app_context():
        # Create tables only if they don't exist (don't drop existing data)
        db.create_all()
        print("[OK] Database tables initialized")

def seed_initial_data():
    """Seed initial data only if tables are empty"""
    with app.app_context():
        # Seed Posts (if none exist)
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
            print("[OK] Seeded initial posts")
            
        # Seed Consultants (add if less than 8 exist)
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
                existing_consultant = Consultant.query.filter_by(name=c_data['name']).first()
                if existing_consultant:
                    continue
                
                # Check if user already exists
                email = f"{c_data['name']}@example.com"
                existing_user = User.query.filter_by(email=email).first()
                if existing_user:
                    # Use existing user
                    u = existing_user
                else:
                    # Create dummy user for consultant
                    u = User(email=email, password_hash="dummy", role="consultant", name=c_data['name'])
                    db.session.add(u)
                    db.session.commit()
                
                c = Consultant(user_id=u.id, **c_data)
                db.session.add(c)
            db.session.commit()
            print(f"[OK] Seeded consultants (total: {Consultant.query.count()})")

if __name__ == '__main__':
    init_db()  # Create tables if they don't exist
    seed_initial_data()  # Seed data only if tables are empty
    # Disable reloader to prevent Windows selector issues
    app.run(debug=True, use_reloader=False, port=5000)
