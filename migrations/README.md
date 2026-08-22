# DB 마이그레이션 (수동)

이 프로젝트는 Alembic 같은 마이그레이션 도구를 쓰지 않고, 앱 기동 시
`db.create_all()` 로 스키마를 맞춘다. 이 방식에는 **중요한 한계**가 있다.

> `db.create_all()` 은 **없는 테이블은 생성하지만,
> 기존 테이블에 컬럼을 추가하지는 않는다.**

따라서 `api/models.py` 의 **기존 모델에 컬럼을 추가**했다면,
배포 전에 반드시 아래 SQL을 프로덕션 DB(Supabase)에 직접 실행해야 한다.
그렇지 않으면 해당 테이블을 조회하는 모든 API가 500으로 실패한다.

## 배포 체크리스트 (모델 변경 시)

1. `api/models.py` 에서 컬럼을 추가/변경했는가?
2. 그렇다면 아래에 SQL을 추가하고, **배포 전에** Supabase SQL Editor에서 실행
3. 새 테이블을 추가했다면 `create_all` 이 만들어주지만,
   **RLS는 자동으로 켜지지 않으므로** 별도로 활성화할 것
4. 배포 후 주요 엔드포인트 응답 확인 (`/api/posts`, `/api/auth/login` 등)

---

## 2026-08-22 (3) — L1-C1 문의 접수 + 회원 탈퇴: `inquiry` 신규 + `user.deleted_at` 추가

⚠️ **기존 테이블(`user`)에 컬럼을 추가한다.** `create_all()` 은 기존 테이블에
컬럼을 추가하지 못하므로 **배포 전에 반드시 아래 ALTER 를 먼저 실행**해야 한다.
빠뜨리면 로그인을 포함해 사용자를 조회하는 **모든 API 가 500 으로 실패**한다
(전례 있음).

이번 배치의 스키마 변경은 **`user.deleted_at` 컬럼 1개 + `inquiry` 테이블 1개**뿐이다.

```sql
-- ① 기존 테이블 컬럼 추가 — create_all 이 못 하는 부분. 배포 전 필수.
--    회원 탈퇴(소프트 삭제) 시각. NULL 이면 정상 계정.
--    행을 지우지 않는 이유: user.id 를 참조하는 FK 가 10곳이 넘어
--    (project.company_id / consultant.user_id / message.sender_id /
--     notification.user_id / admin_action_log.admin_user_id 등)
--    하드 삭제하면 상대방의 거래 이력과 감사 로그까지 함께 망가진다.
ALTER TABLE public."user" ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;

-- 탈퇴 계정 제외 조회(로그인·관리자 통지·cron 발송 대상)에 맞춘 부분 인덱스.
-- 탈퇴자는 소수이므로 정상 계정만 인덱스에 남긴다.
CREATE INDEX IF NOT EXISTS ix_user_active
    ON public."user" (id)
    WHERE deleted_at IS NULL;

-- ② 신규 테이블: 문의 접수
--    create_all 이 자동 생성하지만 미리 만들어도 무방하다.
CREATE TABLE IF NOT EXISTS public.inquiry (
    id         SERIAL NOT NULL,
    user_id    INTEGER NULL REFERENCES public."user"(id),  -- 비로그인 접수면 NULL
    name       VARCHAR(100) NOT NULL,
    email      VARCHAR(120) NOT NULL,
    category   VARCHAR(30)  NOT NULL DEFAULT 'etc',
    subject    VARCHAR(200) NOT NULL,
    content    TEXT         NOT NULL,
    status     VARCHAR(20)  NOT NULL DEFAULT 'received',   -- received | checked | done
    admin_memo TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id)
);

-- 관리자 화면의 조회 조건(상태 필터 + 최신순 정렬)에 맞춘 인덱스
CREATE INDEX IF NOT EXISTS ix_inquiry_status     ON public.inquiry (status);
CREATE INDEX IF NOT EXISTS ix_inquiry_created_at ON public.inquiry (created_at);

-- ③ ⚠️ 필수: 신규 테이블의 RLS 활성화 (create_all 은 RLS 를 켜주지 않는다)
ALTER TABLE IF EXISTS public.inquiry ENABLE ROW LEVEL SECURITY;
```

적용 확인:

```sql
-- 컬럼 추가 확인
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'user' AND column_name = 'deleted_at';

-- RLS 확인 (rowsecurity = true 여야 함)
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname = 'public' AND tablename = 'inquiry';
```

### 운영 메모 — 탈퇴 계정의 이메일 재사용

탈퇴 시 `user.email` 을 `deleted_<id>@deleted.invalid` 로 치환한다
(`.invalid` 는 RFC 2606 예약 도메인이라 실제로 존재할 수 없다).
따라서 **탈퇴자는 같은 이메일로 재가입할 수 있다.** `user.email` 의 unique
제약을 유지하면서 개인정보를 파기하기 위한 선택이며, 원본 이메일을 남겨두면
파기 의무(개인정보보호법 제21조)에 반하고 재가입도 영구히 막히게 된다.

> ⚠️ 부작용: 사용자당 하루 한도(`DAILY_MANUAL_LIMIT`, AI 매뉴얼 1회/일)가
> 탈퇴 후 재가입으로 초기화된다. IP 기준 `check_rate_limit` 이 남아 있으므로
> 무제한은 아니지만, 남용이 관측되면 별도 방어를 붙여야 한다.

---

## 2026-08-22 (2) — L1-B 통지·리마인더: `notification.emailed_at` 추가

⚠️ **기존 테이블에 컬럼을 추가한다.** `create_all()` 은 기존 테이블에 컬럼을
추가하지 못하므로 **배포 전에 반드시 아래 ALTER 를 실행**해야 한다. 빠뜨리면
알림을 조회·생성하는 모든 API 가 500 으로 실패한다(전례 있음).

이번 배치의 **스키마 변경은 이 컬럼 하나뿐**이다.

```sql
-- 이 알림이 메일로도 나갔는지(= 나간 시각). NULL 이면 아직 인앱 전용.
-- 일일 배치의 '미열람 알림 메일 승격' 이 이 값으로 재발송을 막는다.
ALTER TABLE public.notification ADD COLUMN IF NOT EXISTS emailed_at TIMESTAMP NULL;

-- 승격 배치의 조회 조건(미열람 + 미발송 + 생성시각 범위)에 맞춘 부분 인덱스.
-- 발송이 끝난 행은 인덱스에서 빠지므로 알림이 쌓여도 조회 비용이 늘지 않는다.
CREATE INDEX IF NOT EXISTS ix_notification_pending_email
    ON public.notification (created_at)
    WHERE emailed_at IS NULL AND is_read IS NOT TRUE;
```

적용 확인:

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'notification' AND column_name = 'emailed_at';
```

### 배포 직후 주의 — 과거 미열람 알림의 일괄 발송

배포 시점에 **기존 알림은 전부 `emailed_at IS NULL`** 이다. 코드에는
`UNREAD_PROMOTION_MAX_AGE_DAYS = 7` 상한이 있어 7일보다 오래된 것은 승격 대상이
아니지만, 최근 7일치 미열람 알림은 첫 배치에서 한 번에 메일로 나간다.
그것도 원치 않는다면 배포 직후 아래로 과거분을 '이미 처리됨' 으로 눌러둔다.

```sql
-- 선택 사항: 배포 이전의 알림은 메일 승격 대상에서 제외한다.
UPDATE public.notification SET emailed_at = NOW() WHERE emailed_at IS NULL;
```

---

## 2026-08-22 — L0 시간 기반 자동화: `cron_run` 신규 + `project.completed_at` 추가

⚠️ **이번 변경은 기존 테이블에 컬럼을 추가한다.** `project.completed_at` 은
`create_all()` 이 만들어주지 않으므로 **배포 전에 반드시 아래 ALTER 를 실행**해야
한다. 이걸 빠뜨리면 프로젝트를 조회하는 모든 API 가 500 으로 실패한다(전례 있음).

```sql
-- ① 기존 테이블 컬럼 추가 — create_all 이 못 하는 부분. 배포 전 필수.
--    프로젝트가 언제 끝났는지(정산 시점 근거·감사용)
ALTER TABLE public.project ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP NULL;

-- ② 신규 테이블: cron 실행 기록
--    create_all 이 자동 생성하지만 미리 만들어도 무방하다.
CREATE TABLE IF NOT EXISTS public.cron_run (
    id            SERIAL NOT NULL,
    job           VARCHAR(50) NOT NULL,
    started_at    TIMESTAMP WITHOUT TIME ZONE,
    finished_at   TIMESTAMP WITHOUT TIME ZONE,
    success       BOOLEAN,
    summary       TEXT,          -- JSON: 작업별 처리 건수
    error_message TEXT,
    triggered_by  VARCHAR(30),   -- 'vercel-cron' | 'external-cron' | 'admin'
    PRIMARY KEY (id)
);

-- 조회 인덱스 (마지막 성공 실행 조회 = job + success 필터 + started_at 정렬)
CREATE INDEX IF NOT EXISTS ix_cron_run_job        ON public.cron_run (job);
CREATE INDEX IF NOT EXISTS ix_cron_run_started_at ON public.cron_run (started_at);
CREATE INDEX IF NOT EXISTS ix_cron_run_success    ON public.cron_run (success);

-- ③ ⚠️ 필수: 신규 테이블의 RLS 활성화 (create_all 은 RLS 를 켜주지 않는다)
ALTER TABLE IF EXISTS public.cron_run ENABLE ROW LEVEL SECURITY;
```

적용 확인:

```sql
-- 컬럼 추가 확인
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'project' AND column_name = 'completed_at';

-- RLS 확인 (rowsecurity = true 여야 함)
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname = 'public' AND tablename = 'cron_run';
```

### 배포 후 환경변수 (SQL 아님)

`CRON_SECRET` 을 Vercel 환경변수에 추가해야 스케줄러가 배치를 호출할 수 있다.
**미설정 시 `/api/cron/daily` 는 401 로 거부한다**(빈 secret 을 허용하면 누구나
메일 발송·행 삭제가 포함된 배치를 돌릴 수 있게 되므로 의도적으로 막았다).

```bash
# 16자 이상 랜덤 문자열. 개행·특수문자가 섞이면 Authorization 헤더에서 깨진다.
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

GitHub Actions 대안을 쓸 경우 저장소 Secret 에도 **같은 값**을 넣는다
(`.github/workflows/daily-cron.yml` 주석 참고).

> 운영 메모: 에러 로그 90일 보관 정리는 이제 이 cron(`expired_cleanup` 작업)이
> 자동으로 처리한다. 아래 2026-08-21 항목의 수동 DELETE 는 더 이상 필요 없다.

---

## 2026-08-21 — L0 관측성: 에러 로그 테이블 (`error_log`)

미처리 예외를 기록하는 신규 테이블이다. `create_all` 이 자동 생성하지만,
**RLS는 자동으로 켜지지 않으므로** 반드시 아래를 실행해야 한다.
(테이블을 미리 만들어두고 싶다면 `CREATE TABLE` 부분까지 함께 실행)

```sql
-- 신규 테이블 (create_all 이 만들지만, 미리 만들어도 무방하다)
CREATE TABLE IF NOT EXISTS public.error_log (
    id          SERIAL NOT NULL,
    created_at  TIMESTAMP WITHOUT TIME ZONE,
    level       VARCHAR(20),
    path        VARCHAR(300),
    method      VARCHAR(10),
    status_code INTEGER,
    exc_type    VARCHAR(120),
    exc_message TEXT,
    traceback   TEXT,
    user_id     INTEGER,        -- ⚠️ FK 없음: 로깅이 참조 무결성 때문에 실패하면 안 된다
    client_ip   VARCHAR(64),
    fingerprint VARCHAR(64),
    PRIMARY KEY (id)
);

-- 조회 인덱스 (기간 필터 + fingerprint GROUP BY)
CREATE INDEX IF NOT EXISTS ix_error_log_created_at  ON public.error_log (created_at);
CREATE INDEX IF NOT EXISTS ix_error_log_fingerprint ON public.error_log (fingerprint);

-- ⚠️ 필수: 신규 테이블의 RLS 활성화
ALTER TABLE IF EXISTS public.error_log ENABLE ROW LEVEL SECURITY;
```

적용 확인:

```sql
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname = 'public' AND tablename = 'error_log';
```

> 운영 메모: 이 테이블은 계속 쌓이기만 한다(카운터 UPDATE 는 서버리스 동시성
> 경합 때문에 의도적으로 쓰지 않는다). 보관 기간 정리는 cron 인프라가 생기는
> 배치 3에서 붙인다. 그 전에 급하면 아래로 수동 정리:
>
> ```sql
> DELETE FROM public.error_log WHERE created_at < NOW() - INTERVAL '90 days';
> ```

## 2026-08-09 — 컨설턴트 정산 정보 + 초대 링크

```sql
-- 정산·세금계산서 정보 (A안: NGB 원청 구조에서 외주비 지급에 필요)
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS business_type VARCHAR(20);
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS biz_reg_no    VARCHAR(20);
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS biz_name      VARCHAR(100);
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS biz_ceo_name  VARCHAR(50);
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS bank_name     VARCHAR(50);
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS account_number VARCHAR(50);
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS account_holder VARCHAR(50);

-- 기본 협력계약 동의 이력
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS partner_agreed_at TIMESTAMP NULL;
ALTER TABLE public.consultant ADD COLUMN IF NOT EXISTS partner_agreement_version VARCHAR(20);
```

`consultant_invite` 테이블은 신규 테이블이라 `create_all`이 자동 생성한다.
**단, RLS는 자동으로 켜지지 않으므로** 배포 후 반드시 실행할 것:

```sql
ALTER TABLE IF EXISTS public.consultant_invite ENABLE ROW LEVEL SECURITY;
```

## 2026-07-05 — 보안 수정 (Critical/High)

```sql
-- Project/Post 소프트 삭제: 삭제해도 대화·본문을 보존하기 위한 컬럼
ALTER TABLE public.post    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;
ALTER TABLE public.project ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL;

-- 토큰 폐기용 버전 (비밀번호 재설정 시 기존 세션 무효화)
ALTER TABLE public."user"  ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

-- 신규 테이블은 create_all 이 생성하지만 RLS는 수동으로 켜야 한다
ALTER TABLE IF EXISTS public.rate_limit_entry  ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.manual_generation ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.admin_action_log  ENABLE ROW LEVEL SECURITY;
```

적용 상태 확인:

```sql
-- 컬럼 존재 확인
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = 'public'
  AND (column_name IN ('deleted_at', 'token_version'))
ORDER BY table_name;

-- RLS 적용 확인 (rowsecurity 가 모두 true 여야 함)
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname = 'public' ORDER BY rowsecurity, tablename;
```

> 주의: Supabase SQL Editor는 여러 문장을 한 트랜잭션으로 실행한다.
> 존재하지 않는 테이블을 대상으로 하면 **전체가 롤백**되므로 `IF EXISTS` 를 붙일 것.
