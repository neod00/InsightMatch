import os
import psycopg2
import json
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get('DATABASE_URL')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

output = []

# Get consultant profile details
output.append("=" * 70)
output.append("컨설턴트 프로필 상세 (consultant@consultant.com)")
output.append("=" * 70)

cursor.execute("""
    SELECT id, name, user_id, status, verified, specialty, experience, rating, reviews,
           iso_experience, industry_experience, detailed_certifications, recent_projects,
           profile_image_url, bio, phone, email, company_name
    FROM consultant 
    WHERE user_id = 19
""")
consultant = cursor.fetchone()

if consultant:
    output.append(f"ID: {consultant[0]}")
    output.append(f"Name: {consultant[1]}")
    output.append(f"UserID: {consultant[2]}")
    output.append(f"Status: {consultant[3]}")
    output.append(f"Verified: {consultant[4]}")
    output.append(f"Specialty: {consultant[5]}")
    output.append(f"Experience: {consultant[6]}")
    output.append(f"Rating: {consultant[7]}")
    output.append(f"Reviews: {consultant[8]}")
    output.append(f"ProfileImageURL: {consultant[13]}")
    output.append(f"Bio: {consultant[14]}")
    output.append(f"Phone: {consultant[15]}")
    output.append(f"Email: {consultant[16]}")
    output.append(f"CompanyName: {consultant[17]}")
    output.append("")
    output.append("ISO Experience (raw):")
    output.append(f"  {consultant[9]}")
    output.append("")
    output.append("Industry Experience (raw):")
    output.append(f"  {consultant[10]}")
    output.append("")
    output.append("Detailed Certifications (raw):")
    output.append(f"  {consultant[11]}")
    output.append("")
    output.append("Recent Projects (raw):")
    output.append(f"  {consultant[12]}")
else:
    output.append("Consultant not found!")

conn.close()

with open('consultant_profile_detail.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print("Result saved to consultant_profile_detail.txt")
