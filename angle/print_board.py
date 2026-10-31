"""Printable 25 mm squares, 9x6 inner corners, with physical PNG scale."""
import argparse
from pathlib import Path
import struct
import zlib

import cv2
import numpy as np


def write_board(path):
    # Ten pixels per mm = 254 dpi. Landscape A4 with a 10 mm white border.
    image = np.full((1950, 2700), 255, np.uint8)
    for row in range(7):
        for col in range(10):
            if (row+col) % 2 == 0:
                image[100+row*250:100+(row+1)*250, 100+col*250:100+(col+1)*250] = 0
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise ValueError('Cannot encode board')
    data = encoded.tobytes()
    payload = b'pHYs' + struct.pack('>IIB', 10000, 10000, 1)
    chunk = struct.pack('>I', 9) + payload + struct.pack('>I', zlib.crc32(payload))
    Path(path).write_bytes(data[:33] + chunk + data[33:])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default='angle/checkerboard-25mm.png')
    args = p.parse_args()
    write_board(args.output)
    print(f'{args.output}: print 270 x 195 mm, 100% scale. Measure 25 mm squares on paper.')


if __name__ == '__main__':
    main()
