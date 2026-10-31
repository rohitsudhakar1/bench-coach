"""Server-free calibration interaction and layout check with synthetic preview frames."""
import base64
import mimetypes
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import cv2
import numpy as np
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


def main():
    image = np.full((480, 640, 3), 45, np.uint8)
    for y in range(7):
        for x in range(10):
            cv2.rectangle(image, (120 + x*35, 100 + y*35), (155 + x*35, 135 + y*35),
                          (235, 235, 235) if (x+y) % 2 else (0, 0, 0), -1)
    frame = base64.b64encode(cv2.imencode('.jpg', image)[1]).decode()
    with sync_playwright() as playwright:
        options = dict(headless=True)
        if os.getenv('CHROMIUM_PATH'):
            options['executable_path'] = os.environ['CHROMIUM_PATH']
        browser = playwright.chromium.launch(**options)
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            state = dict(views=[], revision='preview', count=0, can_capture=True,
                         verdict='collecting', message='Ready', result=None, used=False)
            def route(request):
                path = urlsplit(request.request.url).path
                if path == '/calibrate':
                    request.fulfill(content_type='text/html', body=(ROOT/'app/static/calibrate.html').read_text())
                elif path.startswith('/static/'):
                    file = ROOT/'app'/path.lstrip('/')
                    request.fulfill(content_type=mimetypes.guess_type(file)[0], body=file.read_text())
                elif path.startswith('/calibrate/'):
                    state['views'] = [dict(image=frame, found=True, t=time.time()) for _ in range(2)]
                    if path.endswith('/capture'):
                        state['count'] += 1
                    elif path.endswith('/compute'):
                        state.update(verdict='good', result=dict(rms_px=.16, left_rms_px=.12, right_rms_px=.13,
                                     per_pair=[dict(left_rms_px=.2, right_rms_px=.3)]))
                    elif path.endswith('/use'):
                        state.update(used=True, message='Saved. Physical pencil check still required.')
                    elif path.endswith('/reset'):
                        state.update(count=0, result=None, verdict='collecting', used=False)
                    request.fulfill(json=state)
                else:
                    request.fulfill(status=404)
            page.route('**/*', route)
            for width, height in [(1920,1080), (1440,900), (390,844)]:
                state.update(count=0, result=None, verdict='collecting', used=False)
                page.set_viewport_size(dict(width=width, height=height))
                page.goto('http://127.0.0.1:8880/calibrate')
                expect(page.locator('#view0')).to_be_visible()
                expect(page.locator('#capture')).to_be_disabled()
                page.check('#level')
                page.click('#capture')
                expect(page.locator('#progress')).to_contain_text('1 of 12')
                expect(page.locator('#level-label')).to_be_hidden()
                state['count'] = 12
                expect(page.locator('#compute')).to_be_enabled()
                page.click('#compute')
                expect(page.locator('#verdict')).to_have_text('Good fit')
                expect(page.locator('#metrics')).to_contain_text('0.160 px')
                page.click('#use')
                expect(page.locator('#message')).to_contain_text('Saved.')
                expect(page.locator('#use')).to_be_disabled()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.click('#reset')
                expect(page.locator('#level')).not_to_be_checked()
                expect(page.locator('#capture')).to_be_disabled()
            assert not errors, errors
            print('Calibration browser: three viewports, capture, compute, install and reset passed.')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
