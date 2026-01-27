import sqlite3
import os

# Standardized path to project root
base_dir = os.path.dirname(os.path.abspath(__file__)) # This script is in root
db_path = os.path.join(base_dir, 'insightmatch.db')
print(f"Targeting database at: {db_path}")

if not os.path.exists(db_path):

    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

def add_column_if_not_exists(table, column, definition):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        print(f"Added column {column} to table {table}")
    except sqlite3.OperationalError as e:
        if 'duplicate column name' in str(e).lower():
            print(f"Column {column} already exists in table {table}")
        else:
            print(f"Error adding column {column}: {e}")

# Consultant table columns
consultant_cols = [
    ('pending_changes_at', 'DATETIME'),
    ('profile_image_url', 'VARCHAR(500)'),
    ('introduction_video_url', 'VARCHAR(500)'),
    ('bio', 'TEXT'),
    ('phone', 'VARCHAR(50)'),
    ('email', 'VARCHAR(120)'),
    ('company_name', 'VARCHAR(200)'),
    ('portfolio_files', 'TEXT')
]

for col_name, col_def in consultant_cols:
    add_column_if_not_exists('consultant', col_name, col_def)

# Project table columns (Verification after previous run)
project_cols = [
    ('description', 'TEXT'),
    ('session_id', 'VARCHAR(36)'),
    ('proposal_price', 'INTEGER'),
    ('proposal_duration', 'VARCHAR(50)'),
    ('proposal_message', 'TEXT'),
    ('proposal_file_url', 'VARCHAR(500)'),
    ('proposal_submitted_at', 'DATETIME'),
    ('schedule_data', 'TEXT'),
    ('schedule_status', 'VARCHAR(50) DEFAULT "pending"'),
    ('schedule_proposed_at', 'DATETIME'),
    ('schedule_confirmed_at', 'DATETIME'),
    ('cancelled_at', 'DATETIME'),
    ('cancelled_reason', 'VARCHAR(500)')
]

for col_name, col_def in project_cols:
    add_column_if_not_exists('project', col_name, col_def)

conn.commit()
conn.close()
print("Migration completed.")
