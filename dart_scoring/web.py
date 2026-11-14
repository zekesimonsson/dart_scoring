"""
Web scoreboard: a small HTTP server (standard library only) with a JSON API.

GET  /                 scoreboard page
GET  /api/state        current game as JSON
POST /api/new-game     {"players": [...], "start_score": 501, "double_out": true}
POST /api/throw        {"segment": 20, "multiplier": 3}  or  {"x": 0.0, "y": -0.6},
                       optionally with "capture": the detector's saved images of the dart
POST /api/correct      {"index": 1, "segment": 20, "multiplier": 1}: fix a dart of the
                       turn on display; for a detected dart the right value is saved as
                       truth.json in its capture folder
POST /api/takeout      darts pulled: fill the turn up with misses
POST /api/next         end the current turn
POST /api/undo         undo the last action
"""

import json
import logging
import re
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dart_scoring.game import Game
from dart_scoring.scoring import classify

STATIC_DIR = Path(__file__).parent / "static"
CAPTURE_DIR = Path(__file__).resolve().parent.parent / "captures"
CAPTURE_NAME = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]{3,}-dart")
MAX_BODY = 64 * 1024

log = logging.getLogger(__name__)


class DartServer(ThreadingHTTPServer):
    """HTTP server holding the shared game; all access goes through `lock`."""

    daemon_threads = True

    def __init__(self, address, game: Game | None = None, capture_dir: Path = CAPTURE_DIR):
        super().__init__(address, DartRequestHandler)
        self.lock = threading.Lock()
        self.game = game or Game(["Spelare 1", "Spelare 2"])
        self.capture_dir = capture_dir

    def handle_error(self, request, client_address):
        """Log unexpected exceptions from request handlers."""
        log.exception("Unhandled error while serving %s", client_address[0])


class DartRequestHandler(BaseHTTPRequestHandler):
    """Routes scoreboard and API requests."""

    server: DartServer

    def do_GET(self):  # pylint: disable=invalid-name
        """Serve the page or the game state."""
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/api/state":
            with self.server.lock:
                state = self.server.game.to_dict()
            self._send_json(state)
        else:
            self._send_json({"error": "Hittades inte"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):  # pylint: disable=invalid-name
        """Handle game actions."""
        actions = {
            "/api/new-game": self._new_game,
            "/api/throw": self._throw,
            "/api/correct": self._correct,
            "/api/takeout": lambda _body: self.server.game.takeout(),
            "/api/next": lambda _body: self.server.game.next_player(),
            "/api/undo": lambda _body: self.server.game.undo(),
        }
        action = actions.get(self.path.split("?", 1)[0])
        if action is None:
            self._send_json({"error": "Hittades inte"}, HTTPStatus.NOT_FOUND)
            return

        try:
            body = self._read_json()
            with self.server.lock:
                action(body)
                state = self.server.game.to_dict()
        except (ValueError, TypeError, KeyError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(state)

    def _new_game(self, body: dict) -> None:
        players = body.get("players", [])
        if not isinstance(players, list):
            raise ValueError("players måste vara en lista")
        self.server.game = Game(
            [str(name) for name in players],
            start_score=int(body.get("start_score", 501)),
            double_out=bool(body.get("double_out", True)),
        )

    def _throw(self, body: dict) -> None:
        if "x" in body and "y" in body:
            segment, multiplier = classify(float(body["x"]), float(body["y"]))
        else:
            segment = int(body["segment"])
            multiplier = int(body.get("multiplier", 1))
        capture = body.get("capture")
        if capture is not None and not CAPTURE_NAME.fullmatch(str(capture)):
            raise ValueError("Ogiltigt namn på bildmapp")
        self.server.game.throw(segment, multiplier, capture)

    def _correct(self, body: dict) -> None:
        old, new = self.server.game.correct(int(body["index"]), int(body["segment"]),
                                            int(body.get("multiplier", 1)))
        log.info("Rättade pil %s -> %s (%s)", old.label, new.label, old.capture or "manuell")
        if old.capture is None:
            return
        folder = self.server.capture_dir / old.capture
        if not folder.is_dir():
            log.warning("Hittar inte bildmappen %s, sparar inget facit", folder)
            return
        truth = {"segment": new.segment, "multiplier": new.multiplier, "label": new.label,
                 "detected": old.label if not old.corrected else None,
                 "time": datetime.now().isoformat(timespec="seconds")}
        if old.corrected:  # corrected twice: keep what the detector said the first time
            try:
                previous = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
                truth["detected"] = previous.get("detected")
            except (OSError, ValueError):
                pass
        (folder / "truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("För stor förfrågan")
        if length == 0:
            return {}
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("Förväntade ett JSON-objekt")
        return body

    def _send_json(self, data, status=HTTPStatus.OK):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path, content_type: str):
        payload = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        """Log everything except the once-a-second state polling."""
        if not self.path.startswith("/api/state"):
            log.info("%s %s", self.address_string(), format % args)


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the scoreboard server until interrupted."""
    with DartServer((host, port)) as server:
        log.info("Resultattavla på http://%s:%d/", host, port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        log.info("Servern stoppad")
