"""Repeat offline note builds over probe recordings, tracking Python and native RSS."""
import gc
import json
from pathlib import Path
import sys
import tracemalloc

import psutil
from note.service import make_note, _locks


def main(root, cycles=1):
    tracemalloc.start()
    process = psutil.Process()
    for i, folder in enumerate(sorted(root.glob('probe-*')) * cycles):
        timing = [json.loads(line) for line in (folder / 'timing.jsonl').read_text().splitlines()]
        start, stop = timing[0]['t'], timing[-1]['t']
        events = [dict(t=start, kind='run', detail=dict(status='start', run=folder.name)),
                  dict(t=start + .5, kind='warning', detail=dict(text='Probe warning')),
                  dict(t=stop, kind='run', detail=dict(status='stop', run=folder.name))]
        (folder / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        result = make_note(folder, use_claude=False)
        frames = sum(len(p['frames']) for p in result['note']['problems'])
        del result, timing, events
        gc.collect()
        print(json.dumps(dict(run=i, rss_mib=round(process.memory_info().rss / 2**20, 2),
                              python_mib=round(tracemalloc.get_traced_memory()[0] / 2**20, 2),
                              locks=len(_locks), frames=frames)), flush=True)
    tracemalloc.stop()


if __name__ == '__main__':
    main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 1)
