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

print("=" * 70)
print("1. 기업회원 계정: neod00@naver.com")
print("=" * 70)

cursor.execute('SELECT id, name, email, role FROM "user" WHERE email = %s', ('neod00@naver.com',))
user = cursor.fetchone()

if user:
    user_id, name, email, role = user
    print(f"User Found: ID={user_id}, Name={name}, Email={email}, Role={role}")
    
    # Get projects for this company
    cursor.execute("""
        SELECT id, title, status, consultant_id, created_at 
        FROM project 
        WHERE company_id = %s
        ORDER BY created_at DESC
    """, (user_id,))
    projects = cursor.fetchall()
    print(f"\nProjects (company_id={user_id}): {len(projects)}건")
    for p in projects:
        print(f"  ID:{p[0]} | {p[1][:50] if p[1] else 'N/A'} | Status:{p[2]} | Consultant:{p[3]}")
else:
    print("User NOT FOUND with email neod00@naver.com")

print("\n" + "=" * 70)
print("2. 컨설턴트 계정: consultant@consultant.com")
print("=" * 70)

cursor.execute('SELECT id, name, email, role FROM "user" WHERE email = %s', ('consultant@consultant.com',))
user = cursor.fetchone()

if user:
    user_id, name, email, role = user
    print(f"User Found: ID={user_id}, Name={name}, Email={email}, Role={role}")
    
    # Get consultant profile
    cursor.execute("""
        SELECT id, name, user_id, status, verified, iso_experience, industry_experience, profile_image_url
        FROM consultant 
        WHERE user_id = %s
    """, (user_id,))
    consultant = cursor.fetchone()
    
    if consultant:
        c_id, c_name, c_user_id, c_status, c_verified, c_iso, c_industry, c_profile_img = consultant
        print(f"\nConsultant Profile Found:")
        print(f"  ID: {c_id}")
        print(f"  Name: {c_name}")
        print(f"  Status: {c_status}")
        print(f"  Verified: {c_verified}")
        print(f"  ProfileImage: {c_profile_img}")
        print(f"  ISO Experience: {c_iso[:100] if c_iso else 'None'}...")
        print(f"  Industry Experience: {c_industry[:100] if c_industry else 'None'}...")
        
        # Get projects assigned to this consultant
        cursor.execute("""
            SELECT id, title, status, company_id, created_at 
            FROM project 
            WHERE consultant_id = %s
            ORDER BY created_at DESC
        """, (c_id,))
        projects = cursor.fetchall()
        print(f"\nProjects assigned to this consultant: {len(projects)}건")
        for p in projects:
            print(f"  ID:{p[0]} | {p[1][:50] if p[1] else 'N/A'} | Status:{p[2]} | Company:{p[3]}")
    else:
        print("\nNo Consultant profile linked to this user!")
else:
    print("User NOT FOUND with email consultant@consultant.com")

conn.close()
