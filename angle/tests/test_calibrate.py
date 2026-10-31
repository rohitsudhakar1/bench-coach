import json
import struct

import cv2
import numpy as np
import pytest

from angle.calibrate import collect, fit, main, validate_times, folder_pairs, video_pairs
from angle.geometry import Calibration, angle_between
from angle.print_board import write_board
from .synthetic import board_pairs


def test_rendered_calibration_cli_recovery(tmp_path):
    pairs, k, rotation, translation = board_pairs()
    for side in ('left', 'right'):
        (tmp_path/side).mkdir()
    for name, a, b in pairs:
        cv2.imwrite(str(tmp_path/'left'/name), a)
        cv2.imwrite(str(tmp_path/'right'/name), b)
    output = tmp_path/'fit.json'
    assert main(['--left', str(tmp_path/'left'), '--right', str(tmp_path/'right'), '--output', str(output)]) == 0
    cal = Calibration.load(output)
    rotation_error = float(np.degrees(np.linalg.norm(cv2.Rodrigues(cal.R @ rotation.T)[0])))
    translation_error = float(np.linalg.norm(cal.T-translation))
    gravity_error = angle_between(cal.gravity, [0, 0, 1])
    assert rotation_error < .5
    assert translation_error < 3
    assert np.max(abs(np.diag(cal.K1)[:2]/np.diag(k)[:2]-1)) < .02
    assert gravity_error < .5
    print(f'RECOVERY rotation={rotation_error:.6f} deg translation={translation_error:.6f} mm gravity={gravity_error:.6f} deg')


def test_first_missing_board_and_duplicate_poses():
    blank = np.zeros((240, 320, 3), np.uint8)
    with pytest.raises(ValueError, match='first pair'):
        collect([('0.png', blank, blank)], (9, 6))
    points = [np.zeros((54, 1, 2), np.float32)]*12
    with pytest.raises(ValueError, match='distinct'):
        fit(points, points, list(map(str, range(12))), (640, 480))


@pytest.mark.parametrize('stamps,old', [([98, 98], [0, 0]), ([101, 101], [0, 0]),
                                      ([float('nan'), 100], [0, 0]), ([99.5, 99.9], [0, 0]),
                                      ([99.9, 99.9], [99.9, 99.8])])
def test_bad_capture_pair_times(stamps, old):
    with pytest.raises(ValueError):
        validate_times(stamps, old, 100, .1)


def test_preserve_saved_file_on_redo(tmp_path):
    output = tmp_path/'fit.json'
    output.write_text('old calibration')
    assert main(['--left', str(tmp_path), '--output', str(output)]) == 2
    assert output.read_text() == 'old calibration'
    assert main(['--capture', 'http://unused', '--duration', 'nan']) == 2


def test_board_physical_scale_and_corners(tmp_path):
    output = tmp_path/'board.png'
    write_board(output)
    data = output.read_bytes()
    pos = data.index(b'pHYs')
    assert struct.unpack('>IIB', data[pos+4:pos+13]) == (10000, 10000, 1)
    image = cv2.imread(str(output), 0)
    assert image.shape == (1950, 2700)
    found, corners = cv2.findChessboardCornersSB(image, (9, 6))
    assert found and np.linalg.norm(corners[1]-corners[0]) == pytest.approx(250, abs=.1)


def test_folders_require_matching_names(tmp_path):
    for side in ('left', 'right'):
        (tmp_path/side).mkdir()
    (tmp_path/'left'/'a.png').touch()
    with pytest.raises(ValueError, match='filenames'):
        list(folder_pairs(tmp_path/'left', tmp_path/'right'))
