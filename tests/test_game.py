"""Unit tests for dart_scoring.game (X01 rules)."""
# Test names describe the behaviour under test.
# pylint: disable=missing-function-docstring

import pytest

from dart_scoring.game import Game, make_dart
from dart_scoring.scoring import classify


def game_at(remaining, players=("A", "B"), double_out=True):
    """A game where player A has `remaining` left and it is A's turn."""
    game = Game(list(players), start_score=501, double_out=double_out)
    game.players[0].remaining = remaining
    game.turn_start = remaining
    return game


def test_scores_count_down():
    game = Game(["A"], 501)
    game.throw(20, 3)
    game.throw(20, 1)
    assert game.players[0].remaining == 421


def test_turn_passes_after_three_darts():
    game = Game(["A", "B"])
    for _ in range(3):
        game.throw(20, 1)
    assert game.turn_over
    assert game.current == 0
    game.throw(5, 1)
    assert game.current == 1
    assert game.players[0].last_turn == ["20", "20", "20"]
    assert game.players[1].remaining == 496


def test_takeout_fills_the_turn_with_misses():
    game = Game(["A", "B"])
    game.throw(20, 1)
    game.takeout()
    assert game.turn_over
    assert [d["label"] for d in game.to_dict()["turn"]] == ["20", "Miss", "Miss"]
    assert game.players[0].darts_thrown == 3
    game.throw(5, 1)
    assert game.current == 1
    assert game.players[0].last_turn == ["20", "Miss", "Miss"]


def test_takeout_does_nothing_after_a_full_turn_or_bust_or_before_any_dart():
    game = Game(["A", "B"])
    game.takeout()
    assert game.turn == []
    assert not game.to_dict()["can_undo"]
    for _ in range(3):
        game.throw(20, 1)
    game.takeout()
    assert len(game.turn) == 3 and game.players[0].darts_thrown == 3

    busted = game_at(10)
    busted.throw(20, 1)
    busted.takeout()
    assert len(busted.turn) == 1 and busted.bust


def test_takeout_can_be_undone():
    game = Game(["A", "B"])
    game.throw(20, 1)
    game.takeout()
    game.undo()
    assert len(game.turn) == 1 and not game.turn_over
    assert game.players[0].darts_thrown == 1


def test_bust_below_zero_reverts_turn():
    game = game_at(40)
    game.throw(10, 1)
    game.throw(20, 2)
    assert game.bust
    assert game.turn_over
    assert game.players[0].remaining == 40


def test_bust_on_one_with_double_out():
    game = game_at(21)
    game.throw(20, 1)
    assert game.bust
    assert game.players[0].remaining == 21


def test_checkout_requires_double():
    game = game_at(20)
    game.throw(20, 1)
    assert game.bust
    assert game.winner is None


def test_checkout_on_double_wins():
    game = game_at(40)
    game.throw(20, 2)
    assert game.winner == 0
    assert game.players[0].remaining == 0
    with pytest.raises(ValueError):
        game.throw(1, 1)


def test_bullseye_counts_as_double():
    game = game_at(50)
    game.throw(25, 2)
    assert game.winner == 0


def test_straight_out_allows_single_finish():
    game = game_at(20, double_out=False)
    game.throw(20, 1)
    assert game.winner == 0


def test_undo_reverts_throw_and_turn_change():
    game = Game(["A", "B"])
    for _ in range(3):
        game.throw(20, 3)
    game.next_player()
    assert game.current == 1
    game.undo()
    assert game.current == 0
    game.undo()
    assert game.players[0].remaining == 381
    assert len(game.turn) == 2


def test_average_is_per_three_darts():
    game = Game(["A"])
    game.throw(20, 3)
    game.throw(20, 1)
    game.throw(0, 0)
    assert game.players[0].average == pytest.approx(80.0)


@pytest.mark.parametrize("segment, multiplier", [(25, 3), (21, 1), (20, 4), (-1, 1)])
def test_invalid_darts_are_rejected(segment, multiplier):
    with pytest.raises(ValueError):
        make_dart(segment, multiplier)


def test_empty_player_list_is_rejected():
    with pytest.raises(ValueError):
        Game(["", "  "])


@pytest.mark.parametrize("point, expected", [
    ((0.0, 0.0), (25, 2)),
    ((0.0, -0.06), (25, 1)),
    ((0.0, -0.6), (20, 3)),
    ((0.0, -0.98), (20, 2)),
    ((0.0, -0.4), (20, 1)),
    ((0.0, -1.1), (0, 0)),
])
def test_classify(point, expected):
    assert classify(*point) == expected
