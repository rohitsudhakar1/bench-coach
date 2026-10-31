"""Server-free render check. Synthetic inputs only; no camera, mic, sound or API key."""
import json
import os
import time
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.getenv('BENCH_BROWSER_ARTIFACTS', '/tmp/benchcoach-app-browser'))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    protocol = yaml.safe_load((ROOT/'protocols/pipetting-demo.yaml').read_text())
    state = dict(run=None, recording=False, capture=None, current_step=None, question=None, steps={},
                 warnings=[], chat=[], angle=None, objects={}, note=None, protocol=protocol)
    note_html = '''<!doctype html><html><head><style>body{font:16px sans-serif}video{width:100%}</style></head><body><h1>Lab note</h1><button data-off='{"cam0.mp4":4.2,"cam1.mp4":5.8}'>00:04 · Draw confirmed</button><aside><video id="player" src="http://127.0.0.1:8885/runs/demo/cam0.mp4"></video></aside><script type="application/json" id="vids">["cam0.mp4","cam1.mp4"]</script>'''
    note_html += '<script>'+(ROOT/'app/static/note-player.js').read_text()+'</script></body></html>'
    with sync_playwright() as playwright:
        kwargs = dict(headless=True)
        executable = os.getenv('CHROMIUM_PATH')
        if executable:
            kwargs['executable_path'] = executable
        browser = playwright.chromium.launch(**kwargs)
        try:
            page = browser.new_page(viewport={'width':1440,'height':900})
            errors = []; page.on('pageerror',lambda error: errors.append(str(error)))
            page.add_init_script('''window.WebSocket = class { constructor(){ window.testSocket=this; setTimeout(()=>this.onopen?.(),0); } close(){} };''')
            def route(request):
                path = request.request.url.split('8880')[-1]
                if '/cam/' in path:
                    color = '#263b4c' if '/0/' in path else '#374837'
                    request.fulfill(content_type='image/svg+xml',body=f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="{color}"/><rect x="260" y="100" width="30" height="210" fill="#b7cad4"/><text x="20" y="440" fill="white" font-size="22">Synthetic camera frame</text></svg>'); return
                if '/runs/' in path:
                    request.fulfill(status=404); return
                if path == '/':
                    request.fulfill(content_type='text/html',body=(ROOT/'app/static/index.html').read_text()); return
                if path.startswith('/static/'):
                    file = ROOT / 'app' / path.lstrip('/')
                    mime = 'text/javascript' if file.suffix == '.js' else 'text/css' if file.suffix == '.css' else 'image/svg+xml'
                    request.fulfill(content_type=mime,body=file.read_text()); return
                if path == '/state': value = state
                elif path == '/config': value = {'capture_url':'http://127.0.0.1:8880','protocol':protocol}
                elif path == '/parts': value = {'parts':{name:{'status':'ready','message':'Ready for the run.'} for name in ['app','capture','vision','angle','coach','note']}}
                elif path == '/run/start':
                    state.update(run='demo',recording=True,current_step=1,steps={'1':{'status':'current'}},angle={'deg':27,'ok':False,'limit':20},question='Did you draw from tube A?',warnings=[{'t':time.time(),'detail':{'text':'Camera estimate: pipette tilted 27°. Keep it under 20°.'}}])
                    value = {'run':'demo','capture':{'ok':True}}
                elif path == '/run/stop':
                    state.update(recording=False,warnings=[],question=None,angle=None,note={'run':'demo','url':'http://127.0.0.1:8885/note/demo'})
                    value = {'run':'demo','capture':{'ok':True}}
                elif path == '/nvidia/demo':
                    value = {'export': {'status':'ready', 'score':{'mean':0.8,'tasks':5}, 'tasks':[{'id':'demo-skipped','kind':'skipped_step','question':'Which steps were skipped?','checker':'Exact set of step numbers'}]}, 'synth': {'status':'idle'}}
                elif path == '/nvidia/demo/synth':
                    value = {'export': {'status':'ready', 'score':{'mean':0.8,'tasks':5}, 'tasks':[]}, 'synth': {'status':'ready','manifest':{'synthetic_runs':20,'engine_detail':'local seeded generator; Data Designer was not used','notes':'fixed template','prior':1},'recorded':{'Demo':{'runs':1,'skip_rate':[0.2,0.8]}},'synthetic':{'Demo':{'skip_rate':[0.25,0.75]}}}}
                elif path.startswith('/lab-note/'):
                    request.fulfill(content_type='text/html',body=note_html); return
                else:
                    request.fulfill(status=404); return
                request.fulfill(content_type='application/json',body=json.dumps(value))
            page.route('**/*',route)
            page.goto('http://127.0.0.1:8880/')
            page.locator('#run:not([disabled])').wait_for()
            page.locator('#student').fill('Presenter')
            page.keyboard.press('Space')
            assert not state['recording']
            page.locator('#student').blur()
            page.locator('#load').click()
            page.keyboard.press('Space')
            assert not state['recording']
            page.keyboard.press('Escape')
            page.keyboard.press('?')
            assert page.locator('#hotkeys-dialog').is_visible()
            page.keyboard.press('Escape')
            assert not page.locator('#hotkeys-dialog').is_visible()
            page.keyboard.press('n')
            assert page.locator('#note-tab').get_attribute('aria-selected') == 'true'
            page.keyboard.press('p')
            assert page.locator('#protocol-tab').get_attribute('aria-selected') == 'true'
            page.keyboard.press('Space')
            page.locator('#question').wait_for(state='visible')
            page.keyboard.press('Space')
            assert state['recording'], 'Double press stopped the run'
            page.keyboard.press('?')
            page.screenshot(path=str(OUT/'hotkeys-help.png'))
            page.keyboard.press('Escape')
            for width,height in [(1920,1080),(1440,900),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                page.screenshot(path=str(OUT/f'bench-{width}.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow'
                if width > 700:
                    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight'), 'Desktop scroll'
                    a,b = page.locator('.camera').all()
                    assert abs(a.bounding_box()['y']-b.bounding_box()['y']) < 1
                else:
                    a,b = page.locator('.camera').all()
                    assert b.bounding_box()['y'] > a.bounding_box()['y']
            page.set_viewport_size({'width':1440,'height':900})
            assert page.locator('#warning').is_visible()
            assert page.locator('[aria-current="step"]').count() == 1
            page.wait_for_timeout(1050)
            page.locator('#run').focus()
            page.keyboard.press('Space')
            page.locator('#note-content iframe').wait_for()
            assert not page.locator('#warning').is_visible()
            assert page.locator('#note-tab').get_attribute('aria-selected') == 'true'
            assert page.locator('#pdf').is_visible() and page.locator('#zip').is_visible()
            for width, height in [(1920,1080), (1440,900), (390,844)]:
                page.set_viewport_size({'width':width, 'height':height})
                for export in ('benchling', 'eln', 'csv'):
                    link = page.locator('#' + export)
                    assert link.is_visible()
                    assert link.get_attribute('href') == '/lab-note/demo?format=' + export
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size({'width':1440, 'height':900})
            page.get_by_text('Rules solver: 0.8 / 1 across 5 tasks.').wait_for()
            assert page.get_by_role('link',name='Download task ZIP').get_attribute('href') == '/nvidia/demo/tasks/demo-skipped.zip'
            page.get_by_role('button',name='Make synthetic runs').click()
            page.get_by_text('25.0%',exact=True).wait_for()
            assert '20 synthetic runs' in page.locator('#nvidia-synth-status').inner_text()
            assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
            frame = page.frames[1]
            frame.wait_for_selector('video')
            frame.evaluate('''() => document.querySelectorAll('video').forEach(v => { Object.defineProperty(v,'readyState',{value:1}); Object.defineProperty(v,'duration',{value:30}); Object.defineProperty(v,'error',{value:null}); Object.defineProperty(v,'currentTime',{value:0,writable:true}); })''')
            frame.locator('[data-off]').click()
            assert frame.evaluate('[...document.querySelectorAll("video")].map(v=>v.currentTime)') == [4.2,5.8]
            assert frame.evaluate('[...document.querySelectorAll("video")].every(v=>v.muted && v.paused)')
            page.screenshot(path=str(OUT/'note-1440.png'),full_page=True)
            assert errors == [], errors
            print('PASS: three sizes, warnings, tabs, exports, two paused video offsets; screenshots:',OUT)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
