import os
import psycopg2
import json
from dotenv import load_dotenv
import sys

load_dotenv()

db_url = os.environ.get('DATABASE_URL')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

# 1. Company account
print("=" * 70, flush=True)
print("1. 기업회원 계정: neod00@naver.com", flush=True)
print("=" * 70, flush=True)

cursor.execute('SELECT id, name, email, role FROM "user" WHERE email = %s', ('neod00@naver.com',))
user = cursor.fetchone()

if user:
    user_id, name, email, role = user
    print(f"User Found: ID={user_id}, Name={name}, Email={email}, Role={role}", flush=True)
    
    cursor.execute("SELECT id, title, status, consultant_id FROM project WHERE company_id = %s ORDER BY created_at DESC", (user_id,))
    projects = cursor.fetchall()
    print(f"\nProjects for this company: {len(projects)}건", flush=True)
    for p in projects:
        title = p[1][:40] if p[1] else 'N/A'
        print(f"  ID:{p[0]} | {title} | Status:{p[2]} | Consultant:{p[3]}", flush=True)
else:
    print("User NOT FOUND!", flush=True)

sys.stdout.flush()

# 2. Consultant account
print("\n" + "=" * 70, flush=True)
print("2. 컨설턴트 계정: consultant@consultant.com", flush=True)
print("=" * 70, flush=True)

cursor.execute('SELECT id, name, email, role FROM "user" WHERE email = %s', ('consultant@consultant.com',))
user = cursor.fetchone()

if user:
    user_id, name, email, role = user
    print(f"User Found: ID={user_id}, Name={name}, Email={email}, Role={role}", flush=True)
    
    cursor.execute("SELECT id, name, user_id, status, verified, profile_image_url FROM consultant WHERE user_id = %s", (user_id,))
    consultant = cursor.fetchone()
    
    if consultant:
        c_id, c_name, c_user_id, c_status, c_verified, c_profile = consultant
        print(f"\nConsultant Profile:", flush=True)
        print(f"  ID: {c_id}", flush=True)
        print(f"  Name: {c_name}", flush=True)
        print(f"  Status: {c_status}", flush=True)
        print(f"  Verified: {c_verified}", flush=True)
        print(f"  ProfileImage: {c_profile}", flush=True)
        
        cursor.execute("SELECT id, title, status, company_id FROM project WHERE consultant_id = %s ORDER BY created_at DESC", (c_id,))
        projects = cursor.fetchall()
        print(f"\nProjects assigned: {len(projects)}건", flush=True)
        for p in projects:
            title = p[1][:40] if p[1] else 'N/A'
            print(f"  ID:{p[0]} | {title} | Status:{p[2]} | Company:{p[3]}", flush=True)
    else:
        print("\nNo Consultant profile linked!", flush=True)
else:
    print("User NOT FOUND!", flush=True)

conn.close()
print("\nDone.", flush=True)
