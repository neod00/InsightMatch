"""국세청 사업자등록 상태조회.

공공데이터포털 「국세청_사업자등록정보 진위확인 및 상태조회 서비스」(무료)를 쓴다.
번호만 보내고 영업 상태만 받는다. 회사명·대표자 같은 다른 정보는 보내지 않는다.
(CBAM_Platform 의 src/lib/business-number-check.ts 와 같은 규칙)

결과
- active: 계속사업자 / suspended: 휴업 / closed: 폐업 / not_found: 국세청에 없는 번호
- unavailable: 키 미설정·장애·시간 초과. 이때는 막지 않고 「미확인」으로 남겨 관리자가 본다.

환경변수: NTS_BUSINESS_API_KEY (공공데이터포털 일반 인증키 Decoding 값)
"""
import os

import requests

NTS_STATUS_ENDPOINT = 'https://api.odcloud.kr/api/nts-businessman/v1/status'
NTS_TIMEOUT_SECONDS = 5

ACTIVE = 'active'
SUSPENDED = 'suspended'
CLOSED = 'closed'
NOT_FOUND = 'not_found'
UNAVAILABLE = 'unavailable'


def normalize_business_number(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def interpret_nts_status_code(code):
    if code == '01':
        return ACTIVE
    if code == '02':
        return SUSPENDED
    if code == '03':
        return CLOSED
    return NOT_FOUND


def check_business_status(business_number):
    api_key = os.environ.get('NTS_BUSINESS_API_KEY', '').strip()
    digits = normalize_business_number(business_number)
    if not api_key or len(digits) != 10:
        return UNAVAILABLE

    try:
        response = requests.post(
            NTS_STATUS_ENDPOINT,
            params={'serviceKey': api_key},
            json={'b_no': [digits]},
            headers={'Accept': 'application/json'},
            timeout=NTS_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return UNAVAILABLE
        rows = (response.json() or {}).get('data')
        row = rows[0] if isinstance(rows, list) and rows else None
        return interpret_nts_status_code(row.get('b_stt_cd')) if row else UNAVAILABLE
    except Exception:
        return UNAVAILABLE


def business_status_rejection_message(result):
    if result == NOT_FOUND:
        return ('국세청에 등록되지 않은 사업자등록번호입니다. 번호를 다시 확인해 주세요. '
                '새로 등록한 사업자는 반영까지 1~2일 걸릴 수 있습니다. '
                '공공기관·비영리단체(고유번호)는 문의하기로 알려 주시면 확인해 드립니다.')
    if result == CLOSED:
        return '국세청에서 폐업으로 확인되는 사업자등록번호입니다. 현재 영업 중인 회사의 번호를 입력해 주세요.'
    if result == SUSPENDED:
        return '국세청에서 휴업으로 확인되는 사업자등록번호입니다. 현재 영업 중인 회사의 번호를 입력해 주세요.'
    return ''
