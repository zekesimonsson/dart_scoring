
from pathlib import Path
from dart_scoring.camera import CameraManager


def main():
    manager = CameraManager()
    output_dir = Path("images")
    output_dir.mkdir(exist_ok=True)

    try:
        manager.open()

        # Allow automatic exposure to settle
        for _ in range(15):
            frames = manager.capture()

        import cv2

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
