"""L1-C2: 리뷰 루프 테스트.

여기서 지키려는 것은 세 가지다.

1) **평점 조작 방어.** 리뷰는 매칭 배점의 17%(WEIGHT_RATING)를 직접 움직인다.
   프로젝트당 1건 제약이 뚫리면 같은 거래 하나로 평점을 만들 수 있고,
   completed 검사가 뚫리면 거래가 끝나기도 전에 평점이 생긴다.

2) **수축(shrinkage)이 실제로 작동하는가.** 단순 평균을 쓰면 리뷰 1건짜리
   5.0 이 리뷰 20건짜리 4.8 을 이긴다. 이 역전이 없어야 한다.

3) **집계와 매칭이 같은 '중립'을 쓰는가.** 두 곳이 각자 중립을 정의하면
   점수가 어긋난다 — 이전 배분에서는 '5.0 리뷰 1건' 이 '리뷰 0건' 보다
   낮은 점수를 받는 역전이 실제로 있었다.
"""
import datetime
import os
import sys
import unittest

import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../api')))

# index 를 import 하기 전에 인메모리 DB 를 지정해야 한다.
# (conftest.py 와 같은 이유 — 안 하면 drop_all() 이 개발용 insightmatch.db 를 지운다)
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

from werkzeug.security import generate_password_hash

import index as index_module
from index import app, db
from models import Consultant, Notification, Project, Review, User
from services import matching_service as matching_module


PASSWORD = 'test-password-1234'


def make_token(user):
    payload = {
        'user_id': user.id,
        'role': user.role,
        'tv': user.token_version or 0,
        'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')


def auth_headers(user):
    return {'Authorization': f'Bearer {make_token(user)}'}


class ReviewLoopTestBase(unittest.TestCase):
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
            password_hash=generate_password_hash(PASSWORD),
            role='company',
            name='담당자',
            company_name='인사이트제조',
        )
        self.other_company = User(
            email='other@example.com',
            password_hash=generate_password_hash(PASSWORD),
            role='company',
            name='남의 담당자',
            company_name='남의회사',
        )
        self.consultant_user = User(
            email='consultant@example.com',
            password_hash=generate_password_hash(PASSWORD),
            role='consultant',
            name='전문가',
        )
        self.admin_user = User(
            email='admin@example.com',
            password_hash=generate_password_hash(PASSWORD),
            role='admin',
            name='관리자',
        )
        db.session.add_all([
            self.company, self.other_company, self.consultant_user, self.admin_user,
        ])
        db.session.flush()

        self.consultant = Consultant(
            user_id=self.consultant_user.id,
            name='ISO Expert',
            verified=True,
            status='verified',
            rating=index_module.NEW_CONSULTANT_RATING,
            reviews=0,
        )
        db.session.add(self.consultant)
        db.session.commit()

        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── 헬퍼 ────────────────────────────────────────────────
    def add_project(self, status='completed', company=None, consultant_id=-1, title='ISO 9001 구축'):
        project = Project(
            company_id=(company or self.company).id,
            consultant_id=(self.consultant.id if consultant_id == -1 else consultant_id),
            title=title,
            status=status,
        )
        if status == 'completed':
            project.completed_at = index_module._naive_utc_now()
        db.session.add(project)
        db.session.commit()
        return project

    def post_review(self, project, user=None, **overrides):
        payload = {'rating': 5, 'comment': '꼼꼼하게 진행해주셨습니다.'}
        payload.update(overrides)
        return self.client.post(
            f'/api/projects/{project.id}/review',
            json=payload,
            headers=auth_headers(user or self.company),
        )


class ReviewWritePermissionTest(ReviewLoopTestBase):
    """작성 권한 — 누가, 언제 쓸 수 있는가."""

    def test_company_can_review_completed_project(self):
        project = self.add_project('completed')
        response = self.post_review(project, rating=4)
        self.assertEqual(response.status_code, 201)

        review = Review.query.filter_by(project_id=project.id).first()
        self.assertIsNotNone(review)
        self.assertEqual(review.rating, 4)
        self.assertEqual(review.company_id, self.company.id)
        self.assertEqual(review.consultant_id, self.consultant.id)

    def test_one_review_per_project_enforced_by_application(self):
        """(a) 프로젝트당 1건 — 두 번째 등록은 409 로 거부된다.

        이 제약이 없으면 같은 거래 하나로 5점을 반복 등록해 평점을 만들 수 있다.
        """
        project = self.add_project('completed')
        self.assertEqual(self.post_review(project).status_code, 201)

        second = self.post_review(project, rating=5, comment='한 번 더')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json().get('code'), 'REVIEW_ALREADY_EXISTS')

        self.assertEqual(Review.query.filter_by(project_id=project.id).count(), 1)

    def test_one_review_per_project_enforced_by_database(self):
        """(a) 애플리케이션 검사를 우회해도 DB unique 제약이 막아야 한다.

        서버리스는 같은 요청을 동시에 여러 인스턴스가 처리하므로,
        조회-삽입 사이의 경합은 DB 제약만이 막는다.
        """
        from sqlalchemy.exc import IntegrityError

        project = self.add_project('completed')
        db.session.add(Review(
            project_id=project.id, consultant_id=self.consultant.id,
            company_id=self.company.id, rating=5,
        ))
        db.session.commit()

        db.session.add(Review(
            project_id=project.id, consultant_id=self.consultant.id,
            company_id=self.company.id, rating=1,
        ))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_review_rejected_for_non_completed_project(self):
        """(b) completed 가 아닌 프로젝트에는 리뷰를 쓸 수 없다.

        진행 중에 평점을 남길 수 있으면, 그 평점이 협상 카드가 된다
        ("잘 해주지 않으면 1점 주겠다").
        """
        for status in ('proposal_pending', 'proposal_submitted', 'contracted',
                       'in_progress', 'cancelled_by_company'):
            with self.subTest(status=status):
                project = self.add_project(status, title=f'프로젝트 {status}')
                response = self.post_review(project)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(Review.query.filter_by(project_id=project.id).count(), 0)

    def test_review_rejected_for_other_companys_project(self):
        """(c) 남의 프로젝트에는 리뷰를 쓸 수 없다."""
        project = self.add_project('completed', company=self.company)
        response = self.post_review(project, user=self.other_company)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Review.query.count(), 0)

    def test_review_rejected_for_consultant_self_review(self):
        """(d) 컨설턴트는 자기가 수행한 프로젝트에 리뷰를 쓸 수 없다.

        평가를 받는 쪽이 평가를 쓸 수 있으면 평점 자체가 의미를 잃는다.
        """
        project = self.add_project('completed')
        response = self.post_review(project, user=self.consultant_user)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Review.query.count(), 0)

    def test_admin_cannot_write_review_on_behalf(self):
        """관리자도 대신 쓸 수 없다.

        완료 전이(complete_project)는 분쟁 처리를 위해 관리자 대행을 허용하지만
        리뷰는 다르다 — 관리자가 쓸 수 있으면 평점이 운영자 재량이 된다.
        """
        project = self.add_project('completed')
        response = self.post_review(project, user=self.admin_user)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Review.query.count(), 0)

    def test_rating_outside_one_to_five_is_rejected(self):
        """(g) 1~5 범위 밖의 평점은 거부된다."""
        for bad in (0, 6, -1, 100, 3.5, 'five', None, True):
            with self.subTest(rating=bad):
                project = self.add_project('completed', title=f'프로젝트 {bad}')
                response = self.post_review(project, rating=bad)
                self.assertEqual(response.status_code, 400, f'rating={bad!r} 가 통과했다')
                self.assertEqual(Review.query.filter_by(project_id=project.id).count(), 0)

    def test_review_rejected_when_no_consultant_assigned(self):
        """평가 대상(컨설턴트)이 없는 프로젝트에는 리뷰를 쓸 수 없다."""
        project = self.add_project('completed', consultant_id=None)
        response = self.post_review(project)
        self.assertEqual(response.status_code, 400)


class ReviewAggregationTest(ReviewLoopTestBase):
    """집계 — Consultant.rating / reviews 재계산."""

    def _add_reviews(self, ratings, consultant=None):
        consultant = consultant or self.consultant
        created = []
        for i, rating in enumerate(ratings):
            project = self.add_project('completed', title=f'프로젝트 {i}')
            review = Review(
                project_id=project.id,
                consultant_id=consultant.id,
                company_id=self.company.id,
                rating=rating,
            )
            db.session.add(review)
            created.append(review)
        db.session.commit()
        index_module.recalculate_consultant_rating(consultant.id)
        db.session.commit()
        return created

    def test_rating_is_recalculated_from_review_rows(self):
        """저장값은 Review 행에서 계산한 산술평균이어야 한다."""
        self._add_reviews([5, 4, 3])
        self.assertEqual(self.consultant.reviews, 3)
        self.assertAlmostEqual(self.consultant.rating, 4.0, places=2)

    def test_edit_recalculates_average(self):
        """수정하면 평균이 다시 계산되고 updated_at 이 남는다."""
        project = self.add_project('completed')
        self.assertEqual(self.post_review(project, rating=5).status_code, 201)
        db.session.refresh(self.consultant)
        self.assertAlmostEqual(self.consultant.rating, 5.0, places=2)

        response = self.client.put(
            f'/api/projects/{project.id}/review',
            json={'rating': 2, 'comment': '다시 생각해보니'},
            headers=auth_headers(self.company),
        )
        self.assertEqual(response.status_code, 200)

        db.session.refresh(self.consultant)
        self.assertAlmostEqual(self.consultant.rating, 2.0, places=2)
        self.assertEqual(self.consultant.reviews, 1)

        review = Review.query.filter_by(project_id=project.id).first()
        self.assertIsNotNone(review.updated_at)
        self.assertGreaterEqual(review.updated_at, review.created_at)

    def test_unedited_review_is_not_marked_as_edited(self):
        """한 번도 고치지 않은 리뷰에 '수정됨' 이 붙으면 안 된다.

        default=utc_now 는 컬럼마다 따로 평가되므로 created_at 과 updated_at 에
        마이크로초 차이가 생긴다. 단순 비교(updated_at > created_at)를 쓰면
        신규 리뷰가 전부 '수정됨' 으로 표시된다(로컬에서 실제로 관측했다).
        """
        project = self.add_project('completed')
        self.post_review(project, rating=5, comment='처음 그대로')

        public = self.client.get(
            f'/api/consultants/{self.consultant.id}/reviews').get_json()
        self.assertFalse(public['items'][0]['edited'])

        # 모델 기본값만으로 만든 행(API 를 거치지 않은 경로)도 마찬가지여야 한다
        raw_project = self.add_project('completed', title='기본값 경로')
        db.session.add(Review(project_id=raw_project.id, consultant_id=self.consultant.id,
                              company_id=self.company.id, rating=4))
        db.session.commit()
        public = self.client.get(
            f'/api/consultants/{self.consultant.id}/reviews').get_json()
        self.assertFalse(any(item['edited'] for item in public['items']))

    def test_edited_review_is_marked_as_edited(self):
        project = self.add_project('completed')
        self.post_review(project, rating=5)

        review = Review.query.filter_by(project_id=project.id).first()
        review.created_at = index_module._naive_utc_now() - datetime.timedelta(days=1)
        db.session.commit()

        response = self.client.put(
            f'/api/projects/{project.id}/review',
            json={'rating': 4, 'comment': '수정했습니다'},
            headers=auth_headers(self.company),
        )
        self.assertEqual(response.status_code, 200)

        public = self.client.get(
            f'/api/consultants/{self.consultant.id}/reviews').get_json()
        self.assertTrue(public['items'][0]['edited'])

    def test_only_the_author_can_edit(self):
        project = self.add_project('completed')
        self.post_review(project, rating=5)

        for actor in (self.other_company, self.consultant_user, self.admin_user):
            with self.subTest(actor=actor.email):
                response = self.client.put(
                    f'/api/projects/{project.id}/review',
                    json={'rating': 1},
                    headers=auth_headers(actor),
                )
                self.assertEqual(response.status_code, 403)

        db.session.refresh(self.consultant)
        self.assertAlmostEqual(self.consultant.rating, 5.0, places=2)

    def test_edit_window_expires(self):
        """수정 가능 기간이 지나면 거부된다."""
        project = self.add_project('completed')
        self.post_review(project, rating=5)

        review = Review.query.filter_by(project_id=project.id).first()
        review.created_at = index_module._naive_utc_now() - datetime.timedelta(
            days=index_module.REVIEW_EDIT_WINDOW_DAYS + 1)
        db.session.commit()

        response = self.client.put(
            f'/api/projects/{project.id}/review',
            json={'rating': 1},
            headers=auth_headers(self.company),
        )
        self.assertEqual(response.status_code, 400)

    def test_hidden_review_is_excluded_from_average(self):
        """(f) 관리자가 숨기면 평균에서 즉시 빠지고, 해제하면 되돌아온다.

        공개 목록과 평균 중 한쪽만 빠지면 "화면에 없는 리뷰가 평점을 끌어내리는"
        상태가 되어 아무도 설명하지 못한다.
        """
        reviews = self._add_reviews([5, 1])
        self.assertAlmostEqual(self.consultant.rating, 3.0, places=2)
        self.assertEqual(self.consultant.reviews, 2)

        bad = next(r for r in reviews if r.rating == 1)
        response = self.client.post(
            f'/api/admin/reviews/{bad.id}/hide',
            json={'hidden': True, 'reason': '욕설 포함'},
            headers=auth_headers(self.admin_user),
        )
        self.assertEqual(response.status_code, 200)

        db.session.refresh(self.consultant)
        self.assertAlmostEqual(self.consultant.rating, 5.0, places=2)
        self.assertEqual(self.consultant.reviews, 1)

        # 공개 목록에서도 사라져야 한다
        public = self.client.get(f'/api/consultants/{self.consultant.id}/reviews').get_json()
        self.assertEqual(public['total'], 1)
        self.assertEqual(len(public['items']), 1)

        # 숨김 해제하면 평균이 되돌아온다 (증분 갱신이면 되돌아오지 못한다)
        self.client.post(
            f'/api/admin/reviews/{bad.id}/hide',
            json={'hidden': False},
            headers=auth_headers(self.admin_user),
        )
        db.session.refresh(self.consultant)
        self.assertAlmostEqual(self.consultant.rating, 3.0, places=2)
        self.assertEqual(self.consultant.reviews, 2)

    def test_hiding_the_last_review_returns_to_no_rating_state(self):
        """보이는 리뷰가 0건이 되면 '평가 없음' 상태로 돌아가야 한다."""
        reviews = self._add_reviews([5])
        self.client.post(
            f'/api/admin/reviews/{reviews[0].id}/hide',
            json={'hidden': True, 'reason': '허위 리뷰'},
            headers=auth_headers(self.admin_user),
        )
        db.session.refresh(self.consultant)
        self.assertEqual(self.consultant.reviews, 0)
        self.assertEqual(self.consultant.rating, index_module.NEW_CONSULTANT_RATING)

    def test_hide_requires_reason_and_admin(self):
        reviews = self._add_reviews([5])
        review_id = reviews[0].id

        no_reason = self.client.post(
            f'/api/admin/reviews/{review_id}/hide',
            json={'hidden': True},
            headers=auth_headers(self.admin_user),
        )
        self.assertEqual(no_reason.status_code, 400)

        not_admin = self.client.post(
            f'/api/admin/reviews/{review_id}/hide',
            json={'hidden': True, 'reason': '지워줘'},
            headers=auth_headers(self.company),
        )
        self.assertIn(not_admin.status_code, (401, 403))

        db.session.refresh(self.consultant)
        self.assertEqual(self.consultant.reviews, 1)

    def test_no_user_facing_delete_route(self):
        """삭제는 사용자에게 열지 않는다 (관리자 숨김만)."""
        project = self.add_project('completed')
        self.post_review(project, rating=5)
        response = self.client.delete(
            f'/api/projects/{project.id}/review', headers=auth_headers(self.company))
        self.assertEqual(response.status_code, 405)


class RatingShrinkageTest(ReviewLoopTestBase):
    """(e) 신뢰도 가중 평균 — 표본 1건짜리 만점이 다수 리뷰를 이기면 안 된다."""

    def test_single_five_star_does_not_beat_many_high_ratings(self):
        """리뷰 1건 5.0 < 리뷰 20건 4.8.

        단순 평균이면 5.0 > 4.8 로 1건짜리가 이긴다. 표본이 1이면 그 5.0 은
        거의 정보가 없는데도 만점 대우를 받고, 그 결과 매칭 상위가 신규
        1건짜리로 뒤덮인다.
        """
        rookie = matching_module._rating_block(5.0, 1)
        veteran = matching_module._rating_block(4.8, 20)
        self.assertLess(rookie, veteran,
                        f'수축이 작동하지 않는다: 1건 5.0={rookie:.2f}, 20건 4.8={veteran:.2f}')

    def test_single_five_star_does_not_beat_ten_review_average(self):
        """리뷰 1건 5.0 < 리뷰 10건 4.5.

        구 배분에서 실제로 뒤집혀 있던 조합이다 (12.47 > 11.33). 평점 4.5 는
        5.0 보다 낮지만 표본이 10배라 실적으로는 더 믿을 만하다.
        """
        self.assertLess(
            matching_module._rating_block(5.0, 1),
            matching_module._rating_block(4.5, 10),
        )

    def test_single_five_star_does_not_beat_five_review_average(self):
        """리뷰 1건 5.0 < 리뷰 5건 4.6 (구 배분: 12.47 > 9.07 로 뒤집혀 있었다)."""
        self.assertLess(
            matching_module._rating_block(5.0, 1),
            matching_module._rating_block(4.6, 5),
        )

    def test_shrinkage_converges_to_raw_average_as_reviews_grow(self):
        """리뷰가 쌓일수록 수축값이 실제 평균으로 수렴해야 한다."""
        shrink = matching_module.bayesian_rating
        far = abs(shrink(5.0, 1) - 5.0)
        near = abs(shrink(5.0, 100) - 5.0)
        self.assertLess(near, far)
        self.assertAlmostEqual(shrink(5.0, 10000), 5.0, places=2)

    def test_zero_reviews_yields_exactly_the_prior(self):
        """리뷰 0건이면 수축값은 정확히 사전값이다 (특수 분기가 필요 없다는 근거)."""
        shrink = matching_module.bayesian_rating
        self.assertAlmostEqual(shrink(0.0, 0), matching_module.RATING_PRIOR_VALUE)
        self.assertAlmostEqual(shrink(5.0, 0), matching_module.RATING_PRIOR_VALUE)
        # rating 시드가 무엇이든 결과가 같아야 한다 (가입 경로별 시드 불일치 방어)
        self.assertEqual(
            matching_module._rating_block(0.0, 0),
            matching_module._rating_block(5.0, 0),
        )

    def test_prior_weight_is_a_named_constant_in_a_sane_range(self):
        """사전가중치는 이름 있는 상수여야 하고, 값이 극단이면 의미가 사라진다."""
        weight = matching_module.RATING_PRIOR_WEIGHT
        self.assertIsInstance(weight, int)
        # 1~2 면 1건짜리 만점이 거의 그대로 반영되어 수축의 의미가 없고,
        # 20 이상이면 이 시장의 리뷰 볼륨으로는 아무도 사전값을 벗어나지 못한다.
        self.assertGreaterEqual(weight, 3)
        self.assertLessEqual(weight, 15)

    def test_neutral_is_defined_in_exactly_one_place(self):
        """집계와 매칭이 같은 '중립' 을 써야 한다.

        배치 4는 `if reviews <= 0: return WEIGHT_RATING * 0.5` 로 중립을
        따로 박아 두었다. 지금은 중립이 수축 공식의 n=0 극한에서 유도된다.
        이 항등식이 깨지면 두 곳이 서로 다른 중립을 쓰기 시작한 것이다.
        """
        expected = (
            matching_module.RATING_SUB_SCORE
            * matching_module._rating_ratio(matching_module.RATING_PRIOR_VALUE)
        )
        self.assertAlmostEqual(matching_module.NO_REVIEW_NEUTRAL_RATIO, expected)
        self.assertAlmostEqual(
            matching_module._rating_block(index_module.NEW_CONSULTANT_RATING, 0),
            matching_module.WEIGHT_RATING * matching_module.NO_REVIEW_NEUTRAL_RATIO,
        )
        # 사전값이 척도의 중점이므로 무정보 상태의 평점 비율은 정확히 0.5 다.
        self.assertAlmostEqual(
            matching_module._rating_ratio(matching_module.RATING_PRIOR_VALUE), 0.5)

    def test_collecting_reviews_is_never_a_penalty(self):
        """좋은 평가를 모으면 점수가 올라가야 한다 — 구 배분에서는 내려갔다.

        구 배분(0.6/0.4 + 계단 함수)에서 리뷰 0건은 8.50 인데 '평균 4.0 리뷰
        5건' 은 5.67 이었다. 즉 보통 평가를 다섯 건 모으면 아무 평가도 없는
        것보다 낮아졌다. 리뷰를 모을수록 손해인 구조는 리뷰 수집 기능 자체와
        모순된다.
        """
        block = matching_module._rating_block
        neutral = block(index_module.NEW_CONSULTANT_RATING, 0)
        self.assertGreater(block(5.0, 1), neutral)
        self.assertGreater(block(4.0, 5), neutral)
        # 반대로 나쁜 평가는 중립보다 낮아야 한다
        self.assertLess(block(1.0, 1), neutral)
        self.assertLess(block(3.0, 20), neutral)

    def test_rating_block_is_monotonic_in_rating(self):
        """같은 리뷰 수라면 평점이 높을수록 점수가 높아야 한다."""
        block = matching_module._rating_block
        scores = [block(r, 10) for r in (1.0, 2.0, 3.0, 4.0, 4.5, 5.0)]
        self.assertEqual(scores, sorted(scores))

    def test_end_to_end_shrinkage_through_matching(self):
        """실제 매칭 결과에서도 1건짜리 5.0 이 다수 리뷰를 이기지 않는다."""
        from services.matching_service import MatchingService
        import json as json_module

        def base(**kwargs):
            defaults = dict(
                specialty='General', match_reason='',
                iso_experience=json_module.dumps({}),
                industry_experience=json_module.dumps([]),
                project_types=json_module.dumps([]),
                verified=True, trust_score=70.0, regions='',
            )
            defaults.update(kwargs)
            return Consultant(**defaults)

        db.session.add_all([
            base(name='One Review', rating=5.0, reviews=1),
            base(name='Many Reviews', rating=4.8, reviews=20),
        ])
        db.session.commit()

        results = MatchingService().match_consultants({'timeline': 'flexible'})
        scores = {r['name']: r['matchScore'] for r in results}
        self.assertLess(scores['One Review'], scores['Many Reviews'])


class ReviewDisplayTest(ReviewLoopTestBase):
    """표시 — 공개 목록, 작성자 마스킹, 탈퇴 회원 처리."""

    def test_public_list_masks_author_company_name(self):
        """작성자 기업명을 그대로 노출하면 거래 관계가 드러난다."""
        project = self.add_project('completed')
        self.post_review(project, rating=5, comment='좋았습니다')

        data = self.client.get(f'/api/consultants/{self.consultant.id}/reviews').get_json()
        label = data['items'][0]['authorLabel']
        self.assertNotEqual(label, self.company.company_name)
        self.assertNotIn(self.company.company_name, label)
        self.assertTrue(label.startswith(self.company.company_name[0]))

    def test_withdrawn_author_is_shown_as_anonymous_but_review_survives(self):
        """탈퇴 회원의 리뷰는 남기고 작성자 표기만 익명화한다.

        L1-C1 의 원칙("개인정보는 익명화, 행은 남긴다")과 같다. 리뷰를 지우면
        컨설턴트의 평점이 자기와 무관한 이유로 흔들리고, "리뷰를 남긴 뒤
        탈퇴하면 리뷰가 사라진다" 는 조작 경로가 생긴다.
        """
        project = self.add_project('completed')
        self.post_review(project, rating=5, comment='좋았습니다')

        self.company.deleted_at = index_module._naive_utc_now()
        self.company.name = '탈퇴한 회원'
        db.session.commit()

        data = self.client.get(f'/api/consultants/{self.consultant.id}/reviews').get_json()
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['authorLabel'], '탈퇴한 기업')
        self.assertEqual(data['items'][0]['comment'], '좋았습니다')

        # 평균에서도 빠지지 않는다
        db.session.refresh(self.consultant)
        self.assertEqual(self.consultant.reviews, 1)

    def test_public_list_never_leaks_hidden_reviews_or_author_ids(self):
        project = self.add_project('completed')
        self.post_review(project, rating=1, comment='심한 말')
        review = Review.query.first()
        review.hidden_at = index_module._naive_utc_now()
        db.session.commit()

        data = self.client.get(f'/api/consultants/{self.consultant.id}/reviews').get_json()
        self.assertEqual(data['total'], 0)
        self.assertEqual(data['items'], [])

        # 작성자 user id 는 공개 응답에 들어가면 안 된다
        self.post_review(self.add_project('completed', title='다른 프로젝트'), rating=5)
        data = self.client.get(f'/api/consultants/{self.consultant.id}/reviews').get_json()
        self.assertNotIn('companyId', data['items'][0])

    def test_consultant_can_read_review_received(self):
        project = self.add_project('completed')
        self.post_review(project, rating=4, comment='감사합니다')

        response = self.client.get(
            f'/api/projects/{project.id}/review', headers=auth_headers(self.consultant_user))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['review']['rating'], 4)

    def test_outsider_cannot_read_project_review(self):
        project = self.add_project('completed')
        self.post_review(project, rating=4)
        response = self.client.get(
            f'/api/projects/{project.id}/review', headers=auth_headers(self.other_company))
        self.assertEqual(response.status_code, 403)

    def test_projects_api_exposes_has_review_flag(self):
        """대시보드의 '리뷰 작성' 진입점 판정에 쓰인다."""
        project = self.add_project('completed')

        before = self.client.get(
            f'/api/projects?user_id={self.company.id}', headers=auth_headers(self.company)
        ).get_json()
        self.assertFalse(next(p for p in before if p['id'] == project.id)['has_review'])

        self.post_review(project, rating=5)

        after = self.client.get(
            f'/api/projects?user_id={self.company.id}', headers=auth_headers(self.company)
        ).get_json()
        self.assertTrue(next(p for p in after if p['id'] == project.id)['has_review'])

    def test_frontend_shows_no_rating_instead_of_zero(self):
        """리뷰 0건에 '평점 0.0' 을 그리면 '최악' 으로 읽힌다.

        0.0 은 '평가 없음' 이지 '최악' 이 아니고, 매칭도 이를 중립으로 처리한다.
        화면과 점수 산정이 같은 말을 해야 한다.
        """
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        with open(os.path.join(root, 'script.js'), encoding='utf-8') as fh:
            script = fh.read()
        self.assertIn('function formatRating', script)
        self.assertIn('평가 없음', script)

        with open(os.path.join(root, 'consultant_profile.html'), encoding='utf-8') as fh:
            profile = fh.read()
        self.assertIn('평가 없음', profile)
        # 리뷰 코멘트는 외부 입력이다 — 렌더링에 escapeHtml 이 반드시 있어야 한다.
        self.assertIn('escapeHtml(r.comment)', profile)
        self.assertIn('escapeHtml(r.authorLabel', profile)


class ReviewRequestFlowTest(ReviewLoopTestBase):
    """리뷰 요청 흐름 — 완료 시 알림 + cron 리마인더 1회."""

    def _complete(self, project, actor=None):
        return self.client.post(
            f'/api/projects/{project.id}/complete',
            headers=auth_headers(actor or self.company),
        )

    def test_completion_creates_review_request_notification_for_company(self):
        project = self.add_project('in_progress')
        self.assertEqual(self._complete(project).status_code, 200)

        requests = Notification.query.filter_by(
            user_id=self.company.id, type='review_request').all()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].link, f'/dashboard.html?review={project.id}')

        # 컨설턴트에게는 리뷰 요청을 보내지 않는다 (쓸 권한이 없으므로)
        self.assertEqual(Notification.query.filter_by(
            user_id=self.consultant_user.id, type='review_request').count(), 0)

    def test_review_request_is_left_for_the_unread_promotion_batch(self):
        """메일 코드를 새로 짜지 않는다 — emailed_at 이 비어 있어야 L1-B 가 승격한다."""
        project = self.add_project('in_progress')
        self._complete(project)
        notification = Notification.query.filter_by(type='review_request').first()
        self.assertIsNone(notification.emailed_at)

    def test_review_reminder_is_sent_once_after_the_threshold(self):
        project = self.add_project('completed')
        project.completed_at = index_module._naive_utc_now() - datetime.timedelta(
            days=index_module.REVIEW_REMINDER_DAYS + 1)
        db.session.commit()

        first = index_module._cron_job_review_reminder()
        self.assertEqual(first['notified'], 1)

        # 두 번째 실행에서는 같은 건으로 다시 보내지 않는다 (영원히 조르지 않는다)
        second = index_module._cron_job_review_reminder()
        self.assertEqual(second['notified'], 0)
        self.assertEqual(Notification.query.filter_by(type='review_reminder').count(), 1)

    def test_review_reminder_skips_projects_that_already_have_a_review(self):
        project = self.add_project('completed')
        self.post_review(project, rating=5)
        project.completed_at = index_module._naive_utc_now() - datetime.timedelta(
            days=index_module.REVIEW_REMINDER_DAYS + 1)
        db.session.commit()

        result = index_module._cron_job_review_reminder()
        self.assertEqual(result['notified'], 0)

    def test_review_reminder_gives_up_after_the_deadline(self):
        """포기 기한이 지나면 더 이상 보내지 않는다."""
        project = self.add_project('completed')
        project.completed_at = index_module._naive_utc_now() - datetime.timedelta(
            days=index_module.REVIEW_REMINDER_GIVEUP_DAYS + 1)
        db.session.commit()

        result = index_module._cron_job_review_reminder()
        self.assertEqual(result['notified'], 0)

    def test_review_reminder_not_sent_before_the_threshold(self):
        self.add_project('completed')  # completed_at = 방금
        result = index_module._cron_job_review_reminder()
        self.assertEqual(result['notified'], 0)

    def test_reminder_dedup_window_covers_the_giveup_window(self):
        """중복 방지 조회 창이 포기 기한보다 짧으면 리마인더가 두 번 나간다.

        후보는 completed_at >= now - GIVEUP 로 한정되므로, 알림 이력 조회도
        같은 기간을 덮어야 '1회' 가 보장된다.
        """
        source_path = os.path.join(os.path.dirname(__file__), '..', 'api', 'index.py')
        with open(source_path, encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn(
            "_recently_notified_links(\n        'review_reminder', REVIEW_REMINDER_GIVEUP_DAYS * 24)",
            source,
        )

    def test_review_reminder_is_registered_in_the_daily_cron(self):
        """등록하지 않으면 함수만 있고 영원히 실행되지 않는다."""
        self.assertIn('review_reminder', index_module.CRON_DAILY_JOBS)
        self.assertTrue(callable(index_module._resolve_cron_job('review_reminder')))
        # 리마인더는 미열람 승격보다 먼저 돌아야 한다 (즉시 발송 경로가 먼저).
        jobs = list(index_module.CRON_DAILY_JOBS)
        self.assertLess(jobs.index('review_reminder'), jobs.index('unread_digest'))

    def test_consultant_is_notified_when_a_review_arrives(self):
        project = self.add_project('completed')
        self.post_review(project, rating=5)
        self.assertEqual(Notification.query.filter_by(
            user_id=self.consultant_user.id, type='review_received').count(), 1)


if __name__ == '__main__':
    unittest.main()
