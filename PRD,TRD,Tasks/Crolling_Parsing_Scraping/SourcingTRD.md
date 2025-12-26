# ON-100 Sourcing Tool - Technical Requirements Document (TRD)

**Version:** 0.1 MVP  
**Last Updated:** 2025-12-24  
**Status:** Production Ready

---

## 1. 프로젝트 개요

### 1.1 목적
ON-100 Sourcing Tool은 한국 주요 온라인 쇼핑몰(쿠팡, 11번가, 지마켓, 옥션)에서 상품 정보를 자동으로 수집하고 분석하는 웹 애플리케이션입니다. 카테고리 페이지나 상품 상세 페이지에서 상품명, 가격, 이미지 등의 정보를 추출하여 CSV 형식으로 내보낼 수 있습니다.

### 1.2 주요 기능
- **다중 마켓 지원**: 쿠팡, 11번가, 지마켓, 옥션 4개 쇼핑몰 지원
- **자동 마켓 감지**: URL을 분석하여 자동으로 마켓 선택
- **카테고리/상품 페이지 지원**: 카테고리 목록 페이지 및 개별 상품 상세 페이지 모두 지원
- **이미지 자동 다운로드**: 상품 이미지를 로컬에 저장하여 웹에서 표시
- **CSV 내보내기**: 수집된 데이터를 CSV 형식으로 다운로드
- **봇 탐지 우회**: Playwright Stealth 및 고급 스텔스 기법으로 봇 탐지 회피

### 1.3 지원 마켓
| 마켓 | 상태 | 카테고리 지원 | 상품 상세 지원 |
|------|------|--------------|---------------|
| 쿠팡 (Coupang) | ✅ | ✅ (검색엔진 경유) | ✅ |
| 11번가 (11st) | ✅ | ✅ | ✅ |
| 지마켓 (Gmarket) | ✅ | ✅ | ✅ |
| 옥션 (Auction) | ✅ | ✅ (검색엔진 경유) | ✅ |

---

## 2. 시스템 아키텍처

### 2.1 전체 구조
```
┌─────────────────┐
│  Next.js Frontend │
│  (React + TypeScript) │
└────────┬────────┘
         │ HTTP GET
         ▼
┌─────────────────┐
│  Next.js API Route │
│  /api/sourcing/scrape │
└────────┬────────┘
         │ spawn Python
         ▼
┌─────────────────┐
│  Python Scraper │
│  (Playwright)   │
└────────┬────────┘
         │ HTTP Request
         ▼
┌─────────────────┐
│  Shopping Malls │
│  (Coupang, etc.) │
└─────────────────┘
```

### 2.2 기술 스택

#### Frontend
- **Framework**: Next.js 16.1.0 (App Router)
- **Language**: TypeScript 5
- **UI Library**: React 19.2.3
- **Styling**: Tailwind CSS 4
- **Icons**: Lucide React
- **HTTP Client**: Axios 1.13.2

#### Backend
- **Runtime**: Node.js (Next.js API Routes)
- **Python Version**: Python 3.x
- **Web Scraping**: Playwright (Chromium)
- **Stealth**: playwright-stealth
- **HTTP Client**: aiohttp
- **Image Processing**: Pillow 10.0.0

### 2.3 디렉토리 구조
```
sourcing-tool-mvp/
├── src/
│   └── app/
│       ├── api/
│       │   └── sourcing/
│       │       └── scrape/
│       │           └── route.ts          # API 엔드포인트
│       ├── sourcing/
│       │   └── page.tsx                  # 메인 UI 페이지
│       ├── layout.tsx
│       └── page.tsx
├── scripts_sourcing/
│   ├── main_scraper.py                   # 메인 스크래퍼 진입점
│   ├── base_connector.py                 # 기본 커넥터 클래스
│   ├── coupang_connector.py              # 쿠팡 커넥터
│   ├── eleven_connector.py               # 11번가 커넥터
│   ├── gmarket_connector.py              # 지마켓 커넥터
│   ├── auction_connector.py              # 옥션 커넥터
│   └── requirements.txt                  # Python 의존성
├── public/
│   └── images/
│       └── products/                     # 다운로드된 이미지 저장소
└── package.json
```

---

## 3. 기능 명세

### 3.1 URL 입력 및 처리

#### 입력 형식
- **단일 URL**: 한 줄에 하나의 URL
- **다중 URL**: 줄바꿈으로 구분된 여러 URL
- **지원 URL 형식**:
  - 카테고리 페이지: `https://www.11st.co.kr/category/DisplayCategory.tmall?dispCtgrNo=1129369`
  - 상품 상세 페이지: `https://item.gmarket.co.kr/item?goodsCode=3486427669`

#### URL 검증
- URL 형식 검증 (자동 마켓 감지)
- 지원되지 않는 마켓 URL인 경우 에러 반환

### 3.2 마켓 자동 감지

```python
# main_scraper.py
if 'coupang.com' in url:
    market = 'coupang'
elif '11st.co.kr' in url or '11.st' in url:
    market = 'eleven'
elif 'gmarket.co.kr' in url:
    market = 'gmarket'
elif 'auction.co.kr' in url:
    market = 'auction'
```

### 3.3 상품 정보 추출

#### 추출 항목
| 필드명 | 타입 | 설명 |
|--------|------|------|
| `market` | string | 마켓 이름 (coupang, eleven, gmarket, auction) |
| `source_url` | string | 원본 상품 URL |
| `product_title` | string | 상품명 |
| `price` | number | 가격 (KRW) |
| `currency` | string | 통화 (기본값: KRW) |
| `thumbnail_url` | string | 썸네일 이미지 URL |
| `local_image_path` | string | 로컬 저장된 이미지 경로 |
| `extracted_at` | string | 추출 일시 (ISO 8601) |
| `seller_name` | string | 판매자명 |
| `shipping_fee` | number | 배송비 |
| `success` | boolean | 추출 성공 여부 |
| `error_code` | string | 에러 코드 (실패 시) |
| `error_message` | string | 에러 메시지 (실패 시) |

### 3.4 이미지 처리

#### 이미지 다운로드
- **저장 위치**: `public/images/products/`
- **파일명 형식**: `{market}_{product_id}.{ext}`
- **지원 형식**: JPG, PNG, WebP, GIF
- **중복 방지**: 동일 파일명이 존재하면 재다운로드 스킵

#### 이미지 표시 우선순위
1. `local_image_path` (로컬 저장된 이미지)
2. `thumbnail_url` (원본 URL, 로컬 이미지 실패 시)

### 3.5 봇 탐지 우회 기법

#### Playwright Stealth
- `playwright-stealth` 라이브러리 사용
- WebDriver 속성 제거
- User-Agent 랜덤화

#### 고급 스텔스 (쿠팡, 옥션)
- **WebDriver 속성 완전 제거**
- **하드웨어 정보 랜덤화**: `hardwareConcurrency`, `deviceMemory`
- **플러그인 정보 추가**
- **Chrome 속성 시뮬레이션**
- **자연스러운 스크롤 패턴**
- **세션 워밍업**: 홈페이지 방문 후 타겟 페이지 이동

---

## 4. API 명세

### 4.1 스크래핑 API

**Endpoint**: `GET /api/sourcing/scrape`

#### 요청 파라미터
| 파라미터 | 타입 | 필수 | 설명 |
|----------|------|------|------|
| `url` | string | ✅ | 추출할 상품/카테고리 URL |
| `market` | string | ❌ | 마켓 지정 (기본값: 'auto') |

#### 요청 예시
```
GET /api/sourcing/scrape?url=https://www.11st.co.kr/products/1234567890&market=auto
```

#### 응답 형식

**성공 응답** (200 OK):
```json
{
  "success": true,
  "results": [
    {
      "market": "eleven",
      "source_url": "https://www.11st.co.kr/products/1234567890",
      "product_title": "상품명",
      "price": 15000,
      "currency": "KRW",
      "thumbnail_url": "https://...",
      "local_image_path": "/images/products/eleven_1234567890.jpg",
      "extracted_at": "2025-12-24T10:00:00.000Z",
      "success": true
    }
  ]
}
```

**실패 응답** (500 Internal Server Error):
```json
{
  "success": false,
  "error_code": "SCRAPER_CRASH",
  "error_message": "Python process exited with error"
}
```

#### 에러 코드
| 에러 코드 | 설명 |
|-----------|------|
| `UNSUPPORTED_MARKET` | 지원되지 않는 마켓 |
| `NETWORK_ERROR` | 네트워크 오류 |
| `PARSER_ERROR` | 파싱 오류 |
| `NO_RESULTS` | 추출된 결과 없음 |
| `SCRAPER_CRASH` | Python 프로세스 크래시 |
| `BLOCKED` | 봇 탐지로 차단됨 |

---

## 5. 커넥터별 상세 명세

### 5.1 BaseConnector (기본 클래스)

#### 주요 메서드
- `extract(url: str) -> list`: 추출 메인 메서드 (추상)
- `create_result_item(source_url: str) -> dict`: 결과 아이템 생성
- `download_image(image_url: str, product_id: str) -> str`: 이미지 다운로드
- `run_with_playwright(url: str) -> tuple`: Playwright 실행

### 5.2 CoupangConnector

#### 특징
- **카테고리 페이지**: 봇 탐지가 강력하여 홈페이지에서 상품 추출
- **상품 상세 페이지**: XHR 응답 캡처 및 DOM 파싱 병행
- **고급 스텔스**: WebDriver 제거, 하드웨어 정보 랜덤화

#### URL 형식
- 카테고리: `https://www.coupang.com/np/categories/{category_id}`
- 상품: `https://www.coupang.com/vp/products/{product_id}`

### 5.3 ElevenConnector

#### 특징
- **카테고리/상품 페이지 모두 지원**
- **상세 이미지 추출**: iframe 내 이미지 포함
- **안정적인 DOM 파싱**

#### URL 형식
- 카테고리: `https://www.11st.co.kr/category/DisplayCategory.tmall?dispCtgrNo={id}`
- 상품: `https://www.11st.co.kr/products/{product_id}`

### 5.4 GmarketConnector

#### 특징
- **카테고리/상품 페이지 자동 구분**
- **세션 워밍업**: 홈페이지 방문 후 타겟 페이지 이동
- **JavaScript 기반 DOM 추출**

#### URL 형식
- 카테고리: `https://www.gmarket.co.kr/n/list?category={category_id}`
- 상품: `https://item.gmarket.co.kr/item?goodsCode={goods_code}`

### 5.5 AuctionConnector

#### 특징
- **세션 기반 접근**: 홈페이지에서 세션 확보 후 카테고리 이동
- **봇 탐지 회피**: 자연스러운 마우스 이동 및 스크롤
- **검색 기능 지원**: 카테고리 접근 실패 시 검색으로 폴백

#### URL 형식
- 카테고리: `https://www.auction.co.kr/n/list?category={category_id}`
- 검색: `https://www.auction.co.kr/n/search?keyword={keyword}`

---

## 6. 데이터 구조

### 6.1 결과 데이터 스키마

```typescript
interface SourcingResult {
    market: string;                    // 마켓 이름
    source_url: string;                // 원본 URL
    product_title: string;             // 상품명
    price: number;                     // 가격
    currency: string;                  // 통화 (기본: KRW)
    thumbnail_url: string;             // 썸네일 이미지 URL
    local_image_path?: string;         // 로컬 이미지 경로
    detail_image_urls?: string[];      // 상세 이미지 URL 목록
    local_detail_image_paths?: string[]; // 로컬 상세 이미지 경로 목록
    extracted_at: string;              // 추출 일시 (ISO 8601)
    seller_name?: string;              // 판매자명
    shipping_fee?: number;            // 배송비
    delivery_estimate?: string;       // 배송 예상일
    option_summary?: string;          // 옵션 요약
    success: boolean;                  // 성공 여부
    error_code?: string;              // 에러 코드
    error_message?: string;           // 에러 메시지
}
```

### 6.2 CSV 내보내기 형식

```csv
Market,URL,Product Title,Price,Currency,Extracted At
eleven,https://www.11st.co.kr/products/123,"상품명",15000,KRW,2025-12-24T10:00:00.000Z
```

---

## 7. 환경 설정

### 7.1 필수 요구사항

#### Node.js
- **버전**: Node.js 18.x 이상
- **패키지 매니저**: npm 또는 yarn

#### Python
- **버전**: Python 3.8 이상
- **Playwright**: `playwright install chromium` 실행 필요

### 7.2 설치 및 실행

#### 1. 의존성 설치
```bash
# Node.js 의존성
npm install

# Python 의존성
cd scripts_sourcing
pip install -r requirements.txt

# Playwright 브라우저 설치
playwright install chromium
```

#### 2. 환경 변수 설정
```bash
# .env.local (선택사항)
PYTHON_PATH=python  # 또는 python3, py 등
```

#### 3. 개발 서버 실행
```bash
npm run dev
```

#### 4. 접속
- URL: `http://localhost:3000/sourcing`

### 7.3 프로덕션 빌드
```bash
npm run build
npm start
```

---

## 8. 제한사항 및 알려진 이슈

### 8.1 제한사항

#### 카테고리 페이지 접근
- **쿠팡**: 검색엔진 경유 접근으로 카테고리 키워드 기반 상품 추출 가능 (직접 URL 접근은 여전히 차단됨)
- **옥션**: 검색엔진 경유 및 세션 기반 접근으로 카테고리 페이지 접근 개선

#### URL 요구사항
- **소분류 카테고리 링크 필요**: 대분류/중분류 링크는 상품 목록이 없어 추출 실패
- **올바른 예시**:
  - ✅ `https://www.11st.co.kr/category/DisplayCategory.tmall?dispCtgrNo=1129369` (소분류)
  - ❌ `https://www.11st.co.kr/category/...` (대분류)

#### 성능
- **동시 요청 제한**: 한 번에 하나의 스크래핑 작업만 처리
- **타임아웃**: 각 요청당 최대 45초

### 8.2 알려진 이슈

1. **쿠팡 카테고리 추출**: 홈페이지 기반 추출로 인해 특정 카테고리만 추출 불가
2. **옥션 봇 탐지**: 간헐적으로 봇 탐지 페이지 표시
3. **이미지 다운로드 실패**: 일부 이미지 URL이 만료되거나 접근 불가

---

## 9. 보안 고려사항

### 9.1 봇 탐지 우회
- Playwright Stealth 사용
- User-Agent 랜덤화
- 자연스러운 브라우저 행동 시뮬레이션

### 9.2 데이터 보안
- 이미지 파일은 로컬에만 저장
- 사용자 입력 URL 검증
- 에러 메시지에 민감한 정보 포함하지 않음

---

## 10. 향후 개선 계획

### 10.1 단기 개선
- [ ] UI에 카테고리 URL 가이드 추가
- [ ] 에러 메시지 개선
- [ ] 로딩 상태 표시 개선

### 10.2 중기 개선
- [ ] 동시 다중 URL 처리
- [ ] 스크래핑 결과 캐싱
- [ ] 더 많은 마켓 지원

### 10.3 장기 개선
- [ ] 공식 API 연동 (쿠팡 파트너스 API 등)
- [ ] 데이터베이스 연동
- [ ] 스케줄링 기능

---

## 11. 참고 자료

### 11.1 관련 문서
- `README.md`: 프로젝트 기본 정보
- `scripts_sourcing/README_CATEGORY_SCRAPER.md`: 카테고리 스크래퍼 가이드

### 11.2 외부 라이브러리
- [Playwright](https://playwright.dev/python/)
- [playwright-stealth](https://github.com/AtuboDad/playwright_stealth)
- [Next.js](https://nextjs.org/docs)

---

## 12. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 0.1 MVP | 2025-12-24 | 초기 릴리스 |

---

**문서 작성자**: AI Assistant  
**검토자**: -  
**승인자**: -

