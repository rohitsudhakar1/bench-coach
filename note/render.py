"""The note as Markdown and as one HTML page with a player that jumps to each time."""
from __future__ import annotations

import html
import json
from urllib.parse import quote

STATUS = {"done": "done", "skipped": "skipped", "unsure": "unsure",
          "not reached": "not reached", "not finished": "not finished"}
SECTIONS = [("technique", "Technique", "No technique readings."),
            ("said", "What was said that matters", "Nothing said that changes the note."),
            ("problems", "What went wrong", "Nothing the system could see.")]


def clock(sec: float | None) -> str:
    if sec is None:
        return "--:--"
    s = max(0, int(round(sec)))
    return f"{s // 60:02d}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def cam_label(note: dict, name: str) -> str:
    for i, v in enumerate(note["videos"]):
        if v["name"] == name:
            return f"camera {v['cam'] + 1 if isinstance(v.get('cam'), int) else i + 1}"
    return name


def _href(prefix: str, name: str, off: float) -> str:
    return f"{prefix}{quote(name)}#t={off:g}"


def header_lines(note: dict) -> list[tuple[str, str]]:
    out = []
    if note.get("date"):
        out.append(("Date", f"{note['date']}, started {note['time']}"))
    if note.get("duration") is not None:
        out.append(("Length", clock(note["duration"])))
    out.append(("Protocol", f"{note['title']}" + (f" ({note['protocol_file']})" if note.get("protocol_file") else "")))
    if note.get("who"):
        out.append(("Who", note["who"]))
    out.append(("Run", note["run"]))
    return out


# ---- Markdown ------------------------------------------------------------------------------------

def _md_stamp(note: dict, item: dict, prefix: str) -> str:
    offs = item.get("offsets") or {}
    if not offs:
        return f"`{clock(item.get('at'))}`"
    links = [f"[{clock(off)} {cam_label(note, name)}]({_href(prefix, name, off)})" for name, off in offs.items()]
    return " ".join(links)


def to_markdown(note: dict, prefix: str = "") -> str:
    out = [f"# Lab note: {note['title']}", ""]
    out += ["  \n".join(f"**{k}:** {v}" for k, v in header_lines(note)), "", note["summary"], "", "## Steps", ""]
    for s in note["steps"]:
        label = f" ({s['label']})" if s.get("label") else ""
        out.append(f"{s['number']}. **{STATUS.get(s['status'], s['status'])}**{label} {_md_stamp(note, s, prefix)} {s['text']}")
    if not note["steps"]:
        out.append("No protocol steps.")
    for key, title, empty in SECTIONS:
        out += ["", f"## {title}", ""]
        if not note[key]:
            out.append(f"- {empty}")
        for item in note[key]:
            text = f"\"{item['text']}\"" if key == "said" else item["text"]
            out.append(f"- {_md_stamp(note, item, prefix)} {text}")
            for f in item.get("frames") or []:
                out.append(f"  ![{cam_label(note, f['video'])} at {clock(f['offset'])}]({prefix}{quote(f['file'])})")
    out += ["", f"_Every time links to that moment in the recording. Wording: {note['wording']}._"]
    return "\n".join(out) + "\n"


# ---- HTML ----------------------------------------------------------------------------------------

CSS = """
:root{--ink:#1d2330;--mute:#667085;--line:#e4e7ec;--bg:#fbfbfa;--ok:#12805c;--bad:#c4320a;--warn:#b54708;--acc:#2754c5}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 24px;display:grid;grid-template-columns:minmax(0,1fr) 440px;gap:28px}
@media(max-width:920px){main{grid-template-columns:1fr}.side{position:static!important}}
h1{font-size:24px;margin:0 0 6px}h2{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:6px}
.meta{color:var(--mute);display:flex;flex-wrap:wrap;gap:4px 18px;font-size:14px}.meta b{color:var(--ink);font-weight:600}
.summary{margin:14px 0 0;padding:10px 14px;background:#fff;border:1px solid var(--line);border-radius:8px}
ol,ul{list-style:none;margin:0;padding:0}li{display:flex;flex-wrap:wrap;gap:6px 10px;padding:7px 0;border-bottom:1px dashed var(--line);align-items:baseline}
.ts{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--acc);text-decoration:none;background:#eef2fb;border-radius:4px;padding:1px 6px;white-space:nowrap;cursor:pointer}
.ts.off{color:var(--mute);background:#f2f4f7;cursor:default}
.badge{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;border-radius:4px;padding:1px 6px;white-space:nowrap}
.done{color:var(--ok);background:#e7f6ef}.skipped{color:var(--bad);background:#fdece7}.unsure{color:var(--warn);background:#fef3e6}.not{color:var(--mute);background:#f2f4f7}
.lbl{font-size:12px;color:var(--mute)}.n{color:var(--mute);min-width:1.5em;text-align:right}q{font-style:italic}.txt{flex:1;min-width:200px}
.frames{width:100%;display:flex;gap:8px;margin-top:4px}.frames figure{margin:0;flex:1}.frames img{width:100%;border-radius:6px;border:1px solid var(--line);cursor:pointer}
.frames figcaption{font-size:12px;color:var(--mute)}
.side{position:sticky;top:20px;align-self:start}video{width:100%;max-height:62vh;object-fit:contain;border-radius:8px;background:#000}
.cams{display:flex;gap:6px;margin:8px 0}.cams button{font:inherit;font-size:13px;border:1px solid var(--line);background:#fff;border-radius:6px;padding:3px 10px;cursor:pointer}.cams button.on{border-color:var(--acc);color:var(--acc)}
.foot{color:var(--mute);font-size:13px;margin-top:24px}
"""

# The player is muted: the note never plays sound by itself.
JS = """
const vids=JSON.parse(document.getElementById('vids').textContent);const player=document.getElementById('player');
let cam=vids.length?vids[0]:null;
function src(n){return PREFIX+encodeURIComponent(n);}
function pick(n,t){cam=n;document.querySelectorAll('.cams button').forEach(b=>b.classList.toggle('on',b.dataset.v===n));
 const keep=t===undefined?player.currentTime:t;player.src=src(n);player.addEventListener('loadedmetadata',()=>{player.currentTime=keep},{once:true});}
document.querySelectorAll('.cams button').forEach(b=>b.onclick=()=>pick(b.dataset.v));
document.querySelectorAll('[data-off]').forEach(a=>a.addEventListener('click',e=>{
 if(!player||!cam)return;e.preventDefault();const offs=JSON.parse(a.dataset.off);
 const want=a.dataset.v||cam;const name=(want in offs)?want:Object.keys(offs)[0];if(name===undefined)return;
 if(name!==cam){pick(name,offs[name]);}else{player.currentTime=offs[name];}}));
"""


def _stamp(note: dict, item: dict, prefix: str) -> str:
    e = html.escape
    offs = item.get("offsets") or {}
    if not offs:
        return f'<span class="ts off">{clock(item.get("at"))}</span>'
    data = e(json.dumps(offs))
    return " ".join(
        f'<a class="ts" href="{e(_href(prefix, name, off))}" data-v="{e(name)}" data-off="{data}" '
        f'title="{e(cam_label(note, name))}">{clock(off)} {e(cam_label(note, name).replace("camera ", "cam "))}</a>'
        for name, off in offs.items())


def to_html(note: dict, prefix: str = "") -> str:
    e = html.escape
    meta = "".join(f"<span>{e(k)} <b>{e(str(v))}</b></span>" for k, v in header_lines(note))
    steps = []
    for s in note["steps"]:
        cls = s["status"] if s["status"] in ("done", "skipped", "unsure") else "not"
        lbl = f'<span class="lbl">{e(s["label"])}</span>' if s.get("label") else ""
        steps.append(f'<li><span class="n">{s["number"]}</span><span class="badge {cls}">{e(STATUS.get(s["status"], s["status"]))}</span>'
                     f'{lbl}{_stamp(note, s, prefix)}<span class="txt">{e(s["text"])}</span></li>')
    blocks = []
    for key, title, empty in SECTIONS:
        items = []
        for it in note[key]:
            text = f"<q>{e(it['text'])}</q>" if key == "said" else e(it["text"])
            figs = "".join(
                f'<figure><a href="{e(prefix + quote(f["file"]))}" target="_blank"><img src="{e(prefix + quote(f["file"]))}" '
                f'alt="{e(cam_label(note, f["video"]))} at {clock(f["offset"])}"></a>'
                f'<figcaption>{e(cam_label(note, f["video"]))}, {clock(f["offset"])}</figcaption></figure>'
                for f in it.get("frames") or [])
            items.append(f'<li>{_stamp(note, it, prefix)}<span class="txt">{text}</span>'
                         + (f'<div class="frames">{figs}</div>' if figs else "") + "</li>")
        blocks.append(f"<h2>{e(title)}</h2><ul>{''.join(items) or f'<li>{e(empty)}</li>'}</ul>")

    vids = [v["name"] for v in note["videos"]]
    if vids:
        buttons = "".join(f'<button data-v="{e(v)}" class="{"on" if i == 0 else ""}">{e(cam_label(note, v).capitalize())}</button>'
                          for i, v in enumerate(vids))
        side = (f'<aside class="side"><video id="player" controls muted playsinline preload="metadata" '
                f'src="{e(prefix + quote(vids[0]))}"></video><div class="cams">{buttons}</div>'
                '<div class="foot">Click any time to jump the recording there.</div></aside>')
    else:
        side = '<aside class="side"><div class="foot">No recording in this run folder, so times are minutes:seconds from the start of the run.</div></aside>'
    links = ""
    if prefix:
        run = e(quote(note["run"]))
        links = f' <a href="/note/{run}.pdf">Download PDF</a> · <a href="/note/{run}.zip">Download run</a> (no videos).'
    body = (f"<main><article><h1>Lab note: {e(note['title'])}</h1><div class='meta'>{meta}</div>"
            f"<div class='summary'>{e(note['summary'])}</div>"
            f"<h2>Steps</h2><ol>{''.join(steps) or '<li>No protocol steps.</li>'}</ol>" + "".join(blocks)
            + f"<div class='foot'>Wording: {e(note['wording'])}. Built from the run's event log.{links}</div>"
            + f"</article>{side}</main>")
    vids_json = json.dumps(vids).replace("</", "<\\/")
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Lab note: {e(note['title'])}</title><style>{CSS}</style></head><body>{body}"
            f"<script type='application/json' id='vids'>{vids_json}</script>"
            f"<script>const PREFIX={json.dumps(prefix)};{JS if vids else ''}</script></body></html>")
