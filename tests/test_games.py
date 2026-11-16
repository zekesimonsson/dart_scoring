"""Unit tests for dart_scoring.games (Cricket, Around the Clock, Shanghai, Killer)."""
# Test names describe the behaviour under test.
# pylint: disable=missing-function-docstring

import random

import pytest

from dart_scoring.games import (CRICKET_NUMBERS, SHANGHAI_ROUNDS, AroundTheClockGame,
                                CricketGame, KillerGame, ShanghaiGame, new_game)


def turn(game, *darts):
    """Throw a whole turn: darts as (segment, multiplier); missing ones are misses."""
    darts = list(darts) + [(0, 0)] * (3 - len(darts))
    for segment, multiplier in darts:
        game.throw(segment, multiplier)


# --- Cricket -----------------------------------------------------------------------

def test_cricket_marks_close_a_number_and_extra_marks_score():
    game = CricketGame(["A", "B"])
    game.throw(20, 3)                   # closed
    game.throw(20, 2)                   # 2 extra marks: 40 points, B hasn't closed 20
    assert game.players[0].marks[20] == 3
    assert game.players[0].points == 40


def test_cricket_no_points_on_a_number_everyone_closed():
    game = CricketGame(["A", "B"])
    turn(game, (20, 3))
    turn(game, (20, 3))                 # B closes 20 as well
    turn(game, (20, 3))
    assert game.players[0].points == 0


def test_cricket_ignores_numbers_outside_15_to_20_and_bull():
    game = CricketGame(["A", "B"])
    turn(game, (14, 3), (1, 1))
    assert game.players[0].total_marks == 0
    assert game.to_dict()["players"][0]["big"] == 0


def test_cricket_bull_counts_two_marks():
    game = CricketGame(["A", "B"])
    game.throw(25, 2)
    game.throw(25, 1)
    assert game.players[0].marks[25] == 3


def test_cricket_win_needs_everything_closed_and_enough_points():
    game = CricketGame(["A", "B"])
    game.players[1].points = 50
    for number in CRICKET_NUMBERS[:-1]:
        game.players[0].marks[number] = 3
    game.players[0].marks[25] = 1
    game.throw(25, 2)                   # all closed but 0 < 50 points
    assert game.winner is None
    game.throw(20, 3)                   # 60 points, B has 20 open
    assert game.winner == 0


def test_cricket_targets_drop_numbers_closed_by_everyone():
    game = CricketGame(["A", "B"])
    for player in game.players:
        player.marks[20] = 3
    assert 20 not in game.targets(0)
    assert 19 in game.targets(0)


def test_cricket_correction_recounts_the_turn():
    game = CricketGame(["A", "B"])
    game.throw(20, 3)
    game.throw(20, 1)
    game.correct(0, 19, 3)
    assert game.players[0].marks[20] == 1 and game.players[0].marks[19] == 3
    assert game.players[0].points == 0


# --- Around the Clock ------------------------------------------------------------------

def test_clock_numbers_must_be_hit_in_order():
    game = AroundTheClockGame(["A", "B"])
    game.throw(2, 1)                    # not the target yet
    game.throw(1, 3)                    # one step, whatever the multiplier
    game.throw(2, 1)
    assert game.players[0].done == 2
    assert game.targets(0) == [3]


def test_clock_finishes_on_bull():
    game = AroundTheClockGame(["A"])
    game.players[0].done = 20           # only bull left
    game.throw(20, 1)
    assert game.winner is None
    game.throw(25, 1)
    assert game.winner == 0
    assert game.to_dict()["players"][0]["big"] == "✓"


# --- Shanghai ---------------------------------------------------------------------------

def test_shanghai_only_the_round_number_scores():
    game = ShanghaiGame(["A", "B"])
    turn(game, (1, 3), (2, 3), (1, 1))
    assert game.players[0].score == 4
    turn(game, (1, 1))
    game.throw(2, 2)                    # round 2 for A
    assert game.round == 2
    assert game.players[0].score == 8


def test_shanghai_single_double_treble_wins_at_once():
    game = ShanghaiGame(["A", "B"])
    turn(game, (1, 1), (1, 2), (1, 3))
    assert game.winners == [0]
    assert "SHANGHAI" in game.message


def test_shanghai_ends_after_the_last_round_and_shares_a_tie():
    game = ShanghaiGame(["A", "B"])
    for number in range(1, SHANGHAI_ROUNDS + 1):
        assert not game.finished
        turn(game, (number, 1))
        turn(game, (number, 1))
    assert game.winners == [0, 1]
    with pytest.raises(ValueError):
        game.throw(7, 1)


def test_shanghai_skipping_the_last_turn_also_ends_the_game():
    game = ShanghaiGame(["A", "B"])
    for number in range(1, SHANGHAI_ROUNDS):
        turn(game, (number, 1))
        turn(game, (number, 1))
    turn(game, (7, 3))
    game.next_player()                  # B's last turn starts
    game.next_player()                  # ...and is skipped
    assert game.winners == [0]


def test_shanghai_takeout_completes_the_final_turn():
    game = ShanghaiGame(["A"])
    for number in range(1, SHANGHAI_ROUNDS):
        turn(game, (number, 1))
    game.throw(7, 1)
    game.takeout()
    assert game.winners == [0]


# --- Killer -------------------------------------------------------------------------------

def killer(names=("A", "B", "C"), numbers=(1, 2, 3), lives=3):
    return KillerGame(list(names), lives=lives, numbers=list(numbers))


def test_killer_numbers_are_random_and_different():
    game = KillerGame([str(i) for i in range(20)], rng=random.Random(4))
    assert sorted(p.number for p in game.players) == list(range(1, 21))


def test_killer_needs_two_players():
    with pytest.raises(ValueError):
        KillerGame(["A"])


def test_killer_only_a_killer_takes_lives():
    game = killer()
    game.throw(2, 3)                    # not a killer yet: nothing happens
    assert game.players[1].lives == 3
    game.throw(1, 1)                    # own number: killer
    assert game.players[0].killer
    game.throw(2, 2)                    # double: two lives
    assert game.players[1].lives == 1


def test_killer_hitting_your_own_number_as_killer_costs_lives():
    game = killer()
    game.players[0].killer = True
    game.throw(1, 2)
    assert game.players[0].lives == 1


def test_killer_players_who_are_out_are_skipped():
    game = killer()
    game.players[0].killer = True
    game.throw(2, 3)                    # B is out
    assert game.to_dict()["players"][1]["out"]
    game.throw(0, 0)
    game.throw(0, 0)
    game.throw(3, 1)                    # next turn is C's, not B's
    assert game.current == 2


def test_killer_last_player_left_wins():
    game = killer(names=("A", "B"), numbers=(1, 2))
    game.players[0].killer = True
    game.throw(2, 3)
    assert game.winner == 0


def test_killer_taking_your_own_last_life_ends_the_turn():
    game = killer()
    game.players[0].killer = True
    game.players[0].lives = 1
    game.throw(1, 1)
    assert game.turn_over and game.players[0].lives == 0
    game.throw(2, 1)
    assert game.current == 1


def test_killer_correction_undoes_becoming_killer():
    game = killer()
    game.throw(1, 1)
    game.throw(2, 3)
    game.correct(0, 5, 1)               # the first dart was a 5, not A's own 1
    assert not game.players[0].killer
    assert game.players[1].lives == 3


def test_killer_undo_restores_lives():
    game = killer()
    game.players[0].killer = True
    game.throw(2, 3)
    game.undo()
    assert game.players[1].lives == 3


# --- Starting games -------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["x01", "cricket", "around_the_clock", "shanghai", "killer"])
def test_every_game_starts_and_shows_its_players(kind):
    game = new_game(kind, ["A", "B"], {"start_score": 301, "lives": 5})
    state = game.to_dict()
    assert state["game"] == kind
    assert [p["name"] for p in state["players"]] == ["A", "B"]
    assert all("big" in p and "stats" in p for p in state["players"])
    game.throw(20, 1)
    game.takeout()
    assert game.to_dict()["turn_over"]


def test_unknown_game_is_refused():
    with pytest.raises(ValueError):
        new_game("darts-golf", ["A"])
