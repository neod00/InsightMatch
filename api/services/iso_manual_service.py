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

v4 (AI 매뉴얼 생성기 개선 1단계, PRD T4):
  - 표준 원문 로더와 고정 시스템 프롬프트(.replace() 파생)를 없애고, 규격 JSON(api/standards)과
    병합 outline 으로 프롬프트를 조립한다 → `manual_prompt_builder`.
  - target_isos(1~3개, edition 포함) 지원, target_iso 문자열 하위호환.
  - 규격 데이터 검증 실패·선택 오류 시 LLM 을 부르지 않고 [ERROR] 로 닫는다(FR-05).
  - 절차서 목록·메타는 JSON `procedures` 가 단일 출처다(하드코딩 사본 제거, FR-06).
"""
import os
import json
import time
import requests as http_requests  # Flask의 request와 이름 충돌 방지

from services import standards_loader as sl
from services import manual_prompt_builder as mpb
from services import manual_outline as mo
# 하위호환 재노출: 기존 테스트·코드가 이 모듈에서 import 한다.
from services.manual_prompt_builder import _sanitize_user_field, ISSUE_KPI_MAP  # noqa: F401

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

# ─────────────────────────────────────────────────────────────
# 규격 데이터 검증 (FR-05: 앱 시작 시 + 생성 요청마다, 실패 시 닫힘)
# ─────────────────────────────────────────────────────────────
STANDARDS_UNAVAILABLE = 'STANDARDS_UNAVAILABLE'
_MSG_STANDARDS_UNAVAILABLE = f'매뉴얼 기준 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요. ({STANDARDS_UNAVAILABLE})'
_MSG_SELECTION_INVALID = '선택한 인증 규격으로 매뉴얼을 만들 수 없습니다. 규격 선택을 확인해주세요.'
_MSG_PROMPT_FAILED = '매뉴얼 생성 준비 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.'
_DEFAULT_TARGET_ISO = 'ISO 9001:2015'   # target_isos·target_iso 가 모두 없을 때(현행 기본값 유지)


def _validate_standards(loader=None):
    """모든 (규격, edition) JSON 과 hls_common 을 검증한다. 실패하면 StandardsDataError."""
    return (loader or sl).load_all()


def _startup_check():
    """모듈 import(앱 시작) 시 1회 검증. 실패해도 앱 전체를 죽이지 않고 로그만 남긴다.

    생성 요청 때 `_validate_standards()` 를 다시 부르므로(실패는 캐시되지 않음) 그때 [ERROR] 로 닫힌다.
    """
    try:
        catalog = _validate_standards()
    except sl.StandardsDataError as exc:
        print(f"[ISO Manual] STANDARDS_UNAVAILABLE at startup: {exc}")
        return exc
    except Exception as exc:  # 예기치 못한 오류도 앱 기동은 막지 않는다
        print(f"[ISO Manual] standards startup check failed: {exc!r}")
        return exc
    summary = ", ".join(f"{k} {e} v{d.schema_version} ({len(d.clauses)} clauses)"
                        for (k, e), d in catalog.standards.items())
    print(f"[ISO Manual] standards loaded: hls_common v{catalog.hls.schema_version}; {summary}")
    return None


STANDARDS_STARTUP_ERROR = _startup_check()


def _sse(text):
    return f"data: {text}\n\n"


def _sse_content(text):
    """내용 토큰 SSE (현행 형식: 개행은 리터럴 \\n 으로)."""
    return _sse(text.replace('\n', '\\n').replace('\r', ''))


def _resolve_phase(form_data):
    """현행 phase 결정 규칙 (index.py 계약). 0 = 전체 생성(로컬)."""
    max_sections = form_data.get('max_sections', None)
    continue_from = form_data.get('continue_from', None)
    if continue_from and int(continue_from) >= 9:
        return 3
    if continue_from:
        return 2
    if max_sections == 5:
        return 1
    return mpb.PHASE_FULL


def _resolve_selections(form_data, loader=None):
    """form_data 의 target_isos(우선) 또는 target_iso(하위호환)를 정규화한다.

    세션 생성(index.py, T5)에서 이미 검증된 값이지만, 저장 후 공개 게이트가 닫혔을 수 있으므로
    노출 게이트를 다시 적용한다(닫힌 edition 이면 생성하지 않음 = fail closed).
    """
    target_isos = form_data.get('target_isos')
    target_iso = form_data.get('target_iso')
    if target_isos is None and not target_iso:
        target_iso = _DEFAULT_TARGET_ISO
    return (loader or sl).resolve_manual_standards(target_isos=target_isos, target_iso=target_iso)


def _describe_selection(selections, loader=None):
    """FR-62 로그용: 'iso9001 2015 v1.2 (83 clauses)'."""
    get_std = loader.get_standard if loader else sl.get_standard
    parts = []
    for s in selections:
        d = get_std(s.standard_key, s.edition)
        parts.append(f"{s.standard_key} {s.edition} v{d.schema_version} ({len(d.clauses)} clauses)")
    return "; ".join(parts)


def _log_unverified(phase, unverified):
    """FR-81 (b): 이번 프롬프트에 들어간 carried/toc_only 조항 경고."""
    for code, kinds in unverified.items():
        for kind, ids in kinds.items():
            print(f"[ISO Manual] WARNING phase={phase} {code} {kind} clauses in prompt: {', '.join(ids)}")


def generate_iso_manual_stream(form_data, loader=None):
    """
    ISO 매뉴얼을 SSE 스트리밍으로 생성하는 제너레이터.

    3-Phase 분할 (phase 번호는 form_data 의 max_sections/continue_from 으로 정한다 — index.py 계약):
      - Phase 1 (max_sections=5): 표지 ~ 6절(기획)
      - Phase 2 (continue_from=7): 7절(지원) + 8절(운용)
      - Phase 3 (continue_from=9): 9절(성과) + 10절(개선) + 부록 A·B (+ 서버가 붙이는 부록 C)
    프롬프트는 `manual_prompt_builder` 가 규격 JSON 과 병합 outline 으로 조립한다(원문 미사용).

    SSE 계약(변경 없음): 내용 `data: <텍스트(개행은 \\n)>`, 성공 시 `data: [PHASE_COMPLETE:N]`(phase 1~3),
    마지막 `data: [DONE]`. 실패는 `data: [ERROR] <사용자용 문구>` 다음 `data: [DONE]`.
    loader: 테스트용 `StandardsLoader`(없으면 기본 데이터 디렉터리).
    """
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        yield _sse("[ERROR] OPENAI_API_KEY가 설정되지 않았습니다.")
        yield _sse("[DONE]")
        return

    current_phase = _resolve_phase(form_data)

    # ── 규격 데이터·선택·프롬프트 준비: 실패하면 LLM 을 부르지 않는다 (FR-05, NFR-9) ──
    try:
        _validate_standards(loader)
        selections = _resolve_selections(form_data, loader)
        prompt = mpb.build_manual_prompt(form_data, selections, current_phase, loader)
    except sl.StandardsDataError as exc:
        print(f"[ISO Manual] {STANDARDS_UNAVAILABLE}: {exc}")
        yield _sse(f"[ERROR] {_MSG_STANDARDS_UNAVAILABLE}")
        yield _sse("[DONE]")
        return
    except sl.EditionSelectionError as exc:
        print(f"[ISO Manual] invalid standard selection ({exc.code}, field={exc.field}): {exc}")
        yield _sse(f"[ERROR] {_MSG_SELECTION_INVALID}")
        yield _sse("[DONE]")
        return
    except Exception as exc:  # OutlineError 등 데이터·조립 오류
        print(f"[ISO Manual] prompt build failed: {exc!r}")
        yield _sse(f"[ERROR] {_MSG_PROMPT_FAILED}")
        yield _sse("[DONE]")
        return

    isos_label = "+".join(f"{s.standard_key[3:]}:{s.edition}" for s in selections)
    print(f"[ISO Manual] phase={current_phase} isos={isos_label} standards={_describe_selection(selections, loader)}")
    print(f"[ISO Manual] phase={current_phase} prompt chars system={len(prompt.system_prompt)} "
          f"user={len(prompt.user_prompt)} max_completion_tokens={prompt.max_tokens}")
    _log_unverified(current_phase, prompt.unverified)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "gpt-4.1",
        "messages": [
            {"role": "system", "content": prompt.system_prompt},
            {"role": "user", "content": prompt.user_prompt}
        ],
        "stream": True,
        # 마지막 청크에 usage 를 받아 출력 토큰 수를 로그로 남긴다 (FR-62)
        "stream_options": {"include_usage": True},
        "temperature": 0.3,
        "max_completion_tokens": prompt.max_tokens,
    }

    started = time.monotonic()
    out_tokens = None
    finish_reason = None
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
            yield _sse(f"[ERROR] OpenAI API 오류 ({response.status_code})")
            yield _sse("[DONE]")
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
                except json.JSONDecodeError:
                    continue
                usage = chunk.get("usage") or {}
                if usage.get("completion_tokens") is not None:
                    out_tokens = usage.get("completion_tokens")
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                if choice.get("finish_reason"):
                    finish_reason = choice.get("finish_reason")
                content = (choice.get("delta") or {}).get("content", "")
                if content:
                    yield _sse_content(content)

        # 부록 C(규격 조항 대조표)는 LLM 이 아니라 서버가 JSON 에서 만들어 붙인다 (FR-33)
        if prompt.outline is not None and current_phase in (3, mpb.PHASE_FULL):
            yield _sse_content("\n\n" + mpb.build_appendix_c_markdown(prompt.outline) + "\n")

        # Phase 완료 시그널
        if current_phase > 0:
            yield _sse(f"[PHASE_COMPLETE:{current_phase}]")

        yield _sse("[DONE]")
        print(f"[ISO Manual] phase={current_phase} isos={isos_label} out_tokens={out_tokens} "
              f"elapsed={time.monotonic() - started:.1f}s finish={finish_reason}")

    except http_requests.exceptions.Timeout:
        print(f"[ISO Manual] phase={current_phase} isos={isos_label} request timeout "
              f"elapsed={time.monotonic() - started:.1f}s")
        yield _sse("[ERROR] AI 생성 시간이 초과되었습니다.")
        yield _sse("[DONE]")
    except Exception as e:
        error_msg = str(e)
        print(f"[ISO Manual] Error: {error_msg}")
        yield _sse(f"[ERROR] {error_msg[:200]}")
        yield _sse("[DONE]")


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


def generate_procedure_stream(form_data, proc_type, proc_id, loader=None):
    """
    개별 절차서를 SSE 스트리밍으로 생성하는 제너레이터.
    (향후 유료 기능으로 활성화 예정 — 현재 호출부 없음)

    Args:
        form_data: 기업 정보 딕셔너리 (target_isos / target_iso 로 규격 결정)
        proc_type: 하위호환용 인자('common'/'operation'). 절차서는 JSON `procedures` 에서 id 로 찾는다.
        proc_id: 절차서 ID (예: 'CP03', 'QP01', IMS 통합 'CP08')
    """
    try:
        selections = _resolve_selections(form_data or {}, loader)
        outline = mo.build_outline(selections, loader)
    except Exception as exc:
        print(f"[ISO Manual] procedure lookup failed: {exc!r}")
        yield _sse(f"[ERROR] {_MSG_STANDARDS_UNAVAILABLE}")
        yield _sse("[DONE]")
        return
    proc_info = next((p for p in outline.procedures if p.id == proc_id), None)

    if not proc_info:
        yield _sse(f"[ERROR] 존재하지 않는 절차서 ID: {proc_id}")
        yield _sse("[DONE]")
        return

    # TODO: 결제 확인 로직
    # TODO: 시스템 프롬프트 조립 + OpenAI 호출
    # TODO: SSE 스트리밍 반환

    yield _sse(f"[ERROR] 절차서 생성 기능은 준비 중입니다. ({proc_info.doc_no} {proc_info.name})")
    yield _sse("[DONE]")
