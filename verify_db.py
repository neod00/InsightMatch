import sqlite3
import os

db_path = 'insightmatch.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT title, created_at FROM post ORDER BY created_at DESC LIMIT 1;")
    row = cur.fetchone()
    if row:
        print(f"Title: {row[0]}")
        print(f"Created At: {row[1]}")
    else:
        print("No posts found.")
    conn.close()
else:
    print("Database file not found.")
