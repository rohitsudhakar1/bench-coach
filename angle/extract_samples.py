import os
"""Extract only the twelve labelled stills from the supplied read-only videos."""
import argparse
import json
from pathlib import Path
import re

import cv2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--videos', default=os.path.join(os.environ.get('BENCH_SAMPLES', ''), 'lsv_mv'))
    parser.add_argument('--output', default='angle/samples')
    args = parser.parse_args()
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    labels = Path(__file__).parent/'samples/labels.json'
    for sample in json.loads(labels.read_text())['samples']:
        match = re.fullmatch(r'(\d+-\d+)-(Left|Right)-(\d+)\.jpg', sample['file'])
        clip, side, seconds = match.groups()
        path = Path(args.videos)/f'CP-{side}-Front-{clip}.mp4'
        cap = cv2.VideoCapture(str(path))
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, int(seconds)*1000)
            ok, image = cap.read()
            if not ok or not cv2.imwrite(str(target/sample['file']), image):
                raise ValueError(f'Cannot extract {sample["file"]} from {path}')
        finally:
            cap.release()
    print(f'Extracted 12 stills to {target}; labels remain in {labels}')


if __name__ == '__main__':
    main()
