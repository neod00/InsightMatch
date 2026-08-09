"""
InsightMatch Email Service
이메일 발송 모듈 - 견적 요청 알림, 제안서 발송 등

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
        """SMTP 연결 생성"""
        server = smtplib.SMTP(self.smtp_host, self.smtp_port)
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
