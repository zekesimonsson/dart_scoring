"""
Dart detection from the three calibrated cameras.

The detector keeps a reference frame per camera of the board as it looked
after the last event. Each new set of frames is first checked for motion on
small, downscaled images. When something has moved and the view has been
still for a few frames again, the full-resolution frames are compared with the
reference:

* the difference in each camera should be a single elongated blob, the new
  dart. A line is fitted along it (in undistorted image coordinates);
* the image line of a dart, mapped onto the board plane with the calibration,
  is a line through the point where the dart enters the board (the tip lies
  on the board and on the dart). Intersecting these lines from two or three
  cameras gives the tip even when the thin steel point itself is hard to see
  or hidden. With a single camera, the lower end of the blob is used;
* when the frames look like the empty board again, the darts were taken out.

Coordinates on the board are the normalized ones used by `scoring`.
"""

import logging
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from dart_scoring import calibration
from dart_scoring.scoring import classify

log = logging.getLogger(__name__)

# Motion check on downscaled images
SMALL_SIZE = (320, 180)
MOTION_PIXEL = 15         # grey-level change counting as motion
MOTION_FRACTION = 0.002   # of the board area in a camera
STILL_FRAMES = 3          # still frames in a row before evaluating

# Change against the reference at full resolution
CHANGE_PIXEL = 25
SHIFT_PX = 2              # tolerated image shift (a camera shaking on impact)
MIN_COMPONENT = 25        # ignore smaller specks
MIN_DART_PIXELS = 150     # less than this in every camera: nothing happened
MAX_DART_FRACTION = 0.05  # more than this of the frame: not a single dart
MIN_ELONGATION = 2.5      # length / width of a dart blob
REFINE_PX = 30            # keep blob pixels this close to the first line fit
TIP_PERCENTILE = 99.5     # robust end of the blob along the line
TIP_TRIM_PIXELS = 15      # ...but never trim more pixels than this off an end

# Region where a dart can appear: the board out to this radius, plus the
# image area above it where the shaft and flight stick out
MASK_RADIUS = 1.2
MASK_UP_PX = 320

# Combining cameras
LINE_TOLERANCE_MM = 8     # a line further than this from the intersection is dropped
MIN_LINE_ANGLE = 12       # degrees; flatter pairs of lines give no reliable intersection
MAX_TIP_OFFSET_MM = 40    # an intersection this far from the visible dart ends is wrong
# Weight of the visible lower dart ends next to the lines. Small, so it only
# matters along nearly parallel lines, where the intersection is poorly defined.
TIP_WEIGHT = 0.02
# A camera's tip pulls harder the finer its view of the board is there: the
# weight is scaled by (TIP_REFERENCE_MM_PER_PX / its mm per pixel at the tip)^2,
# so a camera right next to the dart decides where along the lines it is.
TIP_REFERENCE_MM_PER_PX = 0.5
MAX_RADIUS = 1.35         # the edge of the board (451 mm); a dart further out can't stick

# A dart sticks out of the board, so in the image its line must be close to
# the direction the board's normal has there
MAX_AXIS_ANGLE = 35       # degrees
AXIS_HEIGHT = 0.2         # board radii, for computing that direction

# Takeout when the change against the empty board has dropped to this share of
# what it was with the darts in
TAKEOUT_SHARE = 0.1

# Refresh the reference when idle this long without any real change (slow light drift)
REFRESH_SECONDS = 10


def to_gray(frame: np.ndarray) -> np.ndarray:
    """Blurred greyscale image used for all comparisons."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0)


def to_small(frame: np.ndarray) -> np.ndarray:
    """Small greyscale image for the motion check."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(cv2.resize(gray, SMALL_SIZE, interpolation=cv2.INTER_AREA), (3, 3), 0)


def board_mask(model: dict, size: tuple[int, int]) -> np.ndarray:
    """Mask (255 inside) of where a dart can show up in a camera's image."""
    width, height = size
    angles = np.linspace(0, 2 * math.pi, 181)
    circle = np.column_stack([MASK_RADIUS * np.sin(angles), -MASK_RADIUS * np.cos(angles)])
    inverse = np.linalg.inv(np.asarray(model["homography"], dtype=np.float64))
    undistorted = cv2.perspectiveTransform(circle.reshape(-1, 1, 2), inverse).reshape(-1, 2)
    valid = np.linalg.norm(undistorted, axis=1) < calibration.max_valid_radius(model)
    pixels = calibration.distort(model, undistorted[valid])
    pixels = np.clip(pixels, -10 * width, 10 * width)
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [np.round(pixels).astype(np.int32).reshape(-1, 1, 2)], 255)
    # Extend upwards: with the anchor on its top row, a pixel is set when the
    # mask has a pixel up to MASK_UP_PX below it
    kernel = np.ones((MASK_UP_PX + 1, 1), np.uint8)
    return cv2.dilate(mask, kernel, anchor=(0, 0))


def change_mask(before: np.ndarray, after: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """
    Binary image of pixels that changed between two blurred grey frames.

    A pixel only counts as changed when it is outside the range of `before`
    within SHIFT_PX of it, so a camera that shakes by a pixel or two on impact
    doesn't light up every edge of the board.
    """
    kernel = np.ones((2 * SHIFT_PX + 1, 2 * SHIFT_PX + 1), np.uint8)
    diff = cv2.max(cv2.subtract(after, cv2.dilate(before, kernel)),
                   cv2.subtract(cv2.erode(before, kernel), after))
    _, changed = cv2.threshold(diff, CHANGE_PIXEL, 255, cv2.THRESH_BINARY)
    changed = cv2.bitwise_and(changed, mask)
    changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return cv2.morphologyEx(changed, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))


@dataclass
class DartView:
    """A dart seen by one camera, in image pixels."""

    tip: tuple[float, float]        # lower end of the blob along the line
    top: tuple[float, float]        # upper end
    pixels: int
    elongation: float


def find_dart(changed: np.ndarray) -> DartView | None:
    """Fit a line to the changed pixels; None when they don't look like one dart."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(changed, connectivity=8)
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= MIN_COMPONENT]
    if not keep:
        return None
    ys, xs = np.nonzero(np.isin(labels, keep))
    if len(xs) < MIN_DART_PIXELS:
        return None
    points = np.column_stack([xs, ys]).astype(np.float32)
    if len(points) > 20000:
        points = points[np.random.default_rng(0).choice(len(points), 20000, replace=False)]

    for _ in range(2):  # fit, drop pixels far from the line (other specks), refit
        vx, vy, x0, y0 = cv2.fitLine(points, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        normal = np.array([-vy, vx])
        distance = np.abs((points - [x0, y0]) @ normal)
        near = distance < REFINE_PX
        if near.sum() < MIN_DART_PIXELS:
            return None
        points = points[near]

    direction = np.array([vx, vy])
    if direction[1] < 0:  # point down the image, towards the board
        direction = -direction
    along = (points - [x0, y0]) @ direction
    # Trim a few stray pixels off each end, but no more than TIP_TRIM_PIXELS:
    # for a dart close to the camera a percentage would cut off the whole point
    ordered = np.sort(along)
    trim = min(int(len(ordered) * (100 - TIP_PERCENTILE) / 100), TIP_TRIM_PIXELS)
    low, high = ordered[trim], ordered[-1 - trim]
    width = 2 * np.percentile(np.abs((points - [x0, y0]) @ normal), 95)
    elongation = (high - low) / max(width, 1.0)
    if elongation < MIN_ELONGATION:
        return None
    origin = np.array([x0, y0])
    tip = origin + direction * high
    top = origin + direction * low
    return DartView(tip=(float(tip[0]), float(tip[1])), top=(float(top[0]), float(top[1])),
                    pixels=int(len(points)), elongation=float(elongation))


def camera_pose(model: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """
    Rotation and translation (board -> camera, board radii) from a calibration.

    The model's homography maps undistorted normalized image coordinates to the
    board, so its inverse is [r1 r2 t] up to scale. Returns (R, t) with the
    board's z axis pointing towards the camera, or None for a model without
    lens data (then pixels are not normalized and this doesn't apply).
    """
    if "camera_matrix" not in model:
        return None
    inverse = np.linalg.inv(np.asarray(model["homography"], dtype=np.float64))
    scale = 2 / (np.linalg.norm(inverse[:, 0]) + np.linalg.norm(inverse[:, 1]))
    r1, r2, t = (inverse * scale).T
    if t[2] < 0:  # the board must be in front of the camera
        r1, r2, t = -r1, -r2, -t
    r3 = np.cross(r1, r2)
    rotation = np.column_stack([r1, r2, r3])
    if (rotation.T @ -t)[2] < 0:  # camera on the negative-z side: flip z
        rotation[:, 2] = -r3
    return rotation, t


def dart_axis(model: dict, x: float, y: float) -> np.ndarray | None:
    """Unit image direction in which a dart standing straight out of the board at (x, y) points."""
    pose = camera_pose(model)
    if pose is None:
        return None
    rotation, t = pose
    ends = []
    for height in (0.0, AXIS_HEIGHT):
        camera = rotation @ np.array([x, y, height]) + t
        ends.append(calibration.distort(model, [camera[:2] / camera[2]])[0])
    direction = ends[1] - ends[0]
    return direction / np.linalg.norm(direction)


def orient(model: dict, view: DartView) -> DartView | None:
    """
    Check a camera's dart line against the board's normal there, and make sure
    `tip` is the end in the board. None when the line is too far off to be a dart.
    """
    line = np.subtract(view.top, view.tip)
    line /= max(np.linalg.norm(line), 1e-9)
    axis = dart_axis(model, *calibration.image_to_board(model, *view.tip))
    if axis is None:
        return view
    cos = float(line @ axis)  # axis points from the board up the dart
    if math.degrees(math.acos(min(1.0, abs(cos)))) > MAX_AXIS_ANGLE:
        return None
    if cos < 0:
        return DartView(tip=view.top, top=view.tip, pixels=view.pixels,
                        elongation=view.elongation)
    return view


@dataclass
class BoardLine:
    """A camera's dart line mapped onto the board: through `point` along `direction`."""

    camera: int
    point: np.ndarray       # board position of the visible lower end of the dart
    direction: np.ndarray   # unit vector
    mm_per_px: float = TIP_REFERENCE_MM_PER_PX  # coarsest board resolution at `point`


def mm_per_pixel(model: dict, u: float, v: float, step: float = 2.0) -> float:
    """How many mm on the board one image pixel at (u, v) covers, in its coarsest direction."""
    here = np.array(calibration.image_to_board(model, u, v))
    jacobian = np.column_stack([
        np.array(calibration.image_to_board(model, u + step, v)) - here,
        np.array(calibration.image_to_board(model, u, v + step)) - here,
    ]) / step * calibration.BOARD_RADIUS_MM
    return float(np.linalg.svd(jacobian, compute_uv=False)[0])


def board_line(camera: int, model: dict, view: DartView) -> BoardLine:
    """Map a camera's dart line onto the board plane."""
    tip = np.array(calibration.image_to_board(model, *view.tip))
    # The middle of the dart rather than its top: the top of a dart far away
    # can be above the board plane's horizon in the image
    middle = [(a + b) / 2 for a, b in zip(view.tip, view.top)]
    direction = np.array(calibration.image_to_board(model, *middle)) - tip
    norm = np.linalg.norm(direction)
    return BoardLine(camera, tip, direction / norm if norm > 1e-9 else np.array([0.0, 1.0]),
                     mm_per_pixel(model, *view.tip))


def intersect(lines: list[BoardLine], tip_weight: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """
    Least-squares intersection of board lines; returns (point, distance of each line).

    With tip_weight > 0 the visible lower dart ends pull the point towards
    them as well, which settles where along nearly parallel lines it lies;
    each end pulls in proportion to how sharply its camera sees the board there.
    """
    normals = np.array([[-line.direction[1], line.direction[0]] for line in lines])
    offsets = np.array([n @ line.point for n, line in zip(normals, lines)])
    rows, values = [normals], [offsets]
    if tip_weight > 0:
        for line in lines:
            weight = math.sqrt(tip_weight) * TIP_REFERENCE_MM_PER_PX / max(line.mm_per_px, 1e-3)
            rows.append(weight * np.eye(2))
            values.append(weight * line.point)
    point, *_ = np.linalg.lstsq(np.vstack(rows), np.concatenate(values), rcond=None)
    return point, np.abs(normals @ point - offsets)


def _angle(a: BoardLine, b: BoardLine) -> float:
    cos = abs(float(a.direction @ b.direction))
    return math.degrees(math.acos(min(1.0, cos)))


def locate(lines: list[BoardLine]) -> tuple[np.ndarray, str]:
    """
    Board position of the dart tip from one or more camera lines, and how it was found.

    All lines are intersected when they agree. Otherwise (one camera saw
    something else, or its view was blocked) each pair of lines is tried, and
    the pair meeting closest to the visible lower ends of the darts wins. With
    no usable intersection, the visible lower ends are used directly.
    """
    if not lines:
        raise ValueError("Ingen kamera såg pilen")
    tips = np.median(np.array([line.point for line in lines]), axis=0)

    def name(group):
        return "+".join(str(line.camera) for line in group)

    if len(lines) >= 3 and _spread(lines) >= MIN_LINE_ANGLE:
        point, distances = intersect(lines, TIP_WEIGHT)
        if distances.max() * calibration.BOARD_RADIUS_MM <= LINE_TOLERANCE_MM:
            return point, "skärning " + name(lines)
    pairs = [(a, b) for i, a in enumerate(lines) for b in lines[i + 1:]
             if _angle(a, b) >= MIN_LINE_ANGLE]
    if pairs:
        candidates = [(intersect(pair, TIP_WEIGHT)[0], pair) for pair in pairs]
        point, pair = min(candidates, key=lambda c: np.linalg.norm(c[0] - tips))
        if np.linalg.norm(point - tips) * calibration.BOARD_RADIUS_MM <= MAX_TIP_OFFSET_MM:
            return point, "skärning " + name(pair)
    return tips, "spets " + name(lines)


def _spread(lines: list[BoardLine]) -> float:
    return max(_angle(a, b) for i, a in enumerate(lines) for b in lines[i + 1:])


@dataclass
class Event:
    """Something the detector found: a dart, a takeout, or an unclear change."""

    kind: str                                   # "dart", "takeout", "removed" or "unclear"
    x: float | None = None
    y: float | None = None
    segment: int | None = None
    multiplier: int | None = None
    method: str = ""
    views: dict = field(default_factory=dict)   # camera id -> DartView
    lines: list = field(default_factory=list)   # BoardLine per camera
    changed: dict = field(default_factory=dict)  # camera id -> changed pixel count
    before: dict = field(default_factory=dict)  # camera id -> colour frame (reference)
    after: dict = field(default_factory=dict)   # camera id -> colour frame (now)

    def describe(self) -> str:
        """Short human-readable summary."""
        if self.kind != "dart":
            return f"{self.kind} (ändrade pixlar {self.changed})"
        name = {0: "miss", 1: "", 2: "D", 3: "T"}[self.multiplier]
        score = "Bull" if (self.segment, self.multiplier) == (25, 2) else \
            "25" if self.segment == 25 else "Miss" if self.segment == 0 else f"{name}{self.segment}"
        return (f"pil {score} vid ({self.x:+.3f}, {self.y:+.3f}), {self.method}, "
                f"kameror {sorted(self.views)}")


class Detector:
    """Turns a stream of frame sets into dart events. Feed it with `feed()`."""

    def __init__(self, models: dict[int, dict], image_size=(1280, 720)):
        self.models = models
        self.masks = {cid: board_mask(model, image_size) for cid, model in models.items()}
        self.small_masks = {cid: cv2.resize(mask, SMALL_SIZE, interpolation=cv2.INTER_NEAREST) > 0
                            for cid, mask in self.masks.items()}
        self.reference = None      # camera -> blurred grey, after the last event
        self.reference_frames = None
        self.empty = None          # camera -> blurred grey of the board without darts
        self.empty_change = 0      # changed pixels against `empty` after the last event
        self.previous_small = None
        self.moving = False
        self.still = 0
        self.last_event_time = 0.0
        self.darts = 0             # darts on the board since the last takeout

    def reset(self, frames: dict, now: float = 0.0) -> None:
        """Take `frames` as the empty board."""
        grays = {cid: to_gray(frame) for cid, frame in frames.items()}
        self.reference = grays
        self.reference_frames = dict(frames)
        self.empty = grays
        self.empty_change = 0
        self.previous_small = {cid: to_small(frame) for cid, frame in frames.items()}
        self.moving = False
        self.still = 0
        self.last_event_time = now
        self.darts = 0

    def feed(self, frames: dict, now: float) -> Event | None:
        """Process one set of frames; return an event when the view has settled after motion."""
        if self.reference is None:
            self.reset(frames, now)
            return None
        small = {cid: to_small(frame) for cid, frame in frames.items()}
        motion = False
        for cid, image in small.items():
            mask = self.small_masks[cid]
            moved = cv2.absdiff(image, self.previous_small[cid])[mask] > MOTION_PIXEL
            if moved.mean() > MOTION_FRACTION:
                motion = True
        self.previous_small = small

        if motion:
            self.moving = True
            self.still = 0
            return None
        self.still += 1
        if not self.moving:
            if now - self.last_event_time > REFRESH_SECONDS:
                self._refresh(frames, now)
            return None
        if self.still < STILL_FRAMES:
            return None
        self.moving = False
        self.last_event_time = now
        return self.evaluate(frames)

    def _refresh(self, frames: dict, now: float) -> None:
        """Follow slow light changes while nothing happens."""
        self.last_event_time = now
        grays = {cid: to_gray(frame) for cid, frame in frames.items()}
        changed = {cid: cv2.countNonZero(change_mask(self.reference[cid], g, self.masks[cid]))
                   for cid, g in grays.items()}
        if max(changed.values()) < MIN_DART_PIXELS:
            self.reference = grays
            self.reference_frames = dict(frames)
            if self.darts == 0:  # the empty board, as it looks now
                self.empty = grays
                self.empty_change = 0

    def evaluate(self, frames: dict) -> Event:
        """Compare settled frames with the reference and classify what happened."""
        grays = {cid: to_gray(frame) for cid, frame in frames.items()}
        changes = {cid: change_mask(self.reference[cid], g, self.masks[cid]) for cid, g in grays.items()}
        changed = {cid: cv2.countNonZero(c) for cid, c in changes.items()}
        empty_change = sum(cv2.countNonZero(change_mask(self.empty[cid], g, self.masks[cid]))
                           for cid, g in grays.items())
        event = Event("unclear", changed=changed, before=self.reference_frames, after=dict(frames))

        def accept(kind: str) -> Event:
            event.kind = kind
            self.reference = grays
            self.reference_frames = dict(frames)
            self.empty_change = empty_change
            return event

        if max(changed.values()) < MIN_DART_PIXELS:
            event.kind = "none"
            return event
        if self.darts and (empty_change < MIN_DART_PIXELS * len(grays)
                           or empty_change < TAKEOUT_SHARE * self.empty_change):
            self.darts = 0
            self.empty = grays
            return accept("takeout")
        # Fewer changes against the empty board than before: a dart was pulled out
        if self.darts and empty_change < self.empty_change - sum(changed.values()) / 2:
            self.darts = max(0, self.darts - 1)
            return accept("removed")
        size = next(iter(grays.values())).size
        if max(changed.values()) > MAX_DART_FRACTION * size:
            return accept("unclear")

        lines = []
        for cid, change in changes.items():
            view = find_dart(change)
            if view is not None:
                view = orient(self.models[cid], view)
            if view is None:
                continue
            event.views[cid] = view
            lines.append(board_line(cid, self.models[cid], view))
        if not lines:
            return accept("unclear")
        point, method = locate(lines)
        event.lines = lines
        event.method = method
        if np.linalg.norm(point) > MAX_RADIUS:
            return accept("unclear")  # nothing sticks out there
        event.x, event.y = float(point[0]), float(point[1])
        event.segment, event.multiplier = classify(event.x, event.y)
        self.darts += 1
        return accept("dart")


def draw_event(event: Event, camera: int, models: dict) -> np.ndarray:
    """Debug image for one camera: the change, the fitted line and the found tip."""
    image = event.after[camera].copy()
    before = to_gray(event.before[camera])
    after = to_gray(event.after[camera])
    mask = np.full(before.shape, 255, np.uint8)
    changed = change_mask(before, after, mask)
    contours, _ = cv2.findContours(changed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(image, contours, -1, (0, 0, 255), 1)
    view = event.views.get(camera)
    if view is not None:
        cv2.line(image, tuple(round(c) for c in view.top), tuple(round(c) for c in view.tip),
                 (0, 255, 255), 1, cv2.LINE_AA)
        cv2.circle(image, tuple(round(c) for c in view.tip), 6, (0, 255, 255), 1, cv2.LINE_AA)
    if event.x is not None:
        u, v = calibration.board_to_image(models[camera], event.x, event.y)
        cv2.drawMarker(image, (round(u), round(v)), (0, 255, 0), cv2.MARKER_CROSS, 24, 2)
    cv2.putText(image, event.describe()[:90], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 0), 2, cv2.LINE_AA)
    return image
