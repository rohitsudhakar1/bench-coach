"""Silent Playwright check; owns and stops its label server on a test port.

python -m scripts.tests.check_label_steps_browser --video clip.mov --protocol protocol.txt --screenshots shots
"""
import argparse
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--screenshots", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8810)
    parser.add_argument("--browser", choices=["chromium", "webkit"], default="chromium")
    parser.add_argument("--browser-executable", type=Path, help="Optional installed browser executable")
    args = parser.parse_args()
    if not 8800 <= args.port <= 8899:
        parser.error("Use ports 8800–8899")
    with socket.socket() as check:
        check.bind(("127.0.0.1", args.port))
    args.screenshots.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="label-browser-") as directory:
        out = Path(directory) / "labels.jsonl"
        with (Path(directory) / "server.log").open("w+") as log:
            process = subprocess.Popen([sys.executable, "-m", "scripts.label_steps", "--video", str(args.video),
                                        "--protocol", str(args.protocol), "--out", str(out), "--port", str(args.port)],
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                url = f"http://127.0.0.1:{args.port}"
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        with urlopen(url + "/api/session", timeout=1) as response:
                            session = json.load(response)
                        break
                    except OSError:
                        time.sleep(.2)
                else:
                    raise RuntimeError("Server startup timed out")
                print(f"Server ready in {time.monotonic()-start:.2f}s; converted={session['converted']}")
                with sync_playwright() as p:
                    options = {"args": ["--mute-audio"]} if args.browser == "chromium" else {}
                    if args.browser_executable:
                        options["executable_path"] = str(args.browser_executable)
                    browser = getattr(p, args.browser).launch(headless=True, **options)
                    try:
                        page = browser.new_page(viewport={"width": 1440, "height": 1050})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(url)
                        page.wait_for_function("document.getElementById('video').readyState >= 2")
                        assert page.locator("video").evaluate("v=>v.muted && !v.controls")
                        assert page.locator("#steps button").count() == len(session["steps"])
                        # Mouse step, number key, frame keys, play/pause, retime and undo.
                        page.locator("video").evaluate("v=>{v.currentTime=1;}")
                        page.wait_for_function("!document.getElementById('video').seeking")
                        page.locator('[data-step="0"]').click()
                        assert "00:01.000" in page.locator('[data-step="0"]').inner_text()
                        page.locator("body").click(position={"x": 5, "y": 5})
                        page.keyboard.press("ArrowRight")
                        page.wait_for_function("!document.getElementById('video').seeking")
                        after = page.locator("video").evaluate("v=>v.currentTime")
                        assert 1 < after < 1.2, after
                        page.keyboard.press("ArrowLeft")
                        page.wait_for_function("!document.getElementById('video').seeking")
                        before = page.locator("video").evaluate("v=>v.currentTime")
                        assert abs(before-1) < .05, before
                        page.keyboard.press("Space")
                        page.wait_for_function("document.getElementById('video').currentTime > 1.2")
                        page.keyboard.press("Space")
                        assert page.locator("video").evaluate("v=>v.paused")
                        page.locator("video").evaluate("v=>{v.currentTime=2;}")
                        page.wait_for_function("!document.getElementById('video').seeking")
                        page.keyboard.press("1")
                        assert "00:02.000" in page.locator('[data-step="0"]').inner_text()
                        page.keyboard.press("Control+z")
                        assert "00:01.000" in page.locator('[data-step="0"]').inner_text()
                        page.keyboard.press("2")
                        assert "done" in page.locator('[data-step="1"]').get_attribute("class")
                        page.locator("#warningStep").select_option("1")
                        page.locator("#reuse").click()
                        page.locator("#warningStep").select_option("2")
                        page.locator("#skip").click()
                        assert "skipped" in page.locator('[data-step="2"]').get_attribute("class")
                        page.keyboard.press("Control+s")
                        page.wait_for_function("document.getElementById('status').textContent.startsWith('Saved ')")
                        saved = [json.loads(line) for line in out.read_text().splitlines()]
                        assert len(saved) == 5
                        assert len([r for r in saved if r['kind']=='warning']) == 2
                        assert saved[0]["s"] == 1.0
                        page.reload()
                        page.wait_for_function("document.getElementById('video').readyState >= 2")
                        assert page.locator(".step.done").count() == 2
                        assert page.locator(".step.skipped").count() == 1
                        assert page.locator(".warning").count() == 2
                        # Use real sample footage in the screenshot, after checking all controls.
                        page.locator("video").evaluate("v=>{v.currentTime=Math.min(16,v.duration/2);}")
                        page.wait_for_function("!document.getElementById('video').seeking")
                        page.screenshot(path=str(args.screenshots / f"{args.video.stem}-{args.browser}.png"), full_page=True)
                        page.set_viewport_size({"width": 390, "height": 844})
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        page.screenshot(path=str(args.screenshots / f"{args.video.stem}-{args.browser}-narrow.png"), full_page=True)
                        assert not errors, errors
                        print(f"PASS {args.browser}: media, frame seek, hotkeys, retime, undo, warnings, save/reload, narrow layout; {len(saved)} JSONL rows")
                    finally:
                        browser.close()
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                with socket.socket() as check:
                    assert check.connect_ex(("127.0.0.1", args.port)) != 0, "Server port still open"
                print(f"Owned server PID {process.pid} stopped; port {args.port} released")


if __name__ == "__main__":
    main()
