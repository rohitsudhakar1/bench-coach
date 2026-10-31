"""Silent, server-free trainer layout and filter check with three fake run folders."""
import json
import mimetypes
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, parse_qs

from playwright.sync_api import sync_playwright
from app.trainer import overview
from app.tests.test_trainer import three_runs

ROOT = Path(__file__).resolve().parents[2]


def main():
    with TemporaryDirectory(dir=ROOT / 'app') as tmp:
        runs = three_runs.__wrapped__(Path(tmp))
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                def route(request):
                    url = urlsplit(request.request.url)
                    if url.path == '/trainer/runs':
                        query = parse_qs(url.query)
                        request.fulfill(json=overview(runs, query.get('protocol', [''])[0], query.get('student', [''])[0]))
                    elif url.path == '/trainer':
                        request.fulfill(content_type='text/html', body=(ROOT / 'app/static/trainer.html').read_text())
                    elif url.path.startswith('/static/'):
                        file = ROOT / 'app' / url.path.lstrip('/')
                        request.fulfill(content_type=mimetypes.guess_type(file)[0], body=file.read_text())
                    else:
                        request.fulfill(status=404)
                page.route('**/*', route)
                for width, height in [(1440, 900), (1280, 800), (390, 844)]:
                    page.set_viewport_size(dict(width=width, height=height))
                    page.goto('http://127.0.0.1:8880/trainer')
                    page.wait_for_function("document.querySelectorAll('#runs tr').length === 3")
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.select_option('#protocol', 'pipette.yaml')
                    page.wait_for_function("document.querySelectorAll('#runs tr').length === 2")
                    assert '2/2 missed' in page.locator('#missed').inner_text()
                    page.fill('#student', 'Student One')
                    page.wait_for_function("document.querySelectorAll('#runs tr').length === 1")
                    assert page.get_by_role('link', name='Open note').get_attribute('href').endswith('?format=html')
                assert not errors, errors
                print('Trainer: three viewports, filters, summary, note link and no page overflow passed.')
            finally:
                browser.close()


if __name__ == '__main__':
    main()
