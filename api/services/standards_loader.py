"""규격 조항 데이터 로더 (PRD T2: FR-01~07, FR-70~75, FR-81~82).

`api/standards/*.json`(스키마 v1.2)을 읽어 검증하고, 읽기 전용 구조로 돌려준다.
이 모듈은 **데이터 접근만** 한다. 병합 엔진(outline, IMS R1~R11), 프롬프트 조립,
API 변경은 T3·T4·T5 몫이다.

설계 원칙
- 경로는 이 파일 기준(`../standards`)이다. 현재 작업 디렉터리에 의존하지 않는다.
- 파일 하나는 프로세스당 1회만 파싱·검증하고 캐시한다(Vercel 콜드 스타트 비용).
  (규격, edition) 단위로 지연 로드하므로 9001 단일 요청이 14001 파일을 읽지 않는다.
  앱 시작 시 전체를 한 번에 확인하려면 `load_all()` 을 부른다.
- 실패는 조용히 넘기지 않는다(FR-05). 파일 없음·파싱 오류·스키마 위반은
  `StandardsDataError` 이며 메시지에 파일명과 위치(조항 id)가 들어간다.
  실패 결과는 캐시하지 않는다(파일이 고쳐지면 다음 호출에서 회복).
- 사용자 입력 문제(허용되지 않는 규격·edition, 중복 선택 등)는 데이터 오류가 아니므로
  `EditionSelectionError`(ValueError) 로 구분한다. 호출자가 400 으로 바꿀 수 있다.
- edition 별 코드 분기(`if edition == ...`)를 만들지 않는다(FR-82). 새 edition 은
  JSON 파일 추가 + `STANDARD_REGISTRY` 갱신으로 끝난다.
"""
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.2"

# 이 파일 기준 경로. cwd 에 의존하지 않는다.
STANDARDS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "standards")
)
HLS_FILENAME = "hls_common.json"

# 장(chapter) -> 생성 phase. hls_common.json 의 phase_by_chapter 와 같아야 한다.
PHASE_BY_CHAPTER = MappingProxyType({"4": 1, "5": 1, "6": 1, "7": 2, "8": 2, "9": 3, "10": 3})

# ---------------------------------------------------------------------------
# edition 레지스트리 (FR-70~73, FR-82)
# ---------------------------------------------------------------------------
# 순서가 곧 정렬 순서다(9001 -> 14001 -> 45001, PRD 8.1).
#  - default_edition : 사용자 확정(FR-72). 9001·14001 은 2026, 45001 은 2018.
#                      edition 을 생략한 입력에만 적용한다.
#  - gated_editions  : 공개 전 게이트가 걸린 edition(FR-81, 11.3 Q8·Q14).
#                      환경 변수 `ENABLE_EDITION_<edition>`(예: ENABLE_EDITION_2026)이
#                      켜져 있고 데이터 게이트도 통과해야 노출된다. 기본은 닫힘.
STANDARD_REGISTRY = MappingProxyType({
    "iso9001": MappingProxyType({
        "number": "9001", "display": "ISO 9001", "tag": "Q",
        "editions": ("2015", "2026"), "default_edition": "2026",
        "gated_editions": ("2026",),
    }),
    "iso14001": MappingProxyType({
        "number": "14001", "display": "ISO 14001", "tag": "E",
        "editions": ("2015", "2026"), "default_edition": "2026",
        "gated_editions": ("2026",),
    }),
    "iso45001": MappingProxyType({
        "number": "45001", "display": "ISO 45001", "tag": "S",
        "editions": ("2018",), "default_edition": "2018",
        "gated_editions": (),
    }),
})

MAX_SELECTED_STANDARDS = 3
EDITION_FLAG_PREFIX = "ENABLE_EDITION_"
_TRUTHY = {"1", "true", "yes", "on", "y"}

# 스키마 열거값 (PRD 6.2)
TITLE_STATUSES = ("official", "provisional", "descriptive")
OUTLINE_ROLES = ("section", "fold")
VERIFICATIONS = ("body_verified", "toc_only", "carried")
PROCESS_CATEGORIES = ("MGT", "CORE", "SUP", "IMP")
DOC_INFO_KINDS = ("maintain", "retain")
MAX_SUMMARY_CHARS = 400
SCHEDULE_KEYS = (
    "published_at", "cb_prev_edition_application_stop",
    "new_cert_new_edition_only_after", "prev_edition_cert_invalid_after",
)

# ---------------------------------------------------------------------------
# 예외
# ---------------------------------------------------------------------------


class StandardsDataError(Exception):
    """조항 데이터 파일 문제(없음·파싱 오류·스키마 위반). 호출자는 STANDARDS_UNAVAILABLE 로 매핑한다."""

    def __init__(self, message: str, filename: Optional[str] = None,
                 location: Optional[str] = None,
                 issues: Optional[Sequence[Tuple[str, str]]] = None):
        self.filename = filename
        self.location = location
        self.issues = tuple(issues or ())
        prefix = filename or "standards"
        if location:
            prefix += " [%s]" % location
        super().__init__("%s: %s" % (prefix, message))


class EditionSelectionError(ValueError):
    """사용자가 고른 규격·edition 이 허용되지 않음. 호출자가 400(오류 키 `target_isos`)으로 바꾼다.

    code: empty / too_many / invalid_format / unsupported_standard / unsupported_edition /
          duplicate_standard / edition_not_available
    """

    def __init__(self, message: str, code: str, field_name: str = "target_isos", value: Any = None):
        super().__init__(message)
        self.code = code
        self.field = field_name
        self.value = value


# ---------------------------------------------------------------------------
# 읽기 전용 구조
# ---------------------------------------------------------------------------


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    """읽기 전용 구조를 JSON 직렬화 가능한 평범한 dict/list 사본으로 되돌린다."""
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class DocumentedInfo:
    kind: str   # maintain(문서 유지) / retain(기록 보유)
    name: str


@dataclass(frozen=True)
class Clause:
    id: str
    title_ko: str
    title_ko_status: str
    parent: Optional[str]
    level: int
    merge_key: str
    phase: int
    mandatory: bool
    outline_role: str
    verification: str
    requirement_summary: str
    manual_guidance: Tuple[str, ...] = ()
    required_artifacts: Tuple[str, ...] = ()
    documented_info: Tuple[DocumentedInfo, ...] = ()
    linked_procedures: Tuple[str, ...] = ()
    coverage_keywords: Tuple[str, ...] = ()
    kr_context: Tuple[str, ...] = ()
    carried_from: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class Procedure:
    id: str
    name: str
    iso_clauses: Tuple[str, ...]
    merge_key: str
    process_category: str
    department: str
    input: str
    output: str
    records: str
    retention: str
    media: str
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class StandardData:
    """(규격, edition) 한 벌. 모든 컬렉션은 튜플/읽기 전용 매핑이다."""
    schema_version: str
    standard_key: str
    edition: str
    standard_code: str
    tag: str
    name_ko: str
    system_noun: str
    policy_noun: str
    objective_noun: str
    default_department: str
    content_source: str
    source_note: str
    authored_by: str
    reviewed_by: str
    reviewed_at: str
    published_at: Optional[str]
    terms_section: Optional[Mapping[str, Any]]
    clauses: Tuple[Clause, ...]
    procedures: Tuple[Procedure, ...]
    filename: str = ""
    warnings: Tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    _clause_by_id: Mapping[str, Clause] = field(init=False, repr=False, compare=False)
    _clause_by_key: Mapping[str, Clause] = field(init=False, repr=False, compare=False)
    _procedure_by_id: Mapping[str, Procedure] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_clause_by_id",
                           MappingProxyType({c.id: c for c in self.clauses}))
        object.__setattr__(self, "_clause_by_key",
                           MappingProxyType({c.merge_key: c for c in self.clauses}))
        object.__setattr__(self, "_procedure_by_id",
                           MappingProxyType({p.id: p for p in self.procedures}))

    def clause(self, clause_id: str) -> Optional[Clause]:
        return self._clause_by_id.get(clause_id)

    def clause_by_merge_key(self, merge_key: str) -> Optional[Clause]:
        return self._clause_by_key.get(merge_key)

    def procedure(self, procedure_id: str) -> Optional[Procedure]:
        return self._procedure_by_id.get(procedure_id)

    def children_of(self, clause_id: Optional[str]) -> Tuple[Clause, ...]:
        return tuple(c for c in self.clauses if c.parent == clause_id)

    def clauses_in_phase(self, phase: int) -> Tuple[Clause, ...]:
        return tuple(c for c in self.clauses if c.phase == phase)


@dataclass(frozen=True)
class HlsClause:
    id: str
    title_ko: str
    parent: Optional[str]
    level: int
    merge_key: str
    phase: int
    title_variants: Tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class HlsCommon:
    """edition 공통 뼈대. IMS 규칙 필드(`ims_*`)는 T3 가 해석한다. 여기서는 읽기만 한다."""
    schema_version: str
    content_source: str
    source_note: str
    authored_by: str
    reviewed_by: str
    reviewed_at: str
    standard_order: Tuple[str, ...]
    tag_order: Tuple[str, ...]
    tag_labels: Mapping[str, str]
    phase_by_chapter: Mapping[str, int]
    clauses: Tuple[HlsClause, ...]
    ims_subclause_order: Mapping[str, Tuple[str, ...]]
    ims_subclause_titles: Mapping[str, str]
    ims_naming: Mapping[str, str]
    ims_procedures: Tuple[Mapping[str, Any], ...]
    ims_fold_into_parent: Tuple[str, ...]
    ims_key_absorption: Tuple[Mapping[str, Any], ...]
    edition_info: Mapping[str, Any]
    doc_prefix: str
    notes: Tuple[str, ...] = ()
    filename: str = HLS_FILENAME
    warnings: Tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def clause(self, clause_id: str) -> Optional[HlsClause]:
        for c in self.clauses:
            if c.id == clause_id:
                return c
        return None


@dataclass(frozen=True)
class StandardsCatalog:
    hls: HlsCommon
    standards: Mapping[Tuple[str, str], StandardData]   # (standard_key, edition) -> StandardData


@dataclass(frozen=True)
class GateResult:
    """데이터 측 노출 게이트 판정(FR-81). ready=False 면 공개 불가."""
    ready: bool
    reasons: Tuple[str, ...] = ()
    unsigned: bool = False
    toc_only_mandatory: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ExposureDecision:
    """edition 노출 판정. allowed = released(공개 정책) AND data_ready(데이터 게이트)."""
    standard_key: str
    edition: str
    allowed: bool
    released: bool
    data_ready: bool
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class StandardSelection:
    """정규화된 사용자 선택 한 건. `defaulted` 는 edition 을 생략해 기본값이 적용됐다는 뜻."""
    standard_key: str
    edition: str
    standard_code: str
    tag: str
    defaulted: bool = False


# ---------------------------------------------------------------------------
# 순수 함수: 레지스트리·입력 정규화·데이터 게이트
# ---------------------------------------------------------------------------


def standard_code_of(standard_key: str, edition: str) -> str:
    return "%s:%s" % (STANDARD_REGISTRY[standard_key]["display"], edition)


def registry_pairs() -> List[Tuple[str, str]]:
    """레지스트리의 모든 (규격, edition) 쌍(정렬 순서)."""
    return [(k, e) for k, spec in STANDARD_REGISTRY.items() for e in spec["editions"]]


def expected_filename(standard_key: str, edition: str) -> str:
    return "%s_%s.json" % (standard_key, edition)


def allowed_standard_codes() -> Tuple[str, ...]:
    """허용 목록(ALLOWED_MANUAL_ISO_STANDARDS 후보). 레지스트리에서 파생하므로 하드코딩 사본이 없다."""
    return tuple(standard_code_of(k, e) for k, e in registry_pairs())


def _flag_enabled(edition: str, flags: Optional[Iterable[str]]) -> bool:
    if flags is not None:
        return edition in set(flags)
    return os.environ.get(EDITION_FLAG_PREFIX + edition, "").strip().lower() in _TRUTHY


def is_edition_released(standard_key: str, edition: str,
                        flags: Optional[Iterable[str]] = None) -> bool:
    """공개 정책 판정. 게이트가 걸린 edition 은 `ENABLE_EDITION_<edition>` 이 켜져야 true.

    flags 를 주면 환경 변수 대신 그 edition 집합(예: {"2026"})을 쓴다(테스트·내부용).
    """
    spec = STANDARD_REGISTRY.get(standard_key)
    if spec is None or edition not in spec["editions"]:
        return False
    if edition not in spec["gated_editions"]:
        return True
    return _flag_enabled(edition, flags)


def evaluate_data_gate(standard: "StandardData") -> GateResult:
    """FR-81/AC-26: 서명이 비었거나 `toc_only` 필수 조항이 남았으면 공개 불가."""
    reasons = []
    unsigned = not (standard.reviewed_by or "").strip() or not (standard.reviewed_at or "").strip()
    if unsigned:
        reasons.append("검토자(reviewed_by) 또는 검토일(reviewed_at)이 비어 있음")
    toc_only = tuple(c.id for c in standard.clauses if c.mandatory and c.verification == "toc_only")
    if toc_only:
        reasons.append("toc_only 필수 조항 %d건: %s" % (len(toc_only), ", ".join(toc_only[:10])
                                                       + (" ..." if len(toc_only) > 10 else "")))
    return GateResult(ready=not reasons, reasons=tuple(reasons), unsigned=unsigned,
                      toc_only_mandatory=toc_only)


def find_unverified_clauses(standard: "StandardData") -> Dict[str, Tuple[str, ...]]:
    """`toc_only`/`carried` 조항 id (FR-81 (b): 프롬프트에 들어갈 때 경고 로그용)."""
    out: Dict[str, Tuple[str, ...]] = {}
    for kind in ("toc_only", "carried"):
        ids = tuple(c.id for c in standard.clauses if c.verification == kind)
        if ids:
            out[kind] = ids
    return out


_FULL_RE = re.compile(r"^ISO\s*([0-9]{4,5})\s*:\s*([0-9]{4})$", re.IGNORECASE)
_SHORT_RE = re.compile(r"^([0-9]{4,5})(?:\s*:\s*([0-9]{4}))?$")
_KEY_BY_NUMBER = {spec["number"]: key for key, spec in STANDARD_REGISTRY.items()}


def parse_standard_input(value: Any) -> Tuple[str, Optional[str]]:
    """입력 문자열 하나를 (standard_key, edition 또는 None)으로 파싱한다.

    허용: "ISO 9001:2015"(정식, 대소문자·공백 무시), "9001:2026", "9001"(약식, FR-71).
    edition 이 없는 "ISO 9001" 은 PRD 가 허용하지 않는 형태라 거부한다.
    """
    if not isinstance(value, str):
        raise EditionSelectionError("규격 값은 문자열이어야 합니다.", "invalid_format", value=value)
    text = value.strip()
    m = _FULL_RE.match(text) or _SHORT_RE.match(text)
    if not m:
        raise EditionSelectionError("인식할 수 없는 규격 형식입니다: %r (예: 'ISO 9001:2015', '9001:2026', '9001')"
                                    % value, "invalid_format", value=value)
    number, edition = m.group(1), m.group(2)
    key = _KEY_BY_NUMBER.get(number)
    if key is None:
        raise EditionSelectionError("지원하지 않는 규격입니다: ISO %s" % number,
                                    "unsupported_standard", value=value)
    if edition is not None and edition not in STANDARD_REGISTRY[key]["editions"]:
        supported = ", ".join(standard_code_of(key, e) for e in STANDARD_REGISTRY[key]["editions"])
        raise EditionSelectionError("지원하지 않는 개정판입니다: %s:%s (지원: %s)"
                                    % (STANDARD_REGISTRY[key]["display"], edition, supported),
                                    "unsupported_edition", value=value)
    return key, edition


def format_target_iso(selections: Sequence[StandardSelection]) -> str:
    """`form_data["target_iso"]` 표시용 문자열. 예: 'ISO 9001:2026 + ISO 45001:2018'."""
    return " + ".join(s.standard_code for s in selections)


# ---------------------------------------------------------------------------
# 검증 보조
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")
_MERGE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
_CARRIED_RE = re.compile(r"^([a-z]+[0-9]+)_([0-9]{4}):([0-9]+(?:\.[0-9]+)*)$")
_PROC_ID_RE = re.compile(r"^[A-Z]{2}[0-9]{2}$")
_YEAR_MONTH_RE = re.compile(r"^[0-9]{4}-[0-9]{2}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_FILE_KNOWN = {
    "schema_version", "standard_key", "edition", "standard_code", "tag", "name_ko", "system_noun",
    "policy_noun", "objective_noun", "default_department", "content_source", "source_note",
    "published_at", "terms_section", "authored_by", "reviewed_by", "reviewed_at", "clauses",
    "procedures",
}
_CLAUSE_KNOWN = {
    "id", "title_ko", "title_ko_status", "parent", "level", "merge_key", "phase", "mandatory",
    "outline_role", "verification", "carried_from", "requirement_summary", "manual_guidance",
    "required_artifacts", "documented_info", "linked_procedures", "coverage_keywords", "kr_context",
}
_PROC_KNOWN = {
    "id", "name", "iso_clauses", "merge_key", "process_category", "department", "input", "output",
    "records", "retention", "media",
}
_HLS_KNOWN = {
    "schema_version", "file_type", "content_source", "source_note", "authored_by", "reviewed_by",
    "reviewed_at", "standard_order", "tag_order", "tag_labels", "phase_by_chapter", "clauses",
    "ims_subclause_order", "ims_subclause_titles", "ims_naming", "ims_procedures",
    "ims_fold_into_parent", "ims_key_absorption", "edition_info", "doc_prefix", "notes",
}
_HLS_CLAUSE_KNOWN = {"id", "title_ko", "parent", "level", "merge_key", "phase", "title_variants"}


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


class _Issues:
    def __init__(self, filename: str, strict_unknown: bool):
        self.filename = filename
        self.strict_unknown = strict_unknown
        self.errors: List[Tuple[str, str]] = []
        self.warnings: List[str] = []

    def add(self, location: str, message: str) -> None:
        self.errors.append((location, message))

    def unknown_fields(self, location: str, obj: Mapping[str, Any], known: Iterable[str]) -> Dict[str, Any]:
        """정의 밖 필드 정책: 거부하지 않고 경고로 남기고 `extra` 로 보존한다(strict 모드면 오류)."""
        extra = {k: v for k, v in obj.items() if k not in known}
        for k in extra:
            if self.strict_unknown:
                self.add(location, "정의되지 않은 필드 '%s'" % k)
            else:
                self.warnings.append("%s [%s]: 정의되지 않은 필드 '%s' (무시하고 보존)" % (self.filename, location, k))
        return extra

    def raise_if_any(self) -> None:
        if not self.errors:
            return
        shown = "; ".join("[%s] %s" % (loc, msg) for loc, msg in self.errors[:12])
        more = "" if len(self.errors) <= 12 else " ... 외 %d건" % (len(self.errors) - 12)
        first_loc = self.errors[0][0]
        raise StandardsDataError("스키마 위반 %d건 — %s%s" % (len(self.errors), shown, more),
                                 filename=self.filename, location=first_loc, issues=self.errors)


def _read_json(path: str, filename: str) -> Dict[str, Any]:
    def no_dup_keys(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError("중복된 JSON 키 '%s'" % k)
            obj[k] = v
        return obj

    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise StandardsDataError("파일을 찾을 수 없습니다 (경로: %s)" % path, filename=filename)
    except OSError as exc:
        raise StandardsDataError("파일을 읽을 수 없습니다: %s" % exc, filename=filename)
    except UnicodeDecodeError as exc:
        raise StandardsDataError("UTF-8 로 읽을 수 없습니다: %s" % exc, filename=filename)
    try:
        data = json.loads(text, object_pairs_hook=no_dup_keys)
    except json.JSONDecodeError as exc:
        raise StandardsDataError("JSON 파싱 오류 (line %d, column %d): %s" % (exc.lineno, exc.colno, exc.msg),
                                 filename=filename, location="line %d" % exc.lineno)
    except ValueError as exc:
        raise StandardsDataError("JSON 오류: %s" % exc, filename=filename)
    if not isinstance(data, dict):
        raise StandardsDataError("최상위 값이 객체가 아닙니다", filename=filename)
    return data


def _check_str_fields(issues: _Issues, loc: str, obj: Mapping[str, Any], names: Iterable[str]) -> None:
    for name in names:
        if name not in obj:
            issues.add(loc, "필수 필드 '%s' 없음" % name)
        elif not _nonempty_str(obj[name]):
            issues.add(loc, "'%s' 는 비어 있지 않은 문자열이어야 함" % name)


def _check_signature_fields(issues: _Issues, data: Mapping[str, Any]) -> None:
    # 서명 여부는 노출 게이트가 판단한다(FR-81). 로더는 서명 안 된 초안도 읽을 수 있어야 하므로
    # 키와 타입만 확인한다(빈 문자열 허용).
    for name in ("reviewed_by", "reviewed_at"):
        if name not in data:
            issues.add("file", "필수 필드 '%s' 없음" % name)
        elif not isinstance(data[name], str):
            issues.add("file", "'%s' 는 문자열이어야 함" % name)
    ra = data.get("reviewed_at")
    if isinstance(ra, str) and ra.strip() and not _DATE_RE.match(ra.strip()):
        issues.add("file", "reviewed_at 형식이 YYYY-MM-DD 가 아님: %r" % ra)


def _check_hierarchy(issues: _Issues, rows: Sequence[Mapping[str, Any]], label: str) -> None:
    """id/parent/level/phase 일관성. rows 는 dict 만 담긴 조항 목록."""
    seen_ids: Dict[str, int] = {}
    ids = {r.get("id") for r in rows if _nonempty_str(r.get("id"))}
    for i, r in enumerate(rows):
        cid = r.get("id")
        loc = "%s[%s]" % (label, cid) if _nonempty_str(cid) else "%s[#%d]" % (label, i)
        if not _nonempty_str(cid) or not _ID_RE.match(cid):
            issues.add(loc, "id 는 '6.1.2' 같은 조항 번호여야 함: %r" % (cid,))
            continue
        if cid in seen_ids:
            issues.add(loc, "id 중복 (처음 위치 #%d)" % seen_ids[cid])
        seen_ids.setdefault(cid, i)
        depth = cid.count(".") + 1
        parent = r.get("parent", "__missing__")
        if parent == "__missing__":
            issues.add(loc, "필수 필드 'parent' 없음")
        else:
            expected_parent = cid.rsplit(".", 1)[0] if depth > 1 else None
            if parent != expected_parent:
                issues.add(loc, "parent 불일치: %r (id 기준 기대값 %r)" % (parent, expected_parent))
            elif parent is not None and parent not in ids:
                issues.add(loc, "parent '%s' 조항이 파일에 없음" % parent)
        lv = r.get("level")
        if not _is_int(lv):
            issues.add(loc, "level 은 정수여야 함")
        elif lv != depth or lv not in (1, 2, 3):
            issues.add(loc, "level 불일치: %r (id 깊이 %d, 허용 1~3)" % (lv, depth))
        ph = r.get("phase")
        chapter = cid.split(".")[0]
        if not _is_int(ph):
            issues.add(loc, "phase 는 정수여야 함")
        elif chapter not in PHASE_BY_CHAPTER:
            issues.add(loc, "알 수 없는 장 번호 '%s' (4~10 만 허용)" % chapter)
        elif ph != PHASE_BY_CHAPTER[chapter]:
            issues.add(loc, "phase 불일치: %r (%s장은 phase %d)" % (ph, chapter, PHASE_BY_CHAPTER[chapter]))
    missing_chapters = [c for c in PHASE_BY_CHAPTER if not any(r.get("id") == c for r in rows)]
    if missing_chapters and rows:
        issues.add(label, "절(level 1) 누락: %s" % ", ".join(missing_chapters))


def _validate_standard_dict(raw: Dict[str, Any], filename: str, standard_key: str, edition: str,
                            strict_unknown: bool) -> Tuple[_Issues, Dict[str, Any]]:
    issues = _Issues(filename, strict_unknown)
    spec = STANDARD_REGISTRY[standard_key]

    # 파일 메타
    if raw.get("schema_version") != SCHEMA_VERSION:
        issues.add("file", "schema_version 은 %r 이어야 함 (현재 %r)" % (SCHEMA_VERSION, raw.get("schema_version")))
    if raw.get("standard_key") != standard_key:
        issues.add("file", "standard_key 불일치: %r (파일명 기준 %r)" % (raw.get("standard_key"), standard_key))
    if raw.get("edition") != edition:
        issues.add("file", "edition 불일치: %r (파일명 기준 %r)" % (raw.get("edition"), edition))
    expected_code = standard_code_of(standard_key, edition)
    if raw.get("standard_code") != expected_code:
        issues.add("file", "standard_code 불일치: %r (기대값 %r)" % (raw.get("standard_code"), expected_code))
    if raw.get("tag") != spec["tag"]:
        issues.add("file", "tag 불일치: %r (기대값 %r)" % (raw.get("tag"), spec["tag"]))
    if raw.get("content_source") != "self_authored":
        issues.add("file", "content_source 는 'self_authored' 여야 함 (현재 %r)" % (raw.get("content_source"),))
    _check_str_fields(issues, "file", raw, ("name_ko", "system_noun", "policy_noun", "objective_noun",
                                            "default_department", "source_note", "authored_by"))
    _check_signature_fields(issues, raw)
    pub = raw.get("published_at")
    if "published_at" in raw and not (isinstance(pub, str) and _YEAR_MONTH_RE.match(pub)):
        issues.add("file", "published_at 은 'YYYY-MM' 형식이어야 함: %r" % (pub,))
    ts = raw.get("terms_section")
    if "terms_section" in raw:
        if not isinstance(ts, dict):
            issues.add("terms_section", "객체여야 함")
        elif "verification" in ts and ts["verification"] not in VERIFICATIONS:
            issues.add("terms_section", "verification 열거값 위반: %r" % (ts["verification"],))
    file_extra = issues.unknown_fields("file", raw, _FILE_KNOWN)

    # 절차서
    procs = raw.get("procedures")
    proc_ids = set()
    if not isinstance(procs, list) or not procs:
        issues.add("procedures", "비어 있지 않은 배열이어야 함")
        procs = []
    proc_keys = set()
    for i, p in enumerate(procs):
        pid = p.get("id") if isinstance(p, dict) else None
        loc = "procedures[%s]" % (pid if _nonempty_str(pid) else "#%d" % i)
        if not isinstance(p, dict):
            issues.add(loc, "객체여야 함")
            continue
        if not (_nonempty_str(pid) and _PROC_ID_RE.match(pid)):
            issues.add(loc, "id 는 'CP00' 같은 형식이어야 함: %r" % (pid,))
        elif pid in proc_ids:
            issues.add(loc, "절차서 id 중복")
        else:
            proc_ids.add(pid)
        _check_str_fields(issues, loc, p, ("name", "merge_key", "department"))
        mk = p.get("merge_key")
        if _nonempty_str(mk):
            if mk in proc_keys:
                issues.add(loc, "절차서 merge_key 중복: %s" % mk)
            proc_keys.add(mk)
        if p.get("process_category") not in PROCESS_CATEGORIES:
            issues.add(loc, "process_category 열거값 위반: %r (허용 %s)" % (p.get("process_category"),
                                                                       "/".join(PROCESS_CATEGORIES)))
        for name in ("input", "output", "records", "retention", "media"):
            if not isinstance(p.get(name), str):
                issues.add(loc, "'%s' 는 문자열이어야 함 (없으면 빈 문자열)" % name)
        ic = p.get("iso_clauses")
        if not (_str_list(ic) and ic):
            issues.add(loc, "iso_clauses 는 비어 있지 않은 문자열 배열이어야 함")
        issues.unknown_fields(loc, p, _PROC_KNOWN)

    # 조항
    clauses = raw.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        issues.add("clauses", "비어 있지 않은 배열이어야 함")
        clauses = []
    rows = [c for c in clauses if isinstance(c, dict)]
    for i, c in enumerate(clauses):
        if not isinstance(c, dict):
            issues.add("clauses[#%d]" % i, "객체여야 함")
    _check_hierarchy(issues, rows, "clauses")
    clause_ids = {c.get("id") for c in rows}
    merge_keys: Dict[str, str] = {}
    for i, c in enumerate(rows):
        cid = c.get("id")
        loc = "clauses[%s]" % (cid if _nonempty_str(cid) else "#%d" % i)
        _check_str_fields(issues, loc, c, ("title_ko",))
        if c.get("title_ko_status") not in TITLE_STATUSES:
            issues.add(loc, "title_ko_status 열거값 위반: %r (허용 %s)" % (c.get("title_ko_status"), "/".join(TITLE_STATUSES)))
        if c.get("outline_role") not in OUTLINE_ROLES:
            issues.add(loc, "outline_role 열거값 위반: %r (허용 %s)" % (c.get("outline_role"), "/".join(OUTLINE_ROLES)))
        ver = c.get("verification")
        if ver not in VERIFICATIONS:
            issues.add(loc, "verification 열거값 위반: %r (허용 %s)" % (ver, "/".join(VERIFICATIONS)))
        if not isinstance(c.get("mandatory"), bool):
            issues.add(loc, "mandatory 는 true/false 여야 함")
        mk = c.get("merge_key")
        if not (_nonempty_str(mk) and _MERGE_KEY_RE.match(mk)):
            issues.add(loc, "merge_key 형식 오류: %r" % (mk,))
        elif mk in merge_keys:
            issues.add(loc, "merge_key 중복: %s (이미 %s 에서 사용)" % (mk, merge_keys[mk]))
        else:
            merge_keys[mk] = str(cid)
        summary = c.get("requirement_summary")
        if not isinstance(summary, str):
            issues.add(loc, "requirement_summary 는 문자열이어야 함")
        else:
            if len(summary) > MAX_SUMMARY_CHARS:
                issues.add(loc, "requirement_summary 가 %d자 (최대 %d자)" % (len(summary), MAX_SUMMARY_CHARS))
            if not summary.strip() and ver != "toc_only":
                issues.add(loc, "requirement_summary 가 비어 있음 (toc_only 일 때만 허용)")
        cf = c.get("carried_from")
        if cf is not None:
            m = _CARRIED_RE.match(cf) if isinstance(cf, str) else None
            if not m:
                issues.add(loc, "carried_from 형식 오류: %r (예: iso9001_2015:6.2)" % (cf,))
            else:
                cf_key, cf_ed = m.group(1), m.group(2)
                if cf_key != standard_key:
                    issues.add(loc, "carried_from 의 규격이 다름: %s (이 파일은 %s)" % (cf_key, standard_key))
                elif cf_ed not in spec["editions"] or not cf_ed < edition:
                    issues.add(loc, "carried_from 의 edition 은 같은 규격의 이전 edition 이어야 함: %s" % cf_ed)
        if ver == "carried" and cf is None:
            issues.add(loc, "verification 이 carried 이면 carried_from 이 필요함")
        for name in ("manual_guidance", "required_artifacts", "linked_procedures", "kr_context"):
            if name in c and not _str_list(c[name]):
                issues.add(loc, "'%s' 는 문자열 배열이어야 함" % name)
        kw = c.get("coverage_keywords")
        if not (_str_list(kw) and kw and all(k.strip() for k in kw)):
            issues.add(loc, "coverage_keywords 는 1개 이상의 비어 있지 않은 문자열 배열이어야 함")
        di = c.get("documented_info", [])
        if not isinstance(di, list):
            issues.add(loc, "documented_info 는 배열이어야 함")
        else:
            for j, d in enumerate(di):
                if not (isinstance(d, dict) and d.get("kind") in DOC_INFO_KINDS and _nonempty_str(d.get("name"))):
                    issues.add(loc, "documented_info[%d] 는 {kind: maintain|retain, name} 이어야 함" % j)
        for pid in (c.get("linked_procedures") if _str_list(c.get("linked_procedures", [])) else []):
            if pid not in proc_ids:
                issues.add(loc, "linked_procedures 의 절차서 '%s' 가 이 파일의 procedures 에 없음" % pid)
        issues.unknown_fields(loc, c, _CLAUSE_KNOWN)

    # 절차서 -> 조항 참조
    for p in procs:
        if isinstance(p, dict) and _str_list(p.get("iso_clauses")):
            for cid in p["iso_clauses"]:
                if cid not in clause_ids:
                    issues.add("procedures[%s]" % p.get("id"), "iso_clauses 의 조항 '%s' 가 파일에 없음" % cid)
    return issues, file_extra


def _build_standard(raw: Dict[str, Any], filename: str, file_extra: Dict[str, Any],
                    warnings: Sequence[str]) -> StandardData:
    clauses = []
    for c in raw["clauses"]:
        extra = {k: v for k, v in c.items() if k not in _CLAUSE_KNOWN}
        clauses.append(Clause(
            id=c["id"], title_ko=c["title_ko"], title_ko_status=c["title_ko_status"],
            parent=c["parent"], level=c["level"], merge_key=c["merge_key"], phase=c["phase"],
            mandatory=c["mandatory"], outline_role=c["outline_role"], verification=c["verification"],
            requirement_summary=c["requirement_summary"],
            manual_guidance=tuple(c.get("manual_guidance", ())),
            required_artifacts=tuple(c.get("required_artifacts", ())),
            documented_info=tuple(DocumentedInfo(d["kind"], d["name"]) for d in c.get("documented_info", ())),
            linked_procedures=tuple(c.get("linked_procedures", ())),
            coverage_keywords=tuple(c["coverage_keywords"]),
            kr_context=tuple(c.get("kr_context", ())),
            carried_from=c.get("carried_from"),
            extra=_freeze(extra),
        ))
    procedures = []
    for p in raw["procedures"]:
        extra = {k: v for k, v in p.items() if k not in _PROC_KNOWN}
        procedures.append(Procedure(
            id=p["id"], name=p["name"], iso_clauses=tuple(p["iso_clauses"]), merge_key=p["merge_key"],
            process_category=p["process_category"], department=p["department"], input=p["input"],
            output=p["output"], records=p["records"], retention=p["retention"], media=p["media"],
            extra=_freeze(extra),
        ))
    return StandardData(
        schema_version=raw["schema_version"], standard_key=raw["standard_key"], edition=raw["edition"],
        standard_code=raw["standard_code"], tag=raw["tag"], name_ko=raw["name_ko"],
        system_noun=raw["system_noun"], policy_noun=raw["policy_noun"], objective_noun=raw["objective_noun"],
        default_department=raw["default_department"], content_source=raw["content_source"],
        source_note=raw["source_note"], authored_by=raw["authored_by"], reviewed_by=raw["reviewed_by"],
        reviewed_at=raw["reviewed_at"], published_at=raw.get("published_at"),
        terms_section=_freeze(raw["terms_section"]) if raw.get("terms_section") is not None else None,
        clauses=tuple(clauses), procedures=tuple(procedures), filename=filename,
        warnings=tuple(warnings), extra=_freeze(file_extra),
    )


def _validate_hls_dict(raw: Dict[str, Any], filename: str, strict_unknown: bool) -> Tuple[_Issues, Dict[str, Any]]:
    issues = _Issues(filename, strict_unknown)
    if raw.get("schema_version") != SCHEMA_VERSION:
        issues.add("file", "schema_version 은 %r 이어야 함 (현재 %r)" % (SCHEMA_VERSION, raw.get("schema_version")))
    if raw.get("file_type") != "hls_common":
        issues.add("file", "file_type 은 'hls_common' 이어야 함 (현재 %r)" % (raw.get("file_type"),))
    if raw.get("content_source") != "self_authored":
        issues.add("file", "content_source 는 'self_authored' 여야 함")
    _check_str_fields(issues, "file", raw, ("source_note", "authored_by", "doc_prefix"))
    _check_signature_fields(issues, raw)

    reg_keys = tuple(STANDARD_REGISTRY)
    reg_tags = tuple(STANDARD_REGISTRY[k]["tag"] for k in reg_keys)
    if tuple(raw.get("standard_order") or ()) != reg_keys:
        issues.add("standard_order", "레지스트리 순서와 다름: %r (기대값 %r)" % (raw.get("standard_order"), list(reg_keys)))
    if tuple(raw.get("tag_order") or ()) != reg_tags:
        issues.add("tag_order", "레지스트리 태그 순서와 다름: %r (기대값 %r)" % (raw.get("tag_order"), list(reg_tags)))
    tl = raw.get("tag_labels")
    if not (isinstance(tl, dict) and set(tl) == set(reg_tags) and all(_nonempty_str(v) for v in tl.values())):
        issues.add("tag_labels", "태그 %s 각각에 비어 있지 않은 라벨이 필요함" % "/".join(reg_tags))
    if raw.get("phase_by_chapter") != dict(PHASE_BY_CHAPTER):
        issues.add("phase_by_chapter", "장-phase 매핑이 로더 기준과 다름: %r" % (raw.get("phase_by_chapter"),))

    clauses = raw.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        issues.add("clauses", "비어 있지 않은 배열이어야 함")
        clauses = []
    rows = [c for c in clauses if isinstance(c, dict)]
    if len(rows) != len(clauses):
        issues.add("clauses", "객체가 아닌 항목이 있음")
    _check_hierarchy(issues, rows, "clauses")
    seen_keys: Dict[str, str] = {}
    for i, c in enumerate(rows):
        cid = c.get("id")
        loc = "clauses[%s]" % (cid if _nonempty_str(cid) else "#%d" % i)
        _check_str_fields(issues, loc, c, ("title_ko",))
        mk = c.get("merge_key")
        if not (_nonempty_str(mk) and _MERGE_KEY_RE.match(mk)):
            issues.add(loc, "merge_key 형식 오류: %r" % (mk,))
        elif mk in seen_keys:
            issues.add(loc, "merge_key 중복: %s" % mk)
        else:
            seen_keys[mk] = str(cid)
        tv = c.get("title_variants", [])
        if not isinstance(tv, list):
            issues.add(loc, "title_variants 는 배열이어야 함")
        else:
            for j, v in enumerate(tv):
                if not (isinstance(v, dict) and v.get("requires_tag") in reg_tags and _nonempty_str(v.get("title_ko"))):
                    issues.add(loc, "title_variants[%d] 는 {requires_tag: %s, title_ko} 이어야 함" % (j, "/".join(reg_tags)))
        issues.unknown_fields(loc, c, _HLS_CLAUSE_KNOWN)

    order = raw.get("ims_subclause_order")
    titles = raw.get("ims_subclause_titles")
    if not (isinstance(order, dict) and all(_str_list(v) for v in order.values())):
        issues.add("ims_subclause_order", "{x.y: [merge_key, ...]} 형태여야 함")
        order = {}
    if not (isinstance(titles, dict) and all(_nonempty_str(k) and _nonempty_str(v) for k, v in titles.items())):
        issues.add("ims_subclause_titles", "{merge_key: 제목} 형태여야 함")
        titles = {}
    for sec, keys in order.items():
        for k in keys:
            if k not in titles:
                issues.add("ims_subclause_order", "%s 의 merge_key '%s' 에 ims_subclause_titles 제목이 없음" % (sec, k))
    nm = raw.get("ims_naming")
    if not (isinstance(nm, dict) and nm and all(_nonempty_str(v) for v in nm.values())):
        issues.add("ims_naming", "비어 있지 않은 {이름: 문자열} 객체여야 함")
    ip = raw.get("ims_procedures")
    if not isinstance(ip, list):
        issues.add("ims_procedures", "배열이어야 함")
    else:
        for i, p in enumerate(ip):
            loc = "ims_procedures[%s]" % (p.get("id") if isinstance(p, dict) else "#%d" % i)
            if not isinstance(p, dict):
                issues.add(loc, "객체여야 함")
                continue
            _check_str_fields(issues, loc, p, ("id", "name", "merge_key", "department"))
            if p.get("process_category") not in PROCESS_CATEGORIES:
                issues.add(loc, "process_category 열거값 위반: %r" % (p.get("process_category"),))
            rep = p.get("replaces")
            if not (isinstance(rep, dict) and rep and set(rep) <= set(reg_tags) and all(_nonempty_str(v) for v in rep.values())):
                issues.add(loc, "replaces 는 {태그: 절차서 id} 객체여야 함 (태그 %s)" % "/".join(reg_tags))
    if not _str_list(raw.get("ims_fold_into_parent")):
        issues.add("ims_fold_into_parent", "merge_key 문자열 배열이어야 함")
    ka = raw.get("ims_key_absorption")
    if not isinstance(ka, list):
        issues.add("ims_key_absorption", "배열이어야 함")
    else:
        for i, a in enumerate(ka):
            loc = "ims_key_absorption[#%d]" % i
            if not (isinstance(a, dict) and all(_nonempty_str(a.get(n)) for n in ("key", "into", "condition", "status"))):
                issues.add(loc, "{key, into, condition, status} 문자열이 모두 필요함")
            elif a["condition"] != "numbering_primary_lacks_key":
                issues.add(loc, "알 수 없는 condition: %r" % a["condition"])
    notes = raw.get("notes", [])
    if not _str_list(notes):
        issues.add("notes", "문자열 배열이어야 함")

    # edition_info (FR-75)
    ei = raw.get("edition_info")
    if not isinstance(ei, dict):
        issues.add("edition_info", "객체여야 함")
    else:
        _check_str_fields(issues, "edition_info", ei, ("source", "confirmed_at"))
        labels = ei.get("schedule_labels")
        if not (isinstance(labels, dict) and set(labels) == set(SCHEDULE_KEYS) and all(_nonempty_str(v) for v in labels.values())):
            issues.add("edition_info.schedule_labels", "키 %s 각각에 라벨이 필요함" % ", ".join(SCHEDULE_KEYS))
        stds = ei.get("standards")
        if not isinstance(stds, dict) or set(stds) != set(reg_keys):
            issues.add("edition_info.standards", "레지스트리 규격 %s 와 같은 키를 가져야 함" % ", ".join(reg_keys))
        else:
            for key, info in stds.items():
                loc = "edition_info.standards.%s" % key
                eds = info.get("editions") if isinstance(info, dict) else None
                if not (isinstance(eds, list) and all(isinstance(e, dict) and _nonempty_str(e.get("edition"))
                                                      and _YEAR_MONTH_RE.match(str(e.get("published_at"))) for e in eds)):
                    issues.add(loc, "editions 는 [{edition, published_at(YYYY-MM)}] 이어야 함")
                elif tuple(e["edition"] for e in eds) != tuple(STANDARD_REGISTRY[key]["editions"]):
                    issues.add(loc, "edition 목록 %r 이 레지스트리 %r 와 다름" % ([e["edition"] for e in eds],
                                                                          list(STANDARD_REGISTRY[key]["editions"])))
                sch = info.get("schedule") if isinstance(info, dict) else None
                if sch is not None:
                    if not (isinstance(sch, dict) and set(sch) <= set(SCHEDULE_KEYS)
                            and all(isinstance(v, str) and (_YEAR_MONTH_RE.match(v) or _DATE_RE.match(v)) for v in sch.values())):
                        issues.add(loc, "schedule 은 null 이거나 {%s: YYYY-MM[-DD]} 이어야 함" % "|".join(SCHEDULE_KEYS))
    extra = issues.unknown_fields("file", raw, _HLS_KNOWN)
    return issues, extra


def _build_hls(raw: Dict[str, Any], filename: str, extra: Dict[str, Any], warnings: Sequence[str]) -> HlsCommon:
    clauses = tuple(
        HlsClause(id=c["id"], title_ko=c["title_ko"], parent=c["parent"], level=c["level"],
                  merge_key=c["merge_key"], phase=c["phase"], title_variants=_freeze(c.get("title_variants", [])))
        for c in raw["clauses"])
    return HlsCommon(
        schema_version=raw["schema_version"], content_source=raw["content_source"],
        source_note=raw["source_note"], authored_by=raw["authored_by"], reviewed_by=raw["reviewed_by"],
        reviewed_at=raw["reviewed_at"], standard_order=tuple(raw["standard_order"]),
        tag_order=tuple(raw["tag_order"]), tag_labels=_freeze(raw["tag_labels"]),
        phase_by_chapter=_freeze(raw["phase_by_chapter"]), clauses=clauses,
        ims_subclause_order=_freeze(raw["ims_subclause_order"]),
        ims_subclause_titles=_freeze(raw["ims_subclause_titles"]), ims_naming=_freeze(raw["ims_naming"]),
        ims_procedures=_freeze(raw["ims_procedures"]), ims_fold_into_parent=tuple(raw["ims_fold_into_parent"]),
        ims_key_absorption=_freeze(raw["ims_key_absorption"]), edition_info=_freeze(raw["edition_info"]),
        doc_prefix=raw["doc_prefix"], notes=tuple(raw.get("notes", ())), filename=filename,
        warnings=tuple(warnings), extra=_freeze(extra),
    )


# ---------------------------------------------------------------------------
# 로더 (캐시 단위)
# ---------------------------------------------------------------------------


class StandardsLoader:
    """한 디렉터리의 조항 데이터를 지연 로드·검증·캐시한다. 테스트는 임시 디렉터리로 직접 만든다."""

    def __init__(self, standards_dir: Optional[str] = None, strict_unknown_fields: bool = False):
        self.standards_dir = os.path.abspath(standards_dir or STANDARDS_DIR)
        self.strict_unknown_fields = strict_unknown_fields
        self._lock = threading.RLock()
        self._standards: Dict[Tuple[str, str], StandardData] = {}
        self._hls: Optional[HlsCommon] = None

    # -- 캐시 ---------------------------------------------------------------
    def clear_cache(self) -> None:
        with self._lock:
            self._standards.clear()
            self._hls = None

    # -- 로드 ---------------------------------------------------------------
    def get_hls_common(self) -> HlsCommon:
        with self._lock:
            if self._hls is not None:
                return self._hls
            raw = _read_json(os.path.join(self.standards_dir, HLS_FILENAME), HLS_FILENAME)
            issues, extra = _validate_hls_dict(raw, HLS_FILENAME, self.strict_unknown_fields)
            issues.raise_if_any()
            hls = _build_hls(raw, HLS_FILENAME, extra, issues.warnings)
            for w in hls.warnings:
                logger.warning(w)
            logger.info("standards loaded: hls_common v%s (%d clauses)", hls.schema_version, len(hls.clauses))
            self._hls = hls
            return hls

    def get_standard(self, standard_key: str, edition: str) -> StandardData:
        spec = STANDARD_REGISTRY.get(standard_key)
        if spec is None:
            raise EditionSelectionError("지원하지 않는 규격 키입니다: %r" % (standard_key,),
                                        "unsupported_standard", value=standard_key)
        if edition not in spec["editions"]:
            raise EditionSelectionError("지원하지 않는 개정판입니다: %s:%s" % (spec["display"], edition),
                                        "unsupported_edition", value=edition)
        with self._lock:
            cached = self._standards.get((standard_key, edition))
            if cached is not None:
                return cached
            filename = expected_filename(standard_key, edition)
            raw = _read_json(os.path.join(self.standards_dir, filename), filename)
            issues, file_extra = _validate_standard_dict(raw, filename, standard_key, edition,
                                                         self.strict_unknown_fields)
            issues.raise_if_any()
            data = _build_standard(raw, filename, file_extra, issues.warnings)
            self._check_carried_from(data)
            for w in data.warnings:
                logger.warning(w)
            logger.info("standards loaded: %s %s v%s (%d clauses)", standard_key, edition,
                        data.schema_version, len(data.clauses))
            self._standards[(standard_key, edition)] = data
            return data

    def _check_carried_from(self, data: StandardData) -> None:
        """AC-30: carried_from 대상이 이전 edition 파일에 있고 merge_key·요약이 글자 단위로 같아야 한다."""
        issues = _Issues(data.filename, False)
        for c in data.clauses:
            if c.carried_from is None:
                continue
            m = _CARRIED_RE.match(c.carried_from)
            src = self.get_standard(m.group(1), m.group(2))     # 형식 검증은 이미 통과함
            target = src.clause(m.group(3))
            loc = "clauses[%s]" % c.id
            if target is None:
                issues.add(loc, "carried_from 대상 %s 가 %s 에 없음" % (c.carried_from, src.filename))
            else:
                if target.merge_key != c.merge_key:
                    issues.add(loc, "carried_from 대상의 merge_key 가 다름: %s != %s" % (target.merge_key, c.merge_key))
                if target.requirement_summary != c.requirement_summary:
                    issues.add(loc, "carried_from 이 있지만 requirement_summary 가 이전 edition 과 글자 단위로 같지 않음")
        issues.raise_if_any()

    def load_all(self) -> StandardsCatalog:
        """레지스트리의 모든 (규격, edition) + hls_common 을 검증한다(앱 시작·AC-20용).

        디렉터리에 레지스트리에 없는 `*.json` 이 있으면 오류다(허용 목록과 파일의 1:1 대응).
        """
        hls = self.get_hls_common()
        expected = {HLS_FILENAME} | {expected_filename(k, e) for k, e in registry_pairs()}
        try:
            present = {n for n in os.listdir(self.standards_dir) if n.lower().endswith(".json")}
        except OSError as exc:
            raise StandardsDataError("표준 데이터 디렉터리를 읽을 수 없습니다: %s" % exc, filename=self.standards_dir)
        extras = sorted(present - expected)
        if extras:
            raise StandardsDataError("레지스트리에 없는 JSON 파일이 있음: %s" % ", ".join(extras), filename=extras[0])
        standards = {(k, e): self.get_standard(k, e) for k, e in registry_pairs()}
        self._cross_check_hls(hls, standards)
        return StandardsCatalog(hls=hls, standards=MappingProxyType(standards))

    @staticmethod
    def _cross_check_hls(hls: HlsCommon, standards: Mapping[Tuple[str, str], StandardData]) -> None:
        """edition 파일의 published_at 이 hls edition_info 와 같은지(다르면 경고)."""
        for key, info in hls.edition_info["standards"].items():
            for e in info["editions"]:
                data = standards.get((key, e["edition"]))
                if data is not None and data.published_at and data.published_at != e["published_at"]:
                    logger.warning("%s: published_at(%s) 이 hls_common edition_info(%s)와 다름",
                                   data.filename, data.published_at, e["published_at"])

    # -- 전환 일정 (FR-75) --------------------------------------------------
    def get_edition_info(self) -> Dict[str, Any]:
        """hls_common.json 의 `edition_info` 사본(JSON 직렬화 가능한 평범한 dict)."""
        return _thaw(self.get_hls_common().edition_info)

    # -- 노출 게이트 (FR-81, AC-26) -----------------------------------------
    def edition_exposure(self, standard_key: str, edition: str,
                         flags: Optional[Iterable[str]] = None) -> ExposureDecision:
        """공개 정책(환경 변수/flags)과 데이터 게이트(서명·toc_only)를 모두 통과해야 allowed.

        데이터 파일이 깨져 있으면 `StandardsDataError` 를 그대로 올린다(조용히 닫지 않는다).
        """
        data = self.get_standard(standard_key, edition)
        released = is_edition_released(standard_key, edition, flags)
        gate = evaluate_data_gate(data)
        reasons = []
        if not released:
            reasons.append("공개 전 edition (%s%s 환경 변수/설정이 꺼져 있음)" % (EDITION_FLAG_PREFIX, edition))
        reasons.extend(gate.reasons)
        return ExposureDecision(standard_key, edition, released and gate.ready, released, gate.ready, tuple(reasons))

    def get_default_edition(self, standard_key: str, respect_gate: bool = True,
                            flags: Optional[Iterable[str]] = None) -> str:
        """edition 을 생략한 입력에 적용할 edition (FR-72).

        설정된 기본값(2026/2018)을 쓰되, respect_gate 이고 그 edition 이 아직 노출 불가이면
        노출 가능한 가장 최신 edition 으로 내려간다(2026 기본값은 게이트 통과 뒤에 켜진다, FR-72).
        """
        spec = STANDARD_REGISTRY.get(standard_key)
        if spec is None:
            raise EditionSelectionError("지원하지 않는 규격 키입니다: %r" % (standard_key,),
                                        "unsupported_standard", value=standard_key)
        configured = spec["default_edition"]
        if not respect_gate:
            return configured
        candidates = [configured] + [e for e in sorted(spec["editions"], reverse=True) if e != configured]
        for ed in candidates:
            if self.edition_exposure(standard_key, ed, flags).allowed:
                return ed
        raise EditionSelectionError("%s 는 현재 제공되는 개정판이 없습니다." % spec["display"],
                                    "edition_not_available", value=standard_key)

    # -- 입력 정규화 (FR-71~73) ---------------------------------------------
    def normalize_target_isos(self, items: Any, enforce_exposure: bool = True,
                              flags: Optional[Iterable[str]] = None) -> List[StandardSelection]:
        """`target_isos` 입력을 정규화한다. 결과는 9001 -> 14001 -> 45001 순이다.

        - 1~3개, 항목은 정식 'ISO 9001:2015' 또는 약식 '9001:2026'/'9001'.
        - 같은 규격은 한 번만(두 edition 동시 선택 포함 거부). 규격마다 edition 은 독립(혼합 허용, FR-73).
        - edition 을 생략한 항목에만 기본 edition 을 적용한다(`defaulted=True`).
        - enforce_exposure(기본 True)이면 노출 게이트가 닫힌 edition 을 거부한다(`edition_not_available`).
          False 면 게이트를 보지 않는다(테스트·내부용). 이때 기본 edition 도 설정값 그대로다.
        허용되지 않는 입력은 `EditionSelectionError`(ValueError)이다.
        """
        if not isinstance(items, (list, tuple)):
            raise EditionSelectionError("target_isos 는 배열이어야 합니다.", "invalid_format", value=items)
        if len(items) == 0:
            raise EditionSelectionError("규격을 1개 이상 선택해야 합니다.", "empty")
        if len(items) > MAX_SELECTED_STANDARDS:
            raise EditionSelectionError("규격은 최대 %d개까지 선택할 수 있습니다." % MAX_SELECTED_STANDARDS,
                                        "too_many", value=len(items))
        parsed = [(item,) + parse_standard_input(item) for item in items]
        seen: Dict[str, Any] = {}
        for item, key, _ed in parsed:
            if key in seen:
                raise EditionSelectionError("같은 규격(%s)은 한 번만 선택할 수 있습니다 (개정판 동시 선택 불가)."
                                            % STANDARD_REGISTRY[key]["display"], "duplicate_standard", value=item)
            seen[key] = item
        out = []
        for item, key, ed in parsed:
            defaulted = ed is None
            if defaulted:
                ed = self.get_default_edition(key, respect_gate=enforce_exposure, flags=flags)
            elif enforce_exposure:
                decision = self.edition_exposure(key, ed, flags)
                if not decision.allowed:
                    raise EditionSelectionError("%s 는 아직 제공되지 않습니다." % standard_code_of(key, ed),
                                                "edition_not_available", value=item)
            out.append(StandardSelection(key, ed, standard_code_of(key, ed),
                                         STANDARD_REGISTRY[key]["tag"], defaulted))
        order = list(STANDARD_REGISTRY)
        out.sort(key=lambda s: order.index(s.standard_key))
        return out

    def resolve_manual_standards(self, target_isos: Any = None, target_iso: Any = None,
                                 enforce_exposure: bool = True,
                                 flags: Optional[Iterable[str]] = None) -> List[StandardSelection]:
        """세션 입력/저장된 form_data 에서 선택을 해석한다. `target_isos` 가 있으면 우선(8.1 하위호환).

        `target_isos` 가 없고 기존 `target_iso` 문자열만 있으면 `[target_iso]` 로 본다.
        """
        if target_isos is not None:
            return self.normalize_target_isos(target_isos, enforce_exposure, flags)
        if target_iso is not None:
            if not isinstance(target_iso, str):
                raise EditionSelectionError("target_iso 는 문자열이어야 합니다.", "invalid_format",
                                            field_name="target_iso", value=target_iso)
            return self.normalize_target_isos([target_iso], enforce_exposure, flags)
        raise EditionSelectionError("규격을 1개 이상 선택해야 합니다.", "empty")

    # -- UI 안내용 (FR-63, FR-75) -------------------------------------------
    def get_edition_options(self, flags: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        """규격별 edition 선택지·기본값·노출 여부·전환 일정(JSON 직렬화 가능)."""
        info = self.get_hls_common().edition_info["standards"]
        options = []
        for key, spec in STANDARD_REGISTRY.items():
            default = self.get_default_edition(key, respect_gate=True, flags=flags)
            pubs = {e["edition"]: e["published_at"] for e in info[key]["editions"]}
            editions = []
            for ed in spec["editions"]:
                d = self.edition_exposure(key, ed, flags)
                editions.append({"edition": ed, "standard_code": standard_code_of(key, ed),
                                 "published_at": pubs.get(ed), "exposed": d.allowed,
                                 "released": d.released, "data_ready": d.data_ready,
                                 "is_default": ed == default})
            options.append({"standard_key": key, "display": spec["display"], "number": spec["number"],
                            "tag": spec["tag"], "configured_default_edition": spec["default_edition"],
                            "default_edition": default, "editions": editions,
                            "schedule": _thaw(info[key].get("schedule"))})
        return options


# ---------------------------------------------------------------------------
# 모듈 수준 API (기본 디렉터리, 프로세스당 1회 파싱)
# ---------------------------------------------------------------------------

_default_loader: Optional[StandardsLoader] = None
_default_lock = threading.Lock()


def _loader() -> StandardsLoader:
    global _default_loader
    if _default_loader is None:
        with _default_lock:
            if _default_loader is None:
                _default_loader = StandardsLoader()
    return _default_loader


def clear_cache() -> None:
    """모듈 수준 캐시를 비운다(테스트용). 다음 호출에서 다시 파싱·검증한다."""
    global _default_loader
    with _default_lock:
        _default_loader = None


def get_standard(standard_key: str, edition: str) -> StandardData:
    return _loader().get_standard(standard_key, edition)


def get_hls_common() -> HlsCommon:
    return _loader().get_hls_common()


def load_all() -> StandardsCatalog:
    return _loader().load_all()


def get_edition_info() -> Dict[str, Any]:
    return _loader().get_edition_info()


def get_edition_options(flags: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    return _loader().get_edition_options(flags)


def edition_exposure(standard_key: str, edition: str, flags: Optional[Iterable[str]] = None) -> ExposureDecision:
    return _loader().edition_exposure(standard_key, edition, flags)


def get_default_edition(standard_key: str, respect_gate: bool = True,
                        flags: Optional[Iterable[str]] = None) -> str:
    return _loader().get_default_edition(standard_key, respect_gate, flags)


def normalize_target_isos(items: Any, enforce_exposure: bool = True,
                          flags: Optional[Iterable[str]] = None) -> List[StandardSelection]:
    return _loader().normalize_target_isos(items, enforce_exposure, flags)


def resolve_manual_standards(target_isos: Any = None, target_iso: Any = None,
                             enforce_exposure: bool = True,
                             flags: Optional[Iterable[str]] = None) -> List[StandardSelection]:
    return _loader().resolve_manual_standards(target_isos, target_iso, enforce_exposure, flags)
