"""
Per-camera calibration: a mapping from image pixels to board coordinates.

The board is a plane, so once lens distortion is removed a 3x3 homography maps
any point on it seen by a camera to the normalized board coordinates used by
`scoring` (origin at the bull, radius 1.0 = outer edge of the double ring,
negative y towards the 20). The dart tip touches the board, so its image
position maps correctly as well.

The model is fitted from reference points: places on the board with a known
position, such as where the outer edge of the double ring meets the wire
between 20 and 1. With enough points spread over the image, the camera's
radial lens distortion (k1, k2) is estimated with `cv2.calibrateCamera`; the
wide-angle lenses bend the image strongly towards its edges, so a plain
homography is off by tens of pixels there. With fewer points only the
homography is fitted, on raw pixels.

A camera's calibration entry holds "camera_matrix", "dist_coeffs" and
"homography" (undistorted normalized image coordinates -> board). Entries
without the first two (plain-homography fits) use an identity camera matrix and
no distortion, so the homography then works on pixels directly.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from dart_scoring.scoring import SEGMENTS

CALIBRATION_FILE = Path(__file__).resolve().parent.parent / "calibration.json"

BOARD_RADIUS_MM = 170

# Ring edges (where the ring wires are), as normalized radii
RINGS = {
    "double_outer": 1.0,
    "double_inner": 162 / 170,
    "triple_outer": 107 / 170,
    "triple_inner": 99 / 170,
    "bull_outer": 15.9 / 170,
    "bull_inner": 6.35 / 170,
}

MIN_POINTS = 4
# Fewer points than this give an unstable distortion estimate: fit a plain homography
MIN_DISTORTION_POINTS = 12

# A point is suspect when the model fitted without it misses it by more than
# this many pixels and by more than SUSPECT_FACTOR times the median such miss.
SUSPECT_PX = 20
SUSPECT_FACTOR = 3

_IDENTITY = np.eye(3)
_NO_DISTORTION = np.zeros(5)
_UNDISTORT_CRITERIA = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 50, 1e-9)


def boundary_label(boundary: int) -> str:
    """Name of the wire between two segments, e.g. boundary 0 -> '20/1'."""
    return f"{SEGMENTS[boundary % 20]}/{SEGMENTS[(boundary + 1) % 20]}"


def boundary_angle(boundary: int) -> float:
    """Angle in degrees, clockwise from the 20, of segment boundary wire `boundary`."""
    return 9 + 18 * (boundary % 20)


def reference_point(ring: str, boundary: int | None = None) -> tuple[float, float]:
    """
    Board coordinates of a reference point.

    ring is a key of RINGS, or "center" for the bull's center (boundary ignored).
    boundary 0-19 is the wire between SEGMENTS[boundary] and the next segment
    clockwise, so 0 is the wire between 20 and 1.
    """
    if ring == "center":
        return 0.0, 0.0
    if ring not in RINGS:
        raise ValueError(f"Okänd ring: {ring}")
    if boundary is None or not 0 <= int(boundary) < 20:
        raise ValueError(f"Ogiltig segmentgräns: {boundary}")
    radius = RINGS[ring]
    angle = math.radians(boundary_angle(int(boundary)))
    return radius * math.sin(angle), -radius * math.cos(angle)


def all_labels() -> list[tuple[str, int | None]]:
    """Every possible reference label as (ring, boundary)."""
    return [(ring, b) for ring in RINGS for b in range(20)] + [("center", None)]


def _apply(matrix: np.ndarray, points) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, matrix).reshape(-1, 2)


def _lens(model: dict) -> tuple[np.ndarray, np.ndarray]:
    if "camera_matrix" not in model:
        return _IDENTITY, _NO_DISTORTION
    return (np.asarray(model["camera_matrix"], dtype=np.float64),
            np.asarray(model["dist_coeffs"], dtype=np.float64))


def undistort(model: dict, image_points) -> np.ndarray:
    """Image pixels -> undistorted coordinates the model's homography works on."""
    pts = np.asarray(image_points, dtype=np.float64).reshape(-1, 1, 2)
    if "camera_matrix" not in model:
        return pts.reshape(-1, 2)
    matrix, dist = _lens(model)
    return cv2.undistortPointsIter(pts, matrix, dist, None, None,
                                   _UNDISTORT_CRITERIA).reshape(-1, 2)


def distort(model: dict, points) -> np.ndarray:
    """Undistorted coordinates -> image pixels (inverse of `undistort`)."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if "camera_matrix" not in model:
        return pts
    matrix, dist = _lens(model)
    rays = np.hstack([pts, np.ones((len(pts), 1))])
    pixels, _ = cv2.projectPoints(rays, np.zeros(3), np.zeros(3), matrix, dist)
    return pixels.reshape(-1, 2)


def max_valid_radius(model: dict) -> float:
    """
    Largest undistorted radius where the distortion model is still monotonic.

    Beyond it the polynomial folds back, so points far outside the field of
    view would wrongly land inside the image.
    """
    if "camera_matrix" not in model:
        return math.inf
    k1, k2 = _lens(model)[1][:2]
    # d/dr [r (1 + k1 r^2 + k2 r^4)] = 1 + 3 k1 r^2 + 5 k2 r^4, as a polynomial in r^2
    roots = [r.real for r in np.roots([5 * k2, 3 * k1, 1])
             if abs(r.imag) < 1e-12 and r.real > 0] if k2 or k1 else []
    return math.sqrt(min(roots)) * 0.95 if roots else math.inf


def fit(image_points, board_points, image_size=(1280, 720)) -> dict:
    """
    Fit a camera model mapping image pixels to board coordinates.

    With at least MIN_DISTORTION_POINTS points the radial lens distortion is
    estimated as well; otherwise a plain homography on pixels is fitted.
    Returns the model ("homography", and "camera_matrix" + "dist_coeffs" when
    distortion is modelled) plus "rms_mm", "rms_px", "residuals_mm" and
    "residuals_px" (per point, in board mm and image pixels).
    """
    image_points = np.asarray(image_points, dtype=np.float64).reshape(-1, 2)
    board_points = np.asarray(board_points, dtype=np.float64).reshape(-1, 2)
    if len(image_points) != len(board_points):
        raise ValueError("Lika många bild- och brädpunkter krävs")
    if len(image_points) < MIN_POINTS:
        raise ValueError(f"Minst {MIN_POINTS} punkter krävs")

    model = {}
    if len(image_points) >= MIN_DISTORTION_POINTS:
        width, height = image_size
        guess = np.array([[width * 0.8, 0, width / 2], [0, width * 0.8, height / 2], [0, 0, 1]])
        objects = np.hstack([board_points, np.zeros((len(board_points), 1))]).astype(np.float32)
        flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_ASPECT_RATIO
                 | cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K3)
        try:
            _, matrix, dist, _, _ = cv2.calibrateCamera(
                [objects], [image_points.astype(np.float32)], (width, height),
                guess, None, flags=flags)
        except cv2.error as error:
            raise ValueError("Kunde inte skatta linsdistorsionen") from error
        model = {"camera_matrix": matrix.tolist(),
                 "dist_coeffs": dist.ravel().tolist()}

    undistorted = undistort(model, image_points)
    matrix, _ = cv2.findHomography(undistorted, board_points, 0)
    if matrix is None:
        raise ValueError("Kunde inte beräkna homografi (ligger punkterna på en linje?)")
    model["homography"] = matrix.tolist()

    board_err = np.linalg.norm(_apply(matrix, undistorted) - board_points, axis=1)
    board_err *= BOARD_RADIUS_MM
    image_err = np.linalg.norm(
        distort(model, _apply(np.linalg.inv(matrix), board_points)) - image_points, axis=1)

    return {
        **model,
        "rms_mm": float(np.sqrt(np.mean(board_err ** 2))),
        "rms_px": float(np.sqrt(np.mean(image_err ** 2))),
        "residuals_mm": board_err.tolist(),
        "residuals_px": image_err.tolist(),
    }


def image_to_board(model: dict, u: float, v: float) -> tuple[float, float]:
    """Map an image pixel to normalized board coordinates with a camera's model."""
    point = undistort(model, [(u, v)])
    x, y = _apply(np.asarray(model["homography"], dtype=np.float64), point)[0]
    return float(x), float(y)


def board_to_image(model: dict, x: float, y: float) -> tuple[float, float]:
    """Map normalized board coordinates to an image pixel with a camera's model."""
    inverse = np.linalg.inv(np.asarray(model["homography"], dtype=np.float64))
    u, v = distort(model, _apply(inverse, [(x, y)]))[0]
    return float(u), float(v)


def draw_board(image: np.ndarray, model: dict, color=(0, 255, 255), thickness=1) -> np.ndarray:
    """Return a copy of `image` with the board's rings and segment wires drawn on it."""
    out = image.copy()
    inverse = np.linalg.inv(np.asarray(model["homography"], dtype=np.float64))
    limit = max_valid_radius(model)

    def polyline(board_pts, closed):
        undistorted = _apply(inverse, board_pts)
        # Undistorted coordinates are centred on the optical axis
        valid = np.linalg.norm(undistorted, axis=1) < limit
        pixels = np.round(distort(model, undistorted)).astype(np.int32)
        # Draw each run of valid points; the lens model is meaningless outside them
        runs, run = [], []
        for ok, pixel in zip(valid, pixels):
            if ok:
                run.append(pixel)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        closed = closed and len(runs) == 1 and len(runs[0]) == len(pixels)
        for run in runs:
            if len(run) >= 2:
                cv2.polylines(out, [np.array(run).reshape(-1, 1, 2)], closed, color,
                              thickness, cv2.LINE_AA)

    angles = np.linspace(0, 2 * math.pi, 361)
    for radius in RINGS.values():
        polyline(np.column_stack([radius * np.sin(angles), -radius * np.cos(angles)]), True)
    steps = np.linspace(RINGS["bull_outer"], RINGS["double_outer"], 40)[:, None]
    for boundary in range(20):
        a = math.radians(boundary_angle(boundary))
        polyline(steps * np.array([math.sin(a), -math.cos(a)]), False)
    return out


def find_suspects(points: list[dict], image_size=(1280, 720)) -> list[dict]:
    """
    Points that are probably clicked in the wrong place or given the wrong label.

    Each point is first checked against a model fitted without it, so it
    cannot pull the fit towards itself. A few gross errors still drag every
    such model, so the candidates found are checked again against one model
    fitted without any of them. Returns a list of {"index", "error_px",
    "suggestion"}, where error_px is how far the click is from where the model
    puts its label and suggestion is the label the model puts closest to the
    click ({"ring", "boundary", "error_px"}), or None when none fits clearly
    better.
    """
    if len(points) <= MIN_POINTS:
        return []
    image = np.array([(p["u"], p["v"]) for p in points], dtype=np.float64)
    board = np.array([reference_point(p["ring"], p["boundary"]) for p in points])

    def miss(model, i):
        u, v = board_to_image(model, *board[i])
        return math.hypot(u - image[i, 0], v - image[i, 1])

    misses = []
    for i in range(len(points)):
        keep = np.arange(len(points)) != i
        try:
            misses.append(miss(fit(image[keep], board[keep], image_size), i))
        except ValueError:
            misses.append(0.0)
    threshold = max(SUSPECT_PX, SUSPECT_FACTOR * float(np.median(misses)))
    candidates = [i for i, m in enumerate(misses) if m > threshold]
    if not candidates:
        return []

    keep = np.ones(len(points), bool)
    keep[candidates] = False
    try:
        clean = fit(image[keep], board[keep], image_size)
    except ValueError:  # too few points left to tell
        return []
    inverse = np.linalg.inv(np.asarray(clean["homography"]))
    labels = all_labels()
    predicted = distort(clean, _apply(inverse, [reference_point(r, b) for r, b in labels]))

    suspects = []
    for i in candidates:
        error = miss(clean, i)
        if error <= threshold:
            continue
        distances = np.linalg.norm(predicted - image[i], axis=1)
        best = int(np.argmin(distances))
        suggestion = None
        if distances[best] < min(threshold, error / 3):
            ring, boundary = labels[best]
            suggestion = {"ring": ring, "boundary": boundary,
                          "error_px": float(distances[best])}
        suspects.append({"index": i, "error_px": error, "suggestion": suggestion})
    return suspects


def load(path: Path = CALIBRATION_FILE) -> dict:
    """Load the calibration file; a missing file gives an empty calibration."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "cameras": {}}
    data.setdefault("cameras", {})
    return data


def save(data: dict, path: Path = CALIBRATION_FILE) -> None:
    """Write the calibration file atomically."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def calibrate_camera(data: dict, camera_id: int, points: list[dict],
                     image_size: tuple[int, int]) -> dict:
    """
    Store `points` for one camera in `data` and fit its model.

    Each point is {"u", "v", "ring", "boundary"} (image pixel + reference label).
    With fewer than MIN_POINTS points only the points are stored. Returns the
    camera's entry, including "suspects" (see find_suspects).
    """
    clean = []
    board = []
    for p in points:
        ring = str(p["ring"])
        boundary = None if ring == "center" else int(p["boundary"])
        board.append(reference_point(ring, boundary))
        clean.append({"u": float(p["u"]), "v": float(p["v"]),
                      "ring": ring, "boundary": boundary})

    entry = {
        "points": clean,
        "image_size": list(image_size),
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if len(clean) >= MIN_POINTS:
        entry.update(fit([(p["u"], p["v"]) for p in clean], board, image_size))
        entry["suspects"] = find_suspects(clean, image_size)
    data["cameras"][str(camera_id)] = entry
    return entry
