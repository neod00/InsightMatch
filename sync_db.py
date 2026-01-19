
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def sync_supabase_schema():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url or "[YOUR-PASSWORD]" in db_url:
        print("DATABASE_URL is not configured with a real password in .env")
        return

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        # Check Project table columns
        print("Checking 'project' table columns...")
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'project'")
        columns = [row[0] for row in cursor.fetchall()]
        print(f"Current columns: {columns}")
        
        if 'description' not in columns:
            print("Adding 'description' column to 'project' table...")
            cursor.execute("ALTER TABLE project ADD COLUMN description TEXT")
        
        if 'session_id' not in columns:
            print("Adding 'session_id' column to 'project' table...")
            cursor.execute("ALTER TABLE project ADD COLUMN session_id VARCHAR(36)")

        if 'cancelled_at' not in columns:
            print("Adding 'cancelled_at' column to 'project' table...")
            cursor.execute("ALTER TABLE project ADD COLUMN cancelled_at TIMESTAMP")

        if 'cancelled_reason' not in columns:
            print("Adding 'cancelled_reason' column to 'project' table...")
            cursor.execute("ALTER TABLE project ADD COLUMN cancelled_reason VARCHAR(500)")
            
        # Check AnalysisJob table columns
        print("\nChecking 'analysis_job' table columns...")
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'analysis_job'")
        columns = [row[0] for row in cursor.fetchall()]
        print(f"Current columns: {columns}")
        
        if 'deleted_at' not in columns:
            print("Adding 'deleted_at' column to 'analysis_job' table...")
            cursor.execute("ALTER TABLE analysis_job ADD COLUMN deleted_at TIMESTAMP")
            
        conn.commit()
        print("Schema sync completed successfully.")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    sync_supabase_schema()
