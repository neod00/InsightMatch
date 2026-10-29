# admin.html B안 이식 — 화면 요소 의존성 점검표

기준: main `ba25e37` 의 `admin.html`(HEAD, 3,071줄) → 브랜치 `design/b-momentum-admin`. 인벤토리는 스크립트로 뽑았다(정적 마크업 + 인라인 `<script>` + 템플릿 문자열).
표시: ✅ 이름 그대로 유지 · ⚠️ 의도적으로 바꿈(사유 기재).
같은 파일 안 스크립트가 쓰는 계약이라 **id·name·data-*·탭 onclick 글자는 하나도 바꾸지 않았다.** 이번 변경에는 사용자 승인을 받은 저장형 XSS 수정과 기존 버그 4건(B~E) 수정이 함께 들어 있다(9절).

## 요약

- 요소 id: HEAD 97개(템플릿이 만드는 id 포함) 모두 유지. 새로 생긴 id는 접근성용 3개(`main`, `invite-title`, `reject-panel-title`)뿐이다.
- 탭: `.admin-tab` 6개의 `onclick="switchTab('…')"` 글자가 HEAD와 바이트 단위로 같다. `switchTab` 이 `.admin-tab[onclick="switchTab('x')"]` 로 탭을 찾기 때문이다.
- `#mobile-menu-btn` / `#mobile-nav` 유지. 스크립트가 시작할 때 바로 click 을 거므로, 이 id가 없으면 데이터가 하나도 뜨지 않는다. 열림 계약(`.active`)은 brand.css 11절 `.hm-menu-btn.active` / `.hm-mnav.active` 가 처리한다.
- 인라인 핸들러가 부르는 함수 31종 모두 유지. 새로 생긴 함수는 `logout()`(헤더·모바일 메뉴 로그아웃, 예전 화면에는 로그아웃이 없었다)과 보안용 도우미 `jsArg()`·`safeHttpUrl()` 이다.
- 외부 리소스: `style.css?v=4`·`lucide@latest`·Plus Jakarta/Google "Pretendard" 를 제거했다. 대신 Pretendard(jsdelivr) + DM Sans, `lucide@0.460.0` 고정, `brand.css?v=5` 를 쓴다. 라이트 전용(`<html>` 의 `.dark` 를 항상 걷어 낸다).
- 스타일: `<body class="hm-body hm-app hm-admin">`. 15절(앱 셸: `.btn` 계열·`.card`·상태 알약·`detail-*`·`.hm-note`·`.hm-stat`·토스트)을 그대로 쓰고, 관리자에만 있는 부품은 brand.css **16절(ADMIN, 전부 `.hm-admin` 범위)** 에 새로 넣었다. 1~15절 규칙은 한 줄도 바꾸지 않았다(diff 의 삭제 줄 0). 인라인 `<style>` 블록 2개는 지웠다.

## 1. 요소 id

| id | 결과 |
|---|---|
| `mobile-menu-btn`, `mobile-nav` | ✅ (B안 `.hm-menu-btn` / `.hm-mnav`, `.active` 계약 그대로) |
| `analysis-count` `consultant-count` `blog-count` `funnel-count` `inquiry-count` `error-count` | ✅ 탭 배지(`.badge-count`). 불러오기 전 값 `-` → `—` |
| `analysis-tab` `consultants-tab` `blog-tab` `funnel-tab` `inquiries-tab` `errors-tab` | ✅ 패널 6개. 숨김은 여전히 인라인 `style="display:none"` 이고 스크립트가 block/none 으로 바꾼다. CSS 는 display 를 `!important` 로 강제하지 않는다 |
| `stats-cards` | ✅ `.hm-kpi-grid`(display:grid). 스크립트가 `grid`/`none` 으로 바꾸는 계약과 같다 |
| `stat-total` `stat-pending` `stat-completed` | ✅ (`stat-pending` 은 9절 E로 값이 고쳐졌다) |
| `job-list`, `job-card-<id>`, `detail-<id>`, `chevron-<id>` | ✅ 템플릿 id. 같은 값을 `escapeAttr` 를 거쳐 넣는다 |
| `consultant-stat-total` `-pending` `-verified` | ✅ |
| `invite-name` `invite-email` `invite-memo` `btn-create-invite` `invite-list` | ✅ (`hm-field` 라벨에 `for` 연결) |
| `filter-count-all` `-pending` `-verified` `-rejected` | ✅ |
| `consultant-list-admin` | ✅ 부모는 여전히 `#consultants-tab` 이다. 스크립트가 바로 앞에 컨트롤 줄을 끼워 넣는다 |
| `consultant-list-controls` `consultant-search-input` `consultant-sort-select` `consultant-pagination` | ✅ 스크립트가 만든다. 인라인 cssText → `.hm-list-controls` / `.hm-input` / `.hm-select` / `.hm-pager` |
| `blog-stat-total` `blog-stat-recent` `blog-editor` `editor-title` `blog-edit-id` `blog-input-title` `-author` `-tags` `-image` `-content` `blog-save-btn-text` `blog-list-admin` | ✅ |
| `funnel-days` `funnel-overall` `funnel-survey-list` `funnel-project-list` `funnel-side-negotiation` `-cancelled` `-result` `funnel-limitations` | ✅ |
| `inquiry-stat-received` `-checked` `-done` `inquiry-status-filter` `inquiry-list` `inquiry-pagination` `inquiry-memo-<id>` | ✅ |
| `error-stat-total` `-groups` `-health` `-email` `-email-note` `-cron` `-cron-note` `error-hours` `error-filter-note` `error-group-list` `error-log-list` `error-pagination` `error-detail-<id>` | ✅ (스크립트가 `style.color` 를 바꾸는 3개 값은 색 변수가 모두 정의돼 있다) |
| `side-panel-overlay` `side-panel` `panel-company-name` `panel-request-date` `panel-content` `panel-footer` | ✅ |
| `reject-panel-overlay` `reject-panel` `reject-consultant-id` `reject-consultant-name` `reject-reasons` `custom-reason-radio` `reject-custom-reason` `confirm-reject-btn` | ✅ |
| `main`, `invite-title`, `reject-panel-title` | ⚠️ 추가(건너뛰기 링크·`aria-labelledby` 용) |

## 2. 탭 · 필터 · 선택자 원문

- [x] `.admin-tab` ×6 + `onclick="switchTab('analysis'|'consultants'|'blog'|'funnel'|'inquiries'|'errors')"`: HEAD와 글자가 같다. `type="button"` 만 더했다.
- [x] `.admin-tab.active`: 처음에는 analysis 탭에 붙어 있다. 16-2 에서 잉크 배경으로, 배지는 라임으로 그린다.
- [x] `.consultant-filter-btn[data-filter="all|pending|verified|rejected"]` + `onclick="filterConsultants('…')"`. 선택 상태는 **`.active` 이면서 `.btn-ghost` 가 없는** 조합이다(16-4 `.btn.consultant-filter-btn.active`).
- [x] querySelector 원문 9개(`.admin-tab`, `.admin-tab[onclick=…]`, `.consultant-filter-btn`, `.consultant-filter-btn[data-filter=…]`, `.detail-view`, `[id^="chevron-"]`, `.notification`, `input[name="reject_reason"]`, `input[name="reject_reason"]:checked`)가 모두 그대로 맞는다.
- [x] `name="reject_reason"` 6개의 value 문자열이 HEAD와 같다. 이 값은 서버로 가고 메일에도 들어간다. `value="custom"` 도 그대로다. 라디오는 눈에만 숨겼다(예전 `display:none` → 키보드 초점 유지).

## 3. 스크립트가 붙이고 떼는 상태 클래스 / 인라인 스타일

- [x] `active`: `.admin-tab`, `.consultant-filter-btn`(+`btn-ghost`), `.detail-view`(16-5 기본 숨김, `.active` 일 때 펼침), `.side-panel`/`.side-panel-overlay`(16-9), `#mobile-menu-btn`/`#mobile-nav`(11절).
- [x] `style.display`: 탭 패널 6개, `#stats-cards`(grid), `#blog-editor`, `#error-detail-<id>`. CSS 가 display 를 덮어쓰지 않는다.
- [x] `chevron.style.transform`(펼침 화살표 회전), `card.style.*`(삭제 애니메이션), `body.style.overflow`: 그대로 둔다.
- [x] 거부·검증취소 카드: 예전 `card.style.opacity/borderColor` → `card.classList.add('is-rejected')`. 같은 모양(흐림 + 빨간 테두리)을 16-6 이 그린다.
- [x] `.notification`: 9절 B 참고.

## 4. 인라인 핸들러가 부르는 함수

`approveConsultant`, `closeMobileMenu`, `closeRejectPanel`, `closeSidePanel`, `confirmReject`, `copyInvite`, `createInvite`, `deleteBlogPost`, `deleteJob`, `editBlogPost`, `filterConsultants`, `filterErrorsByFingerprint`, `hideBlogEditor`, `insertBlogTag`, `loadErrorLogs`, `loadFunnelStats`, `loadInquiries`, `loadInvites`, `refreshData`, `rejectConsultant`, `renderConsultantsAdmin`, `restoreConsultant`, `revokeInvite`, `revokeVerification`, `runCronNow`, `saveBlogPost`, `showBlogEditor`, `switchTab`, `toggleDetail`, `toggleErrorDetail`, `updateInquiry`, `viewConsultantDetails`, `viewJobDetails` (+ 추가: `logout`)

- [x] 모두 같은 이름으로 호출된다. 인자가 바뀐 곳은 9절 A에 적었다(사람이 쓴 이름은 넘기지 않고 id 만 넘긴다).
- [x] `showBlogEditor()` 타일은 `div` → `button`(같은 onclick, 키보드로 누를 수 있게).

## 5. 패널 · 메뉴 (열기/닫기 실측)

- [x] `side-panel`: 업체 상세(`viewJobDetails`)와 컨설턴트 상세(`viewConsultantDetails` + 심사 이력). X 버튼과 배경 클릭으로 닫힌다. 업체 패널 안의 "프로필" 버튼이 컨설턴트 상세로 이어진다.
- [x] `reject-panel`: 사유 선택, 직접 입력, 확인 중 스피너, 닫기.
- [x] `mobile-nav`: 375px 에서 열고 닫힌다. `aria-expanded` 도 함께 바뀐다.
- [x] `blog-editor`: 열기, HTML 넣기 버튼, 취소. 열 때 헤더 밑으로 가려지지 않게 `scroll-margin-top` 을 줬다.
- [x] 네이티브 `confirm`/`prompt`/`alert` 는 그대로 쓴다(바꾸지 않음).

## 6. 스크립트가 그리는 HTML (템플릿 문자열)

| 템플릿 | 처리 |
|---|---|
| loadInvites (표) | 인라인 `<table>` → `.hm-table-wrap > .hm-table`. 상태 글자색(#10B981 등) → `.hm-pill.is-ok/is-error` |
| loadInquiries / renderInquiries / 페이지 | `.hm-inquiry*`, 상태 알약 `.hm-pill` (`INQUIRY_STATUS_COLORS` → `INQUIRY_STATUS_TONES`), 페이지 `.hm-pager-label` |
| loadFunnelStats / renderFunnelStages | 막대 `.hm-funnel-track/.hm-funnel-fill(.is-drop)`. 폭만 인라인 width 로 남겼다. 전환율 색 → `.hm-funnel-rate.is-ok/is-warn/is-error`. ⚠️ 이모지 → lucide `alert-triangle`. 빈 값 `-` → `—` |
| runCronNow / confirmReject 로딩 | 스피너 인라인 크기 → `.hm-app .btn .loading-spinner` |
| loadErrorLogs / 그룹 표 / 기록 / 상세 | `.hm-table`(선택 행 `.is-selected`), `.hm-errlog*`, 스택 트레이스는 다크 코드 블록 `.hm-pre`. 👍 이모지 제거 |
| fetchJobs / renderJobs | `.job-card/.job-header/.job-info/.job-meta/.detail-view` 이름 그대로 16-5 에서 그린다. 상태 알약은 `status-<프로젝트 상태>`(15절 색) + `status-failed/deleted/archived`(16절). 규격 묶음 `.hm-std-*`, 요청 정보 `.hm-detail-list .detail-item`, 삭제 버튼 `.btn-danger-soft`. "• 희망 규격:" → "희망 규격" 라벨 |
| renderConsultantsAdmin / 컨트롤 / 페이지 | `consultant-approval-*` 이름 그대로 16-6 에서 그린다. 승인 = 라임, 거부 = 빨간 연면, 복구 = 테두리 버튼. **신뢰 점수가 없으면 `—`(예전엔 지어낸 50점), 리뷰 수가 없으면 `—`** |
| loadConsultantHistory | `.detail-section` + `.hm-history-*`(예전 다크용 rgba 테두리 제거) |
| viewConsultantDetails | `.hm-panel-profile` + 15절 `.detail-section/.detail-grid/.detail-item/.detail-tag/.detail-description/.hm-stat-grid`. 이모지(📧📞🏢💬⭐✓⏳)는 아이콘이나 글자로 바꿨다. **신뢰 점수·평점·리뷰가 없으면 `—`(예전엔 50 / 0 / 0개)** |
| viewJobDetails | 같은 15절 부품. 컨설턴트 행 `.consultant-item` + `.hm-ci-*`, 상태 알약 `status-<상태>`(예전엔 모든 상태가 `status-completed`). "제안서" 버튼은 예전처럼 동작이 없다(아래 10절) |
| showNotification | 9절 B |
| fetchBlogPostsAdmin / renderBlogListAdmin | `job-card` 재사용 → `.hm-blog-row`(썸네일·제목·발췌·태그·작성자·날짜·아이콘 버튼 3개) |
| showBlogEditor 제목 | 아이콘 인라인 색(`--accent` 라임 글자색 = 대비 미달) 제거 |
| insertBlogTag | 변경 없음. 글 본문에 들어가는 내용이라 디자인 이식 범위 밖이다 |

## 7. 반응형

- 탭 6개: 767px 이하에서 화면 끝까지 가로로 밀어 볼 수 있다(페이지 자체 가로 스크롤 없음).
- KPI 타일: 데스크톱은 auto-fit, 767px 이하는 2열이고 홀수 마지막 타일은 한 줄을 다 쓴다.
- 초대 폼: 4열 → 1023px 이하 2열 → 767px 이하 1열.
- 표: `.hm-table-wrap` 안에서만 가로 스크롤.
- 서랍: 600/500px → 767px 이하 전체 폭.
- 1440 / 1024 / 768 / 375 / 320 모든 탭과 상세 패널에서 `scrollWidth == viewport` 를 확인했다.

## 8. 의도적으로 손댄 스크립트 (모양 외)

- 모바일 메뉴 `aria-expanded` 갱신, `logout()` 추가.
- 9절의 보안 수정과 버그 수정.
- 그 밖의 흐름(fetch·조건 분기·confirm/prompt·이벤트·지연 로딩)은 바꾸지 않았다.

## 9. 이번에 고친 것 (사용자 승인)

### A. 저장형 XSS: 사용자가 쓴 값을 전부 이스케이프

원칙: HTML 에 넣는 값은 `escapeHtml`, 속성은 `escapeAttr`, 이미지 주소는 `safeImageUrl`/`safeHttpUrl`, mailto 는 `safeMailto` 를 거친다. 인라인 핸들러에는 **사람이 쓴 이름을 넘기지 않는다**: 숫자 id 는 `Number()`, 문자열 id 는 `jsArg()`(JSON 문자열 리터럴로 만든 뒤 속성용 이스케이프)로 넘기고, 이름은 함수 안에서 불러온 데이터로 찾는다.

| 위치 | 예전 | 지금 |
|---|---|---|
| renderJobs 카드 제목 `job.company_name` | 그대로 | `escapeHtml` |
| renderJobs 삭제 `deleteJob('${job.id}','${job.company_name}')` | 이름이 JS 문자열 안으로 들어감 → 따옴표로 탈출 | `deleteJob(jsArg(id))`, 이름은 `allJobsAdmin` 에서 찾는다 |
| renderJobs `toggleDetail('${job.id}')`, `viewJobDetails('${job.id}')`, `id="detail-…"`/`chevron-…` | 그대로 | `jsArg` / `escapeAttr` |
| renderJobs 규격 코드 `std`, 컨설턴트 이름·첫 글자 `p.consultant_name`, 상태 글자 | 그대로 | `escapeHtml` |
| renderJobs 요청 정보 `standardsText`·`industry`(본문과 메타)·`employees`·`timeline`, 날짜 | 그대로 | `escapeHtml` |
| renderJobs·viewJobDetails `mailto:${intake.contact_email}` | 그대로 | `safeMailto` + `escapeAttr` |
| viewJobDetails 원문 6칸 + `issues` | 그대로 | `escapeHtml` |
| viewJobDetails 컨설턴트 `p.profile_image_url`(img src), `p.consultant_name`, `p.status`, `p.title` | 그대로 | `safeImageUrl`+`escapeAttr`, `escapeHtml` |
| viewJobDetails `viewConsultantDetails('${p.consultant_id}')` | 그대로 | `Number()` |
| viewJobDetails 푸터 삭제 `deleteJob('…','${job.company_name}')` | 이름이 JS 문자열 안 | `deleteJob(jsArg(id))` |
| renderConsultantsAdmin / viewConsultantDetails 버튼 `('${consultant.id}')`, `${consultant.id}` | 따옴표 문자열 / 그대로 | `Number()` |
| renderConsultantsAdmin 신뢰 점수·거부일 | 그대로 | `escapeHtml` |
| viewConsultantDetails `renderTags` 빈 문구, 신뢰 점수·평점 | 그대로 | `escapeHtml` |
| loadInvites `copyInvite('${escapeAttr(url)}')`, `revokeInvite(${i.id})`, 모르는 상태값 | escapeAttr 만(엔티티가 JS 전에 풀린다) / 그대로 | `jsArg(url)`, `Number(id)`, `escapeHtml` |
| renderErrorGroups `filterErrorsByFingerprint('${escapeAttr(fp)}')` | 같은 패턴 | `jsArg(fp)` |
| 블로그 목록 `post.title`, 발췌, `post.author`, `post.created_at`, 태그 `t` | 그대로 | `escapeHtml` |
| 블로그 목록 `<img src="${post.image_url}">` | 그대로 | `safeHttpUrl`(http/https/상대경로만) + `escapeAttr` |
| 블로그 목록 `deleteBlogPost(id,'${title.replace(/'/g,"\\'")}')` | `"`·`\` 로 탈출 가능 | `deleteBlogPost(Number(id))`, 제목은 `allBlogPosts` 에서 찾는다 |
| 페이지 넘김 숫자, 에러 상세·이력·삭제 URL 의 id | 그대로 | `Number()` / `encodeURIComponent` |

이미 안전했던 곳(문의, 초대 이름·메모, 에러 글자, 퍼널, cron 메모는 `textContent`, 컨설턴트 카드·패널의 글자 칸)은 그대로 이스케이프를 유지한다.

### B. 토스트가 쌓이고 클래스가 없음
`showNotification` 이 `className = 'notification notification-success|error'` 를 붙인다(brand.css 7절·12절 다크 토스트). 이제 `querySelector('.notification')` 가 기존 토스트를 찾아 지우므로 한 번에 하나만 뜬다. 4초 뒤 사라지고, 타이머는 새 토스트가 뜰 때 초기화한다. `role="status|alert"` 도 붙인다.

### C. `approveConsultant` / `revokeVerification` 이 두 번씩 선언됨
같은 스크립트 안에서는 뒤쪽 함수 선언이 앞쪽을 덮어쓰므로 실제로 돈 것은 뒤쪽이었다. 뒤쪽은 승인 체크리스트를 보내고, 검증 취소는 `prompt` 로 사유를 받는다. 서버(`validate_approval_checklist`, `get_required_reason`)가 요구하는 형태도 뒤쪽이다. 그래서 체크리스트·사유 없이 요청하던 **앞쪽 정의 2개를 지웠다**. 동작은 바뀌지 않는다.

### D. "이번 달 작성" 이 1년치를 셈
`startsWith(thisMonth.substring(0, 4))`(연도) → `startsWith(thisMonth)`(UTC `YYYY-MM`). 서버의 `created_at` 이 UTC 날짜라 같은 기준으로 비교한다.

### E. 매칭 요청 "대기 중" 숫자와 탭 배지가 항상 0
`/api/admin/jobs` 의 `status` 는 회사별 프로젝트 중 가장 앞선 **프로젝트 상태**다(`planning … completed`, 모두 취소면 `archived`). 예전 코드는 이 API가 절대 주지 않는 분석 작업 시절 값 `processing` 을 셌다. 지금은 계약 전 상태(`planning`, `proposal_pending`, `proposal_submitted`, `negotiating`, `pending_contract`, `awaiting_signature`, 호환용 `processing`)를 센다. 타일 아래에 "계약 전 단계(견적·협의·서명 대기)" 라고 적었다. 프런트만 고쳤다(백엔드 변경 없음).
참고: 컨설턴트 "승인 대기" 숫자는 이미 맞게 세고 있었다. 서버가 `status='pending'` 을 주고, 화면 숫자와 DB 숫자가 같은 것을 확인했다.

## 10. 이식 중 발견한 기존 문제 (이번에 고치지 않음)

- **검증취소(`revoked`) 카드**에 "승인/거부" 버튼이 뜬다. 상태 분기가 `rejected`·`verified` 만 따로 보기 때문이다(HEAD와 같다). 거부 사유 칸도 `rejected` 에만 나온다.
- 컨설턴트 상세 패널의 상태 알약은 `verified` 여부만 본다. 그래서 거부·검증취소도 "승인 대기" 로 나오고, 푸터에 "검증 승인하기" 가 뜬다(HEAD와 같다).
- 업체 상세 패널의 "제안서" 버튼에는 동작이 없다(HEAD와 같다).
- `/api/admin/jobs` 가 업종·직원 수·일정·예산·지역·이슈를 늘 `null` 로 줘서 "미지정" 등 기본 문구만 보인다.
- 매칭 요청 "삭제" 는 서버에서 보관 처리(프로젝트 → `cancelled_by_company`)다. 그래서 목록을 다시 불러오면 같은 업체가 "보관됨" 으로 다시 나온다(HEAD와 같다).
- 상태가 `withdrawn`(탈퇴)인 컨설턴트는 필터 숫자 어디에도 들어가지 않는다.
- `insertBlogTag('tip')` 이 글 본문에 넣는 팁 상자는 예전 초록(#10b981) 인라인 스타일이다. 본문 콘텐츠라 이식 범위 밖이다.
- authFetch 가 401 일 때 `token` 만 지우고 `user` 는 남긴다. 그래서 dashboard ⇄ admin 이 오가며 이동하는 루프가 생길 수 있다(HEAD와 같다).

## 11. 확인 결과 (로컬 오프라인 서버 127.0.0.1:5000, 버릴 수 있는 로컬 DB)

- 테스트 데이터에 HTML/JS 문자열을 넣은 행을 추가했다: 업체 `<img src=x onerror=alert('xss-company')>`, 업체 `O'Brien & "Sons" \ Co`, 컨설턴트 2명(태그·따옴표), 프로젝트 제목, 블로그 제목·태그·작성자·`javascript:` 이미지, 문의, 에러 메시지.
  - HEAD: alert 5종 실행(xss-company·xss-consultant·xss-title·xss-blog·tag), `<img src=x>` 5개 생성, 삭제 버튼에서 페이지 오류 5건(`missing ) after argument list` 등), 토스트 0개(클래스 없음)
  - 이후: alert 0, 주입된 img 0, 페이지·콘솔 오류 0. 해당 행의 삭제·상세·승인·거부·편집 버튼이 모두 동작하고, 확인창에는 원래 이름이 글자 그대로 나온다.
- 1440 / 375 읽기 전용 점검 167/167 통과: 탭 6개 전환과 패널 단독 표시, 가로 스크롤 없음, style.css·lucide@latest 요청 0, Pretendard 적용, 상세·거부 패널 열기/닫기(X·배경), 모바일 메뉴, 토스트 1개만 떴다가 사라짐, 필터 4종, 검색, 블로그 편집기, 에러 그룹 필터·상세, lucide 미렌더 0.
- 실제 동작 점검 25/25 통과(로컬 DB만): 승인(체크리스트) · 거부(사유 + 메모가 서버에 `사유 - 메모` 로 저장) · 검증취소(prompt 사유) · 복구 · 초대 발급/복사/취소 · 매칭 요청 삭제(보관) · 블로그 삭제/작성/수정 · 문의 상태 + 메모 · cron 실행 · 로그아웃.
- 숫자: "대기 중" HEAD 0 → 2(DB 계약 전 업체 2곳), 탭 배지 동일, "이번 달 작성" HEAD 3(올해) → 2(이번 달, DB와 같다), 승인 대기 = DB pending 수.
- HEAD와 같은 데이터로 비교: id 누락 0, 탭·필터·목록 글자 누락 없음. 바뀐 것은 문장 부호(`필터:` → `필터`), 이모지, 지어낸 값 → `—`, 예전에 태그로 사라지던 이름이 이제 글자로 보이는 것뿐이다.
- 리다이렉트: 기업 계정은 dashboard.html 로, 로그아웃 상태는 `login.html?redirect=admin.html` 로 이동한다.
- 이미 B안인 14개 페이지(로그인 묶음 5·컨설턴트 3·콘텐츠 5·대시보드 기업/전문가)를 HEAD brand.css 와 새 brand.css 로 각각 찍어 1440·375 에서 비교했다. 30장 중 28장이 픽셀 동일했다. terms/privacy 375 는 목차 띠의 "목차" 글자 22×12px 이 다르게 나왔는데, 같은 HEAD CSS 로 두 번 찍어도 같은 자리가 달라지므로 웹폰트 로딩 타이밍 문제이고 CSS 차이가 아니다.
- `python -m pytest tests/ -q`: 222 passed.
