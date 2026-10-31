# CPU tests

`workflows/tests.yml` runs on pushes and pull requests using Python 3.12.
It collects tests in every part, including synthetic video, service transport,
protocol, note PDF and table integration checks. The job has a ten-minute limit;
a newer run on the same branch cancels the older one.

Install `requirements-ci.txt` in a separate environment, then run from the root:

```sh
unset BENCH_SAMPLES
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VISION_DEVICE=cpu
"${BENCH_PY:-python3}" -m pytest -m "not models" -q -ra --durations=15
```

Linux also needs the media and PDF packages listed in the workflow. The contrib
headless OpenCV wheel includes the shaft tracker without requiring a display.
Linux torch is pinned to a CPU build from the PyTorch CPU index. The macOS entry
supports local validation; tests select CPU there too. No model weights are
installed. Whisper, MediaPipe, Transformers and Data Designer are intentionally
outside the CI requirement set. Existing cached detection JSON stays testable.

Mark tests requiring weights, GPU, Docker or physical audio devices with
`@pytest.mark.models`. Register additional markers in root `pytest.ini`.
Apply markers to the individual tests when a file also has CPU-only checks.
Mocked clients and synthetic audio tests stay enabled. Sample tests must skip
when `BENCH_SAMPLES` is unset or the needed files are missing. The optional
Data Designer test skips when its package is absent; file-only speech synthesis
is marked too because it needs an installed system speech voice. Browser scripts and JavaScript
tests are not collected by pytest.

Validate workflow edits with `actionlint .github/workflows/tests.yml`. See
[CI-REPORT.md](CI-REPORT.md) for the measured local result and remaining limits.

## Tools and licences

Existing part READMEs list their runtime libraries. CI adds no application code.

| Tool | Source | Licence | Use |
| --- | --- | --- | --- |
| pytest | https://docs.pytest.org/ | MIT | Test discovery and marker selection |
| PyTorch | https://pytorch.org/ | BSD-3-Clause | CPU tensor runtime |
| OpenCV Python wheels | https://github.com/opencv/opencv-python | MIT packaging; Apache-2.0 OpenCV | Headless image and tracker tests |
| WeasyPrint | https://weasyprint.org/ | BSD-3-Clause | PDF export tests |
| FFmpeg | https://ffmpeg.org/ | LGPL/GPL, depending on build | Media decoding |
| Pango | https://pango.gnome.org/ | LGPL-2.1-or-later | PDF text layout |
| Poppler | https://poppler.freedesktop.org/ | GPL-2.0-or-later | PDF text assertions |
| actionlint | https://github.com/rhysd/actionlint | MIT | Local workflow validation |
| checkout | https://github.com/actions/checkout | MIT | Checkout in CI |
| setup-python | https://github.com/actions/setup-python | MIT | Python selection and pip cache |
