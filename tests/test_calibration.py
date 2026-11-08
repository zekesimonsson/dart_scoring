"""Unit tests for dart_scoring.calibration."""
# Test names describe the behaviour under test.
# pylint: disable=missing-function-docstring

import math

import cv2

import numpy as np
import pytest

from dart_scoring import calibration
from dart_scoring.calibration import (
    RINGS, all_labels, board_to_image, boundary_label, calibrate_camera, draw_board,
    find_suspects, fit, image_to_board, reference_point,
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
    result = fit(image, board)
    assert result["rms_mm"] < 1e-3
    assert result["rms_px"] < 1e-3
    for (u, v), (x, y) in zip(image, board):
        assert image_to_board(result, u, v) == pytest.approx((x, y), abs=1e-5)
        assert board_to_image(result, x, y) == pytest.approx((u, v), abs=1e-3)


def test_fit_with_click_noise_scores_correctly():
    rng = np.random.default_rng(1)
    image, board = sample_points(LABELS)
    noisy = np.array(image) + rng.normal(0, 1.0, (len(image), 2))
    result = fit(noisy, board)
    assert 0 < result["rms_px"] < 3
    # Middle of treble 20 still maps to treble 20
    u, v = project(0, -(RINGS["triple_inner"] + RINGS["triple_outer"]) / 2)
    assert classify(*image_to_board(result, u, v)) == (20, 3)


def test_fit_needs_four_points():
    image, board = sample_points(LABELS[:3])
    with pytest.raises(ValueError):
        fit(image, board)


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
    model = fit(image, board)
    blank = np.zeros((720, 1280, 3), np.uint8)
    assert draw_board(blank, model).any()


def test_few_points_fit_plain_homography():
    image, board = sample_points(LABELS)
    assert len(LABELS) < calibration.MIN_DISTORTION_POINTS
    result = fit(image, board)
    assert "camera_matrix" not in result


def test_old_entry_with_only_homography_still_maps():
    image, board = sample_points(LABELS)
    entry = {"homography": fit(image, board)["homography"]}
    assert image_to_board(entry, *image[0]) == pytest.approx(board[0], abs=1e-5)


# A low-angle wide-angle camera with strong barrel distortion, like the real ones
CAMERA_MATRIX = np.array([[1040.0, 0, 660], [0, 1040, 370], [0, 0, 1]])
DIST_COEFFS = np.array([-0.47, 0.21, 0, 0, 0])
RVEC = np.array([1.15, 0.0, 0.0])   # tilted so the board is seen at a low angle
TVEC = np.array([0.0, 0.25, 1.9])   # in board radii


def project_lens(board_points):
    objects = np.hstack([np.asarray(board_points, float).reshape(-1, 2),
                         np.zeros((len(board_points), 1))])
    pixels, _ = cv2.projectPoints(objects, RVEC, TVEC, CAMERA_MATRIX, DIST_COEFFS)
    return pixels.reshape(-1, 2)


def lens_points(step=2):
    labels = [(r, b) for r in ("double_outer", "double_inner", "triple_outer",
                               "triple_inner", "bull_outer") for b in range(0, 20, step)]
    labels.append(("center", None))
    board = [reference_point(r, b) for r, b in labels]
    return labels, project_lens(board), board


def test_lens_setup_is_inside_image_and_distorted():
    _, image, board = lens_points()
    assert image[:, 0].min() > 0 and image[:, 0].max() < 1280
    assert image[:, 1].min() > 0 and image[:, 1].max() < 720
    # A plain homography cannot describe it
    plain = cv2.findHomography(image, np.array(board), 0)[0]
    err = np.linalg.norm(cv2.perspectiveTransform(image.reshape(-1, 1, 2), plain).reshape(-1, 2)
                         - board, axis=1) * 170
    assert err.max() > 5


def test_fit_with_distortion_maps_exactly():
    _, image, board = lens_points()
    result = fit(image, board)
    assert "camera_matrix" in result
    assert result["rms_px"] < 0.05
    assert result["rms_mm"] < 0.05
    # Points that were not used in the fit, including near the image edges
    for x, y in [(0.95, -0.2), (-0.97, 0.1), (0.3, 0.9), (0, -0.6), (-0.5, -0.5)]:
        u, v = project_lens([(x, y)])[0]
        assert image_to_board(result, u, v) == pytest.approx((x, y), abs=2e-3)
        assert board_to_image(result, x, y) == pytest.approx((u, v), abs=0.5)


def test_fit_with_distortion_and_click_noise_scores_correctly():
    rng = np.random.default_rng(2)
    _, image, board = lens_points(step=1)
    result = fit(image + rng.normal(0, 1.0, image.shape), board)
    assert result["rms_mm"] < 1.5
    for segment_angle, number in [(0, 20), (90, 6), (180, 3), (270, 11)]:
        a = math.radians(segment_angle)
        for radius, multiplier in [((RINGS["triple_inner"] + RINGS["triple_outer"]) / 2, 3),
                                   ((RINGS["double_inner"] + RINGS["double_outer"]) / 2, 2)]:
            u, v = project_lens([(radius * math.sin(a), -radius * math.cos(a))])[0]
            assert classify(*image_to_board(result, u, v)) == (number, multiplier)


def test_draw_board_with_distortion_draws_something():
    _, image, board = lens_points()
    blank = np.zeros((720, 1280, 3), np.uint8)
    assert draw_board(blank, fit(image, board)).any()


def as_points(labels, image):
    return [{"u": float(u), "v": float(v), "ring": r, "boundary": b}
            for (r, b), (u, v) in zip(labels, image)]


def test_clean_points_have_no_suspects():
    rng = np.random.default_rng(3)
    labels, image, _ = lens_points()
    assert find_suspects(as_points(labels, image + rng.normal(0, 1.5, image.shape))) == []


def test_wrong_ring_is_suspect_with_suggestion():
    labels, image, _ = lens_points()
    points = as_points(labels, image)
    # Clicked on the inner treble edge at the 20/1 wire but labelled as the inner double edge
    i = labels.index(("triple_inner", 0))
    points[i]["ring"] = "double_inner"
    j = labels.index(("double_outer", 10))
    points[j]["u"] += 90  # a stray click
    suspects = {s["index"]: s for s in find_suspects(points)}
    assert set(suspects) == {i, j}
    assert suspects[i]["suggestion"]["ring"] == "triple_inner"
    assert suspects[i]["suggestion"]["boundary"] == 0
    assert suspects[j]["error_px"] == pytest.approx(90, abs=10)


def test_calibrate_camera_reports_suspects():
    labels, image, _ = lens_points()
    points = as_points(labels, image)
    points[3]["v"] -= 120
    entry = calibrate_camera({"cameras": {}}, 3, points, (1280, 720))
    assert [s["index"] for s in entry["suspects"]] == [3]


def test_all_labels_are_valid():
    labels = all_labels()
    assert len(labels) == 6 * 20 + 1
    for ring, boundary in labels:
        reference_point(ring, boundary)
