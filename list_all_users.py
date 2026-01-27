import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get('DATABASE_URL')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

# List all users
print("ALL USERS IN DATABASE:")
print("=" * 80)
cursor.execute('SELECT id, name, email, role FROM "user" ORDER BY id')
for row in cursor.fetchall():
    print(f"  ID:{row[0]} | {row[1]:20s} | {row[2]:40s} | {row[3]}")

print("\n" + "=" * 80)
print("\nALL CONSULTANTS IN DATABASE:")
print("=" * 80)
cursor.execute('SELECT id, name, user_id, status, verified FROM consultant ORDER BY id')
for row in cursor.fetchall():
    print(f"  ID:{row[0]} | {row[1]:20s} | UserID:{row[2]} | Status:{row[3]} | Verified:{row[4]}")

conn.close()
