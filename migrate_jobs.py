import sqlite3
import uuid
import json
from datetime import datetime

def migrate_missing_analysis_jobs():
    conn = sqlite3.connect('insightmatch.db')
    cursor = conn.cursor()
    
    # 1. Get all projects that don't have a record in analysis_job for their session_id
    # Wait, analysis_job table might be empty, so we just check session_ids in project.
    cursor.execute('''
        SELECT DISTINCT session_id, company_id, title, created_at 
        FROM project 
        WHERE session_id IS NOT NULL
    ''')
    projects = cursor.fetchall()
    
    print(f"Found {len(projects)} unique sessions to migrate.")
    
    for session_id, company_id, title, created_at in projects:
        # Check if already exists in analysis_job
        cursor.execute("SELECT id FROM analysis_job WHERE id = ?", (session_id,))
        if cursor.fetchone():
            print(f"Session {session_id} already exists in analysis_job. Skipping.")
            continue
            
        # Get company info
        cursor.execute("SELECT name, email FROM user WHERE id = ?", (company_id,))
        user_info = cursor.fetchone()
        company_name = user_info[0] if user_info else "알 수 없는 업체"
        contact_email = user_info[1] if user_info else ""
        
        # Build minimal intake data
        standards = []
        if '인증' in title:
            # Try to extract "ISO 9001" from "ISO 9001:2015 인증 프로젝트"
            standards = [title.split(' 인증')[0]]
            
        intake_data = {
            'companyName': company_name,
            'contactEmail': contact_email,
            'standards': standards,
            'industry': '마이그레이션됨',
            'migrated': True
        }
        
        # Insert into analysis_job
        cursor.execute('''
            INSERT INTO analysis_job (id, company_name, status, intake_data, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (session_id, company_name, 'completed', json.dumps(intake_data), created_at))
        print(f"Created analysis_job for session {session_id} ({company_name})")
        
    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate_missing_analysis_jobs()
