# B안 운영 반영 — 화면 요소 의존성 점검표

자동 생성: `dep_inventory.py` (기준 커밋: main `94af895`). 새 디자인에서 아래 요소를 **이름 그대로** 유지해야 스크립트가 동작한다.
"HTML에 있음" 열이 ✗인 항목은 현재 HTML에 없고 스크립트가 동적으로 만들거나, 이미 끊긴 참조다.

## 1. 요소 id (83개)

| id | HTML에 있음 | 사용하는 스크립트 |
|---|---|---|
| `active-filters` | ✓ | script.js |
| `additionalNotes` | ✓ | script.js |
| `ai-manual-banner` | ✓ | script.js |
| `ai-model-badge` | ✗ | script.js |
| `ai-summary-text` | ✓ | script.js |
| `banner-iso-standard` | ✓ | script.js |
| `btn-go-ai-manual` | ✓ | script.js |
| `budget` | ✓ | script.js |
| `certStatus` | ✓ | script.js |
| `close-mobile-nav` | ✓ | index.html (inline scripts) |
| `companyName` | ✓ | script.js |
| `compare-content` | ✓ | script.js |
| `compare-modal` | ✓ | script.js |
| `consultant-filter-panel` | ✓ | script.js |
| `consultant-list` | ✓ | script.js |
| `contactEmail` | ✓ | script.js |
| `data-dashboard` | ✓ | script.js |
| `diagnosis` | ✓ | index.html (inline scripts), script.js |
| `dynamic-stats-card` | ✓ | index.html (inline scripts) |
| `employees` | ✓ | script.js |
| `evidence-section` | ✓ | script.js |
| `filter-industry` | ✓ | script.js |
| `filter-iso` | ✓ | script.js |
| `filter-rated` | ✓ | script.js |
| `filter-region` | ✓ | script.js |
| `filter-result-count` | ✓ | script.js |
| `filter-verified` | ✓ | script.js |
| `floating-cta` | ✓ | index.html (inline scripts) |
| `gov-data-status` | ✓ | script.js |
| `hero-desc` | ✓ | index.html (inline scripts) |
| `hero-h1` | ✓ | index.html (inline scripts) |
| `hero-tagline` | ✓ | index.html (inline scripts) |
| `industry` | ✓ | script.js |
| `intake-form` | ✓ | script.js |
| `iso-cards-grid` | ✓ | iso-catalog.js |
| `iso-show-more-count` | ✓ | iso-catalog.js |
| `iso-show-more-wrap` | ✓ | iso-catalog.js |
| `loading-overlay` | ✓ | script.js |
| `loading-status` | ✓ | script.js |
| `login-required-overlay` | ✗ | script.js |
| `mobile-menu-btn` | ✓ | index.html (inline scripts) |
| `mobile-nav` | ✓ | index.html (inline scripts) |
| `mobile-nav-actions` | ✓ | index.html (inline scripts) |
| `nav-actions` | ✓ | index.html (inline scripts) |
| `news-evidence-items` | ✓ | script.js |
| `news-risk-badge` | ✓ | script.js |
| `news-signal-count` | ✓ | script.js |
| `notification-styles` | ✗ | script.js |
| `progress-fill` | ✓ | script.js |
| `progress-percentage` | ✓ | script.js |
| `recommended-iso-list` | ✓ | script.js |
| `recommended-iso-preview` | ✓ | script.js |
| `refresh-btn` | ✓ | script.js |
| `refresh-container` | ✓ | script.js |
| `region` | ✓ | script.js |
| `request-quotes-btn` | ✓ | script.js |
| `result-company-title` | ✓ | script.js |
| `result-count-text` | ✓ | script.js |
| `results-section` | ✓ | script.js |
| `risk-level-text` | ✓ | script.js |
| `risk-score` | ✓ | script.js |
| `risk-tags` | ✓ | script.js |
| `score-circle-bar` | ✓ | script.js |
| `selected-names` | ✓ | script.js |
| `selected-standards-preview` | ✓ | script.js |
| `selected-standards-text` | ✓ | script.js |
| `selection-bar` | ✓ | script.js |
| `selection-count` | ✓ | script.js |
| `session-expired-overlay` | ✗ | script.js |
| `sns-evidence-items` | ✓ | script.js |
| `sns-evidence-list` | ✓ | script.js |
| `sns-keywords` | ✓ | script.js |
| `sns-keywords-section` | ✓ | script.js |
| `sns-mention-count` | ✓ | script.js |
| `sns-negative-ratio` | ✓ | script.js |
| `stat-desc` | ✓ | index.html (inline scripts) |
| `stat-label` | ✓ | index.html (inline scripts) |
| `stat-value` | ✓ | index.html (inline scripts) |
| `theme-toggle` | ✓ | index.html (inline scripts) |
| `theme-toggle-dark-icon` | ✓ | index.html (inline scripts) |
| `theme-toggle-light-icon` | ✓ | index.html (inline scripts) |
| `timeline` | ✓ | script.js |
| `toggle-filter-btn` | ✓ | script.js |

## 2. 폼 name 속성 (3개)

| name | HTML에 있음 | 사용하는 스크립트 |
|---|---|---|
| `issues` | ✓ | script.js |
| `reasons` | ✓ | script.js |
| `standards` | ✓ | script.js |

## 3. data 속성 (4개)

- `data-consultant-id` — script.js
- `data-filter` — iso-catalog.js
- `data-iso` — script.js
- `data-step` — script.js

## 4. 스크립트가 붙이고 떼는 상태 클래스 (8개)

새 CSS에서 이 클래스들의 시각 상태(숨김·활성·완료 등)를 반드시 정의해야 한다.

- `active` — iso-catalog.js, script.js
- `dark` — index.html (inline scripts) (HTML 초기값엔 없음)
- `expanded` — iso-catalog.js (HTML 초기값엔 없음)
- `hidden` — index.html (inline scripts), script.js
- `opacity-0` — index.html (inline scripts)
- `pointer-events-none` — index.html (inline scripts)
- `selected` — script.js (HTML 초기값엔 없음)
- `visible` — index.html (inline scripts) (HTML 초기값엔 없음)

## 5. HTML 인라인 핸들러가 호출하는 함수 (13개)

- `applyConsultantFilter()`
- `clearConsultantSelection()`
- `closeCompareModal()`
- `closeMobileMenu()`
- `logout()`
- `requestQuotes()`
- `requestQuotesForSelected()`
- `resetConsultantFilter()`
- `selectAllVisibleConsultants()`
- `showCompareModal()`
- `toggleConsultantFilter()`
- `window._isoShowMore()`
- `window.resetDiagnosisForm()`

## 6. querySelector 선택자 원문 (26개)

- `#ai-summary-text` — script.js
- `#notification-styles` — script.js
- `.consultant-card` — script.js
- `.consultant-card[data-consultant-id="${consultantId}"]` — script.js
- `.fade-in-up` — index.html (inline scripts), iso-catalog.js
- `.form-section.form-container` — script.js
- `.form-step` — script.js
- `.form-step[data-step="${step}"]` — script.js
- `.iso-filter-btn` — iso-catalog.js
- `.iso-std-card.expanded` — iso-catalog.js
- `.mobile-nav-link` — index.html (inline scripts)
- `.next-step` — script.js
- `.notification` — script.js
- `.prev-step` — script.js
- `.score-card` — script.js
- `a[href="#diagnosis"]` — script.js
- `a[href^="#"]` — script.js
- `h3` — script.js
- `input[name="issues"]` — script.js
- `input[name="issues"]:checked` — script.js
- `input[name="reasons"]:checked` — script.js
- `input[name="standards"]` — script.js
- `input[name="standards"]:checked` — script.js
- `input[required], select[required]` — script.js
- `input[type="checkbox"]` — script.js
- `main` — index.html (inline scripts)

## 요약

- id 83개 중 HTML에 없는 것 4개: `ai-model-badge`, `login-required-overlay`, `notification-styles`, `session-expired-overlay`
- name 3개, data 속성 4개, 상태 클래스 8개, 인라인 핸들러 함수 13개

## 7. 스크립트가 직접 그리는 HTML의 클래스 (31개)

결과 화면의 컨설턴트 카드·필터·비교 창은 script.js가 문자열로 HTML을 만들어 넣는다. 새 CSS가 아래 클래스를 B안 스타일로 정의해야 한다.
템플릿 안 인라인 style 속성도 40곳 있어, 색·여백 일부는 CSS만으로 바꿀 수 없고 script.js 수정이 필요하다.

`btn` `btn-secondary` `checkbox-custom` `checkbox-label` `compare-table` `compare-table-wrapper` `consultant-avatar` `consultant-checkbox` `consultant-header` `consultant-info` `consultant-match-reason` `consultant-select-checkbox` `consultant-specialty` `consultant-stats` `filter-tag` `flex` `iso-tag` `items-center` `justify-between` `loading-spinner` `match-score` `match-tag` `match-tag-accent` `risk-factor-icon` `session-modal` `session-modal-icon` `trust-score-bar` `trust-score-fill` `trust-tooltip` `unverified-badge` `verified-badge`

## 0단계 기준 캡처 메모 (로컬, main 94af895 기준)

캡처: `docs/redesign/baseline/` (히어로, 진단 1~4단계, 로딩, 결과, 모바일 히어로). 로그: `baseline-log.json`.

- **흐름:** 4단계 진단 → `POST /api/match` 200 → 결과 섹션 표시. 페이지 오류·콘솔 오류 0건. 로그인 없이 끝까지 진행된다.
- **로컬 데이터 한계:** 로컬 DB에 컨설턴트가 0명이라 결과 목록은 빈 상태만 확인했다. 카드가 있는 상태는 3단계 검증에서 테스트 컨설턴트를 넣고 확인해야 한다.
- **운영 헤더 링크:** ISO 표준 가이드(#iso-catalog), 프로세스(#how-it-works), 무료 상담(#diagnosis), FAQ, 블로그(blog.html), AI 매뉴얼(ai-manual.html), 다크모드 토글, 로그인, 도입 문의. B안 헤더에는 블로그·AI 매뉴얼·도입 문의가 없어 추가해야 한다. 다크모드 토글은 B안 방침(라이트 전용)에 따라 뺀다.
- **기존 버그 1 — 선택 바 노출:** 진단 2단계 화면부터 화면 하단에 컨설턴트 비교용 선택 바("명 선택됨 / 선택 취소 / 비교하기 / 견적 요청하기")가 보인다. 결과에서 컨설턴트를 고른 경우에만 보여야 한다. 새 디자인에서 함께 고친다.
- **기존 버그 2 — 결과 문구:** 결과 상단에 "분석된 리스크 점수와 추천 솔루션입니다."라고 나오지만 리스크 점수 블록은 쓰지 않는다(매칭 전용으로 바뀜). 문구 정리와 숨겨진 리스크·뉴스·SNS 블록 제거 여부를 2단계에서 결정한다.
- **규격 표기:** 결과 요약에 "ISO 9001:2015"로 나온다. PR #1 병합 후 2026판 표기로 바뀌는지 확인한다.
- **규격 수 불일치:** 진단에는 ISO 14090(기후적응)이 있어 26종, 컨설턴트 등록은 25종이다. 디자인 이식에서는 현재 진단 선택지를 그대로 유지하고, 정리는 별도 결정으로 남긴다.
- **운영 화면의 근거 없는 수치:** "1,200+"(통계 카드), "상위 1%"(2곳), "평균 3주"(헤드라인 로테이션). B안 이식 시 모두 제거된다.
