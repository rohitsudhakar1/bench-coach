"""Printable note (PDF through WeasyPrint) and a downloadable run zip. No network, no model."""
from __future__ import annotations

import base64
import html
import io
import zipfile
from pathlib import Path

from .render import SECTIONS, STATUS, cam_label, clock, header_lines


def frame_files(note: dict, folder: Path):
    """Key frames that really sit in this run's note_frames folder; nothing else is read."""
    root = folder.resolve()
    for item in note.get("problems", []):
        for f in item.get("frames") or []:
            rel = Path(str(f.get("file", "")))
            path = (folder / rel)
            if (not rel.is_absolute() and ".." not in rel.parts and rel.parts[:1] == ("note_frames",)
                    and path.suffix.lower() in (".jpg", ".jpeg", ".png") and path.is_file()
                    and path.resolve().is_relative_to(root)):
                yield str(rel), path


def _stamp(note: dict, item: dict) -> str:
    offs = item.get("offsets") or {}
    if offs:
        return " · ".join(f"{cam_label(note, n)} ({Path(n).name}) {clock(o)}" for n, o in offs.items())
    return f"{clock(item.get('at'))} from the run start (no camera timing)"


def print_html(note: dict, folder: Path) -> str:
    e = lambda v: html.escape(str(v if v is not None else ""), quote=True)
    images = {}
    for rel, path in frame_files(note, folder):
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        images[rel] = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    out = [f"<h1>Lab note: {e(note['title'])}</h1><div class='meta'>"]
    out += [f"<div><b>{e(k)}:</b> {e(v)}</div>" for k, v in header_lines(note)]
    out.append(f"</div><p class='summary'>{e(note['summary'])}</p><h2>Steps</h2>")
    if not note["steps"]:
        out.append("<p>No protocol steps.</p>")
    for s in note["steps"]:
        label = f"{s['number']}. {STATUS.get(s['status'], s['status'])}" + (f" ({s['label']})" if s.get("label") else "")
        out.append(f"<div class='entry'><div class='label'>{e(label)}</div><div class='stamp'>{e(_stamp(note, s))}</div>"
                   f"<p>{e(s['text'])}</p></div>")
    for key, title, empty in SECTIONS:
        out.append(f"<h2>{e(title)}</h2>")
        if not note[key]:
            out.append(f"<p>{e(empty)}</p>")
        for it in note[key]:
            text = f"“{it['text']}”" if key == "said" else it["text"]
            out.append(f"<div class='entry'><div class='stamp'>{e(_stamp(note, it))}</div><p>{e(text)}</p>")
            figs = []
            for f in it.get("frames") or []:
                cap = f"{cam_label(note, f['video'])}, {clock(f['offset'])}"
                if f["file"] in images:
                    figs.append(f"<figure><img src='{images[f['file']]}' alt='{e(cap)}'><figcaption>{e(cap)}</figcaption></figure>")
                else:
                    figs.append(f"<p class='stamp'>Key frame missing: {e(cap)}</p>")
            if figs:
                out.append(f"<div class='frames'>{''.join(figs)}</div>")
            out.append("</div>")
    out.append(f"<footer>Wording: {e(note['wording'])}. Built from the run's event log. Times are minutes:seconds "
               "into each named camera recording. Videos are not part of this export.</footer>")
    return """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Lab note</title><style>
@page{size:A4;margin:17mm 17mm 20mm;@bottom-center{content:counter(page) " / " counter(pages);font:9pt Arial,sans-serif;color:#53616a}}
*{box-sizing:border-box}body{color:#202b36;font:11pt/1.45 Arial,sans-serif;margin:0}
h1{font-size:22pt;line-height:1.15;margin:0 0 12px;overflow-wrap:anywhere}
h2{font-size:13pt;margin:22px 0 8px;border-bottom:1px solid #b7c4c9;padding-bottom:4px;break-after:avoid}
.meta,footer{color:#53616a;font-size:9pt;overflow-wrap:anywhere}
.summary{border-left:3px solid #31846b;padding:8px 12px;background:#f1f6f4}
.entry{padding:6px 0;border-bottom:1px solid #e1e6e9;break-inside:avoid;overflow-wrap:anywhere}
p{margin:3px 0 6px}.label{font-weight:bold}.stamp,figcaption{font-size:9pt;color:#53616a}
.frames{display:flex;gap:6px}figure{margin:6px 0;flex:1;break-inside:avoid}img{display:block;width:100%;max-height:60mm;object-fit:contain}
footer{margin-top:20px}@media screen{body{max-width:820px;margin:30px auto;padding:20px}}
</style></head><body>""" + "".join(out) + "</body></html>"


def render_pdf(document: str) -> bytes:
    from .worker import run_worker

    return run_worker("pdf", document.encode())


def _render_pdf(document: str) -> bytes:
    try:
        from weasyprint import HTML
        from weasyprint.urls import URLFetcher
    except (ImportError, OSError) as exc:  # OSError: Pango missing on the host
        raise RuntimeError("PDF renderer unavailable") from exc
    # Images are already embedded; never fetch a URL or a local file while printing.
    return HTML(string=document, url_fetcher=URLFetcher(allowed_protocols={"data"})).write_pdf()


def run_zip(note: dict, folder: Path, document: str, pdf: bytes) -> bytes:
    events = folder / "events.jsonl"
    if not events.is_file():
        raise FileNotFoundError("this run has no events.jsonl")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("note.html", document)
        z.writestr("note.pdf", pdf)
        for name in ("note.md", "note.json", "events.jsonl", "timing.jsonl"):
            p = folder / name
            if p.is_file():
                z.write(p, name)
        for rel, path in dict(frame_files(note, folder)).items():
            z.write(path, rel)
    return buf.getvalue()
