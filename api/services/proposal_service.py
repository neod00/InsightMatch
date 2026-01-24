from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
import io

class ProposalService:
    def __init__(self):
        # Register Korean font for Windows environment
        self.font_name = "Helvetica"
        font_path = r"C:\Windows\Fonts\malgun.ttf"
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont('MalgunGothic', font_path))
                self.font_name = "MalgunGothic"
            except Exception as e:
                print(f"Font registration error: {e}")

    def generate_proposal(self, project, consultant, company_name):
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        # Font setup
        title_font = self.font_name + "-Bold" if self.font_name == "Helvetica" else self.font_name
        body_font = self.font_name

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
        msg_lines = self._wrap_text(message, 80)
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

    def _wrap_text(self, text, char_limit):
        lines = []
        if not text: return lines
        for p in text.split('\n'):
            if not p:
                lines.append("")
                continue
            while len(p) > char_limit:
                lines.append(p[:char_limit])
                p = p[char_limit:]
            lines.append(p)
        return lines


