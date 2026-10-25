"""매뉴얼 목차(outline) 병합 엔진 (PRD T3: FR-30~36, FR-73~76, FR-80, 7.3 R1~R11, 7.3.1).

입력은 `standards_loader` 가 정규화한 `StandardSelection` 목록이고, 출력은 결정적(deterministic)
outline 이다. 병합은 LLM 이 아니라 이 코드가 한다(FR-31). 프롬프트 조립은 T4 몫이다.

핵심 원칙
- 병합 판단은 JSON 의 `merge_key` 로만 한다(R6). 규격·edition 이름으로 분기하는 코드
  (`if edition == ...`, `if standard_key == ...`)를 두지 않는다(FR-82). 차이는 모두 데이터
  (`merge_key`, `outline_role`, `hls_common` 의 `ims_*` 필드)에서 온다.
- 단일 규격(R1): 매뉴얼 번호 = 그 edition 의 조항번호, 섹션 여부 = `outline_role`, 순서 = 파일 순서.
  `ims_*` 규칙은 쓰지 않는다(REVIEW_NOTES I.8).
- IMS(2~3개):
  * 절·조항(x, x.y)은 `merge_key` 로 묶고 번호는 Q→E→S 중 그 키를 가진 첫 규격의 번호(R2·R3·R11 a).
    번호가 이미 다른 키에 쓰였으면 같은 절의 마지막 번호 다음으로 보낸다(R4·R11 b).
  * 번호 부여 전에 `ims_key_absorption`(status adopted)을 적용한다(R11, G7).
  * x.y.z 는 `ims_subclause_order` 에 있는 키를 알파벳 순번(x.y.A, x.y.B …)으로 펼친다(R5).
    `ims_fold_into_parent` 키는 펼치지 않고 상위 x.y 에 접는다. 나머지는 `outline_role` 을 따른다.
  * 제목: hls 공통 제목(+`title_variants`, R7) → `ims_subclause_titles` → 우선 규격 조항 제목 순.
- 모든 원 조항은 정확히 한 섹션의 `refs` 에 들어간다(R8 커버리지 단위, AC-4). `role` 로 구분한다.
    heading  : 그 섹션의 제목이 되는 조항 (관련 조항 태그에 표시)
    absorbed : `ims_key_absorption` 으로 다른 섹션에 흡수된 조항 (태그에 표시)
    fold     : `outline_role: fold` 라 상위 섹션 본문에 접힌 조항 (태그에 표시하지 않음)
    ims_fold : `ims_fold_into_parent` 로 IMS 에서 상위 섹션에 접힌 조항 (태그에 표시하지 않음)
- phase 는 섹션 번호의 장(chapter)으로 정한다(R9).
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from services import standards_loader as sl

__all__ = [
    "OutlineError", "ClauseRef", "OutlineSection", "MergedProcedure", "OutlineMeta", "ManualOutline",
    "AppendixC", "build_outline", "merge_procedures", "build_appendix_c", "render_appendix_c_markdown",
    "outline_to_dict", "ROLE_HEADING", "ROLE_ABSORBED", "ROLE_FOLD", "ROLE_IMS_FOLD", "TAGGED_ROLES",
]

ROLE_HEADING = "heading"
ROLE_ABSORBED = "absorbed"
ROLE_FOLD = "fold"
ROLE_IMS_FOLD = "ims_fold"
TAGGED_ROLES = (ROLE_HEADING, ROLE_ABSORBED)
_ROLE_ORDER = {ROLE_HEADING: 0, ROLE_ABSORBED: 1, ROLE_FOLD: 2, ROLE_IMS_FOLD: 3}

_ABSORPTION_CONDITION = "numbering_primary_lacks_key"
_ABSORPTION_ACTIVE_STATUS = "adopted"


class OutlineError(ValueError):
    """선택 목록이 outline 을 만들 수 없는 형태(빈 목록, 같은 규격 중복, 데이터 불일치)."""


# ---------------------------------------------------------------------------
# 출력 구조 (모두 읽기 전용)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClauseRef:
    """섹션에 포함된 원 조항 하나 (규격·edition·조항 id)."""
    tag: str
    standard_key: str
    edition: str
    standard_code: str
    clause_id: str
    merge_key: str
    title_ko: str
    mandatory: bool
    role: str

    @property
    def label(self) -> str:
        """태그 표기용 'Q 6.1.2'."""
        return "%s %s" % (self.tag, self.clause_id)


@dataclass(frozen=True)
class OutlineSection:
    manual_no: str                 # "4", "6.1", "6.1.A", "8.8"
    level: int                     # 1=절, 2=조항, 3=하위 항목
    parent: Optional[str]          # 상위 섹션 manual_no
    title: str
    title_source: str              # standard / hls_common / hls_title_variant / ims_subclause_titles
    merge_key: str                 # 섹션 대표 키 (heading refs 의 공통 merge_key)
    phase: int                     # R9
    mandatory: bool                # 포함 조항 중 하나라도 mandatory
    refs: Tuple[ClauseRef, ...]    # 이 섹션에 대응하는 모든 원 조항(R8)
    related_tag: Optional[str]     # IMS 의 '[관련 조항: Q 6.1.2 · E 6.1.5]', 단일은 None
    coverage_keywords: Tuple[str, ...]
    procedures: Tuple[str, ...]    # 연결 절차서 id (IMS 통합 번호 반영, 예: CP08)

    @property
    def tagged_refs(self) -> Tuple[ClauseRef, ...]:
        return tuple(r for r in self.refs if r.role in TAGGED_ROLES)

    @property
    def folded_refs(self) -> Tuple[ClauseRef, ...]:
        return tuple(r for r in self.refs if r.role not in TAGGED_ROLES)


@dataclass(frozen=True)
class MergedProcedure:
    id: str
    doc_no: str                    # "SMS-CP08"
    name: str
    merge_key: str
    process_category: str
    department: str
    input: str
    output: str
    records: str
    retention: str
    media: str
    iso_clauses: Tuple[ClauseRef, ...]     # 원 규격 조항 (role 은 heading 고정)
    manual_sections: Tuple[str, ...]       # 위 조항이 들어간 outline 섹션 번호
    sources: Tuple[Tuple[str, str], ...]   # ((tag, 원 절차서 id), ...)
    replaces: Tuple[str, ...]              # 통합 절차서가 대체한 원 id (예: EP02, SP02)

    @property
    def iso_clause_label(self) -> str:
        return " · ".join(r.label for r in self.iso_clauses)


@dataclass(frozen=True)
class OutlineMeta:
    is_ims: bool
    standard_codes: Tuple[str, ...]        # ("ISO 9001:2026", "ISO 45001:2018")
    tags: Tuple[str, ...]
    cover_standards: str                   # "ISO 9001:2026 · ISO 45001:2018" (FR-74)
    doc_title: str                         # FR-36
    system_name: str
    policy_name: str                       # FR-35
    objective_name: str
    filename_numbers: str                  # "9001_45001" (파일명에는 edition 을 넣지 않음, FR-74)


@dataclass(frozen=True)
class ManualOutline:
    selections: Tuple[sl.StandardSelection, ...]
    meta: OutlineMeta
    sections: Tuple[OutlineSection, ...]
    procedures: Tuple[MergedProcedure, ...]
    _by_no: Mapping[str, OutlineSection] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_by_no", {s.manual_no: s for s in self.sections})

    @property
    def is_ims(self) -> bool:
        return self.meta.is_ims

    def section(self, manual_no: str) -> Optional[OutlineSection]:
        return self._by_no.get(manual_no)

    def children(self, manual_no: Optional[str]) -> Tuple[OutlineSection, ...]:
        return tuple(s for s in self.sections if s.parent == manual_no)

    def sections_for_phase(self, phase: int) -> Tuple[OutlineSection, ...]:
        return tuple(s for s in self.sections if s.phase == phase)

    def coverage_targets(self, phase: Optional[int] = None) -> Tuple[OutlineSection, ...]:
        """커버리지 점검 대상(R8): mandatory 섹션. 접힌 조항은 상위 섹션이 있으면 충족."""
        return tuple(s for s in self.sections if s.mandatory and (phase is None or s.phase == phase))

    def section_of_clause(self, tag: str, clause_id: str) -> Optional[OutlineSection]:
        for s in self.sections:
            for r in s.refs:
                if r.tag == tag and r.clause_id == clause_id:
                    return s
        return None


# ---------------------------------------------------------------------------
# 내부 보조
# ---------------------------------------------------------------------------


def _num_key(manual_no: str) -> Tuple[Any, ...]:
    """'8.10' > '8.9', 'x.y.A' 정렬용."""
    out = []
    for part in manual_no.split("."):
        out.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return tuple(out)


def _letters(index: int) -> str:
    """0->A, 25->Z, 26->AA."""
    s = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


def _dedupe(items: Iterable[str]) -> Tuple[str, ...]:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)


def _chapter_of(manual_no: str) -> str:
    return manual_no.split(".")[0]


class _Ctx:
    """선택된 규격 데이터와 hls 를 묶어 둔 작업 문맥."""

    def __init__(self, selections: Sequence[sl.StandardSelection], loader: Optional[sl.StandardsLoader]):
        if not selections:
            raise OutlineError("규격을 1개 이상 선택해야 합니다.")
        keys = [s.standard_key for s in selections]
        if len(set(keys)) != len(keys):
            raise OutlineError("같은 규격은 한 번만 선택할 수 있습니다: %s" % ", ".join(keys))
        if len(keys) > sl.MAX_SELECTED_STANDARDS:
            raise OutlineError("규격은 최대 %d개까지 선택할 수 있습니다." % sl.MAX_SELECTED_STANDARDS)
        get_std = loader.get_standard if loader else sl.get_standard
        get_hls = loader.get_hls_common if loader else sl.get_hls_common
        self.hls: sl.HlsCommon = get_hls()
        order = list(self.hls.standard_order)
        for k in keys:
            if k not in order:
                raise OutlineError("hls_common.standard_order 에 없는 규격: %s" % k)
        # 입력 순서와 무관하게 Q→E→S(standard_order) 순으로 고정한다(결정성).
        self.selections: Tuple[sl.StandardSelection, ...] = tuple(
            sorted(selections, key=lambda s: order.index(s.standard_key)))
        self.standards: Tuple[sl.StandardData, ...] = tuple(
            get_std(s.standard_key, s.edition) for s in self.selections)
        for sel, std in zip(self.selections, self.standards):
            if std.tag != sel.tag:
                raise OutlineError("선택 태그(%s)와 데이터 태그(%s)가 다름: %s" % (sel.tag, std.tag, std.filename))
        self.is_ims = len(self.standards) > 1
        self.tag_index = {t: i for i, t in enumerate(self.hls.tag_order)}
        self.hls_by_key = {c.merge_key: c for c in self.hls.clauses}
        self.clause_index = [{c.id: i for i, c in enumerate(std.clauses)} for std in self.standards]

    def ref(self, si: int, clause: sl.Clause, role: str) -> ClauseRef:
        std = self.standards[si]
        return ClauseRef(tag=std.tag, standard_key=std.standard_key, edition=std.edition,
                         standard_code=std.standard_code, clause_id=clause.id, merge_key=clause.merge_key,
                         title_ko=clause.title_ko, mandatory=clause.mandatory, role=role)


class _Bucket:
    """만들어지는 섹션 하나."""

    def __init__(self, merge_key: str, level: int):
        self.merge_key = merge_key
        self.level = level
        self.members: List[Tuple[int, sl.Clause, str]] = []   # (규격 index, 조항, role)
        self.manual_no: Optional[str] = None
        self.parent: Optional["_Bucket"] = None
        self.title = ""
        self.title_source = ""

    def heading_members(self) -> List[Tuple[int, sl.Clause, str]]:
        return [m for m in self.members if m[2] == ROLE_HEADING]

    def primary(self) -> Tuple[int, sl.Clause, str]:
        heads = self.heading_members()
        return min(heads, key=lambda m: m[0])


def _sorted_refs(ctx: _Ctx, bucket: _Bucket) -> Tuple[ClauseRef, ...]:
    # 태그 대상(heading·absorbed)을 먼저 규격 순(Q→E→S)으로, 같은 규격 안에서는 heading → absorbed.
    # 접힌 조항은 그 뒤에 규격 순·파일 순으로.
    def key(m):
        si, clause, role = m
        tagged = role in TAGGED_ROLES
        return (0 if tagged else 1, si, _ROLE_ORDER[role] if tagged else 0, ctx.clause_index[si][clause.id])
    return tuple(ctx.ref(si, c, role) for si, c, role in sorted(bucket.members, key=key))


# ---------------------------------------------------------------------------
# 단일 규격 (R1)
# ---------------------------------------------------------------------------


def _build_single(ctx: _Ctx) -> List[_Bucket]:
    std = ctx.standards[0]
    by_clause: Dict[str, _Bucket] = {}
    buckets: List[_Bucket] = []
    for c in std.clauses:
        if c.level == 1 or c.outline_role == "section":
            b = _Bucket(c.merge_key, c.level)
            b.members.append((0, c, ROLE_HEADING))
            b.manual_no = c.id
            b.title, b.title_source = c.title_ko, "standard"
            b.parent = by_clause.get(c.parent) if c.parent else None
            by_clause[c.id] = b
            buckets.append(b)
        else:
            # fold: 가장 가까운 섹션 조상에 접는다.
            target = by_clause.get(c.parent)
            if target is None:
                raise OutlineError("%s %s: 접을 상위 섹션이 없음" % (std.filename, c.id))
            target.members.append((0, c, ROLE_FOLD))
            by_clause[c.id] = target
    return buckets


# ---------------------------------------------------------------------------
# IMS (R2~R11)
# ---------------------------------------------------------------------------


def _ims_title(ctx: _Ctx, bucket: _Bucket) -> Tuple[str, str]:
    """R7: hls 공통 제목(+선택 태그의 넓은 제목) → ims_subclause_titles → 우선 규격 제목."""
    selected_tags = {std.tag for std in ctx.standards}
    hc = ctx.hls_by_key.get(bucket.merge_key)
    if hc is not None:
        for v in hc.title_variants:
            if v["requires_tag"] in selected_tags:
                return v["title_ko"], "hls_title_variant"
        return hc.title_ko, "hls_common"
    t = ctx.hls.ims_subclause_titles.get(bucket.merge_key)
    if t:
        return t, "ims_subclause_titles"
    return bucket.primary()[1].title_ko, "standard"


def _absorption_map(ctx: _Ctx) -> Dict[str, str]:
    """`ims_key_absorption` 중 이번 조합에 적용되는 {흡수될 key: into key}."""
    primary = ctx.standards[0]           # 번호를 정하는 우선 규격 = Q→E→S 중 첫 선택 규격
    present = {c.merge_key for std in ctx.standards for c in std.clauses}
    out: Dict[str, str] = {}
    for rule in ctx.hls.ims_key_absorption:
        if rule.get("status") != _ABSORPTION_ACTIVE_STATUS:
            continue
        if rule.get("condition") != _ABSORPTION_CONDITION:
            raise OutlineError("알 수 없는 ims_key_absorption condition: %r" % (rule.get("condition"),))
        key, into = rule["key"], rule["into"]
        if primary.clause_by_merge_key(key) is None and into in present and key in present:
            out[key] = into
    return out


def _build_ims(ctx: _Ctx) -> List[_Bucket]:
    absorb = _absorption_map(ctx)
    fold_into_parent = set(ctx.hls.ims_fold_into_parent)
    # x.y 의 merge_key -> 펼칠 하위 키 순서 (hls id 가 아니라 merge_key 로 대응해 edition 번호 차이를 피한다)
    sub_order: Dict[str, Tuple[str, ...]] = {}
    for hls_id, keys in ctx.hls.ims_subclause_order.items():
        hc = ctx.hls.clause(hls_id)
        if hc is None:
            raise OutlineError("ims_subclause_order 의 '%s' 가 hls_common.clauses 에 없음" % hls_id)
        sub_order[hc.merge_key] = tuple(keys)

    # 1) 절·조항(level 1~2): merge_key 로 묶는다. 흡수 대상은 into 버킷으로 보낸다.
    top: Dict[str, _Bucket] = {}
    top_order: List[str] = []
    placed: Dict[Tuple[int, str], _Bucket] = {}     # (규격 index, 조항 id) -> 버킷
    deferred_absorb: List[Tuple[int, sl.Clause]] = []
    for si, std in enumerate(ctx.standards):
        for c in std.clauses:
            if c.level > 2:
                continue
            if c.level == 2 and c.outline_role == "fold":
                continue                             # 아래 3) 에서 절 섹션에 접는다
            if c.merge_key in absorb:
                deferred_absorb.append((si, c))
                continue
            b = top.get(c.merge_key)
            if b is None:
                b = top[c.merge_key] = _Bucket(c.merge_key, c.level)
                top_order.append(c.merge_key)
            elif b.level != c.level:
                raise OutlineError("merge_key '%s' 의 level 이 규격마다 다름" % c.merge_key)
            b.members.append((si, c, ROLE_HEADING))
            placed[(si, c.id)] = b
    for si, c in deferred_absorb:
        b = top.get(absorb[c.merge_key])
        if b is None:
            raise OutlineError("흡수 대상 섹션 '%s' 가 없음" % absorb[c.merge_key])
        b.members.append((si, c, ROLE_ABSORBED))
        placed[(si, c.id)] = b

    # 부모 연결 (level 2 -> 절 버킷)
    for key in top_order:
        b = top[key]
        if b.level == 1:
            continue
        si, c, _ = b.primary()
        parent = placed.get((si, c.parent))
        if parent is None:
            raise OutlineError("%s %s: 상위 절 섹션이 없음" % (ctx.standards[si].filename, c.id))
        b.parent = parent

    # 2) 번호 부여 (R11 a/b, R4 일반화). 우선 규격 순으로 자연 번호를 먼저 잡고, 충돌한 키를 절 끝에 붙인다.
    claims = sorted(top_order, key=lambda k: (top[k].primary()[0], _num_key(top[k].primary()[1].id)))
    used: Dict[str, str] = {}
    bumped: List[str] = []
    for k in claims:
        cand = top[k].primary()[1].id
        if cand in used:
            bumped.append(k)
        else:
            used[cand] = k
            top[k].manual_no = cand
    for k in bumped:
        b = top[k]
        if b.level == 1:
            raise OutlineError("절 번호 충돌: %s" % b.primary()[1].id)
        chapter = b.parent.manual_no
        last = max(int(n.split(".")[1]) for n in used if n.count(".") == 1 and _chapter_of(n) == chapter)
        b.manual_no = "%s.%d" % (chapter, last + 1)
        used[b.manual_no] = k

    # 3) 하위 조항(level 3+): 펼치기(R5) / ims_fold / fold
    sub_buckets: Dict[Tuple[int, str], _Bucket] = {}   # (id(parent bucket), merge_key)
    sub_by_parent: Dict[int, List[_Bucket]] = {}
    for si, std in enumerate(ctx.standards):
        for c in std.clauses:
            if c.level == 1:
                continue
            if c.level == 2:
                if c.outline_role == "fold" and c.merge_key not in absorb:
                    parent = placed.get((si, c.parent))
                    parent.members.append((si, c, ROLE_FOLD))
                    placed[(si, c.id)] = parent
                continue
            parent = placed.get((si, c.parent))
            if parent is None:
                raise OutlineError("%s %s: 상위 섹션이 없음" % (std.filename, c.id))
            if parent.level >= 3:
                parent.members.append((si, c, ROLE_FOLD))      # 4단계 이상은 상위 항목에 접는다
                placed[(si, c.id)] = parent
                continue
            order = sub_order.get(parent.merge_key, ())
            if c.merge_key in fold_into_parent:
                parent.members.append((si, c, ROLE_IMS_FOLD))
                placed[(si, c.id)] = parent
            elif c.merge_key in order or c.outline_role == "section":
                key = (id(parent), c.merge_key)
                b = sub_buckets.get(key)
                if b is None:
                    b = sub_buckets[key] = _Bucket(c.merge_key, 3)
                    b.parent = parent
                    sub_by_parent.setdefault(id(parent), []).append(b)
                b.members.append((si, c, ROLE_HEADING))
                placed[(si, c.id)] = b
            else:
                parent.members.append((si, c, ROLE_FOLD))
                placed[(si, c.id)] = parent

    # 알파벳 순번: ims_subclause_order 순서, 목록 밖 키는 (우선 규격, 조항 순서) 순으로 뒤에.
    all_parents = {id(b): b for b in top.values()}
    for pid, subs in sub_by_parent.items():
        parent = all_parents[pid]
        order = sub_order.get(parent.merge_key, ())

        def sk(b: _Bucket):
            si, c, _ = b.primary()
            if b.merge_key in order:
                return (0, order.index(b.merge_key), 0, 0)
            return (1, 0, si, ctx.clause_index[si][c.id])
        for i, b in enumerate(sorted(subs, key=sk)):
            b.manual_no = "%s.%s" % (parent.manual_no, _letters(i))

    # 4) 제목과 정렬
    buckets: List[_Bucket] = list(top.values()) + list(sub_buckets.values())
    for b in buckets:
        b.title, b.title_source = _ims_title(ctx, b)
    return buckets


def _order_buckets(buckets: List[_Bucket]) -> List[_Bucket]:
    """전위 순회(절 → 조항 → 하위 항목), 형제는 번호 순."""
    kids: Dict[Optional[int], List[_Bucket]] = {}
    for b in buckets:
        kids.setdefault(id(b.parent) if b.parent else None, []).append(b)
    out: List[_Bucket] = []

    def walk(pid):
        for b in sorted(kids.get(pid, []), key=lambda x: _num_key(x.manual_no)):
            out.append(b)
            walk(id(b))
    walk(None)
    return out


# ---------------------------------------------------------------------------
# 절차서 병합 (FR-04, FR-34, R10)
# ---------------------------------------------------------------------------

_YEARS_RE = re.compile(r"([0-9]+)\s*년")
_STATUTORY = "법정 보존기간"


def _split_list(text: str) -> List[str]:
    """'a, b(c, d), e' -> ['a', 'b(c, d)', 'e'] (괄호 안 쉼표는 나누지 않음)."""
    out, buf, depth = [], [], 0
    for ch in text:
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf).strip())
    return [x for x in out if x]


def _union_text(values: Sequence[str], sep: str = ", ") -> str:
    items: List[str] = []
    for v in values:
        items.extend(_split_list(v))
    return sep.join(_dedupe(items))


def _union_media(values: Sequence[str]) -> str:
    items: List[str] = []
    for v in values:
        for part in _split_list(v):
            items.extend(p.strip() for p in part.split("/") if p.strip())
    return "/".join(_dedupe(items))


def _longest_retention(values: Sequence[str]) -> str:
    """R10: 가장 긴 기간. 같으면 첫 규격 값. 법정 기간 표기가 있으면 유지."""
    def years(v):
        nums = [int(n) for n in _YEARS_RE.findall(v)]
        return max(nums) if nums else -1
    best = values[0]
    for v in values[1:]:
        if years(v) > years(best):
            best = v
    if any(_STATUTORY in v for v in values) and _STATUTORY not in best:
        best = "%s 또는 %s" % (best, _STATUTORY)
    return best


def _merge_procedures(ctx: _Ctx) -> Tuple[Tuple[dict, ...], Dict[Tuple[int, str], str]]:
    """반환: (병합 절차서 초안 목록, {(규격 index, 원 id): 병합 id})."""
    groups: Dict[str, List[Tuple[int, sl.Procedure]]] = {}
    order: List[str] = []
    for si, std in enumerate(ctx.standards):
        for p in std.procedures:
            if p.merge_key not in groups:
                groups[p.merge_key] = []
                order.append(p.merge_key)
            groups[p.merge_key].append((si, p))
    ims_by_key = {p["merge_key"]: p for p in ctx.hls.ims_procedures}
    drafts: List[dict] = []
    id_map: Dict[Tuple[int, str], str] = {}
    for key in order:
        members = groups[key]
        ids = _dedupe(p.id for _, p in members)
        integrated = ims_by_key.get(key) if len(members) > 1 else None
        if integrated is not None:
            tags_present = {ctx.standards[si].tag for si, _ in members}
            replaces = dict(integrated["replaces"])
            for si, p in members:
                tag = ctx.standards[si].tag
                if replaces.get(tag) != p.id:
                    raise OutlineError("ims_procedures %s 의 replaces 가 %s %s 와 맞지 않음"
                                       % (integrated["id"], tag, p.id))
            if not tags_present <= set(replaces):
                raise OutlineError("ims_procedures %s 가 대체하지 않는 규격이 있음" % integrated["id"])
            new_id, name = integrated["id"], integrated["name"]
            department = integrated.get("department") or members[0][1].department
            category = integrated.get("process_category") or members[0][1].process_category
            replaced = tuple(p.id for _, p in members)
        elif len(ids) == 1:
            first = members[0][1]
            new_id, name, department, category = first.id, first.name, first.department, first.process_category
            replaced = ()
        else:
            raise OutlineError("merge_key '%s' 의 절차서 id 가 서로 다른데(%s) hls_common.ims_procedures 에 통합 번호가 없음"
                               % (key, ", ".join(ids)))
        for si, p in members:
            id_map[(si, p.id)] = new_id
        iso_refs = []
        for si, p in members:
            std = ctx.standards[si]
            for cid in p.iso_clauses:
                iso_refs.append(ctx.ref(si, std.clause(cid), ROLE_HEADING))
        drafts.append(dict(
            id=new_id, name=name, merge_key=key, process_category=category, department=department,
            input=_union_text([p.input for _, p in members]),
            output=_union_text([p.output for _, p in members]),
            records=_union_text([p.records for _, p in members]),
            retention=_longest_retention([p.retention for _, p in members]),
            media=_union_media([p.media for _, p in members]),
            iso_clauses=tuple(iso_refs),
            sources=tuple((ctx.standards[si].tag, p.id) for si, p in members),
            replaces=replaced,
        ))
    return tuple(drafts), id_map


def _ordered_procedures(ctx: _Ctx, drafts: Sequence[dict]) -> List[dict]:
    """IMS: 여러 규격이 공유·통합한 절차서를 먼저(CP00~, CP08~), 그다음 규격 고유 절차서를 Q→E→S 순으로.
    같은 군 안에서는 파일 등장 순서. 단일 규격은 파일 순서 그대로."""
    def key(item):
        idx, d = item
        shared = len(d["sources"]) > 1
        return (0 if shared else 1 + ctx.tag_index[d["sources"][0][0]], idx)
    return [d for _, d in sorted(enumerate(drafts), key=key)]


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def _meta(ctx: _Ctx) -> OutlineMeta:
    nm = ctx.hls.ims_naming
    codes = tuple(std.standard_code for std in ctx.standards)
    numbers = [sl.STANDARD_REGISTRY[std.standard_key]["number"] for std in ctx.standards]
    if ctx.is_ims:
        fields = nm["fields_separator"].join(ctx.hls.tag_labels[std.tag] for std in ctx.standards)
        code_str = nm["codes_format"].format(numbers=nm["codes_separator"].join(numbers))
        doc_title = nm["doc_title_ims"].format(codes=code_str)
        system_name = nm["system_ims"]
        policy = nm["policy_ims"].format(fields=fields)
        objective = nm["objective_ims"].format(fields=fields)
    else:
        std = ctx.standards[0]
        doc_title = nm["doc_title_single"].format(system_noun=std.system_noun)
        system_name = std.system_noun
        policy = nm["policy_single"].format(policy_noun=std.policy_noun)
        objective = std.objective_noun
    return OutlineMeta(is_ims=ctx.is_ims, standard_codes=codes, tags=tuple(s.tag for s in ctx.standards),
                       cover_standards=" · ".join(codes), doc_title=doc_title, system_name=system_name,
                       policy_name=policy, objective_name=objective, filename_numbers="_".join(numbers))


def build_outline(selections: Sequence[sl.StandardSelection],
                  loader: Optional[sl.StandardsLoader] = None) -> ManualOutline:
    """정규화된 선택(1~3개)으로 결정적 outline 을 만든다.

    selections: `standards_loader.normalize_target_isos()` 결과(순서는 무관).
    loader: 테스트용 디렉터리 로더. 없으면 모듈 기본 로더(api/standards).
    데이터가 병합 규칙을 만족하지 못하면 `OutlineError`, 데이터 파일 문제는 `StandardsDataError`.
    """
    ctx = _Ctx(selections, loader)
    buckets = _build_ims(ctx) if ctx.is_ims else _build_single(ctx)
    drafts, id_map = _merge_procedures(ctx)

    ordered = _order_buckets(buckets)
    sections: List[OutlineSection] = []
    clause_to_no: Dict[Tuple[str, str], str] = {}
    for b in ordered:
        refs = _sorted_refs(ctx, b)
        phase = sl.PHASE_BY_CHAPTER[_chapter_of(b.manual_no)]
        for r in refs:
            clause_to_no[(r.tag, r.clause_id)] = b.manual_no
        for si, c, _ in b.members:
            if c.phase != phase:
                raise OutlineError("%s %s: phase %d 가 섹션 %s 의 phase %d 와 다름"
                                   % (ctx.standards[si].filename, c.id, c.phase, b.manual_no, phase))
        tagged = [r for r in refs if r.role in TAGGED_ROLES]
        related = ("[관련 조항: %s]" % " · ".join(r.label for r in tagged)) if ctx.is_ims else None
        keywords = []
        procs = []
        for si, c, role in sorted(b.members, key=lambda m: (m[2] not in TAGGED_ROLES, m[0],
                                                            ctx.clause_index[m[0]][m[1].id])):
            if role in TAGGED_ROLES:
                keywords.extend(c.coverage_keywords)
            procs.extend(id_map[(si, pid)] for pid in c.linked_procedures)
        sections.append(OutlineSection(
            manual_no=b.manual_no, level=b.level, parent=b.parent.manual_no if b.parent else None,
            title=b.title, title_source=b.title_source, merge_key=b.merge_key, phase=phase,
            # IMS 에서 제목으로 펼쳐진 하위 항목(예: 9001 9.1.2 고객만족)은 원 조항이 fold(mandatory=false)여도
            # 매뉴얼에 독립 섹션으로 쓰이므로 커버리지 점검 대상이다(사용자 결정 2026-10-25).
            mandatory=any(r.mandatory for r in refs) or (ctx.is_ims and bool(tagged)),
            refs=refs, related_tag=related,
            coverage_keywords=_dedupe(keywords), procedures=_dedupe(procs),
        ))

    procedures = []
    for d in _ordered_procedures(ctx, drafts):
        secs = _dedupe(clause_to_no[(r.tag, r.clause_id)] for r in d["iso_clauses"])
        procedures.append(MergedProcedure(doc_no=ctx.hls.doc_prefix + d["id"],
                                          manual_sections=tuple(sorted(secs, key=_num_key)), **d))
    return ManualOutline(selections=ctx.selections, meta=_meta(ctx), sections=tuple(sections),
                         procedures=tuple(procedures))


def merge_procedures(selections: Sequence[sl.StandardSelection],
                     loader: Optional[sl.StandardsLoader] = None) -> Tuple[MergedProcedure, ...]:
    """선택 규격의 절차서 목록(IMS 는 CP 공통 병합·CP08/CP09 통합 반영, R10)."""
    return build_outline(selections, loader).procedures


# ---------------------------------------------------------------------------
# 부록 C (FR-33, FR-74)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppendixC:
    columns: Tuple[str, ...]                          # 규격별 머리글 ("ISO 9001:2026", ...)
    rows: Tuple[Tuple[str, str, Tuple[str, ...]], ...]  # (manual_no, title, 규격별 셀)


def _cell(refs: Sequence[ClauseRef]) -> str:
    tagged = [r.clause_id for r in refs if r.role in TAGGED_ROLES]
    folded = [r.clause_id for r in refs if r.role not in TAGGED_ROLES]
    if not tagged and not folded:
        return "—"
    text = ", ".join(tagged)
    if folded:
        text = (text + " " if text else "") + "(%s)" % ", ".join(folded)
    return text


def build_appendix_c(outline: ManualOutline) -> AppendixC:
    """매뉴얼 번호 ↔ 규격별 조항 대응표 데이터. 접힌 조항은 괄호로 표시한다. 모든 원 조항이 한 번씩 나온다."""
    columns = outline.meta.standard_codes
    rows = []
    for s in outline.sections:
        cells = tuple(_cell([r for r in s.refs if r.standard_code == code]) for code in columns)
        rows.append((s.manual_no, s.title, cells))
    return AppendixC(columns=tuple(columns), rows=tuple(rows))


def render_appendix_c_markdown(outline: ManualOutline, heading: str = "부록 C. 규격 조항 대조표") -> str:
    """간단한 마크다운 표. 제목 수준(#)과 배치는 T4 가 정한다."""
    data = build_appendix_c(outline)
    esc = lambda t: t.replace("|", "\\|")
    lines = [heading, "", "| 매뉴얼 번호 | 제목 | %s |" % " | ".join(data.columns),
             "|---|---|%s|" % "|".join("---" for _ in data.columns)]
    for no, title, cells in data.rows:
        lines.append("| %s | %s | %s |" % (no, esc(title), " | ".join(esc(c) for c in cells)))
    lines.append("")
    lines.append("※ 괄호 안 조항은 해당 매뉴얼 항목 본문에 함께 다룬다.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 직렬화
# ---------------------------------------------------------------------------


def _ref_dict(r: ClauseRef) -> Dict[str, Any]:
    return {"tag": r.tag, "standard_code": r.standard_code, "clause_id": r.clause_id,
            "merge_key": r.merge_key, "title_ko": r.title_ko, "mandatory": r.mandatory, "role": r.role}


def outline_to_dict(outline: ManualOutline) -> Dict[str, Any]:
    """JSON 직렬화 가능한 전체 사본."""
    m = outline.meta
    return {
        "selections": [{"standard_key": s.standard_key, "edition": s.edition,
                        "standard_code": s.standard_code, "tag": s.tag} for s in outline.selections],
        "meta": {"is_ims": m.is_ims, "standard_codes": list(m.standard_codes), "tags": list(m.tags),
                 "cover_standards": m.cover_standards, "doc_title": m.doc_title,
                 "system_name": m.system_name, "policy_name": m.policy_name,
                 "objective_name": m.objective_name, "filename_numbers": m.filename_numbers},
        "sections": [{
            "manual_no": s.manual_no, "level": s.level, "parent": s.parent, "title": s.title,
            "title_source": s.title_source, "merge_key": s.merge_key, "phase": s.phase,
            "mandatory": s.mandatory, "related_tag": s.related_tag,
            "coverage_keywords": list(s.coverage_keywords), "procedures": list(s.procedures),
            "refs": [_ref_dict(r) for r in s.refs],
        } for s in outline.sections],
        "procedures": [{
            "id": p.id, "doc_no": p.doc_no, "name": p.name, "merge_key": p.merge_key,
            "process_category": p.process_category, "department": p.department, "input": p.input,
            "output": p.output, "records": p.records, "retention": p.retention, "media": p.media,
            "iso_clauses": [r.label for r in p.iso_clauses], "manual_sections": list(p.manual_sections),
            "sources": [list(x) for x in p.sources], "replaces": list(p.replaces),
        } for p in outline.procedures],
    }
