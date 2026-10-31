"""Undirected shaft geometry; camera 0 is the world coordinate frame."""
from dataclasses import dataclass
import json

import cv2
import numpy as np


def unit(vector):
    vector = np.asarray(vector, dtype=float)
    length = np.linalg.norm(vector)
    if not np.isfinite(vector).all() or length < 1e-9:
        raise ValueError('Direction must be finite and nonzero')
    return vector / length


def angle_between(first, second):
    # abs makes either endpoint order equivalent.
    return float(np.degrees(np.arccos(np.clip(abs(unit(first) @ unit(second)), 0, 1))))


def image_angle(points):
    points = np.asarray(points, dtype=float).reshape(2, 2)
    return angle_between(points[1] - points[0], [0, 1])


@dataclass
class Calibration:
    K1: np.ndarray
    D1: np.ndarray
    K2: np.ndarray
    D2: np.ndarray
    R: np.ndarray
    T: np.ndarray
    gravity: np.ndarray
    size: tuple

    def __post_init__(self):
        for name in ('K1', 'D1', 'K2', 'D2', 'R', 'T', 'gravity'):
            value = np.asarray(getattr(self, name), dtype=float)
            if not np.isfinite(value).all():
                raise ValueError(f'Nonfinite calibration {name}')
            setattr(self, name, value)
        for name in ('K1', 'K2'):
            matrix = getattr(self, name)
            if (matrix.shape != (3, 3) or matrix[0, 0] <= 0 or matrix[1, 1] <= 0
                    or not np.allclose(matrix[2], [0, 0, 1])):
                raise ValueError('Invalid camera intrinsics')
        if (self.R.shape != (3, 3) or not np.allclose(self.R.T @ self.R, np.eye(3), atol=1e-5)
                or not np.isclose(np.linalg.det(self.R), 1, atol=1e-5)):
            raise ValueError('Invalid stereo rotation')
        if self.T.size != 3 or np.linalg.norm(self.T) < 1e-6 or self.gravity.size != 3:
            raise ValueError('Invalid baseline or gravity')
        self.T = self.T.reshape(3)
        self.gravity = unit(self.gravity.reshape(3))
        if any(d.size not in (4, 5, 8, 12, 14) for d in (self.D1, self.D2)):
            raise ValueError('Unsupported distortion coefficients')
        if len(self.size) != 2 or any(type(x) is not int or x <= 0 for x in self.size):
            raise ValueError('Invalid calibration image size')
        self.size = tuple(self.size)

    @classmethod
    def load(cls, path):
        with open(path) as handle:
            data = json.load(handle)
        return cls(**{key: data[key] for key in cls.__dataclass_fields__})


def stereo_angle(first, second, calibration):
    normals = []
    for points, intrinsic, distortion in ((first, calibration.K1, calibration.D1),
                                           (second, calibration.K2, calibration.D2)):
        pixels = np.asarray(points, dtype=float).reshape(2, 1, 2)
        if not np.isfinite(pixels).all():
            raise ValueError('Nonfinite shaft pixels')
        normalized = cv2.undistortPoints(pixels, intrinsic, distortion).reshape(2, 2)
        rays = np.column_stack((normalized, np.ones(2)))
        normals.append(unit(np.cross(*rays)))
    direction = np.cross(normals[0], calibration.R.T @ normals[1])
    support = float(np.linalg.norm(direction))
    if support < .08:
        raise ValueError('Shaft planes nearly coincide')
    return angle_between(direction, calibration.gravity), support
