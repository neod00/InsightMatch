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
