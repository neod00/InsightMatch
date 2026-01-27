
import sqlite3
import os
from werkzeug.security import generate_password_hash

def check_and_create_consultant():
    db_path = 'insightmatch.db'
    if not os.path.exists(db_path):
        print(f"Database {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check for existing consultants
        cursor.execute("SELECT email FROM user WHERE role = 'consultant'")
        consultants = cursor.fetchall()

        if consultants:
            print(f"Existing consultant found: {consultants[0][0]}. Resetting password to 'test1234'...")
            pw = 'test1234'
            password_hash = generate_password_hash(pw)
            cursor.execute("UPDATE user SET password_hash = ? WHERE email = ?", (password_hash, consultants[0][0]))
            conn.commit()
            print("Password reset successfully.")
        else:
            print("No consultants found. Creating 'consultant@test.com' with password 'test1234'...")
            email = 'consultant@test.com'
            pw = 'test1234'
            password_hash = generate_password_hash(pw)
            role = 'consultant'
            name = '테스트 전문가'

            # Create User
            cursor.execute("INSERT INTO user (email, password_hash, role, name) VALUES (?, ?, ?, ?)",
                           (email, password_hash, role, name))
            user_id = cursor.lastrowid

            # Create Consultant record
            cursor.execute("INSERT INTO consultant (user_id, name, specialty, experience, rating, reviews) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, name, 'ISO 컨설팅', '10년', 4.8, 25))
            
            conn.commit()
            print(f"Consultant created: {email} / {pw}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    check_and_create_consultant()
