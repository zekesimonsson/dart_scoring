"""
Replay saved darts against their corrected values: python3 evaluate_captures.py [--all].

Every dart corrected on the scoreboard has a truth.json in its folder under
captures/. This runs the current detection code on the saved before/after
images of those darts and reports how many it now gets right, next to what the
detector said live. With --all, darts without a correction are listed as
well (they were never corrected, which usually means they were right).

The saved images are JPEGs, so a replay can differ slightly from the live
result on the raw frames.
"""

import argparse
import json
from pathlib import Path

import cv2

from dart_scoring import calibration
from dart_scoring.detection import Detector, Event

CAPTURE_DIR = Path(__file__).resolve().parent / "captures"


def label(segment: int | None, multiplier: int | None) -> str:
    """Short label like the scoreboard's: T20, D16, 25, Bull, Miss."""
    if segment is None:
        return "-"
    if segment == 0 or multiplier == 0:
        return "Miss"
    if segment == 25:
        return "Bull" if multiplier == 2 else "25"
    return {1: "", 2: "D", 3: "T"}[multiplier] + str(segment)


def replay(folder: Path, models: dict) -> Event | None:
    """Run detection on a saved dart; None when its images are incomplete."""
    before, after = {}, {}
    for camera in models:
        before[camera] = cv2.imread(str(folder / f"cam{camera}_before.jpg"))
        after[camera] = cv2.imread(str(folder / f"cam{camera}_after.jpg"))
        if before[camera] is None or after[camera] is None:
            return None
    detector = Detector(models)
    detector.reset(before)
    return detector.evaluate(after)


def main():
    """Replay every corrected dart and print the result."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--all", action="store_true", help="also list darts without a correction")
    parser.add_argument("--captures", type=Path, default=CAPTURE_DIR)
    parser.add_argument("--calibration", type=Path, default=calibration.CALIBRATION_FILE)
    args = parser.parse_args()

    models = {int(k): v for k, v in calibration.load(args.calibration)["cameras"].items()}
    right = total = 0
    for folder in sorted(args.captures.glob("*-dart")):
        truth_file = folder / "truth.json"
        if not truth_file.exists() and not args.all:
            continue
        event = replay(folder, models)
        if event is None:
            continue
        live = json.loads((folder / "event.json").read_text(encoding="utf-8"))
        now = label(event.segment, event.multiplier) if event.kind == "dart" else event.kind
        line = f"{folder.name}  live {label(live.get('segment'), live.get('multiplier')):>5}  nu {now:>5}"
        if truth_file.exists():
            truth = json.loads(truth_file.read_text(encoding="utf-8"))
            ok = now == truth["label"]
            right += ok
            total += 1
            line += f"  facit {truth['label']:>5}  {'rätt' if ok else 'FEL'}"
        print(f"{line}  ({event.method})")
    if total:
        print(f"\n{right} av {total} rättade pilar blir rätt med nuvarande kod")
    else:
        print("Inga rättade pilar ännu (truth.json saknas i alla mappar)")


if __name__ == "__main__":
    main()
