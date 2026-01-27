
import os
import psycopg2
from dotenv import load_dotenv
import sys

load_dotenv()

def diagnose_db():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("DATABASE_URL not found")
        return

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        # 1. PROJECT TABLE
        print("=" * 60)
        print("1. PROJECT TABLE ANALYSIS")
        print("=" * 60)
        sys.stdout.flush()
        
        cursor.execute("SELECT status, COUNT(*) FROM project GROUP BY status ORDER BY COUNT(*) DESC")
        rows = cursor.fetchall()
        print("\nProjects by Status:")
        for row in rows:
            print(f"  {row[0]}: {row[1]}")
        sys.stdout.flush()
        
        cursor.execute("SELECT company_id, COUNT(*) FROM project GROUP BY company_id ORDER BY COUNT(*) DESC LIMIT 10")
        rows = cursor.fetchall()
        print("\nProjects by Company (top 10):")
        for row in rows:
            print(f"  Company ID {row[0]}: {row[1]} projects")
        sys.stdout.flush()
        
        cursor.execute("SELECT id, title, status, company_id, consultant_id FROM project ORDER BY created_at DESC LIMIT 5")
        rows = cursor.fetchall()
        print("\nMost Recent Projects:")
        for row in rows:
            title = (row[1][:30] + '...') if row[1] and len(row[1]) > 30 else (row[1] or 'N/A')
            print(f"  ID:{row[0]} | {title} | Status:{row[2]} | Company:{row[3]} | Consultant:{row[4]}")
        sys.stdout.flush()

    except Exception as e:
        print(f"Error in section: {e}")
        import traceback
        traceback.print_exc()
    
    # 2. CONSULTANT TABLE  
    try:
        print("\n" + "=" * 60)
        print("2. CONSULTANT TABLE ANALYSIS")
        print("=" * 60)
        sys.stdout.flush()
        
        cursor.execute("SELECT status, COUNT(*) FROM consultant GROUP BY status")
        rows = cursor.fetchall()
        print("\nConsultants by Status:")
        for row in rows:
            print(f"  {row[0] or 'NULL'}: {row[1]}")
        sys.stdout.flush()
        
        cursor.execute("SELECT id, name, user_id, status FROM consultant ORDER BY id")
        rows = cursor.fetchall()
        print("\nAll Consultants:")
        for row in rows:
            print(f"  ID:{row[0]} | {row[1]} | UserID:{row[2]} | Status:{row[3]}")
        sys.stdout.flush()
        
    except Exception as e:
        print(f"Error in section: {e}")

    # 3. USER TABLE
    try:
        print("\n" + "=" * 60)
        print("3. USER TABLE ANALYSIS")
        print("=" * 60)
        sys.stdout.flush()
        
        cursor.execute('SELECT id, name, email, role FROM "user" ORDER BY id')
        rows = cursor.fetchall()
        print("\nAll Users:")
        for row in rows:
            print(f"  ID:{row[0]} | {row[1]} | {row[2]} | Role:{row[3]}")
        sys.stdout.flush()
        
    except Exception as e:
        print(f"Error in section: {e}")

    # 4. ANALYSIS_JOB TABLE
    try:
        print("\n" + "=" * 60)
        print("4. ANALYSIS_JOB TABLE (Quote Requests)")
        print("=" * 60)
        sys.stdout.flush()
        
        cursor.execute("SELECT id, company_name, status FROM analysis_job ORDER BY created_at DESC LIMIT 10")
        rows = cursor.fetchall()
        print("\nRecent Analysis Jobs:")
        for row in rows:
            job_id = row[0][:8] + '...' if len(row[0]) > 8 else row[0]
            print(f"  {job_id} | {row[1]} | Status:{row[2]}")
        sys.stdout.flush()
        
    except Exception as e:
        print(f"Error in section: {e}")
    
    if 'conn' in locals():
        conn.close()
    
    print("\n" + "=" * 60)
    print("DIAGNOSIS COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    diagnose_db()
