"""
Follow small movements of the board after calibration.

The board turns or shifts a little when darts are pulled out. Calibration
stores the image each camera's points were clicked on (the reference image)
together with the model at that moment. Later, with the board empty, the
current image is matched against the reference image (ORB features on the
board): the board is a plane, so in undistorted image coordinates the
movement is exactly a homography W, and the model becomes
H_reference @ inv(W). The lens parameters stay as they are.

Every check compares against the reference image, never against the last
update, so small errors don't add up. An update is only made when the match
is trustworthy (enough features, small residual) and the board has moved
noticeably.
"""

import copy
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from dart_scoring import calibration

REFERENCE_DIRNAME = "calibration_ref"  # next to calibration.json
FEATURES = 4000
FEATURE_RADIUS = 1.15   # board radii; match features on the board out to the numbers
MIN_INLIERS = 80        # fewer matching features: someone in the way, don't trust it
MAX_RESIDUAL_PX = 1.5   # median error of the matched features after the fit
RANSAC_PX = 1.5
MIN_UPDATE_PX = 1.0     # smaller movements are left alone
CHECK_RADIUS = 1.0      # movement is measured over the board out to the double ring
BOARD_EDGE = 1.33       # board radii (451 mm board): outside it nothing turns with the board
EDGE_MARGIN_PX = 4      # moved board this close to the image edge counts as unknown


def reference_dir(calibration_file: Path) -> Path:
    """Where the reference images for a calibration file are kept."""
    return Path(calibration_file).resolve().parent / REFERENCE_DIRNAME


def reference_path(directory: Path, camera_id: int) -> Path:
    return Path(directory) / f"cam{camera_id}.jpg"


def set_reference(entry: dict, camera_id: int, image: np.ndarray, directory: Path) -> None:
    """Store `image` as the view the entry's current calibration belongs to."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(reference_path(directory, camera_id)), image)
    entry["reference"] = {
        "homography": copy.deepcopy(entry["homography"]),
        "points": copy.deepcopy(entry.get("points", [])),
    }


def _board_circle(model: dict, radius: float, count: int = 181) -> np.ndarray:
    """Image pixels of a circle of `radius` board radii around the bull."""
    angles = np.linspace(0, 2 * math.pi, count)
    circle = np.column_stack([radius * np.sin(angles), -radius * np.cos(angles)])
    inverse = np.linalg.inv(np.asarray(model["homography"], dtype=np.float64))
    undistorted = cv2.perspectiveTransform(circle.reshape(-1, 1, 2), inverse).reshape(-1, 2)
    valid = np.linalg.norm(undistorted, axis=1) < calibration.max_valid_radius(model)
    return calibration.distort(model, undistorted[valid])


def board_region(model: dict, size: tuple[int, int], radius: float = FEATURE_RADIUS) -> np.ndarray:
    """Mask (255 inside) of the board out to `radius` in a camera's image."""
    width, height = size
    mask = np.zeros((height, width), np.uint8)
    pixels = _board_circle(model, radius)
    if len(pixels) >= 3:
        cv2.fillPoly(mask, [np.round(pixels).astype(np.int32)], 255)
    return mask


def _grid(radius: float = CHECK_RADIUS) -> np.ndarray:
    """Board points spread over the board, for measuring how far a model moved."""
    points = [(0.0, 0.0)]
    for r in np.linspace(0.2, radius, 5):
        for a in np.linspace(0, 2 * math.pi, 24, endpoint=False):
            points.append((r * math.sin(a), -r * math.cos(a)))
    return np.array(points)


def _pixels(model: dict, board_points: np.ndarray) -> np.ndarray:
    inverse = np.linalg.inv(np.asarray(model["homography"], dtype=np.float64))
    return calibration.distort(model, cv2.perspectiveTransform(
        board_points.reshape(-1, 1, 2).astype(np.float64), inverse).reshape(-1, 2))


def model_shift(a: dict, b: dict, size: tuple[int, int]) -> float:
    """Median pixel distance between where two models put the same board points."""
    pa, pb = _pixels(a, _grid()), _pixels(b, _grid())
    width, height = size
    inside = ((pa[:, 0] >= 0) & (pa[:, 0] < width) & (pa[:, 1] >= 0) & (pa[:, 1] < height))
    if not inside.any():
        return 0.0
    return float(np.median(np.linalg.norm(pa[inside] - pb[inside], axis=1)))


def remap(image: np.ndarray, old: dict, new: dict, step: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """
    `image`, seen with the `old` calibration, as it looks with the `new` one:
    every pixel shows the board point the new model puts there. Used to move
    the stored empty board along when the board has turned. Outside the board
    (LED ring, wall) nothing moved, so those pixels are kept. Also returns a
    mask (255) of the pixels that are known: board that came into view from
    outside the image has nothing to show.
    """
    height, width = image.shape[:2]
    # Grid points where cv2.resize puts the samples when scaling up by `step`
    us, vs = np.meshgrid((np.arange(math.ceil(width / step)) + 0.5) * step - 0.5,
                         (np.arange(math.ceil(height / step)) + 0.5) * step - 0.5)
    grid = np.column_stack([us.ravel(), vs.ravel()])
    board = cv2.perspectiveTransform(calibration.undistort(new, grid).reshape(-1, 1, 2),
                                     np.asarray(new["homography"], dtype=np.float64))
    source = calibration.distort(old, cv2.perspectiveTransform(
        board, np.linalg.inv(np.asarray(old["homography"], dtype=np.float64))).reshape(-1, 2))
    # The mapping is smooth: compute it on a coarse grid and scale it up
    def full(values):
        return cv2.resize(values.reshape(us.shape).astype(np.float32),
                          (us.shape[1] * step, us.shape[0] * step))[:height, :width]
    moved = cv2.remap(image, full(source[:, 0]), full(source[:, 1]), cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_REPLICATE)
    on_board = full(np.linalg.norm(board.reshape(-1, 2), axis=1)) <= BOARD_EDGE
    map_u, map_v = full(source[:, 0]), full(source[:, 1])
    m = EDGE_MARGIN_PX
    inside = (map_u >= m) & (map_u <= width - 1 - m) & (map_v >= m) & (map_v <= height - 1 - m)
    known = np.where(on_board & ~inside, 0, 255).astype(np.uint8)
    known[:m, :] = known[-m:, :] = 0
    known[:, :m] = known[:, -m:] = 0
    if image.ndim == 3:
        on_board = on_board[..., None]
    return np.where(on_board, moved, image), known


@dataclass
class Result:
    """Outcome of a check for one camera."""

    entry: dict | None = None    # the updated calibration entry, or None when nothing changes
    shift_px: float = 0.0        # how far the board moved against the current calibration
    inliers: int = 0
    residual_px: float = 0.0
    reason: str = ""             # why nothing was changed

    def describe(self) -> str:
        if self.reason:
            return self.reason
        return (f"flyttad {self.shift_px:.1f} px ({self.inliers} matchningar, "
                f"restfel {self.residual_px:.2f} px)")


def _gray(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def follow(entry: dict, camera_id: int, image: np.ndarray, directory: Path) -> Result:
    """
    Check one camera's empty-board image against its reference image.

    Returns a Result whose `entry` is the updated calibration entry when the
    board has moved at least MIN_UPDATE_PX and the match is trustworthy.
    """
    reference = entry.get("reference")
    path = reference_path(directory, camera_id)
    if not reference or not path.exists():
        return Result(reason="ingen referensbild")
    reference_image = cv2.imread(str(path))
    if reference_image is None or reference_image.shape[:2] != image.shape[:2]:
        return Result(reason="referensbilden går inte att läsa")
    size = (image.shape[1], image.shape[0])

    reference_model = {**entry, "homography": reference["homography"]}
    orb = cv2.ORB_create(FEATURES)
    kp_ref, des_ref = orb.detectAndCompute(_gray(reference_image), board_region(reference_model, size))
    kp_now, des_now = orb.detectAndCompute(_gray(image), board_region(entry, size))
    if des_ref is None or des_now is None:
        return Result(reason="inga detaljer att jämföra")
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(des_ref, des_now)
    if len(matches) < MIN_INLIERS:
        return Result(reason=f"för få matchningar ({len(matches)})")
    p_ref = np.float64([kp_ref[m.queryIdx].pt for m in matches])
    p_now = np.float64([kp_now[m.trainIdx].pt for m in matches])

    # In undistorted coordinates the board's movement is a homography
    u_ref = calibration.undistort(entry, p_ref)
    u_now = calibration.undistort(entry, p_now)
    scale = float(entry["camera_matrix"][0][0]) if "camera_matrix" in entry else 1.0
    movement, inliers = cv2.findHomography(u_ref, u_now, cv2.RANSAC, RANSAC_PX / scale)
    if movement is None:
        return Result(reason="ingen rörelse gick att räkna ut")
    inliers = inliers.ravel() == 1
    count = int(inliers.sum())
    predicted = calibration.distort(entry, cv2.perspectiveTransform(
        u_ref[inliers].reshape(-1, 1, 2), movement).reshape(-1, 2))
    residual = float(np.median(np.linalg.norm(predicted - p_now[inliers], axis=1)))
    if count < MIN_INLIERS:
        return Result(inliers=count, residual_px=residual,
                      reason=f"för få matchningar ({count})")
    if residual > MAX_RESIDUAL_PX:
        return Result(inliers=count, residual_px=residual,
                      reason=f"osäker matchning (restfel {residual:.1f} px)")

    homography = np.asarray(reference["homography"], dtype=np.float64) @ np.linalg.inv(movement)
    homography /= homography[2, 2]
    updated = {**entry, "homography": homography.tolist()}
    shift = model_shift(entry, updated, size)
    if shift < MIN_UPDATE_PX:
        return Result(shift_px=shift, inliers=count, residual_px=residual,
                      reason=f"oförändrad ({shift:.1f} px)")

    if reference.get("points"):
        pixels = np.array([[p["u"], p["v"]] for p in reference["points"]], dtype=np.float64)
        moved = calibration.distort(entry, cv2.perspectiveTransform(
            calibration.undistort(entry, pixels).reshape(-1, 1, 2), movement).reshape(-1, 2))
        updated["points"] = [dict(p, u=float(u), v=float(v))
                             for p, (u, v) in zip(reference["points"], moved)]
    updated["suspects"] = []
    updated["tracked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return Result(entry=updated, shift_px=shift, inliers=count, residual_px=residual)
