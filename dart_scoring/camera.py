"""Capture synchronized frames from the three board cameras."""

from pathlib import Path
import cv2


CAMERA_PATHS = {
    1: "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1.2:1.0-video-index0",
    2: "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1.3:1.0-video-index0",
    3: "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1.4:1.0-video-index0",
}


# Fixed exposure in units of 100 µs. A multiple of 10 ms avoids banding from
# LED flicker on 50 Hz mains and stays below the 33 ms frame time at 30 fps.
EXPOSURE = 200


def configure(camera):
    """Configure a capture device for MJPG 1280x720 @ 30 fps, fixed exposure."""
    camera.set(
        cv2.CAP_PROP_FOURCC,
        cv2.VideoWriter_fourcc(*"MJPG")
    )
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    camera.set(cv2.CAP_PROP_FPS, 30)

    # Auto exposure lowers the frame rate in dim light and makes brightness
    # drift between frames, which breaks frame differencing. For the V4L2
    # backend, 1 = manual and 3 = aperture priority (auto).
    camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
    camera.set(cv2.CAP_PROP_EXPOSURE, EXPOSURE)
    camera.set(cv2.CAP_PROP_GAIN, 0)

    # White balance stays automatic: the manual colour temperature cannot
    # cancel the green tint of the LED ring (the camera has no tint control).
    camera.set(cv2.CAP_PROP_AUTO_WB, 1)


class CameraManager:
    """Opens the cameras in CAMERA_PATHS and captures frames from all of them."""

    def __init__(self):
        self.cameras = {}

    def open(self):
        """Open and configure every camera; raise RuntimeError on failure."""
        for camera_id, device_name in CAMERA_PATHS.items():
            device_path = str(Path("/dev/v4l/by-path") / device_name)

            camera = cv2.VideoCapture(device_path, cv2.CAP_V4L2)

            if not camera.isOpened():
                self.close()
                raise RuntimeError(f"Cannot open camera {camera_id}: {device_path}")

            configure(camera)

            self.cameras[camera_id] = camera
            print(f"Camera {camera_id} opened")

    def capture(self):
        """Return {camera_id: frame}, grabbing all cameras before retrieving."""
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
        """Release all open cameras."""
        for camera in self.cameras.values():
            camera.release()

        self.cameras.clear()
