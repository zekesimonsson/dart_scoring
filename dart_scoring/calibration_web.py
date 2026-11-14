"""
Calibration web tool: click reference points in each camera's image.

GET  /                      calibration page
GET  /api/info              cameras, reference labels and current calibration
GET  /frame/<id>.jpg        latest captured frame (?overlay=1 draws the fitted board)
GET  /stream/<id>.mjpg      live MJPEG stream, downscaled (?overlay=1 as above)
POST /api/capture           capture new frames from all cameras
POST /api/points            {"camera": 1, "points": [{"u", "v", "ring", "boundary"}]}
"""

import json
import logging
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

from dart_scoring import calibration
from dart_scoring.camera import CAMERA_PATHS, CameraManager

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 256 * 1024
SETTLE_FRAMES = 15
LIVE_IDLE_CLOSE_S = 3.0   # close the cameras this long after the last viewer leaves
STREAM_SIZE = (640, 360)
STREAM_INTERVAL_S = 0.1   # at most 10 frames/s per stream
OVERLAY_COLOR = (0, 255, 255)

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


class LiveCameras:
    """Keeps the cameras open in a background thread while anyone uses them.

    Users `acquire()`/`release()`; the thread captures continuously, publishes
    each set of frames (after the exposure has settled) and closes the cameras
    `LIVE_IDLE_CLOSE_S` after the last user is gone.
    """

    def __init__(self):
        self.cond = threading.Condition()
        self.camera_lock = threading.Lock()  # held while the cameras are open
        self.frames = {}
        self.seq = 0
        self.users = 0
        self.thread = None
        self.error = None

    def acquire(self) -> None:
        with self.cond:
            self.users += 1
            if self.thread is None:
                self.error = None
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()

    def release(self) -> None:
        with self.cond:
            self.users -= 1

    def wait_next(self, seq: int, timeout: float = 5.0) -> tuple:
        """Wait for frames newer than `seq`; return (seq, frames). Raises on camera failure."""
        with self.cond:
            self.cond.wait_for(lambda: self.seq > seq or self.error or self.thread is None, timeout)
            if self.error:
                raise RuntimeError(self.error)
            if self.seq <= seq:
                raise RuntimeError("Inga bilder från kamerorna")
            return self.seq, self.frames

    def _run(self) -> None:
        with self.camera_lock:  # wait for a previous thread to close the cameras
            manager = CameraManager()
            try:
                manager.open()
                log.info("Kamerorna öppnade för livebild")
                for _ in range(SETTLE_FRAMES):
                    manager.capture()
                idle_since = None
                while True:
                    with self.cond:
                        if self.users > 0:
                            idle_since = None
                        elif idle_since is None:
                            idle_since = time.monotonic()
                        elif time.monotonic() - idle_since > LIVE_IDLE_CLOSE_S:
                            self.thread = None
                            self.cond.notify_all()
                            break
                    frames = manager.capture()
                    with self.cond:
                        self.frames = frames
                        self.seq += 1
                        self.cond.notify_all()
            except Exception as error:  # pylint: disable=broad-except
                log.exception("Kamerafel i livebilden")
                with self.cond:
                    self.error = str(error) or type(error).__name__
                    self.thread = None
                    self.cond.notify_all()
            finally:
                manager.close()
                log.info("Kamerorna stängda")


class CalibrationServer(ThreadingHTTPServer):
    """Holds the latest frames and the calibration data, guarded by `lock`."""

    daemon_threads = True

    def __init__(self, address, calibration_file: Path):
        super().__init__(address, CalibrationRequestHandler)
        self.lock = threading.Lock()
        self.calibration_file = calibration_file
        self.data = calibration.load(calibration_file)
        self.frames = {}
        self.live = LiveCameras()
        self._overlays = {}

    def recapture(self) -> None:
        """Replace the stored frames with fresh ones from the cameras."""
        self.live.acquire()
        try:
            with self.live.cond:
                seq = self.live.seq
            _, frames = self.live.wait_next(seq, timeout=15)
        finally:
            self.live.release()
        with self.lock:
            self.frames = frames
        log.info("Tog nya bilder från kamera %s", sorted(frames))

    def overlay_mask(self, camera_id: int, size: tuple):
        """Board drawing for the current calibration at `size`, cached; None if uncalibrated."""
        with self.lock:
            entry = self.data["cameras"].get(str(camera_id), {})
        if "homography" not in entry:
            return None
        key = (camera_id, size, json.dumps(entry.get("homography")), json.dumps(entry.get("dist_coeffs")))
        mask = self._overlays.get(key)
        if mask is None:
            full = calibration.draw_board(np.zeros((720, 1280, 3), np.uint8), entry,
                                          color=(255, 255, 255), thickness=2)
            mask = cv2.resize(full[:, :, 0], size, interpolation=cv2.INTER_AREA) > 60
            self._overlays = {k: v for k, v in self._overlays.items() if k[0] != camera_id}
            self._overlays[key] = mask
        return mask

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return  # the browser dropped an image it no longer wanted
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
                    "min_distortion_points": calibration.MIN_DISTORTION_POINTS,
                    "calibration": self.server.data,
                }
            self._send_json(info)
        elif match := re.fullmatch(r"/frame/(\d+)\.jpg", path):
            self._send_frame(int(match.group(1)), "overlay=1" in query)
        elif match := re.fullmatch(r"/stream/(\d+)\.mjpg", path):
            self._send_stream(int(match.group(1)), "overlay=1" in query)
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
            log.info("Kamera %d: %d punkter, fel %.1f px / %.1f mm, %d misstänkta", camera_id,
                     len(entry["points"]), entry["rms_px"], entry["rms_mm"],
                     len(entry["suspects"]))
        return entry

    def _send_frame(self, camera_id: int, overlay: bool) -> None:
        with self.server.lock:
            frame = self.server.frames.get(camera_id)
            entry = self.server.data["cameras"].get(str(camera_id), {})
        if frame is None:
            self._send_json({"error": "Ingen bild tagen"}, HTTPStatus.NOT_FOUND)
            return
        if overlay and "homography" in entry:
            frame = calibration.draw_board(frame, entry)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError("Kunde inte koda bilden")
        self._send(HTTPStatus.OK, "image/jpeg", buf.tobytes())

    def _send_stream(self, camera_id: int, overlay: bool) -> None:
        if camera_id not in CAMERA_PATHS:
            self._send_json({"error": f"Okänd kamera: {camera_id}"}, HTTPStatus.NOT_FOUND)
            return
        live = self.server.live
        live.acquire()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seq = 0
            while True:
                started = time.monotonic()
                seq, frames = live.wait_next(seq)
                frame = frames.get(camera_id)
                if frame is None:
                    continue
                small = cv2.resize(frame, STREAM_SIZE, interpolation=cv2.INTER_AREA)
                mask = self.server.overlay_mask(camera_id, STREAM_SIZE) if overlay else None
                if mask is not None:
                    small[mask] = OVERLAY_COLOR
                ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(buf)).encode() + b"\r\n\r\n" + buf.tobytes() + b"\r\n")
                self.wfile.flush()
                time.sleep(max(0.0, STREAM_INTERVAL_S - (time.monotonic() - started)))
        except (BrokenPipeError, ConnectionResetError):
            pass  # the viewer left
        except RuntimeError as error:
            log.warning("Livebild avbruten: %s", error)
        finally:
            live.release()

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
