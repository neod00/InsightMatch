"""'평가가 있는 컨설턴트만' 필터 — 리뷰가 하나도 없으면 숨긴다.

플랫폼 리뷰가 0건일 때 이 칸을 켜면 추천이 전부 사라져, 기업이 '등록된 전문가가 없다'로 읽었다.
받은 목록에 리뷰가 있는 컨설턴트가 생기면 다시 보인다. 숨길 때 켜져 있던 체크는 끈다.
"""
import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as fh:
        return fh.read()


def _function_body(src, signature):
    start = src.find(signature)
    if start < 0:
        return ''
    i = src.find('{', start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    return ''


class RatedFilterVisibilityTest(unittest.TestCase):
    def test_chip_has_id_and_render_syncs_first(self):
        html = _read('index.html')
        self.assertIn('id="filter-rated-chip"><input type="checkbox" id="filter-rated"', html)
        js = _read('script.js')
        render = _function_body(js, 'function renderConsultants()')
        self.assertIn('syncRatedFilterVisibility()', render)
        # 목록을 그리기 전에 맞춘다
        self.assertLess(render.find('syncRatedFilterVisibility()'), render.find("consultantList.innerHTML = ''"))

    @unittest.skipUnless(shutil.which('node'), 'node 가 없으면 건너뜀')
    def test_sync_behaviour(self):
        fn = _function_body(_read('script.js'), 'function syncRatedFilterVisibility()')
        self.assertTrue(fn)
        script = """
        const make = () => {
          const chip = { classes: new Set(), classList: { toggle(c, on) { on ? chip.classes.add(c) : chip.classes.delete(c); } } };
          const box = { checked: true };
          global.document = { getElementById: id => id === 'filter-rated-chip' ? chip : id === 'filter-rated' ? box : null };
          return { chip, box };
        };
        let allConsultants;
        FN
        const out = {};
        let t = make(); allConsultants = [{ reviews: 0 }, { reviews: null }];
        out.none = { changed: syncRatedFilterVisibility(), hidden: t.chip.classes.has('hidden'), checked: t.box.checked };
        t = make(); allConsultants = [{ reviews: 0 }, { reviews: 2 }];
        out.some = { changed: syncRatedFilterVisibility(), hidden: t.chip.classes.has('hidden'), checked: t.box.checked };
        t = make(); allConsultants = [];
        out.empty = { changed: syncRatedFilterVisibility(), hidden: t.chip.classes.has('hidden'), checked: t.box.checked };
        console.log(JSON.stringify(out));
        """.replace('FN', fn)
        res = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip())
        self.assertEqual(out['none'], {'changed': True, 'hidden': True, 'checked': False})
        self.assertEqual(out['some'], {'changed': False, 'hidden': False, 'checked': True})
        self.assertEqual(out['empty'], {'changed': True, 'hidden': True, 'checked': False})


if __name__ == '__main__':
    unittest.main()
