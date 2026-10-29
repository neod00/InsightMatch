# dashboard.html B안 이식 — 화면 요소 의존성 점검표

기준: main `66f2b44` 의 `dashboard.html`(HEAD) → 브랜치 `design/b-momentum-dashboard`. 인벤토리는 스크립트로 뽑았다(인라인 `<script>` 3개 + 정적 마크업 + 템플릿 문자열).
표시: ✅ 이름 그대로 유지 · ⚠️ 의도적으로 바꿈(사유 기재). 같은 파일 안 스크립트가 쓰는 계약이라 **이름은 하나도 바꾸지 않았다**(테마 토글만 제거).

## 요약

- 요소 id: HEAD 94개 중 91개 유지, 3개 제거(테마 토글). 스크립트가 `getElementById` 로 찾는 id 85개는 전부 마크업에 있다(`custom-confirm-modal` 은 스크립트가 만들고, `schedule-date-<id>` 는 템플릿이 만든다).
- 인라인 핸들러 속성: HEAD 97개 → 이후 97개 (같음). 호출 함수 61종 모두 유지.
- querySelector 선택자 10개, name 2개, data-* 3개, 상태 클래스 6개, 모달 11개 — 전부 유지.
- 템플릿 HTML: 스크립트가 그리는 HTML 의 클래스 이름(btn·card·quote-request-*·status-badge·detail-* …)은 그대로 두고 brand.css 15절에서 B안 모양으로 다시 정의. 인라인 색(초록·파랑·호박색 rgba)만 쓰던 안내 상자는 `.hm-note` 계열 클래스로 바꿨다. id·onclick·data-*·escapeHtml/escapeAttr·조건 분기는 문자 그대로 유지.
- 외부 스크립트: Tailwind CDN·`lucide@latest`·`style.css`·Noto 폰트 제거. lucide 는 메인과 같은 `lucide@0.460.0` 고정. Supabase JS(`@2`, 미고정)는 페이지에서 쓰는 곳이 0곳이라 제거(업로드는 서버 API 경유).

## 1. 요소 id

| id | 결과 |
|---|---|
| `account-faq-link` | ✅ |
| `cancel-reason-modal` | ✅ |
| `cancel-reason-options` | ✅ |
| `close-mobile-nav` | ✅ |
| `confirm-cancel-btn` | ✅ |
| `confirm-ok-btn` | ✅ |
| `consultant-modal` | ✅ |
| `consultant-tabs` | ✅ |
| `contract-modal` | ✅ |
| `contract-preview` | ✅ |
| `contract-project-id` | ✅ |
| `contract-sign-btn` | ✅ |
| `contract-signature-status` | ✅ |
| `counter-duration` | ✅ |
| `counter-message` | ✅ |
| `counter-offer-modal` | ✅ |
| `counter-price` | ✅ |
| `counter-project-id` | ✅ |
| `detail-action-btn` | ✅ |
| `detail-panel-title` | ✅ |
| `direct-reason-container` | ✅ |
| `direct-reason-input` | ✅ |
| `empty-state` | ✅ |
| `message-input` | ✅ |
| `message-list` | ✅ |
| `message-modal` | ✅ |
| `message-modal-title` | ✅ |
| `mobile-menu-btn` | ✅ |
| `mobile-nav` | ✅ |
| `mobile-user-name` | ✅ |
| `modal-body` | ✅ |
| `nego-current-duration` | ✅ |
| `nego-current-price` | ✅ |
| `nego-message` | ✅ |
| `nego-project-id` | ✅ |
| `nego-requested-duration` | ✅ |
| `nego-requested-price` | ✅ |
| `negotiation-modal` | ✅ |
| `notification-badge` | ✅ |
| `notification-btn` | ✅ |
| `notification-list` | ✅ |
| `notification-panel` | ✅ |
| `profile-avatar` | ✅ |
| `profile-bio` | ✅ |
| `profile-form` | ✅ |
| `profile-image-file` | ✅ |
| `profile-image-url` | ✅ |
| `profile-regions` | ✅ |
| `profile-specialty` | ✅ |
| `profile-tab-content` | ✅ |
| `profile-video` | ✅ |
| `project-detail-body` | ✅ |
| `project-detail-footer` | ✅ |
| `project-detail-overlay` | ✅ |
| `project-detail-panel` | ✅ |
| `project-list` | ✅ |
| `proposal-duration` | ✅ |
| `proposal-file` | ✅ |
| `proposal-file-info` | ✅ |
| `proposal-form` | ✅ |
| `proposal-message` | ✅ |
| `proposal-modal` | ✅ |
| `proposal-price` | ✅ |
| `proposal-project-id` | ✅ |
| `requests-tab-content` | ✅ |
| `review-comment` | ✅ |
| `review-modal` | ✅ |
| `review-modal-error` | ✅ |
| `review-modal-subtitle` | ✅ |
| `review-modal-title` | ✅ |
| `review-rating-label` | ✅ |
| `review-stars` | ✅ |
| `review-submit-btn` | ✅ |
| `schedule-date-${m.id}` | ✅ |
| `schedule-form-body` | ✅ |
| `schedule-modal` | ✅ |
| `schedule-review-body` | ✅ |
| `schedule-review-modal` | ✅ |
| `theme-toggle` | ⚠️ 테마 토글 버튼 — B안 라이트 전용이라 버튼과 이를 읽던 스크립트를 함께 제거 |
| `theme-toggle-dark-icon` | ⚠️ 위와 같음 |
| `theme-toggle-light-icon` | ⚠️ 위와 같음 |
| `user-name` | ✅ |
| `verified-industries` | ✅ |
| `verified-iso-experience` | ✅ |
| `verified-name` | ✅ |
| `verified-projects` | ✅ |
| `verified-qualifications` | ✅ |
| `verified-trust-score` | ✅ |
| `withdraw-consultant-note` | ✅ |
| `withdraw-error` | ✅ |
| `withdraw-modal` | ✅ |
| `withdraw-password` | ✅ |
| `withdraw-reason` | ✅ |
| `withdraw-submit-btn` | ✅ |
| `schedule-date-${m.id}` (템플릿) | ✅ |
| `custom-confirm-modal` (스크립트가 생성) | ✅ id·`confirm-cancel-btn`·`confirm-ok-btn` 유지, 인라인 cssText 대신 `.hm-confirm-overlay` 클래스 |

## 2. querySelector 선택자 원문

- [x] `#empty-state .btn-primary` — 빈 상태 CTA 도 `btn-primary` 유지
- [x] `#empty-state h3` 
- [x] `#empty-state p` 
- [x] `.consultant-tabs .tab-btn` 
- [x] `.dashboard-header .btn-primary` — 헤더 CTA 는 `hm-btn hm-btn-lime btn-primary` (스크립트가 `style.display` 를 바꿈)
- [x] `.dashboard-header h1` 
- [x] `.dashboard-header p` — 첫 `<p>` 가 설명문이어야 한다 → 위에 붙인 `DASHBOARD` 꼬리표는 `<span>` 으로 둠
- [x] `.notification` — brand.css 7절 다크 토스트와 같은 이름 — 그대로 씀
- [x] `.notification-dropdown` — 바깥 클릭 닫기 판정용 래퍼 유지
- [x] `input[name="cancel_reason"]:checked` 

## 3. name · data 속성

- [x] `name="cancel_reason"`
- [x] `name="viewport"`
- [x] `data-session-id`
- [x] `data-tab`
- [x] `data-title`

## 4. 스크립트가 붙이고 떼는 상태 클래스

- [x] `active` — `.consultant-tabs .tab-btn.active` (15-3)
- [x] `dark` — head 스크립트가 `<html>` 에서 항상 제거(라이트 전용). 토글 스크립트는 삭제
- [x] `hidden` — brand.css `.hidden{display:none!important}` 가 처리(모달·패널·뱃지·탭)
- [x] `opacity-0` — `#mobile-nav` 닫힘 계약 — brand.css 11절 `.hm-mnav.hm-mnav-fade`
- [x] `open` — `.project-detail-panel.open` / `-overlay.open` (15-8) — `right` 이동 대신 transform
- [x] `pointer-events-none` — 위와 같음

## 5. 인라인 핸들러가 부르는 함수

`addConsultant`, `cancelConsultantRequest`, `closeCancelReasonModal`, `closeContractModal`, `closeCounterOfferModal`, `closeMessageModal`, `closeMobileMenu`, `closeModal`, `closeNegotiationModal`, `closeProjectDetailPanel`, `closeProposalModal`, `closeReviewModal`, `closeScheduleModal`, `closeScheduleReviewModal`, `closeWithdrawModal`, `completeProject`, `confirmCancelWithReason`, `confirmSchedule`, `deleteQuoteRequestGroup`, `downloadProposal`, `enterProjectRoom`, `formatNumberWithComma`, `handleNotificationClick`, `handleProfileImageUpload`, `handleProposalFileChange`, `loadProfile`, `logout`, `markAllNotificationsRead`, `openAddConsultantModal`, `openContractModal`, `openCounterOfferModal`, `openMessageModal`, `openNegotiationModal`, `openProjectDetailPanel`, `openProposalFromDetail`, `openProposalModal`, `openReviewModal`, `openScheduleModal`, `openScheduleReviewModal`, `openWithdrawModal`, `rejectRequest`, `rejectSchedule`, `respondNegotiation`, `saveProfile`, `selectConsultant`, `sendMessage`, `setReviewRating`, `signContract`, `submitCounterOfferFromModal`, `submitNegotiation`, `submitProposal`, `submitReview`, `submitSchedule`, `submitWithdrawal`, `switchConsultantTab`, `toggleNotificationDropdown`, `viewConsultantProfile`, `viewMyProposal`, `viewProjectProgress`

- [x] 전부 같은 이름·같은 인자로 호출된다(핸들러 속성 수 97 → 97).

## 6. 모달 · 패널 (열기/닫기 실측)

- [x] `cancel-reason-modal`
- [x] `consultant-modal`
- [x] `contract-modal`
- [x] `counter-offer-modal`
- [x] `message-modal`
- [x] `negotiation-modal`
- [x] `proposal-modal`
- [x] `review-modal`
- [x] `schedule-modal`
- [x] `schedule-review-modal`
- [x] `withdraw-modal`
- [x] `project-detail-panel (+ project-detail-overlay)`
- [x] `notification-panel`
- [x] `mobile-nav`
- [x] `custom-confirm-modal`

## 7. 스크립트가 그리는 HTML (템플릿 문자열)

| 템플릿 | 처리 |
|---|---|
| fetchProjects 오류 카드 | `.card.hm-dash-error` + `.hm-state-icon` |
| loadProfile (프로필 없음·ISO/산업 칩·아바타) | `.hm-chip-tag`, `.hm-kv-empty`. 신뢰 점수 없음 표기 `-` → `—` |
| renderConsultantProjects (전문가 카드) | 안내 상자 7종 → `.hm-note` (is-error/is-ok/is-warn/is-info), 본문 `.hm-card-body`, 버튼 줄 `.hm-card-actions`. **원본의 닫히지 않은 `<div>`(조건 협의 상자)를 닫음** — 예전엔 아래 안내·버튼 줄까지 호박색 상자 안에 들어갔다 |
| renderGroupedProjects (기업 카드) | 템플릿 안 `<style>` 블록 삭제(취소 색은 15-4 `.status-cancelled_by_company`), `.project-actions` 인라인 스타일 → 클래스 |
| generateStageIndicator | 변경 없음 — 15-4 가 알약 모양으로 그림 |
| generateConsultantResponseCard | 별 아이콘 인라인 색 제거, 메시지 행 `.is-stack`, 협의 대기 칩 `.hm-note.is-warn.is-inline` |
| viewConsultantProfile (#modal-body) | `.hm-profile-head`·`.hm-stat-grid`·`.hm-chip-tag`. **평점 없으면 `—`(예전엔 지어낸 5.0), 경력 없으면 `—`(예전엔 지어낸 10년)** — 평점은 리뷰 1건 이상일 때만 숫자 |
| openAddConsultantModal (#modal-body) | `.add-consultant-item`·`.hm-modal-intro`. 별 이모지 → "평점", 빈 값 `-` → `—` |
| openContractModal (서명 상태) | `.hm-sign-grid`/`.hm-sign.is-done` (조건 분기 동일) |
| showNotification | 인라인 cssText(초록·빨강·파랑 배경) → `className = notification notification-<type>` (brand.css 다크 토스트) |
| showConfirmModal | 인라인 다크 카드(`#1a1a2e` 폴백) → `.hm-confirm-overlay` / `.hm-confirm`, 확인 버튼 `.btn-danger` |
| openProjectDetailPanel / renderProjectDetails | 로딩·오류 `.hm-state`, 취소 섹션 `.detail-section.is-cancelled`, 이슈·사유 태그 `.detail-tag.is-issue/.is-reason`, 위험도 색 `.is-risk-*` |
| updateDetailActionButton | 변경 없음 (.btn 계열) |
| renderReviewStars | 인라인 색 → `<i class="hm-star is-on">` (lucide 가 클래스를 svg 로 복사), `aria-pressed` 추가 |
| loadMessages | 빈 상태·오류 `.hm-chat-empty` (말풍선 클래스 그대로) |
| renderNotifications | 변경 없음 — `.notification-item .message` 가 brand.css 7절 `.message`(폼 메시지 칸, 기본 숨김)와 이름이 겹쳐 15-9 에서 되돌림 |
| deleteQuoteRequestGroup·submitWithdrawal·handleProposalFileChange | 변경 없음 |

## 8. 의도적으로 손댄 스크립트 (로직 아님)

- 테마 토글 블록 삭제 → 주석으로 대체. `localStorage.theme` 를 읽지도 쓰지도 않는다(다른 B안 페이지와 같은 처리).
- 위 7절의 마크업 문자열 · `showNotification` 의 스타일 지정 · `showConfirmModal` 의 스타일 지정.
- 그 밖의 흐름(fetch·상태 분기·confirm/prompt·이벤트)은 바꾸지 않았다.

## 9. 이식 중 발견한 기존 문제 (이번에 고치지 않음)

- 컨설턴트 "내 프로필" 탭이 항상 "컨설턴트 프로필을 찾을 수 없습니다" 로 끝난다: `GET /api/consultants`(공개용 dict)에 `user_id` 가 없어 `consultants.find(c => c.user_id === user.id)` 가 실패. 로컬 테스트에서는 응답에 user_id 를 끼워 넣어 폼 모양만 확인했다(백엔드 수정 필요).
- 기업 "새로운 전문가 추천받기" 목록: `available-consultants` 가 `profileImageUrl` 이 없으면 `avatar`(이니셜 1글자)를 이미지 주소로 내려줘 깨진 이미지 + 404 가 난다(HEAD 와 동일). 추천이 부족할 때 백엔드가 `matchScore: 60` 을 고정값으로 붙이는 것도 그대로다.
- 전문가 카드의 "요청 사항" 은 `/api/projects` 목록이 description 을 내려주지 않아 항상 기본 문구가 나온다. 협의 요청 내용(negotiation_data)도 목록에 없어 전문가 카드에서 보이지 않는다.
- 확인창(showConfirmModal) 확인 버튼 문구가 항상 "삭제" — 프로젝트 "완료 확인" 에서도 "삭제" 로 보인다(문구라 손대지 않음).
- 응답 카드의 인증 배지(badge-check)는 verified 여부와 관계없이 모든 전문가에게 붙는다(HEAD 와 동일).
- loadProfile 의 산업군 칩 `${ind}` 는 escape 없이 들어간다(HEAD 와 동일, 이식 범위 밖).

## 10. 확인 결과 (로컬 오프라인 서버, 127.0.0.1:5000)

- 기업·전문가 × 1440 / 375: 페이지 오류 0, 콘솔 오류 0(위 avatar 404 1건 제외 — HEAD 도 같음), 가로 스크롤 없음, Tailwind/lucide@latest/style.css 요청 0, `dark` 클래스 없음(localStorage.theme=dark 를 심어도), `<i data-lucide>` 미렌더 0.
- 모든 모달·상세 패널·알림 패널·모바일 메뉴·탭 열기/닫기, 별점 선택, 취소 사유 "직접 입력" 토글, 로그아웃 동작 확인.
- HEAD 와 같은 역할·폭에서 id 목록(테마 토글 3개 제외)·핸들러 목록·목록 버튼·목록 본문 글자 동일. 다른 점: 375 에서 현재 단계 이름이 보임, 신뢰 점수 `-` → `—`.
- 1024 / 768 / 320 도 가로 스크롤 없음, 알림 패널 화면 안.
- 관리자: dashboard.html → admin.html 로 넘어감. 로그아웃 상태: login.html 로 넘어감.
- 이미 B안인 13개 페이지(로그인 묶음 5·컨설턴트 3·콘텐츠 5)를 HEAD brand.css 와 새 brand.css 로 각각 찍어 비교: 1440·375 모두 픽셀 동일.
