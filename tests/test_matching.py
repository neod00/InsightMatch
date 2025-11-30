import unittest
import json
import sys
import os

# Add server directory to path so 'import models' works in app.py
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../server')))

from server.app import app, db
from server.models import Consultant, AnalysisJob
from server.services.matching_service import MatchingService
import importlib
import server.models
importlib.reload(server.models)
from server.models import Consultant

class TestMatchingService(unittest.TestCase):
    def setUp(self):
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['TESTING'] = True
        self.app = app.test_client()
        self.matching_service = MatchingService()
        
        # Debug: Print Consultant file and columns
        import inspect
        print(f"DEBUG: Consultant file: {inspect.getfile(Consultant)}")
        print(f"DEBUG: Consultant class columns: {Consultant.__table__.columns.keys()}")

        with app.app_context():
            db.create_all()
            
            # Debug: Print columns
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            columns = [c['name'] for c in inspector.get_columns('consultant')]
            print(f"DEBUG: Consultant columns: {columns}")
            
            # Create test consultants
            c1 = Consultant(
                name="Expert A",
                specialty="Manufacturing",
                match_reason="ISO 9001",
                iso_experience=json.dumps(["9001", "14001"]),
                industry_experience=json.dumps(["Manufacturing", "Automotive"]),
                project_types=json.dumps(["New", "Transition"]),
                verified=True,
                trust_score=80.0,
                rating=4.8,
                reviews=50
            )
            
            c2 = Consultant(
                name="Expert B",
                specialty="IT",
                match_reason="ISO 27001",
                iso_experience=json.dumps(["27001"]),
                industry_experience=json.dumps(["IT/Software"]),
                project_types=json.dumps(["New"]),
                verified=False,
                trust_score=40.0,
                rating=4.0,
                reviews=5
            )
            
            db.session.add(c1)
            db.session.add(c2)
            db.session.commit()

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_perfect_match(self):
        with app.app_context():
            criteria = {
                'industry': 'Manufacturing',
                'recommended_iso': [{'code': '9001'}],
                'project_type': 'New'
            }
            results = self.matching_service.match_consultants(criteria)
            
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]['name'], "Expert A")
            # Expert A score breakdown:
            # ISO 9001 match: 30
            # Industry Manufacturing match: 25
            # Project Type New match: 15
            # Trust Score 80 * 0.1 = 8 + Verified 10 = 18 (capped at 20) -> 18
            # Role/Size (Reviews > 10, Rating > 4.5): 5 + 5 = 10
            # Total: 30 + 25 + 15 + 18 + 10 = 98
            self.assertTrue(results[0]['matchScore'] > 90)

    def test_partial_match(self):
        with app.app_context():
            criteria = {
                'industry': 'IT/Software',
                'recommended_iso': [{'code': '27001'}]
            }
            results = self.matching_service.match_consultants(criteria)
            
            self.assertEqual(results[0]['name'], "Expert B")
            # Expert B score:
            # ISO 27001: 30
            # Industry IT: 25
            # Trust: 4 + 0 = 4
            # Role: 0
            # Total: 59
            self.assertTrue(results[0]['matchScore'] > 50)

if __name__ == '__main__':
    unittest.main()
