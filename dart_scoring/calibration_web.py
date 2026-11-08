"""
Calibration web tool: click reference points in each camera's image.

GET  /                      calibration page
GET  /api/info              cameras, reference labels and current calibration
GET  /frame/<id>.jpg        latest captured frame (?overlay=1 draws the fitted board)
POST /api/capture           capture new frames from all cameras
POST /api/points            {"camera": 1, "points": [{"u", "v", "ring", "boundary"}]}
"""

import json
import logging
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2

from dart_scoring import calibration
from dart_scoring.camera import CAMERA_PATHS, CameraManager

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 256 * 1024
SETTLE_FRAMES = 15

RING_LABELS = {
    "double_outer": "Dubbel, ytterkant",
    "double_inner": "Dubbel, innerkant",
    "triple_outer": "Trippel, ytterkant",
    "triple_inner": "Trippel, innerkant",
    "bull_outer": "Yttre bull (25), kant",
    "bull_inner": "Bullseye, kant",
    "center": "Bullens mitt",
}

log = logging.getLogger(__name__)


def capture_frames() -> dict:
    """Open all cameras, let exposure settle, and return {camera_id: frame}."""
    manager = CameraManager()
    try:
        manager.open()
        frames = {}
        for _ in range(SETTLE_FRAMES):
            frames = manager.capture()
        return frames
    finally:
        manager.close()


class CalibrationServer(ThreadingHTTPServer):
    """Holds the latest frames and the calibration data, guarded by `lock`."""

    daemon_threads = True

    def __init__(self, address, calibration_file: Path):
        super().__init__(address, CalibrationRequestHandler)
        self.lock = threading.Lock()
        self.calibration_file = calibration_file
        self.data = calibration.load(calibration_file)
        self.frames = {}

    def recapture(self) -> None:
        """Replace the stored frames with fresh ones from the cameras."""
        frames = capture_frames()
        with self.lock:
            self.frames = frames
        log.info("Tog nya bilder från kamera %s", sorted(frames))

    def handle_error(self, request, client_address):
        log.exception("Unhandled error while serving %s", client_address[0])


class CalibrationRequestHandler(BaseHTTPRequestHandler):
    """Routes calibration page and API requests."""

    server: CalibrationServer

    def do_GET(self):  # pylint: disable=invalid-name
        """Serve the page, info or a camera frame."""
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, "text/html; charset=utf-8",
                       (STATIC_DIR / "calibrate.html").read_bytes())
        elif path == "/api/info":
            with self.server.lock:
                info = {
                    "cameras": sorted(CAMERA_PATHS),
                    "captured": sorted(self.server.frames),
                    "rings": [{"key": k, "label": v} for k, v in RING_LABELS.items()],
                    "boundaries": [calibration.boundary_label(b) for b in range(20)],
                    "min_points": calibration.MIN_POINTS,
                    "calibration": self.server.data,
                }
            self._send_json(info)
        elif match := re.fullmatch(r"/frame/(\d+)\.jpg", path):
            self._send_frame(int(match.group(1)), "overlay=1" in query)
        else:
            self._send_json({"error": "Hittades inte"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):  # pylint: disable=invalid-name
        """Capture frames or store points."""
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/capture":
                self.server.recapture()
                self._send_json({"captured": sorted(self.server.frames)})
            elif path == "/api/points":
                self._send_json(self._store_points(self._read_json()))
            else:
                self._send_json({"error": "Hittades inte"}, HTTPStatus.NOT_FOUND)
        except RuntimeError as error:  # camera problems
            log.warning("Kamerafel: %s", error)
            self._send_json({"error": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)
        except (ValueError, TypeError, KeyError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _store_points(self, body: dict) -> dict:
        camera_id = int(body["camera"])
        if camera_id not in CAMERA_PATHS:
            raise ValueError(f"Okänd kamera: {camera_id}")
        points = body.get("points", [])
        if not isinstance(points, list):
            raise ValueError("points måste vara en lista")
        with self.server.lock:
            frame = self.server.frames.get(camera_id)
            size = (frame.shape[1], frame.shape[0]) if frame is not None else (1280, 720)
            entry = calibration.calibrate_camera(self.server.data, camera_id, points, size)
            calibration.save(self.server.data, self.server.calibration_file)
        if "rms_px" in entry:
            log.info("Kamera %d: %d punkter, fel %.1f px / %.1f mm", camera_id,
                     len(entry["points"]), entry["rms_px"], entry["rms_mm"])
        return entry

    def _send_frame(self, camera_id: int, overlay: bool) -> None:
        with self.server.lock:
            frame = self.server.frames.get(camera_id)
            entry = self.server.data["cameras"].get(str(camera_id), {})
        if frame is None:
            self._send_json({"error": "Ingen bild tagen"}, HTTPStatus.NOT_FOUND)
            return
        if overlay and "homography" in entry:
            frame = calibration.draw_board(frame, entry["homography"])
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError("Kunde inte koda bilden")
        self._send(HTTPStatus.OK, "image/jpeg", buf.tobytes())

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("För stor förfrågan")
        body = json.loads(self.rfile.read(length)) if length else {}
        if not isinstance(body, dict):
            raise ValueError("Förväntade ett JSON-objekt")
        return body

    def _send_json(self, data, status=HTTPStatus.OK):
        self._send(status, "application/json", json.dumps(data).encode())

    def _send(self, status, content_type: str, payload: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        """Log API calls only, not every image fetch."""
        if self.path.startswith("/api/") and self.command == "POST":
            log.info("%s %s", self.address_string(), format % args)


def serve(host: str = "0.0.0.0", port: int = 8001,
          calibration_file: Path = calibration.CALIBRATION_FILE) -> None:
    """Capture a first set of frames and run the calibration tool until interrupted."""
    with CalibrationServer((host, port), calibration_file) as server:
        server.recapture()
        log.info("Kalibrering på http://%s:%d/ (sparar till %s)", host, port, calibration_file)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
