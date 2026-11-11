"""X01 game logic (301/501 etc.) for any number of players."""

import copy
from dataclasses import dataclass, field

DARTS_PER_TURN = 3


@dataclass(frozen=True)
class Dart:
    """One thrown dart: segment 1-20, 25 (bull) or 0 (miss), multiplier 0-3."""

    segment: int
    multiplier: int

    @property
    def points(self) -> int:
        """Points this dart is worth."""
        return self.segment * self.multiplier

    @property
    def label(self) -> str:
        """Short display label, e.g. 'T20', 'D16', 'Bull', 'Miss'."""
        if self.multiplier == 0:
            return "Miss"
        if self.segment == 25:
            return "Bull" if self.multiplier == 2 else "25"
        prefix = {1: "", 2: "D", 3: "T"}[self.multiplier]
        return f"{prefix}{self.segment}"


def make_dart(segment: int, multiplier: int) -> Dart:
    """Validate and build a Dart; raise ValueError for impossible throws."""
    if segment == 0 or multiplier == 0:
        return Dart(0, 0)
    if segment == 25 and multiplier in (1, 2):
        return Dart(segment, multiplier)
    if 1 <= segment <= 20 and multiplier in (1, 2, 3):
        return Dart(segment, multiplier)
    raise ValueError(f"Ogiltigt kast: segment {segment}, multiplikator {multiplier}")


@dataclass
class Player:
    """A participant and their running statistics."""

    name: str
    remaining: int
    darts_thrown: int = 0
    points_scored: int = 0
    last_turn: list[str] = field(default_factory=list)

    @property
    def average(self) -> float:
        """Three-dart average."""
        if self.darts_thrown == 0:
            return 0.0
        return self.points_scored / self.darts_thrown * DARTS_PER_TURN


class Game:
    """
    An X01 game: each player counts down from start_score to exactly 0.

    Going below 0 (or to 1, or to 0 on a non-double, with double_out) is a
    bust: the player's score reverts to what it was at the start of the turn
    and the turn ends.
    """

    def __init__(self, players: list[str], start_score: int = 501,
                 double_out: bool = True):
        names = [name.strip() for name in players if name.strip()]
        if not names:
            raise ValueError("Minst en spelare krävs")
        if start_score < 2:
            raise ValueError("Startpoängen måste vara minst 2")

        self.start_score = start_score
        self.double_out = double_out
        self.players = [Player(name, start_score) for name in names]
        self.current = 0
        self.turn: list[Dart] = []
        self.turn_start = start_score
        self.bust = False
        self.winner: int | None = None
        self._history: list[tuple] = []

    @property
    def turn_over(self) -> bool:
        """True when the current player has no darts left this turn."""
        return self.bust or len(self.turn) >= DARTS_PER_TURN

    def throw(self, segment: int, multiplier: int) -> Dart:
        """Register a dart for the current player (advancing if their turn is over)."""
        if self.winner is not None:
            raise ValueError("Spelet är slut – starta ett nytt spel")
        dart = make_dart(segment, multiplier)

        self._save()
        if self.turn_over:
            self._advance()

        player = self.players[self.current]
        self.turn.append(dart)
        player.darts_thrown += 1
        new_remaining = player.remaining - dart.points

        if self._is_bust(new_remaining, dart):
            self.bust = True
            player.points_scored -= self.turn_start - player.remaining
            player.remaining = self.turn_start
        else:
            player.remaining = new_remaining
            player.points_scored += dart.points
            if new_remaining == 0:
                self.winner = self.current
                player.last_turn = [d.label for d in self.turn]

        return dart

    def takeout(self) -> None:
        """
        The darts were pulled from the board: darts of this turn that never got
        registered (they missed the board) count as misses, so the turn is over.

        Does nothing when the turn is already over or no dart was registered in it.
        """
        if self.winner is not None or self.turn_over or not self.turn:
            return
        self._save()
        player = self.players[self.current]
        while len(self.turn) < DARTS_PER_TURN:
            self.turn.append(Dart(0, 0))
            player.darts_thrown += 1

    def next_player(self) -> None:
        """End the current turn early (e.g. darts pulled) and move on."""
        if self.winner is not None:
            raise ValueError("Spelet är slut – starta ett nytt spel")
        self._save()
        self._advance()

    def undo(self) -> bool:
        """Revert the last throw or turn change. Return False if nothing to undo."""
        if not self._history:
            return False
        (self.players, self.current, self.turn, self.turn_start,
         self.bust, self.winner) = self._history.pop()
        return True

    def to_dict(self) -> dict:
        """JSON-serializable snapshot of the game for the scoreboard."""
        return {
            "start_score": self.start_score,
            "double_out": self.double_out,
            "current": self.current,
            "winner": self.winner,
            "bust": self.bust,
            "turn_over": self.turn_over,
            "turn": [{"label": d.label, "points": d.points} for d in self.turn],
            "turn_points": sum(d.points for d in self.turn) if not self.bust else 0,
            "can_undo": bool(self._history),
            "players": [
                {
                    "name": p.name,
                    "remaining": p.remaining,
                    "darts_thrown": p.darts_thrown,
                    "average": round(p.average, 1),
                    "last_turn": p.last_turn,
                }
                for p in self.players
            ],
        }

    def _is_bust(self, new_remaining: int, dart: Dart) -> bool:
        if new_remaining < 0:
            return True
        if self.double_out:
            if new_remaining == 1:
                return True
            if new_remaining == 0 and dart.multiplier != 2:
                return True
        return False

    def _advance(self) -> None:
        labels = [d.label for d in self.turn]
        if self.bust:
            labels.append("BUST")
        self.players[self.current].last_turn = labels
        self.current = (self.current + 1) % len(self.players)
        self.turn = []
        self.bust = False
        self.turn_start = self.players[self.current].remaining

    def _save(self) -> None:
        self._history.append(copy.deepcopy(
            (self.players, self.current, self.turn, self.turn_start,
             self.bust, self.winner)
        ))
