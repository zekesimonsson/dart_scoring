"""Start the calibration tool: python3 calibrate.py [--port 8001]."""

import argparse
import logging
from pathlib import Path

from dart_scoring.calibration import CALIBRATION_FILE
from dart_scoring.calibration_web import serve


def main():
    """Parse arguments and run the calibration web tool."""
    parser = argparse.ArgumentParser(description="Dart camera calibration tool")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--file", type=Path, default=CALIBRATION_FILE,
                        help=f"calibration file (default: {CALIBRATION_FILE})")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    serve(args.host, args.port, args.file)


if __name__ == "__main__":
    main()
