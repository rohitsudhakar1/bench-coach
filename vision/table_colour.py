"""Saved table lighting/colour calibration and conservative band association."""
from itertools import permutations
import math

import cv2
import numpy as np

TUBES = ('tube A', 'tube B', 'tube C')


def lighting(frame):
    # A mostly neutral bench is required; medians resist small coloured objects.
    med = np.median(frame.reshape(-1, 3), axis=0)
    return np.clip(100 / np.maximum(med, 1), .5, 6).tolist()


def normalize(frame, gains):
    return np.clip(frame.astype(np.float32) * np.asarray(gains), 0, 255).astype(np.uint8)


def distance(h, target):
    d = np.abs(np.asarray(h, dtype=float) - target)
    return np.minimum(d, 180 - d)


def blobs(mask, min_area=8):
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    out = []
    for contour in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
        area = cv2.contourArea(contour)
        if area >= min_area:
            (x, y), r = cv2.minEnclosingCircle(contour)
            out.append((x, y, r, area))
    return sorted(out, key=lambda b: b[3], reverse=True)


def propose_caps(frame):
    hsv = cv2.cvtColor(normalize(frame, lighting(frame)), cv2.COLOR_BGR2HSV)
    candidates = []
    for x, y, r, area in blobs((hsv[:, :, 1] > 45) & (hsv[:, :, 2] > 45), 30):
        yy, xx = np.ogrid[:frame.shape[0], :frame.shape[1]]
        pixels = hsv[((xx-x)**2 + (yy-y)**2 < (.7*r)**2) & (hsv[:, :, 1] > 45)]
        if len(pixels):
            hues = pixels[:, 0]
            hue = circular_hue(hues)
            candidates.append(([x, y, max(12., 1.65*r)], hue))
    # Assign all three together: a shifted red may also fall in yellow's range.
    # Near-tied assignments are unsafe; leave those names for manual clicks.
    if len(candidates) > 12:
        return {}
    assignments = []
    for chosen in permutations(range(len(candidates)), 3):
        errors = [float(distance(candidates[i][1], h)) for i, h in zip(chosen, (0, 120, 30))]
        if max(errors) <= 18:
            assignments.append((sum(errors), chosen))
    if not assignments:
        return {}
    assignments.sort()
    best_cost, best = assignments[0]
    close = [indices for cost, indices in assignments if cost <= best_cost+3]
    return {name: candidates[best[j]][0] for j, name in enumerate(TUBES)
            if all(indices[j] == best[j] for indices in close)}


def circular_hue(hues):
    angles = np.asarray(hues) * (2*np.pi/180)
    return float((np.arctan2(np.sin(angles).mean(), np.cos(angles).mean()) * 180/(2*np.pi)) % 180)


def learn(frame, regions):
    gains = lighting(frame)
    hsv = cv2.cvtColor(normalize(frame, gains), cv2.COLOR_BGR2HSV)
    yy, xx = np.ogrid[:frame.shape[0], :frame.shape[1]]
    colours = {}
    for name in TUBES:
        x, y, r = regions[name]
        pixels = hsv[((xx-x)**2+(yy-y)**2 < (.6*r)**2) & (hsv[:, :, 1] >= 40)]
        if len(pixels) < 20:
            raise ValueError(f'{name}: too little cap colour; close cap and improve lighting')
        colours[name] = dict(hue=circular_hue(pixels[:, 0]),
                             saturation=float(np.median(pixels[:, 1])),
                             value=float(np.median(pixels[:, 2])))
    # Static magenta objects visible at setup are excluded during the run.
    static = [b for b in blobs(band_mask(hsv), 8)
              if not any(math.dist(b[:2], regions[n][:2]) < regions[n][2] for n in TUBES)]
    return dict(gains=gains, colours=colours, cap_regions={n: regions[n] for n in TUBES}, static_bands=[list(b[:3]) for b in static])


def cap_blob(frame, name, config):
    cal = config['colour_calibration']
    hsv = cv2.cvtColor(normalize(frame, cal['gains']), cv2.COLOR_BGR2HSV)
    colour = cal['colours'][name]
    x, y, r = config['regions'][name]
    yy, xx = np.ogrid[:frame.shape[0], :frame.shape[1]]
    mask = ((xx-x)**2+(yy-y)**2 < r*r) & (distance(hsv[:, :, 0], colour['hue']) <= 12)
    mask &= (hsv[:, :, 1] >= max(35, colour['saturation']*.4)) & (hsv[:, :, 2] >= 40)
    found = blobs(mask, 20)
    return found[0][:3] if found else None


def quality(frame, config):
    """Readiness heuristic, not a probability of correct events."""
    if list(config['size']) != [frame.shape[1], frame.shape[0]]:
        raise ValueError('table resolution changed')
    cal = config.get('colour_calibration')
    if not cal:
        return 0, ['rerun setup to save lighting and cap colours']
    issues = []
    visible = sum(cap_blob(frame, name, config) is not None for name in TUBES)
    if visible < 3:
        issues.append('close all caps in their saved positions')
    level = float(np.median(frame))
    exposure = min(1., level/45, (255-level)/25)
    if exposure < .7:
        issues.append('improve exposure')
    hues = [cal['colours'][n]['hue'] for n in TUBES]
    separation = min(float(distance(a, b)) for a, b in permutations(hues, 2))
    if separation < 20:
        issues.append('cap colours are too similar; verify tube names manually')
    score = round(100 * min(visible/3, exposure, min(1., separation/20)))
    return score, issues


def band_mask(hsv):
    return (distance(hsv[:, :, 0], 152) <= 18) & (hsv[:, :, 1] >= 55) & (hsv[:, :, 2] >= 45)


class BandTracker:
    def __init__(self, calibration=None):
        self.calibration = calibration or {}
        self.point = self.stamp = None
        self.velocity = np.zeros(2)

    def update(self, frame, t):
        gains = self.calibration.get('gains', [1, 1, 1])
        hsv = cv2.cvtColor(normalize(frame, gains), cv2.COLOR_BGR2HSV)
        mask = band_mask(hsv)
        yy, xx = np.ogrid[:frame.shape[0], :frame.shape[1]]
        for name, (x, y, r) in self.calibration.get('cap_regions', {}).items():
            cap_hue = self.calibration['colours'][name]['hue']
            mask &= ~(((xx-x)**2+(yy-y)**2 < r*r) & (distance(hsv[:, :, 0], cap_hue) < 6))
        for x, y, r in self.calibration.get('static_bands', []):
            mask &= (xx-x)**2+(yy-y)**2 > (r+5)**2
        found = blobs(mask)
        if not found:
            return None  # Never emit a predicted point through an occlusion.
        dt = t-self.stamp if self.stamp is not None else math.inf
        if self.point is not None and 0 < dt <= .5:
            prediction = self.point + self.velocity * min(dt, .1)
            best = min(found, key=lambda b: np.linalg.norm(np.asarray(b[:2])-prediction))
            # A small prior resolves ambiguity; a sole band may move quickly between frames.
            if len(found) > 1 and np.linalg.norm(np.asarray(best[:2])-prediction) > 60:
                return None
        elif len(found) != 1:
            return None  # Ambiguous acquisition requires clearing the view.
        else:
            best = found[0]
        point = np.asarray(best[:2])
        self.velocity = np.clip((point-self.point)/dt, -300, 300) if self.point is not None and 0 < dt <= .5 else np.zeros(2)
        self.point, self.stamp = point, t
        return tuple(point)
