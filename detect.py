"""
Run dart detection: python3 detect.py [--dry-run] [--save] [--server URL].

The board must be empty when it starts. Detected darts are sent to the web
scoreboard as {"x", "y"} on /api/throw. --save writes before/after/debug
images and a JSON summary of every event to captures/ for tuning.
"""

import argparse
import json
import logging
import logging.handlers
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2

from dart_scoring import calibration
from dart_scoring.camera import CAMERA_PATHS, CameraManager
from dart_scoring.detection import Detector, Event, draw_event

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
CAPTURE_DIR = Path(__file__).resolve().parent / "captures"

log = logging.getLogger("detect")


def setup_logging(log_file: str | None) -> None:
    """Log to stderr, and also to a size-rotated file if log_file is given."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)


def load_models(path: Path) -> dict[int, dict]:
    """Calibration entry per camera; every camera must be calibrated."""
    cameras = calibration.load(path)["cameras"]
    models = {}
    for camera_id in CAMERA_PATHS:
        entry = cameras.get(str(camera_id), {})
        if "homography" not in entry:
            raise SystemExit(f"Kamera {camera_id} är inte kalibrerad, kör calibrate.py först")
        if "camera_matrix" not in entry:
            log.warning("Kamera %d är kalibrerad utan linsdistorsion (för få punkter)", camera_id)
        models[camera_id] = entry
    return models


def post_throw(server: str, event: Event) -> None:
    """Send a dart to the scoreboard."""
    body = json.dumps({"x": event.x, "y": event.y}).encode()
    request = urllib.request.Request(f"{server}/api/throw", body,
                                     {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
    except (urllib.error.URLError, OSError) as error:
        log.warning("Kunde inte skicka pilen till resultattavlan: %s", error)


def save_event(event: Event, models: dict, number: int) -> Path:
    """Write images and a summary of an event for later inspection."""
    folder = CAPTURE_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{number:03d}-{event.kind}"
    folder.mkdir(parents=True, exist_ok=True)
    for camera_id in event.after:
        cv2.imwrite(str(folder / f"cam{camera_id}_before.jpg"), event.before[camera_id])
        cv2.imwrite(str(folder / f"cam{camera_id}_after.jpg"), event.after[camera_id])
        cv2.imwrite(str(folder / f"cam{camera_id}_debug.jpg"), draw_event(event, camera_id, models))
    summary = {
        "kind": event.kind, "x": event.x, "y": event.y, "segment": event.segment,
        "multiplier": event.multiplier, "method": event.method, "changed": event.changed,
        "views": {c: vars(v) for c, v in event.views.items()},
        "lines": [{"camera": line.camera, "point": line.point.tolist(),
                   "direction": line.direction.tolist()} for line in event.lines],
    }
    (folder / "event.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return folder


def main():
    """Parse arguments and run detection until interrupted."""
    parser = argparse.ArgumentParser(description="Dart detection")
    parser.add_argument("--server", default="http://localhost:8000",
                        help="scoreboard to send darts to (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="only log darts, don't send them to the scoreboard")
    parser.add_argument("--save", action="store_true",
                        help=f"save images of every event to {CAPTURE_DIR}")
    parser.add_argument("--calibration", type=Path, default=calibration.CALIBRATION_FILE)
    parser.add_argument("--log-file", default="logs/detect.log")
    args = parser.parse_args()
    setup_logging(args.log_file)

    models = load_models(args.calibration)
    cameras = CameraManager()
    cameras.open()
    try:
        for _ in range(15):  # let the cameras settle
            cameras.capture()
        detector = Detector(models)
        detector.reset(cameras.capture(), time.monotonic())
        log.info("Pilavkänning igång%s. Tavlan ska vara tom nu.",
                 " (torrkörning, skickar inget)" if args.dry_run else f", skickar till {args.server}")
        frames_seen, started, number = 0, time.monotonic(), 0
        while True:
            frames = cameras.capture()
            now = time.monotonic()
            event = detector.feed(frames, now)
            frames_seen += 1
            if now - started >= 60:
                log.info("%.1f bilder/s", frames_seen / (now - started))
                frames_seen, started = 0, now
            if event is None or event.kind == "none":
                continue
            number += 1
            log.info("%s", event.describe())
            if args.save:
                log.info("Sparat i %s", save_event(event, models, number))
            if event.kind == "dart" and not args.dry_run:
                post_throw(args.server, event)
    except KeyboardInterrupt:
        pass
    finally:
        cameras.close()


if __name__ == "__main__":
    main()
