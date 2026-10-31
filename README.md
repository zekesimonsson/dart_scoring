
# Dart Scoring

Automatic dart scoring system using Raspberry Pi 4,
three USB cameras and OpenCV.

## Hardware
- Raspberry Pi 4 Model B (4 GB)
- 3 × InnoMaker 720P USB cameras
- Dartboard with camera mounting ring
- LED lighting

## Software
- Python 3
- OpenCV 4.10
- Raspberry Pi OS (64-bit)

## Development status
Initial setup completed. Waiting for cameras.

## Web scoreboard
Start with `python3 web_server.py` and open `http://<raspberry-pi-ip>:8000/`
from any device on the network. Supports 301/501/701/101, double out,
any number of players, manual dart entry and undo.

## Planned features
- Camera capture and calibration
- Dart detection
- Automatic score calculation
- Web-based scoreboard
