"""ISO 매뉴얼 작성 예시 페이지 3개를 만든다.

    python tools/manual_samples/build.py

content.py 의 본문을 B안 문서 레이아웃(brand.css 14절)에 넣어 저장소 루트에
iso-9001-manual-sample.html / iso-14001-manual-sample.html / iso-45001-manual-sample.html 을 쓴다.
생성된 HTML 도 커밋한다(Vercel 은 정적 파일만 배포).
"""
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from content import COMPANY, SAMPLES  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
ORDER = ['9001', '14001', '45001']
LABEL = {'9001': 'ISO 9001 품질', '14001': 'ISO 14001 환경', '45001': 'ISO 45001 안전보건'}

e = html.escape
ARROW = ('<span class="hm-arrow" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none"><path d="M3 8h10M9 4l4 4-4 4" '
         'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>')


def para_text(text, sample):
    if text == '@scope':
        return sample['scope_text']
    if text == '@exclusion':
        return sample['exclusion'] or ''
    return text


def table_html(head, rows):
    out = ['<div class="table-scroll"><table><thead><tr>']
    out += [f'<th>{e(h)}</th>' for h in head]
    out.append('</tr></thead><tbody>')
    for row in rows:
        out.append('<tr>' + ''.join(f'<td>{e(c)}</td>' for c in row) + '</tr>')
    out.append('</tbody></table></div>')
    return ''.join(out)


def clause_html(clause, sample):
    parts = [f'<div class="clause"><h3>{e(clause["title"])}</h3>']
    if clause.get('quote'):
        parts.append(f'<div class="hm-legal-callout"><p>“{e(clause["quote"])}”</p></div>')
    for p in clause.get('paras', []):
        text = para_text(p, sample)
        if text:
            parts.append(f'<p>{e(text)}</p>')
    if clause.get('bullets'):
        parts.append('<ul>' + ''.join(f'<li>{e(b)}</li>' for b in clause['bullets']) + '</ul>')
    if clause.get('table'):
        parts.append(table_html(clause['table']['head'], clause['table']['rows']))
    parts.append('</div>')
    return ''.join(parts)


def article_html(key, sample):
    out = []
    out.append('<h2 id="info">문서 정보</h2>')
    out.append(table_html(['항목', '내용'], [
        ['회사', f'{COMPANY["name"]} (가상의 회사)'],
        ['사업 내용', COMPANY['business']],
        ['사업장 · 인원', f'{COMPANY["site"]} · {COMPANY["employees"]}'],
        ['적용 규격', sample['standard']],
        ['문서번호 · 개정', f'{sample["doc_no"]} · 개정 0'],
        ['승인', '대표이사'],
    ]))

    out.append('<h2 id="c1">1~3. 적용범위 · 인용표준 · 용어</h2>')
    out.append('<div class="clause"><h3>1. 적용범위</h3>'
               f'<p>{e(sample["scope_text"])}</p>'
               + (f'<p>{e(sample["exclusion"])}</p>' if sample['exclusion'] else '') + '</div>')
    out.append(f'<div class="clause"><h3>2. 인용표준</h3><p>{e(sample["standard"])} {e(sample["name"])} — 요구사항 및 사용지침</p></div>')
    out.append(f'<div class="clause"><h3>3. 용어와 정의</h3><p>이 매뉴얼의 용어는 {e(sample["standard"])}를 따른다. '
               '"관리책임자"는 대표이사가 지정한 시스템 운영 책임자를 말한다.</p></div>')

    for chapter in sample['chapters']:
        out.append(f'<h2 id="{chapter["id"]}">{e(chapter["title"])}</h2>')
        for clause in chapter['clauses']:
            out.append(clause_html(clause, sample))
        if chapter.get('tip'):
            out.append(f'<p class="is-small"><strong>작성 포인트</strong> · {e(chapter["tip"])}</p>')

    out.append('<h2 id="appendix">부록 A. 문서 목록</h2>')
    out.append('<p>매뉴얼(1단계) 아래에 실제 업무 방법을 정한 절차서(2단계)를 둡니다. 예시 회사의 문서 목록은 다음과 같습니다.</p>')
    out.append(table_html(['문서', '번호'], sample['appendix']))

    others = [k for k in ORDER if k != key]
    out.append('<div class="hm-legal-foot">'
               f'<h3>우리 회사 {e(sample["doc_title"])} 초안, 무료로 만들어 보세요</h3>'
               '<p>업종·규모·경영 이슈를 입력하면 AI가 회사에 맞춘 매뉴얼 초안을 만들어 드립니다. '
               '가입 후 표지부터 6절까지 무료로 볼 수 있습니다.</p>'
               '<p class="sample-cta">'
               f'<a class="hm-btn hm-btn-lime" href="ai-manual.html">무료로 매뉴얼 초안 만들기{ARROW}</a>'
               f'<a class="hm-btn hm-btn-ghost" href="index.html#diagnosis">전문가에게 검토받기{ARROW}</a></p>'
               '<p class="is-small">다른 규격 예시: '
               + ' · '.join(f'<a href="{SAMPLES[k]["slug"]}.html">{e(LABEL[k])} 매뉴얼</a>' for k in others)
               + '</p></div>')
    return '\n'.join(out)


def toc_items(sample):
    items = [('info', '문서 정보'), ('c1', '1~3. 적용범위·용어')]
    items += [(c['id'], c['title']) for c in sample['chapters']]
    items.append(('appendix', '부록 A. 문서 목록'))
    return items


def page(key):
    sample = SAMPLES[key]
    num = key
    title = f'ISO {num} {sample["doc_title"]} 작성 예시'
    toc = ''.join(f'<li><a href="#{i}">{e(t)}</a></li>' for i, t in toc_items(sample))
    switch = ''.join(
        f'<a href="{SAMPLES[k]["slug"]}.html"{" aria-current=\"page\"" if k == key else ""}>{e(LABEL[k])}</a>'
        for k in ORDER)
    with open(os.path.join(os.path.dirname(__file__), 'shell.html'), encoding='utf-8') as f:
        shell = f.read()
    return (shell
            .replace('{{TITLE}}', e(title))
            .replace('{{META_DESC}}', e(sample['meta_desc']))
            .replace('{{STANDARD}}', e(sample['standard']))
            .replace('{{DOC_NO}}', e(sample['doc_no']))
            .replace('{{NUM}}', e(num))
            .replace('{{EDITION_NOTE}}', e(sample['edition_note']))
            .replace('{{SWITCH}}', switch)
            .replace('{{TOC}}', toc)
            .replace('{{ARTICLE}}', article_html(key, sample)))


def main():
    for key in ORDER:
        path = os.path.join(ROOT, SAMPLES[key]['slug'] + '.html')
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(page(key))
        print('wrote', os.path.relpath(path, ROOT))


if __name__ == '__main__':
    main()
