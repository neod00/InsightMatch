"""
ISO Manual Generation Service
OpenAI gpt-5.4를 사용하여 ISO 시스템 매뉴얼/절차서 초안을 SSE 스트리밍으로 생성
requests 라이브러리로 직접 HTTP 스트리밍 구현 (SDK hanging 문제 회피)
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

SYSTEM_PROMPT = """당신은 20년 이상 경력의 수석 ISO 심사원이며, 한국 기업(제조/서비스업)의 경영시스템 구축 전문가입니다.
KAB(한국인정기구) 공인 심사원 자격을 보유하고 있으며, 500개 이상의 기업 인증 컨설팅을 수행했습니다.

## 당신의 역할
사용자가 제공한 기업 정보와 ISO 표준 요구사항 원문을 기반으로, 해당 기업에 맞춤화된 **ISO 시스템 매뉴얼 및 절차서 초안**을 작성합니다.

## 출력 규칙
1. **마크다운 포맷**으로 작성 (H1~H4, 표, 리스트 활용)
2. **공식적인 비즈니스 어투** 사용 ("~하여야 한다", "~을 보장한다")
3. ISO 표준의 **4~10절 요구사항을 빠짐없이** 반영
4. 기업의 업종, 규모, 현장 이슈에 맞게 **구체적으로 커스터마이징**
5. 각 절차서에 **목적 → 적용범위 → 책임과 권한 → 절차 → 관련 양식** 구조 적용
6. 실제 심사에서 통과할 수 있는 수준의 **실무적 내용** 포함

## 매뉴얼 구조 (반드시 이 순서로 작성)
1. 표지 (문서번호, 개정이력, 승인란)
2. 경영방침 선언 (최고경영자 명의)
3. 조직 상황 분석 (4절)
4. 리더십 및 의지표명 (5절)
5. 기획 - 리스크/기회 관리 (6절)
6. 지원 관리 (7절) - 문서관리 절차서, 교육훈련 절차서 포함
7. 운용 절차 (8절) - 해당 업종에 맞는 핵심 프로세스 절차서 포함
8. 성과 평가 (9절) - 내부심사 절차서, 경영검토 절차서 포함
9. 개선 절차 (10절) - 시정조치 절차서 포함
10. 부록: 문서 양식 목록

## 마무리 규칙
- 문서는 **부록(문서 양식 목록)까지만** 작성하고 깔끔하게 종료할 것
- 마지막에 "추가로 필요하시면~", "원하시면 이어서~" 등 **대화체 제안 문구를 절대 포함하지 말 것**
- "다음 단계", "참고 사항" 등의 별도 섹션을 추가하지 말 것
- 문서 자체로 완결되어야 함
"""

# 무료 버전: 1~5절(표지, 경영방침, 4절, 5절, 6절)까지만 생성
FREE_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    """## 매뉴얼 구조 (반드시 이 순서로 작성)
1. 표지 (문서번호, 개정이력, 승인란)
2. 경영방침 선언 (최고경영자 명의)
3. 조직 상황 분석 (4절)
4. 리더십 및 의지표명 (5절)
5. 기획 - 리스크/기회 관리 (6절)
6. 지원 관리 (7절) - 문서관리 절차서, 교육훈련 절차서 포함
7. 운용 절차 (8절) - 해당 업종에 맞는 핵심 프로세스 절차서 포함
8. 성과 평가 (9절) - 내부심사 절차서, 경영검토 절차서 포함
9. 개선 절차 (10절) - 시정조치 절차서 포함
10. 부록: 문서 양식 목록""",
    """## 매뉴얼 구조 (아래 5개 섹션만 작성)
1. 표지 (문서번호, 개정이력, 승인란)
2. 경영방침 선언 (최고경영자 명의)
3. 조직 상황 분석 (4절)
4. 리더십 및 의지표명 (5절)
5. 기획 - 리스크/기회 관리 (6절)

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

## ISO 표준 요구사항 원문 ({target_iso})
아래의 ISO 표준 요구사항을 기반으로 위 기업에 맞춤화된 시스템 매뉴얼/절차서를 작성해주세요:

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
1. 표지 (문서번호, 개정이력, 승인란)
2. 경영방침 선언 (최고경영자 명의)
3. 조직 상황 분석 (4절)
4. 리더십 및 의지표명 (5절)
5. 기획 - 리스크/기회 관리 (6절)
6. 지원 관리 (7절) - 문서관리 절차서, 교육훈련 절차서 포함
7. 운용 절차 (8절) - 해당 업종에 맞는 핵심 프로세스 절차서 포함
8. 성과 평가 (9절) - 내부심사 절차서, 경영검토 절차서 포함
9. 개선 절차 (10절) - 시정조치 절차서 포함
10. 부록: 문서 양식 목록""",
            """## 매뉴얼 구조 (아래 섹션만 이어서 작성 — 1~5절은 이미 작성됨, 6절부터 작성)
6. 지원 관리 (7절) - 문서관리 절차서, 교육훈련 절차서 포함
7. 운용 절차 (8절) - 해당 업종에 맞는 핵심 프로세스 절차서 포함
8. 성과 평가 (9절) - 내부심사 절차서, 경영검토 절차서 포함
9. 개선 절차 (10절) - 시정조치 절차서 포함
10. 부록: 문서 양식 목록

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
