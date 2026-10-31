"""Optional phone rendering check; mocks HTTP/WS without opening any server ports."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


def main():
    state = {'run': 'phone-check', 'recording': True, 'current_step': 0, 'steps': {},
             'protocol': {'name': 'Phone check', 'steps': [
                 {'text': 'Draw 100 microlitres of dye from tube D1.'},
                 {'text': 'Dispense into tube D2.'}]},
             'warnings': [{'detail': {'text': 'Keep the pipette upright.'}}]}
    posts = []
    with sync_playwright() as pw:
        options = {'headless': True}
        if os.getenv('CHROMIUM_PATH'):
            options['executable_path'] = os.environ['CHROMIUM_PATH']
        browser = pw.chromium.launch(**options)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.add_init_script('''window.WebSocket = class {
              constructor() { window.socket = this; setTimeout(() => this.onopen(), 0); }
              close() { this.onclose(); }
            };''')

            def route(r):
                p = r.request.url.split('8880')[-1]
                if p == '/step':
                    r.fulfill(content_type='text/html', body=(ROOT / 'app/static/step.html').read_text())
                elif p.startswith('/static/'):
                    file = ROOT / 'app' / p.lstrip('/')
                    mime = {'.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'}[file.suffix]
                    r.fulfill(content_type=mime, body=file.read_text())
                elif p == '/state':
                    r.fulfill(content_type='application/json', body=json.dumps(state))
                elif p == '/events':
                    posts.append(r.request.post_data_json)
                    r.fulfill(content_type='application/json', body='{"accepted":1}')
                else:
                    r.fulfill(status=404)

            page.route('**/*', route)
            page.goto('http://127.0.0.1:8880/step')
            expect(page.locator('#current')).to_have_text(state['protocol']['steps'][0]['text'])
            for width in (320, 390, 768):
                page.set_viewport_size({'width': width, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert page.locator('#current').evaluate('e => parseFloat(getComputedStyle(e).fontSize)') >= 36
                assert page.locator('#done').bounding_box()['height'] >= 80
            page.get_by_role('button', name='Done', exact=True).click()
            expect(page.locator('#done')).to_be_disabled()
            expect(page.locator('#feedback')).to_contain_text('Confirmation sent')
            assert len(posts) == 1 and posts[0]['source'] == 'app' and posts[0]['detail']['text'] == 'done'
            state['current_step'] = 1
            page.evaluate('window.socket.onmessage({data:"{}"})')
            expect(page.locator('#current')).to_have_text('Dispense into tube D2.')
            expect(page.locator('#done')).to_be_enabled()
            state['recording'] = False
            page.evaluate('window.socket.onmessage({data:"{}"})')
            expect(page.locator('#warning-box')).to_be_hidden()
            expect(page.locator('#done')).to_be_disabled()
            assert not errors, errors
            print('Phone browser checks passed at widths 320, 390 and 768.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
