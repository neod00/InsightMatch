
import os
import psycopg2
import json
from dotenv import load_dotenv

load_dotenv()

def check_consultants():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url or "localhost" in db_url:
        print("DATABASE_URL is not set properly.")
        return

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, name, iso_experience, industry_experience, project_types, org_size_experience, roles FROM consultant")
        rows = cursor.fetchall()
        
        print(f"Total consultants: {len(rows)}")
        
        for row in rows:
            cid, name, iso, ind, proj, size, roles = row
            print(f"\nChecking Consultant {cid}: {name}")
            
            fields = {
                'iso_experience': iso,
                'industry_experience': ind,
                'project_types': proj,
                'org_size_experience': size,
                'roles': roles
            }
            
            for field_name, value in fields.items():
                if value:
                    try:
                        json.loads(value)
                        # print(f"  {field_name}: OK")
                    except Exception as e:
                        print(f"  {field_name}: FAIL - Value: {value} - Error: {e}")
                else:
                    print(f"  {field_name}: Empty")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    check_consultants()
