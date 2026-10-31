"""Read-only trainer summaries. Never open recordings or external protocol paths."""
import json
import math
import re
from pathlib import Path

from note.build import build_note
from note.runfolder import Run

SAFE = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
STATUSES = ('done', 'skipped', 'unsure', 'not finished', 'not reached')


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_run(folder):
    events = []
    issues = []
    path = folder / 'events.jsonl'
    if path.is_file() and not path.is_symlink():
        try:
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        ev = json.loads(line)
                        if not isinstance(ev, dict) or not number(ev.get('t')) or not isinstance(ev.get('detail'), dict):
                            raise ValueError()
                        events.append(ev)
                    except (ValueError, TypeError):
                        issues.append('Some event rows could not be read.')
        except (OSError, UnicodeError):
            issues.append('Event log could not be read.')
    events.sort(key=lambda e: e['t'])
    starts = [e for e in events if e.get('kind') == 'run' and e['detail'].get('status') == 'start' and e['detail'].get('run') == folder.name]
    start = starts[-1] if starts else None
    detail = start['detail'] if start else {}
    t0 = start['t'] if start else None
    stops = [e['t'] for e in events if e.get('kind') == 'run' and e['detail'].get('status') == 'stop' and e['detail'].get('run') == folder.name and (t0 is None or e['t'] >= t0)]
    end = stops[0] if stops else None
    events = [e for e in events if (t0 is None or e['t'] >= t0) and (end is None or e['t'] <= end)]
    proto = detail.get('protocol_data')
    proto = proto if isinstance(proto, dict) and isinstance(proto.get('steps'), list) else {}
    saved = None
    path = folder / 'note.json'
    if path.is_file() and not path.is_symlink():
        try:
            candidate = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(candidate, dict) or not all(isinstance(candidate.get(k), list) and all(isinstance(s, dict) for s in candidate[k]) for k in ('steps', 'technique', 'problems')):
                raise ValueError()
            if candidate.get('run', folder.name) != folder.name:
                raise ValueError()
            saved = candidate
        except (ValueError, OSError, UnicodeError):
            issues.append('Saved note could not be read; using events.')
    note = saved
    if note is None:
        try:
            note = build_note(Run(folder.name, folder, events, proto, detail.get('protocol'), [], t0, end))
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            note = {'steps': [], 'technique': [], 'problems': []}
            issues.append('Some run details could not be summarized.')
    if not saved and not proto:
        issues.append('No protocol snapshot; step counts are unavailable.')
    student = detail.get('student') or note.get('who')
    student = student.strip() if isinstance(student, str) else ''
    protocol = detail.get('protocol') or note.get('protocol_file') or note.get('title') or 'Unknown protocol'
    protocol = str(protocol)
    steps = [s for s in note['steps'] if isinstance(s.get('index'), int) and isinstance(s.get('text'), str)]
    start_time = t0 if t0 is not None else note.get('start')
    duration = end - t0 if end is not None and t0 is not None else note.get('duration') if saved else None
    return dict(run=folder.name, student=student, protocol=protocol,
                protocol_name=str(proto.get('name') or note.get('title') or protocol),
                start=start_time if number(start_time) else None,
                duration=duration if number(duration) and duration >= 0 else None,
                status='complete' if end is not None else 'saved note' if saved else 'in progress' if start else 'incomplete',
                counts={s: sum(step.get('status') == s for step in steps) for s in STATUSES}, steps=steps,
                reused_tips=sum(p.get('kind') == 'tip' for p in note['problems']),
                tilts_over=sum(p['over'] for p in note['technique'] if p.get('kind') == 'angle' and number(p.get('over')) and p['over'] >= 0),
                note_url=f'/lab-note/{folder.name}?format=html' if saved or (folder / 'note.html').is_file() else None,
                source='saved note' if saved else 'events', issues=sorted(set(issues)))


def overview(runs: Path, protocol='', student=''):
    rows = [read_run(p) for p in sorted(runs.iterdir()) if p.is_dir() and not p.is_symlink() and SAFE.fullmatch(p.name)
            and any((p / name).is_file() and not (p / name).is_symlink() for name in ('events.jsonl', 'note.json'))] if runs.is_dir() else []
    protocols = sorted({r['protocol'] for r in rows})
    selected = [r for r in rows if (not protocol or r['protocol'] == protocol) and (not student or student.casefold() in r['student'].casefold())]
    selected.sort(key=lambda r: (r['start'] or 0, r['run']), reverse=True)
    missed = {}
    if protocol:
        for row in selected:
            for step in row['steps']:
                # Keep edited versions of the same protocol separate by instruction text.
                key = (step['index'], step['text'])
                item = missed.setdefault(key, dict(index=key[0], text=key[1], runs=0, missed=0, skipped=0, unsure=0, unfinished=0))
                item['runs'] += 1
                status = step.get('status')
                if status in STATUSES and status != 'done':
                    item['missed'] += 1
                    item[status if status in ('skipped', 'unsure') else 'unfinished'] += 1
        for item in missed.values():
            item['rate'] = item['missed'] / item['runs']
    return dict(runs=selected, protocols=protocols, most_missed=sorted(missed.values(), key=lambda s: (-s['rate'], -s['missed'], s['index'])), total=len(rows))
