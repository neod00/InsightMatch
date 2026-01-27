import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get('DATABASE_URL')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

# Get specific user by email
email_to_check = "nkneod@naver.com"

cursor.execute('SELECT id, name, email, role FROM "user" WHERE email = %s', (email_to_check,))
user = cursor.fetchone()

if user:
    user_id, name, email, role = user
    print(f"User Found: {name} (ID: {user_id}, Role: {role})")
    
    if role == 'company':
        cursor.execute("SELECT id, title, status, consultant_id FROM project WHERE company_id = %s ORDER BY created_at DESC", (user_id,))
        projects = cursor.fetchall()
        print(f"\nProjects for this company ({len(projects)} total):")
        for p in projects:
            print(f"  ID:{p[0]} | {p[1][:50] if p[1] else 'N/A'} | Status:{p[2]} | Consultant:{p[3]}")
    
    elif role == 'consultant':
        cursor.execute("SELECT id, name, user_id, status, verified FROM consultant WHERE user_id = %s", (user_id,))
        consultant = cursor.fetchone()
        if consultant:
            print(f"\nConsultant Profile: ID:{consultant[0]}, Name:{consultant[1]}, Status:{consultant[3]}, Verified:{consultant[4]}")
            
            cursor.execute("SELECT id, title, status, company_id FROM project WHERE consultant_id = %s ORDER BY created_at DESC", (consultant[0],))
            projects = cursor.fetchall()
            print(f"\nProjects assigned to this consultant ({len(projects)} total):")
            for p in projects:
                print(f"  ID:{p[0]} | {p[1][:50] if p[1] else 'N/A'} | Status:{p[2]} | Company:{p[3]}")
        else:
            print("No consultant profile found for this user")
else:
    print(f"User with email {email_to_check} not found")

conn.close()
