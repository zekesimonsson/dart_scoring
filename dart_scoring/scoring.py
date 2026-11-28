"""Dart score calculation in normalized board coordinates."""

import math

BOARD_RADIUS_MM = 170   # normalized radius 1.0

# The ring wires on this board, in mm from the middle of the bull: the outer
# edges of each ring's wires, which is where the calibration points are
# clicked. Measured over all three cameras' calibration points (Winmau Blade 6):
# with their wires the rings are about 10 mm wide, not the nominal 8 mm
# (99-107 and 162-170 mm) between the wires, and fitting the calibration to the
# nominal sizes put the wires about 1 mm off.
RING_EDGES_MM = {
    "double_outer": 171.3,
    "double_inner": 161.0,
    "triple_outer": 107.6,
    "triple_inner": 97.5,
    "bull_outer": 16.9,
    "bull_inner": 7.1,
}
# A dart scores by which side of a wire's middle it is on: half a wire inside
# the clicked edges
WIRE_MM = 1.0


def _wire_middle(ring: str) -> float:
    """Normalized radius of the middle of a ring's wire (`_inner` edges lie outside it)."""
    inward = -1 if ring.endswith("_inner") and not ring.startswith("bull") else 1
    return (RING_EDGES_MM[ring] - inward * WIRE_MM / 2) / BOARD_RADIUS_MM


BULL_RADIUS = _wire_middle("bull_inner")
OUTER_BULL_RADIUS = _wire_middle("bull_outer")
TRIPLE_INNER = _wire_middle("triple_inner")
TRIPLE_OUTER = _wire_middle("triple_outer")
DOUBLE_INNER = _wire_middle("double_inner")
DOUBLE_OUTER = _wire_middle("double_outer")

# Clockwise order, starting at the top
SEGMENTS = [
    20, 1, 18, 4, 13,
    6, 10, 15, 2, 17,
    3, 19, 7, 16, 8,
    11, 14, 9, 12, 5
]


def classify(x: float, y: float) -> tuple[int, int]:
    """
    Classify a dart from normalized coordinates as (segment, multiplier).

    segment is 1-20, 25 for the bulls, or 0 for a miss.
    multiplier is 1-3 (bullseye = (25, 2)), or 0 for a miss.

    (0, 0) = board center
    (0, -1) = top of double ring
    Radius 1.0 = 170 mm, the nominal outer edge of the double ring; the
    ring boundaries are the wires of this board (RING_EDGES_MM)
    """

    radius = math.hypot(x, y)

    # Outside scoring area
    if radius > DOUBLE_OUTER:
        return 0, 0

    # Bullseye
    if radius <= BULL_RADIUS:
        return 25, 2

    # Outer bull
    if radius <= OUTER_BULL_RADIUS:
        return 25, 1

    # Determine segment
    angle = math.degrees(math.atan2(x, -y))
    angle = (angle + 360) % 360

    index = int((angle + 9) // 18) % 20
    segment = SEGMENTS[index]

    # Triple ring
    if TRIPLE_INNER <= radius <= TRIPLE_OUTER:
        return segment, 3

    # Double ring
    if DOUBLE_INNER <= radius:
        return segment, 2

    return segment, 1


def calculate_score(x: float, y: float) -> int:
    """Calculate dart score from normalized coordinates (see classify)."""
    segment, multiplier = classify(x, y)
    return segment * multiplier
