
import os
import psycopg2
import json
from dotenv import load_dotenv

load_dotenv()

def check_consultants():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url or "localhost" in db_url:
        return

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, name, iso_experience, industry_experience, project_types, org_size_experience, roles FROM consultant")
        rows = cursor.fetchall()
        
        for row in rows:
            cid, name, iso, ind, proj, size, roles = row
            
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
                        data = json.loads(value)
                        if field_name == 'iso_experience' and not isinstance(data, dict):
                            print(f"ISO_TYPE_ERROR: Consultant {cid} ({name}) - {field_name} is {type(data)}: {value}")
                        elif field_name in ['industry_experience', 'project_types', 'org_size_experience', 'roles'] and not isinstance(data, list):
                            print(f"LIST_TYPE_ERROR: Consultant {cid} ({name}) - {field_name} is {type(data)}: {value}")
                    except Exception as e:
                        print(f"PARSE_ERROR: Consultant {cid} ({name}) - {field_name}: {e}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    check_consultants()
