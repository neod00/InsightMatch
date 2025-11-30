import os
import uuid
import json
import datetime
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import os
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
from services import AIService, MatchingService, ProposalService

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
    project.status = 'in_progress'
    db.session.commit()
    return jsonify({'message': 'Contract signed successfully', 'status': project.status})

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

# --- Seed Data function (called manually)
def seed_data():
    with app.app_context():
        # Reset DB to ensure schema changes are applied
        db.drop_all()
        db.create_all()
        
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
            
        # Seed Consultants (Mock Data)
        if Consultant.query.count() == 0:
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
                }
            ]
            for c_data in consultants:
                # Create dummy user for consultant
                u = User(email=f"{c_data['name']}@example.com", password_hash="dummy", role="consultant", name=c_data['name'])
                db.session.add(u)
                db.session.commit()
                
                c = Consultant(user_id=u.id, **c_data)
                db.session.add(c)
            db.session.commit()

if __name__ == '__main__':
    seed_data()
    # Disable reloader to prevent Windows selector issues
    app.run(debug=True, use_reloader=False, port=5000)
