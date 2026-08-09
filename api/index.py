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
import hashlib
import secrets
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file, Response, g, stream_with_context
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import jwt
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, AnalysisJob, AdminActionLog, Consultant, User, Project, Milestone, Post, Company, Notification, Message, ProfileChangeLog, PasswordResetToken, ManualGeneration, RateLimitEntry
from services import AIService, MatchingService, ProposalService, EmailService, AdvancedDiagnosticService
from services.iso_manual_service import generate_iso_manual_stream

# Load environment variables
# Load from project root directory
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)
print(f"Loading .env from: {env_path}")
print(f"GOOGLE_API_KEY exists: {os.environ.get('GOOGLE_API_KEY') is not None}")
print(f"DATA_GO_KR_API_KEY exists: {os.environ.get('DATA_GO_KR_API_KEY') is not None}")

# Configure Flask
app = Flask(__name__)
# 1. 파일 업로드 허용 용량을 50MB로 대폭 늘립니다.
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
CORS(app)

# 2. 용량 초과 시 HTML 대신 JSON 에러를 반환하도록 설정합니다.
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({
        'error': '파일 용량이 너무 큽니다. (최대 50MB까지 허용)',
        'message': '제안서 파일 크기를 줄이거나 50MB 이하의 파일을 선택해주세요.'
    }), 413

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
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    import warnings
    warnings.warn('SECRET_KEY 환경변수가 설정되지 않았습니다. 프로덕션에서는 반드시 설정해주세요.', stacklevel=1)
    app.config['SECRET_KEY'] = 'dev-only-insecure-key-' + os.urandom(8).hex()

db.init_app(app)

# Initialize Services
ai_service = AIService()
matching_service = MatchingService()
proposal_service = ProposalService()
email_service = EmailService()
diagnostic_service = AdvancedDiagnosticService()

# Create tables on first request
@app.before_request
def create_tables():
    if not hasattr(app, '_tables_created'):
        db.create_all()
        app._tables_created = True

# --- Helper: JWT Authentication Decorators ---
def token_required(f):
    """JWT 토큰 필수 검증 데코레이터"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1]
        
        if not token:
            return jsonify({'message': '로그인이 필요합니다.'}), 401
        
        try:
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = User.query.get(payload['user_id'])
            if not current_user:
                return jsonify({'message': '유효하지 않은 사용자입니다.'}), 401
            g.current_user = current_user
        except jwt.ExpiredSignatureError:
            return jsonify({'message': '세션이 만료되었습니다. 다시 로그인해주세요.'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': '유효하지 않은 인증 토큰입니다.'}), 401
        
        return f(*args, **kwargs)
    return decorated

def token_optional(f):
    """JWT 토큰 선택적 검증 (비로그인도 허용)"""
    @wraps(f)
    def decorated(*args, **kwargs):
        g.current_user = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1]
            try:
                payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
                g.current_user = User.query.get(payload['user_id'])
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                pass
        return f(*args, **kwargs)
    return decorated

# 회원가입으로 생성 가능한 역할 화이트리스트.
# 'admin'은 절대 포함하지 않는다 — 관리자 계정은 DB에서 수동 부여만 허용.
ALLOWED_SIGNUP_ROLES = {'company', 'consultant'}

ALLOWED_MANUAL_ISO_STANDARDS = {'ISO 9001:2015', 'ISO 14001:2015', 'ISO 45001:2018'}
ALLOWED_MANUAL_ISSUES = {
    'quality_defect',
    'customer_complaint',
    'supplier_quality',
    'process_inefficiency',
    'safety_incident',
    'env_regulation',
    'energy_cost',
    'work_condition',
}
MANUAL_TOKEN_TTL_MINUTES = 30
MAX_MANUAL_MARKDOWN_CHARS = 250000
DAILY_MANUAL_LIMIT = 1  # 기업 사용자당 하루 매뉴얼 생성 세션 상한 (AI API 비용 남용 방지). 관리자는 면제.


def require_company_user():
    user = getattr(g, 'current_user', None)
    if not user or user.role != 'company':
        return jsonify({'message': '기업 사용자만 이용할 수 있습니다.'}), 403
    return None


def require_manual_user():
    """매뉴얼 생성기 접근 권한: 기업 회원 또는 관리자(관리자는 하루 한도 면제)."""
    user = getattr(g, 'current_user', None)
    if not user or user.role not in ('company', 'admin'):
        return jsonify({'message': '기업 사용자만 이용할 수 있습니다.'}), 403
    return None


def _client_ip():
    """프록시(Vercel) 뒤의 실제 클라이언트 IP."""
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()[:64]
    return (request.remote_addr or 'unknown')[:64]


def check_rate_limit(scope, limit, window_minutes):
    """IP 기준 호출량 제한. 허용되면 True, 초과면 False.

    무인증 공개 엔드포인트가 무제한 호출되어 DB가 오염되거나 자원이
    고갈되는 것을 막는다. 실패 시(예: 테이블 미생성) 서비스는 계속 동작한다.
    """
    try:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        cutoff = now - datetime.timedelta(minutes=window_minutes)
        key = f"{scope}:{_client_ip()}"

        # 오래된 기록 정리 (24시간 초과분)
        RateLimitEntry.query.filter(
            RateLimitEntry.created_at < now - datetime.timedelta(hours=24)
        ).delete(synchronize_session=False)

        recent = RateLimitEntry.query.filter(
            RateLimitEntry.key == key,
            RateLimitEntry.created_at >= cutoff,
        ).count()

        if recent >= limit:
            db.session.commit()
            return False

        db.session.add(RateLimitEntry(key=key, created_at=now))
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"[RateLimit] check failed (fail-open): {e}")
        return True


def _manual_token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _clean_text(value, max_length, required=False):
    text = (value or '').strip()
    if required and not text:
        return None, '필수 입력값이 누락되었습니다.'
    if len(text) > max_length:
        return None, f'{max_length}자 이하로 입력해주세요.'
    return text, None


def validate_manual_form_data(data):
    errors = {}
    company_name, err = _clean_text(data.get('company_name'), 100, True)
    if err:
        errors['company_name'] = err
    industry, err = _clean_text(data.get('industry'), 100, True)
    if err:
        errors['industry'] = err
    main_product, err = _clean_text(data.get('main_product'), 200, True)
    if err:
        errors['main_product'] = err
    employees, err = _clean_text(data.get('employees'), 50, True)
    if err:
        errors['employees'] = err
    custom_issue, err = _clean_text(data.get('custom_issue'), 2000, False)
    if err:
        errors['custom_issue'] = err

    target_iso = (data.get('target_iso') or 'ISO 9001:2015').strip()
    if target_iso not in ALLOWED_MANUAL_ISO_STANDARDS:
        errors['target_iso'] = '지원하지 않는 ISO 규격입니다.'

    reasons = data.get('reasons') or []
    if not isinstance(reasons, list):
        errors['reasons'] = '인증 목적 형식이 올바르지 않습니다.'
        reasons = []
    reasons = [str(reason).strip()[:120] for reason in reasons[:8] if str(reason).strip()]

    issues = data.get('issues') or []
    if not isinstance(issues, list):
        errors['issues'] = '경영 이슈 형식이 올바르지 않습니다.'
        issues = []
    clean_issues = []
    for issue in issues[:8]:
        issue_id = issue.get('id') if isinstance(issue, dict) else issue
        issue_id = str(issue_id).strip()
        if issue_id in ALLOWED_MANUAL_ISSUES:
            clean_issues.append({'id': issue_id})

    if errors:
        return None, errors

    return {
        'company_name': company_name,
        'industry': industry,
        'main_product': main_product,
        'employees': employees,
        'target_iso': target_iso,
        'reasons': reasons,
        'issues': clean_issues,
        'custom_issue': custom_issue,
        'cert_status': 'None',
        'timeline': 'flexible',
    }, None


def get_manual_generation_from_stream_token(manual_id, stream_token):
    if not manual_id or not stream_token:
        return None, '매뉴얼 생성 세션이 없습니다.'
    manual = ManualGeneration.query.get(manual_id)
    if not manual:
        return None, '매뉴얼 생성 세션을 찾을 수 없습니다.'
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = manual.token_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if expires_at and expires_at < now:
        return None, '매뉴얼 생성 세션이 만료되었습니다. 다시 생성해주세요.'
    if manual.token_hash != _manual_token_hash(stream_token):
        return None, '매뉴얼 생성 세션이 유효하지 않습니다.'
    return manual, None

def require_admin_request():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({'message': 'Admin authentication required'}), 401

    token = auth_header.split(' ', 1)[1]
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        current_user = User.query.get(payload['user_id'])
    except jwt.ExpiredSignatureError:
        return jsonify({'message': 'Session expired'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'message': 'Invalid token'}), 401

    if not current_user:
        return jsonify({'message': 'Invalid user'}), 401
    if current_user.role != 'admin':
        return jsonify({'message': 'Admin role required'}), 403

    g.current_user = current_user
    return None

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        forbidden = require_admin_request()
        if forbidden:
            return forbidden
        return f(*args, **kwargs)
    return decorated

def log_admin_action(action, target_type, target_id, details=None):
    admin = getattr(g, 'current_user', None)
    if not admin:
        return
    db.session.add(AdminActionLog(
        admin_user_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=json.dumps(details or {})
    ))

APPROVAL_CHECKLIST_KEYS = [
    'identity_verified',
    'iso_credentials_verified',
    'project_history_verified',
    'contact_verified',
]

def validate_approval_checklist(data):
    checklist = data.get('checklist') if isinstance(data, dict) else None
    if not isinstance(checklist, dict):
        return None, 'Approval checklist is required.'
    missing = [key for key in APPROVAL_CHECKLIST_KEYS if checklist.get(key) is not True]
    if missing:
        return None, f'Approval checklist is incomplete: {", ".join(missing)}'
    return {key: True for key in APPROVAL_CHECKLIST_KEYS}, None

def get_required_reason(data, field='reason', max_length=500):
    reason = ((data or {}).get(field) or '').strip()
    if not reason:
        return None, 'Reason is required.'
    if len(reason) > max_length:
        return None, 'Reason is too long.'
    return reason, None

def notify_consultant_review_result(consultant, notification_type, title, message):
    if consultant and consultant.user_id:
        db.session.add(Notification(
            user_id=consultant.user_id,
            type=notification_type,
            title=title,
            message=message,
            link='/dashboard.html'
        ))

def _same_id(left, right):
    """Compare database ids robustly across int/string legacy values."""
    return left is not None and right is not None and str(left) == str(right)

def get_project_consultant_user_id(project):
    consultant = Consultant.query.get(project.consultant_id) if project.consultant_id else None
    return consultant.user_id if consultant else None

def is_project_company(project, user=None):
    user = user or getattr(g, 'current_user', None)
    return bool(user and _same_id(user.id, project.company_id))

def is_project_consultant(project, user=None):
    user = user or getattr(g, 'current_user', None)
    return bool(user and _same_id(user.id, get_project_consultant_user_id(project)))

def is_project_participant(project, user=None):
    return is_project_company(project, user) or is_project_consultant(project, user)

def require_project_participant(project):
    if not is_project_participant(project):
        return jsonify({'message': '해당 프로젝트에 접근할 권한이 없습니다.'}), 403
    return None

def get_supabase_url():
    return os.environ.get('SUPABASE_URL', '').strip().rstrip('/')

def is_allowed_proposal_file_url(file_url):
    if not file_url:
        return True

    supabase_url = get_supabase_url()
    if not supabase_url:
        return False

    parsed_file_url = urlparse(file_url)
    parsed_supabase_url = urlparse(supabase_url)
    return (
        parsed_file_url.scheme == 'https'
        and parsed_file_url.netloc == parsed_supabase_url.netloc
        and parsed_file_url.path.startswith('/storage/v1/object/public/proposals/')
    )

def is_allowed_profile_image_url(file_url):
    if not file_url:
        return True

    supabase_url = get_supabase_url()
    if not supabase_url:
        return False

    parsed_file_url = urlparse(file_url)
    parsed_supabase_url = urlparse(supabase_url)
    return (
        parsed_file_url.scheme == 'https'
        and parsed_file_url.netloc == parsed_supabase_url.netloc
        and parsed_file_url.path.startswith('/storage/v1/object/public/profiles/')
    )

def parse_positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

def is_consultant_owner_or_admin(consultant, user=None):
    user = user or getattr(g, 'current_user', None)
    return bool(user and (user.role == 'admin' or _same_id(user.id, consultant.user_id)))

def consultant_public_dict(consultant):
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
        'orgSizeExperience': json.loads(consultant.org_size_experience) if consultant.org_size_experience else [],
        'roles': json.loads(consultant.roles) if consultant.roles else [],
        'profileImageUrl': consultant.profile_image_url,
        'bio': consultant.bio,
        'introductionVideoUrl': consultant.introduction_video_url,
        'companyName': consultant.company_name,
        'status': 'verified',
    }

def mark_other_session_projects_not_selected(project):
    """Mark non-selected candidates only after the chosen project is contracted."""
    if not project.session_id:
        return

    other_projects = Project.query.filter(
        Project.session_id == project.session_id,
        Project.id != project.id,
        Project.status.notin_(['not_selected', 'cancelled_by_company', 'contracted', 'in_progress', 'completed'])
    ).all()

    for other in other_projects:
        other.status = 'not_selected'
        if other.consultant_id:
            consultant = Consultant.query.get(other.consultant_id)
            if consultant and consultant.user_id:
                notification = Notification(
                    user_id=consultant.user_id,
                    type='not_selected',
                    title='프로젝트 미선정 안내',
                    message=f'"{other.title}" 프로젝트에서 다른 전문가가 최종 선정되었습니다.',
                    link='/dashboard.html'
                )
                db.session.add(notification)

# --- Helper: Email Validation ---
def is_valid_email(email):
    """Unicode-aware email validation (RFC 5321 compliant with IDN support)"""
    if not email:
        return False
    pattern = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
    return re.match(pattern, email) is not None

def register_consultant_validated():
    if g.current_user.role != 'consultant':
        return jsonify({'message': 'Only consultant accounts can register a consultant profile.'}), 403

    user_id = g.current_user.id
    existing = Consultant.query.filter_by(user_id=user_id).first()
    if existing:
        return jsonify({'message': 'Consultant profile already exists.', 'consultant_id': existing.id}), 409

    data = request.json or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    company_name = (data.get('company_name') or '').strip()
    specialty = (data.get('specialty') or '').strip()
    match_reason = (data.get('match_reason') or '').strip()
    regions = (data.get('regions') or '').strip()
    profile_image_url = (data.get('profile_image_url') or '').strip()
    experience_years = parse_positive_int(data.get('experience'))

    if not name:
        return jsonify({'message': 'Name is required.'}), 400
    if len(name) > 100:
        return jsonify({'message': 'Name is too long.'}), 400
    if not email or not is_valid_email(email):
        return jsonify({'message': 'Valid email is required.'}), 400
    if len(email) > 120:
        return jsonify({'message': 'Email is too long.'}), 400
    if not phone or len(phone) > 30:
        return jsonify({'message': 'Valid phone number is required.'}), 400
    if experience_years is None or experience_years > 50:
        return jsonify({'message': 'Experience must be between 1 and 50 years.'}), 400
    if len(company_name) > 100:
        return jsonify({'message': 'Company name is too long.'}), 400
    if len(specialty) > 100:
        return jsonify({'message': 'Specialty is too long.'}), 400
    if not match_reason or len(match_reason) > 500:
        return jsonify({'message': 'Introduction is required and must be 500 characters or fewer.'}), 400
    if not regions or len(regions) > 200:
        return jsonify({'message': 'At least one service region is required.'}), 400
    if not is_allowed_profile_image_url(profile_image_url):
        return jsonify({'message': 'Invalid profile image URL'}), 400

    iso_exp = data.get('iso_experience', {})
    if not isinstance(iso_exp, dict) or not iso_exp:
        return jsonify({'message': 'At least one ISO standard is required.'}), 400
    if len(iso_exp) > 50:
        return jsonify({'message': 'Too many ISO standards selected.'}), 400

    industry_exp = data.get('industry_experience', [])
    if not isinstance(industry_exp, list) or not industry_exp:
        return jsonify({'message': 'At least one industry experience is required.'}), 400
    if len(industry_exp) > 30:
        return jsonify({'message': 'Too many industry experiences selected.'}), 400

    detailed_certs = data.get('detailed_certifications', '')
    if isinstance(detailed_certs, (list, dict)):
        detailed_certs = json.dumps(detailed_certs)
    elif detailed_certs is None:
        detailed_certs = ''
    else:
        detailed_certs = str(detailed_certs).strip()
    if len(detailed_certs) > 2000:
        return jsonify({'message': 'Certification details are too long.'}), 400

    recent_projects = (data.get('recent_projects') or '').strip()
    if not recent_projects or len(recent_projects) > 3000:
        return jsonify({'message': 'Recent project history is required and must be 3000 characters or fewer.'}), 400

    new_consultant = Consultant(
        user_id=user_id,
        name=name,
        avatar=((data.get('avatar') or name[0]) if name else 'N')[:10],
        specialty=specialty,
        experience=f'{experience_years} years',
        rating=5.0,
        reviews=0,
        match_reason=match_reason,
        regions=regions,
        phone=phone,
        email=email,
        company_name=company_name,
        certifications=data.get('certifications'),
        iso_experience=json.dumps(iso_exp),
        industry_experience=json.dumps(industry_exp),
        project_types=json.dumps(data.get('project_types', [])),
        org_size_experience=json.dumps(data.get('org_size_experience', [])),
        roles=json.dumps(data.get('roles', [])),
        detailed_certifications=detailed_certs,
        recent_projects=recent_projects,
        profile_image_url=profile_image_url,
        verified=False,
        trust_score=50.0,
        status='pending',
    )
    db.session.add(new_consultant)
    db.session.commit()

    user = User.query.get(user_id)
    if user and not user.company_name and company_name:
        user.company_name = company_name
    if user and not user.phone and phone:
        user.phone = phone
    db.session.commit()

    return jsonify({
        'message': 'Consultant registration submitted for admin review.',
        'consultant_id': new_consultant.id,
    }), 201

# --- Auth Endpoints ---
@app.route('/api/auth/signup', methods=['POST'])
def signup():
    data = request.json
    email = data.get('email', '').strip().lower()  # BUG-004 Fix: 이메일 정규화
    password = data.get('password')
    name = data.get('name')  # 이름 (담당자명/컨설턴트명)
    company_name = data.get('company_name', '').strip()  # 회사명
    role = data.get('role', 'company')

    # 권한 상승 방지: 가입 시 지정 가능한 역할을 화이트리스트로 제한.
    # (이전에는 role을 그대로 신뢰해 role='admin'으로 관리자 계정 생성이 가능했음)
    if role not in ALLOWED_SIGNUP_ROLES:
        return jsonify({'message': '유효하지 않은 회원 유형입니다.'}), 400

    # Name validation
    if not name or not name.strip():
        return jsonify({'message': '이름을 입력해 주세요.'}), 400
    
    # Company name validation for company users
    if role == 'company' and not company_name:
        return jsonify({'message': '회사명을 입력해 주세요.'}), 400
    
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
        name=name.strip(),
        company_name=company_name,
        role=role,
        phone=phone
    )
    db.session.add(new_user)
    db.session.commit()
    
    if role == 'company':
        industry = data.get('industry', 'Unknown')
        employees = data.get('employees', '')
        new_company = Company(user_id=new_user.id, name=company_name, industry=industry, employees=employees)
        db.session.add(new_company)
        db.session.commit()
    elif role == 'consultant':
        new_consultant = Consultant(
            user_id=new_user.id,
            name=name.strip(),
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
    email = data.get('email', '').strip().lower()  # BUG-004 Fix: 이메일 정규화
    password = data.get('password')
    
    user = User.query.filter_by(email=email).first()
    
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'message': 'Invalid credentials'}), 401
        
    token = jwt.encode({
        'user_id': user.id,
        'role': user.role,
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    }, app.config['SECRET_KEY'], algorithm="HS256")
    
    # Company 정보 조회
    company = Company.query.filter_by(user_id=user.id).first()
    
    return jsonify({
        'token': token,
        'user': {
            'id': user.id,
            'name': user.name,
            'email': user.email,
            'role': user.role,
            'company_name': user.company_name or '',
            'industry': company.industry if company else '',
            'employees': company.employees if company else ''
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
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
        
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
    if reset_token.expires_at < datetime.datetime.now(datetime.timezone.utc):
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
# 보안: 이 계열은 외부 URL 수집 + 유료 AI 호출을 유발하므로 인증 필수.
# (이전에는 무인증이라 누구나 무제한으로 AI 비용을 발생시키고 SSRF를 유발할 수 있었음)
@app.route('/api/analyze', methods=['POST'])
@token_required
def start_analysis():
    data = request.json or {}
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
@token_required
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

# ============================================================
# Advanced Diagnostic Engine Endpoints (정밀 진단 엔진 API)
# ============================================================

@app.route('/api/diagnostic/industries', methods=['GET'])
def get_diagnostic_industries():
    """사용 가능한 산업코드(KSIC) 목록 반환"""
    try:
        industries = diagnostic_service.get_available_industries()
        return jsonify({'industries': industries})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/diagnostic/questions/<ksic_code>', methods=['GET'])
def get_diagnostic_questions(ksic_code):
    """특정 업종의 자기진단 질문지 반환 (공정별 필터링 지원)"""
    try:
        main_process = request.args.get('process', None)
        questions = diagnostic_service.get_diagnostic_questions(ksic_code, main_process)
        return jsonify(questions)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/diagnostic/report', methods=['POST'])
@token_required
def generate_diagnostic_report():
    """자기진단 응답을 기반으로 Gap Analysis 리포트 생성

    보안: 유료 AI(Gemini) 호출을 유발하므로 인증 필수.
    """
    try:
        data = request.json or {}
        ksic_code = data.get('industry_code')
        user_answers = data.get('answers', [])
        user_context = data.get('context', {})
        
        if not ksic_code:
            return jsonify({'error': '업종 코드(industry_code)가 필요합니다.'}), 400
        if not user_answers:
            return jsonify({'error': '진단 응답(answers)이 필요합니다.'}), 400
        
        full_report = data.get('full_report', False)
        
        report = diagnostic_service.generate_gap_report(
            ksic_code=ksic_code,
            user_answers=user_answers,
            user_context=user_context,
            full_report=full_report
        )
        
        # DB에 진단 결과 저장 (리드 추적용)
        try:
            job_id = str(uuid.uuid4())
            new_job = AnalysisJob(
                id=job_id,
                company_name=user_context.get('company_name', '미입력 (자기진단)'),
                url='',
                status='completed'
            )
            new_job.set_intake_data(data)
            new_job.set_result(report)
            db.session.add(new_job)
            db.session.commit()
            report['session_id'] = job_id
        except Exception as db_err:
            print(f'[Diagnostic] DB save error (non-critical): {db_err}')
            db.session.rollback()
        
        return jsonify(report)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        import traceback
        print(f'[Diagnostic] Report generation error: {e}')
        print(traceback.format_exc())
        return jsonify({'error': f'리포트 생성 중 오류: {str(e)}'}), 500


@app.route('/api/iso-manual/session', methods=['POST'])
@token_required
def create_iso_manual_session():
    """Create a short-lived streaming session without exposing the JWT in the SSE URL."""
    role_error = require_manual_user()
    if role_error:
        return role_error

    form_data, errors = validate_manual_form_data(request.json or {})
    if errors:
        return jsonify({'message': '입력값을 확인해주세요.', 'errors': errors}), 400

    # 비용 남용 방지: 기업 사용자당 하루 생성 세션 수 제한 (관리자는 면제).
    # created_at 은 UTC 기준으로 저장되므로 naive UTC 자정을 경계로 사용한다.
    if g.current_user.role != 'admin':
        now = datetime.datetime.now(datetime.timezone.utc)
        today_start = datetime.datetime(now.year, now.month, now.day)
        todays_count = ManualGeneration.query.filter(
            ManualGeneration.user_id == g.current_user.id,
            ManualGeneration.created_at >= today_start,
        ).count()
        if todays_count >= DAILY_MANUAL_LIMIT:
            return jsonify({
                'message': f'하루 매뉴얼 생성 한도({DAILY_MANUAL_LIMIT}건)를 초과했습니다. 내일 다시 이용해주세요.',
                'code': 'DAILY_LIMIT_EXCEEDED',
            }), 429

    raw_token = secrets.token_urlsafe(32)
    manual = ManualGeneration(
        id=str(uuid.uuid4()),
        user_id=g.current_user.id,
        token_hash=_manual_token_hash(raw_token),
        token_expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=MANUAL_TOKEN_TTL_MINUTES),
        status='created',
    )
    manual.set_form_data(form_data)
    db.session.add(manual)
    db.session.commit()

    return jsonify({
        'manual_id': manual.id,
        'stream_token': raw_token,
        'expires_in_seconds': MANUAL_TOKEN_TTL_MINUTES * 60,
    }), 201


# --- ISO Manual Generation SSE Endpoint ---
@app.route('/api/generate-iso', methods=['GET'])
def generate_iso_manual():
    """
    AI ISO 시스템 매뉴얼/절차서를 SSE 스트리밍으로 생성.
    실제 JWT 대신 /api/iso-manual/session에서 발급한 짧은 수명의 stream_token만 받습니다.
    """
    manual, token_error = get_manual_generation_from_stream_token(
        request.args.get('manual_id', ''),
        request.args.get('stream_token', ''),
    )
    if token_error:
        def session_error():
            yield f"data: [ERROR] {token_error}\n\n"
            yield "data: [DONE]\n\n"
        return Response(session_error(), mimetype='text/event-stream',
                       headers={'Cache-Control': 'no-cache', 'Access-Control-Allow-Origin': '*'})

    form_data = manual.get_form_data()
    continue_from = request.args.get('continue_from', '').strip()
    phase = 1
    if continue_from:
        try:
            continue_from_int = int(continue_from)
        except ValueError:
            def invalid_phase_error():
                yield "data: [ERROR] 이어쓰기 단계가 올바르지 않습니다.\n\n"
                yield "data: [DONE]\n\n"
            return Response(invalid_phase_error(), mimetype='text/event-stream',
                           headers={'Cache-Control': 'no-cache', 'Access-Control-Allow-Origin': '*'})
        if continue_from_int not in (7, 9):
            def unsupported_phase_error():
                yield "data: [ERROR] 지원하지 않는 이어쓰기 단계입니다.\n\n"
                yield "data: [DONE]\n\n"
            return Response(unsupported_phase_error(), mimetype='text/event-stream',
                           headers={'Cache-Control': 'no-cache', 'Access-Control-Allow-Origin': '*'})
        form_data['continue_from'] = continue_from_int
        form_data['max_sections'] = None
        if continue_from_int == 7:
            form_data['previous_markdown'] = manual.phase1_markdown or ''
        else:
            form_data['previous_markdown'] = '\n\n'.join(
                part for part in [manual.phase1_markdown, manual.phase2_markdown]
                if part
            )
        phase = 3 if continue_from_int >= 9 else 2
    else:
        form_data['max_sections'] = 5

    # 비용 가드: 이미 완료된 phase는 LLM 재호출 없이 저장본을 그대로 재전송한다.
    # (새로고침·토큰 재사용으로 같은 phase를 반복 생성해 비용이 새는 것을 차단)
    existing_markdown = {
        1: manual.phase1_markdown,
        2: manual.phase2_markdown,
        3: manual.phase3_markdown,
    }.get(phase)
    if existing_markdown:
        def replay_stream():
            escaped = existing_markdown.replace('\n', '\\n').replace('\r', '')
            yield f"data: {escaped}\n\n"
            yield f"data: [PHASE_COMPLETE:{phase}]\n\n"
            yield "data: [DONE]\n\n"
        return Response(
            stream_with_context(replay_stream()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
                'Access-Control-Allow-Origin': '*',
            }
        )

    def event_stream():
        generated_parts = []
        finalized = False  # 성공/실패 상태를 이미 확정·커밋했는가

        def _finalize(success):
            # 성공 시에만 phase_N_markdown 저장 + '완료' 상태로 전이.
            # 오류·절단·빈 응답은 '실패'로 기록하고 부분 결과를 저장하지 않는다
            # (이어쓰기 문맥 오염과 '반쪽 완성본' 노출 방지).
            nonlocal finalized
            if finalized:
                return
            if success:
                text = ''.join(generated_parts).strip()
                if text:
                    if phase == 1:
                        manual.phase1_markdown = text
                    elif phase == 2:
                        manual.phase2_markdown = text
                    else:
                        manual.phase3_markdown = text
                    manual.status = f'phase_{phase}_completed'
                else:
                    manual.status = f'phase_{phase}_failed'
            else:
                manual.status = f'phase_{phase}_failed'
            manual.updated_at = datetime.datetime.now(datetime.timezone.utc)
            db.session.commit()
            finalized = True

        manual.status = f'generating_phase_{phase}'
        manual.updated_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()

        try:
            for chunk in generate_iso_manual_stream(form_data):
                if chunk.startswith('data: [PHASE_COMPLETE'):
                    # 서버리스(Vercel)에서는 스트림 종료 후(post-loop) 코드가
                    # 실행되지 않을 수 있으므로, 성공 신호를 받는 즉시
                    # (스트림이 살아있을 때) 저장을 확정한다.
                    _finalize(success=True)
                elif chunk.startswith('data: [ERROR]'):
                    _finalize(success=False)
                elif chunk.startswith('data: ') and not chunk.startswith('data: ['):
                    text = chunk[6:].strip()
                    generated_parts.append(text.replace('\\n', '\n'))
                yield chunk

            # 성공/실패 신호 없이 끝났으면(비정상 종료) 실패로 마무리
            if not finalized:
                _finalize(success=False)
        except GeneratorExit:
            # 클라이언트 연결 끊김(일시정지·탭 닫기) — 'generating' 상태 고착 방지
            if not finalized:
                try:
                    manual.status = f'phase_{phase}_aborted'
                    manual.updated_at = datetime.datetime.now(datetime.timezone.utc)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            raise
        except Exception as e:
            db.session.rollback()
            print(f"[ISO Manual] stream persistence error: {e}")
            if not finalized:
                try:
                    manual.status = f'phase_{phase}_failed'
                    manual.updated_at = datetime.datetime.now(datetime.timezone.utc)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            yield "data: [ERROR] 매뉴얼 생성 상태 저장 중 오류가 발생했습니다.\n\n"
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
            'Access-Control-Allow-Origin': '*',
        }
    )


# --- ISO Manual Export (PDF / DOCX) ---
@app.route('/api/export-iso', methods=['POST'])
@token_required
def export_iso_manual():
    """
    마크다운 텍스트를 PDF 또는 DOCX로 변환하여 파일로 반환.
    POST body: { markdown, format: "pdf"|"docx", company_name, target_iso }
    """
    try:
        role_error = require_manual_user()
        if role_error:
            return role_error

        data = request.json or {}
        markdown_text = data.get('markdown', '')
        export_format = data.get('format', 'pdf').lower()
        company_name = data.get('company_name', 'ISO매뉴얼')
        target_iso = data.get('target_iso', '')
        manual_id = data.get('manual_id', '')

        if manual_id:
            manual = ManualGeneration.query.get(manual_id)
            if not manual or manual.user_id != g.current_user.id:
                return jsonify({'error': '매뉴얼 생성 이력을 찾을 수 없습니다.'}), 404
            markdown_text = markdown_text or manual.combined_markdown()

        if export_format not in {'pdf', 'docx'}:
            return jsonify({'error': '지원하지 않는 다운로드 형식입니다.'}), 400

        if not isinstance(markdown_text, str) or not markdown_text.strip():
            return jsonify({'error': 'markdown 내용이 없습니다.'}), 400
        if len(markdown_text) > MAX_MANUAL_MARKDOWN_CHARS:
            return jsonify({'error': '문서가 너무 큽니다. 내용을 줄인 뒤 다시 시도해주세요.'}), 413

        from services.document_export_service import markdown_to_pdf, markdown_to_docx

        # Safe filename
        safe_company = re.sub(r'[^\w가-힣\s]', '', company_name).strip() or 'ISO매뉴얼'
        safe_iso = re.sub(r'[:\s]', '_', target_iso)

        if export_format == 'docx':
            buffer = markdown_to_docx(markdown_text, company_name, target_iso)
            filename = f"{safe_company}_{safe_iso}_매뉴얼.docx"
            mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        else:
            buffer = markdown_to_pdf(markdown_text, company_name, target_iso)
            filename = f"{safe_company}_{safe_iso}_매뉴얼.pdf"
            mimetype = 'application/pdf'

        return send_file(
            buffer,
            mimetype=mimetype,
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        print(f"[Export Error] {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'파일 변환 중 오류: {str(e)}'}), 500

# --- Direct Matching Endpoint (Survey-Based, No AI) ---
@app.route('/api/match', methods=['POST'])
def direct_match():
    """
    Direct consultant matching based on survey data.
    No AI analysis - just rule-based matching.

    공개(무인증) 퍼널이므로 인증은 요구하지 않되, IP당 호출량을 제한해
    DB 오염·자원 남용을 방지한다.
    """
    if not check_rate_limit('match', limit=20, window_minutes=60):
        return jsonify({
            'error': '요청이 너무 많습니다. 잠시 후 다시 시도해주세요.',
            'code': 'RATE_LIMITED',
        }), 429

    data = request.json or {}

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
    
    # === NEW: Save as AnalysisJob for Admin Visibility ===
    try:
        session_id = str(uuid.uuid4()) # Create a session ID
        result['session_id'] = session_id
        
        new_job = AnalysisJob(
            id=session_id,
            company_name=company_name,
            url='', # Direct match usually has no URL
            status='completed'
        )
        new_job.set_intake_data(data)
        new_job.set_result(result)
        db.session.add(new_job)
        db.session.commit()
    except Exception as e:
        print(f"[Direct Match] Error saving job to DB: {e}")
        db.session.rollback()
    
    return jsonify(result)

# --- Consultant Endpoints ---
@app.route('/api/consultants', methods=['GET'])
@token_optional
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
            
    if getattr(g, 'current_user', None) and g.current_user.role == 'admin':
        consultants = Consultant.query.all()
        return jsonify([c.to_dict() for c in consultants])
    consultants = Consultant.query.filter(
        (Consultant.verified == True) | (Consultant.status == 'verified')
    ).all()
    return jsonify([consultant_public_dict(c) for c in consultants])

@app.route('/api/consultants/register', methods=['POST'])
@token_required
def register_consultant():
    try:
        return register_consultant_validated()
        # JWT 토큰에서 user_id 추출
        user_id = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
                user_id = payload.get('user_id')
            except jwt.ExpiredSignatureError:
                return jsonify({'message': '세션이 만료되었습니다. 다시 로그인해주세요.'}), 401
            except jwt.InvalidTokenError:
                return jsonify({'message': '유효하지 않은 인증 정보입니다.'}), 401
        
        if not user_id:
            return jsonify({'message': '로그인이 필요합니다.'}), 401
        
        # 중복 등록 체크
        existing = Consultant.query.filter_by(user_id=user_id).first()
        if existing:
            return jsonify({'message': '이미 컨설턴트 프로필이 존재합니다.', 'consultant_id': existing.id}), 409
        
        data = request.json
        
        # detailed_certifications 처리: 문자열이면 그대로, 리스트/딕셔너리면 JSON 직렬화
        detailed_certs = data.get('detailed_certifications', '')
        if isinstance(detailed_certs, (list, dict)):
            detailed_certs = json.dumps(detailed_certs)
        
        # iso_experience 처리
        iso_exp = data.get('iso_experience', {})
        if isinstance(iso_exp, dict):
            iso_exp = json.dumps(iso_exp)
        
        # industry_experience 처리
        industry_exp = data.get('industry_experience', [])
        if isinstance(industry_exp, list):
            industry_exp = json.dumps(industry_exp)
        
        new_consultant = Consultant(
            user_id=user_id,
            name=data.get('name'),
            avatar=data.get('avatar', data.get('name', 'N')[0] if data.get('name') else 'N'),
            specialty=data.get('specialty'),
            experience=f"{data.get('experience')}년",
            rating=5.0,
            reviews=0,
            match_reason=data.get('match_reason'),
            regions=data.get('regions', ''),
            phone=data.get('phone', ''),
            email=data.get('email', ''),
            company_name=data.get('company_name', ''),
            certifications=data.get('certifications'),
            iso_experience=iso_exp,
            industry_experience=industry_exp,
            project_types=json.dumps(data.get('project_types', [])),
            org_size_experience=json.dumps(data.get('org_size_experience', [])),
            roles=json.dumps(data.get('roles', [])),
            detailed_certifications=detailed_certs,
            recent_projects=data.get('recent_projects', ''),
            profile_image_url=data.get('profile_image_url', ''),
            verified=False,
            trust_score=50.0,
            status='pending'
        )
        db.session.add(new_consultant)
        db.session.commit()
        
        # User 테이블에서도 consultant 프로필 연결 정보 업데이트
        user = User.query.get(user_id)
        if user and not user.company_name and data.get('company_name'):
            user.company_name = data.get('company_name')
        if user and not user.phone and data.get('phone'):
            user.phone = data.get('phone')
        db.session.commit()
        
        return jsonify({
            'message': '전문가 등록 신청이 완료되었습니다! 관리자 검토 후 승인됩니다.',
            'consultant_id': new_consultant.id
        }), 201
        
    except Exception as e:
        db.session.rollback()
        import traceback
        print(f"[Consultant Register] Error: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'message': f'등록 처리 중 오류가 발생했습니다: {str(e)}'}), 500

# --- Project Endpoints ---
@app.route('/api/projects', methods=['GET', 'POST'])
@token_required
def handle_projects():
    user_id = str(g.current_user.id)
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
                'session_id': p.session_id,
                'status': p.status,
                'consultant_id': p.consultant_id,
                'consultant_name': consultant_info.name if consultant_info else 'Unknown',
                'profile_image_url': consultant_info.profile_image_url if consultant_info else None,
                'company_id': p.company_id,
                'company_name': company_user.name if company_user else 'Unknown Company',
                'start_date': p.start_date.isoformat() if p.start_date else None,
                'created_at': p.created_at.isoformat() if p.created_at else None,
                # BUG-029 Fix: 불필요한 getattr 제거
                'proposal_price': p.proposal_price,
                'proposal_duration': p.proposal_duration,
                'proposal_message': p.proposal_message,
                'proposal_file_url': p.proposal_file_url,
                'proposal_submitted_at': p.proposal_submitted_at.isoformat() if p.proposal_submitted_at else None,
                'schedule_status': p.schedule_status,
                'cancelled_at': p.cancelled_at.isoformat() if p.cancelled_at else None,
                'cancelled_reason': p.cancelled_reason,
                'milestones': [m.to_dict() for m in p.milestones]
            })
        return jsonify(results)
        
    elif request.method == 'POST':
        data = request.json
        if g.current_user.role != 'company':
            return jsonify({'message': '기업 담당자만 프로젝트를 생성할 수 있습니다.'}), 403
        
        # === Duplicate Prevention ===
        # Check if an ACTIVE project with same company+consultant+title already exists
        active_statuses = ['planning', 'proposal_pending', 'proposal_submitted', 'negotiating', 'pending_contract', 'awaiting_signature', 'contracted', 'in_progress']
        existing = Project.query.filter(
            Project.company_id == g.current_user.id,
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
            company_id=g.current_user.id,
            consultant_id=data.get('consultant_id'),
            title=data.get('title'),
            status='planning',
            start_date=datetime.datetime.now(datetime.timezone.utc)
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
@token_required
def delete_project(project_id):
    """Delete a project (only if not contracted)"""
    project = Project.query.get_or_404(project_id)
    if not is_project_company(project):
        return jsonify({'message': 'Only the project company can delete this project.'}), 403
    
    # Cannot delete contracted projects
    if project.status in ['contracted', 'in_progress', 'completed']:
        return jsonify({'message': '계약된 프로젝트는 삭제할 수 없습니다.'}), 400
    
    # BUG-026 Fix: 관련 데이터도 삭제
    Milestone.query.filter_by(project_id=project_id).delete()
    Message.query.filter_by(project_id=project_id).delete()
    
    # Delete project
    db.session.delete(project)
    db.session.commit()
    
    return jsonify({'message': '프로젝트가 삭제되었습니다.'})

@app.route('/api/projects/<int:project_id>/proposal/download', methods=['GET'])
@token_required
def download_proposal(project_id):
    project = Project.query.get_or_404(project_id)
    forbidden = require_project_participant(project)
    if forbidden:
        return forbidden
    
    # 1. 컨설턴트가 직접 업로드한 원본 파일이 있는 경우 해당 파일로 리다이렉트
    if project.proposal_file_url and is_allowed_proposal_file_url(project.proposal_file_url):
        from flask import redirect
        return redirect(project.proposal_file_url)
    
    # 2. 업로드된 파일이 없는 경우 시스템 요약 제안서 생성
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
@token_required
def sign_contract(project_id):
    """간편 계약 (BUG-018: 표준 계약 경로(/contract/draft + /contract/sign) 사용 권장)"""
    project = Project.query.get_or_404(project_id)
    if not is_project_participant(project):
        return jsonify({'message': 'Only project participants can sign this project.'}), 403
    return jsonify({'message': 'Use /api/projects/<project_id>/contract/sign for two-party signing.'}), 410
    
    # 제안서가 제출되지 않은 경우 계약 불가
    if project.status != 'proposal_submitted':
        return jsonify({'message': '제안서가 제출된 프로젝트만 계약할 수 있습니다.'}), 400
    
    # 상태를 'contracted'로 변경
    project.status = 'contracted'
    project.start_date = datetime.datetime.now(datetime.timezone.utc)
    
    # 계약 후 마일스톤이 없으면 생성
    if not project.milestones:
        defaults = ["Kick-off Meeting", "Gap Analysis", "Documentation", "Internal Audit", "Final Certification"]
        for title in defaults:
            m = Milestone(project_id=project.id, title=title)
            db.session.add(m)
    
    db.session.commit()
    return jsonify({'message': 'Contract signed successfully', 'status': project.status})

# ========================================
# ⑤ 조건 협의 (Negotiation) API
# ========================================

@app.route('/api/projects/<int:project_id>/negotiate', methods=['POST'])
@token_required
def request_negotiation(project_id):
    """기업이 전문가에게 조건 협의 요청"""
    project = Project.query.get_or_404(project_id)
    
    # BUG-015 Fix: 기업(프로젝트 소유자)만 협의 요청 가능
    if not is_project_company(project):
        return jsonify({'message': '해당 프로젝트의 기업만 조건 협의를 요청할 수 있습니다.'}), 403
    
    # 제안서가 제출된 상태 또는 역제안(counter) 상태에서 협의 가능 (BUG-016 Fix)
    negotiation_data_existing = json.loads(project.negotiation_data) if project.negotiation_data else {}
    if project.status == 'negotiating' and negotiation_data_existing.get('counter_price'):
        # 역제안에 대한 기업 응답 — 허용
        pass
    elif project.status != 'proposal_submitted':
        return jsonify({'message': '제안서가 제출된 프로젝트만 조건 협의가 가능합니다.'}), 400
    
    data = request.json
    requested_price = data.get('requested_price')
    requested_duration = data.get('requested_duration')
    message = data.get('message', '')
    
    if not requested_price and not requested_duration:
        return jsonify({'message': '희망 금액 또는 희망 기간을 입력해주세요.'}), 400
    
    # 협의 데이터 저장
    negotiation_data = {
        'original_price': project.proposal_price,
        'original_duration': project.proposal_duration,
        'requested_price': requested_price,
        'requested_duration': requested_duration,
        'company_message': message
    }
    
    project.negotiation_data = json.dumps(negotiation_data)
    project.negotiation_status = 'pending'
    project.negotiation_requested_at = datetime.datetime.now(datetime.timezone.utc)
    project.status = 'negotiating'
    
    db.session.commit()
    
    # 전문가에게 알림 발송
    try:
        consultant = Consultant.query.get(project.consultant_id)
        if consultant and consultant.user_id:
            company_user = User.query.get(project.company_id)
            company_name = company_user.name if company_user else '기업'
            notification = Notification(
                user_id=consultant.user_id,
                type='negotiation_requested',
                title=f'{company_name}에서 조건 협의를 요청했습니다',
                message=f'희망 금액: {int(requested_price):,}원' if requested_price else f'희망 기간: {requested_duration}',
                link='/dashboard.html'
            )
            db.session.add(notification)
            db.session.commit()
    except Exception as e:
        print(f"[Notification] Failed: {e}")
    
    return jsonify({
        'message': '조건 협의 요청이 전송되었습니다.',
        'status': project.status,
        'negotiation_status': project.negotiation_status
    })

@app.route('/api/projects/<int:project_id>/negotiate/respond', methods=['POST'])
@token_required
def respond_negotiation(project_id):
    """전문가가 협의 요청에 응답 (수락/역제안/거절)"""
    project = Project.query.get_or_404(project_id)
    
    # BUG-015 Fix: 컨설턴트만 응답 가능
    consultant = Consultant.query.get(project.consultant_id)
    if not consultant or g.current_user.id != consultant.user_id:
        return jsonify({'message': '해당 프로젝트의 컨설턴트만 협의에 응답할 수 있습니다.'}), 403
    
    if project.status != 'negotiating':
        return jsonify({'message': '협의 중인 프로젝트가 아닙니다.'}), 400
    
    data = request.json
    action = data.get('action')  # accept, counter, reject
    
    if action not in ['accept', 'counter', 'reject']:
        return jsonify({'message': '유효한 응답을 선택해주세요.'}), 400
    
    negotiation_data = json.loads(project.negotiation_data) if project.negotiation_data else {}
    
    if action == 'accept':
        # 요청된 조건으로 제안서 업데이트
        if negotiation_data.get('requested_price'):
            project.proposal_price = int(negotiation_data['requested_price'])
        if negotiation_data.get('requested_duration'):
            project.proposal_duration = negotiation_data['requested_duration']
        
        project.negotiation_status = 'accepted'
        project.status = 'proposal_submitted'  # 다시 제안 완료 상태로
        message = '조건 협의가 수락되었습니다. 최종 선택을 진행해주세요.'
        
    elif action == 'counter':
        # 역제안
        counter_price = parse_positive_int(data.get('counter_price')) if data.get('counter_price') else None
        counter_duration = (data.get('counter_duration') or '').strip()
        counter_message = (data.get('message') or '').strip()
        if counter_price is not None and (counter_price < 100000 or counter_price > 1000000000):
            return jsonify({'message': 'Counter price must be between 100,000 and 1,000,000,000.'}), 400
        if not counter_price and not counter_duration:
            return jsonify({'message': 'Counter price or duration is required.'}), 400
        if len(counter_duration) > 50:
            return jsonify({'message': 'Counter duration is too long.'}), 400
        if len(counter_message) > 1000:
            return jsonify({'message': 'Counter message is too long.'}), 400
        
        negotiation_data['counter_price'] = counter_price
        negotiation_data['counter_duration'] = counter_duration
        negotiation_data['consultant_message'] = counter_message
        
        project.negotiation_data = json.dumps(negotiation_data)
        project.negotiation_status = 'counter'
        message = '역제안이 전송되었습니다.'
        
    else:  # reject
        reject_message = (data.get('message') or '').strip()
        if len(reject_message) > 1000:
            return jsonify({'message': 'Reject message is too long.'}), 400
        negotiation_data['consultant_message'] = reject_message
        project.negotiation_data = json.dumps(negotiation_data)
        project.negotiation_status = 'rejected'
        project.status = 'proposal_submitted'  # 다시 제안 완료 상태로
        message = '조건 협의가 거절되었습니다.'
    
    project.negotiation_responded_at = datetime.datetime.now(datetime.timezone.utc)
    db.session.commit()
    
    # 기업에게 알림 발송
    try:
        company_user = User.query.get(project.company_id)
        consultant = Consultant.query.get(project.consultant_id)
        if company_user and consultant:
            action_text = {'accept': '수락', 'counter': '역제안', 'reject': '거절'}
            notification = Notification(
                user_id=company_user.id,
                type='negotiation_response',
                title=f'{consultant.name}님이 조건 협의에 {action_text[action]}했습니다',
                message=message,
                link='/dashboard.html'
            )
            db.session.add(notification)
            db.session.commit()
    except Exception as e:
        print(f"[Notification] Failed: {e}")
    
    return jsonify({
        'message': message,
        'status': project.status,
        'negotiation_status': project.negotiation_status
    })

# ========================================
# ⑥ 표준 계약서 (Contract) API
# ========================================

@app.route('/api/projects/<int:project_id>/contract/draft', methods=['POST'])
@token_required
def create_contract_draft(project_id):
    """계약서 초안 생성"""
    project = Project.query.get_or_404(project_id)
    if not is_project_company(project):
        return jsonify({'message': '해당 프로젝트의 기업 담당자만 계약 초안을 생성할 수 있습니다.'}), 403
    
    if project.status not in ['proposal_submitted', 'negotiating']:
        return jsonify({'message': '제안서가 확정된 프로젝트만 계약서를 생성할 수 있습니다.'}), 400
    
    data = request.json or {}
    special_terms = data.get('special_terms', '')
    
    project.contract_special_terms = special_terms
    project.status = 'pending_contract'
    
    db.session.commit()
    
    return jsonify({
        'message': '계약서 초안이 생성되었습니다. 내용을 확인하고 서명해주세요.',
        'status': project.status
    })

@app.route('/api/projects/<int:project_id>/contract/sign', methods=['POST'])
@token_required
def sign_contract_step(project_id):
    """계약서 서명 (기업 또는 전문가)"""
    project = Project.query.get_or_404(project_id)
    if not is_project_participant(project):
        return jsonify({'message': '해당 프로젝트의 당사자만 계약서에 서명할 수 있습니다.'}), 403
    
    if project.status not in ['pending_contract', 'awaiting_signature']:
        return jsonify({'message': '계약서가 준비된 프로젝트만 서명할 수 있습니다.'}), 400
    
    signer = 'company' if is_project_company(project) else 'consultant'
    
    now = datetime.datetime.now(datetime.timezone.utc)
    
    if signer == 'company':
        project.company_signed_at = now
    else:
        project.consultant_signed_at = now
    
    # 양측 모두 서명 완료 시
    if project.company_signed_at and project.consultant_signed_at:
        project.status = 'contracted'
        project.start_date = now
        mark_other_session_projects_not_selected(project)
        
        # 마일스톤 생성
        if not project.milestones:
            defaults = ["Kick-off Meeting", "Gap Analysis", "Documentation", "Internal Audit", "Final Certification"]
            for title in defaults:
                m = Milestone(project_id=project.id, title=title)
                db.session.add(m)
        
        message = '양측 서명이 완료되어 계약이 체결되었습니다!'
        
        # BUG-019 Fix: 계약 체결 알림 발송
        try:
            company_user = User.query.get(project.company_id)
            contract_consultant = Consultant.query.get(project.consultant_id)
            if company_user:
                notification = Notification(
                    user_id=company_user.id,
                    type='contract_signed',
                    title='계약이 체결되었습니다!',
                    message=f'"{project.title}" 프로젝트 계약이 완료되었습니다.',
                    link='/dashboard.html'
                )
                db.session.add(notification)
            if contract_consultant and contract_consultant.user_id:
                notification = Notification(
                    user_id=contract_consultant.user_id,
                    type='contract_signed',
                    title='계약이 체결되었습니다!',
                    message=f'"{project.title}" 프로젝트 계약이 완료되었습니다.',
                    link='/dashboard.html'
                )
                db.session.add(notification)
        except Exception as e:
            print(f"[Notification] Contract signed notification failed: {e}")
    else:
        project.status = 'awaiting_signature'
        other_party = '전문가' if signer == 'company' else '기업'
        message = f'서명이 완료되었습니다. {other_party}의 서명을 기다리고 있습니다.'
    
    db.session.commit()
    
    return jsonify({
        'message': message,
        'status': project.status,
        'company_signed': project.company_signed_at is not None,
        'consultant_signed': project.consultant_signed_at is not None
    })

@app.route('/api/projects/<int:project_id>/contract/preview', methods=['GET'])
@token_required
def get_contract_preview(project_id):
    """계약서 미리보기 데이터 반환"""
    project = Project.query.get_or_404(project_id)
    forbidden = require_project_participant(project)
    if forbidden:
        return forbidden
    
    company_user = User.query.get(project.company_id)
    consultant = Consultant.query.get(project.consultant_id)
    
    return jsonify({
        'project_id': project.id,
        'title': project.title,
        'company_name': company_user.name if company_user else '기업',
        'company_company_name': company_user.company_name if company_user else '',
        'consultant_name': consultant.name if consultant else '전문가',
        'consultant_company_name': consultant.company_name if consultant else '',
        'proposal_price': project.proposal_price,
        'proposal_duration': project.proposal_duration,
        'special_terms': project.contract_special_terms,
        'company_signed_at': project.company_signed_at.isoformat() if project.company_signed_at else None,
        'consultant_signed_at': project.consultant_signed_at.isoformat() if project.consultant_signed_at else None,
        'status': project.status
    })

# ========================================
# ② 컨설턴트 직접 견적 체계
# ========================================

@app.route('/api/projects/<int:project_id>/submit-proposal', methods=['POST'])
@token_required
def submit_proposal(project_id):
    """컨설턴트가 제안서(금액, 기간, 메시지, 파일) 제출"""
    project = Project.query.get_or_404(project_id)
    
    # BUG-014 Fix: 해당 프로젝트의 컨설턴트 본인만 제안서 제출 가능
    consultant = Consultant.query.get(project.consultant_id)
    if not consultant or g.current_user.id != consultant.user_id:
        return jsonify({'message': '해당 프로젝트의 컨설턴트만 제안서를 제출할 수 있습니다.'}), 403
    
    # BUG-013 Fix: 이미 제출된 경우에도 '수정' 허용 (계약 전까지)
    if project.status in ['contracted', 'in_progress', 'completed', 'pending_contract', 'awaiting_signature']:
        return jsonify({'message': '이미 계약된 프로젝트입니다.'}), 400
    
    data = request.json
    
    # 필수 필드 검증
    proposal_price = parse_positive_int(data.get('proposal_price'))
    if not proposal_price:
        return jsonify({'message': '제안 금액을 입력해주세요.'}), 400
    
    # 제안 정보 저장
    if proposal_price < 100000 or proposal_price > 1000000000:
        return jsonify({'message': 'Proposal price must be between 100,000 and 1,000,000,000.'}), 400

    proposal_file_url = data.get('proposal_file_url', '')
    if not is_allowed_proposal_file_url(proposal_file_url):
        return jsonify({'message': 'Invalid proposal file URL'}), 400

    proposal_duration = (data.get('proposal_duration') or '').strip()
    proposal_message = (data.get('proposal_message') or '').strip()
    if proposal_duration and len(proposal_duration) > 50:
        return jsonify({'message': 'Proposal duration is too long.'}), 400
    if len(proposal_message) > 2000:
        return jsonify({'message': 'Proposal message is too long.'}), 400

    project.proposal_price = proposal_price
    project.proposal_duration = proposal_duration
    project.proposal_message = proposal_message
    project.proposal_file_url = proposal_file_url
    project.proposal_submitted_at = datetime.datetime.now(datetime.timezone.utc)
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
@token_required
def get_proposal(project_id):
    """특정 프로젝트의 제안서 상세 조회"""
    project = Project.query.get_or_404(project_id)
    forbidden = require_project_participant(project)
    if forbidden:
        return forbidden
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
@token_required
def get_project_detail(project_id):
    """프로젝트 상세 정보 조회 (컨설턴트용)"""
    project = Project.query.get_or_404(project_id)
    forbidden = require_project_participant(project)
    if forbidden:
        return forbidden
    
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
    
    # 2차: session_id로 못 찾으면, company_user의 이메일로 AnalysisJob 검색 (BUG-027 Fix: 성능 개선)
    if not analysis_job and company_user:
        # 이메일로 직접 검색 (LIKE 쿼리로 제한 — 전체 순회 제거)
        if company_user.email:
            # intake_data는 JSON이므로 LIKE로 이메일 검색
            analysis_job = AnalysisJob.query.filter(
                AnalysisJob.deleted_at.is_(None),
                AnalysisJob.intake_data.like(f'%{company_user.email.lower()}%')
            ).order_by(AnalysisJob.created_at.desc()).first()
        
        # 이메일로 못 찾으면 회사명으로 검색
        if not analysis_job and company_user.name:
            analysis_job = AnalysisJob.query.filter(
                AnalysisJob.deleted_at.is_(None),
                AnalysisJob.intake_data.like(f'%{company_user.name}%')
            ).order_by(AnalysisJob.created_at.desc()).first()
    
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
    
    # issues 파싱
    issues = intake_data.get('issues', [])
    issue_names = {
        'quality_defect': '품질 불량', 'customer_complaint': '고객 클레임',
        'process_inefficiency': '프로세스 비효율', 'supplier_quality': '공급업체 품질',
        'safety_incident': '안전사고', 'env_regulation': '환경 규제',
        'energy_cost': '에너지 비용', 'work_condition': '작업환경',
        'esg_demand': 'ESG 요구', 'carbon_report': '탄소 보고',
        'carbon_neutral': '탄소중립', 'esg_disclosure': 'ESG 공시',
        'security_incident': '정보보안', 'privacy_need': '개인정보',
        'cloud_security': '클라우드 보안', 'ai_risk': 'AI 리스크',
        'supply_unstable': '공급망 불안정', 'crisis_response': '위기 대응',
        'compliance_risk': '컴플라이언스', 'corruption_prevent': '부패 방지',
        'turnover': '이직률', 'burnout': '번아웃', 'knowledge_loss': '지식 유실'
    }
    parsed_issues = []
    for issue in issues:
        if isinstance(issue, dict):
            issue_id = issue.get('id', '')
            parsed_issues.append({
                'id': issue_id,
                'name': issue_names.get(issue_id, issue_id),
                'relatedISO': issue.get('relatedISO', [])
            })
        elif isinstance(issue, str):
            parsed_issues.append({'id': issue, 'name': issue_names.get(issue, issue)})
    
    # reasons 파싱
    reasons = intake_data.get('reasons', [])
    reason_names = {
        'regulation': '법/규제 요구', 'customer': '거래처 요구', 'improve': '프로세스 개선',
        'competitive': '경쟁력 강화', 'export': '수출 요건', 'esg': 'ESG 대응', 'other': '기타'
    }
    parsed_reasons = [reason_names.get(r, r) for r in reasons] if isinstance(reasons, list) else []
    
    # timeline 파싱
    timeline = intake_data.get('timeline')
    timeline_map = {'urgent': '긴급 (1개월 이내)', '3months': '3개월 이내', '6months': '6개월 이내', 'flexible': '유연함'}
    timeline_text = timeline_map.get(timeline, timeline) if timeline else None
    
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
        'region': intake_data.get('region'),
        'contact_email': intake_data.get('contactEmail'),
        
        # 인증 요구사항
        'standards': standards,
        'cert_status': intake_data.get('certStatus'),
        'readiness': intake_data.get('readiness'),
        'target_date': intake_data.get('targetDate'),
        'budget': intake_data.get('budget'),
        'timeline': timeline,
        'timeline_text': timeline_text,
        
        # 기업 요청 상세
        'issues': parsed_issues,
        'reasons': parsed_reasons,
        'additional_notes': intake_data.get('additionalNotes'),
        
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
        'schedule_status': project.schedule_status,
        
        # 취소 정보
        'cancelled_at': project.cancelled_at.isoformat() if hasattr(project, 'cancelled_at') and project.cancelled_at else None,
        'cancelled_reason': getattr(project, 'cancelled_reason', None)
    })

# ========================================
# ③ 계약 후 일정 확정 워크플로우
# ========================================

@app.route('/api/projects/<int:project_id>/propose-schedule', methods=['POST'])
@token_required
def propose_schedule(project_id):
    """컨설턴트가 마일스톤별 일정 제안"""
    project = Project.query.get_or_404(project_id)
    
    # BUG-021 Fix: 컨설턴트만 일정 제안 가능
    consultant = Consultant.query.get(project.consultant_id)
    if not consultant or g.current_user.id != consultant.user_id:
        return jsonify({'message': '해당 프로젝트의 컨설턴트만 일정을 제안할 수 있습니다.'}), 403
    
    # 계약된 프로젝트만 일정 제안 가능
    if project.status not in ['contracted', 'in_progress']:
        return jsonify({'message': '계약 완료된 프로젝트만 일정을 제안할 수 있습니다.'}), 400
    
    data = request.json
    schedule = data.get('schedule', [])  # [{milestone_id, proposed_date}, ...]
    
    if not schedule:
        return jsonify({'message': '일정 데이터가 필요합니다.'}), 400
    
    # 마일스톤 일정 업데이트
    today = datetime.datetime.now(datetime.timezone.utc).date()
    previous_date = None
    normalized_schedule = []
    for item in schedule:
        milestone = Milestone.query.get(item.get('milestone_id'))
        if not milestone or milestone.project_id != project_id:
            return jsonify({'message': 'Invalid milestone in schedule.'}), 400
        if not item.get('proposed_date'):
            return jsonify({'message': 'All milestones need a proposed date.'}), 400
        try:
            proposed_date = datetime.datetime.fromisoformat(item['proposed_date'].replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return jsonify({'message': 'Invalid schedule date.'}), 400
        if proposed_date.tzinfo is None:
            proposed_date = proposed_date.replace(tzinfo=datetime.timezone.utc)
        if proposed_date.date() < today:
            return jsonify({'message': 'Schedule dates cannot be in the past.'}), 400
        if previous_date and proposed_date < previous_date:
            return jsonify({'message': 'Milestone dates must be in chronological order.'}), 400
        previous_date = proposed_date
        milestone.due_date = proposed_date
        normalized_schedule.append({
            'milestone_id': milestone.id,
            'title': milestone.title,
            'proposed_date': proposed_date.isoformat()
        })
    
    schedule_history = []
    if project.schedule_data:
        try:
            existing_schedule_data = json.loads(project.schedule_data)
            if isinstance(existing_schedule_data, dict):
                schedule_history = existing_schedule_data.get('history', [])
            elif isinstance(existing_schedule_data, list):
                schedule_history = [{'schedule': existing_schedule_data}]
        except (TypeError, ValueError):
            schedule_history = []
    schedule_history.append({
        'proposed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'schedule': normalized_schedule
    })

    project.schedule_data = json.dumps({
        'current': normalized_schedule,
        'history': schedule_history
    })
    project.schedule_status = 'proposed'
    project.schedule_proposed_at = datetime.datetime.now(datetime.timezone.utc)
    
    db.session.commit()
    
    # BUG-022 Fix: 기업에게 일정 제안 알림
    try:
        company_user = User.query.get(project.company_id)
        if company_user:
            notification = Notification(
                user_id=company_user.id,
                type='schedule_proposed',
                title='컨설턴트가 일정을 제안했습니다',
                message=f'"{project.title}" 프로젝트의 일정을 확인해주세요.',
                link='/dashboard.html'
            )
            db.session.add(notification)
            db.session.commit()
    except Exception as e:
        print(f"[Notification] Schedule proposed notification failed: {e}")
    
    return jsonify({
        'message': '일정이 제안되었습니다. 기업의 확인을 기다려주세요.',
        'schedule_status': project.schedule_status
    })

@app.route('/api/projects/<int:project_id>/confirm-schedule', methods=['POST'])
@token_required
def confirm_schedule(project_id):
    """기업이 제안된 일정 승인"""
    project = Project.query.get_or_404(project_id)
    
    # BUG-021 Fix: 기업(프로젝트 소유자)만 일정 확정 가능
    if not is_project_company(project):
        return jsonify({'message': '해당 프로젝트의 기업만 일정을 확정할 수 있습니다.'}), 403
    
    if project.schedule_status != 'proposed':
        return jsonify({'message': '제안된 일정이 없습니다.'}), 400
    
    project.schedule_status = 'confirmed'
    project.schedule_confirmed_at = datetime.datetime.now(datetime.timezone.utc)
    project.status = 'in_progress'  # 일정 확정 시 프로젝트 시작
    
    db.session.commit()
    
    # BUG-022 Fix: 컨설턴트에게 일정 확정 알림
    try:
        sched_consultant = Consultant.query.get(project.consultant_id)
        if sched_consultant and sched_consultant.user_id:
            notification = Notification(
                user_id=sched_consultant.user_id,
                type='schedule_confirmed',
                title='일정이 확정되었습니다!',
                message=f'"{project.title}" 프로젝트의 일정이 확정되었습니다. 프로젝트가 시작됩니다.',
                link='/dashboard.html'
            )
            db.session.add(notification)
            db.session.commit()
    except Exception as e:
        print(f"[Notification] Schedule confirmed notification failed: {e}")
    
    return jsonify({
        'message': '일정이 확정되었습니다. 프로젝트가 시작됩니다.',
        'schedule_status': project.schedule_status,
        'status': project.status
    })

@app.route('/api/projects/<int:project_id>/reject-schedule', methods=['POST'])
@token_required
def reject_schedule(project_id):
    """기업이 제안된 일정 거절 (재조율 요청)"""
    project = Project.query.get_or_404(project_id)
    
    # BUG-021 Fix: 기업(프로젝트 소유자)만 일정 거절 가능
    if not is_project_company(project):
        return jsonify({'message': '해당 프로젝트의 기업만 일정을 거절할 수 있습니다.'}), 403
    
    if project.schedule_status != 'proposed':
        return jsonify({'message': '제안된 일정이 없습니다.'}), 400
    
    data = request.json
    rejection_reason = data.get('reason', '')
    
    project.schedule_status = 'pending'  # 다시 대기 상태로
    project.schedule_data = None
    
    db.session.commit()
    
    # BUG-022 Fix: 컨설턴트에게 일정 거절 알림 발송
    try:
        sched_consultant = Consultant.query.get(project.consultant_id)
        if sched_consultant and sched_consultant.user_id:
            reason_text = f' (사유: {rejection_reason})' if rejection_reason else ''
            notification = Notification(
                user_id=sched_consultant.user_id,
                type='schedule_rejected',
                title='제안하신 일정이 거절되었습니다',
                message=f'"{project.title}" 프로젝트의 일정 재조율이 요청되었습니다.{reason_text}',
                link='/dashboard.html'
            )
            db.session.add(notification)
            db.session.commit()
    except Exception as e:
        print(f"[Notification] Schedule rejection failed: {e}")
    
    return jsonify({
        'message': '일정 조율을 요청했습니다. 컨설턴트가 새로운 일정을 제안할 것입니다.',
        'schedule_status': project.schedule_status
    })

# --- Cancel Consultant Request ---
@app.route('/api/projects/<int:project_id>/cancel', methods=['POST'])
@token_required
def cancel_consultant_request(project_id):
    """특정 컨설턴트에 대한 요청 취소 (Soft Delete + 알림)"""
    project = Project.query.get_or_404(project_id)
    if not is_project_company(project):
        return jsonify({'message': 'Only the project company can cancel this request.'}), 403
    
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
    project.cancelled_at = datetime.datetime.now(datetime.timezone.utc)
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

@app.route('/api/projects/<int:project_id>/decline', methods=['POST'])
@token_required
def decline_project_request(project_id):
    """Allow the assigned consultant to decline a quote request before contract."""
    project = Project.query.get_or_404(project_id)
    consultant = Consultant.query.get(project.consultant_id) if project.consultant_id else None
    if not consultant or not _same_id(consultant.user_id, g.current_user.id):
        return jsonify({'message': 'Only the assigned consultant can decline this request.'}), 403

    if project.status in ['contracted', 'in_progress', 'completed', 'pending_contract', 'awaiting_signature']:
        return jsonify({'message': 'Contracted or selected requests cannot be declined.'}), 400

    if project.status in ['cancelled_by_company', 'not_selected', 'declined_by_consultant']:
        return jsonify({'message': 'This request is already closed.'}), 400

    data = request.json or {}
    declined_reason = (data.get('reason') or 'Declined by consultant').strip()
    if len(declined_reason) > 500:
        return jsonify({'message': 'Decline reason is too long.'}), 400

    project.status = 'declined_by_consultant'
    project.cancelled_at = datetime.datetime.now(datetime.timezone.utc)
    project.cancelled_reason = declined_reason

    company_user = User.query.get(project.company_id) if project.company_id else None
    if company_user:
        db.session.add(Notification(
            user_id=company_user.id,
            type='request_declined',
            title=f'{consultant.name} declined your quote request',
            message=f'Decline reason: {declined_reason}',
            link='/dashboard.html'
        ))

    db.session.commit()
    return jsonify({
        'message': 'Request declined.',
        'project_id': project.id,
        'status': project.status,
        'declined_at': project.cancelled_at.isoformat() if project.cancelled_at else None
    })

@app.route('/api/projects/groups/cancel', methods=['POST'])
@token_required
def cancel_project_group():
    """Cancel all non-contracted projects in a company quote-request group."""
    if g.current_user.role != 'company':
        return jsonify({'message': 'Only company users can cancel quote request groups.'}), 403

    data = request.json or {}
    session_id = data.get('session_id')
    title = data.get('title')
    cancelled_reason = data.get('reason') or 'Cancelled by company'

    if not session_id and not title:
        return jsonify({'message': 'session_id or title is required.'}), 400

    query = Project.query.filter(Project.company_id == g.current_user.id)
    if session_id:
        query = query.filter(Project.session_id == session_id)
    else:
        query = query.filter(Project.title == title)

    projects = query.all()
    if not projects:
        return jsonify({'message': 'No matching quote request group found.'}), 404

    protected_count = sum(1 for project in projects if project.status in ['contracted', 'in_progress', 'completed'])
    if protected_count:
        return jsonify({
            'message': 'Contracted or active projects cannot be cancelled as a group.',
            'contracted_count': protected_count
        }), 400

    now = datetime.datetime.now(datetime.timezone.utc)
    cancelled_count = 0
    for project in projects:
        if project.status not in ['cancelled_by_company', 'not_selected']:
            project.status = 'cancelled_by_company'
            project.cancelled_at = now
            project.cancelled_reason = cancelled_reason
            cancelled_count += 1

            consultant = Consultant.query.get(project.consultant_id) if project.consultant_id else None
            if consultant and consultant.user_id:
                db.session.add(Notification(
                    user_id=consultant.user_id,
                    type='request_cancelled',
                    title=f'{g.current_user.name} cancelled a quote request',
                    message=f'Cancel reason: {cancelled_reason}',
                    link='/dashboard.html'
                ))

    db.session.commit()
    return jsonify({
        'message': 'Quote request group cancelled.',
        'cancelled_count': cancelled_count,
        'cancelled_at': now.isoformat()
    })

# --- Add Consultant to Existing Quote Request ---
@app.route('/api/projects/add-consultant', methods=['POST'])
@token_required
def add_consultant_to_request():
    """기존 견적 요청 그룹에 컨설턴트 추가"""
    data = request.json
    if g.current_user.role != 'company':
        return jsonify({'message': '기업 담당자만 컨설턴트를 추가할 수 있습니다.'}), 403

    user_id = str(g.current_user.id)  # BUG-001 Fix: JWT에서 추출
    consultant_id = data.get('consultant_id')
    title = data.get('title')  # 기존 프로젝트 제목 사용
    session_id = data.get('session_id') # 세션 ID 추가
    
    if not consultant_id or not title:
        return jsonify({'message': 'consultant_id, title이 필요합니다.'}), 400
    
    # 컨설턴트 확인
    consultant = Consultant.query.get(consultant_id)
    if not consultant:
        return jsonify({'message': '컨설턴트를 찾을 수 없습니다.'}), 404
    
    # BUG-030 Fix: 취소된 프로젝트는 중복으로 간주하지 않음
    existing = Project.query.filter(
        Project.company_id == user_id, 
        Project.consultant_id == consultant_id,
        Project.title == title,
        Project.status.notin_(['cancelled_by_company', 'not_selected'])
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
@token_required
def get_available_consultants(title):
    """이미 요청되지 않은 컨설턴트 목록 조회 (선별 로직 적용)"""
    if g.current_user.role != 'company':
        return jsonify({'message': '기업 담당자만 추가 컨설턴트를 조회할 수 있습니다.'}), 403

    user_id = str(g.current_user.id)
    session_id = request.args.get('session_id')
    
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
@admin_required
def get_admin_jobs():
    """
    Get all matching requests (projects) for admin dashboard.
    Now queries from Project table directly instead of AnalysisJob.
    Groups projects by company to show all consultants matched to each company.
    """
    try:
        # Get all projects ordered by creation date
        projects = Project.query.order_by(Project.created_at.desc()).all()
        
        # Group projects by company_id
        company_projects = {}
        for p in projects:
            company_id = p.company_id
            if company_id not in company_projects:
                company_projects[company_id] = []
            company_projects[company_id].append(p)
        
        results = []
        
        for company_id, project_list in company_projects.items():
            # Get company/user info
            user = User.query.get(company_id) if company_id is not None else None
            company_name = user.company_name or user.name if user else '알 수 없음'
            contact_email = user.email if user else None
            
            # Get earliest project creation date for this company
            earliest_project = min(project_list, key=lambda x: x.created_at if x.created_at else datetime.datetime.max)
            
            # Extract ISO standard from project title (e.g., "ISO 9001:2015 인증 프로젝트" -> "ISO 9001:2015")
            standards = set()
            for p in project_list:
                if p.title:
                    # Extract ISO standard pattern
                    import re
                    iso_match = re.search(r'(ISO[/\s]?\w+[:\s]?\d*)', p.title, re.IGNORECASE)
                    if iso_match:
                        standards.add(iso_match.group(1).strip())
            
            # Build related projects list with consultant info
            # Deduplicate by (consultant_id, ISO standard) - keep most advanced status
            status_priority = {
                'completed': 9,
                'in_progress': 8,
                'contracted': 7,
                'awaiting_signature': 6,
                'pending_contract': 5,
                'negotiating': 4,
                'proposal_submitted': 3,
                'proposal_pending': 2,
                'planning': 1,
                'not_selected': 0,
                'cancelled_by_company': 0
            }
            
            # Group by consultant_id + ISO standard
            dedup_map = {}  # key: (consultant_id, iso_standard) -> project with highest priority
            
            for p in project_list:
                # Extract ISO standard from title
                iso_standard = ''
                if p.title:
                    iso_match = re.search(r'(ISO[/\s]?\w+[:\s]?\d*)', p.title, re.IGNORECASE)
                    if iso_match:
                        iso_standard = iso_match.group(1).strip()
                
                key = (p.consultant_id, iso_standard)
                current_priority = status_priority.get(p.status, 0)
                
                if key not in dedup_map:
                    dedup_map[key] = p
                else:
                    existing_priority = status_priority.get(dedup_map[key].status, 0)
                    if current_priority > existing_priority:
                        dedup_map[key] = p
            
            # Build deduplicated related projects list
            related_projects = []
            for (consultant_id, iso_standard), p in dedup_map.items():
                consultant = Consultant.query.get(p.consultant_id) if p.consultant_id else None
                related_projects.append({
                    'project_id': p.id,
                    'title': p.title,
                    'status': p.status,
                    'consultant_id': p.consultant_id,
                    'consultant_name': consultant.name if consultant else 'Unknown',
                    'created_at': p.created_at.isoformat() if p.created_at else None,
                    'proposal_price': p.proposal_price,
                    'proposal_submitted_at': p.proposal_submitted_at.isoformat() if p.proposal_submitted_at else None
                })
            
            # Sort related projects by created_at descending
            related_projects.sort(key=lambda x: x['created_at'] or '', reverse=True)
            
            statuses = [p.status for p in project_list]
            visible_statuses = [s for s in statuses if s not in ['cancelled_by_company', 'not_selected']]
            if not visible_statuses:
                overall_status = 'archived'
            else:
                overall_status = max(visible_statuses, key=lambda s: status_priority.get(s, 0))
            
            admin_job_id = f"company_{company_id}" if company_id is not None else "company_unassigned"

            results.append({
                'id': admin_job_id,  # Unique ID for frontend
                'company_name': company_name,
                'url': '',  # No URL in direct matching
                'status': overall_status,
                'created_at': earliest_project.created_at.isoformat() if earliest_project.created_at else None,
                'deleted_at': None,
                'intake_data': {
                    'industry': None,
                    'employees': None,
                    'region': None,
                    'standards': list(standards),
                    'issues': [],
                    'timeline': None,
                    'budget': None,
                    'contact_email': contact_email
                },
                'related_projects': related_projects,
                'project_count': len(related_projects)
            })
        
        # Sort by created_at descending
        results.sort(key=lambda x: x['created_at'] or '', reverse=True)
        
        return jsonify(results)
    except Exception as e:
        print(f"[Admin API] Error fetching jobs: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'message': '데이터를 불러오는 중 오류가 발생했습니다.', 'error': str(e)}), 500


# --- Admin Job Delete Endpoint ---
@app.route('/api/admin/jobs/<string:job_id>', methods=['DELETE'])
@admin_required
def delete_admin_job(job_id):
    """Soft delete a matching request (AnalysisJob)"""
    if job_id in ['company_unassigned', 'company_None', 'company_none']:
        protected_count = Project.query.filter(
            Project.company_id.is_(None),
            Project.status.in_(['contracted', 'in_progress', 'completed'])
        ).count()
        if protected_count > 0:
            return jsonify({
                'message': 'Contracted or active projects cannot be archived.',
                'contracted_count': protected_count
            }), 400

        projects = Project.query.filter(Project.company_id.is_(None)).all()
        now = datetime.datetime.now(datetime.timezone.utc)
        archived_count = 0
        for project in projects:
            if project.status not in ['cancelled_by_company', 'not_selected']:
                project.status = 'cancelled_by_company'
                project.cancelled_at = now
                project.cancelled_reason = 'Archived by admin'
                archived_count += 1

        log_admin_action('archive_unassigned_projects', 'company', 'unassigned', {'archived_count': archived_count})
        db.session.commit()
        return jsonify({
            'message': 'Unassigned project group archived',
            'id': job_id,
            'archived_count': archived_count,
            'deleted_at': now.isoformat()
        })

    if job_id.startswith('company_'):
        try:
            company_id = int(job_id.split('_', 1)[1])
        except (IndexError, ValueError):
            return jsonify({'message': 'Invalid company job id'}), 400

        protected_count = Project.query.filter(
            Project.company_id == company_id,
            Project.status.in_(['contracted', 'in_progress', 'completed'])
        ).count()
        if protected_count > 0:
            return jsonify({
                'message': 'Contracted or active projects cannot be archived.',
                'contracted_count': protected_count
            }), 400

        projects = Project.query.filter_by(company_id=company_id).all()
        now = datetime.datetime.now(datetime.timezone.utc)
        archived_count = 0
        for project in projects:
            if project.status not in ['cancelled_by_company', 'not_selected']:
                project.status = 'cancelled_by_company'
                project.cancelled_at = now
                project.cancelled_reason = 'Archived by admin'
                archived_count += 1

        log_admin_action('archive_company_projects', 'company', company_id, {'archived_count': archived_count})
        db.session.commit()
        return jsonify({
            'message': 'Company project group archived',
            'id': job_id,
            'archived_count': archived_count,
            'deleted_at': now.isoformat()
        })

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
    job.deleted_at = datetime.datetime.now(datetime.timezone.utc)
    job.status = 'deleted'
    log_admin_action('delete_analysis_job', 'analysis_job', job_id)
    db.session.commit()
    
    return jsonify({
        'message': '매칭 요청이 삭제되었습니다.',
        'id': job_id,
        'deleted_at': job.deleted_at.isoformat()
    })

# --- Consultant Admin Endpoints ---
@app.route('/api/admin/consultants/<int:consultant_id>/approve', methods=['POST'])
@admin_required
def approve_consultant(consultant_id):
    data = request.json or {}
    checklist, checklist_error = validate_approval_checklist(data)
    if checklist_error:
        return jsonify({'message': checklist_error}), 400
    consultant = Consultant.query.get_or_404(consultant_id)
    consultant.verified = True
    consultant.status = 'verified'
    consultant.rejection_reason = None
    consultant.rejected_at = None
    consultant.trust_score = max(consultant.trust_score or 50, 70)
    log_admin_action('approve_consultant', 'consultant', consultant_id, {'checklist': checklist})
    notify_consultant_review_result(
        consultant,
        'consultant_approved',
        'Consultant profile approved',
        'Your consultant profile has been approved and is now eligible for matching.'
    )
    db.session.commit()
    return jsonify({'message': 'Consultant approved successfully', 'verified': True})

@app.route('/api/admin/consultants/<int:consultant_id>/reject', methods=['POST'])
@admin_required
def reject_consultant(consultant_id):
    data = request.json or {}
    reason, reason_error = get_required_reason(data)
    if reason_error:
        return jsonify({'message': reason_error}), 400
    consultant = Consultant.query.get_or_404(consultant_id)
    consultant.verified = False
    consultant.status = 'rejected'
    consultant.rejection_reason = reason
    consultant.rejected_at = datetime.datetime.now(datetime.timezone.utc)
    consultant.trust_score = min(consultant.trust_score or 50, 50)
    log_admin_action('reject_consultant', 'consultant', consultant_id, {'reason': reason})
    notify_consultant_review_result(
        consultant,
        'consultant_rejected',
        'Consultant profile rejected',
        f'Rejection reason: {reason}'
    )
    db.session.commit()
    return jsonify({'message': f'Consultant rejected: {reason}'})

@app.route('/api/admin/consultants/<int:consultant_id>/revoke', methods=['POST'])
@admin_required
def revoke_consultant_verification(consultant_id):
    data = request.json or {}
    reason, reason_error = get_required_reason(data)
    if reason_error:
        return jsonify({'message': reason_error}), 400
    consultant = Consultant.query.get_or_404(consultant_id)
    consultant.verified = False
    consultant.status = 'revoked'
    consultant.rejection_reason = reason
    consultant.rejected_at = datetime.datetime.now(datetime.timezone.utc)
    consultant.trust_score = min(consultant.trust_score or 50, 50)
    log_admin_action('revoke_consultant', 'consultant', consultant_id, {'reason': reason})
    notify_consultant_review_result(
        consultant,
        'consultant_verification_revoked',
        'Consultant verification revoked',
        f'Revocation reason: {reason}'
    )
    db.session.commit()
    return jsonify({'message': 'Consultant verification revoked', 'verified': False, 'status': consultant.status})

@app.route('/api/admin/consultants/<int:consultant_id>/restore', methods=['POST'])
@admin_required
def restore_consultant(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)
    consultant.verified = False
    consultant.status = 'pending'
    consultant.rejection_reason = None
    consultant.rejected_at = None
    consultant.trust_score = max(consultant.trust_score or 50, 50)
    log_admin_action('restore_consultant', 'consultant', consultant_id)
    notify_consultant_review_result(
        consultant,
        'consultant_restored',
        'Consultant profile restored for review',
        'Your consultant profile has been restored to pending review.'
    )
    db.session.commit()
    return jsonify({'message': 'Consultant restored to pending', 'status': consultant.status})

# --- Consultant Detail Endpoint ---
@app.route('/api/consultants/<int:consultant_id>', methods=['GET'])
def get_consultant_detail(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)
    if not (consultant.verified or consultant.status == 'verified'):
        return jsonify({'message': 'Consultant not found'}), 404
    
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
@token_required
def request_quotes():
    data = request.json
    if g.current_user.role != 'company':
        return jsonify({'message': '기업 담당자만 견적 요청을 생성할 수 있습니다.'}), 403

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
    
    # Get user from JWT token (BUG-001 Fix)
    user_id = str(g.current_user.id)
    
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
    session_id = data.get('session_id')
    
    # === NEW: Ensure AnalysisJob exists for this session (for Admin Visibility) ===
    if session_id:
        existing_job = AnalysisJob.query.get(session_id)
        if not existing_job:
            try:
                company_name = analysis_context.get('company_name', '기업')
                new_job = AnalysisJob(
                    id=session_id,
                    company_name=company_name,
                    status='completed'
                )
                new_job.set_intake_data(analysis_context)
                db.session.add(new_job)
                db.session.commit()
                print(f"[Request Quotes] Created AnalysisJob for session {session_id}")
            except Exception as e:
                print(f"[Request Quotes] Error creating AnalysisJob: {e}")
                db.session.rollback()
    else:
        session_id = str(uuid.uuid4())

    # Create quote requests and projects for each consultant
    quote_request_id = str(uuid.uuid4())
    created_requests = []
    created_projects = []
    
    for consultant in consultants:
        # === Duplicate Prevention ===
        # Check if an ACTIVE project with same company+consultant+title already exists
        active_statuses = ['planning', 'proposal_pending', 'proposal_submitted', 'negotiating', 'pending_contract', 'awaiting_signature', 'contracted', 'in_progress']
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
            # Build description from analysis_context
            description_parts = []
            if analysis_context.get('industry'):
                description_parts.append(f"업종: {analysis_context['industry']}")
            if analysis_context.get('employees'):
                description_parts.append(f"직원 수: {analysis_context['employees']}")
            if analysis_context.get('region'):
                description_parts.append(f"지역: {analysis_context['region']}")
            if analysis_context.get('certStatus'):
                cert_map = {'none': '미보유', 'expired': '만료됨', 'expiring': '갱신 예정', 'valid': '유효', 'initial': '신규 인증'}
                description_parts.append(f"인증 상태: {cert_map.get(analysis_context['certStatus'], analysis_context['certStatus'])}")
            if analysis_context.get('timeline'):
                timeline_map = {'urgent': '긴급 (1개월 이내)', '3months': '3개월 이내', '6months': '6개월 이내', 'flexible': '유연함'}
                description_parts.append(f"희망 일정: {timeline_map.get(analysis_context['timeline'], analysis_context['timeline'])}")
            if analysis_context.get('budget'):
                budget_map = {'under500': '500만원 미만', '500-1000': '500~1,000만원', '1000-2000': '1,000~2,000만원', '2000-3000': '2,000~3,000만원', 'over3000': '3,000만원 이상', 'negotiable': '협의 가능'}
                description_parts.append(f"예산: {budget_map.get(analysis_context['budget'], analysis_context['budget'])}")
            if analysis_context.get('issues_summary'):
                description_parts.append(f"주요 이슈: {analysis_context['issues_summary']}")
            if analysis_context.get('additionalNotes'):
                description_parts.append(f"추가 요청: {analysis_context['additionalNotes']}")
            
            # Also extract from reasons
            reasons = analysis_context.get('reasons', [])
            if reasons:
                reason_names = {
                    'regulation': '법/규제 요구', 'customer': '거래처 요구', 'improve': '프로세스 개선',
                    'competitive': '경쟁력 강화', 'export': '수출 요건', 'esg': 'ESG 대응', 'other': '기타'
                }
                reason_text = ', '.join([reason_names.get(r, r) for r in reasons[:5]])
                description_parts.append(f"인증 사유: {reason_text}")
            
            project_description = '\n'.join(description_parts) if description_parts else None
            
            new_project = Project(
                company_id=user_id,
                consultant_id=consultant.id,
                title=project_title,
                description=project_description,
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
    
    # --- BUG-010 Fix: 인앱 알림 생성 ---
    for consultant in consultants:
        # 이 컨설턴트에 대한 프로젝트가 실제로 생성된 경우에만 알림 발송
        project_info = next(
            (p for p in created_projects if p['consultant_id'] == consultant.id),
            None
        )
        if project_info and consultant.user_id:
            try:
                notification = Notification(
                    user_id=consultant.user_id,
                    type='quote_request',
                    title=f'{company_name}에서 견적을 요청했습니다',
                    message=f'"{project_title}" 프로젝트에 대한 견적 요청이 도착했습니다. 대시보드에서 확인해주세요.',
                    link='/dashboard.html'
                )
                db.session.add(notification)
            except Exception as e:
                print(f"[Notification] Failed to create quote request notification: {e}")
    
    try:
        db.session.commit()
    except Exception as e:
        print(f"[Notification] Commit failed for notifications: {e}")
    
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
        forbidden = require_admin_request()
        if forbidden:
            return forbidden
        data = request.json
        new_post = Post(
            title=data.get('title'),
            content=data.get('content'),
            author=data.get('author', 'InsightMatch Team'),
            tags=data.get('tags'),
            image_url=data.get('image_url')
        )
        db.session.add(new_post)
        db.session.flush()
        log_admin_action('create_post', 'post', new_post.id, {'title': new_post.title})
        db.session.commit()
        return jsonify({'message': 'Post created', 'id': new_post.id}), 201

@app.route('/api/posts/<int:post_id>', methods=['GET', 'PUT', 'DELETE'])
def get_post(post_id):
    post = Post.query.get_or_404(post_id)
    
    if request.method == 'GET':
        return jsonify(post.to_dict())
    
    elif request.method == 'PUT':
        forbidden = require_admin_request()
        if forbidden:
            return forbidden
        data = request.json
        if data.get('title'):
            post.title = data['title']
        if 'content' in data:
            post.content = data['content']
        if 'author' in data:
            post.author = data['author']
        if 'tags' in data:
            post.tags = data['tags']
        if 'image_url' in data:
            post.image_url = data['image_url']
        log_admin_action('update_post', 'post', post_id, {'title': post.title})
        db.session.commit()
        return jsonify({'message': 'Post updated', 'post': post.to_dict()})
    
    elif request.method == 'DELETE':
        forbidden = require_admin_request()
        if forbidden:
            return forbidden
        log_admin_action('delete_post', 'post', post_id, {'title': post.title})
        db.session.delete(post)
        db.session.commit()
        return jsonify({'message': 'Post deleted', 'id': post_id})

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

# --- Blog Posts API ---
@app.route('/api/posts', methods=['GET'])
def get_posts():
    """블로그 게시글 목록 조회 (공개)"""
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return jsonify([p.to_dict() for p in posts])

@app.route('/api/posts/<int:post_id>', methods=['GET'])
def get_post_detail(post_id):
    """블로그 게시글 상세 조회 (공개)"""
    post = Post.query.get_or_404(post_id)
    return jsonify(post.to_dict())

# --- Health Check ---
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()})

# --- Seed Data Endpoint (Admin only) ---
@app.route('/api/admin/seed', methods=['POST'])
@admin_required
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
@token_required
def get_notifications():
    """사용자 알림 목록 조회"""
    user_id = str(g.current_user.id)
    if not user_id:
        return jsonify({'message': 'User ID required'}), 400
    
    notifications = Notification.query.filter_by(user_id=user_id).order_by(Notification.created_at.desc()).limit(50).all()
    unread_count = Notification.query.filter_by(user_id=user_id, is_read=False).count()
    
    return jsonify({
        'notifications': [n.to_dict() for n in notifications],
        'unreadCount': unread_count
    })

@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
@token_required
def mark_notification_read(notification_id):
    """알림 읽음 처리"""
    notification = Notification.query.get_or_404(notification_id)
    if not _same_id(notification.user_id, g.current_user.id):
        return jsonify({'message': 'Notification access denied'}), 403
    notification.is_read = True
    db.session.commit()
    return jsonify({'message': 'Marked as read'})

@app.route('/api/notifications/read-all', methods=['POST'])
@token_required
def mark_all_notifications_read():
    """모든 알림 읽음 처리"""
    user_id = str(g.current_user.id)
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
@token_required
def consultant_profile(consultant_id):
    """컨설턴트 프로필 조회/수정"""
    consultant = Consultant.query.get_or_404(consultant_id)
    if not is_consultant_owner_or_admin(consultant):
        return jsonify({'message': 'Only the consultant owner can manage this profile.'}), 403
    
    if request.method == 'GET':
        return jsonify(consultant.to_dict())
    
    elif request.method == 'PUT':
        data = request.json
        user_id = g.current_user.id
        
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
                if isinstance(new_value, str):
                    new_value = new_value.strip()
                if json_key == 'profileImageUrl' and not is_allowed_profile_image_url(new_value):
                    return jsonify({'message': 'Invalid profile image URL'}), 400
                if json_key in ['bio', 'introductionVideoUrl'] and len(str(new_value or '')) > 1000:
                    return jsonify({'message': f'{json_key} is too long.'}), 400
                if json_key in ['phone', 'email', 'companyName', 'specialty', 'regions'] and len(str(new_value or '')) > 200:
                    return jsonify({'message': f'{json_key} is too long.'}), 400
                
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
@token_required
def manage_portfolio(consultant_id):
    """포트폴리오 파일 관리"""
    consultant = Consultant.query.get_or_404(consultant_id)
    if not is_consultant_owner_or_admin(consultant):
        return jsonify({'message': 'Only the consultant owner can manage portfolio files.'}), 403
    
    current_files = json.loads(consultant.portfolio_files) if consultant.portfolio_files else []
    
    if request.method == 'POST':
        data = request.json
        new_file = {
            'name': data.get('name'),
            'url': data.get('url'),
            'uploaded_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
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
@token_required
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
@admin_required
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

@app.route('/api/admin/consultants/<int:consultant_id>/history', methods=['GET'])
@admin_required
def get_consultant_review_history(consultant_id):
    consultant = Consultant.query.get_or_404(consultant_id)

    action_logs = AdminActionLog.query.filter_by(
        target_type='consultant',
        target_id=str(consultant_id)
    ).order_by(AdminActionLog.created_at.desc()).limit(50).all()

    profile_logs = ProfileChangeLog.query.filter_by(
        consultant_id=consultant_id
    ).order_by(ProfileChangeLog.changed_at.desc()).limit(50).all()

    actions = []
    for log in action_logs:
        item = log.to_dict()
        admin = User.query.get(log.admin_user_id)
        item['adminName'] = admin.name if admin else 'Unknown'
        item['adminEmail'] = admin.email if admin else None
        actions.append(item)

    changes = []
    for log in profile_logs:
        item = log.to_dict()
        changer = User.query.get(log.changed_by) if log.changed_by else None
        item['changedByName'] = changer.name if changer else 'System'
        changes.append(item)

    return jsonify({
        'consultant': {
            'id': consultant.id,
            'name': consultant.name,
            'status': consultant.status,
            'verified': consultant.verified,
        },
        'actions': actions,
        'changes': changes,
    })

@app.route('/api/admin/action-logs', methods=['GET'])
@admin_required
def get_admin_action_logs():
    limit = request.args.get('limit', 100, type=int)
    logs = AdminActionLog.query.order_by(AdminActionLog.created_at.desc()).limit(limit).all()
    result = []
    for log in logs:
        item = log.to_dict()
        admin = User.query.get(log.admin_user_id)
        item['adminName'] = admin.name if admin else 'Unknown'
        item['adminEmail'] = admin.email if admin else None
        result.append(item)
    return jsonify(result)

# ========================================
# 프로필 이미지 업로드 API
# ========================================

@app.route('/api/upload/profile-image', methods=['POST'])
@token_required
def upload_profile_image():
    """프로필 이미지를 Supabase Storage에 업로드"""
    import requests
    import time as time_module
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if g.current_user.role not in ['consultant', 'admin']:
        return jsonify({'error': 'Only consultants can upload profile images'}), 403
    user_id = g.current_user.id
    
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
    supabase_url = get_supabase_url()
    supabase_key = os.environ.get('SUPABASE_ANON_KEY', '').strip() # 사용자가 제공해야 함
    if not supabase_url or not supabase_key:
        return jsonify({'error': 'Profile image storage is not configured'}), 500
    
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

@app.route('/api/upload/proposal-file', methods=['POST'])
@token_required
def upload_proposal_file():
    """제안서 원본 파일을 Supabase Storage에 업로드"""
    import requests
    import time as time_module
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if g.current_user.role not in ['consultant', 'admin']:
        return jsonify({'error': 'Only consultants can upload proposal files'}), 403
    user_id = g.current_user.id

    project_id = request.form.get('project_id')
    if not project_id:
        return jsonify({'error': 'Project ID required'}), 400

    project = Project.query.get_or_404(project_id)
    consultant = Consultant.query.get(project.consultant_id) if project.consultant_id else None
    if g.current_user.role != 'admin' and (not consultant or not _same_id(consultant.user_id, user_id)):
        return jsonify({'error': 'Only the assigned consultant can upload proposal files'}), 403

    if project.status in ['contracted', 'in_progress', 'completed', 'pending_contract', 'awaiting_signature']:
        return jsonify({'error': 'Cannot upload proposal files after contract workflow has started'}), 400
    
    # 1. 파일 확장자 추출 및 안전한 파일명 생성
    file_ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'pdf'
    allowed_exts = {'pdf', 'doc', 'docx'}
    if file_ext not in allowed_exts:
        return jsonify({'error': 'Only PDF, DOC, and DOCX proposal files are allowed'}), 400

    unique_name = f"{project.id}_{user_id}_{int(time_module.time())}.{file_ext}"
    
    # Supabase 설정
    supabase_url = get_supabase_url()
    supabase_key = os.environ.get('SUPABASE_ANON_KEY', '').strip()
    if not supabase_url or not supabase_key:
        return jsonify({'error': 'Proposal storage is not configured'}), 500
    
    # 업로드 경로: bucket/filename (proposals 버킷 내부)
    upload_url = f"{supabase_url}/storage/v1/object/proposals/{unique_name}"
    
    headers = {
        'Authorization': f'Bearer {supabase_key}',
        'apikey': supabase_key,
        'Content-Type': file.content_type or 'application/pdf',
        'x-upsert': 'true'
    }
    
    try:
        file.seek(0)
        file_data = file.read()
        
        # Supabase API 호출 (POST)
        response = requests.post(upload_url, headers=headers, data=file_data)
        
        if response.status_code in [200, 201]:
            # 성공 시 Public URL 생성
            public_url = f"{supabase_url}/storage/v1/object/public/proposals/{unique_name}"
            return jsonify({
                'success': True,
                'url': public_url,
                'fileName': file.filename
            })
        else:
            # 실패 시 상세 에러 출력
            error_data = response.json() if response.text else {"message": response.text}
            print(f"Supabase Error: {error_data}")
            return jsonify({'error': f"Storage Error ({response.status_code}): {error_data.get('message', 'Unknown error')}"}), 500
            
    except Exception as e:
        print(f"Upload Exception: {str(e)}")
        return jsonify({'error': f'Internal Server Error: {str(e)}'}), 500
            
    except Exception as e:
        return jsonify({'error': f'업로드 중 오류: {str(e)}'}), 500

# ========================================
# B. 인앱 메시지 API
# ========================================

@app.route('/api/projects/<int:project_id>/messages', methods=['GET', 'POST'])
@token_required
def handle_messages(project_id):
    """프로젝트 메시지 조회/전송"""
    project = Project.query.get_or_404(project_id)
    forbidden = require_project_participant(project)
    if forbidden:
        return forbidden
    
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
        sender_id = g.current_user.id
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
@token_required
def mark_messages_read(project_id):
    """해당 프로젝트의 메시지 읽음 처리"""
    project = Project.query.get_or_404(project_id)
    forbidden = require_project_participant(project)
    if forbidden:
        return forbidden
    user_id = str(g.current_user.id)
    
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
@token_required
def get_unread_message_count():
    """읽지 않은 메시지 수 조회"""
    user_id = str(g.current_user.id)
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
@token_required
def get_contact_info(project_id):
    """계약 완료된 프로젝트의 컨설턴트 연락처 조회"""
    project = Project.query.get_or_404(project_id)
    forbidden = require_project_participant(project)
    if forbidden:
        return forbidden
    
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

# 정적 서빙 허용 확장자 (화이트리스트). 여기에 없는 확장자는 절대 서빙하지 않는다.
# .py/.db/.env/.log/.sqlite 등은 목록에 없으므로 자동 차단된다.
ALLOWED_STATIC_EXTENSIONS = {
    '.html', '.htm', '.css', '.js', '.mjs', '.map', '.json',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp', '.avif',
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.mp4', '.webm', '.mp3', '.pdf',
}

# 서빙을 금지할 최상위 디렉터리 (소스·설정·데이터·테스트)
BLOCKED_STATIC_PREFIXES = {
    'api', 'tests', 'scripts', 'data', 'directives',
    '.git', '.claude', '.vercel', 'node_modules', 'venv', '__pycache__',
}


@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files (HTML, CSS, JS, images).

    보안: 경로 이탈(../)·비밀파일(.env)·DB(.db)·소스(.py) 노출을 차단한다.
    허용 확장자 화이트리스트 + 루트 격리(realpath) + 디렉터리 차단의 3중 방어.
    """
    root = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # 1) 명백한 경로 조작 문자 차단
    if '..' in filename or filename.startswith('/') or '\\' in filename or '\x00' in filename:
        return jsonify({'error': 'File not found'}), 404

    normalized = filename.replace('\\', '/').strip('/')
    if not normalized:
        return jsonify({'error': 'File not found'}), 404

    segments = [seg for seg in normalized.split('/') if seg]

    # 2) 숨김 파일/디렉터리(.env, .git 등) 및 차단 디렉터리 거부
    if any(seg.startswith('.') for seg in segments):
        return jsonify({'error': 'File not found'}), 404
    if segments[0].lower() in BLOCKED_STATIC_PREFIXES:
        return jsonify({'error': 'File not found'}), 404

    # 3) 확장자 화이트리스트
    ext = os.path.splitext(segments[-1])[1].lower()
    if ext not in ALLOWED_STATIC_EXTENSIONS:
        return jsonify({'error': 'File not found'}), 404

    # 4) 심볼릭 링크 등으로 루트를 벗어나는지 최종 확인
    file_path = os.path.realpath(os.path.join(root, *segments))
    if file_path != root and not file_path.startswith(root + os.sep):
        return jsonify({'error': 'File not found'}), 404

    if os.path.isfile(file_path):
        return send_file(file_path)
    return jsonify({'error': 'File not found'}), 404

# Vercel automatically detects Flask app named 'app'

# For local development
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)

