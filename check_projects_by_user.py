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

# Check projects for user_id 21 (김성혁2/DK인터내셔널)
user_id = 21
print(f"CHECKING PROJECTS FOR USER_ID: {user_id}")
print("=" * 60)

cursor.execute("SELECT id, title, status, company_id, consultant_id, created_at FROM project WHERE company_id = %s ORDER BY created_at DESC", (user_id,))
projects = cursor.fetchall()
print(f"Projects where company_id = {user_id}: {len(projects)}")
for p in projects:
    print(f"  ID:{p[0]} | Title:{p[1][:50] if p[1] else 'N/A'} | Status:{p[2]} | ConsultantID:{p[4]}")

# Also check if user is a consultant
cursor.execute("SELECT id FROM consultant WHERE user_id = %s", (user_id,))
consultant = cursor.fetchone()
if consultant:
    consultant_id = consultant[0]
    print(f"\nUser is also a consultant (ID: {consultant_id})")
    cursor.execute("SELECT id, title, status, company_id FROM project WHERE consultant_id = %s ORDER BY created_at DESC", (consultant_id,))
    as_consultant = cursor.fetchall()
    print(f"Projects where consultant_id = {consultant_id}: {len(as_consultant)}")
    for p in as_consultant:
        print(f"  ID:{p[0]} | Title:{p[1][:50] if p[1] else 'N/A'} | Status:{p[2]} | CompanyID:{p[3]}")

print("\n" + "=" * 60)
print("CHECKING PROJECTS FOR USER_ID: 22 (김성혁2)")
print("=" * 60)
user_id = 22

cursor.execute("SELECT id, title, status, company_id, consultant_id, created_at FROM project WHERE company_id = %s ORDER BY created_at DESC", (user_id,))
projects = cursor.fetchall()
print(f"Projects where company_id = {user_id}: {len(projects)}")
for p in projects:
    print(f"  ID:{p[0]} | Title:{p[1][:50] if p[1] else 'N/A'} | Status:{p[2]} | ConsultantID:{p[4]}")

conn.close()
