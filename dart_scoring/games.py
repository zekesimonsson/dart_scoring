"""
The games besides X01, and `new_game` to start any of them by name.

- Cricket: close 20-15 and bull (three marks each; single 1, double 2, triple 3,
  bull counts 2). Marks on a number you closed score its value as long as some
  opponent hasn't closed it. Win: everything closed and at least as many points
  as everyone else.
- Around the Clock: hit 1, 2, ... 20 and then bull in order; first round wins.
- Shanghai: seven rounds, in round N only N scores (N x multiplier). Single,
  double and treble of N in one turn (a Shanghai) wins at once; otherwise the
  highest score after round 7 wins.
- Killer: every player gets a random number and some lives. Hit your own number
  to become a killer; a killer takes lives from the owner of the number they
  hit (single 1, double 2, treble 3), and loses lives hitting their own. Out at
  0 lives; the last player left wins.
"""

import random
from dataclasses import dataclass, field

from dart_scoring.game import DARTS_PER_TURN, Dart, Player, TurnGame, X01Game, player_names

BULL = 25


def _number_label(segment: int) -> str:
    return "Bull" if segment == BULL else str(segment)


# --- Cricket -------------------------------------------------------------------

CRICKET_NUMBERS = [20, 19, 18, 17, 16, 15, BULL]


@dataclass
class CricketPlayer(Player):
    marks: dict = field(default_factory=lambda: {n: 0 for n in CRICKET_NUMBERS})
    points: int = 0
    total_marks: int = 0

    def closed(self, number: int) -> bool:
        return self.marks[number] >= 3


class CricketGame(TurnGame):
    kind = "cricket"
    name = "Cricket"
    player_class = CricketPlayer

    def _score(self, dart: Dart) -> None:
        number = dart.segment
        if number not in CRICKET_NUMBERS or dart.multiplier == 0:
            return
        player = self.players[self.current]
        hits = dart.multiplier
        player.total_marks += hits
        closing = min(hits, 3 - player.marks[number])
        player.marks[number] += closing
        extra = hits - closing
        if extra and any(not p.closed(number) for i, p in enumerate(self.players) if i != self.current):
            player.points += extra * number
        if (all(player.closed(n) for n in CRICKET_NUMBERS)
                and all(player.points >= p.points for p in self.players)):
            self.winners = [self.current]

    def targets(self, index: int) -> list[int]:
        player = self.players[index]
        others = [p for i, p in enumerate(self.players) if i != index]
        return [n for n in CRICKET_NUMBERS
                if not player.closed(n) or any(not p.closed(n) for p in others)]

    def turn_summary(self) -> str:
        start = self._turn_start_player()
        player = self.players[self.current]
        marks = player.total_marks - start.total_marks
        points = player.points - start.points
        return f"{marks} märken" + (f" · +{points}" if points else "")

    def player_view(self, index: int) -> dict:
        p = self.players[index]
        rounds = p.darts_thrown / DARTS_PER_TURN
        mpr = p.total_marks / rounds if rounds else 0.0
        return {"big": p.points, "caption": "poäng",
                "marks": [{"label": _number_label(n), "marks": p.marks[n]} for n in CRICKET_NUMBERS],
                "stats": [f"{mpr:.1f} märken/runda", f"{p.darts_thrown} pilar"]}


# --- Around the Clock ------------------------------------------------------------

CLOCK_SEQUENCE = list(range(1, 21)) + [BULL]


@dataclass
class ClockPlayer(Player):
    done: int = 0      # targets hit so far; the next one is CLOCK_SEQUENCE[done]


class AroundTheClockGame(TurnGame):
    kind = "around_the_clock"
    name = "Around the Clock"
    player_class = ClockPlayer

    def _score(self, dart: Dart) -> None:
        player = self.players[self.current]
        if dart.multiplier and dart.segment == CLOCK_SEQUENCE[player.done]:
            player.done += 1
            if player.done == len(CLOCK_SEQUENCE):
                self.winners = [self.current]

    def targets(self, index: int) -> list[int]:
        done = self.players[index].done
        return [CLOCK_SEQUENCE[done]] if done < len(CLOCK_SEQUENCE) else []

    def turn_summary(self) -> str:
        hits = self.players[self.current].done - self._turn_start_player().done
        return f"{hits} träff" if hits == 1 else f"{hits} träffar"

    def player_view(self, index: int) -> dict:
        p = self.players[index]
        finished = p.done >= len(CLOCK_SEQUENCE)
        return {"big": "✓" if finished else _number_label(CLOCK_SEQUENCE[p.done]),
                "caption": "klar" if finished else "nästa",
                "stats": [f"{p.done}/{len(CLOCK_SEQUENCE)} klara", f"{p.darts_thrown} pilar"]}


# --- Shanghai ----------------------------------------------------------------------

SHANGHAI_ROUNDS = 7


@dataclass
class ShanghaiPlayer(Player):
    score: int = 0


class ShanghaiGame(TurnGame):
    kind = "shanghai"
    name = "Shanghai"
    player_class = ShanghaiPlayer

    def __init__(self, players: list[str]):
        super().__init__(players)
        self.round = 1

    def title(self) -> str:
        return f"Shanghai · runda {min(self.round, SHANGHAI_ROUNDS)} av {SHANGHAI_ROUNDS}"

    def _score(self, dart: Dart) -> None:
        player = self.players[self.current]
        if dart.segment == self.round:
            player.score += dart.points
        hit = {d.multiplier for d in self.turn if d.segment == self.round}
        if hit >= {1, 2, 3}:
            self.winners = [self.current]
            self.message = f"SHANGHAI! {player.name} vinner direkt"
        elif (len(self.turn) == DARTS_PER_TURN and self.round == SHANGHAI_ROUNDS
              and self._last_in_round()):
            self._finish()

    def _last_in_round(self) -> bool:
        return self.current == len(self.players) - 1

    def _finish(self) -> None:
        best = max(p.score for p in self.players)
        self.winners = [i for i, p in enumerate(self.players) if p.score == best]

    def _new_turn(self, wrapped: bool) -> None:
        if wrapped:
            self.round += 1
            if self.round > SHANGHAI_ROUNDS:  # the last turns were skipped
                self.round = SHANGHAI_ROUNDS
                self._finish()

    def targets(self, index: int) -> list[int]:
        return [self.round]

    def turn_summary(self) -> str:
        return f"+{self.players[self.current].score - self._turn_start_player().score}"

    def player_view(self, index: int) -> dict:
        p = self.players[index]
        return {"big": p.score, "caption": "poäng", "stats": [f"{p.darts_thrown} pilar"]}


# --- Killer -------------------------------------------------------------------------

KILLER_LIVES = 3


@dataclass
class KillerPlayer(Player):
    number: int = 0
    lives: int = KILLER_LIVES
    killer: bool = False


class KillerGame(TurnGame):
    kind = "killer"
    name = "Killer"
    player_class = KillerPlayer

    def __init__(self, players: list[str], lives: int = KILLER_LIVES,
                 numbers: list[int] | None = None, rng: random.Random | None = None):
        names = player_names(players)
        if len(names) < 2:
            raise ValueError("Killer kräver minst två spelare")
        if len(names) > 20:
            raise ValueError("Killer kan ha högst 20 spelare")
        if lives < 1:
            raise ValueError("Minst ett liv krävs")
        if numbers is None:
            numbers = (rng or random.Random()).sample(range(1, 21), len(names))
        if len(numbers) != len(names) or len(set(numbers)) != len(numbers):
            raise ValueError("Varje spelare behöver ett eget nummer")
        self.lives = lives
        self._numbers = list(numbers)
        super().__init__(names)
        for player, number in zip(self.players, self._numbers):
            player.number = number
            player.lives = lives

    def title(self) -> str:
        return f"Killer · {self.lives} liv"

    def _in_game(self, index: int) -> bool:
        return self.players[index].lives > 0

    def _score(self, dart: Dart) -> None:
        if dart.multiplier == 0 or not 1 <= dart.segment <= 20:
            return
        me = self.players[self.current]
        if dart.segment == me.number:
            if not me.killer:
                me.killer = True
                self.message = f"{me.name} är killer!"
            else:
                me.lives = max(0, me.lives - dart.multiplier)
                self.message = f"{me.name} träffade sitt eget nummer"
                if me.lives == 0:
                    self.message = f"{me.name} tog sitt sista liv själv och är ute"
                    self.turn_ended = True
        elif me.killer:
            for victim in self.players:
                if victim.number == dart.segment and victim.lives > 0:
                    victim.lives = max(0, victim.lives - dart.multiplier)
                    self.message = (f"{victim.name} är ute!" if victim.lives == 0
                                    else f"{me.name} tog liv från {victim.name}")
        alive = [i for i in range(len(self.players)) if self._in_game(i)]
        if len(alive) == 1:
            self.winners = alive

    def targets(self, index: int) -> list[int]:
        me = self.players[index]
        if not me.killer:
            return [me.number]
        return [p.number for i, p in enumerate(self.players) if i != index and p.lives > 0]

    def player_view(self, index: int) -> dict:
        p = self.players[index]
        out = p.lives == 0
        status = "UTE" if out else ("KILLER" if p.killer else f"Träffa {p.number} för att bli killer")
        return {"big": p.number, "caption": "♥" * p.lives + "♡" * (self.lives - p.lives),
                "stats": [status], "out": out, "killer": p.killer}


# --- Starting a game ---------------------------------------------------------------

GAMES = {
    "x01": X01Game,
    "cricket": CricketGame,
    "around_the_clock": AroundTheClockGame,
    "shanghai": ShanghaiGame,
    "killer": KillerGame,
}


def new_game(kind: str, players: list[str], options: dict | None = None) -> TurnGame:
    """Start a game by name ("x01", "cricket", ...) with its options."""
    options = options or {}
    if kind == "x01":
        return X01Game(players, start_score=int(options.get("start_score", 501)),
                       double_out=bool(options.get("double_out", True)))
    if kind == "killer":
        return KillerGame(players, lives=int(options.get("lives", KILLER_LIVES)))
    if kind in GAMES:
        return GAMES[kind](players)
    raise ValueError(f"Okänt spel: {kind}")
