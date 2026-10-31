"""Offline notebook exports. See README for the pinned ELN/RO-Crate specifications."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .export import frame_files
from .render import SECTIONS, header_lines

CRATE = "https://w3id.org/ro/crate/1.2"
ELN = "https://purl.org/elnconsortium/eln-spec/1.2+20260923"
COLUMNS = ("step", "instruction", "status", "evidence", "event_epoch_seconds",
           "run_seconds", "camera_offsets_seconds")


def step_rows(note: dict):
    for step in note["steps"]:
        yield (step["number"], step["text"], step["status"], step.get("label") or "",
               step.get("t"), step.get("at"),
               json.dumps(step.get("offsets") or {}, ensure_ascii=False, sort_keys=True))


def steps_csv(note: dict) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    for row in step_rows(note):
        # Prevent protocol or speech text becoming a formula when opened in a spreadsheet.
        writer.writerow("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                        else v for v in row)
    return output.getvalue()


def attachments(note: dict, folder: Path) -> dict[str, Path]:
    root = folder.resolve()
    files = dict(frame_files(note, folder))
    for name in ("events.jsonl", "timing.jsonl"):
        path = folder / name
        if path.is_file() and path.resolve().is_relative_to(root):
            files[name] = path
    return files


def entry_html(note: dict, folder: Path) -> str:
    """Simple escaped HTML suitable for copying from a browser into a notebook."""
    esc = lambda v: html.escape("" if v is None else str(v), quote=True)
    out = [f"<h1>{esc(note['title'])}</h1>"]
    out.extend(f"<p><strong>{esc(k)}:</strong> {esc(v)}</p>" for k, v in header_lines(note))
    out += [f"<p>{esc(note['summary'])}</p>", "<h2>Step results</h2>",
            "<p>Times are recorded status/evidence times, not measured step durations. "
            "Blank times are unknown. Camera offsets are seconds into each recording.</p>",
            "<table border=\"1\"><thead><tr>"]
    out.extend(f"<th>{esc(c)}</th>" for c in COLUMNS)
    out.append("</tr></thead><tbody>")
    for row in step_rows(note):
        out.append("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in row) + "</tr>")
    out.append("</tbody></table>")
    for key, title, empty in SECTIONS:
        out.append(f"<h2>{esc(title)}</h2>")
        for item in note.get(key) or [{"text": empty}]:
            at = item.get("at")
            stamp = "Unknown time" if at is None else f"{at:g} seconds from run start"
            out.append(f"<p>{esc(stamp)}: {esc(item['text'])}</p>")
    out.append("<h2>Attachments</h2><ul>")
    for name in ("entry.html", "steps.csv", "note.json", *sorted(attachments(note, folder))):
        out.append(f"<li>{esc(name)} (included in .eln; upload separately when pasting)</li>")
    for video in note.get("videos", []):
        out.append(f"<li>{esc(video['name'])} (recording, not included)</li>")
    if (folder / "mic.wav").is_file():
        out.append("<li>mic.wav (recording, not included)</li>")
    out += ["</ul>", f"<p>Wording: {esc(note.get('wording', 'rules'))}. "
            "Results reflect recorded evidence, not independent verification.</p>"]
    return '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Lab note</title></head><body>' + "".join(out) + "</body></html>"


def eln_archive(note: dict, folder: Path) -> bytes:
    document = entry_html(note, folder)
    payload = {"entry.html": document.encode(), "steps.csv": steps_csv(note).encode(),
               "note.json": json.dumps(note, ensure_ascii=False, indent=2).encode()}
    payload.update({name: path.read_bytes() for name, path in attachments(note, folder).items()})
    published = datetime.now(timezone.utc).isoformat()
    created = (datetime.fromtimestamp(note["start"], timezone.utc).isoformat()
               if note.get("start") is not None else None)
    author = {"@id": "#author", "@type": "Person", "name": note.get("who") or "Not recorded"}
    files = []
    mime = {".html": "text/html", ".csv": "text/csv", ".json": "application/json",
            ".jsonl": "application/x-ndjson", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    for name, content in payload.items():
        files.append({"@id": "./experiment/" + name, "@type": "File", "name": name,
                      "encodingFormat": mime[Path(name).suffix], "contentSize": str(len(content)),
                      "sha256": hashlib.sha256(content).hexdigest()})
    refs = [{"@id": f["@id"]} for f in files]
    experiment = {"@id": "./experiment/", "@type": "Dataset", "genre": "experiment",
                  "name": note["title"], "identifier": note["run"], "text": document,
                  "dateCreated": created, "author": {"@id": "#author"}, "hasPart": refs}
    if created is None:
        experiment.pop("dateCreated")
    graph = [{"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
              "about": {"@id": "./"}, "conformsTo": {"@id": CRATE}},
             {"@id": "./", "@type": "Dataset", "name": "lab-note.eln",
              "description": note["summary"], "datePublished": published,
              "conformsTo": {"@id": ELN}, "hasPart": [{"@id": "./experiment/"}, *refs]},
             experiment, author, *files]
    metadata = {"@context": CRATE + "/context", "@graph": graph}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("lab-note/ro-crate-metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        for name, content in payload.items():
            archive.writestr("lab-note/experiment/" + name, content)
    return output.getvalue()
