"""Capture one frame from each of the three cameras into images/."""

from pathlib import Path

import cv2

from dart_scoring.camera import CameraManager


def main():
    """Open all cameras, let exposure settle, and save a frame from each."""
    manager = CameraManager()
    output_dir = Path("images")
    output_dir.mkdir(exist_ok=True)

    try:
        manager.open()

        # Allow automatic exposure to settle
        for _ in range(15):
            frames = manager.capture()

        for camera_id, frame in frames.items():
            filename = output_dir / f"camera_{camera_id}.jpg"
            cv2.imwrite(str(filename), frame)

            print(
                f"Camera {camera_id}: "
                f"{frame.shape[1]}x{frame.shape[0]} "
                f"saved to {filename}"
            )

    finally:
        manager.close()


if __name__ == "__main__":
    main()
