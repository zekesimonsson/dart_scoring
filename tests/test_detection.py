"""Unit tests for dart_scoring.detection, on synthetic camera images."""
# Test names describe the behaviour under test.
# pylint: disable=missing-function-docstring

import math

import cv2
import numpy as np
import pytest

from dart_scoring import calibration, detection
from dart_scoring.calibration import RINGS, all_labels, reference_point
from dart_scoring.detection import (
    BoardLine, DartView, Detector, board_mask, dart_axis, find_dart, intersect, locate, orient,
)
from dart_scoring.scoring import classify

SIZE = (1280, 720)
CAMERA_MATRIX = np.array([[1040.0, 0, 640], [0, 1040, 360], [0, 0, 1]])
DIST_COEFFS = np.array([-0.47, 0.21, 0, 0, 0])
DART_LENGTH = 0.65   # board radii (about 110 mm)


def look_at(angle_deg, distance=1.75, height=0.55):
    """Camera pose (rvec, tvec) looking at the bull from `angle_deg` around the board."""
    a = math.radians(angle_deg)
    center = np.array([distance * math.sin(a), -distance * math.cos(a), height])
    forward = -center / np.linalg.norm(center)
    right = np.cross(forward, [0, 0, 1])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.array([right, down, forward])
    return cv2.Rodrigues(rotation)[0].ravel(), -rotation @ center


POSES = {1: look_at(180), 2: look_at(60), 3: look_at(300)}


def project(camera, points3d):
    rvec, tvec = POSES[camera]
    pixels, _ = cv2.projectPoints(np.asarray(points3d, float).reshape(-1, 3), rvec, tvec,
                                  CAMERA_MATRIX, DIST_COEFFS)
    return pixels.reshape(-1, 2)


def fitted_models():
    labels = all_labels()
    board = np.array([reference_point(r, b) for r, b in labels])
    models = {}
    for camera in POSES:
        image = project(camera, np.hstack([board, np.zeros((len(board), 1))]))
        inside = ((image[:, 0] > 0) & (image[:, 0] < SIZE[0])
                  & (image[:, 1] > 0) & (image[:, 1] < SIZE[1]))
        models[camera] = calibration.fit(image[inside], board[inside], SIZE)
    return models


MODELS = fitted_models()
RNG = np.random.default_rng(5)
BACKGROUND = {c: cv2.GaussianBlur(RNG.integers(90, 170, (SIZE[1], SIZE[0], 3), dtype=np.uint8),
                                  (9, 9), 0) for c in POSES}


def dart_points(x, y, lean=(0.0, 0.0), start=0.0):
    """3D points along a dart whose tip enters the board at (x, y)."""
    direction = np.array([lean[0], lean[1], 1.0])
    direction /= np.linalg.norm(direction)
    s = np.linspace(start, DART_LENGTH, 40)[:, None]
    return np.array([x, y, 0.0]) + s * direction


def render(darts, noise=None):
    """Frames from every camera with the given darts [(x, y, lean, start)] in the board."""
    frames = {}
    for camera in POSES:
        image = BACKGROUND[camera].copy()
        for x, y, lean, start in darts:
            pixels = np.round(project(camera, dart_points(x, y, lean, start))).astype(np.int32)
            body = pixels[: int(len(pixels) * 0.7)]
            flight = pixels[int(len(pixels) * 0.7):]
            cv2.polylines(image, [body.reshape(-1, 1, 2)], False, (25, 25, 25), 5, cv2.LINE_AA)
            cv2.polylines(image, [flight.reshape(-1, 1, 2)], False, (20, 40, 200), 16, cv2.LINE_AA)
        if noise is not None:
            image = cv2.add(image, noise.integers(0, 4, image.shape, dtype=np.uint8))
        frames[camera] = image
    return frames


def settle(detector, frames, now):
    """Feed frames as if they appeared after motion and then stayed still."""
    event = None
    for i in range(detection.STILL_FRAMES + 1):
        event = detector.feed(frames, now + 0.1 * i) or event
    return event


def new_detector():
    detector = Detector(MODELS, SIZE)
    detector.reset(render([]), 0.0)
    return detector


def error_mm(event, x, y):
    return math.hypot(event.x - x, event.y - y) * calibration.BOARD_RADIUS_MM


def test_synthetic_cameras_see_the_board():
    for camera, model in MODELS.items():
        assert model["rms_px"] < 0.1
        u, v = calibration.board_to_image(model, 0, 0)
        assert 0 < u < SIZE[0] and 0 < v < SIZE[1]
        mask = board_mask(model, SIZE)
        assert mask[round(v), round(u)] == 255
        # The mask reaches above the far edge of the board, where darts stick out
        edge = np.array([calibration.board_to_image(model, math.sin(a), -math.cos(a))
                         for a in np.linspace(0, 2 * math.pi, 72)])
        u_far, v_far = edge[np.argmin(edge[:, 1])]
        assert mask[round(v_far) - 100, round(u_far)] == 255
        assert mask[5, 5] == 0 or mask[715, 5] == 0


def test_find_dart_on_a_drawn_line():
    changed = np.zeros((720, 1280), np.uint8)
    cv2.line(changed, (600, 200), (640, 500), 255, 7)
    view = find_dart(changed)
    assert view.tip == pytest.approx((640, 500), abs=4)
    assert view.top == pytest.approx((600, 200), abs=4)


def test_find_dart_ignores_a_round_blob():
    changed = np.zeros((720, 1280), np.uint8)
    cv2.circle(changed, (600, 300), 40, 255, -1)
    assert find_dart(changed) is None


def test_find_dart_ignores_specks_away_from_the_dart():
    changed = np.zeros((720, 1280), np.uint8)
    cv2.line(changed, (600, 200), (640, 500), 255, 7)
    cv2.circle(changed, (1000, 650), 8, 255, -1)
    assert find_dart(changed).tip == pytest.approx((640, 500), abs=4)


def wired_board():
    """Grey image with sharp bright wires, like the board's ring and segment wires."""
    image = np.full((720, 1280), 40, np.uint8)
    for y in range(100, 720, 90):
        cv2.line(image, (0, y), (1279, y + 40), 230, 3)
    for x in range(80, 1280, 150):
        cv2.line(image, (x, 0), (x + 60, 719), 230, 3)
    return detection.to_gray(image)


def shifted(image, dx, dy):
    """The image moved by (dx, dy) pixels, repeating the edge."""
    move = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(image, move, image.shape[::-1], borderMode=cv2.BORDER_REPLICATE)


def test_change_mask_ignores_a_small_camera_shake():
    before = wired_board()
    after = shifted(before, -1, 2)
    mask = np.full(before.shape, 255, np.uint8)
    assert cv2.countNonZero(detection.change_mask(before, after, mask)) == 0


def test_dart_tip_is_found_despite_camera_shake():
    before = wired_board()
    after = shifted(before, 2, 1)
    cv2.line(after, (600, 200), (640, 500), 140, 7)
    mask = np.full(before.shape, 255, np.uint8)
    view = find_dart(detection.change_mask(before, after, mask))
    assert view.tip == pytest.approx((640, 500), abs=8)


def test_find_dart_keeps_the_thin_point_of_a_dart_close_to_the_camera():
    changed = np.zeros((720, 1280), np.uint8)
    cv2.line(changed, (600, 50), (610, 480), 255, 60)   # big barrel and flight
    cv2.line(changed, (610, 480), (612, 560), 255, 6)   # thin steel point
    assert find_dart(changed).tip == pytest.approx((612, 560), abs=4)


def test_the_sharpest_camera_decides_along_parallel_lines():
    sharp = BoardLine(1, np.array([0.0, -0.98]), np.array([0.0, 1.0]), mm_per_px=0.15)
    coarse = BoardLine(2, np.array([0.0, -1.02]), np.array([0.0, -1.0]), mm_per_px=2.0)
    point, _ = intersect([sharp, coarse], detection.TIP_WEIGHT)
    assert point[1] == pytest.approx(-0.98, abs=0.002)


def line_through(camera, point, angle_deg):
    a = math.radians(angle_deg)
    return BoardLine(camera, np.array(point, float), np.array([math.cos(a), math.sin(a)]))


def test_intersect_three_lines():
    target = (0.2, -0.3)
    lines = [line_through(c, target, a) for c, a in [(1, 10), (2, 70), (3, 130)]]
    point, distances = intersect(lines)
    assert point == pytest.approx(target, abs=1e-9)
    assert distances.max() < 1e-9


def test_locate_drops_a_line_that_misses():
    target = np.array([0.2, -0.3])
    lines = [line_through(1, target, 10), line_through(2, target, 70),
             line_through(3, target + [0.2, 0], 130)]
    point, method = locate(lines)
    assert point == pytest.approx(target, abs=1e-9)
    assert method == "skärning 1+2"


def test_locate_falls_back_to_tips_for_parallel_lines():
    lines = [line_through(1, (0.1, 0.1), 0), line_through(2, (0.12, 0.1), 3)]
    point, method = locate(lines)
    assert point == pytest.approx((0.11, 0.1))
    assert method.startswith("spets")


@pytest.mark.parametrize("x, y", [
    (0.0, -(RINGS["triple_inner"] + RINGS["triple_outer"]) / 2),   # treble 20
    (0.0, 0.0),                                                     # bullseye
    (-0.68, 0.45),                                                  # single 16
    (0.97 * math.sin(math.radians(54)), -0.97 * math.cos(math.radians(54))),  # double 13
    (0.3, 0.8),
])
def test_detects_a_dart(x, y):
    detector = new_detector()
    event = settle(detector, render([(x, y, (0.0, 0.0), 0.0)]), 1.0)
    assert event.kind == "dart"
    assert error_mm(event, x, y) < 2.5
    assert (event.segment, event.multiplier) == classify(x, y)
    assert event.method.startswith("skärning")


def test_leaning_dart_with_hidden_point():
    # The first 15 mm of the dart is not visible; the lines still meet at the tip
    x, y = 0.25, -0.45
    detector = new_detector()
    event = settle(detector, render([(x, y, (0.25, -0.2), 0.09)]), 1.0)
    assert event.kind == "dart"
    assert error_mm(event, x, y) < 3


def test_second_and_third_dart_are_found_on_their_own():
    darts = [(0.0, -0.6, (0, 0), 0.0), (0.05, -0.62, (0.1, 0), 0.0), (-0.4, 0.3, (0, 0), 0.0)]
    detector = new_detector()
    for n in range(1, 4):
        event = settle(detector, render(darts[:n]), n)
        x, y = darts[n - 1][:2]
        assert event.kind == "dart"
        assert error_mm(event, x, y) < 3.5, n


def test_takeout_after_darts():
    detector = new_detector()
    settle(detector, render([(0.1, 0.2, (0, 0), 0.0)]), 1.0)
    settle(detector, render([(0.1, 0.2, (0, 0), 0.0), (-0.3, 0.1, (0, 0), 0.0)]), 2.0)
    event = settle(detector, render([]), 3.0)
    assert event.kind == "takeout"
    assert detector.darts == 0
    # And the next dart is detected against the empty board again
    event = settle(detector, render([(0.5, 0.0, (0, 0), 0.0)]), 4.0)
    assert event.kind == "dart"
    assert error_mm(event, 0.5, 0.0) < 2.5


def test_pulling_out_one_dart_is_not_a_new_dart():
    darts = [(0.1, 0.2, (0, 0), 0.0), (-0.3, 0.1, (0, 0), 0.0)]
    detector = new_detector()
    settle(detector, render(darts[:1]), 1.0)
    settle(detector, render(darts), 2.0)
    event = settle(detector, render(darts[1:]), 3.0)
    assert event.kind == "removed"
    assert detector.darts == 1


def test_no_event_without_motion():
    detector = new_detector()
    frames = render([])
    for i in range(10):
        assert detector.feed(frames, 1 + i * 0.1) is None


def test_sensor_noise_is_not_a_dart():
    detector = new_detector()
    rng = np.random.default_rng(8)
    events = [detector.feed(render([], rng), 1 + i * 0.1) for i in range(10)]
    assert all(e is None or e.kind == "none" for e in events)


def test_dart_axis_matches_the_true_camera():
    for camera, model in MODELS.items():
        for x, y in [(0, 0), (0.6, -0.3), (-0.8, 0.4)]:
            base, top = project(camera, [[x, y, 0], [x, y, 0.2]])
            expected = (top - base) / np.linalg.norm(top - base)
            assert dart_axis(model, x, y) == pytest.approx(expected, abs=0.02)


def test_orient_rejects_a_line_across_the_board():
    model = MODELS[1]
    u, v = calibration.board_to_image(model, 0.2, 0.1)
    assert orient(model, DartView((u, v), (u + 200, v - 10), 500, 10)) is None


def test_orient_puts_the_tip_in_the_board():
    model = MODELS[2]
    base, top = project(2, [[0.3, 0.2, 0], [0.3, 0.2, 0.5]])
    swapped = DartView(tuple(top), tuple(base), 500, 10)
    view = orient(model, swapped)
    assert view.tip == pytest.approx(tuple(base))


def test_tip_weight_settles_nearly_parallel_lines():
    # Two lines 6 degrees apart, one with a 0.3 mm sideways error: the plain
    # intersection slides about 3 mm along them
    target = np.array([0.3, 0.1])
    a = BoardLine(1, target + [0.01, 0.0], np.array([1.0, 0.0]))
    b = BoardLine(2, target + [0.0, 0.002], np.array([math.cos(0.1), math.sin(0.1)]))
    unweighted, _ = intersect([a, b])
    weighted, _ = intersect([a, b], detection.TIP_WEIGHT)
    assert np.linalg.norm(weighted - target) < np.linalg.norm(unweighted - target)
    assert np.linalg.norm(weighted - target) < 0.02


def test_dart_off_the_board_is_unclear():
    detector = new_detector()
    event = settle(detector, render([(1.5, 0.2, (0, 0), 0.0)]), 1.0)
    assert event.kind == "unclear"
    assert detector.darts == 0


def test_idle_refresh_follows_the_empty_board():
    detector = new_detector()
    brighter = {c: cv2.add(f, np.full(f.shape, 30, np.uint8)) for c, f in render([]).items()}
    detector._refresh(brighter, 100.0)  # pylint: disable=protected-access
    assert detector.empty is detector.reference
