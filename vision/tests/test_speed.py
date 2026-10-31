"""Batch routing, cache reuse and motion policy, with no weights or network."""
import threading
import time

import numpy as np
import pytest

from vision.core import Det
from vision.detector import AsyncDetector, MotionGate, WithContainers, detect_batch


def frame(value=0):
    return np.full((8, 12, 3), value, dtype=np.uint8)


class Detector:
    def __init__(self, label='pipette'):
        self.label = label
        self.batches = []

    def batch(self, frames):
        self.batches.append([int(f[0, 0, 0]) for f in frames])
        return [[Det(self.label, (int(f[0, 0, 0]), 0, 10, 10), .5)] for f in frames]

    def with_prompts(self, prompts):
        return Detector('tube')


def test_batch_fallback_and_empty():
    calls = []
    def one(f):
        calls.append(f)
        return [f]
    assert detect_batch(one, []) == []
    assert detect_batch(one, [1, 2]) == [[1], [2]]
    assert calls == [1, 2]


def test_container_cadence_tracks_camera_identity_not_batch_position():
    d = WithContainers(Detector(), every=2)
    first = d.batch([frame(2), frame(7)], [2, 7])
    assert all([x.label for x in row] == ['pipette', 'container:tube'] for row in first)
    assert [x.label for x in d.batch([frame(7)], [7])[0]] == ['pipette']
    third = d.batch([frame(7), frame(2)], [7, 2])
    assert [x.label for x in third[0]] == ['pipette', 'container:tube']
    assert [x.label for x in third[1]] == ['pipette']
    assert d.cdet.batches == [[2, 7], [7]]
    assert d.batch([], []) == []
    with pytest.raises(ValueError):
        d.batch([frame()], [])


def test_motion_compares_to_successful_call_and_does_not_hide_small_changes():
    gate = MotionGate()
    f = frame()
    assert gate.needs_detection(0, 10, f)
    gate.remember(0, 10, f)
    assert not gate.needs_detection(0, 10.9, f.copy())
    assert gate.needs_detection(0, 11, f)
    assert gate.needs_detection(1, 10.1, f)
    assert gate.needs_detection(0, 9, f)  # source restarted
    assert gate.needs_detection(0, 10.1, f[:4])
    f[3, 4, 2] = 1  # a single colour channel at a single pixel
    assert gate.needs_detection(0, 10.1, f)
    assert gate.needs_detection(0, 10.2, f)  # skip checks never move the reference


def wait_for(predicate):
    end = time.monotonic() + 2
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('worker did not finish')


def test_async_batches_cameras_and_keeps_timestamps_without_fake_fresh_results():
    d = Detector()
    worker = AsyncDetector(d, 2)
    try:
        worker.submit_batch([(0, 10, frame(2)), (1, 20, frame(7))])
        wait_for(lambda: worker.latest(1)[0] == 1)
        assert d.batches == [[2, 7]]
        assert worker.latest(0)[1][0] == 10
        assert worker.latest(1)[1][0] == 20
        worker.submit_batch([(0, 10.1, frame(2)), (1, 20.1, frame(8))])
        wait_for(lambda: worker.latest(1)[0] == 2)
        assert worker.latest(0)[0] == 1
        assert d.batches[-1] == [8]
        worker.submit(0, 11, frame(2))
        wait_for(lambda: worker.latest(0)[0] == 2)
    finally:
        worker.close()
    assert not worker.thread.is_alive()


def test_async_keeps_only_latest_frame_while_busy():
    entered, release = threading.Event(), threading.Event()
    class Blocking(Detector):
        def batch(self, frames):
            entered.set()
            assert release.wait(2)
            return super().batch(frames)
    d = Blocking()
    worker = AsyncDetector(d, 2)
    try:
        worker.submit(0, 1, frame(1))
        assert entered.wait(1)
        worker.submit(0, 2, frame(2))
        worker.submit_batch([(0, 3, frame(3)), (1, 4, frame(4))])
        release.set()
        wait_for(lambda: worker.latest(1)[0] == 1)
        assert d.batches == [[1], [3, 4]]
        assert worker.latest(0)[1][0] == 3
    finally:
        release.set()
        worker.close()


def test_cpu_florence_falls_back_but_other_batch_adapters_do_not():
    class CPU(Detector):
        device = 'cpu'
        batch_on_cpu = False
        def __call__(self, f):
            return self.batch([f])[0]
    d = CPU()
    assert len(detect_batch(d, [frame(1), frame(2)])) == 2
    assert d.batches == [[1], [2]]
    d.batch_on_cpu = True
    detect_batch(d, [frame(3), frame(4)])
    assert d.batches[-1] == [3, 4]


def test_failure_does_not_mark_motion_reference_as_successful():
    class Flaky(Detector):
        def batch(self, frames):
            if not self.batches:
                self.batches.append('failed')
                raise ValueError('simulated failure')
            return super().batch(frames)
    d = Flaky()
    worker = AsyncDetector(d, 1)
    try:
        worker.submit(0, 1, frame())
        wait_for(lambda: bool(d.batches))
        assert worker.latest(0)[0] == 0
        worker.submit(0, 1.1, frame())
        wait_for(lambda: worker.latest(0)[0] == 1)
        assert worker.latest(0)[1][0] == 1.1
    finally:
        worker.close()


def test_agreement_rejects_extra_missing_and_changed_labels():
    from vision.eval.speed_accuracy import agreement
    a = Det('pipette', (0, 0, 10, 10), .5)
    b = Det('tube', (20, 0, 30, 10), .5)
    assert agreement([a, b], [b, a])[0]
    assert not agreement([a], [a, a])[0]
    assert not agreement([a], [b])[0]
    assert not agreement([a], [])[0]
    assert not agreement([a], [Det('pipette', (0, 0, 10, 10), .6)])[0]


def test_florence_runs_each_frame_with_its_size_and_dtype():
    # Florence-2 runs one frame at a time so every box keeps its own confidence score.
    import torch
    from transformers.feature_extraction_utils import BatchFeature
    from vision.zoo import Florence2
    class Tokenizer:
        def convert_ids_to_tokens(self, ids):
            return ['tok'] * len(ids)
    class Processor:
        def __init__(self):
            self.calls = 0
            self.sizes = []
            self.tokenizer = Tokenizer()
        def __call__(self, text, images, return_tensors):
            self.calls += 1
            return BatchFeature({'pixel_values': torch.zeros(1, 3, 4, 4),
                                 'input_ids': torch.ones(1, 4, dtype=torch.long)})
        def batch_decode(self, ids, skip_special_tokens):
            return ['result'] * len(ids)
        def post_process_generation(self, text, task, image_size):
            self.sizes.append(image_size)
            return {task: {'labels': ['pipette'], 'bboxes': [[1, 2, 3, 4]]}}
    class Model:
        def generate(self, input_ids, pixel_values, **kwargs):
            assert input_ids.dtype == torch.long
            assert pixel_values.dtype == torch.float16
            return input_ids
    d = Florence2.__new__(Florence2)
    d.torch, d.device, d.dtype, d.raw, d.last_parts = torch, 'cpu', torch.float16, True, []
    d.proc, d.model = Processor(), Model()
    d.set_prompts({'pipette': 'pipette'})
    images = [frame(), frame()[:4, :6]]
    out = d.batch(images)
    assert len(out) == 2 and all(len(dets) == 1 for dets in out)
    assert d.proc.calls == 2
    assert d.proc.sizes == [(12, 8), (6, 4)]


def test_sample_pairs_skip_if_unavailable():
    import os
    from vision.core import sample
    from vision.eval.speed_pairs import pairs
    if not os.environ.get('BENCH_SAMPLES') or any(sample(
            f'lsv_mv/CP-{side}-Front-414-445.mp4') is None for side in ('Left', 'Right')):
        pytest.skip('BENCH_SAMPLES two-view clips unavailable')
    images = list(pairs(2))
    assert len(images) == 2
    assert all(len(pair) == 2 and all(max(f.shape[:2]) == 640 for f in pair) for pair in images)


def test_file_loop_batches_both_views_and_finishes_remaining_view(monkeypatch):
    from vision import __main__ as cli, hands, pipeline, sink, sources, zoo
    visits = []
    class Source:
        def __init__(self, path, *args):
            self.items = iter([(10, frame(1)), (11, frame(3))] if path == 'a' else [(10, frame(2))])
            self.shape = (8, 12, 3)
        def read(self):
            return next(self.items, (None, None))
        def close(self):
            pass
    class Hands:
        def __init__(self, **kwargs):
            pass
        def __call__(self, *args):
            return []
        def close(self):
            pass
    class Vision:
        def __init__(self, *args, **kwargs):
            pass
        def step(self, cam, t, w, h, landmarked, raw):
            visits.append((cam, t, raw[0].box[0]))
            return []
        def finish(self):
            return []
    class Sink:
        def __init__(self, *args):
            pass
        def send(self, events):
            pass
        def close(self):
            pass
    d = Detector()
    monkeypatch.setattr(sources, 'FileSource', Source)
    monkeypatch.setattr(hands, 'HandTracker', Hands)
    monkeypatch.setattr(pipeline, 'Vision', Vision)
    monkeypatch.setattr(sink, 'Sink', Sink)
    monkeypatch.setattr(zoo, 'make', lambda _: d)
    monkeypatch.setattr(cli.signal, 'signal', lambda *args: None)
    cli.main(['--source', 'a', 'b', '--hub', 'none', '--port', '0'])
    assert d.batches == [[1, 2], [3]]
    assert visits == [(0, 10, 1), (1, 10, 2), (0, 11, 3)]


def test_dino_reuses_only_text_and_processes_new_pixels():
    import torch
    from transformers.feature_extraction_utils import BatchFeature
    from vision.detector import GroundingDino
    class Processor:
        def __init__(self):
            self.full_calls = 0
            self.image_processor = self.images
        def images(self, images, return_tensors):
            return BatchFeature({'pixel_values': torch.tensor([float(f.mean()) for f in images]),
                                 'pixel_mask': torch.ones(len(images), 1, dtype=torch.long)})
        def __call__(self, text, images, return_tensors):
            self.full_calls += 1
            result = self.images(images, return_tensors)
            result['input_ids'] = torch.ones(len(images), 4, dtype=torch.long)
            result['attention_mask'] = torch.ones(len(images), 4, dtype=torch.long)
            return result
        def post_process_grounded_object_detection(self, out, input_ids, **kwargs):
            return [{'text_labels': ['pipette'], 'boxes': torch.tensor([[x, 0, x+2, 2]]),
                     'scores': torch.tensor([.5])} for x in out]
    d = GroundingDino.__new__(GroundingDino)
    d.torch, d.device, d.raw, d.bt, d.tt = torch, 'cpu', True, .25, .2
    d.proc = Processor()
    d.model = lambda **inp: inp['pixel_values']
    d.set_prompts({'pipette': 'pipette'})
    assert [r[0].box[0] for r in d.batch([frame(1), frame(2)])] == [1, 2]
    assert [r[0].box[0] for r in d.batch([frame(3), frame(4)])] == [3, 4]
    assert d.proc.full_calls == 1
    assert set(d._text_inputs[2]) == {'input_ids', 'attention_mask'}
    other = d.with_prompts({'tube': 'tube'})
    assert not other._text_inputs and d._text_inputs


def test_heldout_scorer_refuses_partial_model_cache(tmp_path, monkeypatch):
    import json
    import sys
    from vision.eval import speed_recache
    (tmp_path / 'cache').mkdir()
    (tmp_path / 'labels.json').write_text(json.dumps({'clips': {
        'held': {'path': 'clip.mp4', 'split': 'hold', 'start': 0, 'seconds': 1}}, 'frames': []}))
    speed_recache.write(tmp_path / 'cache' / 'clip@0.json.gz', {
        'video': 'clip.mp4', 'frames': [{'t': .1, 'dets': []}]})
    monkeypatch.setattr(speed_recache, 'HERE', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['speed_recache', '--detector', 'gdino-tiny', '--score'])
    with pytest.raises(SystemExit, match='refusing partial-cache score'):
        speed_recache.main()
