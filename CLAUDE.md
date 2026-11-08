# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Automatic dart scoring on a Raspberry Pi 4 (64-bit Raspberry Pi OS) using three InnoMaker 720p USB cameras mounted on a ring around the board, plus OpenCV 4.10 (system Python 3, no virtualenv or dependency manifest). Planned pipeline: camera capture → calibration → dart detection → score calculation → game logic → web scoreboard. Early stage: calibration exists (manual point picking), `detection.py` is still an empty stub.

The development environment (devices, Tailscale network, tmux session `dart`, GitHub over SSH, how the scoreboard is reached) is described in Swedish in [`ARKITEKTUR.md`](ARKITEKTUR.md). Keep it up to date when that setup changes.

## Commands

All commands run from the repo root (scripts import the `dart_scoring` package relatively).

- `python3 main.py` — runs `calculate_score` on a few hard-coded sample points (no hardware needed).
- `python3 test_cameras.py` — opens all three cameras via `CameraManager`, discards 15 frames for auto-exposure, writes `images/camera_{1,2,3}.jpg`. Requires the cameras to be connected.
- `python3 web_server.py [--port 8000]` — web scoreboard on http://<pi-ip>:8000/ (stdlib only, no Flask). Darts can be entered manually in the page until detection exists. On the Pi it runs as the systemd **user** service `dart-scoreboard` (unit in `systemd/`, linger enabled so it starts at boot; no sudo available). After changing server code: `systemctl --user restart dart-scoreboard`. Logs: `logs/web_server.log` (rotated at 1 MB, 3 backups; the user journal is not persisted on this Pi, so `journalctl --user` shows nothing). Don't also start `web_server.py` by hand on port 8000 — it will conflict.
- `python3 calibrate.py [--port 8001]` — calibration tool on http://<pi-ip>:8001/. Captures a frame from every camera (button to recapture); the user clicks labelled reference points in each image and the page draws the fitted board on top. Saves to `calibration.json` in the repo root (gitignored; redo after moving a camera). It holds all cameras while capturing, so don't run it alongside other camera scripts.
- `python3 test_camera.py` — single-camera smoke test on `/dev/video0`, writes `camera_test.jpg`.

Unit tests (pytest, install with `sudo apt install python3-pytest`):

- `python3 -m pytest` — runs everything in `tests/`.
- `python3 -m pytest tests/test_scoring.py::test_cardinal_directions` — a single test.

`pyproject.toml` restricts collection to `tests/` and puts the repo root on `sys.path`. The top-level `test_camera.py` / `test_cameras.py` are manual hardware scripts that open cameras at import time — never let pytest collect them.

## Architecture

- **`dart_scoring/camera.py`** — `CameraManager` opens cameras by stable `/dev/v4l/by-path/...` names (`CAMERA_PATHS`), so camera IDs 1–3 are tied to **physical USB ports** (ports 1–3 of the USB hub in the Pi's USB 2.0 port 1.1, i.e. 1.1.1, 1.1.2, 1.1.3), not `/dev/videoN` enumeration order. Moving a camera to another port changes its ID. Cameras are configured for MJPG 1280×720 @ 30 fps with a fixed exposure (`EXPOSURE`, 20 ms, gain 0) so brightness stays stable between frames and auto exposure cannot drop the frame rate; white balance stays automatic because the manual colour temperature cannot remove the LED ring's green tint. `capture()` calls `grab()` on every camera first and only then `retrieve()`, to keep the three frames as close to simultaneous as possible. Keep that two-phase pattern when changing capture code.
- **`dart_scoring/scoring.py`** — `calculate_score(x, y)` works in **normalized board coordinates**: origin at the bull, radius 1.0 = outer edge of the double ring (170 mm), and **negative y points up** toward the 20 (image-style coordinates). Ring boundaries are standard board dimensions in mm divided by 170. `SEGMENTS` lists numbers clockwise starting at 20. Detection and calibration are expected to produce points in this coordinate system (e.g. via homography from each camera view).
- **`dart_scoring/calibration.py`** — per camera, a least-squares homography (`cv2.findHomography`, method 0) from image pixels to normalized board coordinates, fitted from reference points: a ring edge (`RINGS`) crossed with a segment-boundary wire (boundary `i` = wire between `SEGMENTS[i]` and `SEGMENTS[i+1]`, so 0 is 20/1), or the bull center. Lens distortion is not modelled; `rms_px` / `residuals_px` show the fit error. `image_to_board()` is what detection should use on the dart tip pixel. `calibration_web.py` + `static/calibrate.html` are the point-picking UI.
- **`dart_scoring/game.py`** — X01 rules (`Game`): 3 darts per turn, bust reverts to the score at turn start, optional double out (bullseye counts as a double). A finished turn stays visible until the next throw or `next_player()`; `undo()` restores deep-copied snapshots.
- **`dart_scoring/web.py`** — `ThreadingHTTPServer` holding one shared `Game` behind a lock. JSON API: `GET /api/state`, `POST /api/new-game`, `/api/throw` (`{segment, multiplier}` or normalized `{x, y}` via `scoring.classify`), `/api/next`, `/api/undo`. The page (`dart_scoring/static/index.html`, plain HTML/JS) polls `/api/state` every second, so several screens stay in sync. Detection should feed darts in via `/api/throw` with `{x, y}`.
