"""Local production-build UI evidence. Requires Playwright and Chrome, never a board.
Run from any directory: python probe_mobile_browser.py --repo <worktree>
Synthetic data is injected only by this test harness, never by product code.
"""
import argparse
import json
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright
from mobile_preview_data import presentation

parser = argparse.ArgumentParser()
parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[5])
args = parser.parse_args()
ROOT = args.repo.resolve()
DEST = ROOT / 'docs/verification/evidence/display-350-mobile-20261103'
DEST.mkdir(parents=True, exist_ok=True)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'frontend/apps/vm-screens/dist'), **kwargs)
    def do_GET(self):
        if self.path.startswith('/display/'):
            self.path = '/index.html'
        super().do_GET()
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
results = []
cases = [
    ('landscape', 915, 412, 'preparing'),
    ('s24-portrait', 360, 780, 'applied'),
    ('s24-landscape', 780, 360, 'preparing'),
    ('s24-landscape-toolbar', 780, 300, 'applied'),
    ('portrait', 412, 915, 'applied'),
    ('small-portrait', 360, 640, 'uncertain'),
    ('small-landscape', 740, 360, 'applied'),
    ('narrow', 320, 568, 'preparing'),
    ('fault', 915, 412, 'fault'),
    ('disconnect', 915, 412, 'disconnect'),
]
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        try:
            for name, width, height, phase in cases:
                page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
                data = presentation('applied' if phase in ('fault', 'disconnect') else phase)
                if phase == 'fault':
                    data['decisions'][data['state']['vms'][0]['vm_id']] = {'kind':'app_error','label':'앱 오류','tone':'danger'}
                writes = []
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('request', lambda req: writes.append(req.url) if req.method != 'GET' else None)
                def feed(route):
                    data['state']['sequence'] += 1
                    route.fulfill(json=data)
                page.route('**/api/v1/display/presentation', feed)
                page.goto(f'http://127.0.0.1:{server.server_port}/display/mobile')
                wanted = '주행 모드 미확인' if phase == 'uncertain' else '자율 모드 선택 · 구동 준비 중' if phase == 'preparing' else '자율주행'
                page.get_by_text(wanted, exact=True).wait_for()
                if phase == 'disconnect':
                    page.unroute('**/api/v1/display/presentation')
                    page.route('**/api/v1/display/presentation', lambda route: route.fulfill(status=503, json={'code':'test-disconnect'}))
                    page.get_by_role('alert').wait_for()
                    page.get_by_text('주행 모드 미확인', exact=True).wait_for()
                    assert page.get_by_text('현재 구동 VM1', exact=True).count() == 0
                if phase == 'fault':
                    assert 'VM1 · 앱 오류' in page.get_by_role('status').inner_text()
                page.evaluate('''async () => {await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));window.scrollTo(0,0)}''')
                dims = page.evaluate('''() => ({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,headerTop:document.querySelector('header').getBoundingClientRect().top})''')
                assert dims['width'] <= width and dims['headerTop'] >= 0, dims
                if width >= 600 and height >= 360 and phase not in ('fault', 'disconnect'):
                    assert dims['height'] <= height, (dims, page.locator('main > *').evaluate_all('(es) => es.map(e => ({tag:e.tagName, h:e.getBoundingClientRect().height, top:e.getBoundingClientRect().top}))'))
                assert not writes and not errors, (writes, errors)
                assert page.locator('button,iframe,video').count() == 0
                page.evaluate('''() => {const e=document.createElement('div');e.textContent='LOCAL SYNTHETIC DATA · NOT BOARD';e.style='position:fixed;right:8px;bottom:2px;font:10px sans-serif;color:#f8b84e;z-index:99999;background:#101821';document.body.append(e)}''')
                page.screenshot(path=str(DEST / f'{name}.png'), full_page=True)
                recovered = None
                if phase == 'disconnect':
                    page.unroute('**/api/v1/display/presentation')
                    page.route('**/api/v1/display/presentation', feed)
                    page.get_by_text('현재 구동 VM1', exact=True).wait_for()
                    assert page.get_by_role('alert').count() == 0
                    recovered = True
                results.append(dict(case=name, viewport=[width,height], dimensions=dims, writes=writes, errors=errors, recovered=recovered))
                page.close()
        finally:
            browser.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join(3)
(DEST / 'local-visual-summary.json').write_text(json.dumps({'environment':'local Windows Chrome / synthetic API / production build; NOT physical Galaxy or USB', 'results':results}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'passed':len(results), 'no_horizontal_overflow':True, 'no_write_requests':True}))
