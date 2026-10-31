
from pathlib import Path
import cv2


CAMERA_PATHS = {
    1: "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1:1.0-video-index0",
    2: "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.2:1.0-video-index0",
    3: "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.4:1.0-video-index0",
}


class CameraManager:
    def __init__(self):
        self.cameras = {}

    def open(self):
        for camera_id, device_name in CAMERA_PATHS.items():
            device_path = str(Path("/dev/v4l/by-path") / device_name)

            camera = cv2.VideoCapture(device_path, cv2.CAP_V4L2)

            if not camera.isOpened():
                self.close()
                raise RuntimeError(f"Cannot open camera {camera_id}: {device_path}")

            camera.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*"MJPG")
            )
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            camera.set(cv2.CAP_PROP_FPS, 30)

            self.cameras[camera_id] = camera
            print(f"Camera {camera_id} opened")

    def capture(self):
        frames = {}

        # Request a frame from each camera
        for camera_id, camera in self.cameras.items():
            if not camera.grab():
                raise RuntimeError(f"Cannot grab camera {camera_id}")

        # Retrieve frames
        for camera_id, camera in self.cameras.items():
            success, frame = camera.retrieve()

            if not success:
                raise RuntimeError(f"Cannot read camera {camera_id}")

            frames[camera_id] = frame

        return frames

    def close(self):
        for camera in self.cameras.values():
            camera.release()

        self.cameras.clear()
