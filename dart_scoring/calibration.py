"""
Per-camera calibration: a homography from image pixels to board coordinates.

The board is a plane, so a 3x3 homography maps any point on it seen by a camera
to the normalized board coordinates used by `scoring` (origin at the bull,
radius 1.0 = outer edge of the double ring, negative y towards the 20). The
dart tip touches the board, so its image position maps correctly as well.

A homography is fitted from reference points: places on the board with a known
position, such as where the outer edge of the double ring meets the wire
between 20 and 1. Lens distortion is not modelled; the fit residual shows how
much that matters.
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


def _apply(matrix: np.ndarray, points) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, matrix).reshape(-1, 2)


def fit_homography(image_points, board_points) -> dict:
    """
    Least-squares homography from image pixels to board coordinates.

    Returns {"homography": 3x3 list, "rms_mm", "rms_px", "residuals_mm",
    "residuals_px"}; residuals are per point, in board mm and image pixels.
    """
    image_points = np.asarray(image_points, dtype=np.float64).reshape(-1, 2)
    board_points = np.asarray(board_points, dtype=np.float64).reshape(-1, 2)
    if len(image_points) != len(board_points):
        raise ValueError("Lika många bild- och brädpunkter krävs")
    if len(image_points) < MIN_POINTS:
        raise ValueError(f"Minst {MIN_POINTS} punkter krävs")

    matrix, _ = cv2.findHomography(image_points, board_points, 0)
    if matrix is None:
        raise ValueError("Kunde inte beräkna homografi (ligger punkterna på en linje?)")

    board_err = np.linalg.norm(_apply(matrix, image_points) - board_points, axis=1)
    board_err *= BOARD_RADIUS_MM
    image_err = np.linalg.norm(
        _apply(np.linalg.inv(matrix), board_points) - image_points, axis=1)

    return {
        "homography": matrix.tolist(),
        "rms_mm": float(np.sqrt(np.mean(board_err ** 2))),
        "rms_px": float(np.sqrt(np.mean(image_err ** 2))),
        "residuals_mm": board_err.tolist(),
        "residuals_px": image_err.tolist(),
    }


def image_to_board(homography, u: float, v: float) -> tuple[float, float]:
    """Map an image pixel to normalized board coordinates."""
    x, y = _apply(np.asarray(homography, dtype=np.float64), [(u, v)])[0]
    return float(x), float(y)


def board_to_image(homography, x: float, y: float) -> tuple[float, float]:
    """Map normalized board coordinates to an image pixel."""
    inverse = np.linalg.inv(np.asarray(homography, dtype=np.float64))
    u, v = _apply(inverse, [(x, y)])[0]
    return float(u), float(v)


def draw_board(image: np.ndarray, homography, color=(0, 255, 255), thickness=1) -> np.ndarray:
    """Return a copy of `image` with the board's rings and segment wires drawn on it."""
    out = image.copy()
    inverse = np.linalg.inv(np.asarray(homography, dtype=np.float64))

    def polyline(board_pts, closed):
        pts = _apply(inverse, board_pts)
        pts = np.round(pts).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(out, [pts], closed, color, thickness, cv2.LINE_AA)

    angles = np.linspace(0, 2 * math.pi, 181)
    for radius in RINGS.values():
        polyline(np.column_stack([radius * np.sin(angles), -radius * np.cos(angles)]), True)
    for boundary in range(20):
        a = math.radians(boundary_angle(boundary))
        direction = np.array([math.sin(a), -math.cos(a)])
        polyline([direction * RINGS["bull_outer"], direction * RINGS["double_outer"]], False)
    return out


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
    Store `points` for one camera in `data` and fit its homography.

    Each point is {"u", "v", "ring", "boundary"} (image pixel + reference label).
    With fewer than MIN_POINTS points only the points are stored. Returns the
    camera's entry.
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
        entry.update(fit_homography([(p["u"], p["v"]) for p in clean], board))
    data["cameras"][str(camera_id)] = entry
    return entry
