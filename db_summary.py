import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

db_url = os.environ.get('DATABASE_URL')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(db_url)
cursor = conn.cursor()

# Projects Summary
cursor.execute("SELECT status, COUNT(*) FROM project GROUP BY status")
print("PROJECTS BY STATUS:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]}")

cursor.execute("SELECT COUNT(*) FROM project")
print(f"\nTOTAL PROJECTS: {cursor.fetchone()[0]}")

# Consultants Summary
cursor.execute("SELECT status, COUNT(*) FROM consultant GROUP BY status")
print("\nCONSULTANTS BY STATUS:")
for row in cursor.fetchall():
    print(f"  {row[0] or 'NULL'}: {row[1]}")

cursor.execute("SELECT COUNT(*) FROM consultant")
print(f"\nTOTAL CONSULTANTS: {cursor.fetchone()[0]}")

# Users Summary  
cursor.execute('SELECT role, COUNT(*) FROM "user" GROUP BY role')
print("\nUSERS BY ROLE:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1]}")

conn.close()
