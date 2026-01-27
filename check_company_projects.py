import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get('DATABASE_URL')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

# Get company users
cursor.execute('SELECT id, name, email FROM "user" WHERE role = %s ORDER BY id', ('company',))
company_users = cursor.fetchall()

print("=" * 60)
print("COMPANY USERS AND THEIR PROJECTS")
print("=" * 60)

for user in company_users:
    user_id, name, email = user
    print(f"\nUser: {name} (ID: {user_id}, Email: {email})")
    
    # Get projects for this company
    cursor.execute("""
        SELECT id, title, status, consultant_id, created_at 
        FROM project 
        WHERE company_id = %s
        ORDER BY created_at DESC
    """, (user_id,))
    projects = cursor.fetchall()
    
    if projects:
        print(f"  Projects ({len(projects)}):")
        for p in projects:
            print(f"    - ID:{p[0]} | {p[1][:40] if p[1] else 'N/A'}... | Status:{p[2]} | Consultant:{p[3]}")
    else:
        print("  No projects found")
    
    # Get analysis jobs for this company  
    cursor.execute("""
        SELECT id, status 
        FROM analysis_job 
        WHERE company_name = %s
        ORDER BY created_at DESC
        LIMIT 5
    """, (name,))
    jobs = cursor.fetchall()
    
    if jobs:
        print(f"  Analysis Jobs ({len(jobs)}):")
        for j in jobs:
            print(f"    - {j[0][:8]}... | Status:{j[1]}")

conn.close()
print("\n" + "=" * 60)
