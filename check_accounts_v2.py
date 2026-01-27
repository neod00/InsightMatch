import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get('DATABASE_URL')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

output = []

# 1. Company account
output.append("=" * 70)
output.append("1. 기업회원 계정: neod00@naver.com")
output.append("=" * 70)

cursor.execute('SELECT id, name, email, role FROM "user" WHERE email = %s', ('neod00@naver.com',))
user = cursor.fetchone()

if user:
    user_id, name, email, role = user
    output.append(f"User Found: ID={user_id}, Name={name}, Email={email}, Role={role}")
    
    cursor.execute("SELECT id, title, status, consultant_id FROM project WHERE company_id = %s ORDER BY created_at DESC", (user_id,))
    projects = cursor.fetchall()
    output.append(f"\nProjects for this company: {len(projects)}건")
    for p in projects:
        title = p[1][:40] if p[1] else 'N/A'
        output.append(f"  ID:{p[0]} | {title} | Status:{p[2]} | Consultant:{p[3]}")
else:
    output.append("User NOT FOUND!")

# 2. Consultant account
output.append("")
output.append("=" * 70)
output.append("2. 컨설턴트 계정: consultant@consultant.com")
output.append("=" * 70)

cursor.execute('SELECT id, name, email, role FROM "user" WHERE email = %s', ('consultant@consultant.com',))
user = cursor.fetchone()

if user:
    user_id, name, email, role = user
    output.append(f"User Found: ID={user_id}, Name={name}, Email={email}, Role={role}")
    
    cursor.execute("SELECT id, name, user_id, status, verified, profile_image_url FROM consultant WHERE user_id = %s", (user_id,))
    consultant = cursor.fetchone()
    
    if consultant:
        c_id, c_name, c_user_id, c_status, c_verified, c_profile = consultant
        output.append(f"\nConsultant Profile:")
        output.append(f"  ID: {c_id}")
        output.append(f"  Name: {c_name}")
        output.append(f"  Status: {c_status}")
        output.append(f"  Verified: {c_verified}")
        output.append(f"  ProfileImage: {c_profile}")
        
        cursor.execute("SELECT id, title, status, company_id FROM project WHERE consultant_id = %s ORDER BY created_at DESC", (c_id,))
        projects = cursor.fetchall()
        output.append(f"\nProjects assigned: {len(projects)}건")
        for p in projects:
            title = p[1][:40] if p[1] else 'N/A'
            output.append(f"  ID:{p[0]} | {title} | Status:{p[2]} | Company:{p[3]}")
    else:
        output.append("\nNo Consultant profile linked!")
else:
    output.append("User NOT FOUND!")

conn.close()

# Write to file and print
with open('account_check_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print("Result saved to account_check_result.txt")
