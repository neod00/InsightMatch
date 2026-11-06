"""InsightMatch 알림 메일 공용 레이아웃.

모든 알림 메일(견적 요청, 제안서 도착, 심사 결과, 비밀번호 재설정, 리마인더, 관리자 통지 …)이
이 모듈 하나로 HTML 과 텍스트 본문을 만든다.

디자인 원칙
- 사이트 brand.css 의 B안 "Momentum" 톤: 잉크색(#0E0F0C) 헤더 띠, 라임(#C3FB79) 포인트,
  따뜻한 회백색(#F0F1EA) 바깥 배경, 흰 카드. 라임은 면(띠·점·번호)에만 쓰고 밝은 배경 위 글자색으로 쓰지 않는다.
- 메일 클라이언트(Gmail, 네이버 메일, Outlook)가 <style> 을 지우거나 무시해도 깨지지 않도록
  레이아웃은 전부 table + 인라인 style 로 만든다. <style> 은 모바일 여백·다크모드 보정에만 쓴다.
- 이모지·화살표 장식은 쓰지 않는다. 버튼은 왼쪽 정렬한 알약 모양 하나.
- 사용자·관리자가 입력한 값은 전부 여기서 이스케이프한다. 이미 안전한 HTML 조각만 Safe 로 감싸 넘긴다.
"""

import html
import os
import re
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

# 브랜드 토큰 (brand.css 1. TOKENS 와 같은 값)
INK = '#0E0F0C'
CHAR = '#2A2C27'
MUTED = '#575A52'
LINE = '#E3E4DC'
SURFACE = '#F0F1EA'
WHITE = '#FFFFFF'
LIME = '#C3FB79'
LIME_DEEP = '#AEEA5C'
LIME_SOFT = '#EBFCD3'
SUCCESS = '#2C6E1F'
ERROR = '#C8382B'
ERROR_SOFT = '#FDECEA'
WARN = '#8A5300'
WARN_SOFT = '#FCF1DC'
WARN_LINE = '#D99A1E'

FONT = ("-apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', "
        "'맑은 고딕', 'Noto Sans KR', 'Segoe UI', Roboto, sans-serif")

# 상태 알약·안내 상자의 색 (밝은 배경 위 글자 대비 4.5:1 이상)
TONES = {
    'default': {'pill_bg': LIME_SOFT, 'pill_fg': SUCCESS, 'bar': LIME_DEEP},
    'warn': {'pill_bg': WARN_SOFT, 'pill_fg': WARN, 'bar': WARN_LINE},
    'alert': {'pill_bg': ERROR_SOFT, 'pill_fg': ERROR, 'bar': ERROR},
    'neutral': {'pill_bg': SURFACE, 'pill_fg': CHAR, 'bar': INK},
}

COMPANY_NAME = 'OpenBrain Limited'
SERVICE_TAGLINE = 'ISO 인증 컨설턴트 매칭 플랫폼'

TRANSACTIONAL_NOTICE = (
    '이 메일은 회원님의 InsightMatch 활동(계정·프로젝트 진행 상황)에 대한 안내입니다. '
    '광고성 정보가 아닙니다.'
)
ADMIN_NOTICE = '이 메일은 InsightMatch 운영자에게 보내는 관리용 통지입니다.'

# BASE_URL 환경 변수가 없을 때 쓰는 운영 주소. 정식 도메인이 생기면 BASE_URL 만 바꾸면 되고,
# 이 값은 환경 변수가 빠졌을 때 링크가 죽지 않게 하는 예비값이다.
DEFAULT_SITE_URL = 'https://insight-match-ashen.vercel.app'


def support_email() -> str:
    """메일 하단 문의 주소. 사이트(약관·문의 페이지)와 같은 주소를 기본값으로 쓰고,
    회사 도메인 주소가 생기면 SUPPORT_EMAIL 환경 변수만 바꾸면 된다."""
    return (os.environ.get('SUPPORT_EMAIL') or 'openbrain.main@gmail.com').strip()


def site_url() -> str:
    """요청 맥락 없이 메일을 만들 때 쓰는 사이트 주소 (기본 인자용)."""
    return (os.environ.get('BASE_URL') or DEFAULT_SITE_URL).rstrip('/')


class Safe(str):
    """이미 이스케이프된 HTML 조각. 레이아웃이 다시 이스케이프하지 않는다."""


def esc(value) -> str:
    """값을 HTML 본문용으로 이스케이프한다. 줄바꿈은 <br> 로."""
    if isinstance(value, Safe):
        return str(value)
    return html.escape('' if value is None else str(value)).replace('\n', '<br>')


def esc_attr(value) -> str:
    return html.escape('' if value is None else str(value), quote=True)


_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'[ \t]+')


def to_text(value) -> str:
    """텍스트 본문용: HTML 조각이면 태그를 걷어낸다."""
    if value is None:
        return ''
    s = str(value)
    if isinstance(value, Safe):
        s = s.replace('<br>', '\n').replace('<br/>', '\n')
        s = html.unescape(_TAG_RE.sub('', s))
    return _WS_RE.sub(' ', s).strip()


def clean_label(label: str) -> str:
    """버튼·링크 문구 끝에 붙어 오던 '→' 장식을 뗀다 (호출부에서 넘어오는 옛 문구 대비)."""
    return re.sub(r'\s*[→›»>]+\s*$', '', str(label or '')).strip()


class Block:
    """본문 한 덩어리. HTML 과 텍스트 본문을 함께 들고 다닌다."""

    def __init__(self, html_part: str, text_part: str = ''):
        self.html = html_part
        self.text = text_part


# ----------------------------------------------------------------------
# 본문 부품
# ----------------------------------------------------------------------

def paragraph(content, muted: bool = False, size: int = 15) -> Block:
    color, cls = (MUTED, 'im-muted') if muted else (CHAR, 'im-body')
    return Block(
        f'<p class="{cls}" style="margin:0 0 14px; font-size:{size}px; line-height:1.75; color:{color};">'
        f'{esc(content)}</p>',
        to_text(content),
    )


def info_table(rows: Sequence[Tuple[str, object]]) -> Block:
    """항목·값 2열 표. 값이 비어 있으면 '-' 로 채운다."""
    cells, texts = [], []
    for label, value in rows:
        shown = value if value not in (None, '', []) else '-'
        cells.append(
            '<tr>'
            f'<td class="im-label im-muted im-line" width="124" valign="top" '
            f'style="width:124px; padding:13px 12px 13px 0; border-bottom:1px solid {LINE}; '
            f'font-size:13px; line-height:1.6; color:{MUTED}; vertical-align:top;">{esc(label)}</td>'
            f'<td class="im-text im-line" valign="top" '
            f'style="padding:13px 0; border-bottom:1px solid {LINE}; font-size:15px; line-height:1.6; '
            f'font-weight:600; color:{INK}; vertical-align:top; word-break:keep-all; overflow-wrap:break-word;">{esc(shown)}</td>'
            '</tr>'
        )
        texts.append(f'- {to_text(label)}: {to_text(shown)}')
    return Block(
        '<table role="presentation" class="im-rule" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%; margin:22px 0 24px; border-collapse:collapse; border-top:2px solid {INK};">'
        f'{"".join(cells)}</table>',
        '\n'.join(texts),
    )


def notice(title: Optional[str], lines: Iterable, tone: str = 'neutral') -> Block:
    """회색 바탕 + 왼쪽 색 띠 안내 상자 (사유·유효기간·주의사항)."""
    bar = TONES.get(tone, TONES['neutral'])['bar']
    lines = [l for l in lines if l not in (None, '')]
    title_html = (
        f'<p class="im-text" style="margin:0 0 6px; font-size:14px; font-weight:700; color:{INK};">{esc(title)}</p>'
        if title else ''
    )
    body_html = ''.join(
        f'<p class="im-body" style="margin:0 0 4px; font-size:14px; line-height:1.7; color:{CHAR};">{esc(l)}</p>'
        for l in lines
    )
    text = '\n'.join(([f'[{to_text(title)}]'] if title else []) + [to_text(l) for l in lines])
    return Block(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%; margin:20px 0;"><tr>'
        f'<td class="im-panel" style="background:{SURFACE}; border-left:3px solid {bar}; '
        f'border-radius:0 10px 10px 0; padding:16px 18px;">{title_html}{body_html}</td>'
        '</tr></table>',
        text,
    )


def steps(title: str, items: Sequence) -> Block:
    """번호 매긴 다음 순서 안내."""
    rows = []
    for i, item in enumerate(items, 1):
        rows.append(
            '<tr>'
            '<td width="34" valign="top" style="width:34px; padding:0 0 12px; vertical-align:top;">'
            f'<span class="im-num" style="display:inline-block; width:22px; height:22px; line-height:22px; '
            f'border-radius:11px; background:{INK}; color:{LIME}; font-size:12px; font-weight:700; '
            f'text-align:center;">{i}</span></td>'
            f'<td class="im-body" valign="top" style="padding:1px 0 12px; font-size:14px; line-height:1.65; '
            f'color:{CHAR}; vertical-align:top;">{esc(item)}</td>'
            '</tr>'
        )
    text = '\n'.join([f'[{title}]'] + [f'{i}. {to_text(item)}' for i, item in enumerate(items, 1)])
    return Block(
        f'<p class="im-text" style="margin:26px 0 12px; font-size:14px; font-weight:700; color:{INK};">{esc(title)}</p>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;">'
        f'{"".join(rows)}</table>',
        text,
    )


def item_card(title, message, link: Optional[str] = None, link_label: str = '바로 보기') -> Block:
    """알림 묶음 메일의 알림 한 건."""
    link_html = (
        f'<p style="margin:10px 0 0; font-size:13px;"><a class="im-link" href="{esc_attr(link)}" target="_blank" '
        f'style="color:{INK}; font-weight:700; text-decoration:underline;">{esc(link_label)}</a></p>'
        if link else ''
    )
    message_html = (
        f'<p class="im-body" style="margin:4px 0 0; font-size:14px; line-height:1.65; color:{CHAR};">{esc(message)}</p>'
        if message else ''
    )
    text = to_text(title) + (f'\n  {to_text(message)}' if message else '') + (f'\n  {link}' if link else '')
    return Block(
        '<table role="presentation" class="im-box" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%; margin:0 0 10px; border:1px solid {LINE}; border-radius:12px; border-collapse:separate;">'
        '<tr><td style="padding:16px 18px;">'
        f'<p class="im-text" style="margin:0; font-size:15px; font-weight:700; line-height:1.5; color:{INK};">{esc(title)}</p>'
        f'{message_html}{link_html}</td></tr></table>',
        text,
    )


def stat_row(stats: Sequence[Tuple[str, object, str]]) -> Block:
    """숫자 타일 한 줄. stats: [(라벨, 값, 'default'|'warn'|'alert')]"""
    cells = []
    width = int(100 / max(len(stats), 1))
    for i, (label, value, tone) in enumerate(stats):
        color = {'alert': ERROR, 'warn': WARN}.get(tone, INK)
        gap = 'padding-left:6px;' if i else ''
        gap += 'padding-right:6px;' if i < len(stats) - 1 else ''
        cells.append(
            f'<td width="{width}%" valign="top" style="{gap}">'
            '<table role="presentation" class="im-box" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%; border:1px solid {LINE}; border-radius:12px; border-collapse:separate;"><tr>'
            '<td style="padding:16px 18px;">'
            f'<p style="margin:0; font-size:28px; font-weight:800; line-height:1.2; color:{color};">{esc(value)}</p>'
            f'<p class="im-muted" style="margin:4px 0 0; font-size:12px; color:{MUTED};">{esc(label)}</p>'
            '</td></tr></table></td>'
        )
    return Block(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%; margin:18px 0 6px;"><tr>{"".join(cells)}</tr></table>',
        '\n'.join(f'- {label}: {value}' for label, value, _ in stats),
    )


def data_table(title: str, headers: Sequence[str], rows: Sequence[Sequence], empty_text: str = '없음',
               tone: str = 'neutral', align_last_right: bool = True) -> Block:
    """여러 열 표 (오류 요약 등). rows 의 셀 값은 이스케이프한다(Safe 는 그대로)."""
    color = {'alert': ERROR, 'warn': WARN}.get(tone, INK)
    n = len(headers)

    def _cell_style(idx, head=False):
        right = align_last_right and idx == n - 1
        base = (f'padding:{"8px" if head else "11px"} 8px; border-bottom:1px solid {LINE}; '
                f'text-align:{"right" if right else "left"}; vertical-align:top;')
        if head:
            return base + f' font-size:12px; font-weight:600; color:{MUTED};'
        return base + f' font-size:13px; line-height:1.6; color:{CHAR}; word-break:keep-all; overflow-wrap:break-word;'

    head_html = ''.join(
        f'<th class="im-muted im-line" style="{_cell_style(i, True)}">{esc(h)}</th>' for i, h in enumerate(headers)
    )
    if rows:
        body_html = ''.join(
            '<tr>' + ''.join(
                f'<td class="im-body im-line" style="{_cell_style(i)}">{esc(c)}</td>' for i, c in enumerate(r)
            ) + '</tr>'
            for r in rows
        )
        text_rows = [' | '.join(to_text(c) for c in r) for r in rows]
    else:
        body_html = (f'<tr><td class="im-muted" colspan="{n}" style="padding:12px 8px; font-size:13px; '
                     f'color:{MUTED};">{esc(empty_text)}</td></tr>')
        text_rows = [empty_text]
    return Block(
        f'<p style="margin:26px 0 8px; font-size:14px; font-weight:700; color:{color};">{esc(title)}</p>'
        '<table role="presentation" class="im-rule" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%; border-collapse:collapse; border-top:2px solid {INK};">'
        f'<tr>{head_html}</tr>{body_html}</table>',
        '\n'.join([f'[{title}]'] + text_rows),
    )


def button(label: str, url: str) -> Block:
    """왼쪽 정렬 알약 버튼. Outlook 은 border-radius 를 무시하지만 배경은 td bgcolor 로 유지된다."""
    label = clean_label(label)
    return Block(
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:28px 0 6px;"><tr>'
        f'<td class="im-btn-td" bgcolor="{INK}" style="background:{INK}; border-radius:999px; mso-padding-alt:14px 28px;">'
        f'<a class="im-btn" href="{esc_attr(url)}" target="_blank" '
        f'style="display:inline-block; padding:14px 28px; font-size:15px; font-weight:700; line-height:1.2; '
        f'color:{WHITE}; text-decoration:none; border-radius:999px; letter-spacing:-0.01em;">{esc(label)}</a>'
        '</td></tr></table>',
        f'{label}: {url}',
    )


def fallback_link(url: str) -> Block:
    """버튼이 안 열릴 때 복사해 쓸 주소."""
    return Block(
        f'<p class="im-muted" style="margin:14px 0 0; font-size:12px; line-height:1.7; color:{MUTED}; '
        'word-break:break-all;">버튼이 열리지 않으면 아래 주소를 복사해 브라우저 주소창에 붙여 넣어 주세요.<br>'
        f'<a class="im-link" href="{esc_attr(url)}" target="_blank" style="color:{CHAR}; text-decoration:underline;">'
        f'{esc(url)}</a></p>',
        '',
    )


# ----------------------------------------------------------------------
# 전체 틀
# ----------------------------------------------------------------------

# 다크모드 보정. Apple Mail·iOS 메일·Outlook(앱/웹 일부)은 prefers-color-scheme 를 따르고,
# Outlook.com 은 [data-ogsc]/[data-ogsb] 접두로 다시 쓴다. Gmail·네이버는 자체 반전을 하므로
# 인라인 색만으로도 읽히게 대비를 잡아 두었다.
_DARK_RULES = {
    '.im-bg': f'background:{INK} !important;',
    '.im-card': 'background:#181A16 !important; border-color:#2E312B !important;',
    '.im-head': 'background:#20231E !important;',
    '.im-text': 'color:#F3F4EE !important;',
    '.im-body': 'color:#D9DBD3 !important;',
    '.im-muted': 'color:#A9ACA2 !important;',
    '.im-line': 'border-color:#2E312B !important;',
    '.im-rule': 'border-color:#F3F4EE !important;',
    '.im-panel': 'background:#20231E !important;',
    '.im-box': 'border-color:#2E312B !important;',
    '.im-btn-td': f'background:{LIME} !important;',
    '.im-btn': f'color:{INK} !important;',
    '.im-link': f'color:{LIME} !important;',
    '.im-num': f'background:{LIME} !important; color:{INK} !important;',
    '.im-pill-default': f'background:#26331A !important; color:{LIME} !important;',
    '.im-pill-warn': 'background:#3A2E14 !important; color:#F2C46B !important;',
    '.im-pill-alert': 'background:#3A1C18 !important; color:#F29A8F !important;',
    '.im-pill-neutral': 'background:#20231E !important; color:#F3F4EE !important;',
}


def _style_block() -> str:
    media = ' '.join(f'{sel} {{ {rule} }}' for sel, rule in _DARK_RULES.items())
    ogsc = ' '.join(f'[data-ogsc] {sel}, [data-ogsb] {sel} {{ {rule} }}' for sel, rule in _DARK_RULES.items())
    return (
        ':root { color-scheme: light dark; supported-color-schemes: light dark; }\n'
        'body, table, td, a { -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }\n'
        'table, td { mso-table-lspace:0pt; mso-table-rspace:0pt; }\n'
        'a[x-apple-data-detectors] { color:inherit !important; text-decoration:none !important; }\n'
        '@media screen and (max-width:620px) {\n'
        '  .im-wrap { padding:16px 10px 24px !important; }\n'
        '  .im-pad { padding-left:22px !important; padding-right:22px !important; }\n'
        '  .im-h1 { font-size:21px !important; }\n'
        '  .im-label { width:92px !important; }\n'
        '}\n'
        f'@media (prefers-color-scheme: dark) {{ {media} }}\n'
        f'{ogsc}\n'
    )


def render_email(
    *,
    subject: str,
    heading: str,
    body: Sequence[Block],
    eyebrow: Optional[str] = None,
    tone: str = 'default',
    preheader: Optional[str] = None,
    cta: Optional[Tuple[str, str]] = None,
    after_cta: Sequence[Block] = (),
    footer_note: Optional[str] = None,
    audience: str = 'member',
) -> Tuple[str, str]:
    """메일 한 통을 (html, text) 로 만든다.

    Args:
        subject: <title> 과 텍스트 본문 첫 줄에 쓴다.
        heading: 카드 맨 위 제목.
        body: 본문 부품(Block) 목록. paragraph/info_table/notice/steps/... 로 만든다.
        eyebrow: 제목 위 상태 알약 문구 (예: '승인 완료').
        tone: 알약 색 — default(라임)/warn/alert/neutral.
        preheader: 받은편지함 미리보기 문구 (본문에는 보이지 않음).
        cta: (버튼 문구, URL). 왼쪽 정렬 버튼 하나.
        after_cta: 버튼 아래에 둘 부품 (주소 직접 붙여넣기 안내 등).
        footer_note: 푸터 첫 줄 위에 붙는 이 메일만의 안내.
        audience: 'member' 면 거래적 메일 안내, 'admin' 이면 관리용 통지 안내를 푸터에 넣는다.
    """
    pill = TONES.get(tone, TONES['default'])
    year = datetime.now().year
    contact = support_email()

    eyebrow_html = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 16px;"><tr>'
        f'<td class="im-pill-{tone}" style="background:{pill["pill_bg"]}; color:{pill["pill_fg"]}; '
        'border-radius:999px; padding:5px 12px; font-size:12px; font-weight:700; line-height:1.4; '
        f'letter-spacing:0.01em;">{esc(eyebrow)}</td></tr></table>'
        if eyebrow else ''
    )
    cta_block = button(*cta) if cta else None
    parts = list(body) + ([cta_block] if cta_block else []) + list(after_cta)
    body_html = ''.join(p.html for p in parts)

    notice_text = ADMIN_NOTICE if audience == 'admin' else TRANSACTIONAL_NOTICE
    note_html = (
        f'<p class="im-muted" style="margin:0 0 8px; font-size:12px; line-height:1.7; color:{MUTED};">{esc(footer_note)}</p>'
        if footer_note else ''
    )
    pre = esc(preheader or '')
    # 미리보기 문구 뒤에 본문 첫 글자가 딸려 나오지 않도록 공백 문자를 채운다.
    pre_pad = '&#8199;&#847;' * 40

    page = f"""<!DOCTYPE html>
<html lang="ko" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{esc(subject)}</title>
<style>
{_style_block()}</style>
<!--[if mso]><style>table, td, p, a, span, h1 {{ font-family:'Malgun Gothic', Arial, sans-serif !important; }}</style><![endif]-->
</head>
<body class="im-bg" style="margin:0; padding:0; width:100%; background:{SURFACE}; font-family:{FONT};">
<div style="display:none; max-height:0; overflow:hidden; mso-hide:all; font-size:1px; line-height:1px; color:{SURFACE}; opacity:0;">{pre}{pre_pad}</div>
<table role="presentation" class="im-bg" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{SURFACE}" style="width:100%; background:{SURFACE};">
<tr><td class="im-wrap" align="center" style="padding:32px 12px 40px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%; max-width:600px;">
    <tr><td class="im-head im-pad" bgcolor="{INK}" style="background:{INK}; border-radius:16px 16px 0 0; padding:22px 36px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;"><tr>
        <td style="font-family:{FONT}; font-size:19px; font-weight:800; letter-spacing:-0.02em; line-height:1; color:{WHITE};">Insight<span style="color:{LIME}; font-size:13px; padding:0 1px; vertical-align:2px;">&#9679;</span>Match</td>
        <td align="right" style="font-family:{FONT}; font-size:12px; line-height:1; color:#A9ACA2;">{SERVICE_TAGLINE}</td>
      </tr></table>
    </td></tr>
    <tr><td height="4" bgcolor="{LIME}" style="height:4px; background:{LIME}; font-size:0; line-height:0;">&nbsp;</td></tr>
    <tr><td class="im-card im-pad" bgcolor="{WHITE}" style="background:{WHITE}; border:1px solid {LINE}; border-top:0; border-radius:0 0 16px 16px; padding:36px 36px 34px; font-family:{FONT}; word-break:keep-all; overflow-wrap:break-word;">
      {eyebrow_html}
      <h1 class="im-h1 im-text" style="margin:0 0 20px; font-size:24px; line-height:1.4; font-weight:800; letter-spacing:-0.02em; color:{INK};">{esc(heading)}</h1>
      {body_html}
    </td></tr>
    <tr><td class="im-pad" style="padding:24px 36px 0; font-family:{FONT}; word-break:keep-all; overflow-wrap:break-word;">
      {note_html}
      <p class="im-muted" style="margin:0 0 8px; font-size:12px; line-height:1.7; color:{MUTED};"><strong class="im-text" style="color:{INK};">InsightMatch</strong> &middot; {SERVICE_TAGLINE}<br>문의 <a class="im-link" href="mailto:{esc_attr(contact)}" style="color:{CHAR}; text-decoration:underline;">{esc(contact)}</a></p>
      <p class="im-muted" style="margin:0 0 8px; font-size:12px; line-height:1.7; color:{MUTED};">{esc(notice_text)}</p>
      <p class="im-muted" style="margin:0; font-size:12px; line-height:1.7; color:{MUTED};">&copy; {year} {COMPANY_NAME}</p>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>
"""

    text_lines: List[str] = [heading, '']
    for p in parts:
        if p.text:
            text_lines += [p.text, '']
    if footer_note:
        text_lines.append(footer_note)
    text_lines += [
        '-' * 30,
        f'InsightMatch · {SERVICE_TAGLINE}',
        f'문의 {contact}',
        notice_text,
        f'(c) {year} {COMPANY_NAME}',
    ]
    return page, '\n'.join(text_lines)
