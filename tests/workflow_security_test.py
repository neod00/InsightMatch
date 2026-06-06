import datetime
import os
import sys
import unittest

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

from index import app, db
from models import Consultant, Message, Notification, Project, User


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
        self.admin_user = User(
            email='admin@example.com',
            password_hash='x',
            role='admin',
            name='Admin User',
        )
        db.session.add_all([self.company, self.other_company, self.consultant_user, self.admin_user])
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

    def test_admin_endpoints_require_admin_role(self):
        response = self.client.get('/api/admin/jobs')
        self.assertEqual(response.status_code, 401)

        response = self.client.get('/api/admin/jobs', headers=auth_headers(self.company))
        self.assertEqual(response.status_code, 403)

        response = self.client.get('/api/admin/jobs', headers=auth_headers(self.admin_user))
        self.assertEqual(response.status_code, 200)

    def test_only_project_company_can_delete_or_cancel_request(self):
        response = self.client.delete(
            f'/api/projects/{self.project.id}',
            headers=auth_headers(self.other_company),
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            f'/api/projects/{self.project.id}/cancel',
            json={'reason': 'not mine'},
            headers=auth_headers(self.other_company),
        )
        self.assertEqual(response.status_code, 403)

    def test_consultant_profile_update_requires_owner(self):
        response = self.client.put(
            f'/api/consultants/{self.consultant.id}/profile',
            json={'bio': 'tampered', 'user_id': self.other_company.id},
            headers=auth_headers(self.other_company),
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.put(
            f'/api/consultants/{self.consultant.id}/profile',
            json={'bio': 'updated by owner', 'user_id': self.other_company.id},
            headers=auth_headers(self.consultant_user),
        )
        self.assertEqual(response.status_code, 200)

        db.session.refresh(self.consultant)
        self.assertEqual(self.consultant.bio, 'updated by owner')

    def test_messages_require_project_participant_and_use_authenticated_sender(self):
        response = self.client.get(
            f'/api/projects/{self.project.id}/messages',
            headers=auth_headers(self.other_company),
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            f'/api/projects/{self.project.id}/messages',
            json={'sender_id': self.other_company.id, 'content': 'hello'},
            headers=auth_headers(self.consultant_user),
        )
        self.assertEqual(response.status_code, 200)

        message = Message.query.filter_by(project_id=self.project.id).first()
        self.assertEqual(message.sender_id, self.consultant_user.id)

    def test_contact_info_requires_project_participant_after_contract(self):
        self.project.status = 'contracted'
        db.session.commit()

        response = self.client.get(
            f'/api/projects/{self.project.id}/contact-info',
            headers=auth_headers(self.other_company),
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.get(
            f'/api/projects/{self.project.id}/contact-info',
            headers=auth_headers(self.company),
        )
        self.assertEqual(response.status_code, 200)

    def test_notification_read_requires_owner(self):
        notification = Notification(
            user_id=self.company.id,
            type='proposal_received',
            title='Proposal received',
        )
        db.session.add(notification)
        db.session.commit()

        response = self.client.post(
            f'/api/notifications/{notification.id}/read',
            headers=auth_headers(self.other_company),
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            f'/api/notifications/{notification.id}/read',
            headers=auth_headers(self.company),
        )
        self.assertEqual(response.status_code, 200)

    def test_public_consultant_list_does_not_expose_contact_fields(self):
        self.consultant.email = 'private@example.com'
        self.consultant.phone = '010-1234-5678'
        db.session.commit()

        response = self.client.get('/api/consultants')
        self.assertEqual(response.status_code, 200)
        consultant = response.get_json()[0]
        self.assertNotIn('email', consultant)
        self.assertNotIn('phone', consultant)


if __name__ == '__main__':
    unittest.main()
