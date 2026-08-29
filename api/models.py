from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
import json

db = SQLAlchemy()

def utc_now():
    """Timezone-aware UTC now (BUG-003 Fix)"""
    return datetime.now(timezone.utc)


def parse_text_or_json_list(raw):
    """평문 텍스트 또는 JSON 문자열을 프론트가 기대하는 '리스트'로 변환한다.

    detailed_certifications / recent_projects 에는 두 형식이 실제로 공존한다:
    * 등록 폼(consultant_register.html)은 여러 줄 <textarea> 를 평문 그대로 보내고,
      저장 로직도 평문 그대로 넣는다 — 실사용 데이터는 거의 전부 이쪽이다.
    * API 로 리스트/딕셔너리를 보내면 register_consultant_validated() 가
      json.dumps 해서 JSON 문자열로 넣는다.

    예전에는 읽는 쪽에서 무조건 json.loads 를 걸어, 평문으로 저장된
    (= 등록 폼으로 정상 등록한) 전문가의 프로필 상세가 항상 500 이었다
    (BUG-E2E-007, JSONDecodeError). 참고로 /api/admin/seed 는 이 두 필드를
    아예 채우지 않아 시드 데이터에서는 재현되지 않았다.

    소비자(consultant_profile.html, dashboard.html)는 배열을 기대하므로 항상
    리스트를 돌려준다. 평문은 줄 단위로 쪼갠다 — 등록 폼 placeholder 가 한 줄에
    한 항목을 적도록 안내하고, 화면도 항목별로 렌더링하기 때문이다.

    index.py 가 아니라 여기 있는 이유: Consultant.to_dict() 와
    get_consultant_detail() 이 같은 규칙을 써야 하는데, models 는 index 를
    임포트할 수 없다(반대 방향 의존). 정의를 한 곳에 두려면 models 쪽이어야 한다.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [raw]

    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        value = None

    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]

    # JSON 이 아니거나 스칼라(숫자·true·"문자열")면 평문으로 취급한다.
    # 스칼라를 파싱값으로 쓰면 '10' 같은 평문 한 줄이 숫자 10 이 되어버린다.
    return [line.strip() for line in str(raw).splitlines() if line.strip()]

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'company', 'consultant', 'admin'
    name = db.Column(db.String(100))  # 이름 (담당자명/컨설턴트명)
    company_name = db.Column(db.String(100))  # 회사명 (기업 필수, 컨설턴트 선택)
    phone = db.Column(db.String(20))  # For find-email feature
    created_at = db.Column(db.DateTime, default=utc_now)
    # 토큰 폐기용 버전. 비밀번호 재설정 등으로 값이 오르면
    # 이전에 발급된 JWT는 즉시 무효가 된다.
    token_version = db.Column(db.Integer, nullable=False, default=0, server_default='0')

    # ── 회원 탈퇴(소프트 삭제) 시각. NULL 이면 정상 계정 ──
    #
    # 행을 지우지 않는 이유: user.id 를 참조하는 곳이 너무 많다.
    #   project.company_id / consultant.user_id / message.sender_id /
    #   notification.user_id / admin_action_log.admin_user_id /
    #   password_reset_token.user_id / manual_generation.user_id /
    #   company.user_id / profile_change_log.changed_by /
    #   consultant_invite.created_by · used_by_user_id
    # 하드 삭제하면 상대방(기업↔컨설턴트)의 거래 이력과 감사 로그까지 함께
    # 망가진다. 탈퇴자 본인의 개인정보는 익명화로 지우고 행은 남긴다.
    #
    # ⚠️ deleted_at 이 찍힌 사용자는 로그인·API 접근이 모두 차단되어야 한다.
    #    (index.py 의 token_required / token_optional / login /
    #     require_admin_request / request-reset / find-email 에서 확인)
    deleted_at = db.Column(db.DateTime, nullable=True)

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Link to User
    name = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(200))
    industry = db.Column(db.String(100))
    employees = db.Column(db.String(50))
    email = db.Column(db.String(120)) # Keep for contact info even if in User
    created_at = db.Column(db.DateTime, default=utc_now)

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
    
    # ④ Pending Changes (관리자 검토 대기)
    pending_changes = db.Column(db.Text)  # JSON: {"name": "새이름", "iso_experience": {...}, ...}
    pending_changes_at = db.Column(db.DateTime)  # 검토 요청 시각
    
    # ⑤ Status & Rejection Fields
    status = db.Column(db.String(20), default='pending')  # pending, verified, rejected
    rejection_reason = db.Column(db.Text)  # 거부 사유
    rejected_at = db.Column(db.DateTime)  # 거부 시각

    # ── 정산·세금계산서 정보 (A안: NGB가 원청으로 외주비를 지급하기 위해 필수) ──
    # 이 정보가 없으면 프로젝트 종료 후 일일이 연락해 계좌를 받아야 하므로
    # 등록 시점에 함께 수집한다.
    business_type = db.Column(db.String(20))       # 'business'(사업자) | 'individual'(개인)
    biz_reg_no = db.Column(db.String(20))          # 사업자등록번호 (숫자 10자리)
    biz_name = db.Column(db.String(100))           # 사업자등록증상 상호
    biz_ceo_name = db.Column(db.String(50))        # 대표자명
    bank_name = db.Column(db.String(50))           # 은행명
    account_number = db.Column(db.String(50))      # 계좌번호 (공개 API에서는 항상 마스킹)
    account_holder = db.Column(db.String(50))      # 예금주

    # ── 기본 협력계약 동의 (수수료율·직거래 금지·세금계산서 발행 의무) ──
    partner_agreed_at = db.Column(db.DateTime)     # 동의 시각
    partner_agreement_version = db.Column(db.String(20))  # 동의한 약관 버전

    def masked_account(self):
        """계좌번호를 마스킹해 반환 (뒤 4자리만 노출)."""
        if not self.account_number:
            return ''
        digits = ''.join(ch for ch in self.account_number if ch.isdigit())
        if len(digits) <= 4:
            return '*' * len(digits)
        return '*' * (len(digits) - 4) + digits[-4:]


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
            'orgSizeExperience': json.loads(self.org_size_experience) if self.org_size_experience else [],
            'roles': json.loads(self.roles) if self.roles else [],
            # 자격·프로젝트 이력 (평문/JSON 혼재 — parse_text_or_json_list 참조).
            # 이 두 필드가 to_dict() 에 없어서, 컨설턴트 본인 대시보드
            # (dashboard.html 이 GET /api/consultants/<id>/profile 로 읽는다)가
            # 데이터가 있어도 항상 '등록된 정보가 없습니다' 를 보여줬다.
            'detailedCertifications': parse_text_or_json_list(self.detailed_certifications),
            'recentProjects': parse_text_or_json_list(self.recent_projects),
            # Profile fields
            'profileImageUrl': self.profile_image_url,
            'bio': self.bio,
            'introductionVideoUrl': self.introduction_video_url,
            'portfolioFiles': json.loads(self.portfolio_files) if self.portfolio_files else [],
            'phone': self.phone,
            'email': self.email,
            'companyName': self.company_name,
            # Pending changes
            'pendingChanges': json.loads(self.pending_changes) if self.pending_changes else None,
            'pendingChangesAt': self.pending_changes_at.isoformat() if self.pending_changes_at else None,
            # Status & Rejection
            'status': self.status or ('verified' if self.verified else 'pending'),
            'rejectionReason': self.rejection_reason,
            'rejectedAt': self.rejected_at.isoformat() if self.rejected_at else None
        }

class ConsultantInvite(db.Model):
    """컨설턴트 초대 링크.

    관리자가 검증한 후보에게만 1회용 링크를 발급한다.
    공개 URL을 뿌리는 방식과 달리 아무나 등록할 수 없고,
    카톡으로 링크만 보내면 되므로 온보딩 마찰이 낮다.
    """
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100))        # 초대 대상 이름 (표시용)
    email = db.Column(db.String(120))       # 초대 대상 이메일 (표시용)
    memo = db.Column(db.String(200))        # 관리자 메모
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=utc_now)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    used_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    revoked_at = db.Column(db.DateTime)

    def is_usable(self, now=None):
        """사용 가능 여부와 사유를 함께 반환."""
        now = now or utc_now()
        if self.revoked_at:
            return False, '취소된 초대 링크입니다.'
        if self.used_at:
            return False, '이미 사용된 초대 링크입니다.'
        expires = self.expires_at
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires and expires < now:
            return False, '만료된 초대 링크입니다.'
        return True, ''
    

# 프로필 변경 이력 모델
class ProfileChangeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consultant_id = db.Column(db.Integer, db.ForeignKey('consultant.id'), nullable=False)
    field_name = db.Column(db.String(50), nullable=False)  # 변경된 필드명
    old_value = db.Column(db.Text)  # 이전 값
    new_value = db.Column(db.Text)  # 새 값
    changed_at = db.Column(db.DateTime, default=utc_now)
    changed_by = db.Column(db.Integer, db.ForeignKey('user.id'))  # 변경한 사용자
    
    def to_dict(self):
        return {
            'id': self.id,
            'consultantId': self.consultant_id,
            'fieldName': self.field_name,
            'oldValue': self.old_value,
            'newValue': self.new_value,
            'changedAt': self.changed_at.isoformat() if self.changed_at else None,
            'changedBy': self.changed_by
        }

# ② Notification Model
class AdminActionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(80), nullable=False)
    target_id = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        return {
            'id': self.id,
            'adminUserId': self.admin_user_id,
            'action': self.action,
            'targetType': self.target_type,
            'targetId': self.target_id,
            'details': json.loads(self.details) if self.details else {},
            'createdAt': self.created_at.isoformat() if self.created_at else None
        }

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # quote_request, proposal_received, contract_signed, schedule_proposed, etc.
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text)
    link = db.Column(db.String(500))  # 클릭 시 이동할 URL
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    # 이 알림이 메일로도 나갔는지(= 나간 시각). NULL 이면 아직 인앱 전용이다.
    #
    # 일일 배치의 '미열람 알림 메일 승격' 이 이 컬럼 하나로 재발송을 막는다.
    # cron 은 매일 도는데 사용자가 계속 안 읽으면 조건이 계속 참이므로,
    # 이 표식이 없으면 같은 알림이 매일 메일로 나가 소음이 된다.
    #
    # 생성 시점에 이미 메일을 보낸 경로(심사 결과·리마인더·오류 다이제스트)는
    # 여기에 발송 시각을 채워둔다. 그러면 승격 배치가 같은 내용을 두 번 보내지
    # 않는다 — 두 경로가 같은 컬럼 하나로 자연히 맞물린다.
    emailed_at = db.Column(db.DateTime, nullable=True)

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

# B. 인앱 메시지 모델
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now)
    
    def to_dict(self):
        return {
            'id': self.id,
            'projectId': self.project_id,
            'senderId': self.sender_id,
            'content': self.content,
            'isRead': self.is_read,
            'createdAt': self.created_at.isoformat() if self.created_at else None
        }

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Using User ID for simplicity in MVP
    consultant_id = db.Column(db.Integer, db.ForeignKey('consultant.id'))
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)  # 프로젝트 설명/요청 내용
    session_id = db.Column(db.String(36))  # AnalysisJob ID 연결용
    status = db.Column(db.String(50), default='proposal_pending') # proposal_pending, proposal_submitted, contracted, in_progress, completed, cancelled_by_company
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utc_now)
    
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
    
    # Cancellation Fields (④ 취소 관련)
    cancelled_at = db.Column(db.DateTime)  # 취소 시간
    cancelled_reason = db.Column(db.String(500))  # 취소 사유

    # 완료 시각. status='completed' 로 전이한 시점을 별도로 남긴다.
    # status 문자열만으로는 "언제 끝났는지"를 알 수 없어 정산 시점의 근거가 되지 못하고,
    # 리뷰 요청·이행 추적 같은 후속 기능도 기준 시각이 없으면 붙일 수 없다.
    completed_at = db.Column(db.DateTime, nullable=True)

    # Soft delete: 삭제해도 대화(Message)·마일스톤 이력은 보존한다.
    # (기업 일방의 삭제로 컨설턴트와의 협상 기록이 영구 소실되는 것을 방지)
    deleted_at = db.Column(db.DateTime, nullable=True)
    
    # ⑤ 조건 협의 (Negotiation) 관련 필드
    negotiation_status = db.Column(db.String(50))  # pending, accepted, counter, rejected
    negotiation_data = db.Column(db.Text)  # JSON: {requested_price, requested_duration, message, response}
    negotiation_requested_at = db.Column(db.DateTime)
    negotiation_responded_at = db.Column(db.DateTime)
    
    # ⑥ 계약서 (Contract) 관련 필드
    contract_pdf_url = db.Column(db.String(500))  # 생성된 계약서 PDF URL
    contract_special_terms = db.Column(db.Text)  # 특약 사항
    company_signed_at = db.Column(db.DateTime)  # 기업 서명 시간
    consultant_signed_at = db.Column(db.DateTime)  # 전문가 서명 시간
    
    milestones = db.relationship('Milestone', backref='project', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'consultant_id': self.consultant_id,
            'title': self.title,
            'description': self.description,
            'session_id': self.session_id,
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
            # Negotiation fields
            'negotiation_status': self.negotiation_status,
            'negotiation_data': json.loads(self.negotiation_data) if self.negotiation_data else None,
            'negotiation_requested_at': self.negotiation_requested_at.isoformat() if self.negotiation_requested_at else None,
            # Contract fields
            'contract_pdf_url': self.contract_pdf_url,
            'contract_special_terms': self.contract_special_terms,
            'company_signed_at': self.company_signed_at.isoformat() if self.company_signed_at else None,
            'consultant_signed_at': self.consultant_signed_at.isoformat() if self.consultant_signed_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
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
    created_at = db.Column(db.DateTime, default=utc_now)
    # Soft delete: 관리자 오조작으로 콘텐츠가 영구 소실되지 않도록 보존한다.
    deleted_at = db.Column(db.DateTime, nullable=True)

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
    created_at = db.Column(db.DateTime, default=utc_now)
    deleted_at = db.Column(db.DateTime, nullable=True) # Soft delete timestamp

    def set_result(self, result_dict):
        self.result = json.dumps(result_dict)

    def get_result(self):
        return json.loads(self.result) if self.result else None

    def set_intake_data(self, data_dict):
        self.intake_data = json.dumps(data_dict)

    def get_intake_data(self):
        return json.loads(self.intake_data) if self.intake_data else {}


class ManualGeneration(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False)
    token_expires_at = db.Column(db.DateTime, nullable=False)
    form_data = db.Column(db.Text, nullable=False)
    phase1_markdown = db.Column(db.Text)
    phase2_markdown = db.Column(db.Text)
    phase3_markdown = db.Column(db.Text)
    status = db.Column(db.String(30), default='created')
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now)

    def set_form_data(self, data_dict):
        self.form_data = json.dumps(data_dict, ensure_ascii=False)

    def get_form_data(self):
        return json.loads(self.form_data) if self.form_data else {}

    def combined_markdown(self):
        return '\n\n'.join(
            part for part in [self.phase1_markdown, self.phase2_markdown, self.phase3_markdown]
            if part
        )


class RateLimitEntry(db.Model):
    """호출량 제한용 요청 기록 (무인증 공개 엔드포인트 남용 방지)."""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(160), nullable=False, index=True)  # 예: "match:1.2.3.4"
    created_at = db.Column(db.DateTime, default=utc_now, index=True)


class CronRun(db.Model):
    """시간 기반 배치(cron) 실행 기록.

    이 기록이 없으면 cron 인프라 자체가 무의미해진다. 스케줄러가 조용히
    멈춰도 아무 일도 일어나지 않는 것처럼 보이기 때문이다("리마인더가 안 온다"는
    사실은 아무도 신고하지 않는다). 마지막 성공 실행 시각을 남겨두고
    /api/health?verbose=1 과 관리자 화면에서 경과 시간을 노출한다.

    RateLimitEntry / ErrorLog 와 같은 방식으로, 행을 그대로 쌓고 조회 시 집계한다
    (서버리스 동시 실행에서 UPDATE 경합을 피하기 위함).
    """
    id = db.Column(db.Integer, primary_key=True)
    job = db.Column(db.String(50), nullable=False, index=True)   # 'daily'
    started_at = db.Column(db.DateTime, default=utc_now, index=True)
    finished_at = db.Column(db.DateTime)

    # 개별 작업 하나가 실패해도 나머지는 계속 돌린다.
    # success 는 "모든 작업이 예외 없이 끝났는가" 를 뜻한다.
    success = db.Column(db.Boolean, default=False, index=True)

    summary = db.Column(db.Text)          # JSON: 작업별 처리 건수
    error_message = db.Column(db.Text)    # 실패한 작업 요약 (없으면 NULL)

    # 누가 호출했는가. Vercel cron 은 User-Agent 가 'vercel-cron/1.0' 이라
    # 이 값으로 "레거시 vercel.json 에서 Vercel crons 가 실제로 동작하는가" 를
    # 배포 후에 실측으로 판별할 수 있다.
    triggered_by = db.Column(db.String(30))  # 'vercel-cron' | 'external-cron' | 'admin'

    def to_dict(self):
        return {
            'id': self.id,
            'job': self.job,
            'startedAt': self.started_at.isoformat() if self.started_at else None,
            'finishedAt': self.finished_at.isoformat() if self.finished_at else None,
            'success': bool(self.success),
            'summary': json.loads(self.summary) if self.summary else {},
            'errorMessage': self.error_message,
            'triggeredBy': self.triggered_by,
        }


class PasswordResetToken(db.Model):
    """Password reset token for account recovery"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utc_now)


class Inquiry(db.Model):
    """문의 접수.

    지금까지 문의 경로는 푸터의 개인 Gmail `mailto:` 하나뿐이었다. 그래서
    문의가 플랫폼 밖 개인 메일함에만 쌓여 (1) 이력이 남지 않고, (2) FAQ 를
    무엇으로 채워야 하는지 알려주는 원천 데이터가 통째로 유실됐다.
    로그인 후 화면(dashboard.html)에는 문의 경로 자체가 없어, 유료 퍼널
    한가운데서 막힌 사용자가 연락할 방법이 없었다.

    ⚠️ 무인증 공개 경로(POST /api/inquiries)로 들어오므로 name·email·
       subject·content 는 **전부 외부 입력**이다. 렌더링하는 쪽
       (admin.html)은 반드시 escapeHtml 을 거쳐야 한다.
    """
    id = db.Column(db.Integer, primary_key=True)

    # 로그인 상태로 접수했으면 계정을 연결한다. 비로그인 접수면 NULL.
    # (탈퇴해도 문의 이력은 남아야 하므로 소프트 삭제 정책과 충돌하지 않는다)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(30), nullable=False, default='etc')
    subject = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)

    # received(접수) → checked(확인) → done(완료)
    status = db.Column(db.String(20), nullable=False, default='received', index=True)

    admin_memo = db.Column(db.Text)          # 관리자 내부 메모 (문의자에게 노출하지 않는다)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    updated_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        return {
            'id': self.id,
            'userId': self.user_id,
            'name': self.name,
            'email': self.email,
            'category': self.category,
            'subject': self.subject,
            'content': self.content,
            'status': self.status,
            'adminMemo': self.admin_memo,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
        }


class Review(db.Model):
    """완료된 프로젝트에 대한 기업의 컨설턴트 평가.

    이 테이블이 생기기 전까지 `Consultant.rating` / `Consultant.reviews` 는
    **공급원이 없는 컬럼**이었다. 매칭은 이 두 값을 17점(WEIGHT_RATING)으로
    반영하는데 값을 채우는 코드가 0건이라, 전원이 중립값을 받았다.
    즉 매칭 배점의 17%가 아무 정보도 나르지 않고 있었다.

    ⚠️ 이 테이블이 평점의 **단일 진실**이다.
       `Consultant.rating` / `Consultant.reviews` 는 조회 성능을 위한 캐시일
       뿐이며, 등록·수정·숨김 때마다 이 테이블에서 **전부 다시 계산**한다
       (index.py 의 recalculate_consultant_rating). 증분 갱신
       (rating = (rating*n + new)/(n+1))을 쓰면 부동소수 오차가 누적되고,
       숨김 처리를 되돌릴 때 원래 값으로 돌아오지 못한다.

    ⚠️ `comment` 는 외부 입력이다. 렌더링하는 쪽은 반드시 escapeHtml 을 거칠 것.
    """
    id = db.Column(db.Integer, primary_key=True)

    # ── 프로젝트당 1건 (조작 방지의 핵심) ──
    #
    # unique 가 없으면 같은 거래 하나로 5점을 100번 등록해 평점을 만들 수 있다.
    # 애플리케이션 검사(기존 행 조회)만으로는 부족하다. Vercel 서버리스는 같은
    # 요청을 동시에 여러 인스턴스가 처리할 수 있어, 조회-삽입 사이의 경합에서
    # 두 행이 함께 들어간다. DB 제약과 애플리케이션 검사를 **둘 다** 둔다.
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'),
                           nullable=False, unique=True)

    consultant_id = db.Column(db.Integer, db.ForeignKey('consultant.id'),
                              nullable=False, index=True)

    # 작성자 = 그 프로젝트의 기업 사용자(user.id). project.company_id 와 같은 값이다.
    # 굳이 중복 저장하는 이유: 프로젝트가 소프트 삭제되거나 소유가 바뀌어도
    # "누가 썼는가" 는 리뷰 자체의 속성으로 남아야 하기 때문이다(수정 권한 판정에 쓴다).
    company_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # 1~5 정수. 애플리케이션에서 범위를 검증하고, DB 에도 CHECK 를 건다.
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    updated_at = db.Column(db.DateTime, default=utc_now)

    # ── 관리자 숨김 (삭제 대신) ──
    #
    # 사용자에게 삭제를 열지 않는다. 컨설턴트가 나쁜 평가를 지워달라고 압박하는
    # 경로가 생기고, 기업이 협상 카드로 삭제를 쓰게 된다. 욕설·개인정보 노출 같은
    # 건은 관리자가 '숨김' 으로 처리하고, 숨긴 행은 평균 계산에서 제외한다.
    # 행 자체는 남으므로 분쟁 시 무엇을 왜 숨겼는지 되짚을 수 있다.
    hidden_at = db.Column(db.DateTime, nullable=True, index=True)
    hidden_reason = db.Column(db.String(300))
    hidden_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    __table_args__ = (
        db.CheckConstraint('rating >= 1 AND rating <= 5', name='ck_review_rating_range'),
    )

    def is_visible(self):
        return self.hidden_at is None

    def to_dict(self, author_label=None, include_admin_fields=False):
        """공개용 직렬화.

        author_label 은 호출부가 마스킹해서 넘긴다 (index.py 의 _review_author_label).
        기업명을 그대로 노출하면 "어느 기업이 어느 컨설턴트에게 무엇을 맡겼는가" 라는
        거래 관계가 공개 페이지에서 드러난다.
        """
        data = {
            'id': self.id,
            'projectId': self.project_id,
            'consultantId': self.consultant_id,
            'rating': self.rating,
            'comment': self.comment,
            'authorLabel': author_label,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'updatedAt': self.updated_at.isoformat() if self.updated_at else None,
            # 수정된 리뷰는 그 사실이 보여야 한다. 조용히 바뀌면 읽는 쪽이
            # 지금 보는 문장이 최초 평가인지 알 수 없다.
            #
            # ⚠️ 단순 비교(updated_at > created_at)를 쓰면 **한 번도 수정하지 않은
            #    리뷰까지 '수정됨' 으로 표시된다.** default=utc_now 는 컬럼마다
            #    따로 평가되므로 행을 만들 때 두 값에 마이크로초 차이가 생긴다.
            #    (실제로 관측했다 — 신규 리뷰 3건 전부 '수정됨' 으로 나왔다.)
            #    사람이 수정한 것과 구분되는 여유(1초)를 둔다.
            'edited': bool(
                self.created_at and self.updated_at
                and (self.updated_at - self.created_at).total_seconds() > 1
            ),
        }
        if include_admin_fields:
            data['companyId'] = self.company_id
            data['hiddenAt'] = self.hidden_at.isoformat() if self.hidden_at else None
            data['hiddenReason'] = self.hidden_reason
            data['hiddenBy'] = self.hidden_by
        return data


class ErrorLog(db.Model):
    """미처리 예외 기록 (관측성 확보).

    Vercel 서버리스에서는 print() 로그가 콘솔에만 남고 휘발되므로,
    "에러가 나도 아무도 모르는" 상태가 된다. 외부 SDK(Sentry) 대신
    이 테이블에 쌓고 관리자 화면에서 조회한다.

    설계상 주의점 두 가지:
    1. user_id 에 ForeignKey 를 걸지 않는다.
       로깅이 참조 무결성 때문에 실패하면 원래 에러까지 함께 사라진다.
    2. 발생 횟수를 카운터 컬럼으로 증가시키지 않는다.
       서버리스는 동시에 여러 인스턴스가 뜨므로 UPDATE 경합이 생긴다.
       행을 그대로 쌓고, 조회 시 fingerprint 로 GROUP BY 해서 센다.
    """
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    level = db.Column(db.String(20), default='error')

    # 요청 컨텍스트 — 본문/헤더/쿠키/쿼리스트링은 절대 저장하지 않는다.
    # 비밀번호·JWT·재설정 토큰이 그대로 들어가기 때문이다.
    path = db.Column(db.String(300))
    method = db.Column(db.String(10))
    status_code = db.Column(db.Integer)

    exc_type = db.Column(db.String(120))
    exc_message = db.Column(db.Text)
    traceback = db.Column(db.Text)          # 8000자로 잘라 저장 (index.py 에서 처리)

    user_id = db.Column(db.Integer, nullable=True)   # FK 없음 (위 1번 사유)
    client_ip = db.Column(db.String(64))

    # exc_type + 정규화된 path + 예외 발생 프레임의 해시. 같은 에러를 묶는 용도.
    fingerprint = db.Column(db.String(64), index=True)

    def to_dict(self, include_traceback=False):
        """스택 트레이스는 기본 제외 — 목록 응답을 가볍게 유지한다."""
        data = {
            'id': self.id,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'level': self.level,
            'path': self.path,
            'method': self.method,
            'statusCode': self.status_code,
            'excType': self.exc_type,
            'excMessage': self.exc_message,
            'userId': self.user_id,
            'clientIp': self.client_ip,
            'fingerprint': self.fingerprint,
        }
        if include_traceback:
            data['traceback'] = self.traceback
        return data
