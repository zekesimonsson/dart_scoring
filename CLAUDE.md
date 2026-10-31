# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Automatic dart scoring on a Raspberry Pi 4 (64-bit Raspberry Pi OS) using three InnoMaker 720p USB cameras mounted on a ring around the board, plus OpenCV 4.10 (system Python 3, no virtualenv or dependency manifest). Planned pipeline: camera capture → calibration → dart detection → score calculation → game logic → web scoreboard. Early stage: `calibration.py` and `detection.py` are still empty stubs.

## Commands

All commands run from the repo root (scripts import the `dart_scoring` package relatively).

- `python3 main.py` — runs `calculate_score` on a few hard-coded sample points (no hardware needed).
- `python3 test_cameras.py` — opens all three cameras via `CameraManager`, discards 15 frames for auto-exposure, writes `images/camera_{1,2,3}.jpg`. Requires the cameras to be connected.
- `python3 web_server.py [--port 8000]` — web scoreboard on http://<pi-ip>:8000/ (stdlib only, no Flask). Darts can be entered manually in the page until detection exists.
- `python3 test_camera.py` — single-camera smoke test on `/dev/video0`, writes `camera_test.jpg`.

Unit tests (pytest, install with `sudo apt install python3-pytest`):

- `python3 -m pytest` — runs everything in `tests/`.
- `python3 -m pytest tests/test_scoring.py::test_cardinal_directions` — a single test.

`pyproject.toml` restricts collection to `tests/` and puts the repo root on `sys.path`. The top-level `test_camera.py` / `test_cameras.py` are manual hardware scripts that open cameras at import time — never let pytest collect them.

## Architecture

- **`dart_scoring/camera.py`** — `CameraManager` opens cameras by stable `/dev/v4l/by-path/...` names (`CAMERA_PATHS`), so camera IDs 1–3 are tied to **physical USB ports** (1.1, 1.2, 1.4), not `/dev/videoN` enumeration order. Moving a camera to another port changes its ID. Cameras are configured for MJPG 1280×720 @ 30 fps. `capture()` calls `grab()` on every camera first and only then `retrieve()`, to keep the three frames as close to simultaneous as possible. Keep that two-phase pattern when changing capture code.
- **`dart_scoring/scoring.py`** — `calculate_score(x, y)` works in **normalized board coordinates**: origin at the bull, radius 1.0 = outer edge of the double ring (170 mm), and **negative y points up** toward the 20 (image-style coordinates). Ring boundaries are standard board dimensions in mm divided by 170. `SEGMENTS` lists numbers clockwise starting at 20. Detection and calibration are expected to produce points in this coordinate system (e.g. via homography from each camera view).
- **`dart_scoring/game.py`** — X01 rules (`Game`): 3 darts per turn, bust reverts to the score at turn start, optional double out (bullseye counts as a double). A finished turn stays visible until the next throw or `next_player()`; `undo()` restores deep-copied snapshots.
- **`dart_scoring/web.py`** — `ThreadingHTTPServer` holding one shared `Game` behind a lock. JSON API: `GET /api/state`, `POST /api/new-game`, `/api/throw` (`{segment, multiplier}` or normalized `{x, y}` via `scoring.classify`), `/api/next`, `/api/undo`. The page (`dart_scoring/static/index.html`, plain HTML/JS) polls `/api/state` every second, so several screens stay in sync. Detection should feed darts in via `/api/throw` with `{x, y}`.
