"""AI 매뉴얼 생성기 T6 — 저장 텍스트 추출, 앞 단계 검증, resume(재시도), 중단 상태.

OpenAI 는 호출하지 않는다. generate_iso_manual_stream 을 mock 으로 바꿔 검증한다.
"""
import datetime
import os
import sys
import unittest
from unittest.mock import patch

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

# index 를 import 하기 전에 인메모리 DB 를 지정해야 한다 (tests/conftest.py 와 같은 이유 —
# 그렇지 않으면 tearDown 의 drop_all() 이 로컬 개발 DB 를 지운다).
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

from index import (  # noqa: E402
    app, db, _extract_stream_text, _is_full_manual_entitled,
    MAX_MANUAL_RETRIES, MANUAL_GENERATING_STALE_SECONDS,
)
from models import ManualGeneration, User  # noqa: E402


def auth_headers(user):
    token = jwt.encode(
        {'user_id': user.id, 'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)},
        app.config['SECRET_KEY'], algorithm='HS256')
    return {'Authorization': f'Bearer {token}'}


def utcnow_naive():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class ExtractStreamTextTest(unittest.TestCase):
    """FR-44: 공백 보존, '[' 로 시작하는 내용 보존, 제어 신호 정확 구분."""

    def test_leading_space_preserved(self):
        self.assertEqual(_extract_stream_text('data:  경영\n\n'), ' 경영')
        self.assertEqual(_extract_stream_text('data:  \n\n'), ' ')

    def test_bracket_content_preserved(self):
        self.assertEqual(_extract_stream_text('data: [링크](http://x)\n\n'), '[링크](http://x)')
        self.assertEqual(_extract_stream_text('data: [ ] 체크박스\n\n'), '[ ] 체크박스')
        self.assertEqual(_extract_stream_text('data: A["고객"]\n\n'), 'A["고객"]')
        self.assertEqual(_extract_stream_text('data: [PHASE_COMPLETE 설명]\n\n'), '[PHASE_COMPLETE 설명]')

    def test_control_signals_are_not_content(self):
        self.assertIsNone(_extract_stream_text('data: [DONE]\n\n'))
        self.assertIsNone(_extract_stream_text('data: [ERROR] OpenAI API 오류 (500)\n\n'))
        self.assertIsNone(_extract_stream_text('data: [PHASE_COMPLETE:2]\n\n'))

    def test_escaped_newline_restored(self):
        self.assertEqual(_extract_stream_text('data: ## 4.\\n\\n본문\n\n'), '## 4.\n\n본문')

    def test_non_data_chunk_ignored(self):
        self.assertIsNone(_extract_stream_text(': keep-alive\n\n'))


class ManualRetryTestBase(unittest.TestCase):
    def setUp(self):
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        if hasattr(app, '_tables_created'):
            delattr(app, '_tables_created')
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        self.company = User(email='c1@example.com', password_hash='x', role='company',
                            name='C1', company_name='Buyer Co')
        self.other = User(email='c2@example.com', password_hash='x', role='company',
                          name='C2', company_name='Other Co')
        self.consultant = User(email='k@example.com', password_hash='x', role='consultant', name='K')
        self.admin = User(email='a@example.com', password_hash='x', role='admin', name='A')
        db.session.add_all([self.company, self.other, self.consultant, self.admin])
        db.session.commit()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def payload(self):
        return {
            'company_name': 'Buyer Co', 'industry': '제조업', 'main_product': '자동차 부품',
            'employees': '50~100명', 'target_iso': 'ISO 9001:2015', 'issues': ['quality_defect'],
        }

    def new_session(self, user=None):
        resp = self.client.post('/api/iso-manual/session', json=self.payload(),
                                headers=auth_headers(user or self.company))
        self.assertEqual(resp.status_code, 201)
        return resp.get_json()

    def run_generate(self, session, stream_fn, continue_from=None):
        url = f"/api/generate-iso?manual_id={session['manual_id']}&stream_token={session['stream_token']}"
        if continue_from:
            url += f'&continue_from={continue_from}'
        with patch('index.generate_iso_manual_stream', stream_fn) as mocked:
            resp = self.client.get(url)
            body = resp.get_data(as_text=True)  # 끝까지 소비해야 상태 커밋이 실행된다
        return resp, body

    def resume(self, manual_id, user=None):
        return self.client.post(f'/api/iso-manual/{manual_id}/resume',
                                headers=auth_headers(user or self.company))

    @staticmethod
    def ok_stream(phase, text='본문'):
        def stream(form_data):
            yield f'data: {text}\n\n'
            yield f'data: [PHASE_COMPLETE:{phase}]\n\n'
            yield 'data: [DONE]\n\n'
        return stream

    @staticmethod
    def failing_stream(form_data):
        yield 'data: 일부\n\n'
        yield 'data: [ERROR] OpenAI API 오류 (500)\n\n'
        yield 'data: [DONE]\n\n'

    def manual(self, session):
        db.session.expire_all()
        return ManualGeneration.query.get(session['manual_id'])


class StoredTextTest(ManualRetryTestBase):
    def test_saved_markdown_equals_joined_tokens(self):
        """AC-13: 공백·'[' 토큰이 저장본에서 사라지지 않는다."""
        tokens = ['품질', ' 경영', ' 시스템', '\n', 'A[', '"고객"]', ' ', '끝']
        calls = []

        def stream(form_data):
            calls.append(1)
            for t in tokens:
                yield 'data: ' + t.replace('\n', '\\n') + '\n\n'
            yield 'data: [PHASE_COMPLETE:1]\n\n'
            yield 'data: [DONE]\n\n'

        session = self.new_session()
        self.run_generate(session, stream)
        m = self.manual(session)
        self.assertEqual(m.phase1_markdown, ''.join(tokens))
        self.assertEqual(m.status, 'phase_1_completed')

    def test_saved_text_survives_into_next_phase_context_and_replay(self):
        session = self.new_session()
        self.run_generate(session, self.ok_stream(1, ' 앞 [링크](u) 뒤'))
        self.assertEqual(self.manual(session).phase1_markdown, ' 앞 [링크](u) 뒤')

        seen = {}

        def phase2(form_data):
            seen['prev'] = form_data.get('previous_markdown')
            yield 'data: 둘\n\n'
            yield 'data: [PHASE_COMPLETE:2]\n\n'
            yield 'data: [DONE]\n\n'

        self.run_generate(session, phase2, continue_from=7)
        self.assertEqual(seen['prev'], ' 앞 [링크](u) 뒤')

        # 완료 phase 재전송 — 공백이 보존된 채 그대로 나간다
        _, body = self.run_generate(session, self.failing_stream)
        self.assertIn('data:  앞 [링크](u) 뒤\n\n', body)


class PhasePreconditionTest(ManualRetryTestBase):
    def assert_blocked(self, session, continue_from):
        called = []

        def stream(form_data):
            called.append(1)
            yield 'data: x\n\n'

        before = self.manual(session).status
        resp, body = self.run_generate(session, stream, continue_from=continue_from)
        self.assertIn('[ERROR] 이전 단계가 완료되지 않았습니다.', body)
        self.assertEqual(called, [])          # LLM 호출 0
        self.assertEqual(self.manual(session).status, before)  # 상태 변화 없음

    def test_phase3_requires_phase2(self):
        """AC-14: phase2 가 비어 있으면 continue_from=9 차단."""
        session = self.new_session()
        self.run_generate(session, self.ok_stream(1))
        self.assert_blocked(session, 9)

    def test_phase2_requires_phase1(self):
        session = self.new_session()
        self.assert_blocked(session, 7)
        self.assert_blocked(session, 9)

    def test_valid_order_still_works(self):
        session = self.new_session()
        self.run_generate(session, self.ok_stream(1))
        self.run_generate(session, self.ok_stream(2), continue_from=7)
        self.run_generate(session, self.ok_stream(3), continue_from=9)
        m = self.manual(session)
        self.assertEqual(m.status, 'phase_3_completed')
        self.assertTrue(m.phase3_markdown)


class ResumeTest(ManualRetryTestBase):
    def test_resume_after_phase2_failure_keeps_one_row_and_calls_llm_once_more(self):
        """AC-9: 행 1개 유지, 429 없음, phase 2 에만 LLM 1회 추가."""
        session = self.new_session()
        llm_calls = []

        def counted(phase):
            def stream(form_data):
                llm_calls.append(phase)
                yield 'data: 내용\n\n'
                yield f'data: [PHASE_COMPLETE:{phase}]\n\n'
                yield 'data: [DONE]\n\n'
            return stream

        def failing(form_data):
            llm_calls.append(2)
            yield 'data: 일부\n\n'
            yield 'data: [ERROR] boom\n\n'
            yield 'data: [DONE]\n\n'

        self.run_generate(session, counted(1))
        self.run_generate(session, failing, continue_from=7)
        self.assertEqual(self.manual(session).status, 'phase_2_failed')
        self.assertEqual(llm_calls, [1, 2])

        r = self.resume(session['manual_id'])
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['next_phase'], 2)
        self.assertEqual(data['continue_from'], 7)
        self.assertEqual(data['completed_phases'], [1])
        self.assertTrue(data['is_retry'])
        # T11 F-03 이후 resume 은 차감하지 않고 '이번 재생성까지 포함한 예정값'을 돌려준다.
        # 실제 DB 차감은 generate-iso 선점 시점에 한 번 (tests/manual_generation_claim_test.py).
        self.assertEqual(data['retry_count'], 1)
        self.assertEqual(self.manual(session).retry_count, 0)
        self.assertNotEqual(data['stream_token'], session['stream_token'])
        self.assertEqual(data['expires_in_seconds'], 30 * 60)

        new_session = {'manual_id': data['manual_id'], 'stream_token': data['stream_token']}
        self.run_generate(new_session, counted(2), continue_from=data['continue_from'])
        self.assertEqual(llm_calls, [1, 2, 2])  # phase 1 재호출 0회, phase 2 1회 추가
        m = self.manual(session)
        self.assertEqual(m.retry_count, 1)      # 재생성 선점 시 1회 차감 (T11)
        self.assertEqual(m.status, 'phase_2_completed')
        self.assertTrue(m.phase2_markdown)
        self.assertEqual(ManualGeneration.query.count(), 1)

        # 이전 토큰은 더 이상 쓸 수 없다
        _, body = self.run_generate(session, counted(3), continue_from=9)
        self.assertIn('유효하지 않습니다', body)

    def test_resume_does_not_consume_daily_limit(self):
        session = self.new_session()
        self.run_generate(session, self.failing_stream)
        r = self.resume(session['manual_id'])
        self.assertEqual(r.status_code, 200)  # DAILY_MANUAL_LIMIT=1 이어도 429 아님
        self.assertEqual(ManualGeneration.query.count(), 1)
        # 새 세션은 여전히 하루 한도에 걸린다 (이전 동작 유지)
        again = self.client.post('/api/iso-manual/session', json=self.payload(),
                                 headers=auth_headers(self.company))
        self.assertEqual(again.status_code, 429)

    def test_phase1_failure_resume_phase1(self):
        session = self.new_session()
        self.run_generate(session, self.failing_stream)
        data = self.resume(session['manual_id']).get_json()
        self.assertEqual(data['next_phase'], 1)
        self.assertIsNone(data['continue_from'])
        self.assertEqual(data['completed_phases'], [])
        self.assertTrue(data['is_retry'])

    def test_retry_limit_three_then_429(self):
        """AC-11: 실제 재생성 3회 뒤 4번째 resume 은 429."""
        session = self.new_session()
        self.run_generate(session, self.failing_stream)
        current = session
        for i in range(1, MAX_MANUAL_RETRIES + 1):
            r = self.resume(session['manual_id'])
            self.assertEqual(r.status_code, 200, f'{i}번째 resume')
            self.assertEqual(r.get_json()['retry_count'], i)
            current = {'manual_id': session['manual_id'], 'stream_token': r.get_json()['stream_token']}
            self.run_generate(current, self.failing_stream)  # 다시 실패
        blocked = self.resume(session['manual_id'])
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.get_json()['code'], 'RETRY_LIMIT_EXCEEDED')
        self.assertEqual(self.manual(session).retry_count, MAX_MANUAL_RETRIES)

    def test_retry_limit_is_shared_across_phases_including_free_phase1(self):
        session = self.new_session()
        self.run_generate(session, self.failing_stream)           # phase 1 실패
        r = self.resume(session['manual_id'])                     # 재시도 1 (phase 1)
        tok = {'manual_id': session['manual_id'], 'stream_token': r.get_json()['stream_token']}
        self.run_generate(tok, self.ok_stream(1))                 # phase 1 성공
        self.run_generate(tok, self.failing_stream, continue_from=7)  # phase 2 실패
        for expected in (2, 3):
            r = self.resume(session['manual_id'])
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()['retry_count'], expected)
            tok = {'manual_id': session['manual_id'], 'stream_token': r.get_json()['stream_token']}
            self.run_generate(tok, self.failing_stream, continue_from=7)
        self.assertEqual(self.resume(session['manual_id']).status_code, 429)

    def test_first_attempt_token_reissue_is_not_counted(self):
        """처음 시도하는 phase(1단계 완료 후 2단계 잠금 해제)의 토큰 재발급은 횟수에 넣지 않는다."""
        session = self.new_session()
        self.run_generate(session, self.ok_stream(1))
        for _ in range(MAX_MANUAL_RETRIES + 2):
            r = self.resume(session['manual_id'])
            self.assertEqual(r.status_code, 200)
            data = r.get_json()
            self.assertEqual(data['next_phase'], 2)
            self.assertFalse(data['is_retry'])
            self.assertEqual(data['retry_count'], 0)
        self.assertEqual(self.manual(session).retry_count, 0)

    def test_created_but_never_started_is_not_counted(self):
        session = self.new_session()
        data = self.resume(session['manual_id']).get_json()
        self.assertEqual(data['next_phase'], 1)
        self.assertFalse(data['is_retry'])

    def test_replay_of_completed_phase_does_not_count_or_call_llm(self):
        session = self.new_session()
        self.run_generate(session, self.ok_stream(1))
        called = []

        def stream(form_data):
            called.append(1)
            yield 'data: x\n\n'

        for _ in range(5):
            _, body = self.run_generate(session, stream)
            self.assertIn('[PHASE_COMPLETE:1]', body)
        self.assertEqual(called, [])
        self.assertEqual(self.manual(session).retry_count, 0)

    def test_other_users_manual_returns_404(self):
        session = self.new_session()
        self.run_generate(session, self.failing_stream)
        self.assertEqual(self.resume(session['manual_id'], self.other).status_code, 404)
        self.assertEqual(self.manual(session).retry_count, 0)
        # 존재하지 않는 id 도 404
        self.assertEqual(self.resume('no-such-id').status_code, 404)

    def test_consultant_forbidden_and_anonymous_unauthorized(self):
        session = self.new_session()
        self.assertEqual(self.resume(session['manual_id'], self.consultant).status_code, 403)
        anon = self.client.post(f"/api/iso-manual/{session['manual_id']}/resume")
        self.assertEqual(anon.status_code, 401)

    def test_admin_can_resume_other_users_manual(self):
        session = self.new_session()
        self.run_generate(session, self.failing_stream)
        self.assertEqual(self.resume(session['manual_id'], self.admin).status_code, 200)

    def test_retry_window_24_hours(self):
        session = self.new_session()
        self.run_generate(session, self.failing_stream)
        m = self.manual(session)
        m.created_at = utcnow_naive() - datetime.timedelta(hours=23, minutes=50)
        db.session.commit()
        self.assertEqual(self.resume(session['manual_id']).status_code, 200)

        m = self.manual(session)
        m.created_at = utcnow_naive() - datetime.timedelta(hours=24, minutes=5)
        db.session.commit()
        r = self.resume(session['manual_id'])
        self.assertEqual(r.status_code, 410)
        self.assertEqual(r.get_json()['code'], 'RETRY_WINDOW_EXPIRED')

    def test_all_phases_complete_returns_409(self):
        session = self.new_session()
        self.run_generate(session, self.ok_stream(1))
        self.run_generate(session, self.ok_stream(2), continue_from=7)
        self.run_generate(session, self.ok_stream(3), continue_from=9)
        r = self.resume(session['manual_id'])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()['completed_phases'], [1, 2, 3])

    def test_entitlement_hook_defaults_true_and_gates_phase_2_plus(self):
        self.assertTrue(_is_full_manual_entitled(None))
        session = self.new_session()
        self.run_generate(session, self.ok_stream(1))
        with patch('index._is_full_manual_entitled', lambda m: False):
            r = self.resume(session['manual_id'])
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.get_json()['code'], 'ENTITLEMENT_REQUIRED')
            called = []

            def stream(form_data):
                called.append(1)
                yield 'data: x\n\n'

            _, body = self.run_generate(session, stream, continue_from=7)
            self.assertIn('[ERROR]', body)
            self.assertEqual(called, [])
        # phase 1 은 hook 과 무관하게 허용
        s2 = self.new_session(self.other)
        with patch('index._is_full_manual_entitled', lambda m: False):
            self.assertEqual(self.resume(s2['manual_id'], self.other).status_code, 200)


class StaleGenerationTest(ManualRetryTestBase):
    def set_generating(self, session, phase, age_seconds):
        m = self.manual(session)
        m.status = f'generating_phase_{phase}'
        m.updated_at = utcnow_naive() - datetime.timedelta(seconds=age_seconds)
        db.session.commit()

    def test_resume_allowed_after_120s_conflict_within_30s(self):
        """AC-12."""
        session = self.new_session()
        self.run_generate(session, self.ok_stream(1))

        self.set_generating(session, 2, 30)
        r = self.resume(session['manual_id'])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()['code'], 'GENERATION_IN_PROGRESS')
        self.assertEqual(self.manual(session).status, 'generating_phase_2')  # 건드리지 않음
        self.assertEqual(self.manual(session).retry_count, 0)

        self.set_generating(session, 2, 120)
        r = self.resume(session['manual_id'])
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['next_phase'], 2)
        self.assertTrue(data['is_retry'])           # 중단된 phase 의 재생성이므로 횟수에 포함
        self.assertEqual(data['retry_count'], 1)
        self.assertEqual(self.manual(session).status, 'phase_2_aborted')

    def test_boundary_around_90_seconds(self):
        session = self.new_session()
        self.set_generating(session, 1, MANUAL_GENERATING_STALE_SECONDS - 10)
        self.assertEqual(self.resume(session['manual_id']).status_code, 409)
        self.set_generating(session, 1, MANUAL_GENERATING_STALE_SECONDS + 10)
        self.assertEqual(self.resume(session['manual_id']).status_code, 200)

    def test_generate_blocks_concurrent_run_and_cleans_stale(self):
        session = self.new_session()
        self.set_generating(session, 1, 20)
        called = []

        def stream(form_data):
            called.append(1)
            yield 'data: 본문\n\n'
            yield 'data: [PHASE_COMPLETE:1]\n\n'
            yield 'data: [DONE]\n\n'

        _, body = self.run_generate(session, stream)
        self.assertIn('[ERROR]', body)
        self.assertEqual(called, [])
        self.assertEqual(self.manual(session).status, 'generating_phase_1')

        # 90초 넘게 갱신 없으면 중단으로 보고 새로 생성한다
        self.set_generating(session, 1, 200)
        _, body = self.run_generate(session, stream)
        self.assertEqual(called, [1])
        self.assertEqual(self.manual(session).status, 'phase_1_completed')
        # T11 F-01 이후: 중단된 generating 의 재생성은 선점 UPDATE 에서 재시도 1회로 센다 (이전: 0 유지)
        self.assertEqual(self.manual(session).retry_count, 1)


if __name__ == '__main__':
    unittest.main()
