"""Start the web scoreboard: python3 web_server.py [--port 8000] [--log-file PATH]."""

import argparse
import logging
import logging.handlers
from pathlib import Path

from dart_scoring.web import serve

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(log_file: str | None) -> None:
    """Log to stderr, and also to a size-rotated file if log_file is given."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)


def main():
    """Parse arguments and run the server."""
    parser = argparse.ArgumentParser(description="Dart scoring web scoreboard")
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to listen on (default: all interfaces)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--log-file",
                        help="also log to this file (rotated at 1 MB, 3 backups)")
    args = parser.parse_args()
    setup_logging(args.log_file)
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
