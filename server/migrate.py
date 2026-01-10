"""
Database Migration Script
새로운 컬럼을 Project 테이블에 추가

사용법:
python migrate.py
"""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# Get database URL
database_url = os.environ.get('DATABASE_URL')
if not database_url:
    print("Error: DATABASE_URL not set")
    sys.exit(1)

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

print(f"Connecting to database...")

engine = create_engine(database_url)

# Migration SQL statements
migrations = [
    # Project 테이블에 새 컬럼 추가
    """
    ALTER TABLE project 
    ADD COLUMN IF NOT EXISTS proposal_status VARCHAR(50) DEFAULT 'pending';
    """,
    """
    ALTER TABLE project 
    ADD COLUMN IF NOT EXISTS proposal_data TEXT;
    """,
    """
    ALTER TABLE project 
    ADD COLUMN IF NOT EXISTS proposal_submitted_at TIMESTAMP;
    """,
    """
    ALTER TABLE project 
    ADD COLUMN IF NOT EXISTS request_data TEXT;
    """,
]

print("Running migrations...")

with engine.connect() as conn:
    for i, sql in enumerate(migrations):
        try:
            conn.execute(text(sql))
            conn.commit()
            print(f"  ✓ Migration {i+1} completed")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                print(f"  ⓘ Migration {i+1} skipped (column already exists)")
            else:
                print(f"  ✗ Migration {i+1} failed: {e}")

print("\n✅ Database migration completed!")
