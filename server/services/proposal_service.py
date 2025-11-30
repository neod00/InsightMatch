from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
import io

class ProposalService:
    def __init__(self):
        # Register a Korean font if available, otherwise use standard
        # For MVP/Windows environment without specific font file, we might have issues with Korean.
        # Ideally we should bundle a font like NanumGothic.ttf.
        # For this demo, we'll try to use a standard font or just English if Korean fails, 
        # but let's try to assume a font exists or use a default that might work.
        # Actually, reportlab needs a TTF file registered to support Korean.
        # We will skip Korean characters in PDF for this MVP step to avoid file missing errors,
        # OR we can try to use a system font if we know the path.
        pass

    def generate_proposal(self, project, consultant, company_name):
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        # Title
        c.setFont("Helvetica-Bold", 24)
        c.drawString(50, height - 50, "Project Proposal")

        # Project Info
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 100, f"Project Title: {project.title}")
        c.drawString(50, height - 120, f"Prepared for: {company_name}")
        c.drawString(50, height - 140, f"Consultant: {consultant.name}")
        c.drawString(50, height - 160, f"Date: {project.created_at.strftime('%Y-%m-%d')}")

        # Body
        c.drawString(50, height - 200, "Scope of Work:")
        y = height - 220
        for milestone in project.milestones:
            c.drawString(70, y, f"- {milestone.title}")
            y -= 20

        c.drawString(50, y - 40, "Terms and Conditions:")
        c.drawString(50, y - 60, "1. This proposal is valid for 30 days.")
        c.drawString(50, y - 80, "2. Payment terms: 50% upfront, 50% upon completion.")

        # Signature Section
        c.line(50, 150, 250, 150)
        c.drawString(50, 130, "Authorized Signature")
        c.drawString(50, 115, f"{company_name}")

        c.line(350, 150, 550, 150)
        c.drawString(350, 130, "Consultant Signature")
        c.drawString(350, 115, f"{consultant.name}")

        c.save()
        buffer.seek(0)
        return buffer
