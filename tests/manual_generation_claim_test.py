"""T11 F-01·F-02·F-03 (T10 D-1·D-2) 회귀 — /api/generate-iso 원자적 선점과 재시도 차감 위치.

정책 (api/index.py `_claim_manual_generation`):
- LLM 을 부르기 직전에 조건부 UPDATE 한 번으로 선점한다(status=generating_phase_N). 재시도(직전 상태가
  이 phase 의 failed/aborted 또는 stale generating)일 때만 retry_count 를 1 올리고 상한을 건다.
- 재시도 차감은 그 선점 UPDATE 한 곳에서만 한다. resume 은 상한 확인과 토큰 재발급만 한다.
- LLM 경로에서 실패·중단되면 그 토큰은 무효화된다. 재시도는 resume(새 토큰)으로만 한다.
- 처음 시도하는 phase 는 같은 토큰으로 이어서 생성할 수 있다(기존 프론트 흐름).

OpenAI 는 호출하지 않는다 (generate_iso_manual_stream 은 mock).
"""
import datetime
import unittest
from unittest.mock import patch

from tests.manual_retry_test import ManualRetryTestBase, utcnow_naive

from index import (  # noqa: E402
    db, _claim_manual_generation, MAX_MANUAL_RETRIES, MANUAL_GENERATING_STALE_SECONDS,
    MANUAL_RETRY_LIMIT_MESSAGE, MANUAL_GENERATION_IN_PROGRESS_MESSAGE,
)


class CountingStreams:
    """LLM 호출 수를 세는 mock 스트림 모음."""

    def __init__(self):
        self.calls = []

    def ok(self, phase, text='본문'):
        def stream(form_data):
            self.calls.append(phase)
            yield f'data: {text}\n\n'
            yield f'data: [PHASE_COMPLETE:{phase}]\n\n'
            yield 'data: [DONE]\n\n'
        return stream

    def failing(self, phase):
        def stream(form_data):
            self.calls.append(phase)
            yield 'data: 일부\n\n'
            yield 'data: [ERROR] OpenAI API 오류 (500)\n\n'
            yield 'data: [DONE]\n\n'
        return stream


class ClaimTestBase(ManualRetryTestBase):
    def setUp(self):
        super().setUp()
        self.llm = CountingStreams()

    def tok(self, session, resume_json):
        return {'manual_id': session['manual_id'], 'stream_token': resume_json['stream_token']}

    def set_state(self, session, status=None, age_seconds=None, retry_count=None):
        m = self.manual(session)
        if status is not None:
            m.status = status
        if age_seconds is not None:
            m.updated_at = utcnow_naive() - datetime.timedelta(seconds=age_seconds)
        if retry_count is not None:
            m.retry_count = retry_count
        db.session.commit()


class SameTokenReuseTest(ClaimTestBase):
    def test_poc_failing_phase_same_token_ten_times(self):
        """F-01 PoC 재현: 실패하는 phase 에 같은 토큰으로 10회 → 이전엔 LLM 10회·retry_count 0. 이제 LLM 1회."""
        session = self.new_session()
        bodies = [self.run_generate(session, self.llm.failing(1))[1] for _ in range(10)]
        self.assertEqual(self.llm.calls, [1])
        m = self.manual(session)
        self.assertEqual(m.retry_count, 0)            # 첫 시도는 차감 없음, 이후 호출은 LLM 미호출
        self.assertEqual(m.status, 'phase_1_failed')
        for body in bodies[1:]:
            self.assertIn('[ERROR] 매뉴얼 생성 세션이 유효하지 않습니다', body)
            self.assertIn('[DONE]', body)

    def test_phase2_same_token_after_failure_is_blocked(self):
        session = self.new_session()
        self.run_generate(session, self.llm.ok(1))
        for _ in range(3):
            self.run_generate(session, self.llm.failing(2), continue_from=7)
        self.assertEqual(self.llm.calls, [1, 2])
        self.assertEqual(self.manual(session).retry_count, 0)
        # 재시도는 resume 으로 — 새 토큰으로 phase 2 를 다시 돌리면 그때 1회 차감
        r = self.resume(session['manual_id']).get_json()
        self.assertEqual((r['next_phase'], r['is_retry']), (2, True))
        self.run_generate(self.tok(session, r), self.llm.ok(2), continue_from=7)
        self.assertEqual(self.llm.calls, [1, 2, 2])
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('phase_2_completed', 1))

    def test_client_disconnect_marks_aborted_and_revokes_token(self):
        """연결 끊김(GeneratorExit) → phase_1_aborted, 같은 토큰으로 LLM 재호출 불가."""
        session = self.new_session()
        url = f"/api/generate-iso?manual_id={session['manual_id']}&stream_token={session['stream_token']}"

        def long_stream(form_data):
            self.llm.calls.append(1)
            for _ in range(5):
                yield 'data: 조각\n\n'
            yield 'data: [PHASE_COMPLETE:1]\n\n'

        with patch('index.generate_iso_manual_stream', long_stream):
            resp = self.client.get(url, buffered=False)
            it = iter(resp.response)
            next(it)          # 첫 조각만 받고
            resp.close()      # 연결을 끊는다
        m = self.manual(session)
        self.assertEqual(m.status, 'phase_1_aborted')
        self.assertIsNone(m.phase1_markdown)
        _, body = self.run_generate(session, self.llm.ok(1))
        self.assertIn('유효하지 않습니다', body)
        self.assertEqual(self.llm.calls, [1])
        # resume → 새 토큰으로 재생성하면 1회 차감
        r = self.resume(session['manual_id']).get_json()
        self.assertTrue(r['is_retry'])
        self.run_generate(self.tok(session, r), self.llm.ok(1))
        self.assertEqual(self.manual(session).retry_count, 1)

    def test_resume_token_reused_after_its_retry_fails(self):
        session = self.new_session()
        self.run_generate(session, self.llm.failing(1))
        r = self.resume(session['manual_id']).get_json()
        retry_tok = self.tok(session, r)
        for _ in range(4):
            self.run_generate(retry_tok, self.llm.failing(1))
        self.assertEqual(self.llm.calls, [1, 1])          # 최초 1 + 재시도 1
        self.assertEqual(self.manual(session).retry_count, 1)

    def test_stale_generating_with_same_token_counts_once_and_respects_limit(self):
        """강제 종료(정리 코드 미실행)로 토큰이 살아 있는 stale generating 은 재시도로 센다 — 상한 안에서만."""
        session = self.new_session()
        self.set_state(session, status='generating_phase_1', age_seconds=MANUAL_GENERATING_STALE_SECONDS + 60)
        self.run_generate(session, self.llm.ok(1))
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('phase_1_completed', 1))

        s2 = self.new_session(self.other)
        self.set_state(s2, status='generating_phase_1', age_seconds=MANUAL_GENERATING_STALE_SECONDS + 60,
                       retry_count=MAX_MANUAL_RETRIES)
        _, body = self.run_generate(s2, self.llm.ok(1))
        self.assertIn(f'[ERROR] {MANUAL_RETRY_LIMIT_MESSAGE}', body)
        self.assertEqual(self.llm.calls, [1])            # s2 는 LLM 미호출
        self.assertEqual(self.manual(s2).retry_count, MAX_MANUAL_RETRIES)

    def test_failed_row_with_live_token_at_limit_is_blocked_by_generate_itself(self):
        """resume 을 거치지 않아도(예: 패치 전 행) generate 선점이 상한을 건다."""
        session = self.new_session()
        self.set_state(session, status='phase_1_failed', retry_count=MAX_MANUAL_RETRIES)
        _, body = self.run_generate(session, self.llm.ok(1))
        self.assertIn(f'[ERROR] {MANUAL_RETRY_LIMIT_MESSAGE}', body)
        self.assertEqual(self.llm.calls, [])
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('phase_1_failed', MAX_MANUAL_RETRIES))


class ConcurrentClaimTest(ClaimTestBase):
    def test_second_request_while_first_is_streaming_gets_in_progress(self):
        """F-02: 선점은 응답 반환 전에 끝난다 → 첫 응답을 읽기 전에 들어온 두 번째 요청은 LLM 미호출."""
        session = self.new_session()
        url = f"/api/generate-iso?manual_id={session['manual_id']}&stream_token={session['stream_token']}"
        with patch('index.generate_iso_manual_stream', self.llm.ok(1)):
            first = self.client.get(url, buffered=False)        # 선점 완료, 스트림은 아직 소비 전
            self.assertEqual(self.manual(session).status, 'generating_phase_1')
            second_body = self.client.get(url).get_data(as_text=True)
            first_body = first.get_data(as_text=True)
        self.assertIn(f'[ERROR] {MANUAL_GENERATION_IN_PROGRESS_MESSAGE}', second_body)
        self.assertIn('[PHASE_COMPLETE:1]', first_body)
        self.assertEqual(self.llm.calls, [1])
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('phase_1_completed', 0))

    # --- 조건부 UPDATE 단위 검증: 두 요청이 같은 '읽은 상태'로 동시에 선점을 시도하는 경우 ---

    def _snapshot(self, session):
        m = self.manual(session)
        return m.id, m.token_hash, m.status

    def test_claim_is_compare_and_swap_first_attempt(self):
        session = self.new_session()
        mid, h, observed = self._snapshot(session)
        self.assertEqual(_claim_manual_generation(mid, h, 1, observed, False), (True, False))
        self.assertEqual(_claim_manual_generation(mid, h, 1, observed, False), (False, False))
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('generating_phase_1', 0))

    def test_retry_claim_increments_once_under_race_and_never_exceeds_limit(self):
        session = self.new_session()
        self.set_state(session, status='phase_1_failed', retry_count=MAX_MANUAL_RETRIES - 1)
        mid, h, observed = self._snapshot(session)
        self.assertEqual(_claim_manual_generation(mid, h, 1, observed, False), (True, True))
        self.assertEqual(_claim_manual_generation(mid, h, 1, observed, False), (False, True))
        self.assertEqual(self.manual(session).retry_count, MAX_MANUAL_RETRIES)

    def test_retry_claim_at_limit_changes_nothing(self):
        session = self.new_session()
        self.set_state(session, status='phase_2_aborted', retry_count=MAX_MANUAL_RETRIES)
        mid, h, observed = self._snapshot(session)
        self.assertEqual(_claim_manual_generation(mid, h, 2, observed, False), (False, True))
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('phase_2_aborted', MAX_MANUAL_RETRIES))

    def test_stale_claim_succeeds_once(self):
        session = self.new_session()
        self.set_state(session, status='generating_phase_1', age_seconds=MANUAL_GENERATING_STALE_SECONDS + 30)
        mid, h, observed = self._snapshot(session)
        self.assertEqual(_claim_manual_generation(mid, h, 1, observed, True), (True, True))
        # 첫 선점이 updated_at 을 갱신했으므로 같은 스냅샷의 두 번째 선점은 '신선한 generating' 에 막힌다
        self.assertEqual(_claim_manual_generation(mid, h, 1, observed, True), (False, True))
        self.assertEqual(self.manual(session).retry_count, 1)

    def test_fresh_generating_is_never_claimed(self):
        session = self.new_session()
        self.set_state(session, status='generating_phase_1', age_seconds=10)
        mid, h, observed = self._snapshot(session)
        # 호출자가 stale 로 잘못 판단해도 SQL 조건(updated_at < 90초 전)이 막는다
        self.assertEqual(_claim_manual_generation(mid, h, 1, observed, True), (False, True))
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('generating_phase_1', 0))

    def test_token_mismatch_is_never_claimed(self):
        session = self.new_session()
        mid, _, observed = self._snapshot(session)
        self.assertEqual(_claim_manual_generation(mid, 'f' * 64, 1, observed, False), (False, False))
        self.assertEqual(self.manual(session).status, 'created')


class ResumeNoDoubleChargeTest(ClaimTestBase):
    def test_resume_twice_without_generation_charges_nothing(self):
        """F-03/D-1: resume 연속 2회 — 응답은 같은 예정값, DB 차감 0, 실제 차감은 생성 시 1회."""
        session = self.new_session()
        self.run_generate(session, self.llm.failing(1))
        first = self.resume(session['manual_id']).get_json()
        second = self.resume(session['manual_id']).get_json()
        for r in (first, second):
            self.assertTrue(r['is_retry'])
            self.assertEqual((r['retry_count'], r['retries_remaining']), (1, MAX_MANUAL_RETRIES - 1))
        self.assertEqual(self.manual(session).retry_count, 0)

        _, body = self.run_generate(self.tok(session, first), self.llm.ok(1))   # 먼저 받은 토큰은 교체됨
        self.assertIn('유효하지 않습니다', body)
        self.run_generate(self.tok(session, second), self.llm.ok(1))
        self.assertEqual(self.manual(session).retry_count, 1)
        self.assertEqual(self.llm.calls, [1, 1])

    def test_resume_many_times_never_exhausts_retries(self):
        """T11 PoC 반대: 생성 없이 resume 만 여러 번 불러도 429 가 나지 않는다 (이전: 4번째 429)."""
        session = self.new_session()
        self.run_generate(session, self.llm.failing(1))
        for _ in range(MAX_MANUAL_RETRIES + 2):
            r = self.resume(session['manual_id'])
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()['retries_remaining'], MAX_MANUAL_RETRIES - 1)
        self.assertEqual(self.manual(session).retry_count, 0)

    def test_resume_after_stale_marks_aborted_without_charge(self):
        session = self.new_session()
        self.run_generate(session, self.llm.ok(1))
        self.set_state(session, status='generating_phase_2', age_seconds=MANUAL_GENERATING_STALE_SECONDS + 30)
        r = self.resume(session['manual_id']).get_json()
        self.assertEqual((r['next_phase'], r['is_retry'], r['retry_count']), (2, True, 1))
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('phase_2_aborted', 0))


class NormalFlowTest(ClaimTestBase):
    def test_three_phases_with_one_token_no_charge(self):
        session = self.new_session()
        self.run_generate(session, self.llm.ok(1))
        self.run_generate(session, self.llm.ok(2), continue_from=7)
        self.run_generate(session, self.llm.ok(3), continue_from=9)
        self.assertEqual(self.llm.calls, [1, 2, 3])
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('phase_3_completed', 0))
        # 성공한 토큰은 무효화되지 않는다 — 완료 phase 재전송(LLM 미호출)은 그대로 가능
        _, body = self.run_generate(session, self.llm.failing(1))
        self.assertIn('[PHASE_COMPLETE:1]', body)
        self.assertEqual(self.llm.calls, [1, 2, 3])

    def test_retry_success_then_next_phase_with_same_resume_token(self):
        session = self.new_session()
        self.run_generate(session, self.llm.ok(1))
        self.run_generate(session, self.llm.failing(2), continue_from=7)
        r = self.resume(session['manual_id']).get_json()
        tok = self.tok(session, r)
        self.run_generate(tok, self.llm.ok(2), continue_from=7)
        self.run_generate(tok, self.llm.ok(3), continue_from=9)   # 첫 시도 phase → 차감 없음
        m = self.manual(session)
        self.assertEqual((m.status, m.retry_count), ('phase_3_completed', 1))
        self.assertEqual(self.llm.calls, [1, 2, 2, 3])

    def test_first_attempt_after_token_reissue_is_not_charged(self):
        """1단계 완료 후 resume(예: 30분 만료 뒤 잠금 해제)으로 받은 토큰의 phase 2 첫 시도는 차감 없음."""
        session = self.new_session()
        self.run_generate(session, self.llm.ok(1))
        r = self.resume(session['manual_id']).get_json()
        self.assertFalse(r['is_retry'])
        self.assertEqual((r['retry_count'], r['retries_remaining']), (0, MAX_MANUAL_RETRIES))
        self.run_generate(self.tok(session, r), self.llm.ok(2), continue_from=7)
        self.assertEqual(self.manual(session).retry_count, 0)


if __name__ == '__main__':
    unittest.main()
