"""Start the web scoreboard: python3 web_server.py [--port 8000]."""

import argparse

from dart_scoring.web import serve


def main():
    """Parse arguments and run the server."""
    parser = argparse.ArgumentParser(description="Dart scoring web scoreboard")
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to listen on (default: all interfaces)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
