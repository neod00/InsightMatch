import os
import sys
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import sqlite3

# Add current directory to path for imports if needed
sys.path.insert(0, os.path.join(os.getcwd(), 'api'))

# Load environment variables
load_dotenv()

ID = "master"
PW = "master0837!"
ROLE = "admin"
NAME = "InsightMatch Admin"

def setup_local_admin():
    db_path = 'insightmatch.db'
    if not os.path.exists(db_path):
        print(f"Local database {db_path} not found. Make sure the server has run at least once.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    password_hash = generate_password_hash(PW)
    
    try:
        # Check if user exists
        cursor.execute("SELECT id FROM user WHERE email = ?", (ID,))
        user = cursor.fetchone()
        
        if user:
            cursor.execute("UPDATE user SET password_hash = ?, role = ? WHERE email = ?", (password_hash, ROLE, ID))
            print(f"Local: Updated existing admin '{ID}'")
        else:
            cursor.execute("INSERT INTO user (email, password_hash, role, name) VALUES (?, ?, ?, ?)", 
                           (ID, password_hash, ROLE, NAME))
            print(f"Local: Created new admin '{ID}'")
            
        conn.commit()
    except Exception as e:
        print(f"Error updating local database: {e}")
    finally:
        conn.close()

def setup_supabase_admin():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url or "[YOUR-PASSWORD]" in db_url:
        print("Supabase: DATABASE_URL is not configured with a real password in .env")
        print("Please update DATABASE_URL in .env with your Supabase password and run this script again.")
        return

    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        password_hash = generate_password_hash(PW)
        
        # Check if user exists
        cursor.execute("SELECT id FROM \"user\" WHERE email = %s", (ID,))
        user = cursor.fetchone()
        
        if user:
            cursor.execute("UPDATE \"user\" SET password_hash = %s, role = %s WHERE email = %s", (password_hash, ROLE, ID))
            print(f"Supabase: Updated existing admin '{ID}'")
        else:
            cursor.execute("INSERT INTO \"user\" (email, password_hash, role, name) VALUES (%s, %s, %s, %s)", 
                           (ID, password_hash, ROLE, NAME))
            print(f"Supabase: Created new admin '{ID}'")
            
        conn.commit()
        conn.close()
    except ImportError:
        print("Supabase: 'psycopg2' library not found. Cannot connect to Supabase from this script.")
        print("Please run: pip install psycopg2-binary")
    except Exception as e:
        print(f"Supabase: Error updating Supabase database: {e}")

if __name__ == "__main__":
    setup_local_admin()
    setup_supabase_admin()
