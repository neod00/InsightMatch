import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get('DATABASE_URL')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

# Check user "김성혁" (likely the test user)
print("SEARCHING FOR USER '김성혁'...")
cursor.execute('SELECT id, name, email, role FROM "user" WHERE name LIKE %s', ('%김성혁%',))
users = cursor.fetchall()

for user in users:
    user_id, name, email, role = user
    print(f"\nFound User: {name} (ID: {user_id}, Email: {email}, Role: {role})")
    
    if role == 'company':
        # Check projects by company_id
        cursor.execute("SELECT id, title, status FROM project WHERE company_id = %s ORDER BY created_at DESC", (user_id,))
        projects = cursor.fetchall()
        print(f"  Projects as company ({len(projects)}):")
        for p in projects:
            print(f"    - ID:{p[0]} | {p[1][:40] if p[1] else 'N/A'} | Status:{p[2]}")
            
    elif role == 'consultant':
        # Check consultant profile
        cursor.execute("SELECT id, name, status FROM consultant WHERE user_id = %s", (user_id,))
        consultant = cursor.fetchone()
        if consultant:
            print(f"  Consultant Profile: ID:{consultant[0]}, Name:{consultant[1]}, Status:{consultant[2]}")
            
            # Check projects as consultant
            cursor.execute("SELECT id, title, status, company_id FROM project WHERE consultant_id = %s ORDER BY created_at DESC", (consultant[0],))
            projects = cursor.fetchall()
            print(f"  Projects as consultant ({len(projects)}):")
            for p in projects:
                print(f"    - ID:{p[0]} | {p[1][:40] if p[1] else 'N/A'} | Status:{p[2]} | Company:{p[3]}")
        else:
            print("  No consultant profile linked to this user")

# Also check analysis_jobs for 김성혁
print("\n" + "=" * 60)
print("ANALYSIS JOBS (QUOTE REQUESTS) FOR '김성혁':")
cursor.execute("SELECT id, company_name, status FROM analysis_job WHERE company_name LIKE %s ORDER BY created_at DESC LIMIT 5", ('%김성혁%',))
jobs = cursor.fetchall()
for j in jobs:
    print(f"  {j[0][:8]}... | {j[1]} | Status:{j[2]}")

conn.close()
