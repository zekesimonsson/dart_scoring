"""
Web scoreboard: a small HTTP server (standard library only) with a JSON API.

GET  /                 scoreboard page
GET  /api/state        current game as JSON
POST /api/new-game     {"players": [...], "start_score": 501, "double_out": true}
POST /api/throw        {"segment": 20, "multiplier": 3}  or  {"x": 0.0, "y": -0.6}
POST /api/next         end the current turn
POST /api/undo         undo the last action
"""

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dart_scoring.game import Game
from dart_scoring.scoring import classify

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024

log = logging.getLogger(__name__)


class DartServer(ThreadingHTTPServer):
    """HTTP server holding the shared game; all access goes through `lock`."""

    daemon_threads = True

    def __init__(self, address, game: Game | None = None):
        super().__init__(address, DartRequestHandler)
        self.lock = threading.Lock()
        self.game = game or Game(["Spelare 1", "Spelare 2"])

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
        self.server.game.throw(segment, multiplier)

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
