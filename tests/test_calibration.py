"""Unit tests for dart_scoring.calibration."""
# Test names describe the behaviour under test.
# pylint: disable=missing-function-docstring

import math

import numpy as np
import pytest

from dart_scoring import calibration
from dart_scoring.calibration import (
    RINGS, board_to_image, boundary_label, calibrate_camera, draw_board,
    fit_homography, image_to_board, reference_point,
)
from dart_scoring.scoring import classify

# A plausible low-angle camera: board -> image
BOARD_TO_IMAGE = np.array([
    [420.0, 35.0, 640.0],
    [-12.0, 95.0, 330.0],
    [0.02, 0.45, 1.0],
])


def project(x, y):
    p = BOARD_TO_IMAGE @ np.array([x, y, 1.0])
    return p[0] / p[2], p[1] / p[2]


def sample_points(labels):
    board = [reference_point(r, b) for r, b in labels]
    image = [project(*p) for p in board]
    return image, board


LABELS = [("double_outer", b) for b in (0, 3, 6, 9, 12, 15)] + [
    ("triple_inner", 1), ("triple_outer", 8), ("bull_outer", 4), ("center", None)]


def test_boundary_zero_is_between_20_and_1():
    assert boundary_label(0) == "20/1"
    assert boundary_label(19) == "5/20"
    x, y = reference_point("double_outer", 0)
    assert x == pytest.approx(math.sin(math.radians(9)))
    assert y == pytest.approx(-math.cos(math.radians(9)))


def test_reference_points_lie_on_the_segment_boundary():
    # Just either side of the 20/1 wire on the triple ring
    x, y = reference_point("triple_inner", 0)
    r, a = math.hypot(x, y), math.atan2(x, -y)
    left = (r * 1.01 * math.sin(a - 0.01), -r * 1.01 * math.cos(a - 0.01))
    right = (r * 1.01 * math.sin(a + 0.01), -r * 1.01 * math.cos(a + 0.01))
    assert classify(*left) == (20, 3)
    assert classify(*right) == (1, 3)


def test_center_point():
    assert reference_point("center") == (0.0, 0.0)


@pytest.mark.parametrize("ring, boundary", [("nope", 0), ("double_outer", 20),
                                            ("double_outer", None)])
def test_invalid_reference_point(ring, boundary):
    with pytest.raises(ValueError):
        reference_point(ring, boundary)


def test_fit_recovers_exact_homography():
    image, board = sample_points(LABELS)
    result = fit_homography(image, board)
    assert result["rms_mm"] < 1e-3
    assert result["rms_px"] < 1e-3
    for (u, v), (x, y) in zip(image, board):
        assert image_to_board(result["homography"], u, v) == pytest.approx((x, y), abs=1e-5)
        assert board_to_image(result["homography"], x, y) == pytest.approx((u, v), abs=1e-3)


def test_fit_with_click_noise_scores_correctly():
    rng = np.random.default_rng(1)
    image, board = sample_points(LABELS)
    noisy = np.array(image) + rng.normal(0, 1.0, (len(image), 2))
    result = fit_homography(noisy, board)
    assert 0 < result["rms_px"] < 3
    # Middle of treble 20 still maps to treble 20
    u, v = project(0, -(RINGS["triple_inner"] + RINGS["triple_outer"]) / 2)
    assert classify(*image_to_board(result["homography"], u, v)) == (20, 3)


def test_fit_needs_four_points():
    image, board = sample_points(LABELS[:3])
    with pytest.raises(ValueError):
        fit_homography(image, board)


def test_calibrate_camera_stores_points_and_fit(tmp_path):
    image, _ = sample_points(LABELS)
    points = [{"u": u, "v": v, "ring": r, "boundary": b}
              for (u, v), (r, b) in zip(image, LABELS)]
    data = calibration.load(tmp_path / "cal.json")
    entry = calibrate_camera(data, 2, points, (1280, 720))
    assert entry["rms_px"] < 1e-3
    calibration.save(data, tmp_path / "cal.json")
    loaded = calibration.load(tmp_path / "cal.json")
    assert loaded["cameras"]["2"]["points"][-1]["ring"] == "center"
    assert np.allclose(loaded["cameras"]["2"]["homography"], entry["homography"])


def test_calibrate_camera_with_few_points_stores_only_points():
    data = {"cameras": {}}
    entry = calibrate_camera(data, 1, [{"u": 1, "v": 2, "ring": "center"}], (1280, 720))
    assert "homography" not in entry
    assert len(entry["points"]) == 1


def test_draw_board_draws_something():
    image, board = sample_points(LABELS)
    homography = fit_homography(image, board)["homography"]
    blank = np.zeros((720, 1280, 3), np.uint8)
    assert draw_board(blank, homography).any()
