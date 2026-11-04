"""2026-11-04 E2E 2차 재점검(T12 6절) 회귀 테스트.

R2-01: 로그인하고 돌아온 화면이 session_id 를 비워 보내도, 함께 온 매칭 결과의 session_id 로
       같은 매칭 세션에 묶여 프로젝트 상세에 설문(업종·직원 수·지역·규격)이 보여야 한다.
(R2-02 그룹 대표 상태, R2-03 라디오 표시, R2-04 관리자 정산 정보는 화면 코드라 브라우저로 확인)
"""
import datetime
import os
import sys
import unittest

import jwt
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

from index import app, db  # noqa: E402
from models import Consultant, Project, User  # noqa: E402


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256',
    )
    return {'Authorization': f'Bearer {token}'}


MATCH_FORM = {
    'companyName': '세션제조', 'contactEmail': 'session@example.com',
    'industry': 'Manufacturing', 'employees': '51-200', 'region': '경기',
    'standards': ['ISO 9001:2026', 'ISO 14001:2026'],
    'issues': ['quality_defect'], 'reasons': ['customer_demand'],
    'certStatus': 'None', 'timeline': '3months', 'budget': 'unknown', 'additionalNotes': '',
}


class Round2FixesTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        self.company = User(email='buyer2@example.com', password_hash=generate_password_hash('Passw0rd!'),
                            role='company', name='담당', company_name='세션제조',
                            biz_reg_no='1234567891', biz_status='verified')
        self.other = User(email='other2@example.com', password_hash='x', role='company', name='타사',
                          company_name='타사', biz_reg_no='2208812340', biz_status='verified')
        self.cu = User(email='expert2@example.com', password_hash='x', role='consultant', name='전문가')
        db.session.add_all([self.company, self.other, self.cu])
        db.session.flush()
        self.consultant = Consultant(user_id=self.cu.id, name='전문가', verified=True, status='verified',
                                     iso_experience='{"9001": true, "14001": true}',
                                     industry_experience='["Manufacturing"]', regions='경기')
        db.session.add(self.consultant)
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _match(self):
        res = self.client.post('/api/match', json=MATCH_FORM)
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body.get('session_id'))
        return body

    def _request(self, user, match_result, session_id):
        return self.client.post('/api/quotes/request', headers=auth_headers(user), json={
            'consultant_ids': [self.consultant.id],
            'analysis_context': match_result,
            'session_id': session_id,
        })

    def test_null_session_id_falls_back_to_match_result_session(self):
        match_result = self._match()
        res = self._request(self.company, match_result, None)
        self.assertEqual(res.status_code, 201, res.get_json())
        project = Project.query.filter_by(company_id=self.company.id).one()
        self.assertEqual(project.session_id, match_result['session_id'])

        detail = self.client.get(f'/api/projects/{project.id}/detail', headers=auth_headers(self.cu)).get_json()
        self.assertEqual(detail['industry'], 'Manufacturing')
        self.assertEqual(detail['employees'], '51-200')
        self.assertEqual(detail['region'], '경기')
        self.assertIn('ISO 9001:2026', detail['standards'])

    def test_fallback_still_refuses_another_companys_session(self):
        match_result = self._match()
        # 다른 기업이 먼저 이 세션으로 요청했다
        self.assertEqual(self._request(self.other, match_result, match_result['session_id']).status_code, 201)
        # 같은 매칭 결과를 들고 session_id 를 비워 보내도 남의 세션에 붙지 않는다
        self.assertEqual(self._request(self.company, match_result, None).status_code, 201)
        mine = Project.query.filter_by(company_id=self.company.id).one()
        self.assertNotEqual(mine.session_id, match_result['session_id'])

    def test_explicit_session_id_wins_over_context(self):
        first = self._match()
        second = self._match()
        self.assertEqual(self._request(self.company, first, second['session_id']).status_code, 201)
        project = Project.query.filter_by(company_id=self.company.id).one()
        self.assertEqual(project.session_id, second['session_id'])


if __name__ == '__main__':
    unittest.main()
