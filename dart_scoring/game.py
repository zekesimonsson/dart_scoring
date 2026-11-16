"""
Dart games for any number of players.

`TurnGame` holds what every game shares: turns of three darts, a finished
turn staying on display until the next throw, correcting a dart of the turn
on display (the turn is replayed from a snapshot taken when it started),
takeout (unregistered darts count as misses), undo, and the JSON snapshot the
scoreboard shows. Each game only implements how a dart scores (`_score`) and
how a player is displayed. X01 is here; the other games are in `games.py`.
"""

import copy
from dataclasses import dataclass, field

DARTS_PER_TURN = 3


@dataclass(frozen=True)
class Dart:
    """
    One thrown dart: segment 1-20, 25 (bull) or 0 (miss), multiplier 0-3.

    `capture` names the detector's saved images of the dart (a folder in
    captures/), so a correction can be stored next to them; `corrected` marks
    a dart whose value was fixed by hand.
    """

    segment: int
    multiplier: int
    capture: str | None = field(default=None, compare=False)
    corrected: bool = field(default=False, compare=False)

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


def make_dart(segment: int, multiplier: int, capture: str | None = None,
              corrected: bool = False) -> Dart:
    """Validate and build a Dart; raise ValueError for impossible throws."""
    if segment == 0 or multiplier == 0:
        return Dart(0, 0, capture, corrected)
    if segment == 25 and multiplier in (1, 2):
        return Dart(segment, multiplier, capture, corrected)
    if 1 <= segment <= 20 and multiplier in (1, 2, 3):
        return Dart(segment, multiplier, capture, corrected)
    raise ValueError(f"Ogiltigt kast: segment {segment}, multiplikator {multiplier}")


def player_names(players: list[str]) -> list[str]:
    names = [str(name).strip() for name in players if str(name).strip()]
    if not names:
        raise ValueError("Minst en spelare krävs")
    return names


@dataclass
class Player:
    """A participant; games subclass this with their own counters."""

    name: str
    darts_thrown: int = 0
    last_turn: list[str] = field(default_factory=list)


class TurnGame:
    """Turn handling shared by all games. Subclasses implement `_score` and the views."""

    kind = ""
    name = ""
    player_class = Player

    def __init__(self, players: list[str]):
        self.players = [self._new_player(name) for name in player_names(players)]
        self.current = 0
        self.turn: list[Dart] = []
        self.turn_ended = False      # the turn ended before its third dart (bust, out, ...)
        self.winners: list[int] = []
        self.message = ""            # shown in a banner, e.g. "BUST" or "Shanghai!"
        self._history: list[dict] = []
        self._turn_start: dict | None = None

    def _new_player(self, name: str) -> Player:
        return self.player_class(name)

    # --- state -------------------------------------------------------------

    @property
    def winner(self) -> int | None:
        """The winning player's index (the first of them on a shared win)."""
        return self.winners[0] if self.winners else None

    @property
    def finished(self) -> bool:
        return bool(self.winners)

    @property
    def turn_over(self) -> bool:
        """True when the current player has no darts left this turn."""
        return self.finished or self.turn_ended or len(self.turn) >= DARTS_PER_TURN

    # --- actions -----------------------------------------------------------

    def throw(self, segment: int, multiplier: int, capture: str | None = None) -> Dart:
        """Register a dart for the current player (advancing if their turn is over)."""
        if self.finished:
            raise ValueError("Spelet är slut – starta ett nytt spel")
        dart = make_dart(segment, multiplier, capture)
        self._save()
        if self.turn_over:
            self._advance()
        self._apply(dart)
        return dart

    def correct(self, index: int, segment: int, multiplier: int) -> tuple[Dart, Dart]:
        """
        Change dart `index` of the turn on display (also a finished turn, until
        the next throw) and recount the turn. Returns (old dart, new dart).
        """
        if not 0 <= index < len(self.turn) or self._turn_start is None:
            raise ValueError("Ingen sådan pil i turen")
        old = self.turn[index]
        new = make_dart(segment, multiplier, old.capture, corrected=True)
        self._save()
        darts = list(self.turn)
        darts[index] = new
        self._restore(self._turn_start)
        for dart in darts:
            if self.turn_over:
                break  # the corrected turn ended earlier; later darts don't count
            self._apply(dart)
        return old, new

    def takeout(self) -> None:
        """
        The darts were pulled from the board: darts of this turn that never got
        registered (they missed the board) count as misses, so the turn is over.

        Does nothing when the turn is already over or no dart was registered in it.
        """
        if self.turn_over or not self.turn:
            return
        self._save()
        while not self.turn_over:
            self._apply(Dart(0, 0))

    def next_player(self) -> None:
        """End the current turn early (e.g. darts pulled) and move on."""
        if self.finished:
            raise ValueError("Spelet är slut – starta ett nytt spel")
        self._save()
        self._advance()

    def undo(self) -> bool:
        """Revert the last throw or turn change. Return False if nothing to undo."""
        if not self._history:
            return False
        self._restore(self._history.pop())
        return True

    # --- view --------------------------------------------------------------

    def to_dict(self) -> dict:
        """JSON-serializable snapshot of the game for the scoreboard."""
        return {
            "game": self.kind,
            "title": self.title(),
            "current": self.current,
            "winner": self.winner,
            "winners": list(self.winners),
            "message": self.message,
            "turn_over": self.turn_over,
            "turn": [{"label": d.label, "points": d.points, "segment": d.segment,
                      "multiplier": d.multiplier, "detected": d.capture is not None,
                      "corrected": d.corrected} for d in self.turn],
            "turn_summary": self.turn_summary(),
            "highlight": [] if self.turn_over else self.targets(self.current),
            "can_undo": bool(self._history),
            "players": [{"name": p.name, "darts_thrown": p.darts_thrown, "last_turn": p.last_turn,
                         **self.player_view(i)} for i, p in enumerate(self.players)],
        }

    def title(self) -> str:
        return self.name

    def turn_summary(self) -> str:
        return ""

    def targets(self, index: int) -> list[int]:
        """Segments worth aiming at for a player (highlighted on the number pad)."""
        return []

    def player_view(self, index: int) -> dict:
        """
        How a player is shown: "big" (the main number), "caption" (under it),
        "stats" (small texts), optionally "out" (no longer in the game) and
        "marks" ([{"label", "marks"}], Cricket style).
        """
        raise NotImplementedError

    # --- rules -------------------------------------------------------------

    def _score(self, dart: Dart) -> None:
        """Apply a dart already added to the turn; may set turn_ended, winners, message."""
        raise NotImplementedError

    def _in_game(self, index: int) -> bool:
        """Whether a player still takes turns (Killer: lives left)."""
        return True

    def _turn_note(self) -> str:
        """Extra label for the finished turn in a player's "last turn", e.g. "BUST"."""
        return ""

    def _new_turn(self, wrapped: bool) -> None:
        """Called when the next player's turn starts; `wrapped` = a new round began."""

    # --- internals ---------------------------------------------------------

    def _turn_start_player(self) -> Player:
        """The current player as they were when this turn started."""
        if self._turn_start is None:
            return self.players[self.current]
        return self._turn_start["players"][self.current]

    def _apply(self, dart: Dart) -> None:
        if not self.turn:
            self._turn_start = self._snapshot()
        self.turn.append(dart)
        self.players[self.current].darts_thrown += 1
        self._score(dart)
        if self.finished:
            self.players[self.current].last_turn = [d.label for d in self.turn]

    def _advance(self) -> None:
        labels = [d.label for d in self.turn]
        note = self._turn_note()
        if note:
            labels.append(note)
        self.players[self.current].last_turn = labels
        wrapped = False
        for _ in range(len(self.players)):
            self.current = (self.current + 1) % len(self.players)
            wrapped = wrapped or self.current == 0
            if self._in_game(self.current):
                break
        self.turn = []
        self.turn_ended = False
        self.message = ""
        self._turn_start = None
        self._new_turn(wrapped)

    def _snapshot(self) -> dict:
        return copy.deepcopy({k: v for k, v in vars(self).items()
                              if k not in ("_history", "_turn_start")})

    def _restore(self, snapshot: dict) -> None:
        """Go back to a snapshot; one without "_turn_start" is the start of a turn."""
        history = self._history
        vars(self).update(copy.deepcopy(snapshot))
        self._history = history
        if "_turn_start" not in snapshot:
            self._turn_start = None

    def _save(self) -> None:
        state = self._snapshot()
        state["_turn_start"] = copy.deepcopy(self._turn_start)
        self._history.append(state)


@dataclass
class X01Player(Player):
    remaining: int = 0
    points_scored: int = 0

    @property
    def average(self) -> float:
        """Three-dart average."""
        if self.darts_thrown == 0:
            return 0.0
        return self.points_scored / self.darts_thrown * DARTS_PER_TURN


class X01Game(TurnGame):
    """
    An X01 game: each player counts down from start_score to exactly 0.

    Going below 0 (or to 1, or to 0 on a non-double, with double_out) is a
    bust: the player's score reverts to what it was at the start of the turn
    and the turn ends.
    """

    kind = "x01"
    player_class = X01Player

    def __init__(self, players: list[str], start_score: int = 501,
                 double_out: bool = True):
        if start_score < 2:
            raise ValueError("Startpoängen måste vara minst 2")
        self.start_score = start_score
        self.double_out = double_out
        super().__init__(players)
        self.turn_start = start_score

    def _new_player(self, name: str) -> X01Player:
        return X01Player(name, remaining=self.start_score)

    @property
    def bust(self) -> bool:
        return self.turn_ended

    def title(self) -> str:
        return f"{self.start_score}{' · double out' if self.double_out else ''}"

    def turn_summary(self) -> str:
        return str(0 if self.bust else sum(d.points for d in self.turn))

    def player_view(self, index: int) -> dict:
        p = self.players[index]
        return {"big": p.remaining, "caption": "", "remaining": p.remaining,
                "average": round(p.average, 1),
                "stats": [f"Snitt {p.average:.1f}", f"{p.darts_thrown} pilar"]}

    def to_dict(self) -> dict:
        state = super().to_dict()
        state.update(start_score=self.start_score, double_out=self.double_out, bust=self.bust,
                     turn_points=0 if self.bust else sum(d.points for d in self.turn))
        return state

    def _score(self, dart: Dart) -> None:
        player = self.players[self.current]
        new_remaining = player.remaining - dart.points
        if self._is_bust(new_remaining, dart):
            self.turn_ended = True
            player.points_scored -= self.turn_start - player.remaining
            player.remaining = self.turn_start
            self.message = f"BUST – {player.name} går tillbaka till {player.remaining}"
        else:
            player.remaining = new_remaining
            player.points_scored += dart.points
            if new_remaining == 0:
                self.winners = [self.current]

    def _is_bust(self, new_remaining: int, dart: Dart) -> bool:
        if new_remaining < 0:
            return True
        if self.double_out:
            if new_remaining == 1:
                return True
            if new_remaining == 0 and dart.multiplier != 2:
                return True
        return False

    def _turn_note(self) -> str:
        return "BUST" if self.bust else ""

    def _new_turn(self, wrapped: bool) -> None:
        self.turn_start = self.players[self.current].remaining


# The scoreboard's default game
Game = X01Game
