"""
InsightMatch Email Service
이메일 발송 모듈 - 견적 요청 알림, 제안서 발송 등

지원 채널:
1. 이메일 (현재 구현) - SendGrid / SMTP
2. 카카오톡 알림톡 (향후 구현) - 사업자 인증 후

"""

import os
import html
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime
from typing import List, Dict, Optional


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


class EmailService:
    def __init__(self):
        # SMTP 설정 (환경변수에서 로드)
        self.smtp_host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 587))
        self.smtp_user = os.environ.get('SMTP_USER', '')
        self.smtp_password = os.environ.get('SMTP_PASSWORD', '')
        self.from_email = os.environ.get('FROM_EMAIL', 'noreply@insightmatch.com')
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
        dashboard_url: str = "https://www.insightmatch.com/dashboard.html"
    ) -> Dict:
        """
        컨설턴트에게 견적 요청 알림 이메일 발송
        """
        # 타임라인 한글 변환
        timeline_map = {
            'flexible': '여유 있음',
            '6months': '6개월 이내',
            '3months': '3개월 이내',
            '1month': '1개월 이내 (긴급)'
        }
        timeline_kr = timeline_map.get(timeline, timeline)
        
        # 예산 한글 변환
        budget_map = {
            'unknown': '미정',
            'under500': '500만원 미만',
            '500-1000': '500만원 ~ 1,000만원',
            '1000-2000': '1,000만원 ~ 2,000만원',
            '2000+': '2,000만원 이상'
        }
        budget_kr = budget_map.get(budget, budget)
        
        # ISO 규격 포맷
        standards_text = ', '.join(standards) if standards else '미정'
        
        subject = f"[InsightMatch] {company_name}에서 견적을 요청했습니다"
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Pretendard', -apple-system, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #10b981, #059669); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border: 1px solid #e2e8f0; }}
        .info-box {{ background: white; padding: 20px; border-radius: 8px; margin: 15px 0; border-left: 4px solid #10b981; }}
        .label {{ color: #64748b; font-size: 0.85rem; margin-bottom: 4px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .cta-button {{ display: inline-block; background: #10b981; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 20px; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 0.85rem; padding: 20px; }}
        .urgent {{ background: #fef3c7; border-left-color: #f59e0b; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0; font-size: 1.5rem;">🎉 새로운 견적 요청</h1>
            <p style="margin: 10px 0 0; opacity: 0.9;">{consultant_name}님, 새로운 프로젝트 기회입니다!</p>
        </div>
        
        <div class="content">
            <p>안녕하세요, {consultant_name}님.</p>
            <p><strong>{company_name}</strong>에서 귀하의 전문성을 필요로 하는 프로젝트에 견적 요청을 보냈습니다.</p>
            
            <div class="info-box {'urgent' if timeline == '1month' else ''}">
                <div class="label">요청 기업</div>
                <div class="value">{company_name}</div>
            </div>
            
            <div class="info-box">
                <div class="label">업종</div>
                <div class="value">{industry}</div>
            </div>
            
            <div class="info-box">
                <div class="label">관심 인증</div>
                <div class="value">{standards_text}</div>
            </div>
            
            {f'''<div class="info-box">
                <div class="label">주요 경영 이슈</div>
                <div class="value">{issues_summary}</div>
            </div>''' if issues_summary else ''}
            
            <div class="info-box">
                <div class="label">희망 일정</div>
                <div class="value">{timeline_kr}</div>
            </div>
            
            <div class="info-box">
                <div class="label">예산 범위</div>
                <div class="value">{budget_kr}</div>
            </div>
            
            {f'''<div class="info-box">
                <div class="label">추가 요청사항</div>
                <div class="value">{additional_notes}</div>
            </div>''' if additional_notes else ''}
            
            <p style="margin-top: 25px;">대시보드에서 상세 내용을 확인하고 제안서를 작성해주세요.</p>
            
            <a href="{dashboard_url}" class="cta-button">대시보드에서 확인하기 →</a>
        </div>
        
        <div class="footer">
            <p>이 이메일은 InsightMatch 플랫폼에서 발송되었습니다.</p>
            <p>© 2025 OpenBrain Limited. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
        
        return self.send_email(consultant_email, subject, html_content)
    
    def send_quote_confirmation_to_company(
        self,
        company_email: str,
        company_name: str,
        consultant_names: List[str],
        standards: List[str]
    ) -> Dict:
        """
        기업에게 견적 요청 완료 확인 이메일 발송
        """
        consultant_list = ', '.join(consultant_names)
        standards_text = ', '.join([s.split(':')[0] for s in standards]) if standards else '미정'
        
        subject = f"[InsightMatch] {len(consultant_names)}명의 전문가에게 견적 요청이 전달되었습니다"
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Pretendard', -apple-system, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #10b981, #059669); color: white; padding: 30px; border-radius: 12px 12px 0 0; text-align: center; }}
        .content {{ background: #f8fafc; padding: 30px; border: 1px solid #e2e8f0; }}
        .check-icon {{ font-size: 3rem; margin-bottom: 15px; }}
        .consultant-badge {{ display: inline-block; background: rgba(16, 185, 129, 0.1); color: #059669; padding: 6px 12px; border-radius: 100px; margin: 4px; font-size: 0.9rem; }}
        .next-steps {{ background: white; padding: 20px; border-radius: 8px; margin-top: 20px; }}
        .step {{ display: flex; align-items: flex-start; gap: 12px; margin: 12px 0; }}
        .step-number {{ background: #10b981; color: white; width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.8rem; flex-shrink: 0; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 0.85rem; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="check-icon">✅</div>
            <h1 style="margin: 0; font-size: 1.5rem;">견적 요청 완료!</h1>
        </div>
        
        <div class="content">
            <p>안녕하세요, {company_name}님.</p>
            <p>귀사의 <strong>{standards_text}</strong> 인증 프로젝트에 대한 견적 요청이 {len(consultant_names)}명의 전문가에게 전달되었습니다.</p>
            
            <p style="margin-top: 20px;"><strong>견적 요청된 전문가:</strong></p>
            <div>
                {''.join([f'<span class="consultant-badge">{name}</span>' for name in consultant_names])}
            </div>
            
            <div class="next-steps">
                <h3 style="margin-top: 0; color: #1e293b;">다음 단계</h3>
                <div class="step">
                    <span class="step-number">1</span>
                    <span>전문가들이 귀사의 요청을 검토합니다 (평균 24시간 이내)</span>
                </div>
                <div class="step">
                    <span class="step-number">2</span>
                    <span>각 전문가로부터 맞춤 제안서를 받게 됩니다</span>
                </div>
                <div class="step">
                    <span class="step-number">3</span>
                    <span>제안을 비교하고 가장 적합한 전문가를 선택하세요</span>
                </div>
            </div>
            
            <p style="margin-top: 25px; color: #64748b; font-size: 0.9rem;">
                💡 문의사항이 있으시면 <a href="mailto:openbrain.main@gmail.com">openbrain.main@gmail.com</a>으로 연락주세요.
            </p>
        </div>
        
        <div class="footer">
            <p>이 이메일은 InsightMatch 플랫폼에서 발송되었습니다.</p>
            <p>© 2025 OpenBrain Limited. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
        
        return self.send_email(company_email, subject, html_content)

    def send_proposal_notification(
        self,
        company_email: str,
        company_name: str,
        consultant_name: str,
        project_title: str,
        proposal_price: Optional[int] = None,
        proposal_duration: Optional[str] = None,
        dashboard_url: str = "https://www.insightmatch.com/dashboard.html"
    ) -> Dict:
        """
        기업에게 제안서 제출 알림 이메일 발송

        인앱 알림만 만들면 기업은 대시보드에 접속해야만 제안서 도착을 안다.
        견적 요청 확인 메일(send_quote_confirmation_to_company)의 바로 다음
        단계이므로 같은 구조·톤을 유지한다.
        """
        # 컨설턴트가 입력한 값(기간)이 그대로 HTML 본문에 들어가므로 이스케이프한다.
        safe_company = html.escape(str(company_name or '고객'))
        safe_consultant = html.escape(str(consultant_name or '전문가'))
        safe_title = html.escape(str(project_title or 'ISO 인증 프로젝트'))

        try:
            price_text = f"{int(proposal_price):,}원" if proposal_price else '제안서 참조'
        except (TypeError, ValueError):
            price_text = '제안서 참조'
        duration_text = html.escape(str(proposal_duration)) if proposal_duration else '제안서 참조'

        subject = f"[InsightMatch] {consultant_name}님이 제안서를 보냈습니다"

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Pretendard', -apple-system, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #10b981, #059669); color: white; padding: 30px; border-radius: 12px 12px 0 0; text-align: center; }}
        .content {{ background: #f8fafc; padding: 30px; border: 1px solid #e2e8f0; }}
        .info-box {{ background: white; padding: 20px; border-radius: 8px; margin: 15px 0; border-left: 4px solid #10b981; }}
        .label {{ color: #64748b; font-size: 0.85rem; margin-bottom: 4px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .cta-button {{ display: inline-block; background: #10b981; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 20px; }}
        .next-steps {{ background: white; padding: 20px; border-radius: 8px; margin-top: 20px; }}
        .step {{ display: flex; align-items: flex-start; gap: 12px; margin: 12px 0; }}
        .step-number {{ background: #10b981; color: white; width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.8rem; flex-shrink: 0; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 0.85rem; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="font-size: 3rem; margin-bottom: 15px;">📄</div>
            <h1 style="margin: 0; font-size: 1.5rem;">제안서가 도착했습니다</h1>
        </div>

        <div class="content">
            <p>안녕하세요, {safe_company}님.</p>
            <p><strong>{safe_consultant}</strong>님이 요청하신 프로젝트에 대한 제안서를 보냈습니다.</p>

            <div class="info-box">
                <div class="label">프로젝트</div>
                <div class="value">{safe_title}</div>
            </div>

            <div class="info-box">
                <div class="label">제안 전문가</div>
                <div class="value">{safe_consultant}</div>
            </div>

            <div class="info-box">
                <div class="label">제안 금액</div>
                <div class="value">{price_text}</div>
            </div>

            <div class="info-box">
                <div class="label">예상 기간</div>
                <div class="value">{duration_text}</div>
            </div>

            <div class="next-steps">
                <h3 style="margin-top: 0; color: #1e293b;">다음 단계</h3>
                <div class="step">
                    <span class="step-number">1</span>
                    <span>대시보드에서 제안서 상세 내용과 첨부 파일을 확인하세요</span>
                </div>
                <div class="step">
                    <span class="step-number">2</span>
                    <span>다른 전문가의 제안과 비교해보세요</span>
                </div>
                <div class="step">
                    <span class="step-number">3</span>
                    <span>가장 적합한 전문가를 선택하고 계약을 진행하세요</span>
                </div>
            </div>

            <p style="text-align: center;">
                <a href="{dashboard_url}" class="cta-button">제안서 확인하기 →</a>
            </p>

            <p style="margin-top: 25px; color: #64748b; font-size: 0.9rem;">
                💡 문의사항이 있으시면 <a href="mailto:openbrain.main@gmail.com">openbrain.main@gmail.com</a>으로 연락주세요.
            </p>
        </div>

        <div class="footer">
            <p>이 이메일은 InsightMatch 플랫폼에서 발송되었습니다.</p>
            <p>© 2025 OpenBrain Limited. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""

        return self.send_email(company_email, subject, html_content)

    # 컨설턴트 심사 결과 4개 이벤트의 메일 문안.
    # 인앱 알림 type 값(consultant_approved 등)을 그대로 키로 쓴다.
    REVIEW_RESULT_TEMPLATES = {
        'consultant_approved': {
            'subject': '[InsightMatch] 전문가 등록이 승인되었습니다',
            'icon': '🎉',
            'heading': '등록 승인 완료',
            'gradient': 'linear-gradient(135deg, #10b981, #059669)',
            'accent': '#10b981',
            'body': '제출해주신 전문가 프로필 검토가 완료되어 <strong>등록이 승인</strong>되었습니다. '
                    '이제 기업의 견적 요청 매칭 대상에 포함됩니다.',
            'cta': '대시보드로 이동 →',
        },
        'consultant_rejected': {
            'subject': '[InsightMatch] 전문가 등록 검토 결과 안내',
            'icon': '📝',
            'heading': '등록 검토 결과 안내',
            'gradient': 'linear-gradient(135deg, #f59e0b, #d97706)',
            'accent': '#f59e0b',
            'body': '제출해주신 전문가 프로필을 검토했으나 아래 사유로 이번에는 승인해드리지 못했습니다. '
                    '보완 후 다시 등록해주시면 재검토해드립니다.',
            'cta': '프로필 보완하고 재등록하기 →',
        },
        'consultant_verification_revoked': {
            'subject': '[InsightMatch] 전문가 인증 자격이 해제되었습니다',
            'icon': '⚠️',
            'heading': '인증 자격 해제 안내',
            'gradient': 'linear-gradient(135deg, #f59e0b, #d97706)',
            'accent': '#f59e0b',
            'body': '아래 사유로 전문가 인증 자격이 해제되었습니다. '
                    '해제 기간에는 신규 매칭 대상에서 제외되며, 사유가 해소되면 재심사를 요청하실 수 있습니다.',
            'cta': '대시보드에서 확인하기 →',
        },
        'consultant_restored': {
            'subject': '[InsightMatch] 전문가 프로필이 재검토 대기 상태가 되었습니다',
            'icon': '🔄',
            'heading': '재검토 대기 상태로 전환',
            'gradient': 'linear-gradient(135deg, #6366f1, #8b5cf6)',
            'accent': '#6366f1',
            'body': '전문가 프로필이 <strong>재검토 대기</strong> 상태로 전환되었습니다. '
                    '관리자 검토가 끝나면 결과를 다시 이메일로 안내드립니다.',
            'cta': '대시보드에서 확인하기 →',
        },
    }

    def send_consultant_review_result(
        self,
        consultant_email: str,
        consultant_name: str,
        notification_type: str,
        reason: Optional[str] = None,
        dashboard_url: str = "https://www.insightmatch.com/dashboard.html",
        register_url: Optional[str] = None
    ) -> Dict:
        """
        컨설턴트 심사 결과(승인·거절·자격해제·복원) 이메일 발송

        consultant_register.html 과 admin.html 이 이미 사용자에게
        "승인 완료 시 이메일로 안내드립니다" / "거부 사유는 이메일로 전달됩니다"
        라고 약속하고 있으므로, 인앱 알림만으로는 약속을 지키지 못한다.

        Args:
            notification_type: REVIEW_RESULT_TEMPLATES 의 키
            reason: 거절·자격해제 사유 (본문에 포함)
        """
        template = self.REVIEW_RESULT_TEMPLATES.get(notification_type)
        if not template:
            # 알 수 없는 이벤트로 엉뚱한 메일을 보내느니 보내지 않는다.
            print(f"[EmailService] 알 수 없는 심사 결과 유형: {notification_type}")
            return {'success': False, 'message': f'Unknown review result type: {notification_type}'}

        safe_name = html.escape(str(consultant_name or '전문가'))
        # 관리자가 입력한 사유가 그대로 HTML 본문에 들어가므로 이스케이프한다.
        safe_reason = html.escape(str(reason)).replace('\n', '<br>') if reason else ''

        reason_block = f"""
            <div class="reason-box">
                <div class="label">사유</div>
                <div class="value">{safe_reason}</div>
            </div>
""" if safe_reason else ''

        # 거절은 '재등록' 이 다음 행동이므로 등록 페이지로 보낸다.
        action_url = register_url if (notification_type == 'consultant_rejected' and register_url) else dashboard_url

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Pretendard', -apple-system, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: {template['gradient']}; color: white; padding: 30px; border-radius: 12px 12px 0 0; text-align: center; }}
        .content {{ background: #f8fafc; padding: 30px; border: 1px solid #e2e8f0; }}
        .reason-box {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid {template['accent']}; }}
        .label {{ color: #64748b; font-size: 0.85rem; margin-bottom: 4px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .cta-button {{ display: inline-block; background: {template['accent']}; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 20px; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 0.85rem; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="font-size: 3rem; margin-bottom: 15px;">{template['icon']}</div>
            <h1 style="margin: 0; font-size: 1.5rem;">{template['heading']}</h1>
        </div>

        <div class="content">
            <p>안녕하세요, {safe_name}님.</p>
            <p>{template['body']}</p>
{reason_block}
            <p style="text-align: center;">
                <a href="{action_url}" class="cta-button">{template['cta']}</a>
            </p>

            <p style="margin-top: 25px; color: #64748b; font-size: 0.9rem;">
                💡 문의사항이 있으시면 <a href="mailto:openbrain.main@gmail.com">openbrain.main@gmail.com</a>으로 연락주세요.
            </p>
        </div>

        <div class="footer">
            <p>이 이메일은 InsightMatch 플랫폼에서 발송되었습니다.</p>
            <p>© 2025 OpenBrain Limited. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""

        return self.send_email(consultant_email, template['subject'], html_content)

    def send_password_reset_email(
        self,
        to_email: str,
        user_name: str,
        reset_link: str
    ) -> Dict:
        """
        비밀번호 재설정 이메일 발송
        """
        subject = "[InsightMatch] 비밀번호 재설정 요청"
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Pretendard', -apple-system, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; padding: 30px; border-radius: 12px 12px 0 0; text-align: center; }}
        .content {{ background: #f8fafc; padding: 30px; border: 1px solid #e2e8f0; }}
        .cta-button {{ display: inline-block; background: #10b981; color: white; padding: 16px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; margin: 20px 0; }}
        .warning {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; margin: 20px 0; border-radius: 4px; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 0.85rem; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0; font-size: 1.5rem;">🔐 비밀번호 재설정</h1>
        </div>
        
        <div class="content">
            <p>안녕하세요, {user_name}님.</p>
            <p>InsightMatch 계정의 비밀번호 재설정 요청이 접수되었습니다.</p>
            
            <p style="text-align: center;">
                <a href="{reset_link}" class="cta-button">새 비밀번호 설정하기</a>
            </p>
            
            <div class="warning">
                <strong>⚠️ 주의사항</strong>
                <ul style="margin: 10px 0 0; padding-left: 20px;">
                    <li>이 링크는 <strong>30분간</strong> 유효합니다.</li>
                    <li>본인이 요청하지 않았다면 이 이메일을 무시하세요.</li>
                </ul>
            </div>
            
            <p style="color: #64748b; font-size: 0.9rem; margin-top: 25px;">
                문의사항이 있으시면 <a href="mailto:openbrain.main@gmail.com">openbrain.main@gmail.com</a>으로 연락주세요.
            </p>
        </div>
        
        <div class="footer">
            <p>이 이메일은 InsightMatch 플랫폼에서 발송되었습니다.</p>
            <p>© 2025 OpenBrain Limited. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
        
        return self.send_email(to_email, subject, html_content)

    @staticmethod
    def _digest_rows(groups: List[Dict], accent: str) -> str:
        """다이제스트 표의 행 HTML.

        메시지·경로에는 사용자 입력이 그대로 섞여 들어온다(예: 잘못된 입력값이
        예외 메시지에 포함된 경우). 관리자 메일함에서 렌더링되므로 반드시
        이스케이프한다.
        """
        if not groups:
            return (
                '<tr><td colspan="3" style="padding:12px 8px; color:#94a3b8; '
                'font-size:0.85rem;">없음</td></tr>'
            )

        rows = []
        for group in groups:
            exc_type = html.escape(str(group.get('excType') or '-'))
            message = html.escape(str(group.get('message') or '')[:200])
            path = html.escape(str(group.get('path') or '-'))
            count = html.escape(str(group.get('count') or 0))
            rows.append(f"""
                <tr style="border-bottom:1px solid #e2e8f0;">
                    <td style="padding:10px 8px; vertical-align:top;">
                        <div style="font-weight:600; color:{accent};">{exc_type}</div>
                        <div style="font-size:0.8rem; color:#64748b;">{path}</div>
                    </td>
                    <td style="padding:10px 8px; font-size:0.85rem; color:#334155; word-break:break-word;">{message}</td>
                    <td style="padding:10px 8px; text-align:right; font-weight:700;">{count}</td>
                </tr>""")
        return ''.join(rows)

    def send_error_digest(
        self,
        to_email: str,
        admin_name: Optional[str],
        hours: int,
        error_count: int,
        warning_count: int,
        error_groups: List[Dict],
        warning_groups: List[Dict],
        admin_url: str = "https://www.insightmatch.com/admin.html"
    ) -> Dict:
        """관리자에게 보내는 오류 일일 다이제스트.

        ErrorLog 는 관리자가 화면을 열어봐야만 보인다. 즉 "보러 가지 않으면
        아무 일도 없는 것처럼 보인다". 하루 한 번 요약을 밀어넣어야 관측성이
        실제로 작동한다. 단, 0건일 때는 호출부에서 발송을 건너뛴다 —
        매일 오는 '이상 없음' 메일은 곧 읽히지 않고, 그러면 문제가 생긴 날의
        메일까지 함께 묻힌다.

        미처리 예외(error)와 부분 실패(warning)를 나눠 보여준다.
        한 칸에 섞으면 500 이 늘었는지 메일만 막혔는지 구분할 수 없다.
        """
        safe_name = html.escape(str(admin_name or '관리자'))
        subject = f"[InsightMatch] 오류 요약 — 예외 {error_count}건 / 부분 실패 {warning_count}건"

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Pretendard', -apple-system, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 640px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #ef4444, #f97316); color: white; padding: 26px; border-radius: 12px 12px 0 0; text-align: center; }}
        .content {{ background: #f8fafc; padding: 26px; border: 1px solid #e2e8f0; }}
        .stat-row {{ display: flex; gap: 12px; margin: 0 0 22px; }}
        .stat {{ flex: 1; background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; text-align: center; }}
        .stat .num {{ font-size: 1.8rem; font-weight: 800; }}
        .stat .label {{ font-size: 0.8rem; color: #64748b; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #e2e8f0; border-radius: 8px; }}
        th {{ text-align: left; font-size: 0.78rem; color: #64748b; padding: 8px; border-bottom: 1px solid #e2e8f0; }}
        h3 {{ font-size: 0.95rem; margin: 22px 0 8px; }}
        .cta-button {{ display: inline-block; background: #ef4444; color: white; padding: 13px 26px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 22px; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 0.85rem; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="font-size: 2.4rem; margin-bottom: 8px;">🚨</div>
            <h1 style="margin: 0; font-size: 1.35rem;">지난 {hours}시간 오류 요약</h1>
        </div>

        <div class="content">
            <p>{safe_name}님, 지난 {hours}시간 동안 기록된 오류입니다.</p>

            <div class="stat-row">
                <div class="stat">
                    <div class="num" style="color:#ef4444;">{error_count}</div>
                    <div class="label">미처리 예외 (500)</div>
                </div>
                <div class="stat">
                    <div class="num" style="color:#f59e0b;">{warning_count}</div>
                    <div class="label">부분 실패 (메일 등)</div>
                </div>
            </div>

            <h3 style="color:#ef4444;">미처리 예외 — 요청이 실패했습니다</h3>
            <table>
                <thead><tr><th>예외 / 경로</th><th>메시지</th><th style="text-align:right;">횟수</th></tr></thead>
                <tbody>{self._digest_rows(error_groups, '#ef4444')}</tbody>
            </table>

            <h3 style="color:#f59e0b;">부분 실패 — 요청은 성공했지만 후속 작업이 실패했습니다</h3>
            <table>
                <thead><tr><th>종류 / 경로</th><th>메시지</th><th style="text-align:right;">횟수</th></tr></thead>
                <tbody>{self._digest_rows(warning_groups, '#f59e0b')}</tbody>
            </table>

            <p style="text-align: center;">
                <a href="{admin_url}" class="cta-button">관리자 화면에서 스택 트레이스 보기 →</a>
            </p>
        </div>

        <div class="footer">
            <p>이 이메일은 InsightMatch 일일 배치에서 자동 발송되었습니다.</p>
            <p>© 2025 OpenBrain Limited. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""

        return self.send_email(to_email, subject, html_content)

    # ------------------------------------------------------------------
    # 통지·리마인더 (L1-B)
    # ------------------------------------------------------------------
    # 아래 메일은 전부 **거래적(transactional)** 통지다. 회원 본인의 계정·
    # 프로젝트 진행 상황에 대한 안내이며 광고·홍보 문구를 넣지 않는다.
    # 정보통신망법상 광고성 정보는 별도 수신동의가 필요하므로, 성격이
    # 오해되지 않도록 본문 하단에 안내 문구를 명시한다.
    TRANSACTIONAL_NOTICE = (
        '이 메일은 회원님의 InsightMatch 활동(계정·프로젝트 진행 상황)에 대한 안내입니다. '
        '광고성 정보가 아닙니다.'
    )

    def _footer(self, extra: Optional[str] = None) -> str:
        """모든 통지 메일 공통 푸터 (거래적 메일임을 명시)."""
        extra_line = f'<p>{extra}</p>' if extra else ''
        return f"""
        <div class="footer">
            <p>{self.TRANSACTIONAL_NOTICE}</p>
            {extra_line}
            <p>© 2025 OpenBrain Limited. All rights reserved.</p>
        </div>"""

    @staticmethod
    def _base_style(accent: str, gradient: str) -> str:
        """통지 메일 공통 스타일. 기존 템플릿들과 같은 톤을 유지한다."""
        return f"""
        body {{ font-family: 'Pretendard', -apple-system, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 620px; margin: 0 auto; padding: 20px; }}
        .header {{ background: {gradient}; color: white; padding: 26px; border-radius: 12px 12px 0 0; text-align: center; }}
        .content {{ background: #f8fafc; padding: 26px; border: 1px solid #e2e8f0; }}
        .item {{ background: white; border: 1px solid #e2e8f0; border-left: 4px solid {accent}; border-radius: 8px; padding: 14px 16px; margin: 0 0 12px; }}
        .item .t {{ font-weight: 700; color: #1e293b; margin-bottom: 4px; }}
        .item .m {{ font-size: 0.9rem; color: #475569; }}
        .item .go {{ display: inline-block; margin-top: 8px; font-size: 0.85rem; color: {accent}; text-decoration: none; font-weight: 600; }}
        .kv {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 4px 16px; margin: 16px 0; }}
        .kv div {{ padding: 8px 0; border-bottom: 1px solid #f1f5f9; font-size: 0.9rem; }}
        .kv div:last-child {{ border-bottom: none; }}
        .kv .label {{ color: #64748b; display: inline-block; min-width: 92px; }}
        .cta-button {{ display: inline-block; background: {accent}; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 20px; }}
        .note {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 14px; margin: 20px 0; border-radius: 4px; font-size: 0.9rem; }}
        .footer {{ text-align: center; color: #94a3b8; font-size: 0.8rem; padding: 20px; line-height: 1.5; }}"""

    def send_notification_digest(
        self,
        to_email: str,
        user_name: Optional[str],
        items: List[Dict],
        total_count: int,
        dashboard_url: str = "https://www.insightmatch.com/dashboard.html"
    ) -> Dict:
        """읽지 않은 인앱 알림을 **사용자당 1통**으로 묶어 보낸다.

        알림은 지금까지 인앱 전용이라 사용자가 사이트에 접속하지 않으면 아무것도
        모르는 상태였다. 이벤트마다 메일 코드를 붙이는 대신, 이미 잘 쌓이고 있는
        Notification 행을 하루 한 번 메일로 승격시킨다.

        묶는 것이 핵심이다. 미열람이 5건일 때 메일 5통을 보내면 알림 자체가
        소음이 되어 정작 중요한 메일까지 무시된다.

        Args:
            items: [{'title', 'message', 'link'}] — 본문에 나열할 알림 (이미 잘려 있음)
            total_count: 실제 미열람 총건수 (items 보다 많으면 '외 N건' 으로 표기)
        """
        safe_name = html.escape(str(user_name or '회원'))
        subject = (
            f"[InsightMatch] 확인하지 않은 알림 {total_count}건이 있습니다"
            if total_count > 1 else
            "[InsightMatch] 확인하지 않은 알림이 있습니다"
        )

        blocks = []
        for item in items:
            # 알림 제목·본문에는 프로젝트명·상대방 이름 등 사용자 입력이 그대로
            # 들어간다. 메일 클라이언트에서 렌더링되므로 반드시 이스케이프한다.
            title = html.escape(str(item.get('title') or '알림'))
            message = html.escape(str(item.get('message') or '')[:300])
            link = item.get('link') or ''
            go = (
                f'<a class="go" href="{html.escape(str(link), quote=True)}">바로 확인하기 →</a>'
                if link else ''
            )
            blocks.append(f"""
            <div class="item">
                <div class="t">{title}</div>
                <div class="m">{message}</div>
                {go}
            </div>""")

        remaining = max(total_count - len(items), 0)
        more_block = (
            f'<p style="color:#64748b; font-size:0.9rem;">외 {remaining}건이 더 있습니다.</p>'
            if remaining else ''
        )

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>{self._base_style('#6366f1', 'linear-gradient(135deg, #6366f1, #8b5cf6)')}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="font-size: 2.2rem; margin-bottom: 8px;">🔔</div>
            <h1 style="margin: 0; font-size: 1.3rem;">확인하지 않은 알림 {total_count}건</h1>
        </div>

        <div class="content">
            <p>{safe_name}님, 아직 확인하지 않으신 알림이 있어 안내드립니다.</p>
            {''.join(blocks)}
            {more_block}
            <p style="text-align: center;">
                <a href="{dashboard_url}" class="cta-button">대시보드에서 전체 보기 →</a>
            </p>
        </div>
{self._footer('알림을 대시보드에서 확인하시면 같은 내용을 다시 보내드리지 않습니다.')}
    </div>
</body>
</html>
"""

        return self.send_email(to_email, subject, html_content)

    def send_reminder_notice(
        self,
        to_email: str,
        user_name: Optional[str],
        title: str,
        message: str,
        action_url: str,
        cta_label: str = "대시보드에서 처리하기"
    ) -> Dict:
        """"당신이 늦고 있다" 는 리마인더 메일 (미서명 계약 / 무응답 견적 요청).

        이 두 건은 성격상 하루라도 빨리 닿아야 하므로, 미열람 승격을 기다리지 않고
        인앱 알림을 만드는 그 시점에 바로 보낸다. 호출부가 Notification.emailed_at
        을 채우므로 승격 배치가 같은 건을 다시 보내지 않는다.
        """
        safe_name = html.escape(str(user_name or '회원'))
        safe_title = html.escape(str(title or '처리가 필요한 항목이 있습니다'))
        safe_message = html.escape(str(message or '')).replace('\n', '<br>')

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>{self._base_style('#f59e0b', 'linear-gradient(135deg, #f59e0b, #f97316)')}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="font-size: 2.2rem; margin-bottom: 8px;">⏰</div>
            <h1 style="margin: 0; font-size: 1.3rem;">{safe_title}</h1>
        </div>

        <div class="content">
            <p>{safe_name}님, 아래 건이 처리를 기다리고 있습니다.</p>
            <div class="item">
                <div class="m">{safe_message}</div>
            </div>
            <p style="text-align: center;">
                <a href="{action_url}" class="cta-button">{html.escape(str(cta_label))}</a>
            </p>
        </div>
{self._footer()}
    </div>
</body>
</html>
"""

        return self.send_email(to_email, f"[InsightMatch] {safe_title}", html_content)

    def send_admin_alert(
        self,
        to_email: str,
        admin_name: Optional[str],
        subject_label: str,
        heading: str,
        summary: str,
        rows: Optional[List] = None,
        action_url: str = "https://www.insightmatch.com/admin.html",
        cta_label: str = "관리자 화면에서 보기 →"
    ) -> Dict:
        """관리자에게 즉시 알려야 하는 운영 이벤트 (예: 신규 컨설턴트 심사 대기).

        관리자가 화면을 직접 새로고침해야만 알 수 있으면 승인이 늦어지고,
        컨설턴트는 방치됐다고 느낀다.

        Args:
            rows: [(라벨, 값)] — 표 형태로 붙일 상세. 값은 전부 이스케이프한다.
        """
        safe_admin = html.escape(str(admin_name or '관리자'))
        safe_heading = html.escape(str(heading or '운영 알림'))
        safe_summary = html.escape(str(summary or '')).replace('\n', '<br>')

        row_html = ''
        if rows:
            cells = []
            for label, value in rows:
                cells.append(
                    f'<div><span class="label">{html.escape(str(label))}</span>'
                    f'<strong>{html.escape(str(value if value not in (None, "") else "-"))}</strong></div>'
                )
            row_html = f'<div class="kv">{"".join(cells)}</div>'

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>{self._base_style('#0ea5e9', 'linear-gradient(135deg, #0ea5e9, #6366f1)')}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="font-size: 2.2rem; margin-bottom: 8px;">🛎️</div>
            <h1 style="margin: 0; font-size: 1.3rem;">{safe_heading}</h1>
        </div>

        <div class="content">
            <p>{safe_admin}님, {safe_summary}</p>
            {row_html}
            <p style="text-align: center;">
                <a href="{action_url}" class="cta-button">{html.escape(str(cta_label))}</a>
            </p>
        </div>
{self._footer('이 메일은 InsightMatch 운영자에게 발송되는 관리용 통지입니다.')}
    </div>
</body>
</html>
"""

        return self.send_email(to_email, f"[InsightMatch] {subject_label}", html_content)

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

        지금까지는 관리자가 발급된 URL 을 복사해 카톡 등으로 직접 전달해야 했다.
        발급 시점에 메일로 함께 보낸다. 단, 이 메일 실패가 초대 생성 자체를
        실패시키면 안 된다 — 복사·직접 전달 경로는 그대로 살아 있어야 한다.
        """
        safe_name = html.escape(str(invite_name or '전문가'))
        safe_expires = html.escape(str(expires_at_text or ''))
        safe_url = html.escape(str(invite_url or ''), quote=True)
        # 관리자가 입력한 메모가 본문에 들어간다.
        safe_memo = html.escape(str(memo)).replace('\n', '<br>') if memo else ''
        memo_block = (
            f'<div class="item"><div class="t">전달 사항</div><div class="m">{safe_memo}</div></div>'
            if safe_memo else ''
        )

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>{self._base_style('#10b981', 'linear-gradient(135deg, #10b981, #059669)')}</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="font-size: 2.2rem; margin-bottom: 8px;">✉️</div>
            <h1 style="margin: 0; font-size: 1.3rem;">InsightMatch 전문가 등록 초대</h1>
        </div>

        <div class="content">
            <p>안녕하세요, {safe_name}님.</p>
            <p>InsightMatch 운영팀이 ISO 인증 전문가 등록을 위해 초대 링크를 보내드립니다.
               아래 버튼을 눌러 프로필을 등록해주세요.</p>
            {memo_block}
            <p style="text-align: center;">
                <a href="{safe_url}" class="cta-button">전문가 등록 시작하기</a>
            </p>

            <div class="note">
                <strong>⚠️ 유효기간 안내</strong>
                <ul style="margin: 8px 0 0; padding-left: 20px;">
                    <li>이 링크는 발급일로부터 <strong>{ttl_days}일간</strong>({safe_expires} UTC까지) 유효합니다.</li>
                    <li>1회만 사용할 수 있으며, 만료 후에는 운영팀에 재발급을 요청해주세요.</li>
                </ul>
            </div>

            <p style="color: #64748b; font-size: 0.85rem; word-break: break-all;">
                버튼이 동작하지 않으면 아래 주소를 브라우저에 붙여넣어 주세요.<br>{safe_url}
            </p>

            <p style="color: #64748b; font-size: 0.9rem; margin-top: 20px;">
                문의사항이 있으시면 <a href="mailto:openbrain.main@gmail.com">openbrain.main@gmail.com</a>으로 연락주세요.
            </p>
        </div>
{self._footer('본인이 요청하지 않은 초대라면 이 메일을 무시하셔도 됩니다.')}
    </div>
</body>
</html>
"""

        return self.send_email(to_email, "[InsightMatch] 전문가 등록 초대 링크 안내", html_content)


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
