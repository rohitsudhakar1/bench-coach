#!/usr/bin/env python3
"""Record the rehearsed Bench Coach demo as a silent 1920x1080 H.264 video.

Starts the launcher the way REHEARSAL.md does (the two lsv_mv 12-27 sample clips as cameras,
scripted vision), drives the page with Playwright while it records, and puts a title card in
front and an end card behind with ffmpeg. The coach's lines are captioned from the hub's
websocket, because the video has no sound.

After the lab note it shows the download buttons (PDF, Benchling, eLabFTW, CSV), the NVIDIA
training tasks, /trainer with this run listed and /class with three benches. The launcher runs
three benches for that; benches 2 and 3 replay the same scripted events and say so on screen.

    BENCH_PY=... BENCH_SAMPLES=... "$BENCH_PY" scripts/make_demo_video.py

Output: runs/demo-video/bench-coach-demo.mp4 (runs/ is not committed). Nothing is played out
loud: the browser runs headless with --mute-audio, the launcher is not given --speak, and the
final file has no audio track.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
W, H, FPS = 1920, 1080, 30
CLIPS = ('lsv_mv/CP-Left-Front-12-27.mp4', 'lsv_mv/CP-Right-Front-12-27.mp4')
TAG = 'Sample clips as cameras. Vision events scripted for this recording.'
BENCHES = 3
STUDENT = 'Student A'
# Benches 2 and 3 exist only so /class has three cards. They replay scripts/fake_vision.py's events.
EXTRA_STUDENTS = {'2': 'Student B (replayed)', '3': 'Student C (replayed)'}
CLASS_TAG = 'Benches 2 and 3 replay the same scripted events on the sample clips.'
DOWNLOADS = (('pdf', 'pdf'), ('benchling', 'benchling'), ('eln', 'eln'), ('csv', 'csv'))
TITLE_LINE = 'Two cameras watch your bench and coach you through the protocol, step by step.'
END_LINE = 'It catches the tilt, the reused tip and the skipped step, then writes the lab note.'

# Presenter captions during the run, in seconds after the Start click. They follow the
# scripted events in scripts/fake_vision.py (tip on at 5 s, tilt at 11 s, reused tip at 33 s,
# skipped dispense at 45 s).
RUN_CUES = [
    (1.0, 'The coach reads the first step as the run starts.'),
    (5.5, 'A fresh tip goes on. Step 1 is ticked.'),
    (10.0, 'Drawing from tube A. Watch the pipette angle.'),
    (16.0, 'Steps tick off as the cameras see each one.'),
    (31.0, 'The common mistake: the same tip goes into tube C.'),
    (43.0, 'And the second dispense into tube B never happens.'),
]
STOP_AT = 53.0
PROBLEM_TIMES = ('xpath=//h2[normalize-space()="What went wrong"]/following-sibling::ul[1]'
                 '/li/a[contains(concat(" ", @class, " "), " ts ")][1]')

# Captions drawn over the page. Coach lines come from the hub's websocket (kinds `say` and
# `warning`); presenter lines are set by this script; the tag marks the recording as staged.
OVERLAY_JS = r"""
(() => {
  if (window !== window.top) return;
  const css = `
  #dc-wrap{position:fixed;left:0;right:0;bottom:var(--dc-bottom,40px);display:flex;flex-direction:column;
    align-items:center;gap:10px;z-index:2147483647;pointer-events:none;
    font-family:-apple-system,"Helvetica Neue",Arial,sans-serif}
  #dc-coach,#dc-narr{max-width:1400px;padding:14px 28px;border-radius:12px;text-align:center}
  #dc-coach{background:rgba(10,18,28,.92);color:#fff;font-size:34px;font-weight:600;
    box-shadow:0 6px 24px rgba(0,0,0,.35)}
  #dc-coach b{color:#7fd3ff;font-size:18px;letter-spacing:.12em;display:block;margin-bottom:4px}
  #dc-narr{background:rgba(255,255,255,.95);color:#111;font-size:26px;border:1px solid #bbb}
  #dc-tag{position:fixed;left:24px;bottom:16px;z-index:2147483647;pointer-events:none;
    background:#fff3c4;color:#5a4300;border:1px solid #e0c060;border-radius:8px;
    padding:6px 12px;font:600 18px -apple-system,Arial,sans-serif}
  .dc-hide{display:none!important}`;
  const add = () => {
    if (document.getElementById('dc-wrap') || !document.body) return;
    const style = document.createElement('style'); style.textContent = css; document.head.append(style);
    const wrap = document.createElement('div'); wrap.id = 'dc-wrap';
    wrap.innerHTML = '<div id="dc-narr" class="dc-hide"></div><div id="dc-coach" class="dc-hide"></div>';
    const tag = document.createElement('div'); tag.id = 'dc-tag'; tag.className = 'dc-hide';
    tag.textContent = __TAG__;
    document.body.append(wrap, tag);
  };
  let coachTimer = null;
  window.dcNarr = text => { add(); const n = document.getElementById('dc-narr');
    n.textContent = text || ''; n.classList.toggle('dc-hide', !text); };
  window.dcTag = (on, text) => { add(); const t = document.getElementById('dc-tag');
    t.textContent = text || __TAG__; t.classList.toggle('dc-hide', !on); };
  window.dcBottom = px => { add(); document.getElementById('dc-wrap').style.setProperty('--dc-bottom', px + 'px'); };
  window.dcCoach = text => { add(); const c = document.getElementById('dc-coach');
    if (!text) { c.classList.add('dc-hide'); return; }
    c.innerHTML = '<b>COACH SAYS (CAPTIONED, NO SOUND)</b>'; c.append(text); c.classList.remove('dc-hide');
    clearTimeout(coachTimer); coachTimer = setTimeout(() => c.classList.add('dc-hide'), 5000); };
  document.addEventListener('DOMContentLoaded', add);
  if (location.pathname !== '/') return;
  let last = '';
  const listen = () => {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = m => { try { const e = JSON.parse(m.data);
      if (!window.dcLive) return;
      if ((e.kind === 'say' || e.kind === 'warning') && e.detail && e.detail.text && e.detail.text !== last) {
        last = e.detail.text; window.dcCoach(last); } } catch {} };
    ws.onclose = () => setTimeout(listen, 1000);
  };
  listen();
})();
""".replace('__TAG__', json.dumps(TAG))

CARD = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;width:1920px;height:1080px;display:flex;flex-direction:column;justify-content:center;
align-items:center;background:#0b1520;color:#fff;font-family:-apple-system,"Helvetica Neue",Arial,sans-serif}
.mark{width:120px;height:120px;border-radius:28px;background:#2f9be0;display:flex;align-items:center;
justify-content:center;font-size:64px;font-weight:800;margin-bottom:40px}
h1{font-size:120px;margin:0;letter-spacing:-2px} p{font-size:42px;color:#c9d6e2;margin:28px 0 0;
max-width:1800px;text-align:center} small{position:absolute;bottom:80px;font-size:32px;color:#7fd3ff;
letter-spacing:.08em}</style></head><body><div class="mark">B</div><h1>Bench Coach</h1><p>{line}</p>
<small>Built at MIT ScienceClaw 2026</small></body></html>"""

PDF_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;width:1920px;height:1080px;background:#e9edf1;display:flex;align-items:center;
justify-content:center;gap:60px;font-family:-apple-system,"Helvetica Neue",Arial,sans-serif}
img{height:1000px;box-shadow:0 10px 40px rgba(0,0,0,.25);background:#fff}
.side{width:560px} h2{font-size:44px;margin:0 0 18px} p{font-size:26px;color:#333;line-height:1.4}
code{font-size:22px;background:#fff;padding:4px 8px;border-radius:6px}</style></head><body>
<img src="{img}"><div class="side"><h2>Download PDF</h2><p>The same note as a PDF, page 1 shown.</p>
<p><code>{name}</code></p><p>{size}</p></div></body></html>"""


def launch_command(samples, base_port=8700):
    samples = Path(samples)
    return [str(ROOT / 'scripts' / 'demo.sh'), '--source', *(str(samples / c) for c in CLIPS),
            '--fake-vision', '--benches', str(BENCHES), '--base-port', str(base_port), '--timeout', '120']


def missing_clips(samples):
    return [c for c in CLIPS if not (Path(samples) / c).is_file()]


def busy_ports(base_port):
    busy = []
    for port in range(base_port, base_port + 6 * BENCHES):
        with socket.socket() as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                busy.append(port)
    return busy


def get(url):
    with urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def codec(path):
    return subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                           'stream=codec_name', '-of', 'csv=p=0', str(path)],
                          capture_output=True, text=True).stdout.strip()


def playable_recordings(folder):
    """Browsers cannot play MPEG-4 Part 2. Capture writes avc1 when OpenCV has it and falls back
    to mp4v; if a run fell back, re-encode its cam*.mp4 in place so the note's times can seek."""
    changed = []
    for cam in sorted(Path(folder).glob('cam*.mp4')):
        found = codec(cam)
        if found == 'h264':
            continue
        tmp = cam.with_suffix('.h264.mp4')
        run(['ffmpeg', '-y', '-i', str(cam), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
             '-pix_fmt', 'yuv420p', '-an', '-movflags', '+faststart', str(tmp)])
        tmp.replace(cam)
        changed.append((cam.name, found))
    return changed


def start_demo(cmd, log):
    env = dict(os.environ, BENCH_PY=os.environ.get('BENCH_PY', sys.executable))
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True, env=env)
    ready = threading.Event()

    def pump():
        for line in proc.stdout:
            log.write(line); log.flush()
            if line.startswith('Demo ready'):
                ready.set()
    threading.Thread(target=pump, daemon=True).start()
    if not ready.wait(240) or proc.poll() is not None:
        stop_demo(proc)
        sys.exit(f'launcher never printed Demo ready; see {log.name}')
    return proc


def stop_demo(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGINT)   # the launcher stops every part on Ctrl-C
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def browser_for(p):
    try:
        return p.chromium.launch(channel='chrome', args=['--mute-audio'])
    except Exception:
        return p.chromium.launch(args=['--mute-audio'])


def pdf_still(pdf, png):
    run(['pdftoppm', '-png', '-r', '110', '-f', '1', '-l', '1', '-singlefile', str(pdf), str(png.with_suffix(''))])


def record(app, runs, raw):
    """Drive the page; return (webm path, seconds of blank lead-in to cut, checks)."""
    from playwright.sync_api import sync_playwright
    checks = {}
    with sync_playwright() as p:
        browser = browser_for(p)
        ctx = browser.new_context(viewport={'width': W, 'height': H}, device_scale_factor=1,
                                  accept_downloads=True,
                                  record_video_dir=str(raw), record_video_size={'width': W, 'height': H})
        ctx.add_init_script(OVERLAY_JS)
        t0 = time.monotonic()
        page = ctx.new_page()
        page.goto(app)
        page.wait_for_function("!document.getElementById('cam0').hidden && document.getElementById('cam0').naturalWidth > 0"
                               " && !document.getElementById('cam1').hidden && document.getElementById('cam1').naturalWidth > 0",
                               timeout=30000)
        page.wait_for_function("document.querySelectorAll('#steps li').length > 0 && !document.getElementById('run').disabled",
                               timeout=20000)
        page.wait_for_timeout(1000)
        lead = time.monotonic() - t0
        say = lambda text: page.evaluate('t => window.dcNarr(t)', text)
        wait_until = lambda at: page.wait_for_timeout(max(0, (start + at - time.monotonic()) * 1000))

        page.evaluate('window.dcTag(true)')
        say('Two sample clips stand in for two cameras on one bench. The protocol is on the right.')
        page.wait_for_timeout(5000)
        say('Type the student name and press Start run.')
        page.click('#student')
        page.type('#student', STUDENT, delay=60)
        page.wait_for_timeout(1500)
        page.evaluate('window.dcLive = true')
        page.click('#run')
        start = time.monotonic()
        for at, text in RUN_CUES:
            wait_until(at)
            say(text)
        wait_until(STOP_AT)
        say('Stop the run. The lab note is built from everything that happened.')
        page.click('#run')
        page.wait_for_function("!document.getElementById('note-badge').hidden", timeout=30000)
        page.evaluate('window.dcLive = false')
        page.wait_for_timeout(2500)
        page.evaluate("window.dcCoach('')")
        page.click('#note-tab')
        say('The Lab note tab: what was done, what was skipped, what went wrong, each with a time.')
        page.wait_for_selector('#note-content iframe', timeout=20000)
        page.wait_for_timeout(4000)
        frame = page.frame_locator('#note-content iframe')
        for y in (350, 750, 1200):
            try:
                frame.locator('body').evaluate(f'b => window.scrollTo({{top: {y}, behavior: "smooth"}})')
            except Exception:
                pass
            page.wait_for_timeout(2500)

        run_id = page.evaluate('fetch("/state").then(r => r.json()).then(s => s.run)')
        checks['run'] = run_id
        checks['reencoded'] = playable_recordings(runs / run_id)
        pdf_href = page.get_attribute('#pdf', 'href')

        say('Open the full note.')
        page.wait_for_timeout(1500)
        page.goto(f'{app}/lab-note/{run_id}?format=html')
        page.evaluate('window.dcTag(true)')
        page.wait_for_function("document.querySelectorAll('video').length >= 1 && "
                               "[...document.querySelectorAll('video')].every(v => v.readyState >= 1)",
                               timeout=20000)
        say('The full note sits beside both camera recordings.')
        page.wait_for_timeout(4500)
        # The first time of each item under What went wrong: the tilt, then the reused tip.
        target = page.locator(PROBLEM_TIMES)
        if target.count() == 0:
            raise RuntimeError('the note has no clickable times under What went wrong')
        times = "[...document.querySelectorAll('video')].map(v => Math.round(v.currentTime * 10) / 10)"
        checks['before'] = page.evaluate(times)
        checks['after'] = []
        for n, i in enumerate(range(min(2, target.count()))):
            stamp = target.nth(i)
            stamp.scroll_into_view_if_needed()
            if n == 0:
                say('Click a time under What went wrong, and the recordings jump to that moment.')
            stamp.evaluate("a => { a.style.outline = '4px solid #f0a020'; a.style.outlineOffset = '3px'; }")
            page.wait_for_timeout(2500)
            stamp.click()
            page.wait_for_timeout(1500)
            checks['after'].append(page.evaluate(times))
            what = stamp.evaluate("a => (a.closest('li')?.querySelector('.txt')?.textContent || '').trim()")
            say(f'Jumped to {stamp.inner_text().strip()}: {what}' if what else 'The recordings jumped to that moment.')
            page.wait_for_timeout(5500)
            stamp.evaluate("a => { a.style.outline = ''; }")

        say('')
        start_extra(page, app, '2', checks)   # far enough along at /class to show a warning
        page.goto(app)
        page.evaluate('window.dcTag(true)')
        page.click('#note-tab')
        page.wait_for_selector('#pdf:not([hidden])', timeout=20000)
        buttons = ', '.join('#' + b for b, _ in DOWNLOADS)
        page.evaluate(f"document.querySelectorAll('{buttons}').forEach(a => {{ a.style.outline = '4px solid #f0a020';"
                      " a.style.outlineOffset = '3px'; }})")
        say('Download the note as a PDF, as Benchling HTML, as an eLabFTW .eln file or as a steps CSV.')
        checks['downloads'] = {b: fetched(page, page.get_attribute('#' + b, 'href')) for b, _ in DOWNLOADS}
        page.wait_for_timeout(5000)
        page.evaluate(f"document.querySelectorAll('{buttons}').forEach(a => {{ a.style.outline = ''; }})")

        panel = page.locator('#nvidia-panel')
        page.wait_for_function("/ready|tasks/i.test(document.getElementById('nvidia-status').textContent)"
                               " && document.querySelectorAll('#nvidia-tasks p').length > 0", timeout=60000)
        panel.scroll_into_view_if_needed()
        panel.evaluate("p => { p.style.outline = '4px solid #f0a020'; p.style.outlineOffset = '6px'; }")
        say('The same run becomes training tasks for NVIDIA models: a question, its answer key and the clip.')
        checks['nvidia'] = {'status': page.inner_text('#nvidia-status').strip(),
                            'tasks': page.locator('#nvidia-tasks p').count()}
        page.wait_for_timeout(7000)
        panel.evaluate("p => { p.style.outline = ''; }")
        page.evaluate("document.querySelector('#note-panel .note-actions').scrollIntoView({block: 'center'})")
        page.locator('#pdf').evaluate("a => { a.style.outline = '4px solid #f0a020'; a.style.outlineOffset = '3px'; }")
        say('Download PDF saves the note to share or file.')
        page.wait_for_timeout(2000)
        with page.expect_download(timeout=60000) as info:
            page.click('#pdf')
        download = info.value
        pdf = raw / download.suggested_filename
        download.save_as(pdf)
        checks['pdf'] = (pdf.name, pdf.stat().st_size, pdf.read_bytes()[:5])
        png = raw / 'pdf-page1.png'
        pdf_still(pdf, png)
        page.wait_for_timeout(500)
        say('')
        page.set_content(PDF_PAGE.replace('{img}', 'data:image/png;base64,' + __import__('base64').b64encode(png.read_bytes()).decode())
                         .replace('{name}', pdf.name).replace('{size}', f'{pdf.stat().st_size // 1024} KB, built from the run'))
        page.wait_for_timeout(6000)
        start_extra(page, app, '3', checks)   # just started at /class: an early step

        page.goto(f'{app}/trainer')
        page.evaluate('window.dcTag(true)')
        row = page.locator('#runs tr', has_text=STUDENT)
        row.first.wait_for(timeout=20000)
        row.first.evaluate("r => { r.style.outline = '4px solid #f0a020'; r.style.background = '#fff6df'; }")
        say(f'The trainer page lists every saved run. This one is {STUDENT}.')
        checks['trainer_rows'] = page.locator('#runs tr').count()
        page.wait_for_timeout(6500)

        page.goto(f'{app}/class')
        page.evaluate(f'window.dcTag(true, {json.dumps(CLASS_TAG)})')
        page.wait_for_function(f"document.querySelectorAll('#benches .bench').length >= {BENCHES}", timeout=20000)
        say('The classroom view: three benches at once, and the one that needs help comes first.')
        checks['class_cards'] = page.locator('#benches .bench').count()
        page.wait_for_timeout(6500)
        # Playwright's video runs a little behind the page; hold long enough that it lands.
        page.wait_for_timeout(2500)
        checks['pdf_href'] = pdf_href
        video = page.video
        ctx.close()
        browser.close()
        return Path(video.path()), lead, checks


def fetched(page, href):
    """Status, size and first bytes of one download link, read through the browser's session."""
    r = page.request.get(urljoin(page.url, href), timeout=60000)
    body = r.body()
    return (r.status, len(body), body[:5])


def start_extra(page, app, bench, checks):
    """Start a replayed run on an extra bench so /class has more than one card."""
    r = page.request.post(f'{app}/benches/{bench}/run/start', data={'student': EXTRA_STUDENTS[bench]})
    checks.setdefault('extra_started', {})[bench] = r.status


def stop_extra(app):
    for bench in EXTRA_STUDENTS:
        try:
            req = Request(f'{app}/benches/{bench}/run/stop', data=b'{}',
                          headers={'Content-Type': 'application/json'})
            urlopen(req, timeout=10).read()
        except Exception:
            pass


def card(line, png):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': W, 'height': H})
        page.set_content(CARD.replace('{line}', line))
        page.screenshot(path=str(png))
        browser.close()


def encode_still(png, mp4, seconds=3):
    run(['ffmpeg', '-y', '-loop', '1', '-i', str(png), '-t', str(seconds), '-r', str(FPS),
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', str(mp4)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=ROOT / 'runs' / 'demo-video')
    parser.add_argument('--base-port', type=int, default=8700)
    args = parser.parse_args(argv)
    samples = os.environ.get('BENCH_SAMPLES')
    if not samples or missing_clips(samples):
        sys.exit('set BENCH_SAMPLES to the folder that holds lsv_mv/ with the 12-27 clips')
    if busy_ports(args.base_port):
        sys.exit(f'ports in use: {busy_ports(args.base_port)}; stop whatever holds them first')
    out = args.out; out.mkdir(parents=True, exist_ok=True)
    raw = out / 'raw'; shutil.rmtree(raw, ignore_errors=True); raw.mkdir()

    for name, line in (('title', TITLE_LINE), ('end', END_LINE)):
        card(line, raw / f'{name}.png')
        encode_still(raw / f'{name}.png', raw / f'{name}.mp4')

    app = f'http://127.0.0.1:{args.base_port}'
    with open(out / 'launcher.log', 'w') as log:
        proc = start_demo(launch_command(samples, args.base_port), log)
        try:
            webm, lead, checks = record(app, ROOT / 'runs', raw)
        finally:
            stop_extra(app)
            stop_demo(proc)
    print(json.dumps(checks, default=str), flush=True)

    run(['ffmpeg', '-y', '-ss', f'{lead:.2f}', '-i', str(webm), '-r', str(FPS), '-c:v', 'libx264',
         '-pix_fmt', 'yuv420p', '-crf', '20', '-preset', 'medium', '-an', str(raw / 'main.mp4')])
    (raw / 'list.txt').write_text(''.join(f"file '{raw / n}.mp4'\n" for n in ('title', 'main', 'end')))
    final = out / 'bench-coach-demo.mp4'
    run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(raw / 'list.txt'),
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '21', '-movflags', '+faststart', '-an', str(final)])
    print(f'wrote {final.relative_to(ROOT)} ({final.stat().st_size / 1e6:.1f} MB)', flush=True)
    print(f'ports still in use: {busy_ports(args.base_port)}', flush=True)


if __name__ == '__main__':
    main()
