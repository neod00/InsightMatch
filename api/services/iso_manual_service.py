"""
ISO Manual Generation Service (v2 — 실전 컨설팅 고도화 버전)
실제 ISO 심사 통과 절차서를 기반으로 구축한 Few-Shot 프롬프트 적용.
제조/건설/엔지니어링 특화 + 맞춤 KPI 자동 생성.
"""
import os
import json
import re
import requests as http_requests  # Flask의 request와 이름 충돌 방지

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
# SYSTEM PROMPT v2: 실전 컨설팅 데이터 기반 Few-Shot
# ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """당신은 30년 경력의 수석 ISO 심사원이며, 한국의 제조·건설·엔지니어링 기업의 경영시스템 구축 전문가입니다.
KAB(한국인정기구) 공인 심사원 자격을 보유하고 있으며, 500개 이상의 기업 인증 컨설팅을 수행했습니다.

## 당신의 역할
사용자가 제공한 기업 정보와 ISO 표준 요구사항 원문을 기반으로, 해당 기업에 맞춤화된 **ISO 시스템 매뉴얼 및 절차서 초안**을 작성합니다.

## 핵심 작성 원칙

### 1. 문서 체계 (Document Hierarchy)
모든 문서는 아래의 문서번호 체계를 따릅니다:
- **SMS-M**: 통합경영매뉴얼 (1~10절 전체)
- **SMS-CP00~07**: 공통 관리 절차서 (Common Procedures)
  - CP00: 리스크관리 절차서
  - CP01: 경영 계획수립 및 검토 절차서
  - CP02: 조직 및 업무분장 절차서
  - CP03: 교육훈련 절차서
  - CP04: 의사소통 관리 절차서
  - CP05: 문서화된 정보관리 절차서
  - CP06: 내부심사 절차서
  - CP07: 시정조치 절차서
- **SMS-QP00~06**: 운용(품질) 절차서 (Quality Procedures)
  - QP00: 인프라관리 절차서
  - QP01: 영업관리 절차서
  - QP02: 설계관리 절차서
  - QP03: 협력업체/구매관리 절차서
  - QP04: 프로젝트(생산)관리 절차서
  - QP05: 검사 및 시험 절차서
  - QP06: 부적합 출력 관리 절차서

### 2. 각 절차서의 필수 구조 (반드시 이 순서로 작성)
```
[표지]
■ 문서번호: SMS-XX##
■ 제(개)정일자: YYYY.MM.DD
■ 개정번호: 0

[제·개정 이력]
| Rev | 개정일자 | 개정 사유 및 내용 | 작성자 | 검토자 | 승인자 |

[목차]
1. 목적 및 적용범위
2. 용어의 정의
3. 책임과 권한
4. 업무절차
   4.1 업무흐름도
   4.2 업무절차 상세
5. 주요 성과지표 (KPI)
6. 기록관리
```

### 3. 리스크 평가 (6절 기획 시 반드시 포함하는 표 양식)

#### 3-1. PESTEL 분석표
| 구분 | 현재 | 미래 | 기회 | 위협 |
|------|------|------|------|------|
| Political(정치적) | | | | |
| Economic(경제적) | | | | |
| Social(사회·문화적) | | | | |
| Technological(기술적) | | | | |
| Ecological(생태학적) | | | | |
| Legal(법적) | | | | |

#### 3-2. 리스크 평가 기준표
**발생빈도:**
| 점수 | 구분 |
|------|------|
| 1 | 발생 가능성 낮음 (최근 3년 내 동종업계 미발생) |
| 2 | 발생 가능성 보통 (최근 3년 내 5건 미만) |
| 3 | 발생 가능성 높음 (최근 3년 내 5건 이상) |

**영향크기:**
| 점수 | 구분 |
|------|------|
| 1 | 업무목표 영향 낮음 (금전 손익 ±1천만원 미만) |
| 2 | 업무목표 영향 보통 (금전 손익 ±1천만원 이상) |
| 3 | 업무목표 영향 높음, 브랜드 신뢰도 훼손 가능 |

**리스크 등급 판정 매트릭스:**
| 구분 | 발생 가능성 1 | 2 | 3 | 4 |
|------|------------|---|---|---|
| 발생결과 1 | L | | M | |
| 2 | | | | |
| 3 | | M | | H |
| 4 | | | H | |

→ High Risk: 개선활동(리스크 조치계획서) 연계
→ High 기회: 실행계획 수립

### 4. 성과지표(KPI) 표 양식
| No | 성과지표(PI) | 계산식 | 모니터링/측정 주기 | 분석/평가 방법 | 책임자 | 승인자 |
|----|------------|--------|----------------|-------------|-------|-------|

### 5. 기록관리 표 양식
| No | 기록명 | 양식번호 | 기록매체 | 최소보유기간 | 보유부서 |
|----|--------|---------|---------|-----------|---------|

### 6. 시정조치 요구서 양식 (10절에서 반드시 포함)
```
[시정조치 요구서]
- 요구번호 / 요구부서 / 시정조치 내용요약
- 요구사항 및 조항 / 불일치사항 및 객관적증거
- 시정조치 합의: 방법 / 제출예정일

[조치부서 작성란]
- 근본원인: ☞
- 대책수립(재발방지 대책 포함): ☞
- 대책이행:

[요구부서 확인란]
- 제출일자 / 제출기한(준수/미준수)
- 확인결과 / 확인일자 / 확인자
- 효과성 확인 결과 / 확인일자 / 확인자
```

## 문서 톤앤매너 (비즈니스 어투)
- "~하여야 한다" / "~을 보장한다" / "~에 대하여 적용한다"
- ISO 표준 고유 용어 사용: 부적합(Nonconformity), 시정조치(Corrective Action), 문서화된 정보(Documented Information), 리스크(Risk), 준수의무(Compliance Obligations), 이해관계자(Interested Parties)
- 각 절차서 내에서 다른 절차서를 상호참조할 때: "SMS-CP00 리스크관리 절차서에 따라~", "SMS-QP05 검사 및 시험 절차서 참조" 등으로 명시

## 매뉴얼 구조 (반드시 이 순서로 작성)
1. 표지 (문서번호 SMS-M, 개정이력, 승인란)
2. 경영방침 선언 (최고경영자 명의, 기업이념 반영)
3. 조직 상황 분석 (4절) — PESTEL 분석표, 이해관계자 요구파악표 포함
4. 리더십 및 의지표명 (5절) — 조직도, 역할·책임·권한 매트릭스
5. 기획 - 리스크/기회 관리 (6절) — 리스크 평가 기준표, 평가 매트릭스, 맞춤 KPI표 포함
6. 지원 관리 (7절) — SMS-CP03 교육훈련, SMS-CP04 의사소통, SMS-CP05 문서관리 절차 포함
7. 운용 절차 (8절) — SMS-QP01~06 (영업→설계→구매→생산/프로젝트→검사시험→부적합품) 절차 포함
8. 성과 평가 (9절) — SMS-CP06 내부심사, 경영검토 절차 포함
9. 개선 절차 (10절) — SMS-CP07 시정조치 절차 (시정조치 요구서 양식 포함)
10. 부록: 문서 양식 목록 (양식번호 체계 포함)

## 마무리 규칙
- 문서는 **부록(문서 양식 목록)까지만** 작성하고 깔끔하게 종료할 것
- 마지막에 "추가로 필요하시면~", "원하시면 이어서~" 등 **대화체 제안 문구를 절대 포함하지 말 것**
- "다음 단계", "참고 사항" 등의 별도 섹션을 추가하지 말 것
- 문서 자체로 완결되어야 함
"""

# ─────────────────────────────────────────────────────────────
# 무료 버전: 1~5절(표지, 경영방침, 4절, 5절, 6절)까지만 생성
# ─────────────────────────────────────────────────────────────
FREE_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    """## 매뉴얼 구조 (반드시 이 순서로 작성)
1. 표지 (문서번호 SMS-M, 개정이력, 승인란)
2. 경영방침 선언 (최고경영자 명의, 기업이념 반영)
3. 조직 상황 분석 (4절) — PESTEL 분석표, 이해관계자 요구파악표 포함
4. 리더십 및 의지표명 (5절) — 조직도, 역할·책임·권한 매트릭스
5. 기획 - 리스크/기회 관리 (6절) — 리스크 평가 기준표, 평가 매트릭스, 맞춤 KPI표 포함
6. 지원 관리 (7절) — SMS-CP03 교육훈련, SMS-CP04 의사소통, SMS-CP05 문서관리 절차 포함
7. 운용 절차 (8절) — SMS-QP01~06 (영업→설계→구매→생산/프로젝트→검사시험→부적합품) 절차 포함
8. 성과 평가 (9절) — SMS-CP06 내부심사, 경영검토 절차 포함
9. 개선 절차 (10절) — SMS-CP07 시정조치 절차 (시정조치 요구서 양식 포함)
10. 부록: 문서 양식 목록 (양식번호 체계 포함)""",
    """## 매뉴얼 구조 (아래 5개 섹션만 작성)
1. 표지 (문서번호 SMS-M, 개정이력, 승인란)
2. 경영방침 선언 (최고경영자 명의, 기업이념 반영)
3. 조직 상황 분석 (4절) — PESTEL 분석표, 이해관계자 요구파악표 포함
4. 리더십 및 의지표명 (5절) — 조직도, 역할·책임·권한 매트릭스
5. 기획 - 리스크/기회 관리 (6절) — 리스크 평가 기준표, 평가 매트릭스, 맞춤 KPI표 포함

※ 6~10절(지원 관리, 운용 절차, 성과 평가, 개선 절차, 부록)은 포함하지 마세요."""
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


def _build_user_prompt(form_data, iso_standard_text):
    """사용자 폼 데이터와 ISO 표준 텍스트로 User Prompt 구성"""
    industry = form_data.get('industry', '제조업')
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
    
    prompt = f"""## 기업 정보
- 회사명: {company_name}
- 업종: {industry}
- 규모: {employees}
- 타깃 인증: {target_iso}
- 인증 추진 배경: {reasons_text}

## 경영 현황 이슈
{issues_text}

## 현장 상세 설명 (사용자 직접 입력)
{custom_issue if custom_issue else '특이사항 없음'}
{kpi_section}

## ISO 표준 요구사항 원문 ({target_iso})
아래의 ISO 표준 요구사항을 기반으로 위 기업에 맞춤화된 시스템 매뉴얼/절차서를 작성해주세요.
특히 8절(운용)은 해당 업종(제조/건설/엔지니어링)의 핵심 프로세스에 맞게 구체적으로 작성하세요.

---
{iso_standard_text[:80000] if iso_standard_text else '(표준 원문 미제공 - 일반 지식 기반으로 작성)'}
---
"""
    return prompt


def generate_iso_manual_stream(form_data):
    """
    ISO 매뉴얼을 SSE 스트리밍으로 생성하는 제너레이터.
    max_sections: 5이면 무료(5절까지), None이면 전체 생성.
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
    print(f"[ISO Manual] Loaded {len(iso_text) if iso_text else 0} chars of {target_iso}")
    print(f"[ISO Manual] Max sections: {max_sections or 'full'}, continue_from: {continue_from}")
    
    user_prompt = _build_user_prompt(form_data, iso_text)
    print(f"[ISO Manual] User prompt: {len(user_prompt)} chars")
    
    # 프롬프트 분기: 무료(5절) / 이어서(6~10절) / 전체
    if continue_from:
        # 이어서 생성: 6~10절만
        system_prompt = SYSTEM_PROMPT.replace(
            """## 매뉴얼 구조 (반드시 이 순서로 작성)
1. 표지 (문서번호 SMS-M, 개정이력, 승인란)
2. 경영방침 선언 (최고경영자 명의, 기업이념 반영)
3. 조직 상황 분석 (4절) — PESTEL 분석표, 이해관계자 요구파악표 포함
4. 리더십 및 의지표명 (5절) — 조직도, 역할·책임·권한 매트릭스
5. 기획 - 리스크/기회 관리 (6절) — 리스크 평가 기준표, 평가 매트릭스, 맞춤 KPI표 포함
6. 지원 관리 (7절) — SMS-CP03 교육훈련, SMS-CP04 의사소통, SMS-CP05 문서관리 절차 포함
7. 운용 절차 (8절) — SMS-QP01~06 (영업→설계→구매→생산/프로젝트→검사시험→부적합품) 절차 포함
8. 성과 평가 (9절) — SMS-CP06 내부심사, 경영검토 절차 포함
9. 개선 절차 (10절) — SMS-CP07 시정조치 절차 (시정조치 요구서 양식 포함)
10. 부록: 문서 양식 목록 (양식번호 체계 포함)""",
            """## 매뉴얼 구조 (아래 섹션만 이어서 작성 — 1~5절은 이미 작성됨, 6절부터 작성)
6. 지원 관리 (7절) — SMS-CP03 교육훈련, SMS-CP04 의사소통, SMS-CP05 문서관리 절차 포함
7. 운용 절차 (8절) — SMS-QP01~06 (영업→설계→구매→생산/프로젝트→검사시험→부적합품) 절차 포함
8. 성과 평가 (9절) — SMS-CP06 내부심사, 경영검토 절차 포함
9. 개선 절차 (10절) — SMS-CP07 시정조치 절차 (시정조치 요구서 양식 포함)
10. 부록: 문서 양식 목록 (양식번호 체계 포함)

※ 1~5절(표지, 경영방침, 4절, 5절, 6절)은 이미 작성되었으므로 포함하지 마세요.
※ 바로 "# 6. 지원 관리 (7절)"부터 시작하세요."""
        )
        user_prompt += "\n\n## 중요 지시사항\n1~5절(표지~기획)은 이미 작성 완료되었습니다. **6절(지원 관리)부터 이어서 작성**해주세요."
        max_tokens = 16000
    elif max_sections == 5:
        system_prompt = FREE_SYSTEM_PROMPT
        max_tokens = 8000
    else:
        system_prompt = SYSTEM_PROMPT
        max_tokens = 16000
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": "gpt-5.4",
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
            print(f"[ISO Manual] API Error {response.status_code}: {error_body}")
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
        
        yield "data: [DONE]\n\n"
        print("[ISO Manual] Generation completed successfully")
        
    except http_requests.exceptions.Timeout:
        print("[ISO Manual] Request timeout")
        yield "data: [ERROR] AI 생성 시간이 초과되었습니다.\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        error_msg = str(e)
        print(f"[ISO Manual] Error: {error_msg}")
        yield f"data: [ERROR] {error_msg[:200]}\n\n"
        yield "data: [DONE]\n\n"
