"""Voice regression: no speaker or real microphone; bounded model subprocess."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from coach import heard
from coach.eval_voicerobust import CASES, cached_model, prepare
from coach.protocol import Step, Expect
from coach.tests.helpers import demo_brain, said, T0


@pytest.mark.parametrize('text,start,target,answer', CASES)
def test_matrix_oracles(text, start, target, answer):
    b = demo_brain()
    for i in range(start):
        b.hear('done', t=T0 + i)
    answers = said(b.hear(text, t=T0 + 20))
    assert b.current == target
    if answer is not None:
        assert answers == [answer]


@pytest.mark.parametrize('word', ['aye', 'A', 'ay', 'eh'])
def test_tube_a_spellings(word):
    assert heard.objects_in('drew from tube ' + word) == {'tube a'}


@pytest.mark.parametrize('letter', ['A', 'B', 'C', 'Z'])
def test_joined_tube_letters_preserved(letter):
    assert heard.objects_in('drew from tube' + letter) == {'tube ' + letter.lower()}


def test_unknown_explicit_tube_cannot_confirm_current_step():
    b = demo_brain()
    b.hear("tip's on", t=T0 + 1)
    b.hear('I drew from tube Z', t=T0 + 2)
    assert b.current == 1


@pytest.mark.parametrize('count,expected', [('free', '3'), ('for', '4'), ('six', '6'), ('ten', '10')])
def test_count_homophones_are_checked(count, expected):
    claim = heard.statement(f'Mixed {count} times')
    assert claim['count'] == expected
    step = Step(0, 'Mix three times', [Expect('dispense', 'tube B')])
    assert heard.claim_fits(step, claim) == (expected == '3')


@pytest.mark.models
def test_compact_noisy_fake_mic_matrix(tmp_path):
    if not cached_model():
        pytest.skip('Complete local Whisper weights missing')
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg missing')
    prepare(tmp_path, fixtures=True)
    output = tmp_path / 'results.jsonl'
    # Includes process startup and inference; kills the worker if it exceeds the budget.
    result = subprocess.run([sys.executable, '-m', 'coach.eval_voicerobust', 'measure',
                    '--directory', str(tmp_path), '--output', str(output)],
                   env=dict(os.environ, HF_HUB_OFFLINE='1'), check=False,
                   capture_output=True, text=True, timeout=80)
    if result.returncode == 75:
        pytest.skip('Memory below 40% free; model batch paused')
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 18
    assert all(r['exact'] and r['correct'] and r['utterances'] == 1 for r in rows), rows
