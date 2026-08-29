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
import traceback as traceback_module
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file, Response, g, stream_with_context, abort
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import jwt
from sqlalchemy import and_, case, func, text
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, AnalysisJob, AdminActionLog, Consultant, ConsultantInvite, CronRun, ErrorLog, Inquiry, Review, User, Project, Milestone, Post, Company, Notification, Message, ProfileChangeLog, PasswordResetToken, ManualGeneration, RateLimitEntry, parse_text_or_json_list
from services import MatchingService, ProposalService, EmailService
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
#    (미처리 예외 전역 핸들러는 아래 "전역 예외 핸들러" 섹션에 있다.
#     Flask 는 코드별 핸들러를 먼저 찾으므로 이 413 핸들러가 그대로 우선한다.)
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

# 테스트 전용 오버라이드.
# 테스트는 SQLALCHEMY_DATABASE_URI 환경변수로 인메모리 DB를 지정한다.
# 이 훅이 없으면 db.init_app() 시점에 파일 DB로 엔진이 만들어져,
# 테스트가 config를 나중에 바꿔도 무시되고 drop_all()이 실제 개발 DB를 지운다.
_db_override = os.environ.get('SQLALCHEMY_DATABASE_URI')
if _db_override:
    database_url = _db_override

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    # 프로덕션에서 약한 키로 기동하면 JWT 위조가 가능해지므로 부팅을 중단한다.
    if os.environ.get('VERCEL'):
        raise RuntimeError(
            'SECRET_KEY 환경변수가 설정되지 않았습니다. '
            '프로덕션에서는 반드시 설정해야 합니다 (Vercel 환경변수).'
        )
    import warnings
    warnings.warn('SECRET_KEY 미설정 — 로컬 개발용 임시 키를 사용합니다.', stacklevel=1)
    app.config['SECRET_KEY'] = 'dev-only-insecure-key-' + secrets.token_hex(32)

db.init_app(app)

# Initialize Services
# 참고: AIService는 레거시 /api/analyze 제거와 함께 사용처가 없어져 초기화하지 않는다.
#       AdvancedDiagnosticService도 /api/diagnostic/* 제거와 함께 초기화하지 않는다
#       (서비스 파일·리스크 DB는 재사용 대비로 보존).
matching_service = MatchingService()
proposal_service = ProposalService()
email_service = EmailService()

# 신규 컨설턴트의 평점 초기값.
# 가입 경로가 3개(등록 폼 / 일반 회원가입 / 프로필 등록)인데 각각 5.0 · 0.0 · 5.0
# 으로 제각각이라, 어느 경로로 들어왔느냐에 따라 매칭 평점 점수가 갈렸다.
# 리뷰가 0건인데 5.0 만점으로 출발하는 것은 기업에게 없는 실적을 있다고
# 말하는 것이므로 0.0 으로 통일한다.
#
# 신규 컨설턴트가 이 때문에 불리해지지는 않는다. matching_service 의 평점 블록은
# reviews == 0 이면 rating 값을 아예 쓰지 않고 중립값을 준다
# (services/matching_service.py 의 _rating_block 참조).
NEW_CONSULTANT_RATING = 0.0

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
            # 탈퇴 검사: 소프트 삭제라 행이 남아 있으므로 여기서 막지 않으면
            # 탈퇴한 사용자가 기존 JWT 로 계속 API 를 쓸 수 있다.
            # (token_version 도 함께 올리지만, 두 방어선을 모두 둔다)
            if getattr(current_user, 'deleted_at', None) is not None:
                return jsonify({'message': '탈퇴한 계정입니다.'}), 401
            # 토큰 폐기 검사: 비밀번호 재설정 등으로 버전이 오르면 기존 토큰은 무효
            if payload.get('tv', 0) != (current_user.token_version or 0):
                return jsonify({'message': '세션이 만료되었습니다. 다시 로그인해주세요.'}), 401
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
                candidate = User.query.get(payload['user_id'])
                # 탈퇴한 계정은 '비로그인' 과 동일하게 취급한다.
                # (여기서 걸러내지 않으면 탈퇴자가 공개 API 에서 로그인 사용자로
                #  인식되어 문의 자동 연결·관리자 목록 노출 등이 그대로 동작한다)
                if candidate is not None and getattr(candidate, 'deleted_at', None) is None:
                    g.current_user = candidate
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


def get_active_project_or_404(project_id):
    """삭제(soft delete)되지 않은 프로젝트만 반환. 삭제된 건 404 처리."""
    project = Project.query.get_or_404(project_id)
    if getattr(project, 'deleted_at', None) is not None:
        abort(404)
    return project


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


# ============================================================
# 전역 예외 핸들러 (관측성)
# ============================================================
# 이 앱은 Vercel 서버리스라 print() 로그가 콘솔에만 남고 휘발된다.
# 미처리 예외를 ErrorLog 테이블에 남겨야 "에러가 나도 아무도 모르는" 상태를
# 벗어날 수 있다. 외부 SDK(Sentry)는 쓰지 않기로 결정했다.

ERROR_TRACEBACK_MAX_LENGTH = 8000

# SQLAlchemy 예외는 실패한 SQL 의 바인딩 파라미터를 메시지에 그대로 붙인다.
#   (sqlite3.IntegrityError) UNIQUE constraint failed: user.email
#   [SQL: INSERT INTO user (email, password_hash) VALUES (?, ?)]
#   [parameters: ('a@b.com', 'pbkdf2:sha256:...')]
# 즉 비밀번호 해시·재설정 토큰·이메일이 에러 로그에 그대로 적재된다.
# SQL 문 자체는 디버깅에 필요하므로 남기고, 값(parameters)만 지운다.
_SQL_PARAMS_RE = re.compile(r'\[parameters:.*?\]', re.DOTALL)


def _scrub_sql_parameters(text_value):
    """예외 문자열에서 SQL 바인딩 값을 제거한다 (민감정보 적재 방지)."""
    if not text_value:
        return ''
    return _SQL_PARAMS_RE.sub('[parameters: <제거됨>]', text_value)

# 경로의 가변 부분(ID/UUID)을 치환해 같은 에러가 흩어지지 않게 한다.
# /api/projects/12 와 /api/projects/34 는 같은 버그다.
_ERROR_PATH_UUID_RE = re.compile(
    r'/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
)
_ERROR_PATH_NUMERIC_RE = re.compile(r'/\d+')


def _normalize_error_path(path):
    """fingerprint 계산용 경로 정규화."""
    normalized = _ERROR_PATH_UUID_RE.sub('/<uuid>', path or '')
    normalized = _ERROR_PATH_NUMERIC_RE.sub('/<id>', normalized)
    return normalized[:300]


def _error_top_frame(exc):
    """예외가 실제로 발생한 프레임을 'file:func:line' 으로 요약.

    스택의 마지막 프레임(=raise 가 일어난 지점)이 에러를 가장 잘 구분한다.
    """
    try:
        frames = traceback_module.extract_tb(exc.__traceback__)
        if not frames:
            return ''
        last = frames[-1]
        return f"{os.path.basename(last.filename)}:{last.name}:{last.lineno}"
    except Exception:
        return ''


def _error_fingerprint(exc_type, normalized_path, top_frame):
    """같은 에러를 묶기 위한 해시.

    카운터 컬럼을 올리는 방식은 서버리스 동시 실행에서 경합하므로 쓰지 않는다.
    행을 그대로 쌓고 조회 시 GROUP BY 로 센다.
    """
    raw = f"{exc_type}|{normalized_path}|{top_frame}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


def _truncate_traceback(text_value):
    """스택 트레이스를 저장 한도에 맞춰 자른다.

    앞이 아니라 뒤를 남긴다. 실제 예외가 터진 지점은 항상 마지막에 있다.
    """
    if not text_value:
        return ''
    if len(text_value) <= ERROR_TRACEBACK_MAX_LENGTH:
        return text_value
    return '...(앞부분 생략)...\n' + text_value[-ERROR_TRACEBACK_MAX_LENGTH:]


def record_error_log(error, status_code):
    """예외 1건을 ErrorLog 에 기록.

    ⚠️ 이 함수는 어떤 경우에도 예외를 밖으로 던지지 않는다.
       로깅 실패가 원래 에러를 가려버리면 관측성을 위해 만든 코드가
       오히려 디버깅을 방해하게 된다.
    """
    try:
        # 롤백하면 세션의 인스턴스가 expire 되어 g.current_user.id 접근이
        # 새 SELECT 를 유발한다(DB 장애 시 그 쿼리마저 실패). 이미 로드된
        # 속성이라 SQL 이 발생하지 않는 지금 미리 꺼내둔다.
        user_id = None
        try:
            current_user = getattr(g, 'current_user', None)
            user_id = getattr(current_user, 'id', None) if current_user is not None else None
        except Exception:
            user_id = None

        # 예외 시점의 세션은 오염돼 있다. 롤백 없이 add/commit 하면
        # 로깅 자체가 PendingRollbackError 로 실패한다.
        db.session.rollback()

        exc_type = type(error).__name__[:120]
        exc_message = _scrub_sql_parameters(str(error))[:2000]
        tb_text = _truncate_traceback(_scrub_sql_parameters(
            ''.join(traceback_module.format_exception(type(error), error, error.__traceback__))
        ))
        path = (request.path or '')[:300]
        normalized_path = _normalize_error_path(request.path)

        db.session.add(ErrorLog(
            level='error',
            path=path,
            method=(request.method or '')[:10],
            status_code=status_code,
            exc_type=exc_type,
            exc_message=exc_message,
            traceback=tb_text,
            user_id=user_id,
            client_ip=_client_ip(),
            fingerprint=_error_fingerprint(exc_type, normalized_path, _error_top_frame(error)),
        ))
        db.session.commit()
    except Exception as log_error:
        # DB 기록에 실패하면 최소한 콘솔에는 남긴다 (Vercel 런타임 로그).
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[ErrorLog] 기록 실패: {type(log_error).__name__}: {log_error}")
        try:
            print(''.join(traceback_module.format_exception(type(error), error, error.__traceback__)))
        except Exception:
            pass


def record_email_failure(purpose, error, commit=False):
    """메일 발송 실패를 ErrorLog 에 남긴다 (요청 자체는 계속 진행한다).

    메일이 안 갔다고 API 를 500 으로 만들 수는 없다. 그런데 print 만 하면
    Vercel 콘솔에서 휘발돼 "메일이 안 갔다"는 사실을 아무도 모르게 된다.
    직전 배치에서 만든 ErrorLog 에 level='warning' 으로 함께 쌓아
    관리자 에러 로그 화면에서 확인할 수 있게 한다.

    ⚠️ record_error_log 와 달리 db.session.rollback() 을 하지 않는다.
       메일 실패는 DB 세션을 오염시키지 않는데, 여기서 롤백하면 호출부가 아직
       커밋하지 않은 변경(예: 컨설턴트 승인)까지 함께 날아간다.

    Args:
        purpose: 어떤 메일인지 (fingerprint 그룹 키로도 쓰인다)
        error: 발생한 예외
        commit: 호출부가 이미 커밋을 끝내 세션에 걸린 변경이 없을 때만 True.
                False 면 행을 세션에 얹어두고 호출부의 커밋에 함께 실린다.
    """
    # DB 기록이 실패하더라도 최소한 런타임 로그에는 남긴다.
    print(f"[Email] {purpose} 발송 실패: {type(error).__name__}: {error}")
    try:
        user_id = None
        try:
            current_user = getattr(g, 'current_user', None)
            user_id = getattr(current_user, 'id', None) if current_user is not None else None
        except Exception:
            user_id = None

        exc_type = type(error).__name__[:120]
        normalized_path = _normalize_error_path(request.path)

        db.session.add(ErrorLog(
            level='warning',   # 요청은 성공했다. 장애가 아니라 부분 실패다.
            path=(request.path or '')[:300],
            method=(request.method or '')[:10],
            status_code=None,  # HTTP 상태로 드러나지 않는 실패라 비워둔다.
            exc_type=exc_type,
            exc_message=f'[{purpose}] {_scrub_sql_parameters(str(error))}'[:2000],
            traceback=_truncate_traceback(_scrub_sql_parameters(
                ''.join(traceback_module.format_exception(type(error), error, error.__traceback__))
            )),
            user_id=user_id,
            client_ip=_client_ip(),
            # 메일 종류별로 그룹이 갈리도록 purpose 를 fingerprint 에 포함한다.
            fingerprint=_error_fingerprint(
                f'email:{purpose}:{exc_type}', normalized_path, _error_top_frame(error)
            ),
        ))
        if commit:
            db.session.commit()
    except Exception as log_error:
        print(f"[ErrorLog] 메일 실패 기록에 실패: {type(log_error).__name__}: {log_error}")
        if commit:
            # 커밋을 시도했다가 실패한 경우에만 롤백한다.
            # (commit=False 인 호출부는 아직 커밋 전이라 여기서 롤백하면 안 된다)
            try:
                db.session.rollback()
            except Exception:
                pass


def frontend_base_url():
    """메일 본문에 넣을 프론트엔드 기본 URL.

    BASE_URL 이 없으면 요청 호스트를 쓴다(비밀번호 재설정 링크와 동일한 규칙).
    """
    base = os.environ.get('BASE_URL')
    if not base:
        try:
            base = request.host_url
        except Exception:
            base = 'https://www.insightmatch.com'
    return (base or '').rstrip('/')


def normalize_standard_codes(value):
    """ISO 규격 목록을 메일 본문에 넣을 문자열 리스트로 정규화한다.

    /api/match 퍼널은 문자열 리스트를 주지만, 저장된 진단 맥락에는
    [{'code': 'ISO 9001'}] 형태가 섞여 들어올 수 있다. 그대로 넘기면
    메일 템플릿의 ', '.join() 이 TypeError 로 터져 메일이 통째로 안 나간다.
    """
    if not isinstance(value, list):
        return []
    codes = []
    for item in value:
        if isinstance(item, dict):
            code = item.get('code') or item.get('name') or ''
        else:
            code = item
        code = str(code or '').strip()
        if code:
            codes.append(code)
    return codes


@app.errorhandler(Exception)
def handle_unexpected_exception(error):
    """미처리 예외를 기록하고 클라이언트에는 일반 500 만 반환한다."""
    # Flask 의 errorhandler(Exception) 은 HTTPException 도 함께 잡는다.
    # 여기서 걸러내지 않으면 404/401/403/400 이 전부 500 으로 바뀐다.
    # 이들은 정상 동작이지 에러가 아니므로 기록하지 않고 그대로 통과시킨다.
    # 단, 5xx HTTPException(예: abort(503))은 장애이므로 기록한다.
    if isinstance(error, HTTPException):
        if not error.code or error.code < 500:
            return error
        status_code = error.code
    else:
        status_code = 500

    record_error_log(error, status_code)

    # 스택 트레이스는 절대 클라이언트에 노출하지 않는다.
    # (내부 경로·라이브러리 버전·쿼리 구조가 공격자에게 그대로 넘어간다)
    return jsonify({
        'error': 'Internal Server Error',
        'message': '요청을 처리하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'
    }), status_code


def _naive_utc_now():
    """DB 비교용 naive UTC.

    DateTime 컬럼은 tz 정보를 저장하지 않으므로(SQLite/PG both),
    조회 시에도 naive UTC 로 비교해야 한다.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _iso_or_none(value):
    """SQL 함수 결과가 문자열로 돌아오는 경우까지 안전하게 처리."""
    if not value:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _as_naive_utc(value):
    """모델에서 읽은 DateTime 을 naive UTC 로 통일한다.

    DateTime 컬럼은 tz 를 저장하지 않으므로 DB 왕복 후에는 naive 로 돌아오지만,
    같은 세션에서 방금 쓴 인스턴스는 identity map 에 남아 aware 인 채로 나온다
    (저장 시 datetime.now(timezone.utc) 를 그대로 넣기 때문). 이 둘을 그대로
    비교하면 TypeError: can't compare offset-naive and offset-aware 로 터진다.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


def _apply_error_level_filter(query, level_filter):
    """ErrorLog 조회에 level 조건을 건다.

    'error'   = 미처리 예외(장애). warning 이 아닌 모든 것.
    'warning' = 메일 발송 실패 같은 부분 실패(요청 자체는 성공했다).
    두 가지를 한 칸에 섞으면 500 이 늘었는지 메일만 막혔는지 구분되지 않는다.
    """
    if level_filter == 'warning':
        return query.filter(ErrorLog.level == 'warning')
    if level_filter == 'error':
        return query.filter(ErrorLog.level != 'warning')
    return query


def error_group_summary(since=None, limit=10, level_filter=None):
    """fingerprint 별 요약 — 발생 횟수 / 최근·최초 발생 시각 / 대표 메시지.

    카운터 컬럼 대신 GROUP BY 로 센다(서버리스 동시성 경합 회피).

    Args:
        level_filter: None(전체) | 'error'(미처리 예외) | 'warning'(부분 실패)
    """
    query = db.session.query(
        ErrorLog.fingerprint,
        func.count(ErrorLog.id).label('count'),
        func.max(ErrorLog.created_at).label('last_seen'),
        func.min(ErrorLog.created_at).label('first_seen'),
    )
    if since is not None:
        query = query.filter(ErrorLog.created_at >= since)
    query = _apply_error_level_filter(query, level_filter)

    rows = (
        query.group_by(ErrorLog.fingerprint)
        .order_by(func.count(ErrorLog.id).desc())
        .limit(limit)
        .all()
    )

    groups = []
    for row in rows:
        # 대표 메시지는 가장 최근 발생 건에서 가져온다 (그룹당 1회, 최대 limit 회)
        latest_query = ErrorLog.query.filter(ErrorLog.fingerprint == row.fingerprint)
        if since is not None:
            latest_query = latest_query.filter(ErrorLog.created_at >= since)
        latest_query = _apply_error_level_filter(latest_query, level_filter)
        latest = latest_query.order_by(ErrorLog.created_at.desc(), ErrorLog.id.desc()).first()

        groups.append({
            'fingerprint': row.fingerprint,
            'count': row.count,
            'lastSeen': _iso_or_none(row.last_seen),
            'firstSeen': _iso_or_none(row.first_seen),
            'excType': latest.exc_type if latest else None,
            'message': (latest.exc_message or '')[:300] if latest else '',
            'path': latest.path if latest else None,
            'statusCode': latest.status_code if latest else None,
        })
    return groups


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
    # 탈퇴 검사 (token_required 와 동일한 이유). 관리자 경로는 별도 데코레이터라
    # 여기에 넣지 않으면 관리자 API 만 탈퇴 후에도 열려 있게 된다.
    if getattr(current_user, 'deleted_at', None) is not None:
        return jsonify({'message': '탈퇴한 계정입니다.'}), 401
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

def get_consultant_contact_email(consultant):
    """컨설턴트에게 심사 결과를 보낼 이메일 주소를 고른다.

    Consultant.email 은 프로필 '공개용' 으로 따로 받는 값이라 계정 이메일과
    다를 수 있고, 관리자가 대신 등록한 경우 비어 있기도 하다.
    승인·거절은 계정 상태 통지이므로 로그인에 쓰는 User.email 을 우선하고,
    연결된 User 가 없을 때만 Consultant.email 로 내려간다.
    """
    if not consultant:
        return None
    user = User.query.get(consultant.user_id) if consultant.user_id else None
    email = (user.email if user else None) or consultant.email
    return (email or '').strip() or None


def notify_consultant_review_result(consultant, notification_type, title, message, reason=None):
    """컨설턴트 심사 결과 통지 (승인·거절·자격해제·복원 공통 관문).

    인앱 알림 + 이메일을 함께 보낸다. consultant_register.html 과 admin.html 이
    이미 사용자에게 "승인 완료 시 이메일로 안내드립니다" / "거부 사유는 이메일로
    전달됩니다" 라고 약속하고 있으므로 인앱 알림만으로는 약속이 지켜지지 않는다.

    Args:
        reason: 거절·자격해제 사유. 메일 본문에 포함된다.
    """
    if not (consultant and consultant.user_id):
        return

    notification = Notification(
        user_id=consultant.user_id,
        type=notification_type,
        title=title,
        message=message,
        link='/dashboard.html'
    )
    db.session.add(notification)

    # 메일 실패가 승인 처리 자체를 되돌리면 안 된다.
    # commit=False: 호출부(approve/reject/revoke/restore)가 바로 뒤에서 커밋하므로
    # 실패 기록도 그 커밋에 함께 실린다.
    consultant_email = get_consultant_contact_email(consultant)
    if not consultant_email:
        return

    try:
        base_url = frontend_base_url()
        result = email_service.send_consultant_review_result(
            consultant_email=consultant_email,
            consultant_name=consultant.name,
            notification_type=notification_type,
            reason=reason,
            dashboard_url=f'{base_url}/dashboard.html',
            register_url=f'{base_url}/consultant_register.html'
        )
        if (result or {}).get('success'):
            # 이 알림은 이미 메일로 나갔다. 표식을 남기지 않으면 미열람 승격
            # 배치가 하루 뒤 같은 내용을 다시 메일로 보낸다.
            notification.emailed_at = _naive_utc_now()
    except Exception as e:
        record_email_failure(f'consultant_review_result:{notification_type}', e)


# 관리자 통지 수신자 상한. admin 계정이 늘어도 이벤트 1건당 메일이
# 무한정 늘어나지 않게 한다 (cron 의 CRON_ADMIN_RECIPIENT_LIMIT 과 같은 취지).
ADMIN_NOTIFY_RECIPIENT_LIMIT = 5


def notify_admins(notif_type, title, message, link='/admin.html', email_spec=None):
    """관리자 전원(상한 내)에게 인앱 알림을 만들고, 필요하면 메일도 보낸다.

    커밋은 하지 않는다 — 호출부가 자기 트랜잭션과 함께 커밋한다.

    Args:
        email_spec: None 이면 인앱 알림만 만들고 emailed_at 을 비워둔다. 그러면
               미열람 승격 배치가 하루 1통으로 묶어 보낸다(리드 통지가 이 경로다).
               dict 를 주면 그 자리에서 메일을 보내고 emailed_at 을 채운다.
               {'subject_label', 'heading', 'summary', 'rows', 'cta_label'}

    Returns:
        (생성한 알림 수, 메일 발송 성공 수)
    """
    admins = (
        User.query.filter(User.role == 'admin', User.deleted_at.is_(None))
        .order_by(User.id)
        .limit(ADMIN_NOTIFY_RECIPIENT_LIMIT)
        .all()
    )
    if not admins:
        return 0, 0

    base_url = frontend_base_url()
    admin_url = f"{base_url}{link if str(link).startswith('/') else '/' + str(link)}"
    created = 0
    sent = 0

    for admin in admins:
        notification = Notification(
            user_id=admin.id,
            type=notif_type,
            title=title,
            message=message,
            link=link,
        )
        db.session.add(notification)
        created += 1

        if not email_spec:
            continue

        admin_email = (admin.email or '').strip()
        if not admin_email:
            continue

        try:
            result = email_service.send_admin_alert(
                to_email=admin_email,
                admin_name=admin.name,
                subject_label=email_spec.get('subject_label') or title,
                heading=email_spec.get('heading') or title,
                summary=email_spec.get('summary') or message,
                rows=email_spec.get('rows'),
                action_url=admin_url,
                cta_label=email_spec.get('cta_label') or '관리자 화면에서 보기 →',
            )
        except Exception as e:
            record_email_failure(f'admin_alert:{notif_type}', e)
            continue

        if not (result or {}).get('success'):
            # send_email 은 SMTP 실패를 예외가 아니라 {'success': False} 로 돌려준다.
            record_email_failure(
                f'admin_alert:{notif_type}',
                RuntimeError((result or {}).get('message', 'unknown')),
            )
            continue

        notification.emailed_at = _naive_utc_now()
        sent += 1

    return created, sent


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
        Project.deleted_at.is_(None),
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

# --- 정산·세금계산서 정보 검증 (A안: NGB 원청 구조의 외주비 지급에 필요) ---
PARTNER_AGREEMENT_VERSION = '1.0'
ALLOWED_BUSINESS_TYPES = {'business', 'individual'}


def is_valid_biz_reg_no(value):
    """사업자등록번호 형식 + 체크섬 검증.

    국세청 진위확인 API 연동 전까지 오타를 걸러내는 로컬 검증이다.
    (실제 사업자 존재 여부는 확인하지 못함)
    """
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if len(digits) != 10:
        return False
    weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    total = sum(int(digits[i]) * weights[i] for i in range(9))
    total += (int(digits[8]) * 5) // 10
    check = (10 - (total % 10)) % 10
    return check == int(digits[9])


def validate_settlement_fields(data):
    """등록/수정 시 정산 정보를 검증해 정규화된 dict를 반환.

    반환: (values: dict, error_message: str | None)
    """
    business_type = (data.get('business_type') or '').strip()
    if business_type not in ALLOWED_BUSINESS_TYPES:
        return None, '사업자 구분(사업자/개인)을 선택해주세요.'

    bank_name = (data.get('bank_name') or '').strip()
    account_number = ''.join(
        ch for ch in str(data.get('account_number') or '') if ch.isdigit() or ch == '-'
    ).strip()
    account_holder = (data.get('account_holder') or '').strip()

    if not bank_name or len(bank_name) > 50:
        return None, '은행명을 입력해주세요.'
    if not account_number or len(account_number) > 50:
        return None, '계좌번호를 입력해주세요.'
    if not account_holder or len(account_holder) > 50:
        return None, '예금주를 입력해주세요.'

    values = {
        'business_type': business_type,
        'bank_name': bank_name,
        'account_number': account_number,
        'account_holder': account_holder,
        'biz_reg_no': '',
        'biz_name': '',
        'biz_ceo_name': '',
    }

    if business_type == 'business':
        biz_reg_no = ''.join(ch for ch in str(data.get('biz_reg_no') or '') if ch.isdigit())
        biz_name = (data.get('biz_name') or '').strip()
        biz_ceo_name = (data.get('biz_ceo_name') or '').strip()

        if not is_valid_biz_reg_no(biz_reg_no):
            return None, '사업자등록번호가 올바르지 않습니다. 10자리 숫자를 확인해주세요.'
        if not biz_name or len(biz_name) > 100:
            return None, '상호(사업자등록증상)를 입력해주세요.'
        if not biz_ceo_name or len(biz_ceo_name) > 50:
            return None, '대표자명을 입력해주세요.'

        values.update({
            'biz_reg_no': biz_reg_no,
            'biz_name': biz_name,
            'biz_ceo_name': biz_ceo_name,
        })

    return values, None


def is_placeholder_consultant_profile(consultant):
    """회원가입이 미리 만들어 둔 '빈 껍데기' 프로필인지 판별한다.

    POST /api/auth/signup 은 role='consultant' 이면 Consultant 행을 즉시 만든다
    (specialty='General', match_reason='New Joiner'). 이 행은 "아직 아무것도
    제출하지 않은 상태"를 뜻하므로 중복 등록이 아니다. 이 둘을 구분하지 못해
    정식 프로필 등록이 항상 409 로 막혀 있었다(BUG-E2E-001).

    실제 제출은 iso_experience / regions / recent_projects 를 반드시 채운다
    (아래 필수값 검증 참조). 따라서 셋 다 비어 있으면 제출 전 껍데기로 본다.
    """
    def _blank(value):
        text = (value or '').strip()
        return text in ('', '{}', '[]', 'null')

    return (
        _blank(consultant.iso_experience)
        and _blank(consultant.regions)
        and _blank(consultant.recent_projects)
    )


def register_consultant_validated():
    if g.current_user.role != 'consultant':
        return jsonify({'message': 'Only consultant accounts can register a consultant profile.'}), 403

    user_id = g.current_user.id
    existing = Consultant.query.filter_by(user_id=user_id).first()
    if existing and not is_placeholder_consultant_profile(existing):
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

    # 정산·세금계산서 정보 (등록 시점에 받아두지 않으면 정산 때 사람이 개입해야 함)
    settlement, settlement_error = validate_settlement_fields(data)
    if settlement_error:
        return jsonify({'message': settlement_error}), 400

    # 기본 협력계약 동의 (수수료율·직거래 금지·세금계산서 발행 의무)
    if not data.get('partner_agreement_agreed'):
        return jsonify({'message': '기본 협력계약에 동의해주세요.'}), 400

    # 초대 링크로 들어온 경우 토큰 검증 (없으면 일반 등록으로 허용)
    invite = None
    invite_token = (data.get('invite_token') or '').strip()
    if invite_token:
        invite = ConsultantInvite.query.filter_by(token=invite_token).first()
        if not invite:
            return jsonify({'message': '유효하지 않은 초대 링크입니다.'}), 400
        usable, reason = invite.is_usable()
        if not usable:
            return jsonify({'message': reason}), 400

    profile_values = dict(
        name=name,
        avatar=((data.get('avatar') or name[0]) if name else 'N')[:10],
        specialty=specialty,
        # 한국어 UI 이므로 '10년' 표기로 통일한다. signup() 은 '0년' 으로 넣는데
        # 여기만 '10 years' 라, 가입 경로에 따라 같은 목록에 두 언어가 섞여 보였다.
        # (script.js 는 parseInt(c.experience) 로 숫자만 뽑으므로 두 표기 모두 안전)
        experience=f'{experience_years}년',
        rating=NEW_CONSULTANT_RATING,
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
        business_type=settlement['business_type'],
        biz_reg_no=settlement['biz_reg_no'],
        biz_name=settlement['biz_name'],
        biz_ceo_name=settlement['biz_ceo_name'],
        bank_name=settlement['bank_name'],
        account_number=settlement['account_number'],
        account_holder=settlement['account_holder'],
        partner_agreed_at=datetime.datetime.now(datetime.timezone.utc),
        partner_agreement_version=PARTNER_AGREEMENT_VERSION,
    )

    if existing is not None:
        # 회원가입이 만들어 둔 껍데기 행을 실제 제출값으로 채운다.
        # 새 행을 추가하면 user_id 하나에 Consultant 가 둘이 되어
        # Consultant.query.filter_by(user_id=...).first() 가 어느 쪽을 집을지
        # 알 수 없게 된다(프로젝트 목록·탈퇴 처리가 전부 이 조회에 의존).
        # 껍데기가 실수로 승인된 상태였더라도 verified/status 를 함께 덮어써
        # 실제 제출 내용은 반드시 다시 심사를 거치게 한다.
        new_consultant = existing
        for field, value in profile_values.items():
            setattr(new_consultant, field, value)
    else:
        new_consultant = Consultant(user_id=user_id, **profile_values)
        db.session.add(new_consultant)

    # 초대 링크 소비 (1회용)
    if invite:
        invite.used_at = datetime.datetime.now(datetime.timezone.utc)
        invite.used_by_user_id = user_id

    db.session.commit()

    user = User.query.get(user_id)
    if user and not user.company_name and company_name:
        user.company_name = company_name
    if user and not user.phone and phone:
        user.phone = phone
    db.session.commit()

    # 관리자 통지 (인앱 + 메일 즉시).
    # 지금까지 신규 등록은 관리자가 화면을 직접 새로고침해야만 알 수 있었다.
    # 승인이 늦으면 컨설턴트는 방치됐다고 느낀다 — 심사 대기는 '사람이 기다리는'
    # 이벤트라 하루 뒤 다이제스트로 미루지 않고 즉시 보낸다.
    # 등록은 이미 커밋됐으므로 통지 실패가 등록을 되돌리면 안 된다.
    try:
        notify_admins(
            'consultant_pending_review',
            '신규 전문가 등록 — 심사 대기',
            f'{name}님이 전문가 등록을 신청했습니다. 심사가 필요합니다.',
            link='/admin.html',
            email_spec={
                'subject_label': f'신규 전문가 심사 대기 — {name}',
                'heading': '신규 전문가 등록 신청',
                'summary': '새 전문가 등록 신청이 접수되어 심사를 기다리고 있습니다.',
                'rows': [
                    ('이름', name),
                    ('소속', company_name or '-'),
                    ('전문 분야', specialty or '-'),
                    ('경력', f'{experience_years}년'),
                    ('연락처', phone),
                    ('이메일', email),
                    ('초대 링크', '초대 링크 경유' if invite else '직접 등록'),
                ],
                'cta_label': '관리자 화면에서 심사하기 →',
            },
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        record_email_failure('consultant_pending_review', e, commit=True)

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
    
    # 탈퇴 계정은 이메일이 deleted_<id>@deleted.invalid 로 치환되어 있으므로
    # 여기에 걸리지 않는다 = 탈퇴 후 같은 이메일로 재가입이 가능하다(의도된 정책).
    # 근거는 withdraw_account() 의 주석 참조.
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
            rating=NEW_CONSULTANT_RATING,
            reviews=0,
            match_reason="New Joiner"
        )
        db.session.add(new_consultant)
        db.session.commit()
        
    return jsonify({'message': 'User created successfully'}), 201

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json or {}
    email = data.get('email', '').strip().lower()  # BUG-004 Fix: 이메일 정규화
    password = data.get('password')

    # 무차별 대입(brute force) 방어: IP당 로그인 시도 횟수 제한
    if not check_rate_limit('login', limit=10, window_minutes=15):
        return jsonify({
            'message': '로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요.',
            'code': 'RATE_LIMITED',
        }), 429

    user = User.query.filter_by(email=email).first()

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'message': 'Invalid credentials'}), 401

    # 탈퇴 계정 차단.
    # 탈퇴 시 이메일을 deleted_<id>@deleted.invalid 로 치환하므로 원래 이메일로는
    # 애초에 조회되지 않지만, 익명화가 부분 실패한 경우까지 대비한 방어선이다.
    # 계정 존재 여부를 흘리지 않도록 자격증명 오류와 같은 응답을 준다.
    if getattr(user, 'deleted_at', None) is not None:
        return jsonify({'message': 'Invalid credentials'}), 401


    token = jwt.encode({
        'user_id': user.id,
        'role': user.role,
        'tv': user.token_version or 0,  # 토큰 폐기 검증용 버전
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    }, app.config['SECRET_KEY'], algorithm="HS256")
    
    # Company 정보 조회
    company = Company.query.filter_by(user_id=user.id).first()

    # 컨설턴트 프로필 '제출' 완료 여부.
    # login.html 은 이 값이 거짓이면 consultant_register.html 로 보낸다.
    # 지금까지 이 필드를 응답에 아예 넣지 않아 프론트에서 항상 undefined 였고,
    # 그 결과 프로필을 이미 제출한 컨설턴트도 로그인할 때마다 등록 페이지로
    # 튕겨나갔다. 기준은 '승인 완료'가 아니라 '제출 완료'다 — 심사 대기 중이라고
    # 다시 등록시키면 등록 API 가 (정상적으로) 409 를 돌려줘 막다른 길이 된다.
    # 회원가입이 만들어 둔 껍데기 행은 아직 제출 전이므로 False 로 본다.
    has_consultant_profile = False
    if user.role == 'consultant':
        profile = Consultant.query.filter_by(user_id=user.id).first()
        has_consultant_profile = (
            profile is not None and not is_placeholder_consultant_profile(profile)
        )

    return jsonify({
        'token': token,
        'user': {
            'id': user.id,
            'name': user.name,
            'email': user.email,
            'role': user.role,
            'company_name': user.company_name or '',
            'industry': company.industry if company else '',
            'employees': company.employees if company else '',
            # 컨설턴트가 아니면 항상 False (undefined 로 새어나가지 않게 한다)
            'has_consultant_profile': has_consultant_profile,
        }
    })

# --- Password Reset Endpoints ---
@app.route('/api/auth/request-reset', methods=['POST'])
def request_password_reset():
    """Request a password reset link via email"""
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()

        if not email:
            return jsonify({'message': '이메일을 입력해주세요.'}), 400

        # 메일 폭탄·남용 방지: IP당 재설정 요청 횟수 제한
        if not check_rate_limit('pwreset', limit=5, window_minutes=60):
            return jsonify({
                'message': '요청이 너무 많습니다. 잠시 후 다시 시도해주세요.',
                'code': 'RATE_LIMITED',
            }), 429
        
        # Always return success to prevent email enumeration attacks
        success_message = '입력하신 이메일로 비밀번호 재설정 링크를 발송했습니다. 이메일을 확인해주세요.'
        
        user = User.query.filter_by(email=email).first()
        # 탈퇴 계정에는 재설정 링크를 발급하지 않는다 (링크로 계정이 되살아나면 안 된다).
        # 열거 방지를 위해 응답은 동일하게 성공으로 돌려준다.
        if not user or getattr(user, 'deleted_at', None) is not None:
            print("[Password Reset] Email not found or withdrawn (address redacted)")
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
        # 보안: 재설정 토큰은 로그에 남기지 않는다.
        # (로그 열람자가 임의 계정의 비밀번호를 재설정할 수 있었음)
        print(f"[Password Reset] Link generated for user_id={user.id} (token redacted)")
        
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
    # DB에서 읽어온 값은 tz 정보가 없으므로(naive) UTC로 간주해 비교한다.
    # (이전에는 aware 값과 직접 비교해 TypeError → 500이 발생했다)
    expires_at = reset_token.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    if expires_at is not None and expires_at < datetime.datetime.now(datetime.timezone.utc):
        return jsonify({'message': '링크가 만료되었습니다. 다시 요청해주세요.'}), 400
    
    # Update password
    user = User.query.get(reset_token.user_id)
    if not user or getattr(user, 'deleted_at', None) is not None:
        # 탈퇴 전에 발급된 링크가 남아 있어도 계정을 되살릴 수 없어야 한다.
        return jsonify({'message': '사용자를 찾을 수 없습니다.'}), 404

    user.password_hash = generate_password_hash(new_password)
    # 비밀번호가 바뀌면 기존에 발급된 모든 토큰을 무효화한다
    # (계정 탈취 시 공격자의 세션이 최대 24시간 살아있던 문제 해결)
    user.token_version = (user.token_version or 0) + 1
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
    # 탈퇴 계정은 제외한다. (이름·전화가 익명화되어 매칭될 일이 사실상 없지만,
    #  익명화가 부분 실패했을 때 탈퇴자의 이메일이 노출되는 것을 막는 방어선)
    users = User.query.filter(User.name == name, User.deleted_at.is_(None)).all()
    
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


# ============================================================
# 회원 탈퇴 (소프트 삭제 + 개인정보 익명화)
# ============================================================
# 지금까지 탈퇴 기능이 라우트도 UI 도 없었다. 탈퇴하려면 운영자가 DB 를 직접
# 만져야 했고, 약관·개인정보처리방침에는 "메일로 요청" 이라고 쓸 수밖에 없었다.
#
# 설계 원칙:
#  1) User 행을 지우지 않는다. user.id 를 참조하는 FK 가 10곳이 넘어(models.py
#     User.deleted_at 주석 참조) 하드 삭제하면 상대방의 거래 이력과 감사 로그가
#     함께 망가진다.
#  2) 대신 개인정보(이메일·이름·전화)를 식별 불가능한 값으로 치환한다.
#  3) token_version 을 올려 발급된 JWT 를 즉시 무효화하고, deleted_at 으로
#     로그인·API 접근을 모두 차단한다.
#
# ⚠️ 법률 확인 필요 지점 (임의로 정하지 않았다):
#    전자상거래법상 계약·대금결제 기록 5년 보존 의무와 개인정보보호법 제21조의
#    파기 의무가 충돌하는 구간이 있다. 지금 구현은 "행은 남기고 개인 식별정보만
#    지운다" 는 방어적 선택이며, **거래 이력이 있는 사용자의 보존 범위·기간은
#    변호사 확인 후 확정해야 한다.**

# 탈퇴를 거부하는 프로젝트 상태.
# 계약이 형성됐거나 형성 중인 건은 상대방(기업/컨설턴트)에게 이행 상대가
# 사라지는 것이므로 화면에서 일방적으로 끊게 두면 안 된다.
WITHDRAWAL_BLOCKING_PROJECT_STATUSES = (
    'pending_contract',     # 계약서 초안 검토 중
    'awaiting_signature',   # 한쪽 서명 완료, 상대 서명 대기
    'contracted',           # 계약 체결
    'in_progress',          # 수행 중
)

# 탈퇴 시 자동으로 종료 처리하는 '계약 전' 상태.
# 그대로 두면 상대방이 영영 응답을 기다리고, cron 리마인더도 계속 돈다.
WITHDRAWAL_AUTO_CLOSE_PROJECT_STATUSES = (
    'proposal_pending',
    'proposal_submitted',
    'reviewing',
    'negotiating',
    'planning',
)


def _anonymized_email(user_id):
    """탈퇴 계정의 치환 이메일.

    `.invalid` 는 RFC 2606 이 예약한 도메인이라 실제로 존재할 수 없다.
    익명화 누락으로 어딘가에서 메일을 보내려 해도 외부로 나가지 않는다.
    user_id 를 넣어 `user.email` 의 unique 제약과 충돌하지 않게 한다.
    """
    return f'deleted_{user_id}@deleted.invalid'


def find_withdrawal_blockers(user):
    """탈퇴를 거부해야 하는 사유 목록을 반환한다. 비어 있으면 탈퇴 가능.

    ⚠️ 정산 기능(L1-A)이 붙으면 **미정산 건 검사를 여기에 추가해야 한다.**
       컨설턴트에게 지급하지 않은 외주비가 남은 채로 계정이 익명화되면
       계좌·사업자 정보가 지워져 지급 자체가 불가능해진다. 지금은 정산
       테이블이 존재하지 않으므로 프로젝트 상태로만 판단한다.
    """
    blockers = []

    query = None
    if user.role == 'company':
        query = Project.query.filter(Project.company_id == user.id)
    elif user.role == 'consultant':
        consultant = Consultant.query.filter_by(user_id=user.id).first()
        if consultant:
            query = Project.query.filter(Project.consultant_id == consultant.id)

    if query is not None:
        active = (
            query.filter(
                Project.deleted_at.is_(None),
                Project.status.in_(WITHDRAWAL_BLOCKING_PROJECT_STATUSES),
            )
            .order_by(Project.id)
            .limit(20)
            .all()
        )
        for project in active:
            blockers.append({
                'type': 'project',
                'projectId': project.id,
                'title': project.title,
                'status': project.status,
            })

    return blockers


def _close_open_projects_on_withdrawal(user):
    """탈퇴자의 '계약 전' 프로젝트를 종료 상태로 전이시킨다.

    이 처리가 없으면 상대방은 오지 않을 제안서를 기다리고, cron 의
    제안·서명 리마인더가 탈퇴자를 대상으로 계속 돈다.
    프로젝트 행 자체는 지우지 않는다(대화·협상 이력 보존).
    """
    now = _naive_utc_now()
    closed = 0

    if user.role == 'company':
        projects = Project.query.filter(
            Project.company_id == user.id,
            Project.deleted_at.is_(None),
            Project.status.in_(WITHDRAWAL_AUTO_CLOSE_PROJECT_STATUSES),
        ).all()
        for project in projects:
            project.status = 'cancelled_by_company'
            project.cancelled_at = now
            project.cancelled_reason = '기업 회원 탈퇴로 자동 취소'
            closed += 1
    elif user.role == 'consultant':
        consultant = Consultant.query.filter_by(user_id=user.id).first()
        if consultant:
            projects = Project.query.filter(
                Project.consultant_id == consultant.id,
                Project.deleted_at.is_(None),
                Project.status.in_(WITHDRAWAL_AUTO_CLOSE_PROJECT_STATUSES),
            ).all()
            for project in projects:
                project.status = 'declined_by_consultant'
                project.cancelled_at = now
                project.cancelled_reason = '전문가 회원 탈퇴로 자동 종료'
                closed += 1

    return closed


@app.route('/api/auth/withdraw', methods=['POST'])
@token_required
def withdraw_account():
    """회원 탈퇴 (소프트 삭제 + 개인정보 익명화).

    Body: {"password": "...", "reason": "선택 입력"}
    """
    user = g.current_user

    # 관리자 계정은 화면에서 탈퇴할 수 없다.
    # admin_action_log.admin_user_id 가 감사 기록의 주체이고, 운영자가 실수로
    # 자기 계정을 끊으면 플랫폼을 관리할 수단 자체가 사라진다.
    if user.role == 'admin':
        return jsonify({'message': '관리자 계정은 화면에서 탈퇴할 수 없습니다.'}), 403

    data = request.json or {}
    password = data.get('password') or ''
    reason = str(data.get('reason') or '').strip()[:500]

    # 오조작 방지: 비밀번호 재확인 필수.
    # 남의 기기에 로그인된 세션으로 계정이 날아가는 것을 막는다.
    if not password or not check_password_hash(user.password_hash, password):
        return jsonify({'message': '비밀번호가 일치하지 않습니다.'}), 403

    blockers = find_withdrawal_blockers(user)
    if blockers:
        return jsonify({
            'message': '진행 중인 프로젝트가 있어 탈퇴할 수 없습니다. '
                       '프로젝트를 완료하거나 취소한 뒤 다시 시도해주세요.',
            'code': 'WITHDRAWAL_BLOCKED',
            'blockers': blockers,
        }), 409

    user_id = user.id
    original_role = user.role
    now = _naive_utc_now()

    closed_projects = _close_open_projects_on_withdrawal(user)

    # ── 컨설턴트: 매칭 노출에서 제거 + 프로필 개인정보 삭제 ──
    if original_role == 'consultant':
        consultant = Consultant.query.filter_by(user_id=user_id).first()
        if consultant:
            # 공개 목록·매칭 질의는 (verified == True) OR (status == 'verified')
            # 조건이라 **둘 다** 꺼야 노출에서 빠진다.
            consultant.verified = False
            consultant.status = 'withdrawn'
            consultant.name = '탈퇴한 전문가'
            consultant.email = None
            consultant.phone = None
            consultant.bio = None
            consultant.profile_image_url = None
            consultant.introduction_video_url = None
            consultant.portfolio_files = None
            consultant.pending_changes = None
            consultant.pending_changes_at = None
            # 정산·금융정보는 남길 이유가 없다. 정산 기능이 아직 없어
            # (L1-A 미착수) 보존해야 할 지급 이력 자체가 존재하지 않는다.
            # ⚠️ 정산이 생기면 "지급 완료 건의 증빙 보존" 과 충돌하므로
            #    이 블록을 조건부 삭제로 바꿔야 한다 (변호사 확인 필요).
            consultant.business_type = None
            consultant.biz_reg_no = None
            consultant.biz_name = None
            consultant.biz_ceo_name = None
            consultant.bank_name = None
            consultant.account_number = None
            consultant.account_holder = None

    # ── 기업 프로필의 연락처 이메일도 함께 지운다 ──
    # company.name(회사명)은 남긴다. 상대 컨설턴트의 거래 기록에서
    # "누구와 계약했는지" 가 사라지면 그쪽 이력이 무의미해진다.
    for company in Company.query.filter_by(user_id=user_id).all():
        company.email = None

    # ── 미사용 비밀번호 재설정 토큰 폐기 ──
    try:
        PasswordResetToken.query.filter_by(user_id=user_id, used=False).update({'used': True})
    except Exception as e:
        print(f"[Withdraw] 재설정 토큰 폐기 실패 (계속 진행): {e}")

    # ── 계정 본체 익명화 ──
    user.email = _anonymized_email(user_id)
    user.name = '탈퇴한 회원'
    user.phone = None
    # 로그인 자체를 불가능하게 만든다. deleted_at 검사가 뚫려도 통과할 수 없다.
    user.password_hash = generate_password_hash(secrets.token_urlsafe(32))
    # 발급된 JWT 즉시 무효화 (비밀번호 재설정과 같은 메커니즘 재사용)
    user.token_version = (user.token_version or 0) + 1
    user.deleted_at = now
    # company_name 은 남긴다 — 법인명은 상대방 거래기록의 일부다.
    # ⚠️ 개인사업자의 상호는 개인정보에 해당할 수 있다 (변호사 확인 필요).

    db.session.commit()

    # 통지는 커밋 이후에 별도 트랜잭션으로. 실패해도 탈퇴는 이미 확정이다.
    try:
        notify_admins(
            'member_withdrawal',
            '회원 탈퇴가 처리되었습니다',
            f'user #{user_id} ({original_role}) 탈퇴 — 자동 종료된 프로젝트 {closed_projects}건'
            + (f' / 사유: {reason}' if reason else ''),
            link='/admin.html',
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[Withdraw] 관리자 통지 실패: {type(e).__name__}: {e}")

    return jsonify({
        'message': '탈퇴가 완료되었습니다. 그동안 이용해주셔서 감사합니다.',
        'closedProjects': closed_projects,
    })


# --- Analysis Endpoints (제거됨) ---
# 레거시 /api/analyze (POST·GET)는 2026-07-05 제거.
#  - 프론트엔드에 호출처가 없는 사장 코드였고,
#  - 외부 URL 수집(SSRF)과 유료 AI 호출 비용 위험만 남아 있었다.
# 현재 매칭 퍼널은 규칙 기반 /api/match 를 사용한다.
# 분석 로직 자체는 api/services/ai_service.py 에 보존되어 있다.

# --- Advanced Diagnostic Endpoints (제거됨) ---
# /api/diagnostic/industries · /questions/<ksic_code> · /report 는 2026-08-21 제거.
#  - 자기진단 리포트는 쓰지 않기로 결정된 기능이고,
#  - 프론트엔드(diagnostic.html)는 git 에 커밋된 적이 없어 배포되지 않으며,
#  - /report 는 유료 AI(Gemini) 를 호출하는데 호출량 제한이 걸려 있지 않았다.
#    (check_rate_limit 은 login/pwreset/match 에만 적용)
# 즉 아무도 쓰지 않는 경로가 AI 비용을 무제한으로 태울 수 있는 구멍이었다.
# 레거시 /api/analyze 제거(2026-07-05)와 같은 성격의 조치다.
#
# 자산은 보존한다 — 나중에 되살릴 수 있게 위험면(라우트)만 끊었다:
#   api/services/advanced_diagnostic_service.py, data/risk_dbs/, services/__init__.py export


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

    # 관리자 리드 통지 — **인앱만 즉시, 메일은 승격 배치가 하루 1통으로 묶는다.**
    #
    # 여기는 무인증 공개 경로이고 방어는 IP당 20회/시간 뿐이다. 즉 메일 발송량이
    # 사실상 호출자 통제 하에 있다. 건당 즉시 발송으로 두면 관리자 메일함이
    # 리드로 도배되고, 같은 메일함으로 오는 오류 다이제스트·전문가 심사 대기처럼
    # 정작 조치가 필요한 통지가 묻힌다. 반면 리드는 '기다리는 사람' 이 없어
    # (신규 전문가 등록과 달리) 몇 시간 지연이 무언가를 막지 않는다.
    #
    # emailed_at 을 비워두면 작업 1(미열람 승격)이 이 알림들을 자동으로 하루
    # 1통 다이제스트로 묶어 보낸다. 리드 전용 요약 작업을 따로 만들 필요가 없다.
    #
    # 별도 try/except + 별도 커밋: 통지 실패가 위에서 저장한 AnalysisJob 을
    # 롤백시키면 리드 자체가 사라진다.
    try:
        # 무인증 입력이므로 길이를 잘라서 넣는다 (알림 목록이 한 건에 밀리지 않게).
        lead_company = str(company_name or '기업')[:100]
        lead_standards = ', '.join(str(s)[:40] for s in all_standards[:3]) or '규격 미선택'
        notify_admins(
            'new_match_request',
            '신규 매칭 요청이 접수되었습니다',
            f'{lead_company} — {lead_standards}'
            f'{" 외" if len(all_standards) > 3 else ""} / 전문가 {len(matched_consultants)}명 매칭',
            link='/admin.html',
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[Direct Match] 관리자 리드 알림 실패: {type(e).__name__}: {e}")

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
                (Project.company_id == user_id) | (Project.consultant_id == consultant_id),
                Project.deleted_at.is_(None)
            ).all()
        else:
            projects = Project.query.filter(
                Project.company_id == user_id,
                Project.deleted_at.is_(None)
            ).all()
        
        # 완료된 프로젝트에 이미 리뷰가 있는지 (대시보드의 '리뷰 작성' 진입점 판정).
        # 프로젝트마다 개별 조회하면 목록 길이만큼 DB 왕복이 생기므로 한 번에 모은다.
        reviewed_ids = set()
        completed_ids = [p.id for p in projects if p.status == 'completed']
        if completed_ids:
            reviewed_ids = {
                row[0] for row in db.session.query(Review.project_id)
                .filter(Review.project_id.in_(completed_ids)).all()
            }

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
                # 대시보드 카드가 평점·경력을 '4.9 / 15년' 으로 하드코딩해 두고
                # 있었다. 모든 컨설턴트에게 같은 가짜 숫자가 붙어 있었다는 뜻이다.
                # 리뷰 기능을 켜면서 그 자리에 실제 값을 내려준다.
                'consultant_rating': consultant_info.rating if consultant_info else None,
                'consultant_reviews': consultant_info.reviews if consultant_info else None,
                'consultant_specialty': consultant_info.specialty if consultant_info else None,
                'consultant_experience': consultant_info.experience if consultant_info else None,
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
                'completed_at': p.completed_at.isoformat() if p.completed_at else None,
                # 완료된 프로젝트에만 의미가 있다. 미완료 건에 리뷰 버튼이
                # 뜨는 것을 막기 위해 상태와 함께 판정한다.
                'has_review': p.id in reviewed_ids,
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
    project = get_active_project_or_404(project_id)
    if not is_project_company(project):
        return jsonify({'message': 'Only the project company can delete this project.'}), 403
    
    # Cannot delete contracted projects
    if project.status in ['contracted', 'in_progress', 'completed']:
        return jsonify({'message': '계약된 프로젝트는 삭제할 수 없습니다.'}), 400

    # Soft delete: 프로젝트는 목록에서 사라지지만 마일스톤·대화(Message)는 보존한다.
    # 하드 삭제 시 컨설턴트와 주고받은 협상 이력이 상대 동의 없이 영구 소실되어
    # 분쟁 시 증거가 사라지는 문제가 있었다.
    project.deleted_at = datetime.datetime.now(datetime.timezone.utc)
    project.status = 'deleted'
    db.session.commit()

    return jsonify({'message': '프로젝트가 삭제되었습니다.'})

@app.route('/api/projects/<int:project_id>/proposal/download', methods=['GET'])
@token_required
def download_proposal(project_id):
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
    
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
    project = get_active_project_or_404(project_id)
    
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
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
    
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

            # 이메일 발송
            # 인앱 알림만 있으면 기업이 대시보드에 접속해야만 제안서 도착을 안다.
            # 실패해도 제안서 제출 자체는 성공 응답을 준다(위 커밋은 이미 끝났다).
            if company_user.email:
                try:
                    result = email_service.send_proposal_notification(
                        company_email=company_user.email,
                        company_name=company_user.company_name or company_user.name or '고객',
                        consultant_name=consultant.name,
                        project_title=project.title,
                        proposal_price=project.proposal_price,
                        proposal_duration=project.proposal_duration,
                        dashboard_url=f'{frontend_base_url()}/dashboard.html'
                    )
                    if (result or {}).get('success'):
                        # 이미 메일이 나갔음을 표시한다. 없으면 미열람 승격
                        # 배치가 하루 뒤 같은 건을 다시 메일로 보낸다.
                        notification.emailed_at = _naive_utc_now()
                        db.session.commit()
                except Exception as e:
                    # 세션은 위에서 이미 커밋했으므로 여기서 커밋해도 안전하다.
                    record_email_failure('proposal_notification', e, commit=True)
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
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
    
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
    project = get_active_project_or_404(project_id)
    
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
    project = get_active_project_or_404(project_id)
    
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

# ========================================
# 프로젝트 완료 처리 (상태 머신의 종착점)
# ========================================
# 지금까지 Project.status 는 in_progress 에서 끝났다. completed 로 전이하는 코드가
# 한 줄도 없어 상태 머신이 닫히지 않았고, 그 결과 정산·리뷰 수집·이행 추적처럼
# "프로젝트가 끝났다"를 시작점으로 삼는 기능을 아예 붙일 수 없었다.

# 완료 처리가 가능한 상태.
# contracted 도 포함한다 — 일정 확정(confirm-schedule)은 선택적 단계라
# 짧은 용역은 in_progress 를 거치지 않고 끝나는 경우가 있다.
PROJECT_COMPLETABLE_STATUSES = ('contracted', 'in_progress')

# 마일스톤에 허용되는 상태 (models.Milestone.status 주석과 동일)
MILESTONE_STATUSES = ('pending', 'in_progress', 'completed')


@app.route('/api/projects/<int:project_id>/complete', methods=['POST'])
@token_required
def complete_project(project_id):
    """프로젝트 완료 처리.

    권한: 용역을 **받은** 기업, 또는 관리자.
      컨설턴트에게는 주지 않는다. 완료 전이는 정산의 트리거이자 리뷰 수집의
      시작점이므로, 대금을 받는 쪽이 스스로 "끝났다"고 선언할 수 있으면
      완료 여부가 조작 가능해진다(용역이 끝나지 않았는데 정산이 열린다).
      확인은 돈을 지불하는 쪽이 한다.
      관리자는 분쟁·기업 무응답 같은 예외 처리를 위해 허용한다.
    """
    project = get_active_project_or_404(project_id)

    is_admin = getattr(g.current_user, 'role', None) == 'admin'
    if not (is_admin or is_project_company(project)):
        if is_project_consultant(project):
            return jsonify({
                'message': '완료 확인은 기업이 진행합니다. 기업에 완료 확인을 요청해주세요.'
            }), 403
        return jsonify({'message': '해당 프로젝트를 완료 처리할 권한이 없습니다.'}), 403

    if project.status == 'completed':
        return jsonify({'message': '이미 완료된 프로젝트입니다.'}), 400

    if project.status not in PROJECT_COMPLETABLE_STATUSES:
        return jsonify({'message': '계약이 체결되어 진행 중인 프로젝트만 완료 처리할 수 있습니다.'}), 400

    previous_status = project.status
    now = datetime.datetime.now(datetime.timezone.utc)
    project.status = 'completed'
    project.completed_at = now
    if not project.end_date:
        project.end_date = now

    # 남아 있는 마일스톤도 함께 닫는다. 프로젝트는 끝났는데 마일스톤이
    # pending 으로 남아 있으면 대시보드의 진행률이 영원히 100%가 되지 않는다.
    for milestone in project.milestones:
        if milestone.status != 'completed':
            milestone.status = 'completed'

    # 양측에 인앱 알림
    company_user = User.query.get(project.company_id) if project.company_id else None
    consultant_user_id = get_project_consultant_user_id(project)
    for user_id in filter(None, [company_user.id if company_user else None, consultant_user_id]):
        db.session.add(Notification(
            user_id=user_id,
            type='project_completed',
            title='프로젝트가 완료되었습니다',
            message=f'"{project.title}" 프로젝트가 완료 처리되었습니다.',
            link='/dashboard.html',
        ))

    # ── 리뷰 요청 (L1-C2) ──
    #
    # 배치 3(30a2c7b)이 TODO 로 남긴 지점이다. 완료 시각(completed_at)이
    # 리뷰 수집 기한의 기준이 되고, cron 리마인더도 이 값을 본다.
    #
    # 요청은 **기업에게만** 보낸다. 리뷰 작성 권한이 기업뿐이므로(위
    # handle_project_review 참조) 컨설턴트에게 보내면 할 수 없는 일을 하라는
    # 알림이 된다.
    #
    # ⚠️ 메일 코드를 여기에 붙이지 않는다. 인앱 알림만 만들면 L1-B(19f547f)의
    #    미열람 승격 배치가 하루 뒤 알아서 메일로 올린다(emailed_at 을 비워
    #    두는 것이 그 신호다). 이벤트마다 메일 코드를 붙이지 않는 것이 L1-B
    #    설계의 요지다.
    if company_user and project.consultant_id:
        consultant_name = None
        completed_consultant = Consultant.query.get(project.consultant_id)
        if completed_consultant:
            consultant_name = completed_consultant.name
        db.session.add(Notification(
            user_id=company_user.id,
            type='review_request',
            title='프로젝트는 어떠셨나요?',
            message=(
                f'"{project.title}" 프로젝트가 완료되었습니다. '
                + (f'{consultant_name} 전문가에 대한 ' if consultant_name else '')
                + '평가를 남겨주시면 다른 기업의 전문가 선택에 큰 도움이 됩니다.'
            ),
            link=_review_request_link(project.id),
        ))

    if is_admin:
        # 관리자 대행 완료는 분쟁 처리의 근거가 되므로 감사 로그에 남긴다.
        log_admin_action('complete_project', 'project', str(project.id),
                         {'previous_status': previous_status})

    db.session.commit()

    return jsonify({
        'message': '프로젝트를 완료 처리했습니다.',
        'status': project.status,
        'completed_at': project.completed_at.isoformat(),
    })


@app.route('/api/projects/<int:project_id>/milestones/<int:milestone_id>/status', methods=['POST'])
@token_required
def update_milestone_status(project_id, milestone_id):
    """마일스톤 진행 상태 갱신.

    지금까지 Milestone.status 를 변경하는 코드가 없어 모든 마일스톤이 영원히
    pending 이었다. 즉 대시보드의 진행 표시가 실제 진행과 무관했다.

    권한: 프로젝트 당사자 양측 + 관리자.
      완료 전이(정산 트리거)와 달리 마일스톤은 진행 상황 공유 수단이므로
      실제로 작업하는 컨설턴트가 갱신할 수 있어야 하고, 기업도 정정할 수 있어야 한다.
    """
    project = get_active_project_or_404(project_id)

    is_admin = getattr(g.current_user, 'role', None) == 'admin'
    if not is_admin:
        forbidden = require_project_participant(project)
        if forbidden:
            return forbidden

    milestone = Milestone.query.get_or_404(milestone_id)
    # 다른 프로젝트의 마일스톤 id 를 넣어 남의 데이터를 바꾸지 못하게 한다.
    if not _same_id(milestone.project_id, project.id):
        abort(404)

    if project.status not in ('contracted', 'in_progress', 'completed'):
        return jsonify({'message': '계약이 체결된 프로젝트만 마일스톤을 갱신할 수 있습니다.'}), 400

    data = request.json or {}
    new_status = (data.get('status') or '').strip()
    if new_status not in MILESTONE_STATUSES:
        return jsonify({
            'message': f"status 는 {', '.join(MILESTONE_STATUSES)} 중 하나여야 합니다."
        }), 400

    milestone.status = new_status

    # 계약 직후 첫 마일스톤이 움직이면 프로젝트도 진행 중으로 올린다.
    # (일정 확정 없이 바로 작업을 시작하는 경우를 상태에 반영한다)
    if project.status == 'contracted' and new_status in ('in_progress', 'completed'):
        project.status = 'in_progress'

    db.session.commit()

    return jsonify({
        'message': '마일스톤 상태가 갱신되었습니다.',
        'milestone': milestone.to_dict(),
        'project_status': project.status,
    })

# ========================================
# 리뷰 (L1-C2)
# ========================================
# Consultant.rating / reviews 컬럼은 처음부터 있었고 매칭이 이를 17점
# (WEIGHT_RATING) 으로 반영하는데, **값을 채우는 경로가 코드에 0건**이었다.
# 데이터 공급원이 없으니 전원이 중립값을 받았고, 배점의 17%가 아무 정보도
# 나르지 않았다. 여기서 그 공급원을 만든다.
#
# 설계 요지 세 가지:
#  1) 프로젝트당 1건 (DB unique + 애플리케이션 검사). 없으면 같은 거래로
#     평점을 반복 등록해 조작할 수 있다.
#  2) 작성 권한은 **그 프로젝트의 기업**, **completed 상태에서만**. 관리자도
#     대신 쓰지 못한다 — 관리자가 쓸 수 있으면 평점이 운영자 재량이 된다.
#  3) 삭제는 사용자에게 열지 않는다. 관리자 '숨김' 만 있고 숨긴 건은 평균에서
#     빠진다 (models.Review 주석 참조).

REVIEW_MIN_RATING = 1
REVIEW_MAX_RATING = 5
REVIEW_MAX_COMMENT = 1000

# 작성자가 스스로 고칠 수 있는 기간.
# 오타·오해로 남긴 평가를 영영 못 고치면 컨설턴트에게 부당하고, 반대로 무기한
# 열어두면 "나중에 깎겠다" 는 협상 카드가 된다. 2주는 정산·마무리 대화가
# 끝나는 시점과 대체로 겹친다.
REVIEW_EDIT_WINDOW_DAYS = 14

# 공개 리뷰 목록 1회 조회 상한
REVIEW_LIST_LIMIT = 20
REVIEW_LIST_MAX_LIMIT = 50


def _review_request_link(project_id):
    """리뷰 요청/리마인더 알림의 링크 (= cron 중복 발송 판정 키).

    쿼리 파라미터로 프로젝트를 지정하면 대시보드가 리뷰 작성 모달을 바로 연다.
    링크에 프로젝트 id 가 들어가야 _recently_notified_links 가 "같은 종류의
    다른 프로젝트 알림" 까지 잘못 억제하지 않는다(배치 3의 리마인더와 동일 패턴).
    """
    return f'/dashboard.html?review={project_id}'


def recalculate_consultant_rating(consultant_id):
    """Consultant.rating / reviews 를 Review 행에서 **전부 다시** 계산한다.

    증분 갱신(rating = (rating*n + new)/(n+1))을 쓰지 않는 이유:
      · 부동소수 오차가 등록할 때마다 누적된다.
      · 숨김/숨김 해제를 되돌릴 때 원래 값으로 돌아오지 못한다.
      · 어떤 경로가 갱신을 빠뜨리면 두 값이 영구히 어긋나고, 어긋난 것을
        알아챌 방법이 없다.
    단일 진실은 Review 테이블이고 이 두 컬럼은 조회용 캐시다.

    저장하는 값은 **가공하지 않은 산술평균**이다. 신뢰도 가중(베이지안 수축)은
    매칭 정책이라 services/matching_service.py 에서 조회 시점에 건다.
    여기에 미리 수축을 반영하면 프로필에 "4.64" 같은, 어떤 리뷰와도 일치하지
    않는 숫자가 표시된다.
    """
    consultant = Consultant.query.get(consultant_id)
    if not consultant:
        return None

    count, average = db.session.query(
        func.count(Review.id), func.avg(Review.rating)
    ).filter(
        Review.consultant_id == consultant_id,
        Review.hidden_at.is_(None),
    ).one()

    count = int(count or 0)
    consultant.reviews = count
    # 보이는 리뷰가 0건이면 신규 컨설턴트와 같은 상태로 되돌린다.
    # (매칭의 평점 블록은 reviews == 0 이면 rating 값을 쓰지 않으므로
    #  여기에 무엇을 넣든 점수는 같지만, 화면에는 "평가 없음" 으로 나가야 한다)
    consultant.rating = round(float(average), 2) if count else NEW_CONSULTANT_RATING
    return consultant


def _mask_company_name(name):
    """기업명 마스킹 — 첫 글자만 남긴다. '삼성전자' → '삼***'

    공개 프로필에 작성자 기업명을 그대로 쓰면 "어느 기업이 어느 컨설턴트에게
    무엇을 맡겼는가" 라는 거래 관계가 드러난다. 컨설팅 발주 사실 자체를
    알리고 싶지 않은 기업이 많고(인증 준비 중이라는 신호가 된다), 리뷰를
    쓰는 데 마찰이 된다.

    ⚠️ 한계: 컨설턴트 풀이 작으면 마스킹해도 재식별이 가능하다(글자 수 + 규격 +
       시기). 완전 익명이 필요해지면 여기만 '기업 고객' 고정으로 바꾸면 된다.
    """
    text = (name or '').strip()
    if not text:
        return ''
    if len(text) == 1:
        return text + '*'
    return text[0] + '*' * (len(text) - 1)


def _review_author_label(user):
    """리뷰 작성자 표기.

    탈퇴 회원은 '탈퇴한 기업' 으로 고정한다. L1-C1(d6c37a9)의 탈퇴 처리는
    "탈퇴자 본인의 개인정보는 익명화로 지우고 행은 남긴다" 가 원칙이므로
    리뷰도 같게 다룬다 — **리뷰 행과 평점은 남기고 작성자 식별정보만 지운다.**

    리뷰를 지우지 않는 이유: 리뷰는 컨설턴트의 실적 기록이다. 작성자가
    탈퇴했다고 지우면 컨설턴트의 평점이 자기와 무관한 이유로 흔들리고,
    "리뷰를 남긴 뒤 탈퇴하면 리뷰가 사라진다" 는 조작 경로가 열린다.
    """
    if user is None:
        return '기업 고객'
    if getattr(user, 'deleted_at', None) is not None:
        return '탈퇴한 기업'
    masked = _mask_company_name(user.company_name) or _mask_company_name(user.name)
    return masked or '기업 고객'


def _review_author_labels(reviews):
    """작성자 id -> 표기. 리뷰 수만큼 User 를 개별 조회하지 않도록 한 번에 모은다."""
    ids = {r.company_id for r in reviews if r.company_id}
    if not ids:
        return {}
    users = User.query.filter(User.id.in_(ids)).all()
    return {u.id: _review_author_label(u) for u in users}


def _validate_review_payload(data):
    """(rating, comment, 에러응답) 을 반환한다."""
    raw_rating = data.get('rating')
    try:
        # bool 은 int 의 서브클래스라 True 가 1점으로 통과한다. 명시적으로 막는다.
        if isinstance(raw_rating, bool):
            raise ValueError('bool')
        rating = int(raw_rating)
        # int(3.5) 는 예외 없이 3 을 돌려준다. 그대로 두면 사용자가 보낸 값이
        # 조용히 내림되어 저장된다 — 별 4.7 을 눌렀는데 4점이 기록되는 식이다.
        # 소수점이 붙은 평점은 잘라 쓰지 말고 거부한다.
        if float(raw_rating) != rating:
            raise ValueError('not an integer')
    except (TypeError, ValueError):
        return None, None, (jsonify({
            'message': f'평점은 {REVIEW_MIN_RATING}~{REVIEW_MAX_RATING} 사이의 정수여야 합니다.'
        }), 400)

    if not (REVIEW_MIN_RATING <= rating <= REVIEW_MAX_RATING):
        return None, None, (jsonify({
            'message': f'평점은 {REVIEW_MIN_RATING}~{REVIEW_MAX_RATING} 사이의 정수여야 합니다.'
        }), 400)

    comment = str(data.get('comment') or '').strip()[:REVIEW_MAX_COMMENT] or None
    return rating, comment, None


def _review_editable_until(review):
    created = _as_naive_utc(review.created_at) or _naive_utc_now()
    return created + datetime.timedelta(days=REVIEW_EDIT_WINDOW_DAYS)


def _review_owner_dict(review):
    """작성자 본인에게 돌려주는 형태 (수정 가능 여부 포함)."""
    data = review.to_dict(author_label='나')
    data['editableUntil'] = _review_editable_until(review).isoformat()
    data['editable'] = (
        review.hidden_at is None and _naive_utc_now() <= _review_editable_until(review)
    )
    data['hidden'] = review.hidden_at is not None
    return data


@app.route('/api/projects/<int:project_id>/review', methods=['GET', 'POST', 'PUT'])
@token_required
def handle_project_review(project_id):
    """프로젝트 리뷰 조회/작성/수정.

    권한:
      GET  — 프로젝트 당사자 양측 + 관리자 (컨설턴트도 자기가 받은 평가는 봐야 한다)
      POST/PUT — **그 프로젝트의 기업만**. 컨설턴트·제3자·관리자 모두 불가.
    """
    project = get_active_project_or_404(project_id)
    is_admin = getattr(g.current_user, 'role', None) == 'admin'

    review = Review.query.filter_by(project_id=project.id).first()

    if request.method == 'GET':
        if not (is_admin or is_project_participant(project)):
            return jsonify({'message': '해당 프로젝트에 접근할 권한이 없습니다.'}), 403
        if not review:
            return jsonify({'review': None})
        if is_project_company(project):
            return jsonify({'review': _review_owner_dict(review)})
        # 컨설턴트/관리자에게는 공개 형태로 준다 (작성자 표기 마스킹).
        if review.hidden_at is not None and not is_admin:
            return jsonify({'review': None})
        label = _review_author_label(User.query.get(review.company_id))
        return jsonify({'review': review.to_dict(author_label=label,
                                                 include_admin_fields=is_admin)})

    # ── 작성·수정 공통 가드 ──
    #
    # 관리자도 여기서 막힌다. 완료 전이(complete_project)는 분쟁 처리를 위해
    # 관리자 대행을 허용하지만, 리뷰는 다르다 — 관리자가 대신 쓸 수 있으면
    # 평점이 실제 거래의 기록이 아니라 운영자 재량이 된다.
    if not is_project_company(project):
        if is_project_consultant(project):
            return jsonify({'message': '리뷰는 용역을 의뢰한 기업만 작성할 수 있습니다.'}), 403
        return jsonify({'message': '해당 프로젝트에 리뷰를 작성할 권한이 없습니다.'}), 403

    if project.status != 'completed':
        return jsonify({
            'message': '완료된 프로젝트만 리뷰를 작성할 수 있습니다.'
        }), 400

    if not project.consultant_id:
        # 컨설턴트가 배정되지 않은 프로젝트에는 평가 대상이 없다.
        return jsonify({'message': '평가할 전문가가 지정되지 않은 프로젝트입니다.'}), 400

    data = request.json or {}
    rating, comment, error = _validate_review_payload(data)
    if error:
        return error

    now = _naive_utc_now()

    if request.method == 'POST':
        # 애플리케이션 검사 (DB unique 제약과 이중으로 둔다 — 서버리스 동시 요청은
        # 조회-삽입 사이에서 경합하므로 애플리케이션 검사만으로는 막히지 않는다)
        if review:
            return jsonify({
                'message': '이미 이 프로젝트에 리뷰를 작성했습니다. 수정만 가능합니다.',
                'code': 'REVIEW_ALREADY_EXISTS',
            }), 409

        review = Review(
            project_id=project.id,
            consultant_id=project.consultant_id,
            company_id=g.current_user.id,
            rating=rating,
            comment=comment,
            created_at=now,
            updated_at=now,
        )
        db.session.add(review)
        try:
            db.session.flush()
        except IntegrityError:
            # unique 제약에 걸렸다 = 같은 순간 다른 인스턴스가 먼저 넣었다.
            db.session.rollback()
            return jsonify({
                'message': '이미 이 프로젝트에 리뷰를 작성했습니다. 수정만 가능합니다.',
                'code': 'REVIEW_ALREADY_EXISTS',
            }), 409

        recalculate_consultant_rating(project.consultant_id)

        # 컨설턴트에게 인앱 알림. 메일은 L1-B 의 미열람 승격이 알아서 처리한다.
        consultant_user_id = get_project_consultant_user_id(project)
        if consultant_user_id:
            db.session.add(Notification(
                user_id=consultant_user_id,
                type='review_received',
                title='새 평가가 등록되었습니다',
                message=f'"{project.title}" 프로젝트에 {rating}점 평가가 등록되었습니다.',
                link='/dashboard.html',
            ))

        db.session.commit()
        return jsonify({
            'message': '리뷰가 등록되었습니다.',
            'review': _review_owner_dict(review),
        }), 201

    # ── PUT: 수정 ──
    if not review:
        return jsonify({'message': '수정할 리뷰가 없습니다.'}), 404

    if not _same_id(review.company_id, g.current_user.id):
        return jsonify({'message': '본인이 작성한 리뷰만 수정할 수 있습니다.'}), 403

    if review.hidden_at is not None:
        # 숨겨진 리뷰를 수정해 되살리는 우회로를 막는다.
        return jsonify({'message': '관리자가 숨긴 리뷰는 수정할 수 없습니다.'}), 403

    if now > _review_editable_until(review):
        return jsonify({
            'message': f'리뷰는 작성 후 {REVIEW_EDIT_WINDOW_DAYS}일 이내에만 수정할 수 있습니다.'
        }), 400

    review.rating = rating
    review.comment = comment
    review.updated_at = now

    # 평점이 바뀌었으므로 평균을 다시 계산한다 (Review 행 전체에서 재계산).
    db.session.flush()
    recalculate_consultant_rating(review.consultant_id)
    db.session.commit()

    return jsonify({
        'message': '리뷰가 수정되었습니다.',
        'review': _review_owner_dict(review),
    })


@app.route('/api/consultants/<int:consultant_id>/reviews', methods=['GET'])
def get_consultant_reviews(consultant_id):
    """컨설턴트 공개 리뷰 목록.

    무인증 공개 경로다. 숨긴 리뷰는 절대 나가지 않고, 작성자는 마스킹된
    표기로만 나간다. comment 는 외부 입력이므로 **렌더링하는 쪽이 반드시
    escapeHtml 을 거쳐야 한다** (consultant_profile.html).
    """
    consultant = Consultant.query.get_or_404(consultant_id)

    limit = parse_positive_int(request.args.get('limit')) or REVIEW_LIST_LIMIT
    limit = min(limit, REVIEW_LIST_MAX_LIMIT)
    offset = parse_positive_int(request.args.get('offset')) or 0

    base = Review.query.filter(
        Review.consultant_id == consultant_id,
        Review.hidden_at.is_(None),
    )
    total = base.count()
    rows = base.order_by(Review.created_at.desc(), Review.id.desc()) \
               .offset(offset).limit(limit).all()

    labels = _review_author_labels(rows)

    return jsonify({
        'consultantId': consultant.id,
        # 저장된 산술평균 그대로. 매칭 점수에 쓰이는 신뢰도 가중 평점과는 다르다
        # (수축은 순위 산정용이지 표시용이 아니다 — matching_service 주석 참조).
        'rating': consultant.rating,
        'reviews': consultant.reviews or 0,
        'total': total,
        'items': [
            r.to_dict(author_label=labels.get(r.company_id, '기업 고객')) for r in rows
        ],
    })


@app.route('/api/admin/reviews', methods=['GET'])
@admin_required
def get_admin_reviews():
    """관리자 리뷰 목록 (숨김 처리 대상 탐색용)."""
    limit = min(parse_positive_int(request.args.get('limit')) or 50, 200)
    include_hidden = request.args.get('includeHidden') == '1'

    query = Review.query
    if not include_hidden:
        query = query.filter(Review.hidden_at.is_(None))

    rows = query.order_by(Review.created_at.desc(), Review.id.desc()).limit(limit).all()

    consultant_ids = {r.consultant_id for r in rows}
    names = {
        c.id: c.name for c in Consultant.query.filter(Consultant.id.in_(consultant_ids)).all()
    } if consultant_ids else {}
    labels = _review_author_labels(rows)

    items = []
    for r in rows:
        data = r.to_dict(author_label=labels.get(r.company_id, '기업 고객'),
                         include_admin_fields=True)
        data['consultantName'] = names.get(r.consultant_id)
        items.append(data)

    return jsonify({'reviews': items, 'count': len(items)})


@app.route('/api/admin/reviews/<int:review_id>/hide', methods=['POST'])
@admin_required
def hide_review(review_id):
    """리뷰 숨김/숨김 해제.

    Body: {"hidden": true|false, "reason": "..."}

    숨기면 공개 목록과 **평균 계산 양쪽에서 동시에** 빠진다. 한쪽만 빠지면
    "화면에 없는 리뷰가 평점을 끌어내리는" 상태가 되어 아무도 설명하지 못한다.
    """
    review = Review.query.get_or_404(review_id)
    data = request.json or {}
    hidden = data.get('hidden', True)

    if hidden:
        # 숨김에는 사유를 반드시 남긴다. 사유 없는 숨김은 나중에 분쟁이 났을 때
        # "왜 지웠느냐" 에 답할 근거가 없다 (컨설턴트 거절/취소와 같은 원칙).
        reason, reason_error = get_required_reason(data)
        if reason_error:
            return jsonify({'message': reason_error}), 400
        review.hidden_at = _naive_utc_now()
        review.hidden_reason = reason
        review.hidden_by = g.current_user.id
        action = 'hide_review'
    else:
        review.hidden_at = None
        review.hidden_reason = None
        review.hidden_by = None
        action = 'unhide_review'

    db.session.flush()
    consultant = recalculate_consultant_rating(review.consultant_id)
    log_admin_action(action, 'review', str(review.id), {
        'consultantId': review.consultant_id,
        'rating': review.rating,
        'reason': review.hidden_reason,
    })
    db.session.commit()

    return jsonify({
        'message': '리뷰를 숨겼습니다.' if hidden else '리뷰 숨김을 해제했습니다.',
        'review': review.to_dict(author_label=None, include_admin_fields=True),
        'consultantRating': consultant.rating if consultant else None,
        'consultantReviews': consultant.reviews if consultant else None,
    })


# --- Cancel Consultant Request ---
@app.route('/api/projects/<int:project_id>/cancel', methods=['POST'])
@token_required
def cancel_consultant_request(project_id):
    """특정 컨설턴트에 대한 요청 취소 (Soft Delete + 알림)"""
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
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

    query = Project.query.filter(
        Project.company_id == g.current_user.id,
        Project.deleted_at.is_(None)
    )
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
        Project.status.notin_(['cancelled_by_company', 'not_selected', 'deleted']),
        Project.deleted_at.is_(None)
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
    # 기존 견적 요청 그룹에 컨설턴트를 '추가' 한 것이므로 최초 요청과 같은
    # 견적 요청 알림 메일을 보낸다.
    # (과거에는 존재하지 않는 send_consultant_notification 을 호출해서
    #  AttributeError 가 아래 except 에 삼켜지고 메일이 항상 무발송이었다)
    try:
        company = User.query.get(user_id)
        consultant_user = User.query.get(consultant.user_id) if consultant.user_id else None
        consultant_email = (consultant_user.email if consultant_user else None) or consultant.email
        if consultant_email and company:
            # 같은 세션(견적 요청 그룹)의 진단 맥락을 재사용한다.
            # 없으면 메일을 거르지 않고 '미정'으로 채워 보낸다.
            intake = {}
            if session_id:
                job = AnalysisJob.query.get(session_id)
                if job:
                    intake = job.get_intake_data() or {}

            email_service.send_quote_request_to_consultant(
                consultant_email=consultant_email,
                consultant_name=consultant.name,
                company_name=company.company_name or company.name or '기업',
                industry=intake.get('industry') or '미정',
                standards=normalize_standard_codes(
                    intake.get('selected_standards')
                    or intake.get('all_standards')
                    or intake.get('recommended_standards')
                ),
                issues_summary=intake.get('issues_summary'),
                timeline=intake.get('timeline') or 'flexible',
                budget=intake.get('budget') or 'unknown',
                additional_notes=intake.get('additionalNotes') or intake.get('additional_notes'),
                project_id=new_project.id,
                dashboard_url=f'{frontend_base_url()}/dashboard.html'
            )
    except Exception as e:
        # 위에서 프로젝트 커밋이 끝났으므로 여기서 커밋해도 안전하다.
        record_email_failure('quote_request_added', e, commit=True)

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
    existing_projects = Project.query.filter_by(company_id=user_id, title=title).filter(
        Project.deleted_at.is_(None)
    ).all()
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
        projects = Project.query.filter(
            Project.deleted_at.is_(None)
        ).order_by(Project.created_at.desc()).all()
        
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

        projects = Project.query.filter(
            Project.company_id.is_(None),
            Project.deleted_at.is_(None)
        ).all()
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

        projects = Project.query.filter_by(company_id=company_id).filter(
            Project.deleted_at.is_(None)
        ).all()
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

# --- Consultant Invite Endpoints ---
CONSULTANT_INVITE_TTL_DAYS = 14


def _invite_public_url(token):
    base = (os.environ.get('BASE_URL') or request.host_url.rstrip('/')).rstrip('/')
    return f"{base}/consultant_register.html?invite={token}"


def _invite_to_dict(inv, include_url=False):
    usable, reason = inv.is_usable()
    data = {
        'id': inv.id,
        'name': inv.name,
        'email': inv.email,
        'memo': inv.memo,
        'created_at': inv.created_at.isoformat() if inv.created_at else None,
        'expires_at': inv.expires_at.isoformat() if inv.expires_at else None,
        'used_at': inv.used_at.isoformat() if inv.used_at else None,
        'revoked_at': inv.revoked_at.isoformat() if inv.revoked_at else None,
        'status': 'used' if inv.used_at else ('revoked' if inv.revoked_at else ('usable' if usable else 'expired')),
    }
    if include_url:
        data['invite_url'] = _invite_public_url(inv.token)
    return data


def _send_consultant_invite_email(invite, invite_url):
    """초대 링크를 이메일로 보낸다. 발송 결과를 응답에 담을 dict 로 돌려준다.

    ⚠️ 메일 실패가 초대 생성 자체를 실패시키면 안 된다. 관리자가 URL 을 복사해
       직접 전달하는 기존 경로는 그대로 살아 있어야 하므로, 여기서는 예외를
       바깥으로 내보내지 않고 결과만 보고한다(admin.html 이 이를 표시한다).

    초대는 이미 커밋된 뒤라 record_email_failure(commit=True) 가 안전하다.
    """
    if not invite.email:
        return {'email_sent': False, 'email_skipped': 'no_email'}

    expires_text = invite.expires_at.strftime('%Y-%m-%d %H:%M') if invite.expires_at else ''
    try:
        result = email_service.send_consultant_invite(
            to_email=invite.email,
            invite_name=invite.name,
            invite_url=invite_url,
            expires_at_text=expires_text,
            ttl_days=CONSULTANT_INVITE_TTL_DAYS,
            memo=invite.memo,
        )
    except Exception as e:
        record_email_failure('consultant_invite', e, commit=True)
        return {'email_sent': False, 'email_error': f'{type(e).__name__}'}

    if not (result or {}).get('success'):
        # send_email 은 SMTP 실패를 예외가 아니라 {'success': False} 로 돌려준다.
        message = (result or {}).get('message', 'unknown')
        record_email_failure('consultant_invite', RuntimeError(message), commit=True)
        return {'email_sent': False, 'email_error': str(message)[:200]}

    return {'email_sent': True, 'email_simulated': bool(result.get('simulated'))}


@app.route('/api/admin/consultant-invites', methods=['GET', 'POST'])
@admin_required
def handle_consultant_invites():
    """컨설턴트 초대 링크 발급·조회 (관리자 전용)."""
    if request.method == 'POST':
        data = request.json or {}
        name = (data.get('name') or '').strip()[:100]
        email = (data.get('email') or '').strip().lower()[:120]
        memo = (data.get('memo') or '').strip()[:200]

        if email and not is_valid_email(email):
            return jsonify({'message': '이메일 형식이 올바르지 않습니다.'}), 400

        invite = ConsultantInvite(
            token=secrets.token_urlsafe(24),
            name=name,
            email=email,
            memo=memo,
            created_by=g.current_user.id,
            expires_at=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=CONSULTANT_INVITE_TTL_DAYS),
        )
        db.session.add(invite)
        db.session.commit()
        log_admin_action('create_consultant_invite', 'consultant_invite', str(invite.id),
                         {'name': name, 'email': email})

        payload = _invite_to_dict(invite, include_url=True)
        payload.update(_send_consultant_invite_email(invite, payload['invite_url']))
        return jsonify(payload), 201

    invites = ConsultantInvite.query.order_by(ConsultantInvite.created_at.desc()).limit(100).all()
    return jsonify([_invite_to_dict(i, include_url=True) for i in invites])


@app.route('/api/admin/consultant-invites/<int:invite_id>/revoke', methods=['POST'])
@admin_required
def revoke_consultant_invite(invite_id):
    """미사용 초대 링크 취소."""
    invite = ConsultantInvite.query.get_or_404(invite_id)
    if invite.used_at:
        return jsonify({'message': '이미 사용된 초대는 취소할 수 없습니다.'}), 400
    invite.revoked_at = datetime.datetime.now(datetime.timezone.utc)
    db.session.commit()
    log_admin_action('revoke_consultant_invite', 'consultant_invite', str(invite.id), {})
    return jsonify(_invite_to_dict(invite))


@app.route('/api/consultant-invites/<string:token>', methods=['GET'])
def verify_consultant_invite(token):
    """초대 링크 유효성 확인 (등록 페이지 진입 시 호출, 공개).

    토큰 자체를 아는 사람만 조회할 수 있고, 노출 정보는 표시용 이름뿐이다.
    """
    invite = ConsultantInvite.query.filter_by(token=token).first()
    if not invite:
        return jsonify({'valid': False, 'message': '유효하지 않은 초대 링크입니다.'}), 404
    usable, reason = invite.is_usable()
    if not usable:
        return jsonify({'valid': False, 'message': reason}), 410
    return jsonify({
        'valid': True,
        'name': invite.name,
        'email': invite.email,
        'expires_at': invite.expires_at.isoformat() if invite.expires_at else None,
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
        f'Rejection reason: {reason}',
        reason=reason
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
        f'Revocation reason: {reason}',
        reason=reason
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
        # 평문(등록 폼) / JSON 두 형식이 공존한다 — parse_text_or_json_list 참조.
        'detailedCertifications': parse_text_or_json_list(consultant.detailed_certifications),
        'recentProjects': parse_text_or_json_list(consultant.recent_projects)
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
        posts = Post.query.filter(Post.deleted_at.is_(None)).order_by(Post.created_at.desc()).all()
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
        # 삭제(soft delete)된 글은 공개 조회에서 숨긴다.
        # PUT/DELETE는 관리자 복구 작업을 위해 접근을 허용한다.
        if post.deleted_at is not None:
            abort(404)
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
        # Soft delete: 본문을 보존해 오삭제 시 복구 가능하게 한다.
        log_admin_action('delete_post', 'post', post_id, {'title': post.title})
        post.deleted_at = datetime.datetime.now(datetime.timezone.utc)
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
        
    posts = Post.query.filter(Post.deleted_at.is_(None)).all()
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
    posts = Post.query.filter(Post.deleted_at.is_(None)).order_by(Post.created_at.desc()).all()
    return jsonify([p.to_dict() for p in posts])

@app.route('/api/posts/<int:post_id>', methods=['GET'])
def get_post_detail(post_id):
    """블로그 게시글 상세 조회 (공개)"""
    post = Post.query.get_or_404(post_id)
    if post.deleted_at is not None:
        abort(404)
    return jsonify(post.to_dict())

# --- Health Check ---
def _is_admin_request_safe():
    """require_admin_request() 를 재사용하되, 실패는 조용히 False 로 처리.

    health 는 외부 모니터가 무인증으로 붙는 엔드포인트라
    인증 실패가 응답을 401 로 바꿔서는 안 된다.
    """
    try:
        return require_admin_request() is None
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def _health_verbose_payload():
    """관리자 전용 상세 필드. 실패해도 health 자체는 살아있어야 한다."""
    data = {'email_configured': bool(getattr(email_service, 'is_configured', False))}
    try:
        since = _naive_utc_now() - datetime.timedelta(hours=24)
        recent = ErrorLog.query.filter(ErrorLog.created_at >= since)
        # errors_24h 는 '미처리 예외(500)' 개수라는 뜻을 유지한다.
        # 메일 발송 실패는 요청이 성공한 부분 실패라 warning 으로 따로 센다.
        # (한 칸에 섞으면 500 이 늘었는지 메일만 막혔는지 구분이 안 된다)
        data['errors_24h'] = recent.filter(ErrorLog.level != 'warning').count()
        data['warnings_24h'] = recent.filter(ErrorLog.level == 'warning').count()
        # errors_top 은 카운터가 아니라 '무슨 일이 있었나' 목록이므로 warning 도 함께 싣는다.
        # warnings_24h 가 올라간 이유(어떤 메일이 막혔는지)를 여기서 바로 읽을 수 있어야 한다.
        data['errors_top'] = error_group_summary(since=since, limit=3)
    except Exception as e:
        # error_log 테이블이 아직 없는 배포 직후 등. health 를 500 으로 만들지 않는다.
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[Health] error log summary failed: {type(e).__name__}")
        data['errors_24h'] = None
        data['warnings_24h'] = None
        data['errors_top'] = []

    # cron 이 조용히 멈추면 리마인더·정리·다이제스트가 전부 사라진다.
    # 그런데 "알림이 안 온다"는 사실은 아무도 신고하지 않으므로,
    # 마지막 성공 실행 시각과 경과 시간을 여기서 드러낸다.
    try:
        data['cron'] = _cron_health_payload()
    except Exception as e:
        # cron_run 테이블이 아직 없는 배포 직후 등. health 를 500 으로 만들지 않는다.
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[Health] cron summary failed: {type(e).__name__}")
        data['cron'] = None
    return data


@app.route('/api/health', methods=['GET'])
def health_check():
    """서비스 상태 점검 (공개).

    ⚠️ DB 장애 시 반드시 503 을 반환해야 한다.
       UptimeRobot 같은 외부 모니터는 상태 코드로 장애를 감지하므로
       200 + {"db": "error"} 로는 아무도 장애를 알아채지 못한다.

    ?verbose=1 + 관리자 인증이면 운영 지표를 함께 반환한다.
    관리자가 아니면 verbose 를 무시하고 공개 필드만 준다 (401 로 막지 않는다).
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # 정적 JSON 이 아니라 실제 커넥션을 검증해야 Supabase 장애를 잡을 수 있다.
    try:
        db.session.execute(text('SELECT 1'))
        db_ok = True
    except Exception as e:
        db_ok = False
        try:
            db.session.rollback()
        except Exception:
            pass
        # 접속 문자열·자격증명이 섞일 수 있으므로 예외 메시지는 응답에 담지 않는다.
        print(f"[Health] DB check failed: {type(e).__name__}")

    if not db_ok:
        return jsonify({'status': 'unhealthy', 'timestamp': timestamp, 'db': 'error'}), 503

    payload = {'status': 'healthy', 'timestamp': timestamp, 'db': 'ok'}

    if request.args.get('verbose') in ('1', 'true', 'yes') and _is_admin_request_safe():
        payload.update(_health_verbose_payload())

    return jsonify(payload)

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

# ============================================================
# 문의 접수 (Inquiry)
# ============================================================
# 기존 문의 경로는 푸터의 개인 Gmail mailto: 하나뿐이었다. 이력이 남지 않아
# FAQ 개선의 원천 데이터가 유실되고, 로그인 후 화면에는 경로 자체가 없었다.

INQUIRY_CATEGORIES = {
    'service':     '서비스 이용 문의',
    'matching':    '컨설턴트 매칭 문의',
    'consultant':  '전문가 등록·정산 문의',
    'account':     '계정·회원정보 문의',
    'bug':         '오류 신고',
    'partnership': '제휴·제안',
    'etc':         '기타',
}
INQUIRY_STATUSES = ('received', 'checked', 'done')
INQUIRY_STATUS_LABELS = {'received': '접수', 'checked': '확인', 'done': '완료'}

INQUIRY_MAX_NAME = 100
INQUIRY_MAX_SUBJECT = 200
INQUIRY_MAX_CONTENT = 4000
INQUIRY_MAX_MEMO = 2000


@app.route('/api/inquiries', methods=['POST'])
@token_optional
def create_inquiry():
    """문의 접수. **무인증 공개 경로**.

    로그인 상태면 user_id 와 계정 이메일을 자동으로 연결한다.
    """
    # 스팸 방지. 무인증이라 방어선이 이것뿐이므로 pwreset(5회/시간)보다 더 좁게 잡는다.
    # 관리자 1인당 메일 1통이 즉시 나가므로 발송량이 호출자 통제 하에 놓이지
    # 않도록 하는 것이 목적이다 (ADMIN_NOTIFY_RECIPIENT_LIMIT=5 와 함께 상한이 결정된다).
    if not check_rate_limit('inquiry', limit=3, window_minutes=60):
        return jsonify({
            'message': '문의 접수가 너무 많습니다. 잠시 후 다시 시도해주세요.',
            'code': 'RATE_LIMITED',
        }), 429

    data = request.json or {}
    current_user = getattr(g, 'current_user', None)

    name = str(data.get('name') or '').strip()[:INQUIRY_MAX_NAME]
    email = str(data.get('email') or '').strip().lower()[:120]
    category = str(data.get('category') or 'etc').strip()
    subject = str(data.get('subject') or '').strip()[:INQUIRY_MAX_SUBJECT]
    content = str(data.get('content') or '').strip()[:INQUIRY_MAX_CONTENT]

    # 로그인 사용자는 폼 입력값보다 계정 정보를 우선한다.
    # (사칭 방지 + 답변 보낼 주소를 확실히 하기 위함)
    if current_user is not None:
        email = (current_user.email or email or '').strip().lower()
        if not name:
            name = (current_user.name or '').strip()[:INQUIRY_MAX_NAME]

    if not name:
        return jsonify({'message': '이름을 입력해주세요.'}), 400
    if not is_valid_email(email):
        return jsonify({'message': '유효하지 않은 이메일 형식입니다.'}), 400
    if category not in INQUIRY_CATEGORIES:
        return jsonify({'message': '유효하지 않은 문의 유형입니다.'}), 400
    if not subject:
        return jsonify({'message': '제목을 입력해주세요.'}), 400
    if len(content) < 5:
        return jsonify({'message': '문의 내용을 5자 이상 입력해주세요.'}), 400

    inquiry = Inquiry(
        user_id=current_user.id if current_user is not None else None,
        name=name,
        email=email,
        category=category,
        subject=subject,
        content=content,
        status='received',
    )
    db.session.add(inquiry)
    db.session.commit()

    # 관리자 통지 — 인앱 + 메일 즉시.
    # 리드 통지(/api/match)와 달리 문의는 '답변을 기다리는 사람' 이 있는
    # 이벤트라 하루 뒤 다이제스트로 미루면 응대가 그만큼 늦어진다.
    # 접수는 이미 커밋됐으므로 통지 실패가 접수를 되돌리면 안 된다.
    try:
        notify_admins(
            'new_inquiry',
            '새 문의가 접수되었습니다',
            f'[{INQUIRY_CATEGORIES[category]}] {subject}',
            link='/admin.html',
            email_spec={
                'subject_label': f'새 문의 — {subject}',
                'heading': '새 문의가 접수되었습니다',
                'summary': content[:500],
                'rows': [
                    ('문의 유형', INQUIRY_CATEGORIES[category]),
                    ('이름', name),
                    ('이메일', email),
                    ('회원 여부', f'회원 (user #{inquiry.user_id})' if inquiry.user_id else '비회원'),
                    ('제목', subject),
                ],
                'cta_label': '관리자 화면에서 확인하기 →',
            },
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # record_error_log 가 아니라 record_email_failure 를 쓴다
        # (전자는 첫 동작이 rollback 이라 앞선 변경을 날린다).
        record_email_failure('new_inquiry', e, commit=True)

    return jsonify({
        'message': '문의가 접수되었습니다. 입력하신 이메일로 답변드리겠습니다.',
        'inquiryId': inquiry.id,
    }), 201


@app.route('/api/admin/inquiries', methods=['GET'])
@admin_required
def get_admin_inquiries():
    """문의 목록 조회 (관리자 전용). ?status=received&page=1&per_page=20"""
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = min(max(request.args.get('per_page', 20, type=int) or 20, 1), 100)
    status_filter = (request.args.get('status') or '').strip()

    query = Inquiry.query
    if status_filter in INQUIRY_STATUSES:
        query = query.filter(Inquiry.status == status_filter)

    total = query.count()
    inquiries = (
        query.order_by(Inquiry.created_at.desc(), Inquiry.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # 상태별 건수 — 탭 배지와 필터 버튼에 쓴다.
    counts = {status: 0 for status in INQUIRY_STATUSES}
    for status, count in db.session.query(Inquiry.status, func.count(Inquiry.id)).group_by(Inquiry.status).all():
        if status in counts:
            counts[status] = count

    return jsonify({
        'inquiries': [item.to_dict() for item in inquiries],
        'counts': counts,
        'total': total,
        'page': page,
        'perPage': per_page,
        'totalPages': (total + per_page - 1) // per_page if total else 0,
        'status': status_filter or None,
        'categories': INQUIRY_CATEGORIES,
    })


@app.route('/api/admin/inquiries/<int:inquiry_id>', methods=['POST'])
@admin_required
def update_admin_inquiry(inquiry_id):
    """문의 처리 상태·관리자 메모 갱신 (관리자 전용)."""
    inquiry = Inquiry.query.get_or_404(inquiry_id)
    data = request.json or {}

    changed = {}

    if 'status' in data:
        new_status = str(data.get('status') or '').strip()
        if new_status not in INQUIRY_STATUSES:
            return jsonify({'message': '유효하지 않은 처리 상태입니다.'}), 400
        if new_status != inquiry.status:
            changed['status'] = {'from': inquiry.status, 'to': new_status}
            inquiry.status = new_status

    if 'memo' in data:
        memo = str(data.get('memo') or '').strip()[:INQUIRY_MAX_MEMO]
        if memo != (inquiry.admin_memo or ''):
            changed['memo'] = True
            inquiry.admin_memo = memo or None

    if changed:
        inquiry.updated_at = _naive_utc_now()
        log_admin_action('update_inquiry', 'inquiry', inquiry.id, changed)

    db.session.commit()
    return jsonify(inquiry.to_dict())


# ========================================
# 관리자용 에러 로그 조회 API (관측성)
# ========================================

@app.route('/api/admin/error-logs', methods=['GET'])
@admin_required
def get_error_logs():
    """미처리 예외 로그 조회 (관리자용).

    - groups: fingerprint 별 요약 (발생 횟수 / 최근 발생 / 대표 메시지)
    - logs  : 개별 발생 기록 (페이지네이션)
    쿼리: ?page=1&per_page=20&hours=168&fingerprint=<hash>
    """
    page = request.args.get('page', 1, type=int) or 1
    page = max(page, 1)
    per_page = request.args.get('per_page', 20, type=int) or 20
    per_page = min(max(per_page, 1), 100)
    hours = request.args.get('hours', 168, type=int) or 168
    hours = min(max(hours, 1), 24 * 90)
    fingerprint = (request.args.get('fingerprint') or '').strip()[:64]

    since = _naive_utc_now() - datetime.timedelta(hours=hours)

    query = ErrorLog.query.filter(ErrorLog.created_at >= since)
    if fingerprint:
        query = query.filter(ErrorLog.fingerprint == fingerprint)

    total = query.count()
    logs = (
        query.order_by(ErrorLog.created_at.desc(), ErrorLog.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return jsonify({
        # 목록에는 스택 트레이스를 싣지 않는다 (응답 크기). 상세 API 에서 제공.
        'logs': [log.to_dict() for log in logs],
        'groups': error_group_summary(since=since, limit=10),
        'total': total,
        'page': page,
        'perPage': per_page,
        'totalPages': (total + per_page - 1) // per_page if total else 0,
        'hours': hours,
        'fingerprint': fingerprint or None,
    })


@app.route('/api/admin/error-logs/<int:log_id>', methods=['GET'])
@admin_required
def get_error_log_detail(log_id):
    """개별 에러 상세 (스택 트레이스 포함). 관리자 전용."""
    log = ErrorLog.query.get_or_404(log_id)
    return jsonify(log.to_dict(include_traceback=True))


# ============================================================
# 시간 기반 자동화 (cron)
# ============================================================
# 이 플랫폼에는 지금까지 시간 기반 트리거가 하나도 없었다. 그래서 "N일 무응답
# 리마인더", "만료 정리", "일일 요약" 같은 것이 원리적으로 불가능했다.
#
# 설계 원칙: **엔드포인트는 누가 호출하든 동작해야 한다.**
#   vercel.json 이 레거시 version:2 + builds 형식이라 Vercel 의 crons 속성이
#   실제로 등록되는지 공식 문서상 확인되지 않는다(문서가 둘의 조합을 언급하지
#   않는다). 그래서 스케줄러(드라이버)를 Vercel cron -> GitHub Actions 로
#   바꿔도 이 엔드포인트 코드는 그대로여야 한다. 인증을 헤더 하나
#   (Authorization: Bearer <CRON_SECRET>)로 통일한 이유가 이것이다.
#   Vercel 은 CRON_SECRET 환경변수가 있으면 이 헤더를 자동으로 붙이고,
#   GitHub Actions 의 curl 도 같은 헤더를 쓰면 된다.
#
# 멱등성: Vercel 문서는 "cron 전달은 best effort 이며 같은 실행이 두 번
#   호출될 수 있다"고 명시한다. 따라서 모든 작업은 두 번 돌아도 결과가
#   같아야 한다. 새 컬럼을 추가하는 대신 이미 있는 Notification 행을 조회해
#   최근 같은 알림이 있으면 건너뛰는 방식으로 이를 만족시킨다.
#
# Supabase keepalive: 이 cron 이 매일 DB 를 건드리므로 무료 티어의
#   자동 일시정지(일정 기간 무활동 시 프로젝트 pause)가 자연히 방지된다.
#   별도 keepalive 작업을 만들 필요가 없다.

CRON_JOB_NAME = 'daily'

# Vercel 서버리스 maxDuration 이 60초다. 한 작업이 테이블 전체를 훑다가
# 타임아웃 나면 뒤 작업이 통째로 실행되지 않는다. 작업별 처리 상한을 두고,
# 상한에 걸리면 실행 결과에 truncated 를 남긴다 — 조용한 절단은
# "다 처리했다"로 오해되고, 밀린 건은 영원히 처리되지 않는다.
CRON_MAX_ITEMS_PER_JOB = 200

# 상한을 '실제로 처리한 건수' 가 아니라 '조회한 행 수' 에 걸면 굶는 행이 생긴다.
# 앞의 200건이 전부 "이미 알렸음" 으로 건너뛰어져도 201번째는 영영 차례가 오지 않기
# 때문이다. 그래서 조회는 더 넓게 하고(아래), 상한은 생성 건수에만 건다.
CRON_MAX_SCAN_PER_JOB = 1000

# 다이제스트 수신 관리자 수 상한 (실수로 admin 계정이 늘어나도 메일 폭주 방지)
CRON_ADMIN_RECIPIENT_LIMIT = 5

CRON_STALE_HOURS = 36           # 이보다 오래 성공 실행이 없으면 cron 이 멈춘 것으로 본다
ERROR_DIGEST_HOURS = 24         # 다이제스트가 다루는 기간
ERROR_DIGEST_REPEAT_HOURS = 20  # 같은 관리자에게 이 시간 안에는 다시 보내지 않는다

SIGNATURE_REMINDER_DAYS = 3     # 한쪽만 서명한 채 이만큼 지나면 상대에게 알림
PROPOSAL_REMINDER_DAYS = 3      # 제안 요청 후 이만큼 무응답이면 컨설턴트에게 알림
REMINDER_REPEAT_DAYS = 3        # 같은 건으로 이 기간 안에는 다시 알리지 않는다
# 이보다 오래된 건은 사실상 방치된 요청이다. 계속 리마인더를 보내면 알림이
# 소음이 되어 정작 필요한 알림까지 무시된다.
REMINDER_MAX_AGE_DAYS = 30

# ---- 리뷰 미작성 리마인더 (L1-C2) ----
# 완료 직후 인앱 알림이 한 번 나가고(complete_project), 그래도 안 썼으면
# 이만큼 지난 뒤 **딱 한 번** 더 상기시킨다.
REVIEW_REMINDER_DAYS = 7
# 완료 후 이만큼 지나면 포기한다. 영원히 조르지 않는다 — 한 달이 지나도록
# 안 쓴 사람은 앞으로도 안 쓰고, 계속 보내면 알림 전체가 소음이 되어 정작
# 중요한 알림(서명·제안 요청)까지 함께 무시된다.
#
# ⚠️ '조르기' 만 멈추는 것이지 **작성 자체는 계속 열려 있다.** 늦게 오는
#    리뷰도 컨설턴트에게는 똑같이 유효한 실적이라 마감을 걸 이유가 없다.
REVIEW_REMINDER_GIVEUP_DAYS = 30

INVITE_RETENTION_DAYS = 30      # 만료된 미사용 초대를 이만큼 더 보관한 뒤 정리
ERROR_LOG_RETENTION_DAYS = 90   # 에러 로그 보관 기간 (migrations/README.md 의 수동 DELETE 를 대체)

# ---- 미열람 알림 메일 승격 (L1-B 작업 1) ----
# 생성 후 이만큼 지나도 안 읽힌 알림을 메일로 승격한다.
# 24시간보다 반드시 작아야 한다: 배치는 하루 한 번 도는데 임계값이 24시간이면
# 배치 시각 직후에 생긴 알림은 그날 회차에서 조건을 못 채우고 이틀 뒤에야 나간다.
# 6시간이면 사용자가 인앱으로 먼저 볼 여지를 주면서도 하루 안에 반드시 걸린다.
UNREAD_PROMOTION_HOURS = 6
# 이보다 오래된 미열람 알림은 승격하지 않는다.
# 이 기능을 처음 배포하는 순간 기존 미열람 알림이 전부 emailed_at IS NULL 이다.
# 상한이 없으면 몇 달치 과거 알림이 한꺼번에 메일로 쏟아진다. 게다가 일주일 넘게
# 안 읽은 알림은 지금 메일을 보내도 행동으로 이어지지 않는다.
UNREAD_PROMOTION_MAX_AGE_DAYS = 7
# 다이제스트 본문에 나열할 알림 수. 나머지는 '외 N건' 으로 표기한다.
UNREAD_DIGEST_MAX_ITEMS = 10

# 한 번의 배치 실행에서 보낼 수 있는 메일 총량.
# Vercel 서버리스 maxDuration 이 60초인데 SMTP 는 1통마다 TLS 핸드셰이크를 새로
# 하므로 1통에 1초 안팎이 든다. 작업별 상한(CRON_MAX_ITEMS_PER_JOB=200)만으로는
# 메일을 보내는 작업이 여러 개 겹쳤을 때 시간이 초과되고, 그러면 뒤 작업(정리 등)이
# 통째로 실행되지 않는다. 그래서 발송량은 작업별이 아니라 **실행 전체**로 묶어
# 제한한다. 상한에 걸린 건은 emailed_at 을 채우지 않으므로 다음 회차에 다시 잡힌다.
CRON_MAX_EMAILS_PER_RUN = 40

# 실행 단위 카운터. 모듈 전역이지만 run_daily_cron_jobs() 진입 시 항상 리셋하므로
# 서버리스 인스턴스가 재사용돼도 이전 실행의 잔량이 넘어오지 않는다.
_cron_email_budget = {'remaining': CRON_MAX_EMAILS_PER_RUN}


def _reset_cron_email_budget():
    _cron_email_budget['remaining'] = CRON_MAX_EMAILS_PER_RUN


def _take_cron_email_budget():
    """이번 실행에서 메일을 한 통 더 보내도 되는지. 소비하면 True."""
    if _cron_email_budget['remaining'] <= 0:
        return False
    _cron_email_budget['remaining'] -= 1
    return True


def _send_cron_reminder_email(user_id, title, message, link, purpose):
    """리마인더를 생성 시점에 바로 메일로 보낸다 (작업 2).

    이 두 리마인더는 본질적으로 "당신이 늦고 있다" 는 푸시라, 작업 1의 미열람
    승격(하루 뒤)을 기다리면 그만큼 더 늦어진다.

    Returns:
        발송에 성공했으면 발송 시각(=Notification.emailed_at 에 넣을 값), 아니면 None.
    """
    if not _take_cron_email_budget():
        return None

    user = User.query.get(user_id)
    # 탈퇴 계정은 이메일이 deleted_<id>@deleted.invalid 로 치환되어 있다.
    # 걸러내지 않으면 존재할 수 없는 도메인으로 SMTP 를 시도해 매일
    # 에러 로그(warning)만 쌓인다.
    if user is not None and getattr(user, 'deleted_at', None) is not None:
        return None
    email = (user.email or '').strip() if user else ''
    if not email:
        return None

    try:
        result = email_service.send_reminder_notice(
            to_email=email,
            user_name=user.name,
            title=title,
            message=message,
            action_url=f'{frontend_base_url()}{link}',
        )
    except Exception as e:
        record_email_failure(purpose, e)
        return None

    if not (result or {}).get('success'):
        # send_email 은 SMTP 실패를 예외가 아니라 {'success': False} 로 돌려준다.
        record_email_failure(purpose, RuntimeError((result or {}).get('message', 'unknown')))
        return None

    return _naive_utc_now()


def _has_recent_notification(user_id, notif_type, link, within_hours):
    """같은 사용자에게 같은 건으로 최근에 보낸 알림이 있는지 확인한다.

    cron 은 매일 도는데 조건(예: 아직 서명 안 함)은 계속 참이므로,
    이 검사가 없으면 매일 같은 알림이 나가 사용자가 알림을 통째로 무시하게 된다.
    새 컬럼(last_reminded_at)을 만들지 않고 이미 있는 Notification 행으로
    판정한다 — 스키마 변경 없이 멱등성까지 함께 얻는다.

    link 에 프로젝트 id 를 담아 두면 "같은 종류의 다른 프로젝트 알림"까지
    잘못 억제하는 것을 피할 수 있다.
    """
    since = _naive_utc_now() - datetime.timedelta(hours=within_hours)
    query = Notification.query.filter(
        Notification.user_id == user_id,
        Notification.type == notif_type,
        Notification.created_at >= since,
    )
    if link:
        query = query.filter(Notification.link == link)
    return db.session.query(query.exists()).scalar()


def _recently_notified_links(notif_type, within_hours, limit=CRON_MAX_SCAN_PER_JOB * 2):
    """최근 이 종류로 알림이 나간 link 집합을 한 번에 가져온다.

    후보마다 EXISTS 질의를 날리면 후보 수만큼 DB 왕복이 생긴다. Supabase 는
    네트워크 너머라 왕복 1회가 수십 ms 이고, 수백 건이면 그것만으로
    maxDuration(60초)을 넘긴다. 한 번에 모아 와서 파이썬에서 대조한다.
    """
    since = _naive_utc_now() - datetime.timedelta(hours=within_hours)
    rows = (
        db.session.query(Notification.link)
        .filter(Notification.type == notif_type, Notification.created_at >= since)
        .distinct()
        .limit(limit)
        .all()
    )
    return {row[0] for row in rows}


def _project_reminder_link(project_id):
    """리마인더 알림의 링크 (= 중복 발송 판정 키)."""
    return f'/dashboard.html?project={project_id}'


# ---------- 작업 1. 에러 일일 다이제스트 ----------

def _cron_job_error_digest():
    """지난 24시간 ErrorLog 를 fingerprint 로 묶어 관리자에게 메일 1통.

    0건이면 보내지 않는다. 매일 오는 "이상 없음" 메일은 곧 읽히지 않게 되고,
    그러면 정작 문제가 생긴 날의 메일도 함께 묻힌다.
    """
    since = _naive_utc_now() - datetime.timedelta(hours=ERROR_DIGEST_HOURS)
    base = ErrorLog.query.filter(ErrorLog.created_at >= since)
    error_count = _apply_error_level_filter(base, 'error').count()
    warning_count = _apply_error_level_filter(base, 'warning').count()

    if error_count == 0 and warning_count == 0:
        return {'errors': 0, 'warnings': 0, 'sent': 0, 'skipped': 'no_events'}

    error_groups = error_group_summary(since=since, limit=10, level_filter='error')
    warning_groups = error_group_summary(since=since, limit=10, level_filter='warning')

    admins = (
        User.query.filter(User.role == 'admin', User.deleted_at.is_(None))
        .order_by(User.id).limit(CRON_ADMIN_RECIPIENT_LIMIT).all()
    )
    if not admins:
        return {'errors': error_count, 'warnings': warning_count, 'sent': 0, 'skipped': 'no_admin'}

    base_url = frontend_base_url()
    sent = 0
    failed = 0
    skipped = 0

    for admin in admins:
        # 하루에 두 번 호출돼도(Vercel 의 중복 invoke) 메일은 한 번만 나간다.
        if _has_recent_notification(admin.id, 'error_digest', None, ERROR_DIGEST_REPEAT_HOURS):
            skipped += 1
            continue

        email = (admin.email or '').strip()
        if not email:
            skipped += 1
            continue

        if not _take_cron_email_budget():
            # 발송 예산 소진. emailed_at·알림 행을 만들지 않았으므로 다음 회차에 다시 잡힌다.
            skipped += 1
            continue

        try:
            result = email_service.send_error_digest(
                to_email=email,
                admin_name=admin.name,
                hours=ERROR_DIGEST_HOURS,
                error_count=error_count,
                warning_count=warning_count,
                error_groups=error_groups,
                warning_groups=warning_groups,
                admin_url=f'{base_url}/admin.html',
            )
        except Exception as e:
            record_email_failure('error_digest', e)
            failed += 1
            continue

        if not (result or {}).get('success'):
            # send_email 은 SMTP 실패를 예외가 아니라 {'success': False} 로 돌려준다.
            # 그대로 성공으로 세면 "다이제스트가 나가고 있다"고 착각하게 된다.
            record_email_failure('error_digest', RuntimeError((result or {}).get('message', 'unknown')))
            failed += 1
            continue

        # 발송 사실을 인앱 알림으로도 남긴다. 이 행이 곧 중복 발송 방지 키다.
        # emailed_at 을 함께 채운다 — 안 그러면 미열람 승격 배치가 하루 뒤
        # "확인하지 않은 알림" 다이제스트로 같은 내용을 또 보낸다.
        db.session.add(Notification(
            user_id=admin.id,
            type='error_digest',
            title=f'지난 {ERROR_DIGEST_HOURS}시간 오류 요약',
            message=f'미처리 예외 {error_count}건 / 부분 실패 {warning_count}건이 기록되었습니다.',
            link='/admin.html',
            emailed_at=_naive_utc_now(),
        ))
        sent += 1

    db.session.commit()
    return {
        'errors': error_count,
        'warnings': warning_count,
        'errorGroups': len(error_groups),
        'warningGroups': len(warning_groups),
        'sent': sent,
        'failed': failed,
        'skipped': skipped,
    }


# ---------- 작업 2. 미서명 계약 리마인더 ----------

def _cron_job_signature_reminder():
    """한쪽만 서명한 채 멈춘 계약을, 아직 서명하지 않은 쪽에 알린다.

    awaiting_signature 는 '한 명이 서명했고 상대를 기다리는' 상태다.
    경과 시간의 기준은 먼저 서명한 쪽의 서명 시각 — 기다림이 시작된 시점이다.
    """
    now = _naive_utc_now()
    stale_before = now - datetime.timedelta(days=SIGNATURE_REMINDER_DAYS)
    give_up_before = now - datetime.timedelta(days=REMINDER_MAX_AGE_DAYS)

    # awaiting_signature 는 본질적으로 소수이므로 상태로만 좁혀 가져오고
    # 경과 판정은 파이썬에서 한다(nullable 두 컬럼의 OR 조건을 SQL 로 쓰면
    # 읽기 어렵고 실수하기 쉽다).
    rows = (
        Project.query.filter(
            Project.status == 'awaiting_signature',
            Project.deleted_at.is_(None),
        )
        .order_by(Project.id)
        .limit(CRON_MAX_SCAN_PER_JOB + 1)
        .all()
    )
    scan_truncated = len(rows) > CRON_MAX_SCAN_PER_JOB
    rows = rows[:CRON_MAX_SCAN_PER_JOB]

    already_notified = _recently_notified_links(
        'contract_signature_reminder', REMINDER_REPEAT_DAYS * 24)

    notified = 0
    emailed = 0
    skipped = 0
    deferred = 0

    for project in rows:
        company_signed = _as_naive_utc(project.company_signed_at)
        consultant_signed = _as_naive_utc(project.consultant_signed_at)

        # 정확히 한쪽만 서명한 경우만 다룬다.
        # 양쪽 다 서명했는데 상태가 남아 있으면 데이터 이상이라 건드리지 않고,
        # 아무도 서명하지 않았으면 기다림의 시작 시점을 알 수 없다.
        if bool(company_signed) == bool(consultant_signed):
            skipped += 1
            continue

        waiting_since = company_signed or consultant_signed
        if not (give_up_before <= waiting_since <= stale_before):
            skipped += 1
            continue

        if company_signed:
            # 기업이 서명했다 -> 컨설턴트가 미서명
            target_user_id = get_project_consultant_user_id(project)
            waiting_for = '전문가'
        else:
            target_user_id = project.company_id
            waiting_for = '기업'

        if not target_user_id:
            skipped += 1
            continue

        link = _project_reminder_link(project.id)
        if link in already_notified:
            skipped += 1
            continue

        if notified >= CRON_MAX_ITEMS_PER_JOB:
            # 상한 초과분은 다음 실행으로 넘긴다 (조건이 그대로라 다음 회차에 잡힌다)
            deferred += 1
            continue

        days_waiting = max((now - waiting_since).days, SIGNATURE_REMINDER_DAYS)
        title = '서명하지 않은 계약서가 있습니다'
        message = (
            f'"{project.title}" 계약서에 상대방이 {days_waiting}일 전 서명했지만 '
            f'{waiting_for} 서명이 아직 완료되지 않았습니다.'
        )
        # 생성 시점에 바로 메일을 보내고 emailed_at 을 채운다 (작업 2).
        # 미열람 승격(작업 1)을 기다리면 하루가 더 늦어지고, emailed_at 을
        # 채워두면 그 승격이 같은 건을 중복 발송하지 않는다.
        emailed_at = _send_cron_reminder_email(
            target_user_id, title, message, link, 'contract_signature_reminder')
        db.session.add(Notification(
            user_id=target_user_id,
            type='contract_signature_reminder',
            title=title,
            message=message,
            link=link,
            emailed_at=emailed_at,
        ))
        notified += 1
        if emailed_at:
            emailed += 1

    db.session.commit()
    return {
        'scanned': len(rows),
        'notified': notified,
        'emailed': emailed,
        'skipped': skipped,
        'deferred': deferred,
        'truncated': scan_truncated or deferred > 0,
    }


# ---------- 작업 3. 제안 무응답 리마인더 ----------

def _cron_job_proposal_reminder():
    """제안 요청을 받고 N일째 응답하지 않은 컨설턴트에게 알린다."""
    now = _naive_utc_now()
    stale_before = now - datetime.timedelta(days=PROPOSAL_REMINDER_DAYS)
    give_up_before = now - datetime.timedelta(days=REMINDER_MAX_AGE_DAYS)

    rows = (
        Project.query.filter(
            Project.status == 'proposal_pending',
            Project.deleted_at.is_(None),
            Project.created_at <= stale_before,
            Project.created_at >= give_up_before,
        )
        .order_by(Project.id)
        .limit(CRON_MAX_SCAN_PER_JOB + 1)
        .all()
    )
    scan_truncated = len(rows) > CRON_MAX_SCAN_PER_JOB
    rows = rows[:CRON_MAX_SCAN_PER_JOB]

    already_notified = _recently_notified_links('proposal_reminder', REMINDER_REPEAT_DAYS * 24)

    notified = 0
    emailed = 0
    skipped = 0
    deferred = 0

    for project in rows:
        target_user_id = get_project_consultant_user_id(project)
        if not target_user_id:
            skipped += 1
            continue

        link = _project_reminder_link(project.id)
        if link in already_notified:
            skipped += 1
            continue

        if notified >= CRON_MAX_ITEMS_PER_JOB:
            deferred += 1
            continue

        created = _as_naive_utc(project.created_at) or now
        days_waiting = max((now - created).days, PROPOSAL_REMINDER_DAYS)
        title = '응답하지 않은 견적 요청이 있습니다'
        message = (
            f'"{project.title}" 요청을 받은 지 {days_waiting}일이 지났습니다. '
            '제안서를 보내거나 거절해주세요.'
        )
        # 작업 2: 생성 시점 즉시 발송 + emailed_at 표기 (서명 리마인더와 동일한 이유)
        emailed_at = _send_cron_reminder_email(
            target_user_id, title, message, link, 'proposal_reminder')
        db.session.add(Notification(
            user_id=target_user_id,
            type='proposal_reminder',
            title=title,
            message=message,
            link=link,
            emailed_at=emailed_at,
        ))
        notified += 1
        if emailed_at:
            emailed += 1

    db.session.commit()
    return {
        'scanned': len(rows),
        'notified': notified,
        'emailed': emailed,
        'skipped': skipped,
        'deferred': deferred,
        'truncated': scan_truncated or deferred > 0,
    }


# ---------- 작업 4. 리뷰 미작성 리마인더 ----------

def _cron_job_review_reminder():
    """완료 후 N일이 지나도록 리뷰를 쓰지 않은 기업에게 **1회** 상기시킨다.

    배치 3의 리마인더 패턴을 그대로 따른다(_recently_notified_links 로 중복
    방지, 생성 건수 기준 상한, 상한 초과 시 truncated).

    '1회' 를 새 컬럼 없이 보장하는 방법:
      후보는 `completed_at >= now - GIVEUP` 로 이미 한정되어 있다. 리마인더는
      완료 이후에만 생성되므로, GIVEUP 기간만큼만 거슬러 알림 이력을 조회하면
      "이 프로젝트로 리마인더를 보낸 적이 있는가" 를 빠짐없이 판정할 수 있다.
      즉 조회 창과 포기 기한이 같아야 한다 — 둘 중 하나만 늘리면 중복이 샌다.

    complete_project 가 만든 최초 요청 알림(type='review_request')과는 type 이
    달라 서로를 억제하지 않는다. 의도한 것이다: 요청 1회 + 리마인더 1회 = 총 2회.
    """
    now = _naive_utc_now()
    stale_before = now - datetime.timedelta(days=REVIEW_REMINDER_DAYS)
    give_up_before = now - datetime.timedelta(days=REVIEW_REMINDER_GIVEUP_DAYS)

    rows = (
        Project.query.filter(
            Project.status == 'completed',
            Project.deleted_at.is_(None),
            Project.consultant_id.isnot(None),
            Project.completed_at <= stale_before,
            Project.completed_at >= give_up_before,
        )
        .order_by(Project.id)
        .limit(CRON_MAX_SCAN_PER_JOB + 1)
        .all()
    )
    scan_truncated = len(rows) > CRON_MAX_SCAN_PER_JOB
    rows = rows[:CRON_MAX_SCAN_PER_JOB]

    already_notified = _recently_notified_links(
        'review_reminder', REVIEW_REMINDER_GIVEUP_DAYS * 24)

    # 이미 리뷰가 있는 프로젝트는 제외한다. 후보마다 EXISTS 를 날리면 후보 수만큼
    # DB 왕복이 생기므로(배치 3의 _recently_notified_links 와 같은 이유) 한 번에 모은다.
    reviewed_project_ids = set()
    if rows:
        reviewed_project_ids = {
            row[0] for row in db.session.query(Review.project_id)
            .filter(Review.project_id.in_([p.id for p in rows])).all()
        }

    notified = 0
    emailed = 0
    skipped = 0
    deferred = 0

    for project in rows:
        if project.id in reviewed_project_ids:
            skipped += 1
            continue

        target_user_id = project.company_id
        if not target_user_id:
            skipped += 1
            continue

        link = _review_request_link(project.id)
        if link in already_notified:
            skipped += 1
            continue

        if notified >= CRON_MAX_ITEMS_PER_JOB:
            deferred += 1
            continue

        title = '완료된 프로젝트의 평가를 기다리고 있습니다'
        message = (
            f'"{project.title}" 프로젝트가 완료된 지 {REVIEW_REMINDER_DAYS}일이 지났습니다. '
            '1분이면 되는 평가가 다음 기업의 전문가 선택을 좌우합니다.'
        )
        emailed_at = _send_cron_reminder_email(
            target_user_id, title, message, link, 'review_reminder')
        db.session.add(Notification(
            user_id=target_user_id,
            type='review_reminder',
            title=title,
            message=message,
            link=link,
            emailed_at=emailed_at,
        ))
        notified += 1
        if emailed_at:
            emailed += 1

    db.session.commit()
    return {
        'scanned': len(rows),
        'notified': notified,
        'emailed': emailed,
        'skipped': skipped,
        'deferred': deferred,
        'truncated': scan_truncated or deferred > 0,
    }


# ---------- 작업 5. 미열람 알림 메일 승격 ----------

def _cron_job_unread_digest():
    """읽지 않은 채 방치된 인앱 알림을 사용자별 메일 1통으로 묶어 보낸다.

    **이 플랫폼의 알림은 13종 이벤트에 대해 잘 생성되지만 전부 인앱 전용이었다.**
    사용자가 사이트에 접속하지 않으면 아무것도 모르고, 퍼널이 거기서 멈춘다.
    이벤트마다 메일 코드를 붙이는 대신 이미 쌓이고 있는 Notification 행을
    하루 한 번 메일로 승격시킨다 — **하나의 메커니즘으로 13종 전부를 덮는다.**

    설계 규칙 세 가지:
      1) 사용자당 1통. 미열람 5건에 메일 5통을 보내면 알림이 소음이 되어
         정작 중요한 메일까지 무시된다.
      2) emailed_at 으로 재발송 방지. cron 은 매일 도는데 "안 읽음" 조건은
         계속 참이므로 이 표식이 없으면 매일 같은 메일이 나간다.
      3) 이미 다른 경로로 메일이 나간 알림(심사 결과·리마인더·오류 다이제스트)은
         생성 시점에 emailed_at 이 채워져 있어 여기서 자연히 제외된다.
    """
    now = _naive_utc_now()
    ready_before = now - datetime.timedelta(hours=UNREAD_PROMOTION_HOURS)
    too_old_before = now - datetime.timedelta(days=UNREAD_PROMOTION_MAX_AGE_DAYS)

    rows = (
        Notification.query
        .filter(
            # is_read 는 nullable 이라 is_(False) 만으로는 NULL 행을 놓친다.
            db.or_(Notification.is_read.is_(False), Notification.is_read.is_(None)),
            Notification.emailed_at.is_(None),
            Notification.created_at <= ready_before,
            Notification.created_at >= too_old_before,
        )
        # 사용자별로 묶어야 하므로 user_id 로 먼저 정렬한다.
        # 상한에 걸려 잘리더라도 한 사용자의 알림이 두 회차로 쪼개질 뿐,
        # 각 회차는 여전히 '사용자당 1통' 을 지킨다.
        .order_by(Notification.user_id, Notification.created_at.desc())
        .limit(CRON_MAX_SCAN_PER_JOB + 1)
        .all()
    )
    scan_truncated = len(rows) > CRON_MAX_SCAN_PER_JOB
    rows = rows[:CRON_MAX_SCAN_PER_JOB]

    grouped = {}
    for notification in rows:
        grouped.setdefault(notification.user_id, []).append(notification)

    base_url = frontend_base_url()
    sent = 0
    failed = 0
    skipped = 0
    deferred = 0
    promoted = 0

    for user_id, notifications in grouped.items():
        if sent >= CRON_MAX_ITEMS_PER_JOB:
            deferred += 1
            continue

        user = User.query.get(user_id)
        # 탈퇴 계정은 발송 대상에서 제외한다 (_send_cron_reminder_email 과 같은 이유).
        if user is not None and getattr(user, 'deleted_at', None) is not None:
            user = None
        email = (user.email or '').strip() if user else ''
        if not email:
            # 발송할 곳이 없다. emailed_at 을 거짓으로 채우지 않는다 —
            # 이 알림들은 MAX_AGE 를 넘기면 조회 대상에서 자연히 빠진다.
            skipped += 1
            continue

        if not _take_cron_email_budget():
            deferred += 1
            continue

        items = [{
            'title': n.title,
            'message': n.message,
            # 상대 경로를 그대로 넣으면 메일 클라이언트에서 링크가 깨진다.
            'link': f'{base_url}{n.link}' if (n.link or '').startswith('/') else (n.link or ''),
        } for n in notifications[:UNREAD_DIGEST_MAX_ITEMS]]

        try:
            result = email_service.send_notification_digest(
                to_email=email,
                user_name=user.name,
                items=items,
                total_count=len(notifications),
                dashboard_url=f'{base_url}/dashboard.html',
            )
        except Exception as e:
            record_email_failure('unread_notification_digest', e)
            failed += 1
            continue

        if not (result or {}).get('success'):
            # send_email 은 SMTP 실패를 예외가 아니라 {'success': False} 로 돌려준다.
            # 그대로 성공으로 세면 emailed_at 이 찍혀 알림이 영영 메일로 안 나간다.
            record_email_failure(
                'unread_notification_digest',
                RuntimeError((result or {}).get('message', 'unknown')),
            )
            failed += 1
            continue

        # 발송에 성공한 것만 표기한다. 실패한 건은 emailed_at 이 비어 있어
        # 다음 회차에 다시 잡힌다.
        for notification in notifications:
            notification.emailed_at = now
        promoted += len(notifications)
        sent += 1

    db.session.commit()
    return {
        'candidates': len(rows),
        'users': len(grouped),
        'sent': sent,
        'promoted': promoted,
        'failed': failed,
        'skipped': skipped,
        'deferred': deferred,
        'truncated': scan_truncated or deferred > 0,
    }


# ---------- 작업 6. 만료·보관기간 정리 ----------

def _cron_job_expired_cleanup():
    """만료된 미사용 초대와 보관기간이 지난 에러 로그를 정리한다.

    사용(used_at)·취소(revoked_at)된 초대는 지우지 않는다 — 누가 언제 어떤
    링크로 들어왔는지는 감사 이력이다. 지우는 것은 '한 번도 쓰이지 않은 채
    만료된' 토큰뿐이고, 그마저도 만료 후 INVITE_RETENTION_DAYS 를 더 기다린다.

    is_usable() 과 모순되지 않는다: 만료 시점부터 이미 사용 불가이고, 정리는
    그보다 한참 뒤에 일어난다. (만료 직후 접근하면 지금처럼 '만료된 초대
    링크입니다' 410 이 그대로 나온다.)
    """
    now = _naive_utc_now()
    invite_cutoff = now - datetime.timedelta(days=INVITE_RETENTION_DAYS)

    stale_invites = (
        ConsultantInvite.query.filter(
            ConsultantInvite.used_at.is_(None),
            ConsultantInvite.revoked_at.is_(None),
            ConsultantInvite.expires_at < invite_cutoff,
        )
        .order_by(ConsultantInvite.id)
        .limit(CRON_MAX_ITEMS_PER_JOB + 1)
        .all()
    )
    invites_truncated = len(stale_invites) > CRON_MAX_ITEMS_PER_JOB
    stale_invites = stale_invites[:CRON_MAX_ITEMS_PER_JOB]
    for invite in stale_invites:
        db.session.delete(invite)

    # 에러 로그 보관기간 정리.
    # ErrorLog 는 카운터 UPDATE 대신 행을 그대로 쌓는 구조라 계속 늘어나기만 한다.
    # migrations/README.md 가 "보관 기간 정리는 cron 인프라가 생기는 배치 3에서
    # 붙인다"고 남겨둔 항목이 이것이다.
    log_cutoff = now - datetime.timedelta(days=ERROR_LOG_RETENTION_DAYS)
    old_log_ids = [
        row.id for row in (
            ErrorLog.query.with_entities(ErrorLog.id)
            .filter(ErrorLog.created_at < log_cutoff)
            .order_by(ErrorLog.id)
            .limit(CRON_MAX_ITEMS_PER_JOB + 1)
            .all()
        )
    ]
    logs_truncated = len(old_log_ids) > CRON_MAX_ITEMS_PER_JOB
    old_log_ids = old_log_ids[:CRON_MAX_ITEMS_PER_JOB]
    if old_log_ids:
        ErrorLog.query.filter(ErrorLog.id.in_(old_log_ids)).delete(synchronize_session=False)

    db.session.commit()
    return {
        'invitesDeleted': len(stale_invites),
        'errorLogsDeleted': len(old_log_ids),
        'truncated': invites_truncated or logs_truncated,
    }


# 실행 순서 주의:
#  - 다이제스트가 에러 로그를 읽은 뒤에 정리 작업이 지운다.
#  - unread_digest 는 리마인더 뒤에 둔다. 리마인더가 방금 만든 알림은 이미
#    emailed_at 이 찍혀 있어 어차피 제외되지만, "즉시 발송 경로가 먼저,
#    남은 것을 승격이 쓸어 담는다" 는 순서가 코드에도 드러나는 편이 낫다.
CRON_DAILY_JOBS = (
    'error_digest',
    'signature_reminder',
    'proposal_reminder',
    'review_reminder',
    'unread_digest',
    'expired_cleanup',
)


def _resolve_cron_job(name):
    """작업 이름 -> 함수. 호출 시점에 모듈 전역에서 찾는다(늦은 바인딩).

    튜플에 함수 객체를 직접 담아두면 참조가 모듈 로드 시점에 고정되어,
    "한 작업이 터져도 나머지가 계속 도는가" 를 테스트로 확인할 수 없다.
    """
    return globals()[f'_cron_job_{name}']


def run_daily_cron_jobs():
    """등록된 작업을 순서대로 실행하고 (요약, 실패목록) 을 반환한다.

    한 작업이 예외를 던져도 나머지는 계속 실행한다. 리마인더 하나가 깨졌다고
    만료 정리와 다이제스트까지 멈추면, 고장 하나가 인프라 전체를 멈춘다.
    각 작업은 스스로 커밋하므로 뒤 작업의 실패가 앞 작업의 결과를 되돌리지 않는다.
    """
    results = {}
    failures = []

    # 발송 예산은 실행 단위다. 리셋을 빠뜨리면 재사용된 서버리스 인스턴스에서
    # 두 번째 실행부터 메일이 한 통도 안 나간다.
    _reset_cron_email_budget()

    for name in CRON_DAILY_JOBS:
        try:
            results[name] = _resolve_cron_job(name)()
        except Exception as e:
            # 실패한 작업이 세션을 오염시킨 채로 넘기면 다음 작업이 연쇄 실패한다.
            try:
                db.session.rollback()
            except Exception:
                pass
            results[name] = {'error': f'{type(e).__name__}: {e}'[:300]}
            failures.append(name)
            # ErrorLog 에도 남겨 관리자 화면·다음 다이제스트에 드러나게 한다.
            # (record_error_log 는 첫 동작이 rollback 이지만 이미 롤백했고
            #  각 작업이 자기 결과를 커밋한 뒤이므로 잃을 변경이 없다.)
            record_error_log(e, None)

    return results, failures


def _cron_trigger_source():
    """호출 주체 추정. Vercel cron 은 User-Agent 가 'vercel-cron/1.0' 이다.

    이 값이 있어야 "레거시 vercel.json(builds 형식)에서 Vercel crons 가 실제로
    등록되는가" 를 배포 후 실측으로 판별할 수 있다. 공식 문서가 둘의 조합에
    대해 아무 말도 하지 않으므로 추측 대신 기록으로 답한다.
    """
    agent = (request.headers.get('User-Agent') or '').lower()
    if 'vercel-cron' in agent:
        return 'vercel-cron'
    return 'external-cron'


def _authorize_cron_request():
    """cron 엔드포인트 인증. (triggered_by, 거부응답) 을 반환한다.

    허용 경로 두 가지:
      1) Authorization: Bearer <CRON_SECRET>  — 스케줄러(Vercel cron / GitHub Actions)
      2) 관리자 JWT                            — 배포 직후 수동 검증, cron 이 죽었을 때 수동 실행

    CRON_SECRET 이 설정되지 않았다면 1번 경로는 아예 열리지 않는다.
    빈 문자열을 secret 으로 받아들이면 누구나 배치를 돌릴 수 있게 되고,
    그 배치는 메일을 보내고 DB 행을 지운다. 미설정 시 거부가 안전한 쪽이다.
    (그 결과 cron 이 매일 401 로 튕기게 되는데, 이것은 /api/health?verbose=1 의
     '마지막 성공 실행 경과 시간' 이 늘어나면서 드러난다.)
    """
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.split(' ', 1)[1].strip() if auth_header.startswith('Bearer ') else ''

    secret = (os.environ.get('CRON_SECRET') or '').strip()
    if secret and token and secrets.compare_digest(token, secret):
        return _cron_trigger_source(), None

    # 관리자 JWT 로도 실행할 수 있게 한다.
    # (_is_admin_request_safe 는 인증 실패를 조용히 False 로 만든다)
    if token and _is_admin_request_safe():
        return 'admin', None

    if not secret:
        return None, (jsonify({
            'message': 'CRON_SECRET 이 설정되지 않아 요청을 거부했습니다. '
                       '환경변수를 설정하거나 관리자 토큰으로 호출하세요.'
        }), 401)

    return None, (jsonify({'message': '유효하지 않은 cron 인증입니다.'}), 401)


@app.route('/api/cron/daily', methods=['GET', 'POST'])
def run_daily_cron():
    """일일 배치 실행 (드라이버 비종속).

    Vercel cron 은 GET 으로만 호출하므로 GET 을 반드시 열어둔다.
    관리자 화면의 '지금 실행' 버튼은 POST 를 쓴다.
    """
    triggered_by, denial = _authorize_cron_request()
    if denial:
        return denial

    started_at = _naive_utc_now()
    results, failures = run_daily_cron_jobs()
    finished_at = _naive_utc_now()

    error_message = None
    if failures:
        error_message = '; '.join(
            f"{name}: {results[name].get('error', 'unknown')}" for name in failures
        )[:2000]

    # 실행 기록은 실패해도 응답 자체를 500 으로 만들지 않는다.
    # (기록이 안 되는 것보다 배치가 안 도는 것이 더 큰 문제다)
    run_id = None
    try:
        run = CronRun(
            job=CRON_JOB_NAME,
            started_at=started_at,
            finished_at=finished_at,
            success=not failures,
            summary=json.dumps(results, ensure_ascii=False)[:8000],
            error_message=error_message,
            triggered_by=triggered_by,
        )
        db.session.add(run)
        db.session.commit()
        run_id = run.id
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[Cron] CronRun 기록 실패: {type(e).__name__}: {e}")

    return jsonify({
        'job': CRON_JOB_NAME,
        'runId': run_id,
        'triggeredBy': triggered_by,
        'success': not failures,
        'startedAt': started_at.isoformat(),
        'finishedAt': finished_at.isoformat(),
        'durationMs': int((finished_at - started_at).total_seconds() * 1000),
        'failedJobs': failures,
        'results': results,
    })


def _cron_health_payload():
    """마지막 cron 실행 상태. cron 이 멈춘 사실이 드러나야 한다."""
    last_success = (
        CronRun.query.filter(CronRun.job == CRON_JOB_NAME, CronRun.success.is_(True))
        .order_by(CronRun.started_at.desc(), CronRun.id.desc())
        .first()
    )
    last_run = (
        CronRun.query.filter(CronRun.job == CRON_JOB_NAME)
        .order_by(CronRun.started_at.desc(), CronRun.id.desc())
        .first()
    )

    age_hours = None
    if last_success and last_success.started_at:
        delta = _naive_utc_now() - _as_naive_utc(last_success.started_at)
        age_hours = round(delta.total_seconds() / 3600, 1)

    return {
        'lastSuccessAt': _iso_or_none(last_success.started_at) if last_success else None,
        'ageHours': age_hours,
        # 한 번도 안 돌았거나(None) 하루를 훌쩍 넘겼으면 스케줄러가 죽은 것으로 본다.
        'stale': age_hours is None or age_hours > CRON_STALE_HOURS,
        'staleAfterHours': CRON_STALE_HOURS,
        'lastRun': last_run.to_dict() if last_run else None,
    }


@app.route('/api/admin/cron-runs', methods=['GET'])
@admin_required
def get_cron_runs():
    """최근 cron 실행 이력 (관리자용)."""
    limit = request.args.get('limit', 10, type=int) or 10
    limit = min(max(limit, 1), 50)
    runs = (
        CronRun.query.order_by(CronRun.started_at.desc(), CronRun.id.desc())
        .limit(limit)
        .all()
    )
    return jsonify({
        'runs': [run.to_dict() for run in runs],
        'health': _cron_health_payload(),
    })


# ============================================================
# 퍼널 계측 (전환율)
# ============================================================
# 설계 결정: **이벤트 테이블을 새로 만들지 않는다.**
#   퍼널 단계가 거의 전부 이미 저장된 타임스탬프에서 파생 가능하기 때문이다.
#   설문 완료 → AnalysisJob.created_at
#   견적 요청 → Project.created_at (+ session_id 로 설문과 연결)
#   제안 제출 → Project.proposal_submitted_at
#   계약 체결 → company_signed_at + consultant_signed_at
#   일정 확정 → Project.schedule_confirmed_at
#   완료      → Project.completed_at
#
#   파생 집계의 장점: 새 쓰기 경로가 없어 요청 경로가 느려지지 않고,
#   과거 데이터에 소급 적용되며, 이벤트 적재 실패로 통계가 비는 일이 없다.
#
# ⚠️ 파생 집계의 한계 (숨기면 전환율을 실제보다 좋게 오해한다):
#   1) 설문을 '시작만 하고 이탈한' 사용자는 DB 에 아무 흔적이 없다.
#      POST /api/match 가 성공해야 AnalysisJob 이 생기기 때문이다.
#      따라서 이 퍼널의 첫 단계는 "방문"이 아니라 "설문 완료"다.
#      실제 방문→완료 이탈을 보려면 별도의 프론트엔드 계측이 필요하다.
#   2) 단계별 단위가 도중에 바뀐다. 1~2단계는 '설문(AnalysisJob)' 단위,
#      3단계부터는 '견적 요청(Project)' 단위다. 설문 1건이 컨설턴트 N명에게
#      견적을 요청하면 Project 가 N개 생기므로 같은 축으로 나눌 수 없다.
#      그래서 응답을 두 구간으로 분리하고 각 구간에 단위를 명시한다.
#   3) 기간 필터는 '생성 시각' 기준이다. 기간 안에 시작해 기간 뒤에 계약된
#      건은 아직 계약으로 잡히지 않는다(진행 중인 코호트는 과소 집계된다).

FUNNEL_DEFAULT_DAYS = 30
FUNNEL_MAX_DAYS = 365

FUNNEL_LIMITATIONS = [
    '설문을 시작만 하고 이탈한 사용자는 DB에 기록이 남지 않습니다. 첫 단계는 "방문"이 아니라 "설문 완료"입니다.',
    '1~2단계는 설문 건수, 3단계부터는 견적 요청 건수가 단위입니다. 설문 1건이 여러 컨설턴트에게 견적을 요청하면 견적 건수가 더 많아집니다.',
    '기간 필터는 생성 시각 기준입니다. 최근에 시작된 건은 아직 뒷단계에 도달할 시간이 없어 전환율이 낮게 나옵니다.',
]


def _funnel_rate(numerator, denominator):
    """단계 간 전환율(%). 분모가 0이면 None.

    0건일 때 0% 로 내보내면 "전환이 하나도 안 됐다"로 읽힌다.
    데이터가 없는 것과 전환이 0인 것은 다르므로 None(화면에서 '-')으로 구분한다.
    """
    if not denominator:
        return None
    return round(numerator / denominator * 100, 1)


def _funnel_stage(key, label, count, prev_count, unit):
    return {
        'key': key,
        'label': label,
        'count': int(count or 0),
        'unit': unit,
        # 직전 단계 대비 전환율. 첫 단계는 기준이 없으므로 None.
        'rateFromPrev': None if prev_count is None else _funnel_rate(count or 0, prev_count),
    }


@app.route('/api/admin/funnel-stats', methods=['GET'])
@admin_required
def get_funnel_stats():
    """퍼널 단계별 건수와 전환율 (관리자용). 쿼리: ?days=30

    집계는 테이블당 한 번의 조건부 집계 쿼리로 끝낸다(총 3회 왕복).
    Supabase 는 네트워크 왕복이 곧 지연이라 단계마다 count() 를 날리면
    단계 수만큼 느려진다.
    """
    days = request.args.get('days', FUNNEL_DEFAULT_DAYS, type=int) or FUNNEL_DEFAULT_DAYS
    days = min(max(days, 1), FUNNEL_MAX_DAYS)
    since = _naive_utc_now() - datetime.timedelta(days=days)

    # 소프트 삭제 + status='deleted' 를 모두 제외한다.
    # (AnalysisJob 은 두 방식이 혼재한다 — deleted_at 만 보면 과거 삭제 건이 남는다)
    job_filters = [
        AnalysisJob.created_at >= since,
        AnalysisJob.deleted_at.is_(None),
        AnalysisJob.status != 'deleted',
    ]
    project_filters = [
        Project.created_at >= since,
        Project.deleted_at.is_(None),
    ]

    # ── 1회차: 설문(AnalysisJob) 집계 ──
    job_row = db.session.query(
        func.count(AnalysisJob.id).label('surveys'),
        func.sum(case((AnalysisJob.result.isnot(None), 1), else_=0)).label('with_result'),
    ).filter(*job_filters).one()

    surveys = int(job_row.surveys or 0)
    surveys_with_result = int(job_row.with_result or 0)

    # ── 2회차: 견적 요청(Project) 단계별 집계 ──
    #   조건부 SUM 하나로 전 단계를 한 번에 센다.
    both_signed = and_(
        Project.company_signed_at.isnot(None),
        Project.consultant_signed_at.isnot(None),
    )
    project_row = db.session.query(
        func.count(Project.id).label('requests'),
        func.sum(case((Project.proposal_submitted_at.isnot(None), 1), else_=0)).label('proposals'),
        func.sum(case((both_signed, 1), else_=0)).label('contracted'),
        func.sum(case((Project.schedule_confirmed_at.isnot(None), 1), else_=0)).label('scheduled'),
        func.sum(case((Project.completed_at.isnot(None), 1), else_=0)).label('completed'),
        func.sum(case((Project.negotiation_requested_at.isnot(None), 1), else_=0)).label('negotiations'),
        func.sum(case((Project.cancelled_at.isnot(None), 1), else_=0)).label('cancelled'),
    ).filter(*project_filters).one()

    requests_count = int(project_row.requests or 0)
    proposals = int(project_row.proposals or 0)
    contracted = int(project_row.contracted or 0)
    scheduled = int(project_row.scheduled or 0)
    completed = int(project_row.completed or 0)

    # ── 3회차: 설문 → 견적 요청 전환 (코호트 정렬) ──
    #   그냥 "기간 내 Project 의 distinct session_id" 를 세면, 기간 이전 설문에서
    #   생긴 견적이 섞여 전환율이 100% 를 넘을 수 있다. 기간 내 '설문' 을 기준으로
    #   조인해 그 설문이 견적으로 이어졌는지만 센다.
    surveys_with_quote = int(
        db.session.query(func.count(func.distinct(AnalysisJob.id)))
        .select_from(AnalysisJob)
        .join(Project, Project.session_id == AnalysisJob.id)
        .filter(*job_filters)
        .filter(Project.deleted_at.is_(None))
        .scalar() or 0
    )

    # 1~2단계: 설문 단위
    survey_funnel = [
        _funnel_stage('survey_completed', '설문 완료 (= 매칭 실행)', surveys, None, '설문'),
        _funnel_stage('quote_requested', '견적 요청으로 진행', surveys_with_quote, surveys, '설문'),
    ]

    # 3단계 이후: 견적 요청 단위
    project_funnel = [
        _funnel_stage('quote_requests', '견적 요청', requests_count, None, '견적'),
        _funnel_stage('proposal_submitted', '제안 제출', proposals, requests_count, '견적'),
        _funnel_stage('contracted', '계약 체결 (양측 서명)', contracted, proposals, '견적'),
        _funnel_stage('schedule_confirmed', '일정 확정', scheduled, contracted, '견적'),
        _funnel_stage('completed', '프로젝트 완료', completed, scheduled, '견적'),
    ]

    return jsonify({
        'days': days,
        'since': since.isoformat(),
        'surveyFunnel': survey_funnel,
        'projectFunnel': project_funnel,
        # 본류가 아닌 분기들 — 단계로 세면 퍼널이 왜곡되므로 따로 낸다.
        # (협상은 선택 단계이고, 취소는 이탈이지 진행이 아니다)
        'side': {
            'surveysWithResult': surveys_with_result,
            'negotiationRequested': int(project_row.negotiations or 0),
            'cancelled': int(project_row.cancelled or 0),
        },
        # 견적 요청 → 완료까지의 전체 전환율
        'overallRate': _funnel_rate(completed, requests_count),
        'limitations': FUNNEL_LIMITATIONS,
    })


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

    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
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
    project = get_active_project_or_404(project_id)
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
        projects = Project.query.filter_by(company_id=user_id).filter(
            Project.deleted_at.is_(None)
        ).all()
    else:
        consultant = Consultant.query.filter_by(user_id=user_id).first()
        if consultant:
            projects = Project.query.filter_by(consultant_id=consultant.id).filter(
                Project.deleted_at.is_(None)
            ).all()
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
    project = get_active_project_or_404(project_id)
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

