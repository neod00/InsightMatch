"""
Migration script to add company_name column to user table in Supabase
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def migrate_supabase():
    """Add company_name column to user table in Supabase"""
    
    # Get database URL from environment
    database_url = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
    
    if not database_url:
        print("❌ DATABASE_URL not found in environment variables")
        return False
    
    try:
        # Connect to Supabase PostgreSQL
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        
        print("✅ Connected to Supabase database")
        
        # Check if column already exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'user' AND column_name = 'company_name'
        """)
        
        if cursor.fetchone():
            print("ℹ️ company_name column already exists")
        else:
            # Add company_name column
            cursor.execute("""
                ALTER TABLE "user" 
                ADD COLUMN company_name VARCHAR(100)
            """)
            conn.commit()
            print("✅ Added company_name column to user table")
        
        cursor.close()
        conn.close()
        print("✅ Migration completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {str(e)}")
        return False

if __name__ == "__main__":
    migrate_supabase()
