"""Checks that a fresh clone of the repo has what scripts/setup.sh and the README need."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Parts that keep their own environment, and names the root file spells differently.
OWN_ENV = {'data-designer'}
SAME_AS = {'opencv-python': 'opencv-contrib-python'}


def names(path):
    out = {}
    for line in path.read_text().splitlines():
        line = line.split('#')[0].strip()
        if line:
            name = re.split(r'[<>=!~\[ ;]', line, 1)[0].lower().replace('_', '-')
            out[name] = line[len(name):].replace(' ', '')
    return out


def test_root_requirements_cover_every_part():
    root = names(ROOT / 'requirements.txt')
    for part in sorted(ROOT.glob('*/requirements.txt')):
        for name, pin in names(part).items():
            if name in OWN_ENV:
                continue
            name = SAME_AS.get(name, name)
            assert name in root, f'{name} from {part.parent.name} is missing in requirements.txt'
            if pin:
                assert root[name] == pin, f'{name}: {part.parent.name} pins {pin}, root has {root[name]}'


def test_root_does_not_install_both_opencv_builds():
    assert 'opencv-python' not in names(ROOT / 'requirements.txt')


def tracked(*paths):
    out = subprocess.run(['git', 'ls-files', '--', *paths], cwd=ROOT, capture_output=True, text=True)
    return set(out.stdout.split())


def test_quick_start_files_are_committed():
    need = ['scripts/setup.sh', 'scripts/demo.sh', 'requirements.txt',
            'app/samples/demo.jsonl', 'protocols/pipetting-demo.yaml']
    assert set(need) <= tracked(*need)


def test_no_machine_paths_in_tracked_files():
    home = '/' + 'Users' + '/'  # split so this file does not match itself
    out = subprocess.run(['git', 'grep', '-n', '-I', home, '--', '.', ':!*.lock'],
                         cwd=ROOT, capture_output=True, text=True)
    assert not out.stdout, out.stdout
