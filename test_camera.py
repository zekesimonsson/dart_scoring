"""Single-camera smoke test: capture one frame from /dev/video0."""

import cv2

from dart_scoring.camera import configure

camera = cv2.VideoCapture(0, cv2.CAP_V4L2)

if not camera.isOpened():
    raise RuntimeError("Could not open camera")

configure(camera)

# Capture a few frames to allow auto exposure to settle
for _ in range(15):
    ret, frame = camera.read()
    if not ret:
        break

if ret:
    cv2.imwrite("camera_test.jpg", frame)

    print("Image captured successfully!")
    print(f"Resolution: {frame.shape[1]}x{frame.shape[0]}")
    print(f"FPS: {camera.get(cv2.CAP_PROP_FPS)}")
else:
    print("Failed to capture image")

camera.release()
