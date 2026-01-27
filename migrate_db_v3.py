import os
import sqlite3
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def migrate_sqlite():
    db_path = os.path.join(os.getcwd(), 'insightmatch.db')
    if not os.path.exists(db_path):
        print(f"SQLite DB not found at {db_path}")
        return

    print(f"Migrating SQLite DB: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 1. Add phone column to user table if it doesn't exist
        cursor.execute("PRAGMA table_info(user)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'phone' not in columns:
            print("Adding 'phone' column to 'user' table...")
            cursor.execute("ALTER TABLE user ADD COLUMN phone VARCHAR(20)")
        else:
            print("'phone' column already exists in 'user' table.")

        # 2. Create password_reset_token table if it doesn't exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='password_reset_token'")
        if not cursor.fetchone():
            print("Creating 'password_reset_token' table...")
            cursor.execute("""
                CREATE TABLE password_reset_token (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token VARCHAR(100) UNIQUE NOT NULL,
                    expires_at DATETIME NOT NULL,
                    used BOOLEAN DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES user (id)
                )
            """)
        else:
            print("'password_reset_token' table already exists.")

        conn.commit()
        print("SQLite migration successful.")
    except Exception as e:
        print(f"SQLite migration error: {e}")
    finally:
        conn.close()

def migrate_supabase():
    db_url = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
    if not db_url:
        print("Supabase connection string not found in .env")
        return

    print("Migrating Supabase DB...")
    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        # 1. Add phone column to user table if it doesn't exist
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='user' AND column_name='phone'
        """)
        if not cursor.fetchone():
            print("Adding 'phone' column to 'user' table in Supabase...")
            cursor.execute('ALTER TABLE "user" ADD COLUMN phone VARCHAR(20)')
        else:
            print("'phone' column already exists in Supabase 'user' table.")

        # 2. Create password_reset_token table if it doesn't exist
        cursor.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE tablename='password_reset_token'")
        if not cursor.fetchone():
            print("Creating 'password_reset_token' table in Supabase...")
            cursor.execute("""
                CREATE TABLE password_reset_token (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES "user"(id),
                    token VARCHAR(100) UNIQUE NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            print("'password_reset_token' table already exists in Supabase.")

        conn.commit()
        print("Supabase migration successful.")
    except Exception as e:
        print(f"Supabase migration error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    migrate_sqlite()
    migrate_supabase()
