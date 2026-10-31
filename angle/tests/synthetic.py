"""Known cameras and independently rendered board polygons for calibration recovery."""
import cv2
import numpy as np


def board_pairs(count=16):
    rng = np.random.default_rng(81)
    k = np.array([[900., 0, 480], [0, 910, 360], [0, 0, 1]])
    r = cv2.Rodrigues(np.array([.025, -.12, .015]))[0]
    t = np.array([-105., 4., 8.])
    poses = [(np.zeros(3), np.array([-95., -65., 750.]))]
    poses += [(rng.uniform([-.48, -.48, -.15], [.48, .48, .15]),
               rng.uniform([-100, -80, 630], [-20, -15, 900])) for _ in range(count-1)]
    pairs = []
    for index, (rv, tv) in enumerate(poses):
        rotation = cv2.Rodrigues(rv)[0]
        images = []
        for camera_r, camera_t in ((np.eye(3), np.zeros(3)), (r, t)):
            image = np.full((1440, 1920), 210, np.uint8)
            for row in range(-1, 6):
                for col in range(-1, 9):
                    points = np.array([[col, row, 0], [col+1, row, 0], [col+1, row+1, 0], [col, row+1, 0]], float)*25
                    points = (camera_r @ (rotation @ points.T + tv[:, None]) + camera_t[:, None]).T
                    pixels = (k @ points.T).T
                    polygon = np.rint(2*pixels[:, :2]/pixels[:, 2:]).astype(np.int32)
                    cv2.fillConvexPoly(image, polygon, 0 if (col+row) % 2 == 0 else 255)
            images.append(cv2.cvtColor(cv2.resize(image, (960, 720), interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR))
        pairs.append((f'{index:04d}.png', *images))
    return pairs, k, r, t
