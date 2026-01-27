import sqlite3

def check_projects():
    conn = sqlite3.connect('insightmatch.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, title, status, created_at, company_id, consultant_id, session_id FROM project')
    rows = cursor.fetchall()
    print(f"Total projects: {len(rows)}")
    for r in rows:
        print(r)
    
    cursor.execute('SELECT id, email, role FROM user')
    users = cursor.fetchall()
    print("\nUsers:")
    for u in users:
        print(u)
    
    conn.close()

if __name__ == "__main__":
    check_projects()
