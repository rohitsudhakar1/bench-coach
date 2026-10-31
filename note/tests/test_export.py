import io
import shutil
import subprocess
import zipfile

import pytest

from note.export import print_html, render_pdf, run_zip
from note.service import make_note


def test_pdf_and_zip(fake_run, tmp_path):
    note = make_note(fake_run, use_claude=False)["note"]
    doc = print_html(note, fake_run)
    assert doc.count("data:image/jpeg;base64,") == 6
    pdf = render_pdf(doc)
    assert pdf.startswith(b"%PDF")
    if shutil.which("pdftotext"):
        (tmp_path / "n.pdf").write_bytes(pdf)
        text = subprocess.run(["pdftotext", "-layout", str(tmp_path / "n.pdf"), "-"],
                              capture_output=True, text=True).stdout
        for s in note["steps"]:
            assert s["text"][:25] in text
        assert "seen+said" in text and "lot 4471B" in text and "Maya Lopez" in text
    z = zipfile.ZipFile(io.BytesIO(run_zip(note, fake_run, doc, pdf)))
    names = set(z.namelist())
    assert {"note.html", "note.pdf", "note.md", "note.json", "events.jsonl", "timing.jsonl"} <= names
    assert sum(n.startswith("note_frames/") for n in names) == 6
    assert not any(n.endswith(".mp4") for n in names)


def test_frames_outside_the_run_are_not_read(fake_run, tmp_path):
    note = make_note(fake_run, use_claude=False)["note"]
    (tmp_path / "secret.jpg").write_bytes(b"\xff\xd8 not a frame")
    note["problems"][0]["frames"] = [{"file": "../secret.jpg", "video": "cam0.mp4", "offset": 1.0}]
    doc = print_html(note, fake_run)
    assert "Key frame missing" in doc


def test_zip_needs_events(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_zip({"problems": []}, tmp_path, "<html></html>", b"%PDF")
