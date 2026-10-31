"""Silent browser check for the classroom grid and scoped bench controls."""
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def main():
    protocol = yaml.safe_load((ROOT/'protocols/pipetting-demo.yaml').read_text())
    state = dict(bench='2', run=None, recording=False, current_step=None, steps={}, warnings=[],
                 chat=[], objects={}, angle=None, note=None, question=None, protocol=protocol)
    rows = [dict(bench=str(n), student=f'Student {n}', recording=True, run=f'run-{n}',
                 current_step=n-1, instruction=protocol['steps'][n-1]['text'],
                 attention=n==2, warning='Change the tip' if n==2 else '', question=None,
                 url=f'/benches/{n}/') for n in (2,1,3)]
    calls = []
    with sync_playwright() as p:
        options = dict(headless=True, args=['--mute-audio'])
        if os.getenv('CHROMIUM_PATH'):
            options['executable_path'] = os.environ['CHROMIUM_PATH']
        browser = p.chromium.launch(**options)
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script('''window.WebSocket = class { constructor(url){window.socketURL=url;setTimeout(()=>this.onopen?.(),0);} close(){} };''')
            def route(request):
                path = urlsplit(request.request.url).path
                calls.append(path)
                if path == '/class/state':
                    request.fulfill(json=rows)
                elif path in ('/class','/benches/2/'):
                    name = 'class.html' if path == '/class' else 'index.html'
                    request.fulfill(content_type='text/html',body=(ROOT/'app/static'/name).read_text())
                elif path.startswith('/static/'):
                    file = ROOT/'app'/path.lstrip('/')
                    request.fulfill(content_type=mimetypes.guess_type(file)[0],body=file.read_text())
                elif path == '/benches/2/state': request.fulfill(json=state)
                elif path == '/benches/2/config': request.fulfill(json={'capture_url':'http://127.0.0.1:8887','protocol':protocol})
                elif path == '/benches/2/parts': request.fulfill(json={'parts':{}})
                elif path == '/benches/2/capture-status': request.fulfill(json={'cams':[], 'recording':{}})
                elif path == '/benches/2/run/start':
                    state.update(recording=True,run='bench-two',current_step=0)
                    request.fulfill(json={'run':'bench-two','capture':{'ok':True}})
                elif '/cam/' in path: request.fulfill(status=404)
                else: request.fulfill(status=404)
            page.route('**/*',route)
            for width,height in ((1440,900),(1280,800),(390,844)):
                page.set_viewport_size({'width':width,'height':height})
                page.goto('http://127.0.0.1:8880/class')
                page.wait_for_selector('.bench')
                assert page.locator('.bench').count() == 3
                assert page.locator('.bench').first.get_attribute('href') == '/benches/2/'
                assert page.locator('.dot').count() == 1
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size({'width':1440,'height':900})
            page.goto('http://127.0.0.1:8880/class')
            page.locator('.bench').first.click()
            page.wait_for_function("document.getElementById('run').disabled === false")
            assert page.evaluate('window.socketURL').endswith('/benches/2/ws')
            page.locator('#student').fill('Student 2')
            page.locator('#run').click()
            page.wait_for_function("document.getElementById('run').textContent.includes('Stop')")
            assert '/benches/2/run/start' in calls
            assert '/run/start' not in calls and '/state' not in calls
            assert not errors, errors
            print('Passed: three grid sizes, attention order/red dot, scoped page, websocket and Start control.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
