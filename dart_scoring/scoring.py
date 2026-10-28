
import math

# Clockwise order, starting at the top
SEGMENTS = [
    20, 1, 18, 4, 13,
    6, 10, 15, 2, 17,
    3, 19, 7, 16, 8,
    11, 14, 9, 12, 5
]


def calculate_score(x: float, y: float) -> int:
    """
    Calculate dart score from normalized coordinates.

    (0, 0) = board center
    (0, -1) = top of double ring
    Radius 1.0 = outer edge of double ring
    """

    radius = math.hypot(x, y)

    # Outside scoring area
    if radius > 1.0:
        return 0

    # Bullseye
    if radius <= 6.35 / 170:
        return 50

    # Outer bull
    if radius <= 15.9 / 170:
        return 25

    # Determine segment
    angle = math.degrees(math.atan2(x, -y))
    angle = (angle + 360) % 360

    index = int((angle + 9) // 18) % 20
    segment = SEGMENTS[index]

    # Triple ring
    if 99 / 170 <= radius <= 107 / 170:
        return segment * 3

    # Double ring
    if 162 / 170 <= radius <= 1.0:
        return segment * 2

    return segment
