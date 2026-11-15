"""Tests for following the board after calibration (dart_scoring/tracking.py)."""

import math

import cv2
import numpy as np
import pytest

from dart_scoring import calibration, tracking
from test_detection import MODELS, SIZE

CAMERA = 1


def textured_view(seed=3):
    """A camera image with plenty of sharp detail to match, like the real board."""
    rng = np.random.default_rng(seed)
    image = np.full((SIZE[1], SIZE[0], 3), 110, np.uint8)
    for _ in range(1500):
        x, y = int(rng.integers(0, SIZE[0])), int(rng.integers(0, SIZE[1]))
        colour = tuple(int(c) for c in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            cv2.circle(image, (x, y), int(rng.integers(3, 14)), colour, -1)
        else:
            cv2.rectangle(image, (x, y), (x + int(rng.integers(4, 24)), y + int(rng.integers(4, 24))),
                          colour, -1)
    return cv2.GaussianBlur(image, (3, 3), 0)


BACKGROUND = {CAMERA: textured_view()}


def rotation(angle_deg, dx=0.0, dy=0.0):
    """Board-plane movement: turn around the bull by `angle_deg` and shift (board radii)."""
    a = math.radians(angle_deg)
    return np.array([[math.cos(a), -math.sin(a), dx], [math.sin(a), math.cos(a), dy], [0, 0, 1]])


def moved_view(image, model, movement):
    """
    The camera's image after the board moved by `movement` (board coordinates):
    what was at board point b before is now at movement @ b.
    """
    width, height = SIZE
    u, v = np.meshgrid(np.arange(width, dtype=np.float64), np.arange(height, dtype=np.float64))
    pixels = np.column_stack([u.ravel(), v.ravel()])
    homography = np.asarray(model["homography"], dtype=np.float64)
    board = cv2.perspectiveTransform(calibration.undistort(model, pixels).reshape(-1, 1, 2), homography)
    before = cv2.perspectiveTransform(board, np.linalg.inv(movement))
    source = calibration.distort(model, cv2.perspectiveTransform(before, np.linalg.inv(homography))
                                 .reshape(-1, 2)).astype(np.float32)
    return cv2.remap(image, source[:, 0].reshape(height, width), source[:, 1].reshape(height, width),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


@pytest.fixture
def entry(tmp_path):
    entry = dict(MODELS[CAMERA])
    tracking.set_reference(entry, CAMERA, BACKGROUND[CAMERA], tmp_path)
    return entry


def board_error_mm(model, movement):
    """Largest error of the model on the moved board, over pixels inside the double ring."""
    worst = 0.0
    for r in (0.0, 0.3, 0.6, 0.9):
        for a in np.linspace(0, 2 * math.pi, 12, endpoint=False):
            true_board = np.array([r * math.sin(a), -r * math.cos(a)])
            moved = movement @ np.append(true_board, 1)
            # Where the moved board point shows up in the image (original model)
            u, v = calibration.board_to_image(MODELS[CAMERA], *(moved[:2] / moved[2]))
            if not (0 <= u < SIZE[0] and 0 <= v < SIZE[1]):
                continue
            seen = np.array(calibration.image_to_board(model, u, v))
            worst = max(worst, np.linalg.norm(seen - true_board) * calibration.BOARD_RADIUS_MM)
    return worst


def test_board_turned_a_little_is_followed(entry, tmp_path):
    movement = rotation(1.5, 0.01, -0.005)
    image = moved_view(BACKGROUND[CAMERA], MODELS[CAMERA], movement)
    # Before the update the old calibration is clearly off
    assert board_error_mm(MODELS[CAMERA], movement) > 3
    result = tracking.follow(entry, CAMERA, image, tmp_path)
    assert result.entry is not None, result.describe()
    assert result.shift_px > tracking.MIN_UPDATE_PX
    assert board_error_mm(result.entry, movement) < 0.5
    # Lens parameters are kept
    assert result.entry["camera_matrix"] == entry["camera_matrix"]
    assert result.entry["dist_coeffs"] == entry["dist_coeffs"]


def test_followed_entry_is_not_moved_again(entry, tmp_path):
    image = moved_view(BACKGROUND[CAMERA], MODELS[CAMERA], rotation(1.5))
    first = tracking.follow(entry, CAMERA, image, tmp_path)
    assert first.entry is not None
    # Compared with the reference image again, not with the last update: no drift
    second = tracking.follow(first.entry, CAMERA, image, tmp_path)
    assert second.entry is None
    assert second.shift_px < tracking.MIN_UPDATE_PX


def test_unmoved_board_leaves_the_calibration_alone(entry, tmp_path):
    result = tracking.follow(entry, CAMERA, BACKGROUND[CAMERA].copy(), tmp_path)
    assert result.entry is None
    assert result.reason.startswith("oförändrad")


def test_nothing_to_match_keeps_the_calibration(entry, tmp_path):
    blank = np.full_like(BACKGROUND[CAMERA], 128)
    result = tracking.follow(entry, CAMERA, blank, tmp_path)
    assert result.entry is None
    assert result.reason


def test_without_a_reference_image_nothing_changes(tmp_path):
    result = tracking.follow(dict(MODELS[CAMERA]), CAMERA, BACKGROUND[CAMERA], tmp_path)
    assert result.entry is None
    assert result.reason == "ingen referensbild"


def test_reference_points_move_with_the_board(tmp_path):
    entry = dict(MODELS[CAMERA])
    board = (0.0, -0.5)
    u, v = calibration.board_to_image(entry, *board)
    entry["points"] = [{"u": u, "v": v, "ring": "test", "boundary": None}]
    tracking.set_reference(entry, CAMERA, BACKGROUND[CAMERA], tmp_path)
    movement = rotation(1.5)
    result = tracking.follow(entry, CAMERA, moved_view(BACKGROUND[CAMERA], MODELS[CAMERA], movement),
                             tmp_path)
    assert result.entry is not None
    moved = movement @ np.array([*board, 1.0])
    expected = calibration.board_to_image(MODELS[CAMERA], *moved[:2])
    point = result.entry["points"][0]
    assert math.hypot(point["u"] - expected[0], point["v"] - expected[1]) < 1.0
