"""매뉴얼 커버리지 점검 (PRD T7: FR-50~53, AC-15, 7.3 R8).

생성된 phase 마크다운과 `ManualOutline`(T3 `build_outline`)을 받아 그 phase 의 품질 게이트 결과를
`CoverageReport` 로 만든다. 모두 정규식·문자열 비교라 수십 ms 안에 끝난다.

점검 항목
  1. 필수 섹션 (FR-50): `mandatory` 섹션의 제목 줄이 마크다운에 있는가.
     - 번호 우선. 번호가 맞는 제목 줄이 있으면 충족 (IMS 알파벳 번호 `6.1.A` 포함).
     - 번호가 맞지 않아도 제목이 같으면(영문 병기·공백·`[관련 조항]` 태그 차이 허용, 같은 수준 제목 줄,
       outline 에 없는 번호 또는 번호 없음) 충족으로 보고 `renumbered` 에 기록한다.
     - 접힌 조항(`fold`·`ims_fold`)은 별도 섹션이 아니므로 상위 섹션이 있으면 충족이다 (R8, 14001:2026 6.1.1 포함).
  2. 키워드: 섹션 `coverage_keywords`(OR). 섹션 제목 줄 또는 본문(하위 제목 포함)에 하나라도 있으면 충족.
     PRD 는 '제목 키워드'로 정의했지만 모델이 제목 문구를 바꿔 써도 본문에 다뤘다면 누락으로 보지 않도록
     제목+본문 범위로 판정한다. 키워드 누락은 섹션 누락(found/required)에 세지 않고 별도 경고로 둔다.
  3. 문서번호: 본문의 `SMS-CPxx/QPxx/EPxx/SPxx` 가 outline.procedures(IMS 통합 번호 반영)에 있는가.
     통합 절차서가 대체한 번호(EP02 -> CP08 등)가 남아 있으면 `replaced` 로 구분한다.
  4. Mermaid: 코드 펜스 짝, 선언, 노드 라벨 따옴표, 엣지 라벨 금지 문자 등 (프롬프트 규칙 7~11 일부).

점검 실패는 생성 완료를 막지 않는다. 이 모듈의 공개 함수는 예외를 던지지 않고 `error` 가 채워진 리포트를
돌려준다 (내부 경로·스택은 담지 않는다).
"""
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services import manual_outline as mo
from services import standards_loader as sl

__all__ = [
    "CoverageReport", "ManualCoverage", "MissingSection", "KeywordGap", "DocNoIssue", "MermaidWarning",
    "check_phase_coverage", "check_manual_coverage", "outline_from_form_data",
    "report_to_dict", "report_to_json", "manual_coverage_to_dict",
    "MAX_LISTED_ISSUES",
]

# 리포트에 담는 경고 목록 길이 상한 (SSE 페이로드를 작게 유지)
MAX_LISTED_ISSUES = 30

_DEFAULT_TARGET_ISO = "ISO 9001:2015"      # iso_manual_service 와 같은 기본값 (target_isos·target_iso 둘 다 없는 옛 행)

# ---------------------------------------------------------------------------
# 결과 구조
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MissingSection:
    manual_no: str
    title: str
    refs: Tuple[str, ...]          # 관련 규격 조항 라벨 ("Q 6.1.3", "E 6.1.2")


@dataclass(frozen=True)
class KeywordGap:
    manual_no: str
    title: str
    keywords: Tuple[str, ...]


@dataclass(frozen=True)
class DocNoIssue:
    doc_no: str                    # "SMS-EP02"
    kind: str                      # "replaced"(통합 번호로 대체됨) | "unknown"(문서 체계에 없음)
    count: int
    line: int                      # 처음 나온 줄 (1부터)
    replaced_by: Optional[str] = None


@dataclass(frozen=True)
class MermaidWarning:
    kind: str
    message: str
    line: int                      # 1부터 (블록 시작 또는 문제 줄)


@dataclass(frozen=True)
class CoverageReport:
    phase: int
    required: int
    found: int
    missing: Tuple[MissingSection, ...] = ()
    keyword_missing: Tuple[KeywordGap, ...] = ()
    renumbered: Tuple[Tuple[str, str], ...] = ()      # (기대 번호, 실제로 쓰인 번호 또는 '')
    doc_no_issues: Tuple[DocNoIssue, ...] = ()
    mermaid_warnings: Tuple[MermaidWarning, ...] = ()
    mermaid_blocks: int = 0
    elapsed_ms: float = 0.0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """섹션 누락이 없고 점검 자체가 성공했는가 (경고는 ok 에 영향 없음)."""
        return self.error is None and not self.missing


@dataclass(frozen=True)
class ManualCoverage:
    """매뉴얼 전체(저장된 phase 들)의 커버리지."""
    standards: Tuple[str, ...]
    phases: Tuple[CoverageReport, ...]

    @property
    def required(self) -> int:
        return sum(r.required for r in self.phases)

    @property
    def found(self) -> int:
        return sum(r.found for r in self.phases)


# ---------------------------------------------------------------------------
# 마크다운 파싱
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([A-Za-z0-9_-]*)")
# 제목 줄: 정규식 하나로 '제목 + 닫는 #'을 처리하면 공백이 긴 줄에서 역추적이 2차로 늘어난다(F-08).
# 시작 부분만 정규식으로 보고 나머지는 선형 문자열 연산으로 자른다.
_HEADING_START_RE = re.compile(r"^\s{0,3}(#{1,6})(?=[ \t])")
# 실제 제목은 짧다. 이보다 긴 줄은 앞부분만 제목 비교에 쓴다(병적 입력 방어).
_MAX_HEADING_CHARS = 300
_NUMBER_RE = re.compile(r"^(?:제\s*)?(\d{1,2}(?:\.\d{1,2}){0,2}(?:\.[A-Za-z]{1,2})?)\.?(?=\s|$)\s*")
_TAG_RE = re.compile(r"\[\s*관련\s*조항[^\]]*\]")
_PAREN_RE = re.compile(r"[(（][^)）]*[)）]")
_HANGUL_RE = re.compile(r"[가-힣]")
_NON_WORD_RE = re.compile(r"[\s\W_]+", re.UNICODE)
_DOC_NO_RE = re.compile(r"(?<![A-Za-z0-9])SMS-((?:CP|QP|EP|SP)[0-9]{2})(?![A-Za-z0-9])")


@dataclass
class _Heading:
    line: int                      # 0부터
    depth: int
    number: Optional[str]
    title: str                     # 번호·태그를 뗀 제목 원문
    end: int = 0                   # 이 제목이 끝나는 줄(다음 같은/상위 수준 제목 직전, 제외)
    claimed: bool = False


@dataclass
class _Fence:
    open_line: int
    info: str
    marker: str
    close_line: Optional[int] = None
    body: List[Tuple[int, str]] = field(default_factory=list)   # (줄 번호, 텍스트)


def _parse(markdown: str) -> Tuple[List[str], List[_Heading], List[_Fence]]:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings: List[_Heading] = []
    fences: List[_Fence] = []
    cur: Optional[_Fence] = None
    for i, line in enumerate(lines):
        fm = _FENCE_RE.match(line)
        if cur is not None:
            # 닫는 펜스: 같은 문자·같거나 긴 길이, 뒤에 내용 없음
            stripped = line.strip()
            if stripped and set(stripped) == {cur.marker[0]} and len(stripped) >= len(cur.marker):
                cur.close_line = i
                cur = None
            else:
                cur.body.append((i, line))
            continue
        if fm:
            cur = _Fence(open_line=i, info=fm.group(2).lower(), marker=fm.group(1))
            fences.append(cur)
            continue
        hm = _match_heading(line)
        if hm:
            depth, raw_title = hm
            number, title = _split_heading(raw_title)
            headings.append(_Heading(line=i, depth=depth, number=number, title=title))
    # 섹션 범위: 다음 같은/상위 수준 제목 직전까지 (하위 제목 포함)
    for idx, h in enumerate(headings):
        h.end = len(lines)
        for nxt in headings[idx + 1:]:
            if nxt.depth <= h.depth:
                h.end = nxt.line
                break
    return lines, headings, fences


def _match_heading(line: str) -> Optional[Tuple[int, str]]:
    """`## 제목 ##` 형태의 제목 줄이면 (깊이, 제목 원문). 선형 시간."""
    m = _HEADING_START_RE.match(line)
    if not m:
        return None
    rest = line[m.end():]
    if len(rest) < 2:               # '#' 다음 공백 하나뿐인 줄은 제목이 아니다(기존 정규식과 같은 동작)
        return None
    body = rest.lstrip(" \t")
    if not body:
        return len(m.group(1)), ""
    body = body.rstrip(" \t")
    trimmed = body.rstrip("#")
    if trimmed != body and trimmed and trimmed[-1] in " \t":     # 닫는 ## 제거
        body = trimmed.rstrip(" \t")
    return len(m.group(1)), body


def _split_heading(raw: str) -> Tuple[Optional[str], str]:
    raw = raw[:_MAX_HEADING_CHARS]
    text = re.sub(r"\*+", "", raw).strip().strip("_ ").strip()      # 굵게(**) 표시 제거
    text = _TAG_RE.sub("", text).strip()
    m = _NUMBER_RE.match(text)
    if m:
        number = m.group(1).upper()
        return number, text[m.end():].strip().strip("_ ").strip()
    return None, text


def _norm_title(text: str) -> str:
    """제목 비교용: 영문만 든 괄호 병기·태그 제거, 공백·구두점 제거, 소문자."""
    text = _TAG_RE.sub("", text)
    text = _PAREN_RE.sub(lambda m: m.group(0) if _HANGUL_RE.search(m.group(0)) else "", text)
    # 'a / English' 형태의 영문 병기(슬래시·하이픈 뒤 영문만)도 제거
    text = re.sub(r"\s*[/|·-]\s*[A-Za-z][A-Za-z ,'&-]*$", "", text)
    return _NON_WORD_RE.sub("", text).casefold()


def _titles_match(expected: str, actual: str) -> bool:
    a, b = _norm_title(expected), _norm_title(actual)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return long_.startswith(short) and len(short) >= 4 and len(short) / len(long_) >= 0.6


def _norm_text(text: str) -> str:
    return _NON_WORD_RE.sub("", text).casefold()


# ---------------------------------------------------------------------------
# 점검 구현
# ---------------------------------------------------------------------------


def _section_missing(section: mo.OutlineSection) -> MissingSection:
    return MissingSection(
        manual_no=section.manual_no, title=section.title,
        refs=tuple(r.label for r in section.tagged_refs))


def _check_sections(lines: List[str], headings: List[_Heading], outline: mo.ManualOutline,
                    phase: int) -> Tuple[int, List[MissingSection], List[KeywordGap], List[Tuple[str, str]]]:
    targets = outline.coverage_targets(phase)
    by_number: Dict[str, List[_Heading]] = {}
    for h in headings:
        if h.number:
            by_number.setdefault(h.number, []).append(h)
    known_numbers = {s.manual_no for s in outline.sections}

    matched: Dict[str, _Heading] = {}
    renumbered: List[Tuple[str, str]] = []
    # 1) 번호 일치
    for s in targets:
        hs = by_number.get(s.manual_no)
        if hs:
            hs[0].claimed = True
            matched[s.manual_no] = hs[0]
    # 2) 제목 일치 (번호가 없거나 outline 에 없는 번호를 쓴 같은 수준 제목 줄만, 한 줄은 한 섹션에만)
    for s in targets:
        if s.manual_no in matched:
            continue
        expected_depth = min(s.level, 3)
        for h in headings:
            if h.claimed or h.depth != expected_depth:
                continue
            if h.number and h.number in known_numbers:
                continue
            if _titles_match(s.title, h.title):
                h.claimed = True
                matched[s.manual_no] = h
                renumbered.append((s.manual_no, h.number or ""))
                break

    missing: List[MissingSection] = []
    keyword_gaps: List[KeywordGap] = []
    for s in targets:
        h = matched.get(s.manual_no)
        if h is None:
            missing.append(_section_missing(s))
            continue
        if s.coverage_keywords:
            body = _norm_text("\n".join(lines[h.line:h.end]))
            if not any(_norm_text(k) in body for k in s.coverage_keywords if _norm_text(k)):
                keyword_gaps.append(KeywordGap(s.manual_no, s.title, tuple(s.coverage_keywords)))
    return len(matched), missing, keyword_gaps, renumbered


def _check_doc_numbers(lines: List[str], outline: mo.ManualOutline) -> List[DocNoIssue]:
    valid = {p.doc_no for p in outline.procedures}
    replaced_by: Dict[str, str] = {}
    for p in outline.procedures:
        prefix = p.doc_no[:-len(p.id)] if p.doc_no.endswith(p.id) else "SMS-"
        for old in p.replaces:
            replaced_by[prefix + old] = p.doc_no
    seen: Dict[str, List[int]] = {}
    for i, line in enumerate(lines):
        if "SMS-" not in line:
            continue
        for m in _DOC_NO_RE.finditer(line):
            doc_no = "SMS-" + m.group(1)
            if doc_no in valid:
                continue
            seen.setdefault(doc_no, []).append(i + 1)
    issues = []
    for doc_no, where in seen.items():
        new = replaced_by.get(doc_no)
        issues.append(DocNoIssue(doc_no=doc_no, kind="replaced" if new else "unknown",
                                 count=len(where), line=where[0], replaced_by=new))
    return issues


_NODE_DEF_RE = re.compile(r"^\s*([^\s\[\]\"]+)\[")
_BAD_CHARS = {"“": "스마트 따옴표", "”": "스마트 따옴표", "—": "em-dash"}
_EDGE_RE = re.compile(r"-\.[^\s>]*\.->|-\.->|-->|==>|---")
_EDGE_LABEL_RE = re.compile(r"-\.(?P<label>[^\s>]*?)\.->")
_QUOTED_RE = re.compile(r'"[^"]*"')


def _check_mermaid_block(fence: _Fence) -> List[MermaidWarning]:
    out: List[MermaidWarning] = []
    body = [(n, t) for n, t in fence.body]
    first = next(((n, t) for n, t in body if t.strip()), None)
    if first is None:
        return [MermaidWarning("empty_block", "Mermaid 코드블록이 비어 있습니다.", fence.open_line + 1)]
    if not re.match(r"^\s*(flowchart|graph)\b", first[1]):
        out.append(MermaidWarning("no_declaration", "첫 줄이 flowchart/graph 선언이 아닙니다.", first[0] + 1))
    for n, text in body:
        stripped = text.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        for ch, name in _BAD_CHARS.items():
            if ch in text:
                out.append(MermaidWarning("forbidden_char", "%s 사용 금지" % name, n + 1))
                break
        nodes = _NODE_DEF_RE.match(text)
        if nodes and not stripped.startswith(("subgraph", "classDef", "class ")):
            if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", nodes.group(1)):
                out.append(MermaidWarning("bad_node_id", "노드 ID 는 영문자만 사용: %s" % nodes.group(1)[:20], n + 1))
        # 노드 라벨은 큰따옴표로 감싼다: A["..."] (X: A[...]); subgraph 이름 뒤 대괄호도 같은 규칙
        no_labels = _QUOTED_RE.sub('""', text)
        if re.search(r"[A-Za-z0-9_]\[(?!\")", no_labels):
            out.append(MermaidWarning("unquoted_label", "노드 라벨을 큰따옴표로 감싸야 합니다", n + 1))
        if len(_EDGE_RE.findall(no_labels)) > 1:
            out.append(MermaidWarning("multiple_edges", "한 줄에 엣지가 둘 이상 있습니다", n + 1))
        for em in _EDGE_LABEL_RE.finditer(no_labels):
            if re.search(r"[·\"()（）]", em.group("label")):
                out.append(MermaidWarning("edge_label", "엣지 레이블에 점·따옴표·괄호 사용 금지", n + 1))
                break
        if "<br" in no_labels:
            out.append(MermaidWarning("br_outside_label", "<br/> 는 노드 라벨 안에서만 사용", n + 1))
    return out


def _check_mermaid(fences: List[_Fence]) -> Tuple[int, List[MermaidWarning]]:
    warnings: List[MermaidWarning] = []
    blocks = 0
    for f in fences:
        if f.close_line is None:
            warnings.append(MermaidWarning(
                "unclosed_fence", "닫히지 않은 코드 펜스(출력이 중간에 끊겼을 수 있음)", f.open_line + 1))
        if f.info == "mermaid":
            blocks += 1
            warnings.extend(_check_mermaid_block(f))
    return blocks, warnings


def _cap(items: Sequence[Any]) -> Tuple[Any, ...]:
    return tuple(items[:MAX_LISTED_ISSUES])


def check_phase_coverage(markdown: str, outline: mo.ManualOutline, phase: int) -> CoverageReport:
    """한 phase 의 마크다운이 outline 의 필수 섹션을 갖췄는지 점검한다. 예외를 던지지 않는다."""
    started = time.perf_counter()
    try:
        if phase not in (1, 2, 3):
            raise ValueError("phase must be 1..3")
        required = len(outline.coverage_targets(phase))
        text = markdown if isinstance(markdown, str) else ""
        lines, headings, fences = _parse(text)
        found, missing, keyword_gaps, renumbered = _check_sections(lines, headings, outline, phase)
        doc_issues = _check_doc_numbers(lines, outline)
        blocks, mermaid = _check_mermaid(fences)
        return CoverageReport(
            phase=phase, required=required, found=found,
            missing=tuple(missing), keyword_missing=_cap(keyword_gaps),
            renumbered=tuple(renumbered), doc_no_issues=_cap(doc_issues),
            mermaid_warnings=_cap(mermaid), mermaid_blocks=blocks,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
    except Exception as exc:           # 점검 실패는 생성 완료를 막지 않는다 (FR-51)
        required = 0
        try:
            required = len(outline.coverage_targets(phase))
        except Exception:
            pass
        return CoverageReport(phase=phase if isinstance(phase, int) else 0, required=required, found=0,
                              error=type(exc).__name__,
                              elapsed_ms=round((time.perf_counter() - started) * 1000, 2))


def check_manual_coverage(phase_markdowns: Mapping[int, Optional[str]],
                          outline: mo.ManualOutline) -> ManualCoverage:
    """저장된 phase 들(내용이 있는 것만)을 점검한다."""
    reports = [check_phase_coverage(md, outline, p)
               for p, md in sorted(phase_markdowns.items()) if md]
    return ManualCoverage(standards=tuple(outline.meta.standard_codes), phases=tuple(reports))


def outline_from_form_data(form_data: Mapping[str, Any],
                           loader: Optional[sl.StandardsLoader] = None) -> mo.ManualOutline:
    """저장된 세션 form_data(target_isos 우선, 옛 행은 target_iso)로 outline 을 다시 만든다.

    이미 생성된 문서를 점검하는 것이므로 노출 게이트(enforce_exposure)는 적용하지 않는다.
    데이터 오류는 `StandardsDataError`/`OutlineError`/`EditionSelectionError` 로 호출자에게 전달한다.
    """
    target_isos = form_data.get("target_isos")
    target_iso = form_data.get("target_iso")
    if target_isos is None and not target_iso:
        target_iso = _DEFAULT_TARGET_ISO
    resolve = loader.resolve_manual_standards if loader else sl.resolve_manual_standards
    selections = resolve(target_isos=target_isos, target_iso=target_iso, enforce_exposure=False)
    return mo.build_outline(selections, loader)


# ---------------------------------------------------------------------------
# 직렬화
# ---------------------------------------------------------------------------


def report_to_dict(report: CoverageReport) -> Dict[str, Any]:
    """FR-51 형식 `{phase, required, found, missing:[{manual_no,title,refs}]}` + 경고 필드."""
    return {
        "phase": report.phase,
        "required": report.required,
        "found": report.found,
        "missing": [{"manual_no": m.manual_no, "title": m.title, "refs": list(m.refs)} for m in report.missing],
        "keyword_missing": [{"manual_no": k.manual_no, "title": k.title, "keywords": list(k.keywords)}
                            for k in report.keyword_missing],
        "renumbered": [{"expected": e, "actual": a} for e, a in report.renumbered],
        "doc_no_issues": [{"doc_no": d.doc_no, "kind": d.kind, "count": d.count, "line": d.line,
                           "replaced_by": d.replaced_by} for d in report.doc_no_issues],
        "mermaid_warnings": [{"kind": w.kind, "message": w.message, "line": w.line}
                             for w in report.mermaid_warnings],
        "mermaid_blocks": report.mermaid_blocks,
        "ok": report.ok,
        "error": report.error,
    }


def report_to_json(report: CoverageReport) -> str:
    """SSE `event: coverage` 의 data 로 쓰는 한 줄 JSON (개행 없음)."""
    return json.dumps(report_to_dict(report), ensure_ascii=False, separators=(",", ":"))


def manual_coverage_to_dict(coverage: ManualCoverage, manual_id: Optional[str] = None) -> Dict[str, Any]:
    required, found = coverage.required, coverage.found
    body: Dict[str, Any] = {
        "standards": list(coverage.standards),
        "completed_phases": [r.phase for r in coverage.phases],
        "phases": [report_to_dict(r) for r in coverage.phases],
        "totals": {"required": required, "found": found, "missing": required - found},
    }
    if manual_id is not None:
        body = dict({"manual_id": manual_id}, **body)
    return body
