"""Unit tests for dart_scoring.scoring.calculate_score."""
# Test names describe the behaviour under test.
# pylint: disable=missing-function-docstring

import math

import pytest

from dart_scoring.scoring import SEGMENTS, calculate_score

BULL_R = 6.35 / 170
OUTER_BULL_R = 15.9 / 170
TRIPLE_INNER_R = 99 / 170
TRIPLE_OUTER_R = 107 / 170
DOUBLE_INNER_R = 162 / 170


def polar(radius: float, angle_deg: float) -> tuple[float, float]:
    """Board coordinates for a point at `angle_deg` clockwise from the 20."""
    a = math.radians(angle_deg)
    return radius * math.sin(a), -radius * math.cos(a)


def score_at(radius: float, angle_deg: float) -> int:
    return calculate_score(*polar(radius, angle_deg))


# --- Bulls --------------------------------------------------------------

def test_center_is_bullseye():
    assert calculate_score(0.0, 0.0) == 50


def test_bullseye_edge_is_inclusive():
    assert calculate_score(0.0, -BULL_R) == 50


def test_just_outside_bullseye_is_outer_bull():
    assert calculate_score(0.0, -(BULL_R + 1e-6)) == 25


def test_outer_bull_edge_is_inclusive():
    assert calculate_score(0.0, -OUTER_BULL_R) == 25


@pytest.mark.parametrize("angle", [0, 45, 90, 180, 270])
def test_bulls_ignore_angle(angle):
    assert score_at(BULL_R / 2, angle) == 50
    assert score_at((BULL_R + OUTER_BULL_R) / 2, angle) == 25


# --- Segments -----------------------------------------------------------

@pytest.mark.parametrize("index, number", list(enumerate(SEGMENTS)))
def test_segment_centers_single(index, number):
    assert score_at(0.4, index * 18) == number
    assert score_at(0.8, index * 18) == number


@pytest.mark.parametrize("index, number", list(enumerate(SEGMENTS)))
def test_segment_centers_triple(index, number):
    assert score_at((TRIPLE_INNER_R + TRIPLE_OUTER_R) / 2, index * 18) == number * 3


@pytest.mark.parametrize("index, number", list(enumerate(SEGMENTS)))
def test_segment_centers_double(index, number):
    assert score_at((DOUBLE_INNER_R + 1.0) / 2, index * 18) == number * 2


@pytest.mark.parametrize(
    "x, y, expected",
    [
        (0.0, -0.5, 20),   # straight up
        (0.5, 0.0, 6),     # right
        (0.0, 0.5, 3),     # down
        (-0.5, 0.0, 11),   # left
    ],
)
def test_cardinal_directions(x, y, expected):
    assert calculate_score(x, y) == expected


def test_segment_boundary_belongs_to_clockwise_segment():
    # 9 degrees is the 20/1 boundary.
    assert score_at(0.4, 8.9) == 20
    assert score_at(0.4, 9.0) == 1


def test_wraparound_near_top_is_twenty():
    assert score_at(0.4, 359.0) == 20
    assert score_at(0.4, 351.1) == 20
    assert score_at(0.4, 350.9) == 5


# --- Rings --------------------------------------------------------------

def test_triple_ring_edges_inclusive():
    assert calculate_score(0.0, -TRIPLE_INNER_R) == 60
    assert calculate_score(0.0, -TRIPLE_OUTER_R) == 60


def test_just_outside_triple_ring_is_single():
    assert calculate_score(0.0, -(TRIPLE_INNER_R - 1e-6)) == 20
    assert calculate_score(0.0, -(TRIPLE_OUTER_R + 1e-6)) == 20


def test_double_ring_edges_inclusive():
    assert calculate_score(0.0, -DOUBLE_INNER_R) == 40
    assert calculate_score(0.0, -1.0) == 40


def test_just_inside_double_ring_is_single():
    assert calculate_score(0.0, -(DOUBLE_INNER_R - 1e-6)) == 20


# --- Misses -------------------------------------------------------------

@pytest.mark.parametrize("x, y", [(0.0, -1.000001), (1.1, 0.0), (-0.8, 0.8), (5.0, 5.0)])
def test_outside_board_is_miss(x, y):
    assert calculate_score(x, y) == 0
