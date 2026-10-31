"""Isolated, bounded worker for the hub's NVIDIA panel."""
import json
from pathlib import Path
import sys
import zipfile

CHECKERS = {
    'skipped_step': 'Exact set of step numbers (or none)',
    'missing_step_text': 'At least 60% of the step’s key words',
    'tip_reuse': 'Yes/no agreement with recorded tip changes',
}


def run(kind, folder):
    output = folder / 'nvidia-ui'
    output.mkdir(exist_ok=True)
    if kind == 'export':
        from nvidia.export_tasks import export_run
        from nvidia.runner import score
        paths = export_run(folder)
        result = {'status': 'ready', 'tasks': [], 'score': score(folder / 'tasks', 'rules')}
        (output / 'zips').mkdir(exist_ok=True)
        for path in paths:
            task = json.loads((path / 'environment/context/task.json').read_text())
            result['tasks'].append({'id': path.name, 'question': task['question'],
                                    'kind': task['kind'], 'checker': CHECKERS[task['kind']]})
            with zipfile.ZipFile(output / 'zips' / (path.name + '.zip'), 'w', zipfile.ZIP_DEFLATED) as archive:
                for item in sorted(path.rglob('*')):
                    if item.is_file():
                        archive.write(item, item.relative_to(path.parent))
    else:
        from nvidia.synth import synthesize
        result = {'status': 'ready', **synthesize([folder], 20, output / 'synth', engine='auto')}
    temporary = output / (kind + '.tmp')
    temporary.write_text(json.dumps(result, allow_nan=False))
    temporary.replace(output / (kind + '.json'))


if __name__ == '__main__':
    run(sys.argv[1], Path(sys.argv[2]))
