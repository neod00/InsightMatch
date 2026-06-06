import datetime
import os
import sys
import unittest

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

from index import app, db
from models import Consultant, Project, User


def make_token(user):
    payload = {
        'user_id': user.id,
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')


def auth_headers(user):
    return {'Authorization': f'Bearer {make_token(user)}'}


class TestWorkflowSecurity(unittest.TestCase):
    def setUp(self):
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        if hasattr(app, '_tables_created'):
            delattr(app, '_tables_created')

        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()

        self.company = User(
            email='company@example.com',
            password_hash='x',
            role='company',
            name='Company User',
            company_name='Buyer Co',
        )
        self.other_company = User(
            email='other@example.com',
            password_hash='x',
            role='company',
            name='Other User',
            company_name='Other Co',
        )
        self.consultant_user = User(
            email='consultant@example.com',
            password_hash='x',
            role='consultant',
            name='Consultant User',
        )
        db.session.add_all([self.company, self.other_company, self.consultant_user])
        db.session.flush()

        self.consultant = Consultant(
            user_id=self.consultant_user.id,
            name='ISO Expert',
            verified=True,
            status='verified',
            trust_score=80,
        )
        db.session.add(self.consultant)
        db.session.flush()

        self.project = Project(
            company_id=self.company.id,
            consultant_id=self.consultant.id,
            title='ISO 9001 Project',
            session_id='session-1',
            status='proposal_submitted',
            proposal_price=5000000,
            proposal_duration='3 months',
        )
        self.other_candidate = Project(
            company_id=self.company.id,
            consultant_id=self.consultant.id,
            title='ISO 9001 Project',
            session_id='session-1',
            status='proposal_submitted',
            proposal_price=6000000,
            proposal_duration='4 months',
        )
        db.session.add_all([self.project, self.other_candidate])
        db.session.commit()

        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_quote_request_requires_company_role(self):
        response = self.client.post(
            '/api/quotes/request',
            json={'consultant_ids': [self.consultant.id], 'analysis_context': {}},
            headers=auth_headers(self.consultant_user),
        )

        self.assertEqual(response.status_code, 403)

    def test_non_participant_cannot_read_project_proposal_or_contract(self):
        headers = auth_headers(self.other_company)

        for path in [
            f'/api/projects/{self.project.id}/proposal',
            f'/api/projects/{self.project.id}/detail',
            f'/api/projects/{self.project.id}/contract/preview',
        ]:
            response = self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 403, path)

        response = self.client.post(
            f'/api/projects/{self.project.id}/contract/draft',
            json={},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_proposal_json_and_download_routes_are_separate(self):
        response = self.client.get(
            f'/api/projects/{self.project.id}/proposal',
            headers=auth_headers(self.company),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['proposal_price'], 5000000)

        rules = {str(rule) for rule in app.url_map.iter_rules()}
        self.assertIn('/api/projects/<int:project_id>/proposal/download', rules)

    def test_contract_signer_is_derived_from_authenticated_user(self):
        self.client.post(
            f'/api/projects/{self.project.id}/contract/draft',
            json={},
            headers=auth_headers(self.company),
        )

        self.assertEqual(Project.query.get(self.other_candidate.id).status, 'proposal_submitted')

        response = self.client.post(
            f'/api/projects/{self.project.id}/contract/sign',
            json={'signer': 'consultant'},
            headers=auth_headers(self.company),
        )
        self.assertEqual(response.status_code, 200)

        db.session.refresh(self.project)
        db.session.refresh(self.other_candidate)
        self.assertIsNotNone(self.project.company_signed_at)
        self.assertIsNone(self.project.consultant_signed_at)
        self.assertEqual(self.project.status, 'awaiting_signature')
        self.assertEqual(self.other_candidate.status, 'proposal_submitted')

        response = self.client.post(
            f'/api/projects/{self.project.id}/contract/sign',
            json={'signer': 'company'},
            headers=auth_headers(self.consultant_user),
        )
        self.assertEqual(response.status_code, 200)

        db.session.refresh(self.project)
        db.session.refresh(self.other_candidate)
        self.assertIsNotNone(self.project.consultant_signed_at)
        self.assertEqual(self.project.status, 'contracted')
        self.assertEqual(self.other_candidate.status, 'not_selected')


if __name__ == '__main__':
    unittest.main()
