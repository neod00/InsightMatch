import sys
import os
import json

# Add server to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'server')))

from app import app, db
from models import Consultant
from services.matching_service import MatchingService

def run_test():
    print("Starting manual test...")
    
    # Debug: Print Consultant file and columns
    import inspect
    print(f"DEBUG: Consultant file: {inspect.getfile(Consultant)}")
    print(f"DEBUG: Consultant class columns: {Consultant.__table__.columns.keys()}")

    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['TESTING'] = True
    
    with app.app_context():
        db.create_all()
        
        # Verify columns in DB
        from sqlalchemy import inspect as sql_inspect
        inspector = sql_inspect(db.engine)
        columns = [c['name'] for c in inspector.get_columns('consultant')]
        print(f"DEBUG: DB Columns: {columns}")
        
        if 'iso_experience' not in columns:
            print("ERROR: iso_experience column missing in DB!")
            return

        # Create multiple test consultants
        consultants = [
            Consultant(
                name="Expert A (Perfect Match)",
                specialty="Manufacturing",
                match_reason="ISO 9001",
                iso_experience=json.dumps(["9001", "14001"]),
                industry_experience=json.dumps(["Manufacturing", "Automotive"]),
                project_types=json.dumps(["New", "Transition"]),
                verified=True,
                trust_score=90.0,
                rating=4.9,
                reviews=50
            ),
            Consultant(
                name="Expert B (Good Match, Unverified)",
                specialty="Manufacturing",
                match_reason="ISO 9001",
                iso_experience=json.dumps(["9001"]),
                industry_experience=json.dumps(["Manufacturing"]),
                project_types=json.dumps(["New"]),
                verified=False,
                trust_score=50.0,
                rating=4.5,
                reviews=20
            ),
            Consultant(
                name="Expert C (Industry Mismatch)",
                specialty="IT",
                match_reason="ISO 27001",
                iso_experience=json.dumps(["9001", "27001"]),
                industry_experience=json.dumps(["IT", "Finance"]),
                project_types=json.dumps(["New"]),
                verified=True,
                trust_score=85.0,
                rating=4.8,
                reviews=30
            ),
            Consultant(
                name="Expert D (ISO Mismatch)",
                specialty="Manufacturing",
                match_reason="ISO 45001",
                iso_experience=json.dumps(["45001"]),
                industry_experience=json.dumps(["Manufacturing"]),
                project_types=json.dumps(["New"]),
                verified=True,
                trust_score=80.0,
                rating=4.7,
                reviews=25
            )
        ]
        
        for c in consultants:
            db.session.add(c)
        db.session.commit()
        print(f"{len(consultants)} Consultants created successfully.")
        
        matching_service = MatchingService()

        # Scenario 1: Manufacturing, ISO 9001, New Project
        print("\n--- Scenario 1: Manufacturing, ISO 9001, New Project ---")
        criteria1 = {
            'industry': 'Manufacturing',
            'recommended_iso': [{'code': '9001'}],
            'project_type': 'New'
        }
        results1 = matching_service.match_consultants(criteria1)
        for i, res in enumerate(results1):
            print(f"{i+1}. {res['name']} - Score: {res['matchScore']} (Verified: {res['verified']}, Trust: {res['trustScore']})")

        # Scenario 2: IT, ISO 27001
        print("\n--- Scenario 2: IT, ISO 27001 ---")
        criteria2 = {
            'industry': 'IT',
            'recommended_iso': [{'code': '27001'}],
            'project_type': 'New'
        }
        results2 = matching_service.match_consultants(criteria2)
        for i, res in enumerate(results2):
            print(f"{i+1}. {res['name']} - Score: {res['matchScore']}")

if __name__ == '__main__':
    run_test()
