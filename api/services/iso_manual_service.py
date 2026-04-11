"""
ISO Manual Generation Service (v3 — 구조 교정 + 멀티에이전트 기반)
=================================================================
v2 대비 주요 변경:
  1. ISO HLS(High Level Structure) 조항 번호(4~10절)와 매뉴얼 목차를 일치시킴
  2. 매뉴얼(Level 1)과 절차서(Level 2)를 명확히 분리
     - 매뉴얼은 방침/원칙/프로세스 상호관계를 기술하고 절차서를 '참조'
     - 절차서는 별도 doc_type='procedure'로 생성 (향후 유료 기능)
  3. 멀티에이전트 생성을 위한 문서 유형(DOC_TYPES) 상수 체계 정립
  4. Phase 1/2 분할 아키텍처 최적화 (Vercel 60초 제한 대응)
"""
import os
import json
import re
import requests as http_requests  # Flask의 request와 이름 충돌 방지

# ─────────────────────────────────────────────────────────────
# 문서 유형 상수 (멀티에이전트 확장용)
# ─────────────────────────────────────────────────────────────
DOC_TYPES = {
    'manual': {
        'level': 1,
        'name': '통합경영매뉴얼',
        'doc_id_prefix': 'SMS-M',
        'description': '회사의 경영방침, ISO 조항별 대응 원칙, 프로세스 상호관계를 기술하는 최상위 문서'
    },
    'common_procedure': {
        'level': 2,
        'name': '공통관리 절차서',
        'doc_id_prefix': 'SMS-CP',
        'description': '부서 공통으로 적용되는 관리 절차 (리스크관리, 문서관리, 내부심사 등)'
    },
    'operation_procedure': {
        'level': 2,
        'name': '운용(품질) 절차서',
        'doc_id_prefix': 'SMS-QP',
        'description': '업종별 핵심 운용 프로세스 절차 (영업, 설계, 구매, 생산, 검사 등)'
    },
    'instruction': {
        'level': 3,
        'name': '작업지침서',
        'doc_id_prefix': 'SMS-WI',
        'description': '현장 작업표준, 기계 조작법, 안전 작업 요령 등'
    },
    'form': {
        'level': 4,
        'name': '양식/기록물',
        'doc_id_prefix': 'SMS-F',
        'description': '실제 작성하는 점검표, 검사성적서, 회의록 양식 등'
    },
}

# 절차서 목록 (향후 개별 생성 대상)
PROCEDURE_LIST = {
    'common': [
        {'id': 'CP00', 'name': '리스크관리 절차서', 'iso_clause': '6.1'},
        {'id': 'CP01', 'name': '경영 계획수립 및 검토 절차서', 'iso_clause': '5.1, 9.3'},
        {'id': 'CP02', 'name': '조직 및 업무분장 절차서', 'iso_clause': '5.3'},
        {'id': 'CP03', 'name': '교육훈련 절차서', 'iso_clause': '7.2'},
        {'id': 'CP04', 'name': '의사소통 관리 절차서', 'iso_clause': '7.4'},
        {'id': 'CP05', 'name': '문서화된 정보관리 절차서', 'iso_clause': '7.5'},
        {'id': 'CP06', 'name': '내부심사 절차서', 'iso_clause': '9.2'},
        {'id': 'CP07', 'name': '시정조치 절차서', 'iso_clause': '10.2'},
    ],
    'operation': [
        {'id': 'QP00', 'name': '인프라관리 절차서', 'iso_clause': '7.1.3'},
        {'id': 'QP01', 'name': '영업관리 절차서', 'iso_clause': '8.2'},
        {'id': 'QP02', 'name': '설계관리 절차서', 'iso_clause': '8.3'},
        {'id': 'QP03', 'name': '협력업체/구매관리 절차서', 'iso_clause': '8.4'},
        {'id': 'QP04', 'name': '프로젝트(생산)관리 절차서', 'iso_clause': '8.5'},
        {'id': 'QP05', 'name': '검사 및 시험 절차서', 'iso_clause': '8.6'},
        {'id': 'QP06', 'name': '부적합 출력 관리 절차서', 'iso_clause': '8.7'},
    ],
}


# ISO 표준별 파일 매핑
ISO_STANDARD_INFO = {
    "ISO 9001:2015": {
        "name": "품질경영시스템",
        "file": "ISO_9001_2015.txt",
        "focus": "품질방침, 고객만족, 제품/서비스 적합성, 프로세스 관리"
    },
    "ISO 14001:2015": {
        "name": "환경경영시스템",
        "file": "ISO_14001_2016.txt",
        "focus": "환경방침, 환경측면/영향, 준수의무, 오염예방, 전과정 관점"
    },
    "ISO 45001:2018": {
        "name": "안전보건경영시스템",
        "file": "ISO_45001_2019.txt",
        "focus": "안전보건방침, 위험요인 파악, 리스크 평가, 근로자 참여, 비상대비"
    }
}

# ─────────────────────────────────────────────────────────────
# 경영 이슈 → 맞춤 KPI 매핑 (제조/건설/엔지니어링 특화)
# ─────────────────────────────────────────────────────────────
ISSUE_KPI_MAP = {
    'quality_defect': {
        'name': '품질 불량/불량률 증가',
        'kpis': [
            {'indicator': '공정불량률', 'target': '전년대비 20% 감소', 'formula': '(불량품수/총생산수)×100%', 'cycle': '월 1회'},
            {'indicator': '고객반품률', 'target': '0.5% 이하 유지', 'formula': '(반품수/출하수)×100%', 'cycle': '월 1회'},
            {'indicator': '초도품 합격률', 'target': '95% 이상', 'formula': '(초도 합격건/초도 검사건)×100%', 'cycle': '분기 1회'},
        ]
    },
    'customer_complaint': {
        'name': '고객 클레임 증가',
        'kpis': [
            {'indicator': '고객 클레임 건수', 'target': '전년대비 30% 감소', 'formula': '월간 VOC 접수건수', 'cycle': '월 1회'},
            {'indicator': '클레임 처리율', 'target': '95% 이상 (7일 이내)', 'formula': '(기한내 처리건/총접수건)×100%', 'cycle': '월 1회'},
            {'indicator': '고객만족도', 'target': '85점 이상', 'formula': '고객만족도 설문조사 점수', 'cycle': '반기 1회'},
        ]
    },
    'process_inefficiency': {
        'name': '프로세스 비효율',
        'kpis': [
            {'indicator': '납기준수율', 'target': '98% 이상', 'formula': '(납기 준수건/총주문건)×100%', 'cycle': '월 1회'},
            {'indicator': '공정 리드타임', 'target': '전년대비 15% 단축', 'formula': '수주~출하 평균 소요일수', 'cycle': '월 1회'},
            {'indicator': '재작업률', 'target': '3% 이하', 'formula': '(재작업건/총작업건)×100%', 'cycle': '월 1회'},
        ]
    },
    'supplier_quality': {
        'name': '공급업체 품질관리 미흡',
        'kpis': [
            {'indicator': '수입검사 합격률', 'target': '97% 이상', 'formula': '(합격LOT/검사LOT)×100%', 'cycle': '월 1회'},
            {'indicator': '협력업체 정기평가 시행률', 'target': '100%', 'formula': '(평가완료업체/등록업체)×100%', 'cycle': '연 1회'},
            {'indicator': '협력업체 부적합 발생률', 'target': '전년대비 25% 감소', 'formula': '(부적합건/납품건)×100%', 'cycle': '분기 1회'},
        ]
    },
    'safety_incident': {
        'name': '안전사고 발생/위험',
        'kpis': [
            {'indicator': '산업재해율', 'target': '0건 (무재해)', 'formula': '(재해자수/근로자수)×100%', 'cycle': '월 1회'},
            {'indicator': '아차사고 보고건수', 'target': '전년대비 20% 증가(보고 활성화)', 'formula': '월간 아차사고 신고건수', 'cycle': '월 1회'},
            {'indicator': '안전교육 이수율', 'target': '100%', 'formula': '(교육이수자/대상자)×100%', 'cycle': '분기 1회'},
        ]
    },
    'env_regulation': {
        'name': '환경 규제 대응 필요',
        'kpis': [
            {'indicator': '환경법규 준수율', 'target': '100%', 'formula': '(준수항목/적용항목)×100%', 'cycle': '반기 1회'},
            {'indicator': '폐기물 감량률', 'target': '전년대비 10% 감소', 'formula': '(전년배출량-금년배출량)/전년배출량×100%', 'cycle': '분기 1회'},
            {'indicator': '환경사고 발생건수', 'target': '0건', 'formula': '환경오염사고 누적건수', 'cycle': '월 1회'},
        ]
    },
    'energy_cost': {
        'name': '에너지 비용 증가',
        'kpis': [
            {'indicator': '에너지 원단위', 'target': '전년대비 5% 절감', 'formula': '에너지사용량/생산량(or 매출)', 'cycle': '월 1회'},
            {'indicator': '온실가스 배출량', 'target': '전년대비 5% 감소', 'formula': 'Scope1+Scope2 연간 총배출량(tCO2eq)', 'cycle': '연 1회'},
        ]
    },
    'work_condition': {
        'name': '작업환경 개선 필요',
        'kpis': [
            {'indicator': '작업환경측정 적합률', 'target': '100%', 'formula': '(적합항목/측정항목)×100%', 'cycle': '반기 1회'},
            {'indicator': '위험성평가 시행률', 'target': '100%', 'formula': '(평가완료공정/대상공정)×100%', 'cycle': '연 1회'},
            {'indicator': '보호구 착용률', 'target': '100%', 'formula': '현장 순찰점검 착용률', 'cycle': '월 1회'},
        ]
    },
}

# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT v3: ISO HLS 조항번호 정렬 + 매뉴얼/절차서 분리
# ─────────────────────────────────────────────────────────────

# ── 전체(10절) 생성용 시스템 프롬프트 ──
SYSTEM_PROMPT_FULL = """당신은 30년 경력의 수석 ISO 심사원이며, 한국의 제조·건설·엔지니어링 기업의 경영시스템 구축 전문가입니다.
KAB(한국인정기구) 공인 심사원 자격을 보유하고 있으며, 500개 이상의 기업 인증 컨설팅을 수행했습니다.

## 당신의 역할
사용자가 제공한 기업 정보와 ISO 표준 요구사항 원문을 기반으로, 해당 기업에 맞춤화된 **ISO 시스템 매뉴얼(Level 1)** 초안을 작성합니다.

## ⚠️ 중요: 매뉴얼(Level 1)의 역할
매뉴얼은 회사의 **경영방침, ISO 조항별 대응 원칙, 프로세스 상호관계**를 기술하는 최상위 문서입니다.
- 각 절(Clause)에서 회사가 "무엇을(What)" 하는지의 방침과 원칙을 선언합니다.
- "어떻게(How)" 하는지의 세부 절차는 **별도 절차서(Level 2)**에서 다루므로, 매뉴얼에서는 해당 절차서를 **문서번호로 참조**만 합니다.
- 예: "교육훈련의 세부 절차는 「SMS-CP03 교육훈련 절차서」에 따른다."

## 문서 체계 (Document Hierarchy)
모든 문서는 아래의 문서번호 체계를 따릅니다:
- **SMS-M**: 통합경영매뉴얼 (본 문서)
- **SMS-CP00~07**: 공통 관리 절차서 (Common Procedures)
  - CP00: 리스크관리 | CP01: 경영계획/검토 | CP02: 조직/업무분장
  - CP03: 교육훈련 | CP04: 의사소통 | CP05: 문서화된 정보관리
  - CP06: 내부심사 | CP07: 시정조치
- **SMS-QP00~06**: 운용(품질) 절차서 (Quality Procedures)
  - QP00: 인프라관리 | QP01: 영업관리 | QP02: 설계관리
  - QP03: 구매/협력업체관리 | QP04: 생산/프로젝트관리
  - QP05: 검사 및 시험 | QP06: 부적합품 관리
- **SMS-WI**: 작업지침서 | **SMS-F**: 양식서식

## 매뉴얼 목차 구조 (ISO HLS에 맞추어 반드시 이 순서로 작성)

### 표지부
- 문서번호: SMS-M
- 제(개)정일자 / 개정번호 / 제·개정 이력표
- 경영방침 선언문 (최고경영자 명의, 서명란)

### 본문 (ISO 조항 순서)
**4. 조직 상황 (Context of the Organization)**
  4.1 조직과 조직 상황의 이해 — PESTEL 분석표 포함
  4.2 이해관계자의 니즈와 기대 이해 — 이해관계자 요구파악표 포함
  4.3 경영시스템의 적용범위 결정 — 적용범위 선언, 적용 제외 항목 및 타당성
  4.4 경영시스템과 그 프로세스 — 핵심 프로세스 상호관계도(Process Map) 포함

**5. 리더십 (Leadership)**
  5.1 리더십과 의지표명 — 최고경영자의 역할 기술
  5.2 방침 — 경영방침의 수립·전달·유지 방법
  5.3 조직의 역할, 책임 및 권한 — 조직도 + 역할·책임·권한(R&R) 매트릭스

**6. 기획 (Planning)**
  6.1 리스크와 기회를 다루는 조치 — 리스크 평가 기준표, 평가 매트릭스 포함
  6.2 목표 및 달성 기획 — 맞춤형 KPI표 포함
  6.3 변경의 기획

**7. 지원 (Support)**
  7.1 자원 (인적/물적/인프라/모니터링측정/조직지식)
  7.2 역량 (적격성) → 「SMS-CP03 교육훈련 절차서」 참조
  7.3 인식
  7.4 의사소통 → 「SMS-CP04 의사소통 관리 절차서」 참조
  7.5 문서화된 정보 → 「SMS-CP05 문서화된 정보관리 절차서」 참조

**8. 운용 (Operation)**
  8.1 운용 기획 및 관리
  8.2 제품 및 서비스 요구사항 → 「SMS-QP01 영업관리 절차서」 참조
  8.3 제품 및 서비스의 설계와 개발 → 「SMS-QP02 설계관리 절차서」 참조
  8.4 외부에서 제공되는 프로세스, 제품 및 서비스의 관리 → 「SMS-QP03 구매관리 절차서」 참조
  8.5 생산 및 서비스 제공 → 「SMS-QP04 생산관리 절차서」 참조
  8.6 제품 및 서비스의 불출(Release) → 「SMS-QP05 검사 및 시험 절차서」 참조
  8.7 부적합 출력(Output)의 관리 → 「SMS-QP06 부적합품 관리 절차서」 참조

**9. 성과 평가 (Performance Evaluation)**
  9.1 모니터링, 측정, 분석 및 평가 — 고객만족 모니터링 방법 포함
  9.2 내부심사 → 「SMS-CP06 내부심사 절차서」 참조
  9.3 경영검토(Management Review) — 경영검토 입력/출력 항목 기술

**10. 개선 (Improvement)**
  10.1 일반사항
  10.2 부적합 및 시정조치 → 「SMS-CP07 시정조치 절차서」 참조
  10.3 지속적 개선

### 부록
- 부록 A: 문서 체계표 (문서번호, 문서명, 관련 ISO 조항, 관리부서)
- 부록 B: 프로세스 상호관계도 (Turtle Diagram 또는 Process Map)

## 리스크 평가 기준표 (6절에 반드시 포함)

#### PESTEL 분석표
| 구분 | 현재 | 미래 | 기회 | 위협 |
|------|------|------|------|------|
| Political(정치적) | | | | |
| Economic(경제적) | | | | |
| Social(사회·문화적) | | | | |
| Technological(기술적) | | | | |
| Ecological(생태학적) | | | | |
| Legal(법적) | | | | |

#### 리스크 평가 매트릭스
**발생빈도:** 1=낮음(3년내 미발생), 2=보통(3년내 5건 미만), 3=높음(3년내 5건 이상)
**영향크기:** 1=낮음(±1천만 미만), 2=보통(±1천만 이상), 3=높음(브랜드 훼손)

| 발생결과\\발생가능성 | 1 | 2 | 3 |
|---------------------|---|---|---|
| 1 | L | L | M |
| 2 | L | M | H |
| 3 | M | H | H |

→ H(High): 리스크 조치계획서 연계 | M(Medium): 모니터링 강화 | L(Low): 현 수준 유지

#### 성과지표(KPI) 표 양식
| No | 성과지표(PI) | 계산식 | 모니터링 주기 | 분석/평가 방법 | 책임자 |
|----|------------|--------|-------------|-------------|-------|

## 문서 톤앤매너
- "~하여야 한다" / "~을 보장한다" / "~에 대하여 적용한다"
- ISO 표준 고유 용어: 부적합(Nonconformity), 시정조치(Corrective Action), 문서화된 정보(Documented Information), 리스크(Risk)
- 절차서 참조 시: "「SMS-CP00 리스크관리 절차서」에 따라~" 형식

## 마무리 규칙
- 부록까지 작성하고 깔끔하게 종료
- "추가로 필요하시면~", "원하시면~" 등 대화체 제안 문구 절대 포함 금지
- "다음 단계", "참고 사항" 등 별도 섹션 추가 금지
- 문서 자체로 완결되어야 함
"""

# ── Phase 1 전용: 표지 ~ 6절(기획)까지만 ──
SYSTEM_PROMPT_PHASE1 = SYSTEM_PROMPT_FULL.replace(
    """### 본문 (ISO 조항 순서)
**4. 조직 상황 (Context of the Organization)**
  4.1 조직과 조직 상황의 이해 — PESTEL 분석표 포함
  4.2 이해관계자의 니즈와 기대 이해 — 이해관계자 요구파악표 포함
  4.3 경영시스템의 적용범위 결정 — 적용범위 선언, 적용 제외 항목 및 타당성
  4.4 경영시스템과 그 프로세스 — 핵심 프로세스 상호관계도(Process Map) 포함

**5. 리더십 (Leadership)**
  5.1 리더십과 의지표명 — 최고경영자의 역할 기술
  5.2 방침 — 경영방침의 수립·전달·유지 방법
  5.3 조직의 역할, 책임 및 권한 — 조직도 + 역할·책임·권한(R&R) 매트릭스

**6. 기획 (Planning)**
  6.1 리스크와 기회를 다루는 조치 — 리스크 평가 기준표, 평가 매트릭스 포함
  6.2 목표 및 달성 기획 — 맞춤형 KPI표 포함
  6.3 변경의 기획

**7. 지원 (Support)**
  7.1 자원 (인적/물적/인프라/모니터링측정/조직지식)
  7.2 역량 (적격성) → 「SMS-CP03 교육훈련 절차서」 참조
  7.3 인식
  7.4 의사소통 → 「SMS-CP04 의사소통 관리 절차서」 참조
  7.5 문서화된 정보 → 「SMS-CP05 문서화된 정보관리 절차서」 참조

**8. 운용 (Operation)**
  8.1 운용 기획 및 관리
  8.2 제품 및 서비스 요구사항 → 「SMS-QP01 영업관리 절차서」 참조
  8.3 제품 및 서비스의 설계와 개발 → 「SMS-QP02 설계관리 절차서」 참조
  8.4 외부에서 제공되는 프로세스, 제품 및 서비스의 관리 → 「SMS-QP03 구매관리 절차서」 참조
  8.5 생산 및 서비스 제공 → 「SMS-QP04 생산관리 절차서」 참조
  8.6 제품 및 서비스의 불출(Release) → 「SMS-QP05 검사 및 시험 절차서」 참조
  8.7 부적합 출력(Output)의 관리 → 「SMS-QP06 부적합품 관리 절차서」 참조

**9. 성과 평가 (Performance Evaluation)**
  9.1 모니터링, 측정, 분석 및 평가 — 고객만족 모니터링 방법 포함
  9.2 내부심사 → 「SMS-CP06 내부심사 절차서」 참조
  9.3 경영검토(Management Review) — 경영검토 입력/출력 항목 기술

**10. 개선 (Improvement)**
  10.1 일반사항
  10.2 부적합 및 시정조치 → 「SMS-CP07 시정조치 절차서」 참조
  10.3 지속적 개선

### 부록
- 부록 A: 문서 체계표 (문서번호, 문서명, 관련 ISO 조항, 관리부서)
- 부록 B: 프로세스 상호관계도 (Turtle Diagram 또는 Process Map)""",
    """### 본문 (아래 3개 조항만 작성 — 7절 이후는 포함하지 마세요)
**4. 조직 상황 (Context of the Organization)**
  4.1 조직과 조직 상황의 이해 — PESTEL 분석표 포함
  4.2 이해관계자의 니즈와 기대 이해 — 이해관계자 요구파악표 포함
  4.3 경영시스템의 적용범위 결정 — 적용범위 선언, 적용 제외 항목 및 타당성
  4.4 경영시스템과 그 프로세스 — 핵심 프로세스 상호관계도(Process Map) 포함

**5. 리더십 (Leadership)**
  5.1 리더십과 의지표명 — 최고경영자의 역할 기술
  5.2 방침 — 경영방침의 수립·전달·유지 방법
  5.3 조직의 역할, 책임 및 권한 — 조직도 + 역할·책임·권한(R&R) 매트릭스

**6. 기획 (Planning)**
  6.1 리스크와 기회를 다루는 조치 — 리스크 평가 기준표, 평가 매트릭스 포함
  6.2 목표 및 달성 기획 — 맞춤형 KPI표 포함
  6.3 변경의 기획

※ 7절(지원)~10절(개선) 및 부록은 포함하지 마세요. 6절까지만 작성합니다."""
)

# ── Phase 2 전용: 7절(지원) + 8절(운용) — 가장 방대한 구간 ──
SYSTEM_PROMPT_PHASE2 = SYSTEM_PROMPT_FULL.replace(
    """### 표지부
- 문서번호: SMS-M
- 제(개)정일자 / 개정번호 / 제·개정 이력표
- 경영방침 선언문 (최고경영자 명의, 서명란)

### 본문 (ISO 조항 순서)
**4. 조직 상황 (Context of the Organization)**
  4.1 조직과 조직 상황의 이해 — PESTEL 분석표 포함
  4.2 이해관계자의 니즈와 기대 이해 — 이해관계자 요구파악표 포함
  4.3 경영시스템의 적용범위 결정 — 적용범위 선언, 적용 제외 항목 및 타당성
  4.4 경영시스템과 그 프로세스 — 핵심 프로세스 상호관계도(Process Map) 포함

**5. 리더십 (Leadership)**
  5.1 리더십과 의지표명 — 최고경영자의 역할 기술
  5.2 방침 — 경영방침의 수립·전달·유지 방법
  5.3 조직의 역할, 책임 및 권한 — 조직도 + 역할·책임·권한(R&R) 매트릭스

**6. 기획 (Planning)**
  6.1 리스크와 기회를 다루는 조치 — 리스크 평가 기준표, 평가 매트릭스 포함
  6.2 목표 및 달성 기획 — 맞춤형 KPI표 포함
  6.3 변경의 기획

**7. 지원 (Support)**""",
    """### 본문 (아래 2개 조항만 이어서 작성 — 표지부/4절/5절/6절은 이미 작성됨, 9절 이후는 포함하지 마세요)

**7. 지원 (Support)**"""
).replace(
    """**9. 성과 평가 (Performance Evaluation)**
  9.1 모니터링, 측정, 분석 및 평가 — 고객만족 모니터링 방법 포함
  9.2 내부심사 → 「SMS-CP06 내부심사 절차서」 참조
  9.3 경영검토(Management Review) — 경영검토 입력/출력 항목 기술

**10. 개선 (Improvement)**
  10.1 일반사항
  10.2 부적합 및 시정조치 → 「SMS-CP07 시정조치 절차서」 참조
  10.3 지속적 개선

### 부록
- 부록 A: 문서 체계표 (문서번호, 문서명, 관련 ISO 조항, 관리부서)
- 부록 B: 프로세스 상호관계도 (Turtle Diagram 또는 Process Map)""",
    """※ 9절(성과 평가)~10절(개선) 및 부록은 포함하지 마세요. 8절까지만 작성합니다."""
)

# ── Phase 3 전용: 9절(성과평가) + 10절(개선) + 부록 ──
SYSTEM_PROMPT_PHASE3 = SYSTEM_PROMPT_FULL.replace(
    """### 표지부
- 문서번호: SMS-M
- 제(개)정일자 / 개정번호 / 제·개정 이력표
- 경영방침 선언문 (최고경영자 명의, 서명란)

### 본문 (ISO 조항 순서)
**4. 조직 상황 (Context of the Organization)**
  4.1 조직과 조직 상황의 이해 — PESTEL 분석표 포함
  4.2 이해관계자의 니즈와 기대 이해 — 이해관계자 요구파악표 포함
  4.3 경영시스템의 적용범위 결정 — 적용범위 선언, 적용 제외 항목 및 타당성
  4.4 경영시스템과 그 프로세스 — 핵심 프로세스 상호관계도(Process Map) 포함

**5. 리더십 (Leadership)**
  5.1 리더십과 의지표명 — 최고경영자의 역할 기술
  5.2 방침 — 경영방침의 수립·전달·유지 방법
  5.3 조직의 역할, 책임 및 권한 — 조직도 + 역할·책임·권한(R&R) 매트릭스

**6. 기획 (Planning)**
  6.1 리스크와 기회를 다루는 조치 — 리스크 평가 기준표, 평가 매트릭스 포함
  6.2 목표 및 달성 기획 — 맞춤형 KPI표 포함
  6.3 변경의 기획

**7. 지원 (Support)**
  7.1 자원 (인적/물적/인프라/모니터링측정/조직지식)
  7.2 역량 (적격성) → 「SMS-CP03 교육훈련 절차서」 참조
  7.3 인식
  7.4 의사소통 → 「SMS-CP04 의사소통 관리 절차서」 참조
  7.5 문서화된 정보 → 「SMS-CP05 문서화된 정보관리 절차서」 참조

**8. 운용 (Operation)**
  8.1 운용 기획 및 관리
  8.2 제품 및 서비스 요구사항 → 「SMS-QP01 영업관리 절차서」 참조
  8.3 제품 및 서비스의 설계와 개발 → 「SMS-QP02 설계관리 절차서」 참조
  8.4 외부에서 제공되는 프로세스, 제품 및 서비스의 관리 → 「SMS-QP03 구매관리 절차서」 참조
  8.5 생산 및 서비스 제공 → 「SMS-QP04 생산관리 절차서」 참조
  8.6 제품 및 서비스의 불출(Release) → 「SMS-QP05 검사 및 시험 절차서」 참조
  8.7 부적합 출력(Output)의 관리 → 「SMS-QP06 부적합품 관리 절차서」 참조

**9. 성과 평가 (Performance Evaluation)**""",
    """### 본문 (아래 2개 조항 + 부록만 이어서 작성 — 표지부/4~8절은 이미 작성됨)

**9. 성과 평가 (Performance Evaluation)**"""
)


def _load_iso_standard_text(standard_code):
    """ISO 표준 요구사항 텍스트를 파일에서 로드 (본문 4.1절~10절)"""
    # api/services/ → api/ → project root
    standards_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'data', 'iso_standards'
    )
    
    info = ISO_STANDARD_INFO.get(standard_code)
    if not info:
        return None
    
    filepath = os.path.join(standards_dir, info["file"])
    if not os.path.exists(filepath):
        return None
    
    with open(filepath, 'r', encoding='utf-8') as f:
        full_text = f.read()
    
    # 본문의 4.1절 시작점을 찾음 (목차의 점선 패턴이 아닌 실제 본문)
    body_start_markers = [
        "4.1  조직과 조직상황",
        "4.1 조직과 조직상황",
        "4.1  일반사항",
        "4.1 일반사항",
        "4.1  조직 및",
    ]
    
    start_idx = -1
    for marker in body_start_markers:
        idx = full_text.find(marker)
        if idx >= 0:
            start_idx = idx
            break
    
    # 부속서 위치를 찾되, 본문 시작 이후의 것을 사용
    end_markers = ["부속서 A", "부속서A", "Annex A"]
    end_idx = -1
    search_from = start_idx if start_idx >= 0 else 0
    for marker in end_markers:
        idx = full_text.find(marker, search_from)
        if idx >= 0:
            end_idx = idx
            break
    
    if start_idx >= 0 and end_idx >= 0 and end_idx > start_idx:
        text = full_text[start_idx:end_idx].strip()
    elif start_idx >= 0:
        text = full_text[start_idx:].strip()
    else:
        text = full_text
    
    # PDF 추출 아티팩트 제거
    text = re.sub(r'--- Page \d+ ---', '', text)
    text = re.sub(r'INSIDabcdef_:MS_\d+MS_\d+', '', text)
    text = re.sub(r'KS Q ISO \d+:\d+', '', text)
    text = re.sub(r'LRQA 교육용', '', text)
    text = re.sub(r'로이드인증원.*?금합니다\.?', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def _build_kpi_section(issues):
    """사용자가 선택한 경영 이슈를 기반으로 맞춤 KPI 마크다운 생성"""
    if not issues:
        return ""
    
    kpi_lines = [
        "\n## 사용자 기업 맞춤형 품질/환경/안전보건 목표 (KPI)",
        "아래 KPI를 매뉴얼 6절(기획)의 '목표 및 달성 기획'에 반드시 포함하세요:",
        "",
        "| No | 경영 이슈 | 성과지표(PI) | 목표치 | 계산식 | 모니터링 주기 |",
        "|-----|----------|------------|--------|--------|------------|",
    ]
    
    no = 1
    for issue in issues:
        issue_id = issue.get('id', issue) if isinstance(issue, dict) else issue
        mapping = ISSUE_KPI_MAP.get(issue_id)
        if not mapping:
            continue
        for kpi in mapping['kpis']:
            kpi_lines.append(
                f"| {no} | {mapping['name']} | {kpi['indicator']} | {kpi['target']} | {kpi['formula']} | {kpi['cycle']} |"
            )
            no += 1
    
    if no == 1:
        return ""  # 매핑되는 이슈가 없으면 빈 문자열
    
    kpi_lines.append("")
    kpi_lines.append("위 KPI를 기반으로 각 부서별 세부 실행계획(담당자, 일정, 소요자원)도 함께 작성하세요.")
    return "\n".join(kpi_lines)


def _build_procedure_reference_table():
    """문서 체계표 (부록 A용) — 매뉴얼에서 참조하는 절차서 목록"""
    lines = [
        "\n## 참조 절차서 목록 (본 매뉴얼에서 참조하는 Level 2 문서)",
        "",
        "### 공통 관리 절차서 (SMS-CP)",
        "| 문서번호 | 문서명 | 관련 ISO 조항 |",
        "|---------|--------|-------------|",
    ]
    for proc in PROCEDURE_LIST['common']:
        lines.append(f"| SMS-{proc['id']} | {proc['name']} | {proc['iso_clause']} |")
    
    lines.extend([
        "",
        "### 운용(품질) 절차서 (SMS-QP)",
        "| 문서번호 | 문서명 | 관련 ISO 조항 |",
        "|---------|--------|-------------|",
    ])
    for proc in PROCEDURE_LIST['operation']:
        lines.append(f"| SMS-{proc['id']} | {proc['name']} | {proc['iso_clause']} |")
    
    lines.append("")
    return "\n".join(lines)


def _build_user_prompt(form_data, iso_standard_text):
    """사용자 폼 데이터와 ISO 표준 텍스트로 User Prompt 구성"""
    industry = form_data.get('industry', '제조업')
    main_product = form_data.get('main_product', '')
    employees = form_data.get('employees', '50~100명')
    target_iso = form_data.get('target_iso', 'ISO 9001:2015')
    reasons = form_data.get('reasons', [])
    issues = form_data.get('issues', [])
    custom_issue = form_data.get('custom_issue', '')
    company_name = form_data.get('company_name', '(주)OOO')
    
    issues_text = ""
    if issues:
        issue_names = {
            'quality_defect': '품질 불량/불량률 증가',
            'customer_complaint': '고객 클레임 증가',
            'process_inefficiency': '프로세스 비효율',
            'supplier_quality': '공급업체 품질관리 미흡',
            'safety_incident': '안전사고 발생/위험',
            'env_regulation': '환경 규제 대응 필요',
            'energy_cost': '에너지 비용 증가',
            'work_condition': '작업환경 개선 필요',
        }
        issues_list = [issue_names.get(i.get('id', i) if isinstance(i, dict) else i, str(i)) for i in issues[:8]]
        issues_text = "\n".join(f"  - {iss}" for iss in issues_list)
    
    reasons_text = ", ".join(reasons) if reasons else "고객사 요구"
    
    # 맞춤 KPI 섹션 생성
    kpi_section = _build_kpi_section(issues)
    
    # 절차서 참조 테이블
    proc_ref = _build_procedure_reference_table()
    
    prompt = f"""## 기업 정보
- 회사명: {company_name}
- 업종: {industry}
- 주요 생산품/서비스: {main_product if main_product else '미입력'}
- 규모: {employees}
- 타깃 인증: {target_iso}
- 인증 추진 배경: {reasons_text}

## 경영 현황 이슈
{issues_text}

## 현장 상세 설명 (사용자 직접 입력)
{custom_issue if custom_issue else '특이사항 없음'}
{kpi_section}
{proc_ref}

## ISO 표준 요구사항 원문 ({target_iso})
아래의 ISO 표준 요구사항을 기반으로 위 기업에 맞춤화된 시스템 매뉴얼을 작성해주세요.
특히 8절(운용)에서는 '{main_product if main_product else industry}'의 핵심 프로세스를 고려하여,
각 하위 절차서(QP01~QP06)의 적용 방침을 구체적으로 기술하세요.

---
{iso_standard_text[:80000] if iso_standard_text else '(표준 원문 미제공 - 일반 지식 기반으로 작성)'}
---
"""
    return prompt


def generate_iso_manual_stream(form_data):
    """
    ISO 매뉴얼을 SSE 스트리밍으로 생성하는 제너레이터.
    
    3-Phase 분할 아키텍처 (총 60,000 토큰):
      - Phase 1 (max_sections=5): 표지 ~ 6절(기획)   → 20,000 tokens
      - Phase 2 (continue_from=7): 7절(지원) + 8절(운용)  → 20,000 tokens
      - Phase 3 (continue_from=9): 9절(성과) + 10절(개선) + 부록 → 20,000 tokens
      - 각 Phase ≈ 20K tokens → Vercel 60초 제한에 안정적
    """
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        yield "data: [ERROR] OPENAI_API_KEY가 설정되지 않았습니다.\n\n"
        yield "data: [DONE]\n\n"
        return
    
    max_sections = form_data.get('max_sections', None)
    continue_from = form_data.get('continue_from', None)
    target_iso = form_data.get('target_iso', 'ISO 9001:2015')
    iso_text = _load_iso_standard_text(target_iso)
    
    # Phase 번호 결정
    if continue_from and int(continue_from) >= 9:
        current_phase = 3
    elif continue_from:
        current_phase = 2
    elif max_sections == 5:
        current_phase = 1
    else:
        current_phase = 0  # 전체 생성 (로컬)
    
    print(f"[ISO Manual v3] Loaded {len(iso_text) if iso_text else 0} chars of {target_iso}")
    print(f"[ISO Manual v3] Phase: {current_phase}, continue_from: {continue_from}, max_sections: {max_sections}")
    
    user_prompt = _build_user_prompt(form_data, iso_text)
    print(f"[ISO Manual v3] User prompt: {len(user_prompt)} chars")
    
    # ── 프롬프트 분기: Phase 1 / Phase 2 / Phase 3 / Full ──
    # 총 60,000 토큰 = 20K + 20K + 20K
    if current_phase == 3:
        # Phase 3: 9절(성과평가) + 10절(개선) + 부록
        system_prompt = SYSTEM_PROMPT_PHASE3
        user_prompt += "\n\n## 중요 지시사항\n표지부, 4~8절은 이미 작성 완료되었습니다.\n**9절(성과 평가)부터 바로 이어서 작성**해주세요. 표지나 서두 없이 바로 \"## 9. 성과 평가 (Performance Evaluation)\"로 시작합니다."
        max_tokens = 20000
    elif current_phase == 2:
        # Phase 2: 7절(지원) + 8절(운용)
        system_prompt = SYSTEM_PROMPT_PHASE2
        user_prompt += "\n\n## 중요 지시사항\n표지부, 4절(조직 상황), 5절(리더십), 6절(기획)은 이미 작성 완료되었습니다.\n**7절(지원)부터 바로 이어서 작성**해주세요. 표지나 서두 없이 바로 \"## 7. 지원 (Support)\"로 시작합니다.\n9절 이후는 작성하지 마세요. 8절까지만 작성합니다."
        max_tokens = 20000
    elif current_phase == 1:
        # Phase 1: 표지 ~ 6절
        system_prompt = SYSTEM_PROMPT_PHASE1
        max_tokens = 20000
    else:
        # 전체 생성 (로컬 테스트용)
        system_prompt = SYSTEM_PROMPT_FULL
        max_tokens = 60000
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": "gpt-4.1",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": True,
        "temperature": 0.3,
        "max_completion_tokens": max_tokens,
    }
    
    try:
        response = http_requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            stream=True,
            timeout=300,
        )
        
        if response.status_code != 200:
            error_body = response.text[:500]
            print(f"[ISO Manual v3] API Error {response.status_code}: {error_body}")
            yield f"data: [ERROR] OpenAI API 오류 ({response.status_code})\n\n"
            yield "data: [DONE]\n\n"
            return
        
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        escaped = content.replace('\n', '\\n').replace('\r', '')
                        yield f"data: {escaped}\n\n"
                except json.JSONDecodeError:
                    continue
        
        # Phase 완료 시그널
        if current_phase > 0:
            yield f"data: [PHASE_COMPLETE:{current_phase}]\n\n"
        
        yield "data: [DONE]\n\n"
        print(f"[ISO Manual v3] Generation completed (Phase {current_phase})")
        
    except http_requests.exceptions.Timeout:
        print("[ISO Manual v3] Request timeout")
        yield "data: [ERROR] AI 생성 시간이 초과되었습니다.\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        error_msg = str(e)
        print(f"[ISO Manual v3] Error: {error_msg}")
        yield f"data: [ERROR] {error_msg[:200]}\n\n"
        yield "data: [DONE]\n\n"


# ─────────────────────────────────────────────────────────────
# 개별 절차서 생성 (향후 유료 기능 — 멀티에이전트 확장용)
# ─────────────────────────────────────────────────────────────

# 절차서 생성용 시스템 프롬프트 템플릿 (향후 활성화)
PROCEDURE_SYSTEM_PROMPT_TEMPLATE = """당신은 30년 경력의 수석 ISO 심사원입니다.
사용자가 제공한 기업 정보를 기반으로, 아래 절차서를 작성합니다.

## 작성할 절차서
- 문서번호: SMS-{proc_id}
- 문서명: {proc_name}
- 관련 ISO 조항: {iso_clause}

## 절차서 필수 구조 (반드시 이 순서로 작성)
```
[표지]
■ 문서번호: SMS-{proc_id}
■ 제(개)정일자: YYYY.MM.DD
■ 개정번호: 0

[제·개정 이력]
| Rev | 개정일자 | 개정 사유 및 내용 | 작성자 | 검토자 | 승인자 |

[본문]
1. 목적 및 적용범위
2. 용어의 정의
3. 책임과 권한
4. 업무절차
   4.1 업무흐름도 (플로우차트)
   4.2 업무절차 상세
5. 주요 성과지표 (KPI)
6. 관련 문서 (상호참조)
7. 기록관리
   | No | 기록명 | 양식번호 | 기록매체 | 최소보유기간 | 보유부서 |
```

## 문서 톤앤매너
- "~하여야 한다" / "~을 보장한다"
- 4.2의 업무절차 상세는 Step-by-Step으로 번호를 매겨 작성
- 업무흐름도는 마크다운 표 또는 텍스트 플로우차트로 표현
"""


def generate_procedure_stream(form_data, proc_type, proc_id):
    """
    개별 절차서를 SSE 스트리밍으로 생성하는 제너레이터.
    (향후 유료 기능으로 활성화 예정)
    
    Args:
        form_data: 기업 정보 딕셔너리
        proc_type: 'common' 또는 'operation'
        proc_id: 절차서 ID (예: 'CP03', 'QP01')
    """
    # 절차서 정보 조회
    proc_list = PROCEDURE_LIST.get(proc_type, [])
    proc_info = next((p for p in proc_list if p['id'] == proc_id), None)
    
    if not proc_info:
        yield f"data: [ERROR] 존재하지 않는 절차서 ID: {proc_id}\n\n"
        yield "data: [DONE]\n\n"
        return
    
    # TODO: 결제 확인 로직
    # TODO: 시스템 프롬프트 조립 + OpenAI 호출
    # TODO: SSE 스트리밍 반환
    
    yield f"data: [ERROR] 절차서 생성 기능은 준비 중입니다. (SMS-{proc_id} {proc_info['name']})\n\n"
    yield "data: [DONE]\n\n"
