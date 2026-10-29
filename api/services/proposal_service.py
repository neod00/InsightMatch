from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
import io

# 저장소에 함께 둔 나눔고딕(OFL, api/fonts/OFL.txt). 예전에는 Windows 의 맑은 고딕만 찾아서
# 운영 서버(Vercel·리눅스)에서는 한글이 없는 Helvetica 로 찍혀 글자가 전부 ■ 로 깨졌다.
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fonts')
BODY_FONT = 'NanumGothic'
TITLE_FONT = 'NanumGothic-Bold'


def _register_fonts():
    registered = pdfmetrics.getRegisteredFontNames()
    for name, filename in ((BODY_FONT, 'NanumGothic-Regular.ttf'), (TITLE_FONT, 'NanumGothic-Bold.ttf')):
        if name not in registered:
            pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, filename)))


class ProposalService:
    def __init__(self):
        self.font_name = BODY_FONT

    def generate_proposal(self, project, consultant, company_name):
        # 글꼴은 PDF 를 만들 때 등록한다. 앱 시작 시 등록하면 글꼴 파일이 빠졌을 때 API 전체가 뜨지 않는다.
        # 파일이 없으면 깨진 PDF 를 조용히 내보내는 대신 이 요청만 오류로 드러난다.
        _register_fonts()
        buffer = io.BytesIO()
        # 기본 글꼴도 나눔고딕으로 둬서 한글이 없는 Helvetica 가 PDF 에 섞이지 않게 한다.
        c = canvas.Canvas(buffer, pagesize=letter, initialFontName=BODY_FONT, initialFontSize=11)
        width, height = letter

        # Font setup
        title_font = TITLE_FONT
        body_font = BODY_FONT

        # Header Section
        c.setFont(title_font, 24)
        c.drawString(50, height - 50, "서비스 제안서 (Service Proposal)")
        c.setStrokeColorRGB(0.06, 0.72, 0.5) # Primary color
        c.setLineWidth(2)
        c.line(50, height - 60, width - 50, height - 60)

        # Project Basic Info
        c.setFont(title_font, 14)
        c.drawString(50, height - 90, "1. 프로젝트 개요")
        c.setFont(body_font, 11)
        c.drawString(70, height - 110, f"프로젝트명: {project.title}")
        c.drawString(70, height - 125, f"고객사: {company_name}")
        c.drawString(70, height - 140, f"담당 컨설턴트: {consultant.name}")
        c.drawString(70, height - 155, f"작성 일자: {project.proposal_submitted_at.strftime('%Y-%m-%d') if project.proposal_submitted_at else project.created_at.strftime('%Y-%m-%d')}")

        # Quotation Details
        c.setFont(title_font, 14)
        y = height - 190
        c.drawString(50, y, "2. 제안 견적 및 기간")
        y -= 20
        c.setFont(body_font, 11)
        price_str = f"{project.proposal_price:,}원" if project.proposal_price else "별도 협의"
        c.drawString(70, y, f"총 제안 금액: {price_str} (VAT 별도)")
        y -= 15
        c.drawString(70, y, f"예상 소요 기간: {project.proposal_duration or '별도 협의'}")

        # Proposal Message
        y -= 40
        c.setFont(title_font, 14)
        c.drawString(50, y, "3. 컨설턴트 메시지")
        y -= 25
        c.setFont(body_font, 11)
        
        # Text wrapping for message
        message = project.proposal_message or "제안 메시지가 없습니다."
        msg_lines = self._wrap_text(message, width - 120, body_font, 11)
        for line in msg_lines:
            if y < 150: # Check for page break (simplified)
                c.showPage()
                y = height - 50
                c.setFont(body_font, 11)
            c.drawString(70, y, line)
            y -= 15

        # Terms
        y -= 40
        if y < 150:
            c.showPage()
            y = height - 50
        c.setFont(title_font, 14)
        c.drawString(50, y, "4. 안내 사항")
        y -= 20
        c.setFont(body_font, 10)
        c.drawString(70, y, "• 본 제안서는 InsightMatch 플랫폼을 통해 공식적으로 전달된 문서입니다.")
        y -= 15
        c.drawString(70, y, "• 상세 일정 및 마일스톤은 플랫폼 내 대시보드를 통해 최종 확정됩니다.")

        # Footer / Signatures
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.line(50, 120, width - 50, 120)
        
        c.setFont(body_font, 9)
        footer_text = "InsightMatch - AI 기반 ISO 컨설턴트 매칭 서비스"
        c.drawCentredString(width/2, 100, footer_text)

        c.save()
        buffer.seek(0)
        return buffer

    def _wrap_text(self, text, max_width, font_name, font_size):
        """글자 수가 아니라 실제 폭으로 줄을 나눈다. 한글은 영문보다 넓어 글자 수 기준이면 종이 밖으로 넘친다."""
        lines = []
        if not text: return lines
        for p in text.split('\n'):
            if not p:
                lines.append("")
                continue
            line = ''
            for ch in p:
                if line and pdfmetrics.stringWidth(line + ch, font_name, font_size) > max_width:
                    lines.append(line)
                    line = ch
                else:
                    line += ch
            lines.append(line)
        return lines


