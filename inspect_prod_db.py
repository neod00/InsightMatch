
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def inspect():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url or "localhost" in db_url:
        print("DATABASE_URL is not set to a production database URL.")
        return

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        tables = ['consultant']
        
        for table in tables:
            print(f"\n--- Columns in '{table}' ---")
            cursor.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}'")
            cols = cursor.fetchall()
            if not cols:
                print("Table does not exist!")
            for col, dtype in cols:
                print(f"{col}: {dtype}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    inspect()
