"""
InsightMatch Email Service
이메일 발송 모듈 - 견적 요청 알림, 제안서 발송 등
모든 메일 HTML 은 email_layout.render_email 공용 레이아웃으로 만든다.

지원 채널:
1. 이메일 (현재 구현) - SendGrid / SMTP
2. 카카오톡 알림톡 (향후 구현) - 사업자 인증 후

"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime
from typing import List, Dict, Optional

from . import email_layout as layout
from .intake_labels import intake_label


# SMTP 연결·응답 대기 상한(초). Vercel maxDuration 이 60초이고 일일 배치가
# 루프 안에서 여러 통을 보내므로, 한 통이 오래 잡고 있으면 뒤 작업이 통째로
# 잘린다. 정상 발송은 1~3초면 끝나므로 10초면 넉넉하다.
SMTP_TIMEOUT_SECONDS = int(os.environ.get('SMTP_TIMEOUT_SECONDS', '10'))


def _mask_email(email):
    """로그에 개인정보를 그대로 남기지 않도록 이메일을 마스킹한다."""
    if not email or '@' not in str(email):
        return '(unknown)'
    local, _, domain = str(email).partition('@')
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}"


def _nowrap_codes(codes):
    """'ISO 14001:2026' 이 좁은 화면에서 'ISO / 14001' 로 갈라지지 않게 공백을 줄바꿈 없는 공백으로."""
    return [str(c).replace('ISO ', 'ISO ') for c in (codes or []) if c]


class EmailService:
    def __init__(self):
        # SMTP 설정 (환경변수에서 로드)
        self.smtp_host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 587))
        self.smtp_user = os.environ.get('SMTP_USER', '')
        self.smtp_password = os.environ.get('SMTP_PASSWORD', '')
        # 발신 주소가 없으면 SMTP 로그인 계정으로 보낸다. 예전 기본값(insightmatch 닷컴의 noreply)은
        # 남이 가진 도메인이라, Gmail SMTP 에서는 바뀌어 보이고 다른 서버에서는 스팸 처리될 수 있었다.
        self.from_email = (os.environ.get('FROM_EMAIL') or self.smtp_user or '').strip()
        self.from_name = os.environ.get('FROM_NAME', 'InsightMatch')
        
        # 이메일 사용 가능 여부 확인
        self.is_configured = bool(self.smtp_user and self.smtp_password)
        
        if not self.is_configured:
            print("[EmailService] Warning: SMTP credentials not configured. Emails will be logged only.")
    
    def _get_smtp_connection(self):
        """SMTP 연결 생성.

        ⚠️ timeout 은 필수다. 없으면 소켓 기본값(무한)으로 대기한다.
           일일 배치가 루프 안에서 메일을 보내므로, SMTP 서버가 응답하지 않으면
           배치 전체가 매달리다 Vercel maxDuration(60초)에 잘린다. 그러면
           CronRun 기록도 남지 않아 관리자 화면에는 "cron 이 아예 안 돌았다"로
           보이고, 원인이 메일 한 통이라는 사실이 드러나지 않는다.
           발송 건수 상한(CRON_MAX_EMAILS_PER_RUN)은 건수를 막을 뿐 시간을
           막지 못하므로, 시간 상한은 여기서 걸어야 한다.
        """
        server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
        server.starttls()
        server.login(self.smtp_user, self.smtp_password)
        return server
    
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        attachments: Optional[List[Dict]] = None
    ) -> Dict:
        """
        이메일 발송
        
        Args:
            to_email: 수신자 이메일
            subject: 제목
            html_content: HTML 본문
            text_content: 텍스트 본문 (선택)
            attachments: 첨부파일 리스트 [{'filename': 'xxx.pdf', 'content': bytes}]
        
        Returns:
            {'success': bool, 'message': str}
        """
        if not self.is_configured:
            # 설정이 없으면 로그만 출력.
            # 본문에는 비밀번호 재설정 토큰 등이 포함될 수 있어 미리보기를 남기지 않는다.
            print(f"[EmailService] Would send email to: {_mask_email(to_email)}")
            print(f"[EmailService] Subject: {subject}")
            return {
                'success': True,
                'message': 'Email logged (SMTP not configured)',
                'simulated': True
            }
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{self.from_name} <{self.from_email}>"
            msg['To'] = to_email
            
            # 텍스트 버전
            if text_content:
                msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
            
            # HTML 버전
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 첨부파일
            if attachments:
                for attachment in attachments:
                    part = MIMEApplication(attachment['content'])
                    part.add_header(
                        'Content-Disposition',
                        'attachment',
                        filename=attachment['filename']
                    )
                    msg.attach(part)
            
            # 발송
            with self._get_smtp_connection() as server:
                server.sendmail(self.from_email, to_email, msg.as_string())
            
            print(f"[EmailService] Email sent successfully to: {_mask_email(to_email)}")
            return {'success': True, 'message': 'Email sent successfully'}
            
        except Exception as e:
            print(f"[EmailService] Failed to send email: {str(e)}")
            return {'success': False, 'message': str(e)}
    
    # ------------------------------------------------------------------
    # 모든 메일은 email_layout.render_email 하나로 만든다.
    # 사용자 입력은 layout 의 부품들이 이스케이프하므로 여기서는 원래 값을 넘긴다.
    # ------------------------------------------------------------------
    TRANSACTIONAL_NOTICE = layout.TRANSACTIONAL_NOTICE

    def _send_rendered(self, to_email: str, subject: str, rendered) -> Dict:
        html_content, text_content = rendered
        return self.send_email(to_email, subject, html_content, text_content=text_content)

    def send_quote_request_to_consultant(
        self,
        consultant_email: str,
        consultant_name: str,
        company_name: str,
        industry: str,
        standards: List[str],
        issues_summary: Optional[str],
        timeline: str,
        budget: str,
        additional_notes: Optional[str],
        project_id: int,
        dashboard_url: Optional[str] = None
    ) -> Dict:
        """컨설턴트에게 견적 요청 알림 메일.

        업종·일정·예산은 설문 코드값(Nuclear, 6months, 500-1000)으로 들어올 수 있어
        intake_label 로 한국어 이름을 붙인다. 이미 한국어면 그대로 둔다.
        """
        dashboard_url = dashboard_url or f'{layout.site_url()}/dashboard.html'
        standards = _nowrap_codes(standards)
        name = consultant_name or '전문가'
        company = company_name or '기업'
        urgent = timeline in ('1month', 'urgent')

        subject = f"[InsightMatch] {company}에서 견적을 요청했습니다"
        rows = [
            ('요청 기업', company),
            ('업종', intake_label('industry', industry) or '미정'),
            ('관심 인증', ', '.join(standards) if standards else '미정'),
        ]
        if issues_summary:
            rows.append(('주요 경영 이슈', issues_summary))
        rows += [
            ('희망 일정', intake_label('timeline', timeline) or '미정'),
            ('예산 범위', intake_label('budget', budget) or '미정'),
        ]
        if additional_notes:
            rows.append(('추가 요청사항', additional_notes))
        body = [
            layout.paragraph(f'{name}님, 안녕하세요.'),
            layout.paragraph(f'{company}에서 견적 요청을 보냈습니다. 요청 내용을 확인하시고 제안서를 보내 주세요.'),
            layout.info_table(rows),
        ]
        if urgent:
            body.append(layout.notice('일정이 급한 요청입니다', [
                '1개월 안에 진행을 원하는 요청입니다. 가능하면 빨리 답변해 주세요.',
            ], tone='warn'))

        return self._send_rendered(consultant_email, subject, layout.render_email(
            subject=subject,
            eyebrow='새 견적 요청',
            tone='warn' if urgent else 'default',
            heading=f'{company}에서 견적을 요청했습니다',
            preheader=f'{company} · {", ".join(standards) if standards else "ISO 인증"} 견적 요청이 도착했습니다.',
            body=body,
            cta=('요청 내용 보기', dashboard_url),
        ))

    def send_quote_confirmation_to_company(
        self,
        company_email: str,
        company_name: str,
        consultant_names: List[str],
        standards: List[str],
        dashboard_url: Optional[str] = None
    ) -> Dict:
        """기업에게 견적 요청 접수 확인 메일.

        ⚠️ 기업에게 가는 메일이다. 수수료·협력계약 이야기는 절대 넣지 않는다.
        """
        dashboard_url = dashboard_url or f'{layout.site_url()}/dashboard.html'
        count = len(consultant_names)
        standards_text = ', '.join(_nowrap_codes([s.split(':')[0] for s in standards])) if standards else '미정'
        company = company_name or '고객'

        subject = f"[InsightMatch] {count}명의 전문가에게 견적 요청이 전달되었습니다"
        return self._send_rendered(company_email, subject, layout.render_email(
            subject=subject,
            eyebrow='견적 요청 완료',
            heading=f'전문가 {count}명에게 견적 요청을 보냈습니다',
            preheader='제안서가 도착하면 메일로 알려 드립니다.',
            body=[
                layout.paragraph(f'{company}님, 안녕하세요.'),
                layout.paragraph('요청하신 인증 견적이 아래 전문가에게 전달되었습니다.'),
                layout.info_table([
                    ('요청 인증', standards_text),
                    ('받은 전문가', ', '.join(consultant_names) if consultant_names else '-'),
                ]),
                layout.steps('앞으로 이렇게 진행됩니다', [
                    '전문가가 요청 내용을 검토하고 제안서를 보냅니다.',
                    '제안서가 도착하면 메일로 알려 드립니다.',
                    '제안을 비교해 보시고 함께할 전문가를 고르시면 됩니다.',
                ]),
            ],
            cta=('대시보드에서 진행 상황 보기', dashboard_url),
        ))

    def send_proposal_notification(
        self,
        company_email: str,
        company_name: str,
        consultant_name: str,
        project_title: str,
        proposal_price: Optional[int] = None,
        proposal_duration: Optional[str] = None,
        dashboard_url: Optional[str] = None
    ) -> Dict:
        """기업에게 제안서 도착 알림 메일.

        인앱 알림만 만들면 기업은 대시보드에 접속해야만 제안서 도착을 안다.
        견적 요청 확인 메일의 바로 다음 단계라 같은 구성을 쓴다.
        ⚠️ 기업에게 가는 메일이다. 수수료·협력계약 이야기는 넣지 않는다.
        """
        dashboard_url = dashboard_url or f'{layout.site_url()}/dashboard.html'
        company = company_name or '고객'
        consultant = consultant_name or '전문가'
        title = project_title or 'ISO 인증 프로젝트'
        try:
            price_text = f"{int(proposal_price):,}원" if proposal_price else '제안서 참조'
        except (TypeError, ValueError):
            price_text = '제안서 참조'
        duration_text = str(proposal_duration) if proposal_duration else '제안서 참조'

        subject = f"[InsightMatch] {consultant}님이 제안서를 보냈습니다"
        return self._send_rendered(company_email, subject, layout.render_email(
            subject=subject,
            eyebrow='제안서 도착',
            heading=f'{consultant}님이 제안서를 보냈습니다',
            preheader=f'{title} · 제안 금액 {price_text}',
            body=[
                layout.paragraph(f'{company}님, 안녕하세요.'),
                layout.paragraph('요청하신 프로젝트에 제안서가 도착했습니다.'),
                layout.info_table([
                    ('프로젝트', title),
                    ('제안 전문가', consultant),
                    ('제안 금액', price_text),
                    ('예상 기간', duration_text),
                ]),
                layout.steps('다음 순서', [
                    '대시보드에서 제안서 내용과 첨부 파일을 확인하세요.',
                    '다른 전문가의 제안이 있다면 함께 비교해 보세요.',
                    '마음에 드는 제안을 고르면 계약 단계로 넘어갑니다.',
                ]),
            ],
            cta=('제안서 보기', dashboard_url),
        ))

    def send_project_update(
        self,
        to_email: str,
        user_name: Optional[str],
        subject: str,
        eyebrow: str,
        heading: str,
        intro: str,
        rows: Optional[List] = None,
        other_message: Optional[str] = None,
        other_message_label: str = '전달 메시지',
        action_url: Optional[str] = None,
        cta_label: str = '대시보드에서 확인하기',
        tone: str = 'default',
    ) -> Dict:
        """프로젝트 진행 중 상대방이 답해야 하는 이벤트 메일 (조건 협의 요청·응답·역제안 수락 등).

        이런 이벤트는 하루 한 번 도는 미열람 알림 묶음(send_notification_digest)을 기다리면
        상대가 다음 날에야 알게 된다. 호출부가 인앱 알림을 만든 직후 바로 보낸다.
        ⚠️ 기업에게도 가는 메일이다. 수수료·협력계약 이야기는 넣지 않는다.

        Args:
            rows: [(항목, 값)] — 정보 표. 값은 레이아웃이 이스케이프한다.
            other_message: 상대방이 적은 메시지(사용자 입력). 안내 상자로 붙인다.
        """
        action_url = action_url or f'{layout.site_url()}/dashboard.html'
        body = [
            layout.paragraph(f'{user_name or "회원"}님, 안녕하세요.'),
            layout.paragraph(intro),
        ]
        if rows:
            body.append(layout.info_table(rows))
        if other_message:
            body.append(layout.notice(other_message_label, [other_message]))
        full_subject = f'[InsightMatch] {subject}'
        return self._send_rendered(to_email, full_subject, layout.render_email(
            subject=full_subject,
            eyebrow=eyebrow,
            tone=tone,
            heading=heading,
            preheader=intro[:80],
            body=body,
            cta=(cta_label, action_url),
        ))

    # 컨설턴트 심사 결과 4개 이벤트의 메일 문안.
    # 인앱 알림 type 값(consultant_approved 등)을 그대로 키로 쓴다.
    REVIEW_RESULT_TEMPLATES = {
        'consultant_approved': {
            'subject': '[InsightMatch] 전문가 등록이 승인되었습니다',
            'eyebrow': '등록 승인',
            'tone': 'default',
            'heading': '전문가 등록이 승인되었습니다',
            'body': '보내 주신 프로필 검토를 마쳤습니다. 이제 기업이 견적을 요청하면 '
                    '조건이 맞는 경우 매칭 후보로 추천됩니다.',
            'cta': '대시보드 열기',
        },
        'consultant_rejected': {
            'subject': '[InsightMatch] 전문가 등록 검토 결과 안내',
            'eyebrow': '검토 결과',
            'tone': 'warn',
            'heading': '이번에는 등록을 승인하지 못했습니다',
            'body': '보내 주신 프로필을 검토했지만 아래 사유로 이번에는 승인하지 못했습니다. '
                    '내용을 보완해 다시 등록해 주시면 다시 검토하겠습니다.',
            'cta': '프로필 보완하기',
        },
        'consultant_verification_revoked': {
            'subject': '[InsightMatch] 전문가 인증 자격이 해제되었습니다',
            'eyebrow': '인증 해제',
            'tone': 'alert',
            'heading': '전문가 인증이 해제되었습니다',
            'body': '아래 사유로 전문가 인증이 해제되었습니다. 해제된 동안에는 새 매칭 후보에서 빠지며, '
                    '사유가 해결되면 재심사를 요청하실 수 있습니다.',
            'cta': '대시보드에서 확인하기',
        },
        'consultant_restored': {
            'subject': '[InsightMatch] 전문가 프로필이 재검토 대기 상태가 되었습니다',
            'eyebrow': '재검토 대기',
            'tone': 'neutral',
            'heading': '프로필이 재검토 대기 상태가 되었습니다',
            'body': '전문가 프로필을 다시 검토하기로 했습니다. 검토가 끝나면 결과를 메일로 알려 드리겠습니다.',
            'cta': '대시보드에서 확인하기',
        },
    }

    def send_consultant_review_result(
        self,
        consultant_email: str,
        consultant_name: str,
        notification_type: str,
        reason: Optional[str] = None,
        dashboard_url: Optional[str] = None,
        register_url: Optional[str] = None
    ) -> Dict:
        """컨설턴트 심사 결과(승인·거절·자격해제·복원) 메일.

        consultant_register.html 과 admin.html 이 이미 사용자에게
        "승인 완료 시 이메일로 안내드립니다" / "거부 사유는 이메일로 전달됩니다"
        라고 약속하고 있으므로, 인앱 알림만으로는 약속을 지키지 못한다.

        Args:
            notification_type: REVIEW_RESULT_TEMPLATES 의 키
            reason: 거절·자격해제 사유 (관리자 입력 — 본문에 이스케이프해 넣는다)
        """
        template = self.REVIEW_RESULT_TEMPLATES.get(notification_type)
        if not template:
            # 알 수 없는 이벤트로 엉뚱한 메일을 보내느니 보내지 않는다.
            print(f"[EmailService] 알 수 없는 심사 결과 유형: {notification_type}")
            return {'success': False, 'message': f'Unknown review result type: {notification_type}'}

        dashboard_url = dashboard_url or f'{layout.site_url()}/dashboard.html'
        name = consultant_name or '전문가'
        body = [
            layout.paragraph(f'{name}님, 안녕하세요.'),
            layout.paragraph(template['body']),
        ]
        if reason:
            body.append(layout.notice('사유', [reason], tone=template['tone']))

        # 거절은 '재등록' 이 다음 행동이므로 등록 페이지로 보낸다.
        action_url = register_url if (notification_type == 'consultant_rejected' and register_url) else dashboard_url

        return self._send_rendered(consultant_email, template['subject'], layout.render_email(
            subject=template['subject'],
            eyebrow=template['eyebrow'],
            tone=template['tone'],
            heading=template['heading'],
            preheader=template['body'][:80],
            body=body,
            cta=(template['cta'], action_url),
        ))

    def send_password_reset_email(
        self,
        to_email: str,
        user_name: str,
        reset_link: str
    ) -> Dict:
        """비밀번호 재설정 메일."""
        subject = "[InsightMatch] 비밀번호 재설정 요청"
        return self._send_rendered(to_email, subject, layout.render_email(
            subject=subject,
            eyebrow='계정 보안',
            tone='neutral',
            heading='비밀번호를 다시 설정해 주세요',
            preheader='링크는 30분 동안 쓸 수 있습니다.',
            body=[
                layout.paragraph(f'{user_name or "회원"}님, 안녕하세요.'),
                layout.paragraph('InsightMatch 계정의 비밀번호 재설정 요청을 받았습니다. '
                                 '아래 버튼을 눌러 새 비밀번호를 정해 주세요.'),
            ],
            cta=('새 비밀번호 설정하기', reset_link),
            after_cta=[
                layout.notice('확인해 주세요', [
                    '이 링크는 30분 동안만 쓸 수 있습니다.',
                    '직접 요청하지 않으셨다면 이 메일은 무시하셔도 됩니다. 비밀번호는 바뀌지 않습니다.',
                ]),
                layout.fallback_link(reset_link),
            ],
        ))

    def send_error_digest(
        self,
        to_email: str,
        admin_name: Optional[str],
        hours: int,
        error_count: int,
        warning_count: int,
        error_groups: List[Dict],
        warning_groups: List[Dict],
        admin_url: Optional[str] = None
    ) -> Dict:
        """관리자에게 보내는 오류 일일 다이제스트.

        ErrorLog 는 관리자가 화면을 열어봐야만 보인다. 하루 한 번 요약을 밀어넣어야
        관측성이 실제로 작동한다. 0건일 때는 호출부에서 발송을 건너뛴다 —
        매일 오는 '이상 없음' 메일은 곧 읽히지 않고, 문제가 생긴 날의 메일까지 묻힌다.

        미처리 예외(error)와 부분 실패(warning)를 나눠 보여준다.
        메시지·경로에는 사용자 입력이 섞여 들어오므로 표 부품이 이스케이프한다.
        """
        admin_url = admin_url or f'{layout.site_url()}/admin.html'
        subject = f"[InsightMatch] 오류 요약 — 예외 {error_count}건 / 부분 실패 {warning_count}건"

        def _rows(groups):
            rows = []
            for g in groups or []:
                kind = layout.Safe(
                    f'<strong>{layout.esc(g.get("excType") or "-")}</strong><br>'
                    f'<span style="font-size:12px; color:{layout.MUTED};">{layout.esc(g.get("path") or "-")}</span>'
                )
                rows.append([kind, str(g.get('message') or '')[:200], g.get('count') or 0])
            return rows

        return self._send_rendered(to_email, subject, layout.render_email(
            subject=subject,
            eyebrow='일일 오류 요약',
            tone='alert' if error_count else 'warn',
            heading=f'지난 {hours}시간 오류 요약',
            preheader=f'미처리 예외 {error_count}건, 부분 실패 {warning_count}건',
            body=[
                layout.paragraph(f'{admin_name or "관리자"}님, 지난 {hours}시간 동안 기록된 오류입니다.'),
                layout.stat_row([
                    ('미처리 예외 (500)', error_count, 'alert'),
                    ('부분 실패 (메일 등)', warning_count, 'warn'),
                ]),
                layout.data_table('미처리 예외 — 요청이 실패했습니다',
                                  ['예외 / 경로', '메시지', '횟수'], _rows(error_groups), tone='alert'),
                layout.data_table('부분 실패 — 요청은 성공했지만 후속 작업이 실패했습니다',
                                  ['종류 / 경로', '메시지', '횟수'], _rows(warning_groups), tone='warn'),
            ],
            cta=('관리자 화면에서 스택 트레이스 보기', admin_url),
            footer_note='일일 배치가 자동으로 보낸 메일입니다.',
            audience='admin',
        ))

    # ------------------------------------------------------------------
    # 통지·리마인더 (L1-B)
    # ------------------------------------------------------------------
    # 아래 메일은 전부 **거래적(transactional)** 통지다. 회원 본인의 계정·
    # 프로젝트 진행 상황에 대한 안내이며 광고·홍보 문구를 넣지 않는다.
    # 정보통신망법상 광고성 정보는 별도 수신동의가 필요하므로, 성격이
    # 오해되지 않도록 푸터에 안내 문구(layout.TRANSACTIONAL_NOTICE)를 넣는다.

    def send_notification_digest(
        self,
        to_email: str,
        user_name: Optional[str],
        items: List[Dict],
        total_count: int,
        dashboard_url: Optional[str] = None
    ) -> Dict:
        """읽지 않은 인앱 알림을 **사용자당 1통**으로 묶어 보낸다.

        이벤트마다 메일 코드를 붙이는 대신, 이미 잘 쌓이고 있는 Notification 행을
        하루 한 번 메일로 승격시킨다. 계약 서명·프로젝트 완료·역제안 같은 이벤트도
        이 메일로 나간다. 묶는 것이 핵심이다 — 5건에 5통을 보내면 알림 자체가 소음이 된다.

        Args:
            items: [{'title', 'message', 'link'}] — 본문에 나열할 알림 (이미 잘려 있음)
            total_count: 실제 미열람 총건수 (items 보다 많으면 '외 N건' 으로 표기)
        """
        dashboard_url = dashboard_url or f'{layout.site_url()}/dashboard.html'
        subject = (
            f"[InsightMatch] 확인하지 않은 알림 {total_count}건이 있습니다"
            if total_count > 1 else
            "[InsightMatch] 확인하지 않은 알림이 있습니다"
        )
        body = [layout.paragraph(f'{user_name or "회원"}님, 아직 확인하지 않으신 알림이 있어 모아서 보내 드립니다.')]
        for item in items:
            body.append(layout.item_card(
                item.get('title') or '알림',
                str(item.get('message') or '')[:300],
                item.get('link') or None,
            ))
        remaining = max(total_count - len(items), 0)
        if remaining:
            body.append(layout.paragraph(f'외 {remaining}건이 더 있습니다.', muted=True, size=14))

        first_title = (items[0].get('title') if items else None) or '새 알림'
        return self._send_rendered(to_email, subject, layout.render_email(
            subject=subject,
            eyebrow='알림 모아보기',
            tone='neutral',
            heading=f'확인하지 않은 알림 {total_count}건',
            preheader=str(first_title)[:80],
            body=body,
            cta=('대시보드에서 전체 보기', dashboard_url),
            footer_note='대시보드에서 알림을 확인하시면 같은 내용을 다시 보내 드리지 않습니다.',
        ))

    def send_reminder_notice(
        self,
        to_email: str,
        user_name: Optional[str],
        title: str,
        message: str,
        action_url: str,
        cta_label: str = "대시보드에서 처리하기"
    ) -> Dict:
        """처리를 기다리는 건이 있다는 리마인더 메일 (미서명 계약 / 무응답 견적 요청 / 리뷰 요청).

        하루라도 빨리 닿아야 하므로 미열람 승격을 기다리지 않고 인앱 알림을 만드는
        시점에 바로 보낸다. 호출부가 Notification.emailed_at 을 채우므로 승격 배치가
        같은 건을 다시 보내지 않는다.
        """
        safe_title = str(title or '처리가 필요한 항목이 있습니다')
        subject = f"[InsightMatch] {safe_title}"
        return self._send_rendered(to_email, subject, layout.render_email(
            subject=subject,
            eyebrow='처리 대기',
            tone='warn',
            heading=safe_title,
            preheader=str(message or '')[:80],
            body=[
                layout.paragraph(f'{user_name or "회원"}님, 아래 건이 처리를 기다리고 있습니다.'),
                layout.notice(None, [message or ''], tone='warn'),
            ],
            cta=(cta_label, action_url),
        ))

    def send_admin_alert(
        self,
        to_email: str,
        admin_name: Optional[str],
        subject_label: str,
        heading: str,
        summary: str,
        rows: Optional[List] = None,
        action_url: Optional[str] = None,
        cta_label: str = "관리자 화면에서 보기"
    ) -> Dict:
        """관리자에게 바로 알려야 하는 운영 이벤트 (신규 컨설턴트 심사 대기, 새 문의 등).

        Args:
            rows: [(라벨, 값)] — 표로 붙일 상세. 값은 표 부품이 이스케이프한다.
        """
        action_url = action_url or f'{layout.site_url()}/admin.html'
        return self._send_rendered(to_email, f"[InsightMatch] {subject_label}", layout.render_email(
            subject=f"[InsightMatch] {subject_label}",
            eyebrow='운영 알림',
            tone='neutral',
            heading=heading or '운영 알림',
            preheader=str(summary or '')[:80],
            body=[
                layout.paragraph(f'{admin_name or "관리자"}님, {summary or ""}'),
            ] + ([layout.info_table(rows)] if rows else []),
            cta=(cta_label, action_url),
            audience='admin',
        ))

    def send_consultant_invite(
        self,
        to_email: str,
        invite_name: Optional[str],
        invite_url: str,
        expires_at_text: str,
        ttl_days: int = 14,
        memo: Optional[str] = None
    ) -> Dict:
        """컨설턴트 초대 링크 메일.

        발급 시점에 메일로 함께 보낸다. 이 메일 실패가 초대 생성 자체를 실패시키면
        안 된다 — 복사·직접 전달 경로는 그대로 살아 있어야 한다.
        """
        body = [
            layout.paragraph(f'{invite_name or "전문가"}님, 안녕하세요.'),
            layout.paragraph('InsightMatch 운영팀에서 ISO 인증 전문가 등록을 부탁드리려고 초대 링크를 보내 드립니다. '
                             '아래 버튼을 눌러 프로필을 등록해 주세요.'),
        ]
        if memo:
            body.append(layout.notice('운영팀 전달 사항', [memo]))

        return self._send_rendered(to_email, "[InsightMatch] 전문가 등록 초대 링크 안내", layout.render_email(
            subject="[InsightMatch] 전문가 등록 초대 링크 안내",
            eyebrow='전문가 초대',
            heading='InsightMatch 전문가 등록에 초대합니다',
            preheader=f'초대 링크는 {ttl_days}일 동안 쓸 수 있습니다.',
            body=body,
            cta=('전문가 등록 시작하기', invite_url),
            after_cta=[
                layout.notice('유효기간', [
                    f'이 링크는 발급일로부터 {ttl_days}일간({expires_at_text} UTC까지) 쓸 수 있습니다.',
                    '한 번만 쓸 수 있으며, 기간이 지나면 운영팀에 다시 요청해 주세요.',
                ]),
                layout.fallback_link(invite_url),
            ],
            footer_note='본인이 요청하지 않은 초대라면 이 메일은 무시하셔도 됩니다.',
        ))


# 카카오톡 알림톡 준비용 클래스 (향후 구현)
class KakaoAlimtalkService:
    """
    카카오톡 알림톡 서비스 (향후 구현)
    
    필수 사전 작업:
    1. 사업자등록증으로 카카오 비즈니스 가입
    2. 카카오톡 채널 개설
    3. 발신 프로필 등록 및 심사 (2-3일)
    4. 메시지 템플릿 사전 승인 (1-2일)
    
    필요한 환경변수:
    - KAKAO_API_KEY
    - KAKAO_CHANNEL_ID
    
    메시지 템플릿 예시:
    ---
    [InsightMatch] 새로운 견적 요청
    
    #{컨설턴트명}님, 새로운 프로젝트 기회입니다!
    
    ▶ 요청 기업: #{기업명}
    ▶ 관심 인증: #{인증목록}
    ▶ 희망 일정: #{일정}
    
    대시보드에서 확인하기 ▶
    ---
    """
    
    def __init__(self):
        self.api_key = os.environ.get('KAKAO_API_KEY', '')
        self.channel_id = os.environ.get('KAKAO_CHANNEL_ID', '')
        self.is_configured = bool(self.api_key and self.channel_id)
        
        if not self.is_configured:
            print("[KakaoAlimtalkService] Not configured. Will be available after business registration.")
    
    def send_alimtalk(self, phone_number: str, template_code: str, variables: Dict) -> Dict:
        """
        알림톡 발송 (향후 구현)
        """
        if not self.is_configured:
            print(f"[KakaoAlimtalkService] Would send to: {phone_number}")
            print(f"[KakaoAlimtalkService] Template: {template_code}")
            print(f"[KakaoAlimtalkService] Variables: {variables}")
            return {
                'success': False,
                'message': 'Kakao Alimtalk not configured. Complete business registration first.',
                'not_implemented': True
            }
        
        # TODO: 실제 카카오 API 연동
        # import requests
        # response = requests.post(...)
        
        return {'success': False, 'message': 'Not implemented yet'}
